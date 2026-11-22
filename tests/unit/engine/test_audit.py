"""Auditor: tampering with a term, a record or a snapshot downgrades; the audit never upgrades."""
from __future__ import annotations

import random
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from pdr.codes import CheckStatus as CS, Ground, Outcome, Reason, Stage, TsSource as S
from pdr.contracts import RunContext
from pdr.engine import checks_terms as T
from pdr.engine import checks_records as R
from pdr.engine.audit import apply_audit, audit
from pdr.engine.combine import combine
from pdr.engine.penalty import recompute
from pdr.models import AuditFinding, CheckResult, Citation, Evidence, sha256_hex

from .b2_fixtures import cap, deduction, ist, obs, rule, settings, trip
from .test_checks_terms import ctx_for

D = Decimal


def _case6():
    tr = trip(delivery_due_at=ist(2026, 10, 10, 10), arrival_at=[obs(ist(2026, 10, 30, 10), S.consignee_gate_log)])
    terms = [rule(1, "pct_per_vehicle_day", "trip_cost"), cap(15, "trip_cost")]
    ded = deduction(100000, calc={"rate": "1", "unit": "pct_per_vehicle_day", "base_amount": "500000", "units_counted": "20"})
    c = ctx_for(ded, tr, terms)
    c.ctx.store.put(tr)
    rs = [T.wrong_base(c), T.over_cap(c), R.delay_at_all(c)]
    return c, rs


def _verdict(c, rs):
    corr = [r.correction for r in rs if r.status == CS.GROUND and r.correction]
    return combine(c.deduction, rs, recompute(c.deduction, c.trip, c.terms, corr, agreement=c.agreement))


def _evidence(c, v):
    return [e for e in (c.ctx.store.get(Evidence, i) for i in v.evidence_ids) if e is not None]


def test_clean_case_passes():
    c, rs = _case6()
    v = _verdict(c, rs)
    assert v.outcome == Outcome.disputable
    f = audit(v, _evidence(c, v), c.ctx, with_refuter=False)
    assert f and all(x.passed for x in f)


def test_term_status_tamper_downgrades():
    c, rs = _case6()
    v = _verdict(c, rs)
    st = c.ctx.store
    st.put(st.get("Term", "T-cap").model_copy(update={"status": "unconfirmed"}))
    st.put(st.get("Term", "T-rule").model_copy(update={"verbatim": "edited text"}))
    f = audit(v, _evidence(c, v), c.ctx)
    assert {x.citation_evidence_id for x in f if not x.passed} == {"term-T-cap", "term-T-rule"}
    rs2 = apply_audit(rs, f)
    assert all(r.status != CS.GROUND for r in rs2 if r.check_id in ("over_cap", "wrong_base"))
    assert {r.reason for r in rs2 if r.check_id in ("over_cap", "wrong_base")} == {Reason.TERMS_UNCONFIRMED}
    v2 = combine(c.deduction, rs2, recompute(c.deduction, c.trip, c.terms, []))
    assert v2.outcome == Outcome.cannot_verify


def test_record_tamper_downgrades():
    c, _ = _case6()
    st = c.ctx.store
    tr = trip(delivery_due_at=ist(2026, 10, 10, 10), arrival_at=[obs(ist(2026, 10, 9, 10), S.consignee_gate_log)])
    st.put(tr)
    c.trip = tr
    r = R.delay_at_all(c)
    assert r.ground == Ground.NO_DELAY
    v = _verdict(c, [r])
    # the arrival record is edited after the verdict was drafted
    st.put(tr.model_copy(update={"arrival_at": [obs(ist(2026, 10, 11, 10), S.consignee_gate_log)]}))
    f = audit(v, _evidence(c, v), c.ctx)
    assert any(not x.passed and x.rule == "internal_record" for x in f)
    assert apply_audit([r], f)[0].reason == Reason.RECORDS_CONFLICT


def _external(store, text="Trucks were stranded at Kini toll plaza on 5 November.", url="https://example.in/kini"):
    h = sha256_hex(text)
    store.snapshot_put(h, url, text, None)
    return Evidence(evidence_id="ext-1", kind="external_news", url=url, passage="stranded at Kini toll plaza",
                    published_at=datetime(2026, 11, 6, 9, tzinfo=timezone.utc), content_hash=h,
                    place_named="Kini", link=url, stance="supports")


