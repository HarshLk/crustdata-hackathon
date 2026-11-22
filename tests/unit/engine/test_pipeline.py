"""Memoised pipeline: barrier, memo hits, settings-section recompute, supersede, deadline, never-raise property."""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from pdr.codes import CheckStatus, Ground, Outcome, Reason, Stage
from pdr.engine import pipeline, registry, run
from pdr.engine.pipeline import process
from pdr.models import Citation, CheckResult, Correction, Verdict

from tests.unit.engine.fixtures_b1 import ctx, deduction, stub_free_checks, unregister, world


@pytest.fixture(autouse=True)
def _free_checks():
    ids = stub_free_checks()
    yield
    unregister(ids)


@pytest.fixture
def no_evidence(monkeypatch):
    import pdr.evidence as ev
    from pdr.contracts import EvidenceResult
    monkeypatch.setattr(ev, "find_evidence", lambda req, c: EvidenceResult(status="none"))


def test_raising_check_gives_internal_error_and_run_continues(no_evidence):
    @registry.check(stage=Stage.records_checked, check_id="t_boom")
    def boom(c):
        if c.deduction.advice_no == "PA-1":
            raise KeyError("x")
        return CheckResult(check_id="t_boom", stage=Stage.records_checked, status=CheckStatus.NOT_APPLICABLE)
    try:
        store, keys = world(deds=[deduction("PA-1"), deduction("PA-2")])
        vs = run(keys, ctx(store))
    finally:
        registry._REGISTRY.pop("t_boom")
    assert len(vs) == 2
    v1 = vs[0]
    assert v1.outcome == Outcome.cannot_verify and Reason.INTERNAL_ERROR in v1.reasons
    assert any("check t_boom failed: KeyError" in m for m in v1.missing_items)
    assert vs[1].dispute_key == keys[1]


def test_second_process_executes_no_check(no_evidence, monkeypatch):
    store, keys = world()
    c = ctx(store)
    v1 = process(keys[0], c)
    calls = []
    real = pipeline.run_check
    monkeypatch.setattr(pipeline, "run_check", lambda spec, cc: calls.append(spec.check_id) or real(spec, cc))
    v2 = process(keys[0], c)
    assert calls == []
    assert v2.verdict_id == v1.verdict_id


def test_settings_change_recomputes_only_declaring_stages(no_evidence, monkeypatch):
    @registry.check(stage=Stage.records_checked, settings=["records"], check_id="t_reads_records")
    def reads(c):
        return CheckResult(check_id="t_reads_records", stage=Stage.records_checked, status=CheckStatus.NOT_APPLICABLE)
    try:
        store, keys = world()
        process(keys[0], ctx(store))
        stages = []
        real = pipeline.run_check
        monkeypatch.setattr(pipeline, "run_check", lambda spec, cc: stages.append(spec.stage) or real(spec, cc))
        c2 = ctx(store, **{"records.conflict_allowance_minutes": 5})
        v2 = process(keys[0], c2)
        declaring = {s for s in Stage if "records" in registry.settings_for_stage(s)}
    finally:
        registry._REGISTRY.pop("t_reads_records")
    assert set(stages) and set(stages) <= declaring
    assert Stage.received not in stages and Stage.matched not in stages
    assert v2.settings_version == c2.settings.version


def test_changed_input_supersedes_old_verdict(no_evidence):
    store, keys = world()
    v1 = process(keys[0], ctx(store))
    d = store.get("Deduction", keys[0])
    store.put(d.model_copy(update={"amount": Decimal("2500")}))
    v2 = process(keys[0], ctx(store))
    assert v2.verdict_id != v1.verdict_id
    assert store.get(Verdict, v1.verdict_id).superseded_by == v2.verdict_id
    assert [v.verdict_id for v in store.find(Verdict, dispute_key=keys[0]) if v.superseded_by is None] == [v2.verdict_id]


