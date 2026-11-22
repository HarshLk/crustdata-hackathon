"""W0 contract tests: models round-trip, keys, settings, MemStore, EvidenceRequest, stubs."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from pdr import codes
from pdr.codes import REASON_PRECEDENCE, STAGE_ORDER, ActionStatus, CheckStatus, Reason, Stage
from pdr.contracts import CheckContext, EvidenceRequest, K7, RunContext, Store
from pdr.models import (
    Action, Agreement, Approval, Cap, CheckResult, Citation, Client, Deduction, Evidence,
    LedgerEntry, Observation, PenaltyRule, Request, SchemaMap, SourceRef, Term, Trip, Verdict,
    TERM_PARAM_TYPES, canonical_json, entity_id, make_dispute_key, make_event_fp, norm_key,
)
from pdr.settings import load_settings
from pdr.store_mem import MemStore

ROOT = Path(__file__).resolve().parents[2]
IST = timezone(timedelta(hours=5, minutes=30))
T0 = datetime(2026, 11, 5, 6, 10, tzinfo=IST)
REF = SourceRef(file="gps_2026-11.csv", row=412)


def _instances():
    term_params = {
        "penalty_rule": {"event": "late_delivery", "rate": "0.5", "unit": "pct_per_week_or_part", "base": "delayed_quantity_value"},
        "cap": {"value": "10", "kind": "pct", "base": "trip_cost", "scope": "per_trip"},
        "force_majeure": {"events_named": ["bandh", "flood"], "general_words": ["agitations"]},
        "notice": {"deadline_hours": 48, "applies_to": "force_majeure"},
        "time_limit": {"window_days": 30, "day_count": "calendar", "counted_from": "deduction_date"},
        "buyer_delay": {"buyer_bears": True, "free_loading_hours": 24},
        "definition": {"name": "delivery", "event": "arrival_at"},
        "waiver": {"condition": "satisfactory_explanation"},
        "hold_rule": {"covers_govt_detention": True},
    }
    terms = [
        Term(term_id=f"T{i}", agreement_id="A1", client_id="ACME", term_type=tt, params=p, page=7,
             clause_ref="14.1", verbatim="...", effective_from=date(2026, 4, 1), status="confirmed")
        for i, (tt, p) in enumerate(term_params.items())
    ]
    cit = Citation(kind="contract_term", evidence_id="E1", label="clause 14.1", link="file.pdf#page=7")
    return [
        Client(client_id="ACME", legal_name="Acme Foods Pvt Ltd"),
        Agreement(agreement_id="A1", client_id="ACME", doc_type="master", title="Transport Agreement",
                  file_ref="ta.pdf", effective_from=date(2026, 4, 1), is_stand_in=True),
        *terms,
        Trip(trip_id="TRIP-0091", client_id="ACME", lr_no="LR-1", origin="Pune", destination="Kolhapur",
             lane="PUN-KOP", vehicle_no="MH12AB1234",
             arrival_at=[Observation(value=T0, source="gps", ref=REF)], freight=Decimal("25000.00")),
        Deduction(deduction_id="D1", dispute_key=make_dispute_key("ACME", "PA/2026/0412", 3),
                  event_fp=make_event_fp("ACME", "TRIP-0091", "late_delivery"), client_id="ACME",
                  client_name_raw="ACME FOODS", advice_no="PA/2026/0412", line_no=3,
                  deduction_date=date(2026, 11, 10), amount=Decimal("2000.00"), reason_text="late delivery",
                  reason_code="late_delivery", trip_refs=["LR-1"], period=(date(2026, 11, 1), date(2026, 11, 30))),
        Evidence(evidence_id="E1", kind="contract_term", term_id="T0", page=7, clause_ref="14.1",
                 verbatim="...", link="ta.pdf#page=7"),
        Verdict(verdict_id="V1", deduction_id="D1", dispute_key="ACME|PA20260412|3", outcome="disputable",
                grounds=["FORCE_MAJEURE"], amount_deducted=Decimal("2000.00"),
                amount_disputable=Decimal("2000.00"), recomputed_penalty=Decimal("0.00")),
        Approval(approval_id="AP1", action_id="ACME|PA20260412|3:abc", role_required="kam"),
        Action(action_id="ACME|PA20260412|3:abc", kind="dispute_letter", client_id="ACME",
               content_version="abc", deduction_ids=["D1"], status="awaiting_approval"),
        SchemaMap(header_hash="h1", source_kind="k4", mapping={"Adv No": "advice_no"}),
        Request(request_type="check_deductions", raw_text="check these", files=["a.csv"]),
        LedgerEntry(entry_id="L1", run_id="r1", cache_key="ck", step="search", provider="crustdata",
                    endpoint="/web/search/live", outcome="ok", started_at=T0, units=Decimal("1")),
        CheckResult(check_id="over_cap", stage="terms_checked", status="GROUND", ground="OVER_CAP",
                    citations=[cit]),
    ]


@pytest.mark.parametrize("obj", _instances(), ids=lambda o: type(o).__name__ + getattr(o, "term_type", ""))
def test_round_trip(obj):
    again = type(obj).model_validate_json(obj.model_dump_json())
    assert again == obj
    assert canonical_json(again) == canonical_json(obj)


def test_term_typed_params():
    for t in (o for o in _instances() if isinstance(o, Term)):
        assert isinstance(t.typed_params(), TERM_PARAM_TYPES[t.term_type])
    t = Term(term_id="x", agreement_id="A", client_id="C", term_type="cap",
             params=Cap(value=Decimal("5"), kind="abs", base="none"), page=1, clause_ref="1",
             verbatim="v", effective_from=date(2026, 1, 1))
    assert t.typed_params().value == Decimal("5")
    with pytest.raises(ValidationError):
        Term(term_id="x", agreement_id="A", client_id="C", term_type="penalty_rule", params={"rate": "1"},
             page=1, clause_ref="1", verbatim="v", effective_from=date(2026, 1, 1))
    assert isinstance(PenaltyRule.model_validate(_instances()[2].params), PenaltyRule)


def test_naive_datetime_rejected():
    with pytest.raises(ValidationError):
        Observation(value=datetime(2026, 11, 5, 6, 10), source="gps", ref=REF)


def test_keys():
    assert norm_key("PA/2026/0412") == norm_key("pa-2026-0412") == "PA20260412"
    assert make_dispute_key("acme", "PA/2026/0412", 3) == make_dispute_key("ACME", "PA-2026-0412", "3") == "ACME|PA20260412|3"
    assert make_dispute_key("ACME", None, None, client_deduction_no="dn-7") == "ACME|N:DN7"
    k1 = make_dispute_key("ACME", None, None, fallback_fields={"date": "2026-11-10", "amount": "2,000"})
    k2 = make_dispute_key("ACME", None, None, fallback_fields={"amount": "2000", "date": "20261110"})
    assert k1 == k2 and k1.startswith("ACME|H:")
    with pytest.raises(ValueError):
        make_dispute_key("ACME", None, None)
    assert make_event_fp("acme", "trip-1", codes.ReasonCode.late_delivery) == make_event_fp("ACME", "TRIP1", "late_delivery")
    assert entity_id(next(o for o in _instances() if isinstance(o, Deduction))) == "ACME|PA20260412|3"  # Deduction keyed by dispute_key


def test_codes():
    assert set(REASON_PRECEDENCE) == set(Reason)
    assert REASON_PRECEDENCE[:4] == [Reason.AMBIGUOUS_MATCH, Reason.TRIP_NOT_FOUND, Reason.TERMS_MISSING, Reason.TERMS_UNCONFIRMED]
    assert STAGE_ORDER[0] == Stage.received and STAGE_ORDER[-1] == Stage.verdict
    assert codes.Outcome("cannot_verify").value == "cannot_verify"
    assert codes.primary_reason(["NO_EVIDENCE_FOUND", "RECORDS_CONFLICT"]) == Reason.RECORDS_CONFLICT
    for enum in (codes.Ground, codes.Reason, codes.Flag, codes.CheckStatus):
        assert all(m.name == m.value for m in enum)


def test_settings_load_and_version():
    s1 = load_settings(ROOT / "settings.yaml")
    s2 = load_settings(ROOT / "settings.yaml")
    assert s1.version == s2.version and len(s1.version) == 12
    assert s1.budget.usd_inr == Decimal("88.0")
    assert s1.section("records") == {"conflict_allowance_minutes": 60.0}
    changed = s1.model_copy(deep=True)
    changed.records.conflict_allowance_minutes = 30
    assert type(s1).model_validate(changed.model_dump()).version != s1.version


def _ctx(store):
    return RunContext(run_id="r1", dispute_key=None, settings=load_settings(ROOT / "settings.yaml"),
                      store=store, deadline=datetime.now(timezone.utc) + timedelta(minutes=10))


def test_memstore():
    st = MemStore()
    assert isinstance(st, Store)
    objs = _instances()
    with st.tx():
        for o in objs:
            if type(o).__name__ in ("Request", "CheckResult"):
                continue
            st.put(o)
    ded = next(o for o in objs if isinstance(o, Deduction))
    assert st.get(Deduction, ded.dispute_key) == ded
    assert st.get("Client", "ACME").legal_name == "Acme Foods Pvt Ltd"
    assert st.get(Client, "nope") is None
    assert len(st.find(Term, client_id="ACME", status="confirmed")) == 9
    assert [t.term_type for t in st.find(Term, term_type="cap")] == ["cap"]

    cr = CheckResult(check_id="basis", stage=Stage.terms_checked, status=CheckStatus.NO_GROUND)
    assert st.load_stage(ded.dispute_key, Stage.terms_checked, "h") is None
    st.save_stage(ded.dispute_key, Stage.terms_checked, "h", [cr])
    assert st.load_stage(ded.dispute_key, "terms_checked", "h") == [cr]
    assert st.load_stage(ded.dispute_key, Stage.terms_checked, "other") is None
    assert st.get(Deduction, ded.dispute_key).stage == Stage.terms_checked

    assert st.unfinished() == []  # V1 is a current verdict for this key
    v = st.get(Verdict, "V1")
    with pytest.raises(ValueError):
        st.put(v.model_copy(update={"outcome": "valid"}))
    st.put(v.model_copy(update={"superseded_by": "V2"}))
    assert [d.dispute_key for d in st.unfinished()] == [ded.dispute_key]

    assert len(st.actions_in(ActionStatus.awaiting_approval)) == 1
    st.trail_append("approve", "kam:asha", "A1", {"x": 1})
    st.trail_append("send", "clerk", "A1", {})
    assert st.trail_verify()
    st._trail[0]["detail"] = {"x": 2}
    assert not st.trail_verify()

    st.snapshot_put("c1", "https://x", "text", None)
    assert st.snapshot_get("c1")["url"] == "https://x" and st.snapshot_get("c2") is None

    ctx = _ctx(st)
    assert ctx.remaining_credits() == Decimal("40")
    ctx.spent_credits += Decimal("3")
    assert ctx.remaining_credits() == Decimal("37")
    CheckContext(deduction=ded, trip=None, client=None, agreement=None, terms=[], prior={}, ctx=ctx)


def test_evidence_request_hashable():
    w = (T0, T0 + timedelta(hours=15))
    a = EvidenceRequest(["Kini", "Kolhapur"], ["Maharashtra"], w, ["bandh"], "asserted")
    b = EvidenceRequest(route_places=("Kini", "Kolhapur"), route_states=("Maharashtra",), window=w,
                        event_types=(codes.EventType.bandh,), mode="asserted")
    assert a == b and hash(a) == hash(b) and a.key() == b.key()
    assert len({a, b}) == 1
    c = EvidenceRequest(["Kini"], ["Maharashtra"], w, ["bandh"], "asserted")
    assert c.key() != a.key()


def test_stubs(tmp_path):
    from pdr import broker, crashpoints, evidence, guard, llm
    from pdr.adapters.k7_outbox import LocalOutbox

    ctx = _ctx(MemStore())
    req = EvidenceRequest(["Kini"], [], (T0, T0), ["bandh"])
    assert evidence.find_evidence(req, ctx).status == "none"
    assert evidence.estimate_credits(req, ctx) == Decimal("7")
    assert evidence.refute(next(o for o in _instances() if isinstance(o, Evidence)), ["x"], ctx) is None
    link = evidence.passage_link("https://n.example/a#old", "one two three four five six seven eight nine ten eleven twelve thirteen")
    assert link == "https://n.example/a#:~:text=one%20two%20three%20four%20five%20six,eight%20nine%20ten%20eleven%20twelve%20thirteen"
    assert evidence.passage_link("https://n.example/a", "NH-48 closed") == "https://n.example/a#:~:text=NH%2D48%20closed"
    with pytest.raises(NotImplementedError):
        broker.paid_call("crustdata", "/x", {}, ctx=ctx, step="s", worst_case_credits=Decimal("1"))
    assert llm.llm_json("map_reason", "text", Client, ctx) is None
    guard.check("crustdata_search", {"q": "bandh Kini"})
    assert guard.pseudonymise("abc", "ACME") == "abc"
    assert "send.after_mark_sent" in crashpoints.KNOWN and "stage.before_save.verdict" in crashpoints.KNOWN
    crashpoints.crashpoint("not.set")  # no env var -> returns

    ob = LocalOutbox(tmp_path / "outbox")
    assert isinstance(ob, K7) and not ob.exists("A:1")
    ob.deliver("A:1", {"kind": "dispute_letter"}, [])
    ob.deliver("A:1", {"kind": "changed"}, [])
    assert ob.exists("A:1") and len(list((tmp_path / "outbox").iterdir())) == 1


def test_cli_imports():
    from typer.testing import CliRunner
    from pdr.cli import app
    assert CliRunner().invoke(app, ["version"]).output.strip() == "0.1.0"
