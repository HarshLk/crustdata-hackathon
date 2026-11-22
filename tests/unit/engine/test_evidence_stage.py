"""Stage 6: corroboration, causation, grouping (one search per route-date), status mapping, economic gate."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from pdr.codes import Ground, Outcome, Reason
from pdr.contracts import EvidenceResult, SearchLogLine
from pdr.engine import run
from pdr.engine.evidence_stage import place_match
from pdr.models import Evidence

from tests.unit.engine.fixtures_b1 import (
    FakeFinder, ctx, deduction, fm_named, kini_evidence, notice_term, stub_free_checks, term, trip, unregister, world,
)


@pytest.fixture(autouse=True)
def _free_checks():
    ids = stub_free_checks()
    yield
    unregister(ids)


def _patch(monkeypatch, fn):
    import pdr.evidence as ev
    f = FakeFinder(fn)
    monkeypatch.setattr(ev, "find_evidence", f)
    return f


def _found(req):
    return EvidenceResult(status="found", supporting=[kini_evidence()],
                          search_log=[SearchLogLine(query="Kini blockade", window=None, n_results=3)])


def test_supporting_evidence_with_gps_halt_is_disputable_fm(monkeypatch):
    f = _patch(monkeypatch, _found)
    store, keys = world()
    v = run(keys, ctx(store, **{"causation.allowance_hours": 12}))[0]
    assert v.outcome == Outcome.disputable, v.explanation
    assert v.grounds == [Ground.FORCE_MAJEURE]
    assert v.amount_disputable == Decimal("2000.00")
    assert len(f.calls) == 1
    assert "ext-kini-1" in v.evidence_ids
    assert store.get(Evidence, "ext-kini-1") is not None
    assert "Kini" in v.explanation


def test_without_halt_is_evidence_partial_naming_gps(monkeypatch):
    _patch(monkeypatch, _found)
    store, keys = world(trips=[trip(halt=False)])
    v = run(keys, ctx(store))[0]
    assert v.outcome == Outcome.cannot_verify and v.primary_reason == Reason.EVIDENCE_PARTIAL
    assert "GPS extract" in v.missing_items[0]


def test_halt_elsewhere_is_not_corroboration(monkeypatch):
    from pdr.models import Halt, SourceRef
    from tests.unit.engine.fixtures_b1 import ist
    t = trip(halt=False).model_copy(update={"halts": [Halt(start=ist(5, 6), end=ist(5, 20), place="Lonavala",
                                                           source="gps", ref=SourceRef(file="g.csv"))]})
    _patch(monkeypatch, _found)
    store, keys = world(trips=[t])
    assert run(keys, ctx(store))[0].primary_reason == Reason.EVIDENCE_PARTIAL


def test_ops_note_alone_is_not_corroboration(monkeypatch):
    _patch(monkeypatch, _found)
    store, keys = world(trips=[trip(halt=False)])  # ops note at Kini remains
    assert run(keys, ctx(store))[0].outcome == Outcome.cannot_verify


def test_two_deductions_one_route_date_one_search(monkeypatch):
    f = _patch(monkeypatch, _found)
    trips = [trip(), trip(tid="TRIP-2", lr="LR-2002")]
    deds = [deduction("PA-1"), deduction("PA-2", refs=("LR-2002",))]
    store, keys = world(trips=trips, deds=deds)
    vs = run(keys, ctx(store))
    assert len(vs) == 2 and len(f.calls) == 1
    assert all(v.outcome == Outcome.disputable for v in vs)


def test_conflicting_sources(monkeypatch):
    _patch(monkeypatch, lambda req: EvidenceResult(status="found", supporting=[kini_evidence()],
                                                   contradicting=[kini_evidence("ext-police", stance="contradicts")]))
    store, keys = world()
    assert run(keys, ctx(store))[0].primary_reason == Reason.SOURCES_CONFLICT


@pytest.mark.parametrize("status,reason", [("none", Reason.NO_EVIDENCE_FOUND), ("undated_only", Reason.SOURCE_UNDATED),
                                           ("budget", Reason.BUDGET_REACHED), ("provider_failed", Reason.PROVIDER_FAILED)])
def test_status_mapping_asserted(monkeypatch, status, reason):
    _patch(monkeypatch, lambda req: EvidenceResult(status=status))
    store, keys = world()
    assert run(keys, ctx(store))[0].primary_reason == reason


def test_budget_exception_is_budget_reached(monkeypatch):
    from pdr.contracts import BudgetReached

    def boom(req):
        raise BudgetReached("x")
    _patch(monkeypatch, boom)
    store, keys = world()
    assert run(keys, ctx(store))[0].primary_reason == Reason.BUDGET_REACHED


def test_budget_result_is_retried_next_run(monkeypatch):
    f = _patch(monkeypatch, lambda req: EvidenceResult(status="budget"))
    store, keys = world()
    run(keys, ctx(store))
    f.result_fn = _found
    v = run(keys, ctx(store, **{"causation.allowance_hours": 12}))[0]
    assert len(f.calls) == 2 and v.outcome == Outcome.disputable


def test_discovery_hit_is_only_incomplete(monkeypatch):
    _patch(monkeypatch, _found)
    store, keys = world(trips=[trip(notice=False, assertion=False)])
    v = run(keys, ctx(store))[0]
    assert v.outcome == Outcome.cannot_verify and v.primary_reason == Reason.EVIDENCE_PARTIAL


def test_per_instance_penalty_causation_partial(monkeypatch):
    _patch(monkeypatch, _found)
    from tests.unit.engine.fixtures_b1 import ist
    pen = term("T-PEN", "penalty_rule", {"event": "late_delivery", "rate": "2000", "unit": "per_instance", "base": "none"})
    t = trip().model_copy(update={"arrival_at": [trip().arrival_at[0].model_copy(update={"value": ist(9, 12)})]})
    store, keys = world(terms=[pen, fm_named(), notice_term()], trips=[t])
    v = run(keys, ctx(store, **{"causation.allowance_hours": 0}))[0]
    assert v.primary_reason == Reason.CAUSATION_PARTIAL


def test_economic_gate(monkeypatch):
    f = _patch(monkeypatch, _found)
    store, keys = world(deds=[deduction(amount="150")])
    v = run(keys, ctx(store, **{"economics.estimated_search_credits": 8}))[0]
    assert v.primary_reason == Reason.SEARCH_NOT_ECONOMICAL and f.calls == []
    store2, keys2 = world(deds=[deduction(amount="150")])
    run(keys2, ctx(store2, **{"economics.estimated_search_credits": 8, "economics.search_gate_ratio": 0}))
    assert len(f.calls) == 1


def test_no_route_places(monkeypatch):
    f = _patch(monkeypatch, _found)
    store, keys = world(trips=[trip(places=[])])
    v = run(keys, ctx(store))[0]
    assert v.primary_reason == Reason.EVIDENCE_PARTIAL and "route places" in v.missing_items[0] and f.calls == []


def test_place_match():
    assert place_match("Kini toll plaza", ["Kini toll plaza, Kolhapur"], ["Kini"])
    assert place_match("near Kini", ["Kini toll plaza"], ["Kini", "Satara"])
    assert not place_match("Lonavala", ["Kini toll plaza"], ["Kini"])
    assert not place_match(None, ["Kini"], ["Kini"])
