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


# ----------------------------------------------------------------------------- named stretches

from pdr.engine.evidence_stage import in_stretch, stretches  # noqa: E402

ROUTE = ["Pune", "Shirwal", "Satara", "Karad", "Kini", "Kolhapur"]
KINI_STRETCH = "Traffic was halted on the 120 km stretch from Nagthane (Satara) to Sangli Phata (Kolhapur) on NH-48."


def test_halt_inside_named_stretch_corroborates():
    assert stretches(KINI_STRETCH, ROUTE) == [(2, 5)]
    assert in_stretch("Karad bypass", [KINI_STRETCH], ROUTE)
    assert in_stretch("Satara", [KINI_STRETCH], ROUTE)  # ends count


def test_halt_outside_named_stretch_does_not():
    assert not in_stretch("Shirwal", [KINI_STRETCH], ROUTE)
    assert not in_stretch("Lonavala", [KINI_STRETCH], ROUTE)  # not on the route at all
    assert not in_stretch(None, [KINI_STRETCH], ROUTE)


def test_unordered_stretch_and_route():
    rev = "Trucks stood between Kolhapur and Satara, police said."
    assert stretches(rev, ROUTE) == [(2, 5)] and in_stretch("Karad", [rev], ROUTE)
    # a stretch whose ends are not both on the route never corroborates
    assert not in_stretch("Karad", ["The stretch from Satara to Mumbai was closed."], ROUTE)
    # the order is the trip's: on a route where Karad is not between the ends, no match
    assert not in_stretch("Karad", [KINI_STRETCH], ["Karad", "Pune", "Satara", "Kini", "Kolhapur"])


def test_stretch_halt_gives_fm_ground(monkeypatch):
    from pdr.models import Halt, SourceRef
    from tests.unit.engine.fixtures_b1 import ist, KINI_TEXT
    ev = kini_evidence().model_copy(update={"place_named": "Kini"})
    _patch(monkeypatch, lambda req: EvidenceResult(status="found", supporting=[ev]))
    t = trip(halt=False, places=ROUTE).model_copy(update={"halts": [Halt(
        start=ist(5, 6), end=ist(5, 20), place="Karad", source="gps", ref=SourceRef(file="g.csv", row=9))]})
    store, keys = world(trips=[t])
    from pdr.models import sha256_hex
    text = KINI_TEXT + " " + KINI_STRETCH
    store.snapshot_put(sha256_hex(text), ev.url, text, None)
    ev2 = ev.model_copy(update={"content_hash": sha256_hex(text)})
    _patch(monkeypatch, lambda req: EvidenceResult(status="found", supporting=[ev2]))
    v = run(keys, ctx(store, **{"causation.allowance_hours": 12}))[0]
    assert v.outcome == Outcome.disputable and v.grounds == [Ground.FORCE_MAJEURE], v.explanation


def test_agitation_is_searchable(monkeypatch):
    f = _patch(monkeypatch, lambda req: EvidenceResult(status="none"))
    from pdr.models import Assertion, SourceRef
    from tests.unit.engine.fixtures_b1 import ist
    t = trip().model_copy(update={"assertions": [Assertion(kind="ops_note", event_type="agitation", text="farmers' agitation",
                                                           at=ist(5, 7), place="Kini", ref=SourceRef(file="o.csv"))]})
    store, keys = world(trips=[t])
    run(keys, ctx(store))
    from pdr.codes import EventType
    assert f.calls and EventType.agitation in f.calls[0].event_types


# ----------------------------------------------------------------------------- place regions (D-27)

def test_build_request_carries_place_regions():
    from types import SimpleNamespace
    from pdr.engine.evidence_stage import build_request
    from tests.unit.engine.fixtures_b1 import settings
    t = trip(places=["Pune", "Shrirampur", "Kolhapur"]).model_copy(update={"route_place_details": {
        "Shrirampur": {"district": "Ahmednagar", "state": "Maharashtra", "state_local": "Maharashtra"},
        "Elsewhere": {"district": "X", "state": "Y"}}})
    c = SimpleNamespace(trip=t, ctx=SimpleNamespace(settings=settings()), deduction=deduction())
    req = build_request(c, {"mode": "asserted", "event_types": ["blockade"]})
    assert dict(req.place_regions) == {"Shrirampur": ("Ahmednagar", "Maharashtra", "Maharashtra")}
    hash(req)  # still hashable
    plain = build_request(SimpleNamespace(trip=trip(), ctx=c.ctx, deduction=c.deduction),
                          {"mode": "asserted", "event_types": ["blockade"]})
    assert not plain.place_regions


def test_region_check_uses_place_district_and_rejects_same_name_elsewhere():
    from types import SimpleNamespace
    from pdr.contracts import EvidenceRequest
    from pdr.evidence.qualify import _region_named
    from tests.unit.engine.fixtures_b1 import ist
    req = EvidenceRequest(route_places=["Pune", "Srirampur"], route_states=["Maharashtra"], window=(ist(1), ist(9)),
                          event_types=["blockade"], place_regions={"Srirampur": ("Ahmednagar", "Maharashtra")})
    district_only = SimpleNamespace(title="Rasta roko at Srirampur", text="Farmers blocked the road at Srirampur in Ahmednagar district.")
    other_state = SimpleNamespace(title="Trucks held at Srirampur", text="Over 300 trucks are held at Srirampur on the Assam-West Bengal border.")
    assert _region_named("Srirampur", district_only, req)
    assert not _region_named("Srirampur", other_state, req)