def test_resubmission_returns_existing_verdict(no_evidence):
    store, keys = world()
    v1 = process(keys[0], ctx(store))
    store.put(deduction())  # same dispute_key again, new created_at
    vs = run([keys[0], keys[0]], ctx(store, run_id="r2"))
    assert len(vs) == 1 and vs[0].verdict_id == v1.verdict_id


def test_deadline_gives_budget_reached():
    store, keys = world()
    c = ctx(store)
    c.deadline = datetime.now(timezone.utc) - timedelta(seconds=1)
    vs = run(keys, c)
    assert vs[0].outcome == Outcome.cannot_verify and vs[0].primary_reason == Reason.BUDGET_REACHED


def test_missing_check_module_fails_closed(no_evidence, monkeypatch):
    import sys
    monkeypatch.delitem(sys.modules, "pdr.engine.checks_terms", raising=False)
    store, keys = world()
    v = process(keys[0], ctx(store))
    assert v.outcome == Outcome.cannot_verify and Reason.INTERNAL_ERROR in v.reasons
    assert any("pdr.engine.checks_terms" in m for m in v.missing_items)


def test_missing_stage_checks_fail_closed(no_evidence):
    saved = {k: v for k, v in registry._REGISTRY.items() if v.stage == Stage.terms_checked}
    for k in saved:
        registry._REGISTRY.pop(k)
    try:
        store, keys = world()
        v = process(keys[0], ctx(store))
    finally:
        registry._REGISTRY.update(saved)
    assert v.outcome == Outcome.cannot_verify and Reason.INTERNAL_ERROR in v.reasons


# ----------------------------------------------------------------------------- property: never raised

def _rand_checks(rng):
    out = []
    for i in range(rng.randint(1, 5)):
        st = rng.choice(list(CheckStatus))
        ground = rng.choice([Ground.WRONG_RATE, Ground.OVER_CAP, Ground.NO_DELAY]) if st == CheckStatus.GROUND else None
        reason = rng.choice([Reason.EVIDENCE_PARTIAL, Reason.RECORDS_CONFLICT]) if st in (CheckStatus.INCOMPLETE, CheckStatus.REFER) else None
        cit = Citation(kind=rng.choice(["contract_term", "internal_record", "external_source"]),
                       evidence_id=f"e{rng.randint(0, 3)}", label="x", link="#")
        corr = Correction(kind="void", value={"ground": "NO_DELAY"}) if ground == Ground.NO_DELAY else None
        out.append(CheckResult(check_id=f"c{i}", stage=rng.choice([Stage.terms_checked, Stage.records_checked]),
                               status=st, ground=ground, reason=reason, citations=[cit], correction=corr))
    return out


def test_property_audit_never_raises_outcome(monkeypatch):
    audit_mod = pytest.importorskip("pdr.engine.audit")
    from pdr.models import AuditFinding
    rng = random.Random(7)
    store, keys = world()
    c = ctx(store)
    st = pipeline.load_state(keys[0], c)
    for _ in range(150):
        checks = _rand_checks(rng)
        fails = {f"e{i}" for i in range(4) if rng.random() < 0.5}
        monkeypatch.setattr(audit_mod, "audit", lambda v, ev, cc, with_refuter=False, _f=fails: [
            AuditFinding(citation_evidence_id=e, rule="t", passed=e not in _f) for e in ("e0", "e1", "e2", "e3")])
        base = pipeline._combine(st, checks)
        final, _, _ = pipeline.audited_verdict(st, checks, c)
        if base.outcome != Outcome.disputable:
            assert final.outcome == base.outcome
        else:
            assert final.outcome in (Outcome.disputable, Outcome.cannot_verify)
            assert final.amount_disputable <= base.amount_disputable


def test_no_raise_guard():
    v = Verdict(verdict_id="v", deduction_id="d", dispute_key="k", outcome=Outcome.cannot_verify, amount_deducted=Decimal("10"))
    up = v.model_copy(update={"outcome": Outcome.disputable, "amount_disputable": Decimal("10")})
    assert pipeline.no_raise(v, up).outcome == Outcome.cannot_verify
