"""Combine check results into a verdict: the DESIGN 5.9 truth table (D-04, D-25). Pure."""
from __future__ import annotations

from decimal import Decimal
from typing import Iterable, Optional

from pdr.codes import CheckStatus, Flag, Ground, Outcome, Reason, Stage, primary_reason
from pdr.engine.penalty import amount_disputable
from pdr.engine.timeline import TIMELINE_EVENTS
from pdr.models import CheckResult, Deduction, RecomputeLine, Verdict, canonical_json, sha256_hex

OPEN = (CheckStatus.INCOMPLETE, CheckStatus.REFER)

NEXT_ACTION: dict[Reason, str] = {
    Reason.TERMS_MISSING: "request the missing document from the account manager",
    Reason.TERMS_UNCONFIRMED: "confirm the extracted term on the review screen",
    Reason.TRIP_NOT_FOUND: "find the trip record",
    Reason.AMBIGUOUS_MATCH: "a person picks the right client or trip",
    Reason.RECORDS_CONFLICT: "a person reconciles the conflicting records",
    Reason.NOTICE_EVIDENCE_MISSING: "ask the account manager for the notice email",
    Reason.EXPLANATION_IGNORED: "to account manager: the client decides if the explanation is satisfactory",
    Reason.LEGAL_JUDGMENT: "refer to legal",
}
DEFEATERS: dict[Ground, str] = {
    Ground.NO_CONTRACTUAL_BASIS: "a signed document in the agreement set that penalises this event",
    Ground.WRONG_RATE: "an amendment in force on the trip date at the rate the client applied",
    Ground.WRONG_BASE: "a clause naming the base the client used",
    Ground.OVER_CAP: "a later clause raising or removing the cap",
    Ground.DUPLICATE: "proof that the two advices penalise different events",
    Ground.WRONG_DAY_COUNT: "a clause counting calendar days, or a different delay period",
    Ground.NO_DELAY: "a delivery record showing a later delivery time",
    Ground.WRONG_START_POINT: "a clause defining the start at the event the client used",
    Ground.CLIENT_CAUSED: "a client gate log showing a different loading or unloading time",
    Ground.REGULATORY_HOLD: "a finding that the hold was the 3PL's fault",
    Ground.FORCE_MAJEURE: "a record placing the truck away from the disruption, or a source saying the road was open",
}


def downgrade(checks: list[CheckResult], conflicted: Iterable[str] = (), unconfirmed: Iterable[str] = ()) -> list[CheckResult]:
    """GROUNDs depending on a conflicted timestamp or unconfirmed term become INCOMPLETE."""
    conf, unc = set(conflicted), set(unconfirmed)
    out = []
    for c in checks:
        if c.status == CheckStatus.GROUND:
            deps = set(c.depends_on)
            if deps & conf:
                ev = sorted(deps & conf)
                c = c.model_copy(update={"status": CheckStatus.INCOMPLETE, "reason": Reason.RECORDS_CONFLICT,
                                         "missing_item": f"records disagree on {', '.join(ev)}; no single governing record",
                                         "note": (c.note + " | downgraded: depends on a conflicted timestamp").strip(" |")})
            elif deps & unc:
                ts = sorted(deps & unc)
                c = c.model_copy(update={"status": CheckStatus.INCOMPLETE, "reason": Reason.TERMS_UNCONFIRMED,
                                         "missing_item": f"term {', '.join(ts)} not yet confirmed",
                                         "note": (c.note + " | downgraded: depends on an unconfirmed term").strip(" |")})
        out.append(c)
    return out


def _missing(c: CheckResult) -> str:
    return c.missing_item or f"check {c.check_id}: {c.reason.value if c.reason else c.status.value}"


