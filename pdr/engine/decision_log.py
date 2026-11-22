"""Decision log (add-on E1): readable entries built from saved stage results, written with the verdict."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from pdr.codes import CheckStatus, Reason, Stage
from pdr.engine.hashing import RULE_VERSION

log = logging.getLogger("pdr.engine")

STEPS = ["gate", "terms", "records", "notice", "fm_wording", "evidence", "combine"]
_STAGE_OF = {"gate": (Stage.received, Stage.matched), "terms": (Stage.terms_checked,),
             "records": (Stage.records_checked,), "notice": (Stage.notice_checked,),
             "fm_wording": (Stage.notice_checked,), "evidence": (Stage.evidence_searched,)}
_CHECK_NAME = {"notice_in_window": "notice within the contract's window", "fm_wording_check": "force-majeure wording",
               "evidence_search": "source four-part test", "run_deadline": "run deadline"}
_STATUS = {CheckStatus.NO_GROUND: "passed", CheckStatus.GROUND: "failed", CheckStatus.REFER: "referred",
           CheckStatus.INCOMPLETE: "incomplete", CheckStatus.NOT_APPLICABLE: "not applicable"}


def _results_for(step: str, results: list) -> list:
    if step == "notice":
        return [r for r in results if not r.check_id.startswith("fm_wording")]
    if step == "fm_wording":
        return [r for r in results if r.check_id.startswith("fm_wording")]
    return list(results)


def _result(r) -> tuple[str, str]:
    """(result, one-sentence reason) for one check result."""
    if r.status == CheckStatus.INCOMPLETE and r.reason == Reason.BUDGET_REACHED:
        return "not run", f"budget: {r.missing_item or 'run cost or time limit reached'}"
    res = _STATUS.get(r.status, str(r.status))
    if r.status == CheckStatus.GROUND:
        why = f"ground {r.ground}" + (f": {r.note}" if r.note else "")
    elif r.status in (CheckStatus.INCOMPLETE, CheckStatus.REFER):
        why = f"{r.reason}: {r.missing_item or r.note}"
    else:
        why = r.note or "no ground found"
    return res, why.split(". search log")[0][:400]


def _considered(st, store) -> list[str]:
    """Lines for every source the evidence stage considered for this deduction's evidence group."""
    try:
        from pdr.engine.checks_notice import evidence_plan
        from pdr.engine.evidence_stage import build_request
        st.c.prior = dict(st.prior)
        plan = evidence_plan(st.c, st.prior)
        req = build_request(st.c, plan) if plan else None
        rows = store.evidence_considered(req.key()) if req is not None else []
    except Exception as err:  # noqa: BLE001
        log.warning("considered sources unavailable for %s: %r", st.key, err)
        return []
    out = []
    for r in rows:
        if r["status"] == "accepted":
            out.append(f"accepted: {r['reason']} ({r['url']})")
        else:
            detail = f": {r['detail']}" if r.get("detail") else ""
            out.append(f"rejected: {r['test']}: {r['reason']}{detail} ({r['url']})")
    return out


def _not_run_reason(st, step: str) -> str:
    if st.stopped is not None:
        return "budget: run's time or cost limit reached before this step"
    from pdr.engine import pipeline
    if step in ("terms", "records") or pipeline._gate_failed(st):
        return "earlier stop: the deduction did not pass the gate"
    if step in ("notice", "fm_wording") or not pipeline.unexplained_delay(st):
        return "delay explained or no delay penalty stands after the terms and records checks"
    return "no evidence plan: no disruption asserted or discoverable for this deduction"


def _ledger_ids(store, key: str) -> list[str]:
    try:
        from pdr.broker import ledger
        return [r.entry_id for r in ledger.rows(store, dispute_key=key) if not r.cache_hit]
    except Exception:  # noqa: BLE001
        return []


def build(st, verdict, checks, store, settings_version: str, stage_hashes: dict) -> list[dict]:
    """Entries in step order (gate .. combine); ids only as inputs, never driver names."""
    now = datetime.now(timezone.utc).isoformat()
    rule = {"rule_version": RULE_VERSION, "settings_version": settings_version}
    rows: list[dict] = []

    def at(stage):
        h = stage_hashes.get(stage)
        try:
            return (store.stage_saved_at(st.key, stage, h) if h else None) or now
        except Exception:  # noqa: BLE001
            return now

    for step in STEPS[:-1]:
        present = [s for s in _STAGE_OF[step] if s in st.prior]
        results = [r for s in present for r in _results_for(step, st.prior[s])]
        if not results:
            rows.append({"step": step, "check": step, "inputs": [], "result": "not run",
                         "reason": _not_run_reason(st, step), "evidence_considered": [], "numbers": [], "cost": [],
                         "rule": rule, "at": now})
            continue
        for r in results:
            res, why = _result(r)
            inputs = list(dict.fromkeys([ct.evidence_id for ct in r.citations] + list(r.depends_on)))
            rows.append({"step": step, "check": _CHECK_NAME.get(r.check_id, r.check_id.replace("_", " ")),
                         "check_id": r.check_id, "inputs": inputs, "result": res, "reason": why,
                         "evidence_considered": _considered(st, store) if step == "evidence" else [],
                         "numbers": [], "cost": _ledger_ids(store, st.key) if step == "evidence" else [],
                         "rule": rule, "at": at(r.stage if r.stage in stage_hashes else present[-1])})
    outcome = f"{verdict.outcome}" + (f" ({', '.join(str(g) for g in verdict.grounds)})" if verdict.grounds else "") + \
        (f", primary reason {verdict.primary_reason}" if verdict.primary_reason else "")
    rows.append({"step": "combine", "check": "recompute penalty and combine", "check_id": "combine",
                 "inputs": list(verdict.terms_applied) + list(verdict.evidence_ids), "result": "decided",
                 "reason": f"{outcome}; amount to recover INR {verdict.amount_disputable}",
                 "evidence_considered": [], "cost": [],
                 "numbers": [f"{ln.step}: {ln.description} = {ln.value}" for ln in verdict.recomputation],
                 "audit": [f"{'passed' if f.passed else 'FAILED'}: {f.rule} on {f.citation_evidence_id}" for f in verdict.audit],
                 "rule": rule, "at": now})
    return rows


def write(store, key: str, verdict, entries: list[dict]) -> None:
    """Append prebuilt entries for this verdict (call inside the verdict's transaction); once per verdict id."""
    if not entries or not hasattr(store, "decision_log_append"):
        return
    versions = store.decision_log_versions(key)
    if verdict.verdict_id in versions:
        return
    version = len(versions) + 1
    for i, e in enumerate(entries, 1):
        e.update({"entry_id": f"{verdict.verdict_id}#{i:02d}", "verdict_id": verdict.verdict_id,
                  "dispute_key": key, "run_id": verdict.run_id, "version": version, "seq": i})
    store.decision_log_append(entries)


def line(e: dict) -> str:
    """One readable line for an entry."""
    return f"[{e['step']}] {e['check']}: {e['result']}: {e['reason']}"
