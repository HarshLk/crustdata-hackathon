"""Stage 4 record checks: no delay, start point, client-caused (case 5), regulatory hold, RECORDS_CONFLICT."""
from __future__ import annotations

from decimal import Decimal

from pdr.codes import CheckStatus as CS, Ground, Outcome, Reason, ReasonCode, TsSource as S
from pdr.engine import checks_records as R
from pdr.engine import checks_terms as T
from pdr.engine.combine import combine
from pdr.engine.penalty import recompute
from pdr.models import Hold, SourceRef

from .b2_fixtures import cap, deduction, ist, obs, rule, term, trip
from .test_checks_terms import ctx_for

D = Decimal
FLAT = rule(2000, "flat_per_day", "none")
BUYER = term("buyer_delay", {"buyer_bears": True, "free_loading_hours": 4}, "T-buyer", clause="15(i)")


def _verdict(c, rs):
    corr = [r.correction for r in rs if r.status == CS.GROUND and r.correction]
    return combine(c.deduction, rs, recompute(c.deduction, c.trip, c.terms, corr, agreement=c.agreement))


def test_no_delay_by_delivery_record():
    t = trip(delivery_due_at=ist(2026, 11, 5, 18), arrival_at=[obs(ist(2026, 11, 5, 16), S.consignee_gate_log)])
    c = ctx_for(deduction(2000), t, [FLAT])
    r = R.delay_at_all(c)
    assert r.status == CS.GROUND and r.ground == Ground.NO_DELAY and len(r.citations) == 2
    v = _verdict(c, [r])
    assert v.outcome == Outcome.disputable and v.amount_disputable == D("2000")


def test_delivery_definition_uses_unloading():
    # delivery defined as unloading (POD): arrived before due but unloaded after -> a delay exists
    t = trip(delivery_due_at=ist(2026, 11, 5, 18), arrival_at=[obs(ist(2026, 11, 5, 16), S.consignee_gate_log)],
             unloaded_at=[obs(ist(2026, 11, 6, 9), S.pod_signed)])
    d = term("definition", {"name": "delivery", "event": "unloaded_at"}, "T-def")
    assert R.delay_at_all(ctx_for(deduction(2000), t, [FLAT, d])).status == CS.NO_GROUND


def test_records_conflict_downgrades_only_dependent_checks():
    # arrival: consignee log 16:00 vs GPS 21:00 (5 h apart) -> conflict; the cap check does not depend on it
    t = trip(delivery_due_at=ist(2026, 11, 5, 18), freight=D("40000"),
             arrival_at=[obs(ist(2026, 11, 5, 16), S.consignee_gate_log), obs(ist(2026, 11, 5, 21), S.gps)])
    terms = [rule(1, "pct_per_vehicle_day", "trip_cost"), cap(15, "trip_cost")]
    c = ctx_for(deduction(10000), t, terms)  # cap 6,000 < 10,000 deducted
    r_delay, r_cap = R.delay_at_all(c), T.over_cap(c)
    assert r_delay.status == CS.INCOMPLETE and r_delay.reason == Reason.RECORDS_CONFLICT
    assert "Consignee gate log" in r_delay.missing_item and "GPS" in r_delay.missing_item
    assert r_cap.status == CS.GROUND
    v = _verdict(c, [r_delay, r_cap])
    assert v.outcome == Outcome.disputable and v.grounds == [Ground.OVER_CAP] and v.unresolved


def test_case5_client_caused_loading():
    # reported 08:00, gate-out 17:00 (client gate log) = 9 h vs 4 h free -> excused 5 h
    # due 5 Nov 10:00, arrived 7 Nov 12:00: 50 h -> 3 days = 6,000; shifted 45 h -> 2 days = 4,000; disputable 2,000
    t = trip(reporting_at=[obs(ist(2026, 11, 3, 8), S.client_gate_log, 3)],
             gate_out_at=[obs(ist(2026, 11, 3, 17), S.client_gate_log, 4)],
             delivery_due_at=ist(2026, 11, 5, 10), arrival_at=[obs(ist(2026, 11, 7, 12), S.consignee_gate_log)])
    c = ctx_for(deduction(6000), t, [FLAT, BUYER])
    rs = R.client_caused(c)
    assert [r.ground for r in rs] == [Ground.CLIENT_CAUSED] and rs[0].correction.kind == "excused_interval"
    v = _verdict(c, rs + [R.delay_at_all(c)])
    assert v.outcome == Outcome.disputable and v.amount_disputable == D("2000.00")


