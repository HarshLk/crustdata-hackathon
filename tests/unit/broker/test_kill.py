"""Killing the process mid-call: detached mode keeps the paid result; inline mode is honest about it."""
import os
import sqlite3
import subprocess
import sys
import time
from decimal import Decimal
from pathlib import Path

import pytest

from pdr import broker
from pdr.broker import ledger
from tests.fakes.transport import FakeTransport
from tests.unit.broker.conftest import ROOT, make_ctx
from tests.unit.broker.kill_helper import REQ, RESPONSE, SEARCH

pytestmark = pytest.mark.skipif(os.name == "nt", reason="detached executor is POSIX only")


def _start_and_kill(db: Path, mode: str, slow: float) -> None:
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    env.pop("PDR_PAIDCALL_MODE", None)
    p = subprocess.Popen([sys.executable, "-m", "tests.unit.broker.kill_helper", str(db), mode, str(slow)],
                         cwd=str(ROOT), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    t_end = time.time() + 20
    while time.time() < t_end:  # wait until the call is in flight
        try:
            row = sqlite3.connect(db).execute("SELECT status FROM call_intents").fetchone()
        except sqlite3.Error:
            row = None
        if row and str(row[0]).startswith("running:"):
            break
        time.sleep(0.05)
    else:
        p.kill()
        raise AssertionError(p.communicate()[0].decode()[-2000:])
    time.sleep(0.3)
    p.kill()
    p.wait()


def _restart(db: Path, monkeypatch, mode: str):
    fake = FakeTransport(entries=[RESPONSE])
    monkeypatch.setitem(broker.TRANSPORTS, "crustdata", fake)
    ctx = make_ctx(db)
    ctx.settings.providers.paidcall_mode = mode
    r = broker.paid_call("crustdata", SEARCH, REQ, ctx=ctx, step="search", worst_case_credits=Decimal(1))
    return r, fake, ctx


def test_detached_kill_leaves_cache_hit_and_one_paid_row(tmp_path, monkeypatch):
    db = tmp_path / "pdr.db"
    _start_and_kill(db, "detached", 2.0)
    r, fake, ctx = _restart(db, monkeypatch, "detached")
    assert r.ok and r.cache_hit
    assert fake.calls == []
    rows = ledger.rows(ctx.store)
    paid = [e for e in rows if not e.cache_hit]
    assert [(e.outcome, e.units) for e in paid] == [("ok", Decimal(1))]
    assert not any(e.outcome == "in_flight_at_crash" for e in rows)


def test_inline_kill_writes_in_flight_row_and_reissues(tmp_path, monkeypatch):
    db = tmp_path / "pdr.db"
    _start_and_kill(db, "inline", 5.0)
    r, fake, ctx = _restart(db, monkeypatch, "inline")
    assert r.ok and not r.cache_hit and len(fake.calls) == 1
    outcomes = [e.outcome for e in ledger.rows(ctx.store)]
    assert outcomes == ["in_flight_at_crash", "ok"]


def test_detached_normal_call_returns_result(tmp_path, monkeypatch):
    r, fake, ctx = _restart(tmp_path / "pdr.db", monkeypatch, "detached")
    assert r.ok and not r.cache_hit and r.credits == Decimal(1) and r.body["results"]
    assert fake.calls == []  # executed by the child's own rebuilt transport
    assert ctx.spent_credits == Decimal(1)
    assert [e.outcome for e in ledger.rows(ctx.store)] == ["ok"]
