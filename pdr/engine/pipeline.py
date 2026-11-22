"""Memoised stage pipeline `process` (DESIGN 5.3, 5.9; D-20, D-25)."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from pdr.codes import STAGE_ORDER, CheckStatus, Flag, Ground, Outcome, Reason, ReasonCode, Stage
from pdr.contracts import CheckContext, RunContext
from pdr.crashpoints import crashpoint
from pdr.engine import gate
from pdr.engine.hashing import RULE_VERSION, inputs_hash, results_hash
from pdr.engine.registry import checks_for, run_check
from pdr.models import CheckResult, Deduction, Evidence, Verdict

log = logging.getLogger("pdr.engine")

TRANSIENT = {Reason.BUDGET_REACHED, Reason.PROVIDER_FAILED}
DELAY_CODES = {ReasonCode.late_delivery, ReasonCode.late_placement, ReasonCode.non_placement,
               ReasonCode.late_reporting, ReasonCode.late_paperwork}
VOID_GROUNDS = {Ground.NO_CONTRACTUAL_BASIS, Ground.DUPLICATE, Ground.NO_DELAY}
REQUIRED_STAGES = {Stage.received, Stage.matched, Stage.terms_checked, Stage.records_checked}
# stage -> check module that must be loaded before the stage may finish (valid needs every check to run)
REQUIRED_MODULES = {Stage.matched: "pdr.engine.gate", Stage.terms_checked: "pdr.engine.checks_terms",
                    Stage.records_checked: "pdr.engine.checks_records", Stage.notice_checked: "pdr.engine.checks_notice",
                    Stage.evidence_searched: "pdr.engine.evidence_stage"}


def missing_check_modules(stage: Stage) -> list[CheckResult]:
    """INCOMPLETE/INTERNAL_ERROR when a stage's check module failed to load or the stage has no checks."""
    import sys
    mod = REQUIRED_MODULES.get(stage)
    if mod and mod not in sys.modules:
        return [CheckResult(check_id=f"{stage.value}_checks", stage=stage, status=CheckStatus.INCOMPLETE,
                            reason=Reason.INTERNAL_ERROR,
                            missing_item=f"check module {mod} failed: ModuleNotLoaded")]
    if stage in REQUIRED_STAGES and not checks_for(stage):
        return [CheckResult(check_id=f"{stage.value}_checks", stage=stage, status=CheckStatus.INCOMPLETE,
                            reason=Reason.INTERNAL_ERROR,
                            missing_item=f"check {stage.value} failed: NoChecksRegistered")]
    return []
_RANK = {Outcome.valid: 0, Outcome.cannot_verify: 0, Outcome.disputable: 1}


@dataclass
class State:
    key: str
    deduction: Deduction
    identity: gate.Identity
    c: CheckContext
    prior: dict = field(default_factory=dict)  # Stage -> list[CheckResult]
    hashes: list = field(default_factory=list)  # results hashes of completed stages, in order
    executed: list = field(default_factory=list)  # stages actually run (not memo hits)
    stopped: Optional[Stage] = None  # stage where the deadline stopped the run
    stage_hashes: dict = field(default_factory=dict)  # Stage -> inputs hash used (decision log times)


def _ctx_for(c: RunContext, key: str) -> RunContext:
    try:
        return RunContext(run_id=c.run_id, dispute_key=key, settings=c.settings, store=c.store,
                          deadline=c.deadline, spent_credits=c.spent_credits)
    except Exception:  # noqa: BLE001
        return c


def load_state(key: str, ctx: RunContext) -> State | None:
    """Load the deduction and resolve its identity (free, deterministic)."""
    ded = ctx.store.get(Deduction, key)
    if ded is None:
        log.warning("no deduction with dispute_key %s", key)
        return None
    idn = gate.resolve(ded, ctx.store)
    c = CheckContext(deduction=ded, trip=idn.trip, client=idn.client, agreement=idn.agreement,
                     terms=list(idn.terms), prior={}, ctx=ctx)
    c.identity = idn  # read by the gate check; avoids resolving twice
    return State(key=key, deduction=ded, identity=idn, c=c)


