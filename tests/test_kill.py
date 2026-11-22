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
SEND_POINTS = ["send.after_lookup", "send.after_intent", "send.outbox_before_rename", "send.after_deliver",
               "send.after_mark_sent"]


def _messages(data_dir: Path) -> list[Path]:
    from pdr.adapters.k7_outbox import LocalOutbox
    return LocalOutbox(data_dir / "outbox").messages()


def _temps(data_dir: Path) -> list[Path]:
    t = data_dir / "outbox" / ".tmp"
    return list(t.iterdir()) if t.is_dir() else []


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


PIPE_CASES = ["case06_wrong_base_over_cap", "case04_duplicate", "case10_instruction_in_input"]


def _pipe_setup(data_dir: Path) -> None:
    from pdr.store import SqliteStore
    from tests.caselib import load_case
    store = SqliteStore(data_dir / "state.db")
    keys: list[str] = []
    for case in PIPE_CASES:
        keys += load_case(ROOT / "tests" / "cases" / case, store)[0]
    (data_dir / "keys.json").write_text(json.dumps(keys))


def _pipe_run(data_dir: Path) -> None:
    """What `pdr run` does after reading: engine, K5 lines, draft actions, case file; then approve and send."""
    from pdr import actions, approval, engine, send
    from pdr.outputs import k5, report
    c = _driver_ctx(data_dir)
    send.startup(c)
    keys = json.loads((data_dir / "keys.json").read_text())
    verdicts = engine.run(keys, c)
    k5.write_all(c.store, data_dir / "out", c.settings)
    for v in verdicts:
        actions.draft_for_verdict(v, c)
    report.render(c.store, data_dir / "out", c.settings)
    for a in c.store.actions_in("awaiting_approval"):  # stands in for the person at the approval screen
        for role in approval.missing_roles(c.store, a):
            approval.approve(c, a.action_id, role, "Kill Test Approver")
    send.send_all(c)


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
    files = _messages(data_dir)
    assert len(files) == len(ids), [f.name for f in files]
    seen = collections.Counter(json.loads(f.read_text())["action_id"] for f in files)
    assert all(seen[i] == 1 for i in ids), seen
    assert _temps(data_dir) == [], "stale temp files left after restart"
    assert sorted(p.name for p in (data_dir / "outbox").iterdir() if p.name != ".tmp") == [f.name for f in files]
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


def test_kill_between_temp_write_and_rename(tmp_path):
    """Deterministic version of the random-kill path: nothing visible before restart, exactly one after."""
    assert _run(["setup", str(tmp_path), "3"]).returncode == 0
    died = _run(["send", str(tmp_path)], crash_at="send.outbox_before_rename")
    assert died.returncode == 137, died.stderr[-2000:]
    assert _messages(tmp_path) == []  # the half-written message is not a message
    assert len(_temps(tmp_path)) == 1  # it sits in outbox/.tmp/, outside the listing
    assert [p.name for p in (tmp_path / "outbox").iterdir()] == [".tmp"]
    again = _run(["send", str(tmp_path)])
    assert again.returncode == 0, again.stderr[-2000:]
    _assert_exactly_once(tmp_path)  # one message per action, temp dir emptied by reconcile


def test_crash_points_registered():
    from pdr.crashpoints import KNOWN
    assert set(SEND_POINTS) <= set(KNOWN)


def _assert_pipeline(data_dir: Path) -> None:
    from pdr.models import Action
    from pdr.store import SqliteStore
    keys = json.loads((data_dir / "keys.json").read_text())
    lines = [json.loads(l) for l in (data_dir / "out" / "verdicts.jsonl").read_text().splitlines() if l.strip()]
    got = collections.Counter(l.get("dispute_key") for l in lines)
    assert sorted(got) == sorted(keys) and all(n == 1 for n in got.values()), got
    assert (data_dir / "out" / "index.html").exists()
    store = SqliteStore(data_dir / "state.db")
    sent = [a for a in store.find(Action) if str(a.status) in ("sent", "sending")]
    files = _messages(data_dir)
    assert len(files) == len(sent) and all(str(a.status) == "sent" for a in sent)
    per_key = collections.Counter(k for a in sent for k in a.dispute_keys)
    assert all(n == 1 for n in per_key.values()), per_key
    try:
        from pdr.broker import ledger
        paid = [r.cache_key for r in ledger.rows(store) if not r.cache_hit and r.outcome == "ok"]
        assert len(paid) == len(set(paid)), "a cache key was paid twice"
    except ImportError:
        pass


def _known_points() -> list[str]:
    from pdr.crashpoints import KNOWN
    return [p for p in KNOWN if not p.startswith("call.")]  # call.* points belong to a1's broker kill tests


@pytest.mark.parametrize("point", _known_points())
def test_kill_pipeline_at_every_point(tmp_path, point):
    pytest.importorskip("tests.caselib")
    assert _run(["pipe-setup", str(tmp_path)]).returncode == 0
    first = _run(["pipe-run", str(tmp_path)], crash_at=point)
    assert first.returncode in (0, 137), first.stderr[-2000:]  # 0: this point is not reached on this path yet
    again = _run(["pipe-run", str(tmp_path)])
    assert again.returncode == 0, again.stderr[-2000:]
    _assert_pipeline(tmp_path)


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
        n_files = len(_messages(d))
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
    elif cmd == "pipe-setup":
        _pipe_setup(data)
    elif cmd == "pipe-run":
        _pipe_run(data)
    else:
        raise SystemExit(f"unknown command {cmd}")