def test_client_caused_needs_buyer_clause():
    t = trip(reporting_at=[obs(ist(2026, 11, 3, 8), S.client_gate_log)], gate_out_at=[obs(ist(2026, 11, 3, 17), S.client_gate_log)])
    assert R.client_caused(ctx_for(deduction(6000), t, [FLAT]))[0].status == CS.NOT_APPLICABLE


def test_client_caused_on_our_records_only_asks_for_gate_log():
    t = trip(reporting_at=[obs(ist(2026, 11, 3, 8), S.driver_report)], gate_out_at=[obs(ist(2026, 11, 3, 17), S.gps)])
    r = R.client_caused(ctx_for(deduction(6000), t, [FLAT, BUYER]))[0]
    assert r.status == CS.INCOMPLETE and "client or consignee record" in r.missing_item


def test_wrong_start_point_placement():
    # placement measured to inspection pass 14:00; gate-in (reporting) at 09:00 per client gate log
    t = trip(placement_required_at=ist(2026, 11, 3, 8), reporting_at=[obs(ist(2026, 11, 3, 9), S.client_gate_log)])
    pr = rule(500, "per_instance", "none", event="late_placement")
    ded = deduction(500, reason=ReasonCode.late_placement, calc={"to_ts": ist(2026, 11, 3, 14).isoformat()})
    rs = R.start_point(ctx_for(ded, t, [pr]))
    end = [r for r in rs if r.check_id == "end_point"][0]
    assert end.status == CS.GROUND and end.ground == Ground.WRONG_START_POINT and end.correction.value["which"] == "to"


def test_regulatory_hold_memo_and_rule():
    hold = Hold(authority="GST mobile squad", place="Kini", held_from=ist(2026, 11, 4, 10), released_at=ist(2026, 11, 5, 16),
                memo_ref=SourceRef(file="memo.pdf", page=1))
    t = trip(holds=[hold], delivery_due_at=ist(2026, 11, 5, 10), arrival_at=[obs(ist(2026, 11, 6, 15), S.consignee_gate_log)])
    hr = term("hold_rule", {"covers_govt_detention": True}, "T-hold")
    c = ctx_for(deduction(4000), t, [FLAT, hr])
    rs = R.regulatory_hold(c)
    assert rs[0].ground == Ground.REGULATORY_HOLD
    # 29 h late -> 2 days = 4,000; 30 h hold moves due past arrival -> 0
    assert _verdict(c, rs).amount_disputable == D("4000.00")
    no_memo = trip(holds=[hold.model_copy(update={"memo_ref": None})])
    r = R.regulatory_hold(ctx_for(deduction(4000), no_memo, [FLAT, hr]))[0]
    assert r.status == CS.INCOMPLETE and "detention memo" in r.missing_item
    r2 = R.regulatory_hold(ctx_for(deduction(4000), t, [FLAT]))[0]
    assert r2.status == CS.REFER


def test_marker_reports_unexplained_delay():
    t = trip(delivery_due_at=ist(2026, 11, 5, 10), arrival_at=[obs(ist(2026, 11, 7, 12), S.consignee_gate_log)])
    assert "unexplained_delay=yes" in R.delay_remaining(ctx_for(deduction(6000), t, [FLAT])).note
    t2 = trip(delivery_due_at=ist(2026, 11, 5, 10), arrival_at=[obs(ist(2026, 11, 5, 9), S.consignee_gate_log)])
    assert "unexplained_delay=no" in R.delay_remaining(ctx_for(deduction(6000), t2, [FLAT])).note