def stage_hash(st: State, stage: Stage, ctx: RunContext) -> str:
    extra = None if stage == Stage.received else st.identity.summary()
    if stage == Stage.verdict:  # a new settings version always yields a newly stamped verdict (no checks re-run)
        extra = {"identity": extra, "settings_version": ctx.settings.version}
    trip = None if stage == Stage.received else st.c.trip
    terms = [] if stage == Stage.received else st.c.terms + st.identity.unconfirmed
    return inputs_hash(stage, st.deduction, trip, terms, ctx.settings, st.hashes, extra=extra)


def run_stage(st: State, stage: Stage, ctx: RunContext) -> list[CheckResult]:
    """Run one stage, or reuse its stored results when its inputs are unchanged."""
    h = stage_hash(st, stage, ctx)
    st.stage_hashes[stage] = h
    stored = ctx.store.load_stage(st.key, stage, h)
    if stored is not None and not any(r.reason in TRANSIENT for r in stored):
        results = stored
    else:
        st.c.prior = dict(st.prior)
        results = []
        results.extend(missing_check_modules(stage))
        for spec in checks_for(stage):
            for r in run_check(spec, st.c):
                if r.stage != stage:
                    r = r.model_copy(update={"stage": stage})
                results.append(r)
        crashpoint(f"stage.before_save.{stage.value}")
        ctx.store.save_stage(st.key, stage, h, results)
        crashpoint(f"stage.after_save.{stage.value}")
        st.executed.append(stage)
    st.prior[stage] = results
    st.hashes.append(results_hash(results))
    return results


def _all(st: State) -> list[CheckResult]:
    return [r for s in STAGE_ORDER if s in st.prior for r in st.prior[s]]


def _corrections(results) -> list:
    return [r.correction for r in results if r.status == CheckStatus.GROUND and r.correction is not None]


def _recompute(st: State, results):
    from pdr.engine.penalty import recompute
    return recompute(st.deduction, st.c.trip, st.c.terms, _corrections(results), agreement=st.c.agreement)


def unexplained_delay(st: State) -> bool:
    """True when a delay penalty still stands after stages 3-4, so notice, wording and evidence matter."""
    if st.deduction.reason_code not in DELAY_CODES:
        return False
    rs = st.prior.get(Stage.terms_checked, []) + st.prior.get(Stage.records_checked, [])
    if any(r.status == CheckStatus.INCOMPLETE for r in rs):
        return False  # cannot verify already; evidence cannot settle it, so no paid call
    if any(r.status == CheckStatus.GROUND and r.ground in VOID_GROUNDS for r in rs):
        return False
    try:
        penalty, _ = _recompute(st, rs)
        if penalty is not None and Decimal(penalty) <= 0:
            return False
    except Exception:  # noqa: BLE001 - unknown means keep checking
        pass
    return True


def _gate_failed(st: State) -> bool:
    return any(r.status == CheckStatus.INCOMPLETE for r in st.prior.get(Stage.matched, []))


def _budget_stop(stage: Stage) -> CheckResult:
    return CheckResult(check_id="run_deadline", stage=stage, status=CheckStatus.INCOMPLETE,
                       reason=Reason.BUDGET_REACHED,
                       missing_item=f"run's time or cost limit reached before stage {stage.value}; run again to finish",
                       note="stopped by the run deadline")


def advance(st: State, ctx: RunContext, upto: Stage = Stage.verdict) -> str:
    """Run stages 1-6 as needed. Returns 'verdict' (ready to decide), 'pending' (needs evidence) or 'stopped'."""
    plan_stages = [Stage.received, Stage.matched]
    for stage in plan_stages:
        if _deadline(st, stage, ctx):
            return "verdict"
        run_stage(st, stage, ctx)
    if _gate_failed(st):
        return "verdict"
    for stage in (Stage.terms_checked, Stage.records_checked):
        if _deadline(st, stage, ctx):
            return "verdict"
        run_stage(st, stage, ctx)
    if STAGE_ORDER.index(upto) < STAGE_ORDER.index(Stage.notice_checked):
        return "stopped"
    if not unexplained_delay(st):
        return "verdict"
    if _deadline(st, Stage.notice_checked, ctx):
        return "verdict"
    run_stage(st, Stage.notice_checked, ctx)
    from pdr.engine.checks_notice import evidence_plan
    plan = evidence_plan(st.c, st.prior)
    if plan is None:
        return "verdict"
    if upto != Stage.verdict and STAGE_ORDER.index(upto) < STAGE_ORDER.index(Stage.evidence_searched):
        return "pending"
    if _deadline(st, Stage.evidence_searched, ctx):
        return "verdict"
    run_stage(st, Stage.evidence_searched, ctx)
    return "verdict"


