"""Stage 3 term checks: cases 4, 6, 9, 13, 20 and the waiver/legal referrals."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from pdr.codes import CheckStatus as CS, Flag, Ground, Outcome, Reason, ReasonCode, Stage, TsSource
from pdr.contracts import CheckContext, RunContext
from pdr.engine import checks_terms as T
from pdr.engine.combine import combine
from pdr.engine.penalty import recompute
from pdr.models import NoticeRecord, SourceRef
from pdr.store_mem import MemStore

from .b2_fixtures import agreement, cap, deduction, ist, obs, rule, settings, term, trip

D = Decimal


def ctx_for(ded, tr, terms, ag=None, store=None, today=None):
    store = store or MemStore()
    rc = RunContext(run_id="r1", dispute_key=ded.dispute_key, settings=settings(), store=store,
                    deadline=datetime.now(timezone.utc) + timedelta(hours=1))
    if today:
        rc.today = today
    ag = ag or agreement()
    store.put(ag)
    for t in terms:
        store.put(t)
    store.put(ded)
    return CheckContext(deduction=ded, trip=tr, client=None, agreement=ag, terms=terms, prior={}, ctx=rc)


def late_trip(**kw):
    return trip(delivery_due_at=ist(2026, 10, 10, 10), arrival_at=[obs(ist(2026, 10, 30, 10), TsSource.consignee_gate_log)], **kw)


def verdict_of(c, results):
    corr = [r.correction for r in results if r.status == CS.GROUND and r.correction]
    return combine(c.deduction, results, recompute(c.deduction, c.trip, c.terms, corr, agreement=c.agreement))


def test_case6_wrong_base_and_over_cap():
    # 20 days; client 1% x 5,00,000 x 20 = 1,00,000; correct 1% x 40,000 x 20 = 8,000 capped at 15% x 40,000 = 6,000
    terms = [rule(1, "pct_per_vehicle_day", "trip_cost"), cap(15, "trip_cost", scope="per_vehicle")]
    ded = deduction(100000, calc={"rate": "1", "unit": "pct_per_vehicle_day", "base_amount": "500000", "units_counted": "20"})
    c = ctx_for(ded, late_trip(), terms)
    rs = [T.contractual_basis(c), T.rate_version(c), T.wrong_base(c), T.over_cap(c), T.day_count(c), T.duplicate(c)]
    by = {r.check_id: r for r in rs}
    assert by["wrong_base"].status == CS.GROUND and by["over_cap"].status == CS.GROUND
    assert by["rate_version"].status == CS.NO_GROUND and by["day_count"].status == CS.NO_GROUND
    assert by["over_cap"].citations[0].label.startswith("Sample transport SLA, clause")
    v = verdict_of(c, rs)
    assert v.outcome == Outcome.disputable and set(v.grounds) == {Ground.WRONG_BASE, Ground.OVER_CAP}
    assert v.amount_disputable == D("94000.00")  # 1,00,000 - 6,000


def test_case13_old_rate_cites_both_versions():
    # old 1% to 30 Jun, new 0.5% from 1 Jul; 20 days x 40,000: client 8,000, correct 4,000, difference 4,000
    old = rule(1, "pct_per_vehicle_day", "trip_cost", term_id="T-old", eff_to=date(2026, 6, 30))
    new = rule("0.5", "pct_per_vehicle_day", "trip_cost", term_id="T-new", eff_from=date(2026, 7, 1), clause="8.1A")
    ded = deduction(8000, calc={"rate": "1", "unit": "pct_per_vehicle_day", "base_amount": "40000", "units_counted": "20"})
    c = ctx_for(ded, late_trip(), [old, new])
    r = T.rate_version(c)
    assert r.status == CS.GROUND and r.ground == Ground.WRONG_RATE
    assert {x.evidence_id for x in r.citations} == {"term-T-new", "term-superseded-T-old"}
    v = verdict_of(c, [r])
    assert v.amount_disputable == D("4000.00")


def test_case13_without_stated_rate_matches_old_version_amount():
    old = rule(1, "pct_per_vehicle_day", "trip_cost", term_id="T-old", eff_to=date(2026, 6, 30))
    new = rule("0.5", "pct_per_vehicle_day", "trip_cost", term_id="T-new", eff_from=date(2026, 7, 1))
    c = ctx_for(deduction(8000), late_trip(), [old, new])
    assert T.rate_version(c).ground == Ground.WRONG_RATE


def test_case20_no_contractual_basis_needs_complete_set():
    terms = [rule(1, "pct_per_vehicle_day", "trip_cost")]
    ded = deduction(5000, reason=ReasonCode.detention_charge)
    r = T.contractual_basis(ctx_for(ded, late_trip(), terms))
    assert r.status == CS.GROUND and r.ground == Ground.NO_CONTRACTUAL_BASIS and r.correction.kind == "void"
    r2 = T.contractual_basis(ctx_for(ded, late_trip(), terms, ag=agreement(completeness="missing",
                                                                           missing_documents=["Annexure C"])))
    assert r2.status == CS.INCOMPLETE and r2.reason == Reason.TERMS_MISSING and "Annexure C" in r2.missing_item


def test_case4_duplicate_later_advice_only():
    store = MemStore()
    first = deduction(3000, advice="PA1", ded_date=date(2026, 11, 2))
    second = deduction(3000, advice="PA7", ded_date=date(2026, 11, 9))
    store.put(first)
    terms = [rule(1000, "flat_per_day", "none")]
    c2 = ctx_for(second, late_trip(), terms, store=store)
    r2 = T.duplicate(c2)
    assert r2.status == CS.GROUND and r2.ground == Ground.DUPLICATE and len(r2.citations) == 2
    assert recompute(second, c2.trip, terms, [r2.correction])[0] == 0
    c1 = ctx_for(first, late_trip(), terms, store=store)
    assert T.duplicate(c1).status == CS.NO_GROUND


def test_case9_out_of_window_flag_only():
    lim = term("time_limit", {"window_days": 30, "day_count": "calendar", "counted_from": "deduction_date"}, "T-lim")
    ded = deduction(1000, ded_date=date(2026, 9, 1))
    r = T.dispute_window(ctx_for(ded, late_trip(), [lim], today=date(2026, 11, 22)))
    assert r.status == CS.NO_GROUND and Flag.OUT_OF_WINDOW in r.flags
    r_in = T.dispute_window(ctx_for(deduction(1000, ded_date=date(2026, 11, 10)), late_trip(), [lim], today=date(2026, 11, 22)))
    assert not r_in.flags


def test_working_day_count_without_holiday_list_is_incomplete_when_no_sunday_error():
    # due Mon 2 Nov 10:00, arrived Tue 3 Nov 10:00; client counted 1 day (correct on Sundays); no holiday list
    tr = trip(delivery_due_at=ist(2026, 11, 2, 10), arrival_at=[obs(ist(2026, 11, 3, 10), TsSource.consignee_gate_log)])
    terms = [rule(2000, "flat_per_day", "none", day_count="working")]
    r = T.day_count(ctx_for(deduction(2000, calc={"units_counted": "1"}), tr, terms))
    assert r.status == CS.INCOMPLETE and "holiday list" in r.missing_item
    # Sunday counted: due Sat 7 Nov 10:00 -> Mon 9 Nov 10:00 is 1 working day; client counted 2 -> ground
    tr2 = trip(delivery_due_at=ist(2026, 11, 7, 10), arrival_at=[obs(ist(2026, 11, 9, 10), TsSource.consignee_gate_log)])
    r2 = T.day_count(ctx_for(deduction(4000, calc={"units_counted": "2"}), tr2, terms))
    assert r2.status == CS.GROUND and r2.ground == Ground.WRONG_DAY_COUNT


def test_waiver_with_explanation_refers():
    w = term("waiver", {"condition": "satisfactory_explanation"}, "T-w")
    tr = late_trip(notices=[NoticeRecord(sent_at=ist(2026, 10, 11, 9), channel="email", addressee="client ops",
                                         ref=SourceRef(file="mail.eml"))])
    r = T.explanation_waiver(ctx_for(deduction(1000), tr, [w]))
    assert r.status == CS.REFER and r.reason == Reason.EXPLANATION_IGNORED and r.missing_item


def test_loss_claim_is_legal_judgment():
    r = T.legal_judgment(ctx_for(deduction(1000, reason=ReasonCode.shortage), late_trip(), []))
    assert r.status == CS.REFER and r.reason == Reason.LEGAL_JUDGMENT
    assert T.legal_judgment(ctx_for(deduction(1000), late_trip(), [])).status == CS.NOT_APPLICABLE


def test_no_cap_clause_not_applicable():
    r = T.over_cap(ctx_for(deduction(1000), late_trip(), [rule(1, "pct_per_vehicle_day", "trip_cost")]))
    assert r.status == CS.NOT_APPLICABLE
