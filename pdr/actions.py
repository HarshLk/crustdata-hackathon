"""Actions (D-21): drafted from verdicts, approved by named roles, sent exactly once (pdr.send)."""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Iterable, Optional

from pdr import approval
from pdr.codes import ActionKind, ActionStatus, Flag, Outcome
from pdr.models import Action, Deduction, Verdict
from pdr.outputs import letter

UNSENT = (ActionStatus.drafted, ActionStatus.awaiting_approval, ActionStatus.approved, ActionStatus.held)
COMMITTED = (ActionStatus.sending, ActionStatus.sent)


def required_roles(amount: Optional[Decimal], flags: Iterable[Any], settings: Any) -> list[str]:
    """Roles from settings.approvals: override roles for OUT_OF_WINDOW, send roles, threshold roles."""
    a = settings.approvals
    roles: list[str] = []
    if Flag.OUT_OF_WINDOW.value in {str(f) for f in flags}:
        roles += [approval.OVERRIDE_PREFIX + r for r in a.override_out_of_window]
    roles += list(a.send_dispute)
    threshold = Decimal(str(a.above_threshold_inr))
    if threshold > 0 and amount is not None and Decimal(str(amount)) >= threshold:
        roles += list(a.above_threshold_roles)
    seen: set[str] = set()
    return [r for r in roles if not (r in seen or seen.add(r))]


def _trail(store: Any, event: str, subject: str, detail: dict) -> None:
    store.trail_append(event, "system", subject, detail)


def draft_action(ctx: Any, kind: ActionKind, *, dispute_keys: list[str], deduction_ids: list[str], client_id: str,
                 body_md: str, amount: Optional[Decimal], flags: Iterable[Any] = (), subject: str = "") -> Optional[Action]:
    """Create (or find) the Action for this exact content; supersede open drafts with other content."""
    store, kind = ctx.store, ActionKind(kind)
    cv = letter.content_version(body_md)
    key = "+".join(dispute_keys)
    action_id = f"{key}:{cv[:12]}" if kind == ActionKind.dispute_letter else f"{kind.value}:{key}:{cv[:12]}"
    with store.tx():
        existing = store.get(Action, action_id)
        if existing is not None:
            return existing  # identical content: no-op
        same_kind = [a for a in store.find(Action, kind=kind) if set(a.dispute_keys) & set(dispute_keys)
                     or (kind == ActionKind.dispute_letter and set(a.deduction_ids) & set(deduction_ids))]
        if kind == ActionKind.dispute_letter and any(ActionStatus(a.status) in COMMITTED for a in same_kind):
            return None  # each deduction in at most one sent dispute
        roles = required_roles(amount, flags, ctx.settings)
        action = Action(action_id=action_id, kind=kind, client_id=client_id, content_version=cv,
                        deduction_ids=list(deduction_ids), dispute_keys=list(dispute_keys), amount_claimed=amount,
                        body_md=body_md, body_ref=f"action:{action_id}", required_roles=roles,
                        status=ActionStatus.drafted, subject=subject)
        action.status = approval.evaluate(store, action)
        for old in same_kind:
            if ActionStatus(old.status) in UNSENT:
                store.put(old.model_copy(update={"status": ActionStatus.rejected}))
                _trail(store, "superseded", old.action_id, {"by": action_id})
        store.put(action)
        _trail(store, "drafted", action_id, {"kind": kind.value, "content_version": cv, "required_roles": roles,
                                             "status": ActionStatus(action.status).value})
    return action


def draft_for_verdict(verdict: Verdict, ctx: Any) -> Optional[Action]:
    """A dispute_letter Action for a current disputable verdict with something to claim; else None."""
    if Outcome(verdict.outcome) != Outcome.disputable or verdict.superseded_by:
        return None
    if Decimal(str(verdict.amount_disputable)) <= 0:
        return None
    ded = ctx.store.get(Deduction, verdict.dispute_key)
    if ded is None:
        return None
    body = letter.render_letter(verdict, ctx.store, ctx.settings)
    from pdr import guard
    from pdr.contracts import GuardViolation
    try:
        guard.check("k7", body, ded.client_id)
    except GuardViolation as exc:
        _trail(ctx.store, "draft_blocked", verdict.dispute_key, {"reason": str(exc)[:200]})
        return None
    flags = set(verdict.flags) | set(ded.flags)
    return draft_action(ctx, ActionKind.dispute_letter, dispute_keys=[verdict.dispute_key],
                        deduction_ids=[ded.deduction_id], client_id=ded.client_id, body_md=body,
                        amount=verdict.amount_disputable, flags=flags,
                        subject=f"Request for reversal of deduction {verdict.dispute_key}")


def _to(ctx: Any, ded: Deduction) -> str:
    from pdr.models import Client
    c = ctx.store.get(Client, ded.client_id)
    return c.legal_name if c else ded.client_name_raw


def draft_document_request(ctx: Any, dispute_key: str, items: list[str]) -> Optional[Action]:
    """Ask the client for missing documents (e.g. an annexure, a detention memo)."""
    ded = ctx.store.get(Deduction, dispute_key)
    if ded is None or not items:
        return None
    body = letter.render_simple("document_request", ref=dispute_key, to=_to(ctx, ded), items=list(items))
    return draft_action(ctx, ActionKind.document_request, dispute_keys=[dispute_key], deduction_ids=[ded.deduction_id],
                        client_id=ded.client_id, body_md=body, amount=None, subject=f"Documents requested for {dispute_key}")


def draft_fm_notice(ctx: Any, dispute_key: str, clause: str, event: str) -> Optional[Action]:
    """A force-majeure notice under the named clause."""
    ded = ctx.store.get(Deduction, dispute_key)
    if ded is None:
        return None
    body = letter.render_simple("fm_notice", ref=dispute_key, to=_to(ctx, ded), clause=clause, event=event)
    return draft_action(ctx, ActionKind.fm_notice, dispute_keys=[dispute_key], deduction_ids=[ded.deduction_id],
                        client_id=ded.client_id, body_md=body, amount=None, subject=f"Force-majeure notice {dispute_key}")


def refresh_required_roles(ctx: Any) -> list[str]:
    """On start: unsent actions take the current settings' roles (threshold changes); returns changed ids."""
    store, changed = ctx.store, []
    for st in UNSENT:
        for a in store.actions_in(st):
            flags = [Flag.OUT_OF_WINDOW] if any(r.startswith(approval.OVERRIDE_PREFIX) for r in a.required_roles) else []
            roles = required_roles(a.amount_claimed, flags, ctx.settings)
            if roles == a.required_roles:
                continue
            with store.tx():
                a = a.model_copy(update={"required_roles": roles})
                a.status = approval.evaluate(store, a)
                store.put(a)
                _trail(store, "roles_changed", a.action_id, {"required_roles": roles, "status": ActionStatus(a.status).value})
            changed.append(a.action_id)
    return changed
