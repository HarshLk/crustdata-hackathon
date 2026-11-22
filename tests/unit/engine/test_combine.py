"""combine(): DESIGN 5.9 truth table, precedence, downgrades, flags; seeded property tests."""
from __future__ import annotations

import random
from decimal import Decimal

from pdr.codes import CheckStatus as CS, Flag, Ground, Outcome, Reason, Stage
from pdr.engine.combine import combine
from pdr.models import CheckResult, Citation

from .b2_fixtures import deduction

D = Decimal


def cr(cid, status, stage=Stage.terms_checked, ground=None, reason=None, missing=None, deps=(), flags=()):
    return CheckResult(check_id=cid, stage=stage, status=status, ground=ground, reason=reason, missing_item=missing,
                       depends_on=list(deps), flags=list(flags),
                       citations=[Citation(kind="contract_term", evidence_id=f"ev-{cid}", label=cid, link="#")])


def test_valid_when_all_finished_without_ground():
    v = combine(deduction(1000), [cr("a", CS.NO_GROUND), cr("b", CS.NOT_APPLICABLE)], (D("1000"), []))
    assert v.outcome == Outcome.valid and v.amount_disputable == 0 and v.checks_run == ["a", "b"]


def test_gate_incomplete_wins():
    checks = [cr("gate", CS.INCOMPLETE, Stage.matched, reason=Reason.TRIP_NOT_FOUND, missing="trip LR9"),
              cr("rate", CS.GROUND, ground=Ground.WRONG_RATE)]
    v = combine(deduction(1000), checks, (D("100"), []))
    assert v.outcome == Outcome.cannot_verify and v.primary_reason == Reason.TRIP_NOT_FOUND
    assert v.missing_items == ["trip LR9"] and v.amount_disputable == 0


def test_ground_plus_incomplete_disputable_for_supported_amount():
    checks = [cr("cap", CS.GROUND, ground=Ground.OVER_CAP),
              cr("fm", CS.INCOMPLETE, Stage.evidence_searched, reason=Reason.EVIDENCE_PARTIAL, missing="GPS extract")]
    v = combine(deduction(10000), checks, (D("6000"), []))
    assert v.outcome == Outcome.disputable and v.amount_disputable == D("4000")
    assert v.grounds == [Ground.OVER_CAP] and any("GPS extract" in u for u in v.unresolved)


def test_primary_reason_by_precedence():
    checks = [cr("a", CS.REFER, reason=Reason.LEGAL_JUDGMENT, missing="no-real-loss claim"),
              cr("b", CS.INCOMPLETE, reason=Reason.RECORDS_CONFLICT, missing="gate log vs POD")]
    v = combine(deduction(1000), checks, (D("1000"), []))
    assert v.primary_reason == Reason.RECORDS_CONFLICT and set(v.reasons) == {Reason.LEGAL_JUDGMENT, Reason.RECORDS_CONFLICT}
    assert len(v.missing_items) == 2


def test_conflicted_dependency_downgrades_only_dependent_ground():
    checks = [cr("nodelay", CS.GROUND, Stage.records_checked, ground=Ground.NO_DELAY, deps=["arrival"]),
              cr("cap", CS.GROUND, ground=Ground.OVER_CAP, deps=["T-cap"])]
    v = combine(deduction(10000), checks, (D("6000"), []), conflicted={"arrival"})
    assert v.outcome == Outcome.disputable and v.grounds == [Ground.OVER_CAP]
    assert any("RECORDS_CONFLICT" in u for u in v.unresolved)
    # alone, the conflicted ground cannot carry the verdict
    v2 = combine(deduction(10000), checks[:1], (D("0"), []), conflicted={"arrival"})
    assert v2.outcome == Outcome.cannot_verify and v2.primary_reason == Reason.RECORDS_CONFLICT


