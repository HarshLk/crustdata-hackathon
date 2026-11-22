"""Kill tests (PRD case 11, DESIGN 7): die at every send crash point or at a random SIGKILL, run again,
and K7 holds exactly one message per action, every action is `sent`, no paid call repeated.

Run as a script, this file is the driver:  python tests/test_kill.py setup|send <data_dir> [n_actions]
"""
from __future__ import annotations

import collections
import json
import os
import random
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
SEND_POINTS = ["send.after_lookup", "send.after_intent", "send.after_deliver", "send.after_mark_sent"]


# --------------------------------------------------------------------------- driver (subprocess side)

def _driver_ctx(data_dir: Path):
    from pdr.store import SqliteStore
    from tests.unit.actions import build
    return build.ctx(store=SqliteStore(data_dir / "state.db"), tmp_path=data_dir)


def _setup(data_dir: Path, n: int) -> None:
    from pdr import actions, approval
    from tests.unit.actions import build
    c = _driver_ctx(data_dir)
    ids = []
    for i in range(n):
        v = build.fm_verdict(c.store, advice=f"PA-2026-{700 + i}")
        a = actions.draft_for_verdict(v, c)
        approval.approve(c, a.action_id, "kam", "Asha Rao")
        ids.append(a.action_id)
    (data_dir / "ids.json").write_text(json.dumps(ids))


def _send(data_dir: Path) -> None:
    from pdr import send
    c = _driver_ctx(data_dir)
    k7 = None
    if os.environ.get("KILL_TEST_SLOW"):
        from pdr.adapters.k7_outbox import get_k7
        k7 = get_k7(c.settings)
        real = k7.deliver

        def slow(action_id, body, attachments):
            time.sleep(0.1)
            real(action_id, body, attachments)
            time.sleep(0.1)
        k7.deliver = slow
    send.run(c, k7)


# --------------------------------------------------------------------------- test side

def _run(args: list[str], crash_at: str | None = None, extra_env: dict | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": str(ROOT), **(extra_env or {})}
    env.pop("PDR_CRASH_AT", None)
    if crash_at:
        env["PDR_CRASH_AT"] = crash_at
    return subprocess.run([PY, str(Path(__file__)), *args], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)


def _assert_exactly_once(data_dir: Path) -> None:
    from pdr.models import Action
    from pdr.store import SqliteStore
    ids = json.loads((data_dir / "ids.json").read_text())
    files = sorted((data_dir / "outbox").glob("*.json"))
    assert len(files) == len(ids), [f.name for f in files]
    seen = collections.Counter(json.loads(f.read_text())["action_id"] for f in files)
    assert all(seen[i] == 1 for i in ids), seen
    assert not list((data_dir / "outbox").glob(".tmp-*"))
    store = SqliteStore(data_dir / "state.db")
    for i in ids:
        a = store.get(Action, i)
        assert str(a.status) == "sent", (i, a.status)
    assert store.trail_verify()
    try:
        from pdr.broker import ledger
        paid = [r.cache_key for r in ledger.rows(store) if not r.cache_hit and r.outcome == "ok"]
        assert len(paid) == len(set(paid)), "a cache key was paid twice"
    except ImportError:
        pass


@pytest.fixture(autouse=True)
def _need_sqlite():
    pytest.importorskip("pdr.store")


@pytest.mark.parametrize("point", SEND_POINTS)
def test_kill_at_each_send_point(tmp_path, point):
    assert _run(["setup", str(tmp_path), "3"]).returncode == 0
    died = _run(["send", str(tmp_path)], crash_at=point)
    assert died.returncode == 137, died.stderr[-2000:]
    again = _run(["send", str(tmp_path)])
    assert again.returncode == 0, again.stderr[-2000:]
    _assert_exactly_once(tmp_path)


def test_crash_points_registered():
    from pdr.crashpoints import KNOWN
    assert set(SEND_POINTS) <= set(KNOWN)


def test_random_sigkill(tmp_path):
    rng = random.Random(1123)
    interrupted = 0
    for trial in range(6):
        d = tmp_path / f"t{trial}"
        d.mkdir()
        assert _run(["setup", str(d), "4"]).returncode == 0
        env = {**os.environ, "PYTHONPATH": str(ROOT), "KILL_TEST_SLOW": "1"}
        env.pop("PDR_CRASH_AT", None)
        p = subprocess.Popen([PY, str(Path(__file__)), "send", str(d)], cwd=ROOT, env=env,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(rng.uniform(0.25, 1.1))  # start-up ~0.3s, then ~0.2s per action
        p.send_signal(signal.SIGKILL)
        p.wait(timeout=30)
        n_files = len(list((d / "outbox").glob("*.json"))) if (d / "outbox").exists() else 0
        interrupted += p.returncode == -signal.SIGKILL and n_files < 4
        again = _run(["send", str(d)])
        assert again.returncode == 0, again.stderr[-2000:]
        _assert_exactly_once(d)
    assert interrupted >= 2, "the random kills never landed mid-send; widen the slowdown"


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    cmd, data = sys.argv[1], Path(sys.argv[2])
    if cmd == "setup":
        _setup(data, int(sys.argv[3]) if len(sys.argv) > 3 else 1)
    elif cmd == "send":
        _send(data)
    else:
        raise SystemExit(f"unknown command {cmd}")