def _deadline(st: State, stage: Stage, ctx: RunContext) -> bool:
    try:
        past = ctx.past_deadline()
    except Exception:  # noqa: BLE001
        past = False
    if past:
        _mark_stopped(st, stage)
    return past


def _mark_stopped(st: State, stage: Stage) -> None:
    st.stopped = stage
    stop = _budget_stop(stage)
    st.prior.setdefault(stage, []).append(stop)
    st.hashes.append(results_hash([stop]))


def process(dispute_key: str, ctx: RunContext, *, upto: Stage = Stage.verdict) -> Verdict | None:
    """Memoised and idempotent: run the stages a deduction needs and write its verdict."""
    ctx = _ctx_for(ctx, dispute_key)
    st = load_state(dispute_key, ctx)
    if st is None:
        return None
    status = advance(st, ctx, Stage(upto))
    if status != "verdict":
        return None
    return decide(st, ctx)


# ----------------------------------------------------------------------------- stage 7

def _fallback_combine(ded: Deduction, checks: list[CheckResult]) -> Verdict:
    """Conservative stand-in used only while lane B's combine is missing: never disputable."""
    from pdr.codes import primary_reason
    reasons = [r.reason for r in checks if r.status in (CheckStatus.INCOMPLETE, CheckStatus.REFER) and r.reason]
    if any(r.status == CheckStatus.GROUND for r in checks):
        reasons.append(Reason.INTERNAL_ERROR)
    outcome = Outcome.cannot_verify if reasons else Outcome.valid
    return Verdict(verdict_id="tmp", deduction_id=ded.deduction_id, dispute_key=ded.dispute_key,
                   client_id=ded.client_id, outcome=outcome, primary_reason=primary_reason(reasons),
                   reasons=sorted(set(reasons), key=str), amount_deducted=ded.amount,
                   missing_items=[r.missing_item for r in checks if r.missing_item])


def _conflicted(st: State) -> list[str]:
    try:
        from pdr.engine import timeline
        return sorted(timeline.resolve(st.c.trip, st.c.ctx.settings).conflicted) if st.c.trip else []
    except Exception:  # noqa: BLE001
        return []


def _combine(st: State, checks: list[CheckResult]) -> Verdict:
    has_ground = any(r.status == CheckStatus.GROUND for r in checks)
    recomputed = None
    try:
        recomputed = _recompute(st, checks)
    except ImportError:
        pass
    except Exception as err:  # noqa: BLE001 - barrier around the calculator
        if has_ground:
            log.error("recompute failed for %s: %r", st.key, err)
            figure = getattr(err, "name", None) if type(err).__name__ == "MissingFigure" else None
            checks = checks + [CheckResult(
                check_id="recompute", stage=Stage.verdict, status=CheckStatus.INCOMPLETE,
                reason=Reason.TERMS_MISSING if figure else Reason.INTERNAL_ERROR,
                missing_item=f"figure needed for the recomputation: {figure}" if figure
                else f"check recompute failed: {type(err).__name__}")]
    try:
        from pdr.engine.combine import combine
    except ImportError:
        return _fallback_combine(st.deduction, checks)
    if recomputed is None and has_ground and not any(r.check_id == "recompute" for r in checks):
        return _fallback_combine(st.deduction, checks)
    try:
        try:
            return combine(st.deduction, checks, recomputed, conflicted=_conflicted(st),
                           unconfirmed=[t.term_id for t in st.identity.unconfirmed])
        except TypeError:
            return combine(st.deduction, checks, recomputed)
    except Exception as err:  # noqa: BLE001
        log.error("combine failed for %s: %r", st.key, err)
        return _fallback_combine(st.deduction, checks + [CheckResult(
            check_id="combine", stage=Stage.verdict, status=CheckStatus.INCOMPLETE, reason=Reason.INTERNAL_ERROR,
            missing_item=f"check combine failed: {type(err).__name__}")])


_NATURAL = {"contract_term": Reason.TERMS_UNCONFIRMED, "internal_record": Reason.RECORDS_CONFLICT,
            "external_source": Reason.EVIDENCE_PARTIAL}