def test_unconfirmed_term_downgrades():
    v = combine(deduction(1000), [cr("r", CS.GROUND, ground=Ground.WRONG_RATE, deps=["T-new"])], (D("500"), []),
                unconfirmed={"T-new"})
    assert v.outcome == Outcome.cannot_verify and v.primary_reason == Reason.TERMS_UNCONFIRMED


def test_ground_without_recomputation_is_not_disputable():
    v = combine(deduction(1000), [cr("r", CS.GROUND, ground=Ground.WRONG_RATE)], None)
    assert v.outcome == Outcome.cannot_verify and v.missing_items


def test_out_of_window_flag_holds_but_never_changes_outcome():
    base = [cr("cap", CS.GROUND, ground=Ground.OVER_CAP)]
    flagged = base + [cr("window", CS.NO_GROUND, flags=[Flag.OUT_OF_WINDOW])]
    a = combine(deduction(1000), base + [cr("window", CS.NO_GROUND)], (D("600"), []))
    b = combine(deduction(1000), flagged, (D("600"), []))
    assert a.outcome == b.outcome == Outcome.disputable and a.amount_disputable == b.amount_disputable
    assert b.next_action == "held for account manager" and Flag.OUT_OF_WINDOW in b.flags


# ------------------------------------------------------------------ seeded property tests

STATUSES = list(CS)
REASONS = list(Reason)


def _random_checks(rng):
    out = []
    for i in range(rng.randint(0, 6)):
        st = rng.choice(STATUSES)
        stage = rng.choice([Stage.matched, Stage.terms_checked, Stage.records_checked, Stage.evidence_searched])
        out.append(cr(f"c{i}", st, stage,
                      ground=rng.choice(list(Ground)) if st == CS.GROUND else None,
                      reason=rng.choice(REASONS) if st in (CS.INCOMPLETE, CS.REFER) and rng.random() < .9 else None,
                      missing=f"item {i}" if rng.random() < .7 else None,
                      deps=rng.sample(["arrival", "reporting", "T-rule", "T-cap"], rng.randint(0, 2)),
                      flags=[Flag.OUT_OF_WINDOW] if rng.random() < .2 else []))
    return out


def test_property_incomplete_without_ground_never_valid_or_disputable():
    rng = random.Random(7)
    for _ in range(3000):
        checks = _random_checks(rng)
        deducted = D(rng.randint(1, 10000))
        correct = D(rng.randint(0, 12000))
        recomputed = None if rng.random() < .1 else (correct, [])
        conflicted = set(rng.sample(["arrival", "reporting"], rng.randint(0, 1)))
        v = combine(deduction(deducted), checks, recomputed, conflicted=conflicted)
        live_grounds = [c for c in checks if c.status == CS.GROUND and not (set(c.depends_on) & conflicted)]
        has_open = any(c.status in (CS.INCOMPLETE, CS.REFER) for c in checks) or len(live_grounds) < \
            len([c for c in checks if c.status == CS.GROUND])
        if has_open and not live_grounds:
            assert v.outcome == Outcome.cannot_verify
        if v.outcome == Outcome.cannot_verify:
            assert v.missing_items and v.primary_reason is not None and v.amount_disputable == 0
        if v.outcome == Outcome.disputable:
            assert live_grounds and recomputed is not None
            assert v.amount_disputable == min(max(deducted - correct, D(0)), deducted) > 0
            assert len(v.unresolved) == len({u for u in v.unresolved})
            n_open = len([c for c in checks if c.status in (CS.INCOMPLETE, CS.REFER)]) + \
                len([c for c in checks if c.status == CS.GROUND and set(c.depends_on) & conflicted])
            assert len(v.unresolved) == n_open
        if v.outcome == Outcome.valid:
            assert not has_open and v.amount_disputable == 0
        # flags never change the outcome
        stripped = [c.model_copy(update={"flags": []}) for c in checks]
        assert combine(deduction(deducted), stripped, recomputed, conflicted=conflicted).outcome == v.outcome
