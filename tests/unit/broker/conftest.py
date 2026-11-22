"""Fixtures for broker tests: a RunContext on a temp SQLite file and a registered FakeTransport."""
from __future__ import annotations

import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from pdr import broker
from pdr.broker import throttle
from pdr.contracts import RunContext
from pdr.settings import load_settings
from tests.fakes.transport import FakeTransport

ROOT = Path(__file__).resolve().parents[3]


def make_ctx(db_path, *, run_id="run1", dispute_key="ACME|PA1|1", **budget) -> RunContext:
    s = load_settings(ROOT / "settings.yaml")
    for k, v in budget.items():
        setattr(s.budget, k, v)
    s.providers.paidcall_mode = "inline"
    return RunContext(run_id=run_id, dispute_key=dispute_key, settings=s,
                      store=types.SimpleNamespace(path=str(db_path)),
                      deadline=datetime.now(timezone.utc) + timedelta(hours=1))


@pytest.fixture
def ctx(tmp_path):
    return make_ctx(tmp_path / "pdr.db")


@pytest.fixture
def fake(monkeypatch):
    t = FakeTransport()
    monkeypatch.setitem(broker.TRANSPORTS, "crustdata", t)
    monkeypatch.setitem(broker.TRANSPORTS, "web", t)
    return t


@pytest.fixture(autouse=True)
def instant_time(monkeypatch):
    """No real sleeping in backoff or throttle; a fake clock that sleep advances."""
    clock = {"t": 1_000_000.0, "sleeps": []}

    def sleep(s):
        clock["sleeps"].append(s)
        clock["t"] += s

    monkeypatch.setattr(throttle, "CLOCK", lambda: clock["t"])
    monkeypatch.setattr(throttle, "SLEEP", sleep)
    from pdr.broker import core
    monkeypatch.setattr(core, "SLEEP", lambda s: clock["sleeps"].append(("backoff", s)))
    return clock