def downgrade(checks: list[CheckResult], failed_ids: set[str], detail: dict | None = None) -> list[CheckResult]:
    """Turn every GROUND that cites a failed citation into INCOMPLETE with its natural reason."""
    out = []
    for r in checks:
        bad = [ct for ct in r.citations if ct.evidence_id in failed_ids]
        if r.status == CheckStatus.GROUND and bad:
            ct = bad[0]
            why = (detail or {}).get(ct.evidence_id, "citation failed re-verification")
            r = r.model_copy(update={"status": CheckStatus.INCOMPLETE, "reason": _NATURAL.get(ct.kind, Reason.EVIDENCE_PARTIAL),
                                     "correction": None, "missing_item": f"{ct.label}: {why}",
                                     "note": (r.note + " | downgraded by the citation audit").strip(" |")})
        out.append(r)
    return out


def _evidence_for(checks, store) -> list[Evidence]:
    ids, out = [], []
    for r in checks:
        for ct in r.citations:
            if ct.evidence_id not in ids:
                ids.append(ct.evidence_id)
    for i in ids:
        e = store.get(Evidence, i)
        if e is not None:
            out.append(e)
    return out


def no_raise(before: Verdict, after: Verdict) -> Verdict:
    """The audit loop may only lower a verdict: never toward disputable, never a larger amount."""
    raised = after.outcome == Outcome.disputable and (
        before.outcome != Outcome.disputable or after.amount_disputable > before.amount_disputable)
    if raised:
        return before.model_copy(update={"outcome": Outcome.cannot_verify, "amount_disputable": Decimal("0"),
                                         "grounds": [], "primary_reason": Reason.INTERNAL_ERROR,
                                         "reasons": [Reason.INTERNAL_ERROR]})
    return after


def audited_verdict(st: State, checks: list[CheckResult], ctx: RunContext) -> tuple[Verdict, list[CheckResult], list]:
    """Combine, then audit a disputable verdict; failures downgrade and combine re-runs (DESIGN 8.5)."""
    verdict = _combine(st, checks)
    findings_all = []
    for _ in range(4):
        if verdict.outcome != Outcome.disputable:
            break
        try:
            from pdr.engine.audit import audit
        except ImportError:
            break
        evidence = _evidence_for(checks, ctx.store)
        refuter = bool(getattr(ctx.settings.evidence, "refuter", False)) and Ground.FORCE_MAJEURE in verdict.grounds
        try:
            findings = audit(verdict, evidence, ctx, with_refuter=refuter) or []
        except Exception as err:  # noqa: BLE001 - an audit that cannot finish holds the verdict
            log.error("audit failed for %s: %r", st.key, err)
            grounds_ids = {ct.evidence_id for r in checks if r.status == CheckStatus.GROUND for ct in r.citations}
            new_checks = downgrade(checks, grounds_ids, {i: f"audit failed: {type(err).__name__}" for i in grounds_ids})
            new_checks = [r if r.status != CheckStatus.GROUND else r.model_copy(update={
                "status": CheckStatus.INCOMPLETE, "reason": Reason.INTERNAL_ERROR, "correction": None,
                "missing_item": f"check audit failed: {type(err).__name__}"}) for r in new_checks]
            verdict, checks = no_raise(verdict, _combine(st, new_checks)), new_checks
            break
        findings_all.extend(findings)
        failed = {f.citation_evidence_id for f in findings if not f.passed}
        if not failed:
            break
        detail = {f.citation_evidence_id: f"{f.rule}: {f.detail}" for f in findings if not f.passed}
        checks = downgrade(checks, failed, detail)
        verdict = no_raise(verdict, _combine(st, checks))
    return verdict, checks, findings_all


def _run_cost(store, key) -> tuple[Decimal, Decimal]:
    try:
        from pdr.broker import ledger
        rows = ledger.rows(store, dispute_key=key)
        cr = sum((Decimal(str(r.units or 0)) for r in rows if r.unit_kind == "credits"), Decimal("0"))
        inr = sum((Decimal(str(getattr(r, "cost_inr", 0) or 0)) for r in rows), Decimal("0"))
        return cr, inr
    except Exception:  # noqa: BLE001 - ledger not available yet
        return Decimal("0"), Decimal("0")