def _uniq(xs):
    seen, out = set(), []
    for x in xs:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def combine(deduction: Deduction, checks: list[CheckResult],
            recomputed: Optional[tuple[Decimal, list[RecomputeLine]]], *,
            settings_version: str = "", run_id: str = "", conflicted: Iterable[str] = (),
            unconfirmed: Iterable[str] = (), rule_version: str = "", inputs_hash: str = "",
            verdict_id: Optional[str] = None, explanation: str = "") -> Verdict:
    """Verdict from check results and the recomputation (None if it could not be computed)."""
    checks = downgrade(list(checks), conflicted, unconfirmed)
    deducted = Decimal(deduction.amount)
    correct, lines = (recomputed if recomputed is not None else (None, []))
    flags = _uniq([Flag(f) for c in checks for f in c.flags] + [Flag(f) for f in deduction.flags])
    grounds = _uniq([c.ground for c in checks if c.status == CheckStatus.GROUND and c.ground])
    open_checks = [c for c in checks if c.status in OPEN]
    gate_open = [c for c in open_checks if c.stage == Stage.matched and c.status == CheckStatus.INCOMPLETE]
    disputable_amt = amount_disputable(deducted, correct) if correct is not None else Decimal(0)

    reasons: list[Reason] = []
    missing: list[str] = []
    unresolved: list[str] = []
    if gate_open:
        outcome = Outcome.cannot_verify
        reasons = _uniq([c.reason or Reason.INTERNAL_ERROR for c in gate_open])
        missing = _uniq([_missing(c) for c in gate_open])
        grounds = []
    elif grounds and correct is not None and disputable_amt > 0:
        outcome = Outcome.disputable
        unresolved = _uniq([f"{c.check_id}: {(c.reason.value if c.reason else c.status.value)}: {_missing(c)}"
                            for c in open_checks])
        reasons = _uniq([c.reason for c in open_checks if c.reason])
    elif open_checks or (grounds and correct is None):
        outcome = Outcome.cannot_verify
        reasons = _uniq([c.reason or Reason.INTERNAL_ERROR for c in open_checks])
        missing = _uniq([_missing(c) for c in open_checks])
        if grounds and correct is None and not open_checks:
            reasons, missing = [Reason.INTERNAL_ERROR], ["recomputation of the correct penalty did not finish"]
        grounds = []
    else:
        outcome = Outcome.valid
        grounds = []

    primary = primary_reason(reasons) if outcome == Outcome.cannot_verify else None
    if outcome == Outcome.cannot_verify:
        reasons = sorted(reasons, key=lambda r: (r != primary,))
    if Flag.OUT_OF_WINDOW in flags:
        next_action = "held for account manager"
    elif outcome == Outcome.disputable:
        next_action = "draft dispute letter for approval"
    elif outcome == Outcome.valid:
        next_action = "accept and record"
    else:
        next_action = NEXT_ACTION.get(primary, "refer to a person") + (f": {missing[0]}" if missing else "")

    if outcome == Outcome.cannot_verify:
        wwc = list(missing)
    elif outcome == Outcome.disputable:
        wwc = [DEFEATERS[g] for g in grounds if g in DEFEATERS]
    else:
        wwc = []

    evidence_ids = _uniq([cit.evidence_id for c in checks for cit in c.citations]
                         + [cit.evidence_id for l in lines for cit in l.citations])
    terms_applied = _uniq([d for c in checks for d in c.depends_on if d not in TIMELINE_EVENTS])
    vid = verdict_id or (deduction.dispute_key + ":" + sha256_hex(canonical_json(
        [inputs_hash, settings_version, rule_version, [c.model_dump(mode="json") for c in checks],
         str(correct)]))[:12])
    return Verdict(
        verdict_id=vid, deduction_id=deduction.deduction_id, dispute_key=deduction.dispute_key,
        client_id=deduction.client_id, outcome=outcome, grounds=grounds, primary_reason=primary,
        reasons=reasons, flags=flags, amount_deducted=deducted, recomputed_penalty=correct,
        amount_disputable=disputable_amt if outcome == Outcome.disputable else Decimal(0),
        recomputation=list(lines), terms_applied=terms_applied, evidence_ids=evidence_ids,
        missing_items=missing, unresolved=unresolved, explanation=explanation, next_action=next_action,
        what_would_change=wwc, checks_run=_uniq([c.check_id for c in checks]), inputs_hash=inputs_hash,
        rule_version=rule_version, settings_version=settings_version, run_id=run_id)