def test_snapshot_tamper_and_redirect_fail():
    c, _ = _case6()
    st = c.ctx.store
    ev = _external(st)
    v = combine(c.deduction, [CheckResult(check_id="fm", stage=Stage.evidence_searched, status=CS.GROUND,
                                          ground=Ground.FORCE_MAJEURE,
                                          citations=[Citation(kind="external_source", evidence_id="ext-1", label="x", link="#")])],
                (D("0"), []))
    assert all(x.passed for x in audit(v, [ev], c.ctx))
    st._snapshots[ev.content_hash]["text"] = "Police said the highway was open."  # tampered snapshot
    f = audit(v, [ev], c.ctx)
    assert {x.rule for x in f if not x.passed} == {"external_snapshot", "external_passage"}
    st2 = c.ctx.store.__class__()
    ev2 = _external(st2, url="https://www.google.com/url?q=x")
    c.ctx.store = st2
    assert any(x.rule == "external_url" and not x.passed for x in audit(v, [ev2], c.ctx))


def test_refuter_none_keeps_mechanical_result(monkeypatch):
    import pdr.evidence
    c, _ = _case6()
    ev = _external(c.ctx.store)
    v = combine(c.deduction, [], (D("0"), []))
    monkeypatch.setattr(pdr.evidence, "refute", lambda e, claims, ctx: None)
    ref = [x for x in audit(v, [ev], c.ctx, with_refuter=True) if x.rule == "refuter"]
    assert ref and ref[0].passed and "did not finish" in ref[0].detail
    monkeypatch.setattr(pdr.evidence, "refute", lambda e, claims, ctx: [False] * len(claims))
    ref = [x for x in audit(v, [ev], c.ctx, with_refuter=True) if x.rule == "refuter"]
    assert ref and not ref[0].passed


def test_property_audit_never_upgrades():
    rng = random.Random(3)
    statuses = list(CS)
    for _ in range(2000):
        checks = []
        for i in range(rng.randint(1, 5)):
            st = rng.choice(statuses)
            checks.append(CheckResult(check_id=f"c{i}", stage=Stage.terms_checked, status=st,
                                      ground=rng.choice(list(Ground)) if st == CS.GROUND else None,
                                      reason=Reason.EVIDENCE_PARTIAL if st in (CS.INCOMPLETE, CS.REFER) else None,
                                      missing_item="x",
                                      citations=[Citation(kind=rng.choice(["contract_term", "internal_record", "external_source"]),
                                                          evidence_id=f"e{rng.randint(0, 4)}", label="l", link="#")]))
        findings = [AuditFinding(citation_evidence_id=f"e{j}", rule="r", passed=rng.random() < .5) for j in range(5)]
        ded = deduction(rng.randint(1, 5000))
        rec = (D(rng.randint(0, 6000)), [])
        before = combine(ded, checks, rec)
        after_checks = apply_audit(checks, findings)
        after = combine(ded, after_checks, rec)
        assert sum(r.status == CS.GROUND for r in after_checks) <= sum(r.status == CS.GROUND for r in checks)
        for a, b in zip(checks, after_checks):
            assert a.status == b.status or (a.status == CS.GROUND and b.status == CS.INCOMPLETE)
        if after.outcome == Outcome.disputable:
            assert before.outcome == Outcome.disputable and after.amount_disputable <= before.amount_disputable
        if before.outcome != Outcome.valid:
            assert after.outcome != Outcome.valid


def test_case13_superseded_version_citation_passes_audit():
    from datetime import date as _d
    old = rule(1, "pct_per_vehicle_day", "trip_cost", term_id="T-old", eff_to=_d(2026, 6, 30))
    new = rule("0.5", "pct_per_vehicle_day", "trip_cost", term_id="T-new", eff_from=_d(2026, 7, 1))
    tr = trip(delivery_due_at=ist(2026, 10, 10, 10), arrival_at=[obs(ist(2026, 10, 30, 10), S.consignee_gate_log)])
    ded = deduction(8000, calc={"rate": "1", "unit": "pct_per_vehicle_day", "base_amount": "40000", "units_counted": "20"})
    c = ctx_for(ded, tr, [old, new])
    c.ctx.store.put(tr)
    r = T.rate_version(c)
    v = _verdict(c, [r])
    f = audit(v, _evidence(c, v), c.ctx)
    assert v.outcome == Outcome.disputable and f and all(x.passed for x in f)
    # an "old version" that is in fact still in force fails
    c.ctx.store.put(old.model_copy(update={"effective_to": None}))
    assert not all(x.passed for x in audit(v, _evidence(c, v), c.ctx))