def decide(st: State, ctx: RunContext) -> Verdict:
    """Stage 7: recompute, combine, audit, explain; write an immutable verdict and supersede the old one."""
    stage = Stage.verdict
    h = stage_hash(st, stage, ctx)
    vid = f"{st.key}#{h[:12]}"
    store = ctx.store
    existing = store.get(Verdict, vid)
    if existing is not None and store.load_stage(st.key, stage, h) is not None and st.stopped is None:
        _supersede_others(store, st.key, vid)
        return existing
    checks = _all(st)
    verdict, checks, findings = audited_verdict(st, checks, ctx)
    from pdr.engine.explain import explain
    flags = []
    for f in list(verdict.flags) + [f for r in checks for f in r.flags]:
        if f not in flags:
            flags.append(f)
    if Flag.INSTRUCTION_IN_INPUT in st.deduction.flags and Flag.INSTRUCTION_IN_INPUT not in flags:
        flags.append(Flag.INSTRUCTION_IN_INPUT)
    ev_ids = list(verdict.evidence_ids) or list(dict.fromkeys(ct.evidence_id for r in checks for ct in r.citations))
    terms_applied = list(verdict.terms_applied) or [t.term_id for t in st.c.terms
                                                    if any(t.term_id in r.depends_on or any(ct.evidence_id.endswith(t.term_id) for ct in r.citations) for r in checks)]
    cr, inr = _run_cost(store, st.key)
    verdict = verdict.model_copy(update={
        "verdict_id": vid, "deduction_id": st.deduction.deduction_id, "dispute_key": st.key,
        "client_id": st.deduction.client_id, "amount_deducted": st.deduction.amount, "flags": flags,
        "evidence_ids": ev_ids, "terms_applied": terms_applied, "inputs_hash": h, "rule_version": RULE_VERSION,
        "settings_version": ctx.settings.version, "run_id": ctx.run_id, "audit": list(verdict.audit) + list(findings),
        "run_cost_credits": cr, "run_cost_inr": inr, "superseded_by": None,
    })
    verdict = explain(verdict, checks, st)
    entries = _log_entries(st, verdict, checks, store, ctx)
    crashpoint("stage.before_save.verdict")
    with store.tx():
        if store.get(Verdict, vid) is None:
            store.put(verdict)
        if entries:
            try:
                from pdr.engine import decision_log
                decision_log.write(store, st.key, verdict, entries)
            except Exception as err:  # noqa: BLE001 - the log never blocks the verdict
                log.error("decision log not written for %s: %r", st.key, err)
        _supersede_others(store, st.key, vid)
        if st.stopped is None:
            store.save_stage(st.key, stage, h, [])
    crashpoint("stage.after_save.verdict")
    return store.get(Verdict, vid) or verdict


def _log_entries(st: State, verdict: Verdict, checks, store, ctx: RunContext) -> list[dict]:
    """Decision-log entries for this verdict (add-on E1); a failure here never blocks the verdict."""
    try:
        from pdr.engine import decision_log
        return decision_log.build(st, verdict, checks, store, ctx.settings.version, st.stage_hashes)
    except Exception as err:  # noqa: BLE001
        log.error("decision log not built for %s: %r", st.key, err)
        return []


def _supersede_others(store, key: str, vid: str) -> None:
    for v in store.find(Verdict, dispute_key=key):
        if v.verdict_id != vid and v.superseded_by is None:
            store.put(v.model_copy(update={"superseded_by": vid}))


def budget_verdict(dispute_key: str, ctx: RunContext, stage: Stage = Stage.evidence_searched) -> Verdict | None:
    """Every undecided deduction still gets a verdict when the run's deadline or budget is reached."""
    ctx = _ctx_for(ctx, dispute_key)
    st = load_state(dispute_key, ctx)
    if st is None:
        return None
    for s in (Stage.received, Stage.matched, Stage.terms_checked, Stage.records_checked, Stage.notice_checked):
        h = stage_hash(st, s, ctx)
        stored = ctx.store.load_stage(st.key, s, h)
        if stored is None:
            break
        st.stage_hashes[s] = h
        st.prior[s] = stored
        st.hashes.append(results_hash(stored))
    _mark_stopped(st, stage)
    return decide(st, ctx)
