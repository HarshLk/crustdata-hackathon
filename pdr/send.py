"""Send exactly once (DESIGN 7): reconcile `sending`, then lookup, re-audit, intent, deliver, mark sent."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from pdr import approval
from pdr.codes import ActionKind, ActionStatus, Outcome
from pdr.contracts import GuardViolation
from pdr.crashpoints import crashpoint
from pdr.models import Action, Client, Evidence, Verdict, utcnow
from pdr.outputs import letter

log = logging.getLogger("pdr.send")


def _k7(ctx: Any, k7: Any) -> Any:
    if k7 is not None:
        return k7
    from pdr.adapters.k7_outbox import get_k7
    return get_k7(ctx.settings)


def _set(store: Any, action: Action, event: str, detail: dict, **update: Any) -> Action:
    with store.tx():
        action = action.model_copy(update=update)
        store.put(action)
        store.trail_append(event, "system", action.action_id, detail)
    return action


def _hold(store: Any, action: Action, reason: str) -> Action:
    log.warning("held %s: %s", action.action_id, reason)
    return _set(store, action, "held", {"reason": reason}, status=ActionStatus.held)


def _mark_sent(store: Any, action: Action, via: str) -> Action:
    return _set(store, action, "sent", {"via": via, "content_version": action.content_version},
                status=ActionStatus.sent, sent_at=utcnow())


def current_verdict(store: Any, dispute_key: str) -> Optional[Verdict]:
    vs = [v for v in store.find(Verdict, dispute_key=dispute_key) if not v.superseded_by]
    return max(vs, key=lambda v: v.created_at) if vs else None


def message(store: Any, action: Action, settings: Any, sent_at: datetime) -> dict:
    """The outbox message (INTERFACES 5.3); approver names and dates are filled here."""
    rows = [a for a in approval.approvals_for(store, action.action_id) if a.decision == "approve"]
    client = store.get(Client, action.client_id)
    return {
        "action_id": action.action_id, "kind": ActionKind(action.kind).value, "client_id": action.client_id,
        "dispute_keys": action.dispute_keys, "to": client.legal_name if client else action.client_id,
        "subject": action.subject, "content_version": action.content_version,
        "amount_claimed": str(action.amount_claimed) if action.amount_claimed is not None else None,
        "body_md": letter.finalize(action.body_md, rows, sent_at, settings.letter.response_days),
        "approved_by": [{"role": a.role_required, "name": a.approver, "at": a.decided_at.isoformat() if a.decided_at else None}
                        for a in rows],
        "sent_at": sent_at.isoformat(),
    }


def reaudit(ctx: Any, action: Action) -> Optional[str]:
    """Free checks before an irreversible send; returns a hold reason or None."""
    store = ctx.store
    if letter.content_version(action.body_md) != action.content_version:
        return "content does not match its version"
    if not approval.is_approved(store, action):
        return f"approvals missing: {approval.missing_roles(store, action) or 'rejected'}"
    for other in store.find(Action, kind=action.kind):
        if other.action_id != action.action_id and ActionStatus(other.status) in (ActionStatus.sending, ActionStatus.sent) \
                and set(other.deduction_ids) & set(action.deduction_ids) and ActionKind(action.kind) == ActionKind.dispute_letter:
            return f"deduction already in dispute {other.action_id}"
    if ActionKind(action.kind) != ActionKind.dispute_letter:
        return None
    for key in action.dispute_keys:
        v = current_verdict(store, key)
        if v is None or Outcome(v.outcome) != Outcome.disputable:
            return f"current verdict for {key} is not disputable"
        try:
            if letter.render_letter(v, store, ctx.settings) != action.body_md:
                return f"verdict or evidence for {key} changed since this letter was drafted"
        except Exception as exc:  # fail closed
            return f"letter re-render failed: {type(exc).__name__}"
        try:
            from pdr.engine.audit import audit
        except ImportError:
            continue
        evidence = [e for e in (store.get(Evidence, i) for i in v.evidence_ids) if e is not None]
        try:
            findings = audit(v, evidence, ctx, with_refuter=False)
        except Exception as exc:  # fail closed
            return f"audit error: {type(exc).__name__}"
        failed = [f for f in findings or [] if not f.passed]
        if failed:
            return "audit failed: " + "; ".join(f"{f.citation_evidence_id} {f.rule}" for f in failed)[:300]
    return None


def reconcile(ctx: Any, k7: Any = None) -> dict:
    """Every `sending` action checked at K7 by id: present -> sent; absent -> redeliver same id; blind -> held."""
    store, k7 = ctx.store, _k7(ctx, k7)
    out = {"sent": [], "redelivered": [], "held": []}
    if hasattr(k7, "cleanup"):
        k7.cleanup()  # temp files from a write killed before its rename
    caps = k7.capabilities()
    for a in store.actions_in(ActionStatus.sending):
        if caps.get("can_check"):
            if k7.exists(a.action_id):
                _mark_sent(store, a, "reconcile:present")
                out["sent"].append(a.action_id)
                continue
        elif not caps.get("dedupes"):
            _hold(store, a, "K7 can neither check nor de-duplicate; a person must confirm delivery")
            out["held"].append(a.action_id)
            continue
        if not approval.is_approved(store, a):
            _hold(store, a, "approvals missing at reconcile")
            out["held"].append(a.action_id)
            continue
        try:
            k7.deliver(a.action_id, message(store, a, ctx.settings, utcnow()), [])
        except GuardViolation as exc:
            _hold(store, a, f"guard: {exc}"[:300])
            out["held"].append(a.action_id)
            continue
        _mark_sent(store, a, "reconcile:redelivered")
        out["redelivered"].append(a.action_id)
    return out


def send_all(ctx: Any, k7: Any = None) -> dict:
    """Send every approved action once."""
    store, k7 = ctx.store, _k7(ctx, k7)
    out = {"sent": [], "already": [], "held": [], "failed": []}
    caps = k7.capabilities()
    for a in store.actions_in(ActionStatus.approved):
        if caps.get("can_check") and k7.exists(a.action_id):
            _mark_sent(store, a, "lookup:present")
            out["already"].append(a.action_id)
            continue
        crashpoint("send.after_lookup")
        reason = reaudit(ctx, a)
        if reason:
            _hold(store, a, reason)
            out["held"].append(a.action_id)
            continue
        a = _set(store, a, "sending", {"attempt": a.send_attempts + 1},
                 status=ActionStatus.sending, send_attempts=a.send_attempts + 1)
        crashpoint("send.after_intent")
        try:
            k7.deliver(a.action_id, message(store, a, ctx.settings, utcnow()), [])
        except GuardViolation as exc:
            _hold(store, a, f"guard: {exc}"[:300])
            out["held"].append(a.action_id)
            continue
        except Exception as exc:  # left in `sending`; the next start reconciles it
            store.trail_append("deliver_failed", "system", a.action_id, {"error": type(exc).__name__})
            out["failed"].append(a.action_id)
            continue
        crashpoint("send.after_deliver")
        _mark_sent(store, a, "deliver")
        crashpoint("send.after_mark_sent")
        out["sent"].append(a.action_id)
    return out


def startup(ctx: Any, k7: Any = None) -> dict:
    """What every command runs first: reconcile `sending`, then re-evaluate roles of unsent actions."""
    from pdr.actions import refresh_required_roles
    res = reconcile(ctx, k7)
    res["roles_changed"] = refresh_required_roles(ctx)
    return res


def run(ctx: Any, k7: Any = None) -> dict:
    """`pdr send`: start-up reconciliation then send approved actions."""
    res = startup(ctx, k7)
    res.update({f"send_{k}": v for k, v in send_all(ctx, k7).items()})
    return res
