"""A9/live: the recorded live runs (22 Nov 2026) replay offline with the same EvidenceResult status and sources."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from pdr import broker
from pdr.contracts import EvidenceRequest, RunContext
from pdr.evidence import find_evidence
from pdr.store_mem import MemStore
from tests.fakes.transport import FakeTransport

from .conftest import CASSETTES

LIVE = CASSETTES / "live"
SCENARIOS = sorted(p.name for p in LIVE.iterdir() if (p / "expected.json").exists()) if LIVE.is_dir() else []


def _dt(s):
    return datetime.fromisoformat(s)


@pytest.mark.parametrize("name", SCENARIOS)
def test_live_scenario_replays(name, settings, monkeypatch):
    exp = json.loads((LIVE / name / "expected.json").read_text(encoding="utf-8"))
    settings.providers.paidcall_mode = "inline"
    settings.budget.run_cost_credits = 300
    t = FakeTransport(cassette_dir=LIVE / name, strict=False)
    monkeypatch.setitem(broker.TRANSPORTS, "crustdata", t)
    monkeypatch.setitem(broker.TRANSPORTS, "web", t)
    monkeypatch.delitem(broker.TRANSPORTS, "llm", raising=False)
    r = exp["request"]
    req = EvidenceRequest(route_places=r["route_places"], route_states=r["route_states"],
                          window=(_dt(r["window"][0]), _dt(r["window"][1])), event_types=r["event_types"])
    ctx = RunContext(run_id="replay", dispute_key=None, settings=settings, store=MemStore(),
                     deadline=datetime.now(timezone.utc) + timedelta(hours=1))
    res = find_evidence(req, ctx)
    assert res.status == exp["status"]
    assert sorted(e.url for e in res.supporting) == sorted(exp["supporting_urls"])
    assert sorted(e.url for e in res.contradicting) == sorted(exp["contradicting_urls"])
    assert [l.query for l in res.search_log] == exp["queries"]
