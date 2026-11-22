"""Approvals (PRD 8): the only code in the service that writes an Approval row.

An approval binds to an exact action_id, which contains the content version, so an edit voids it.
Text inside deductions, emails or documents never reaches this module (PRD case 10).
"""
from __future__ import annotations

from typing import Any, Optional

from pdr.codes import ActionStatus
from pdr.models import Action, Approval, utcnow

OVERRIDE_PREFIX = "override:"
OPEN = (ActionStatus.drafted, ActionStatus.awaiting_approval, ActionStatus.approved, ActionStatus.held)


class ApprovalError(ValueError):
    """Raised when an approval, rejection or override is not permitted."""


def approvals_for(store: Any, action_id: str) -> list[Approval]:
    """Approval rows bound to exactly this action_id."""
    return sorted(store.find(Approval, action_id=action_id), key=lambda a: a.approval_id)


def missing_roles(store: Any, action: Action) -> list[str]:
    """Required roles (incl. override:<role>) with no matching decision on this exact action_id."""
    rows = approvals_for(store, action.action_id)
    approved = {a.role_required for a in rows if a.decision == "approve" and a.approver}
    overridden = {a.role_required for a in rows if a.decision == "override" and a.approver and a.comment}
    out = []
    for r in action.required_roles:
        if r.startswith(OVERRIDE_PREFIX):
            if r[len(OVERRIDE_PREFIX):] not in overridden:
                out.append(r)
        elif r not in approved:
            out.append(r)
    return out


def is_rejected(store: Any, action: Action) -> bool:
    return any(a.decision == "reject" for a in approvals_for(store, action.action_id))


def is_approved(store: Any, action: Action) -> bool:
    """Every required role approved this exact action_id, none rejected it, and it has at least one role."""
    return bool(action.required_roles) and not is_rejected(store, action) and not missing_roles(store, action)


def evaluate(store: Any, action: Action, *, release_hold: bool = False) -> ActionStatus:
    """Status implied by the approval rows; never moves an action that is sending or sent."""
    st = ActionStatus(action.status)
    if st not in OPEN:
        return st
    if is_rejected(store, action):
        return ActionStatus.rejected
    if st == ActionStatus.held and not release_hold:
        return st
    if any(r.startswith(OVERRIDE_PREFIX) for r in missing_roles(store, action)):
        return ActionStatus.held
    return ActionStatus.approved if is_approved(store, action) else ActionStatus.awaiting_approval


def _decide(ctx: Any, action_id: str, role: str, name: str, decision: str, comment: Optional[str]) -> Action:
    store = ctx.store
    if not name or not name.strip():
        raise ApprovalError("an approver name is required")
    if not role or not role.strip():
        raise ApprovalError("a role is required")
    with store.tx():
        action = store.get(Action, action_id)
        if action is None:
            raise ApprovalError(f"no action {action_id!r}")
        if ActionStatus(action.status) not in OPEN:
            raise ApprovalError(f"action {action_id} is {action.status}; decisions are closed")
        if decision == "override":
            allowed = list(ctx.settings.approvals.override_out_of_window)
            if role not in allowed:
                raise ApprovalError(f"role {role!r} may not override (allowed: {allowed})")
            if not comment or not comment.strip():
                raise ApprovalError("an override needs a written reason (--comment)")
            if OVERRIDE_PREFIX + role not in action.required_roles:
                raise ApprovalError(f"action {action_id} has no hold that {role!r} can override")
        elif role not in action.required_roles:
            raise ApprovalError(f"role {role!r} is not required for {action_id} (required: {action.required_roles})")
        approval_id = f"{action_id}|{decision}|{role}"
        if store.get(Approval, approval_id) is None:
            store.put(Approval(approval_id=approval_id, action_id=action_id, client_id=action.client_id,
                               role_required=role, approver=name.strip(), decision=decision,
                               comment=(comment or None), decided_at=utcnow()))
            store.trail_append(decision, f"{name.strip()} ({role})", action_id,
                               {"role": role, "comment": comment, "content_version": action.content_version})
        new = evaluate(store, action, release_hold=(decision == "override"))
        if new != action.status:
            action = action.model_copy(update={"status": new})
            store.put(action)
            store.trail_append("action_status", "system", action_id, {"status": new.value})
    return action


def approve(ctx: Any, action_id: str, role: str, name: str, comment: Optional[str] = None) -> Action:
    """Record one role's approval of this exact action version."""
    return _decide(ctx, action_id, role, name, "approve", comment)


def reject(ctx: Any, action_id: str, role: str, name: str, comment: Optional[str] = None) -> Action:
    """Record a rejection; the action can no longer be sent."""
    return _decide(ctx, action_id, role, name, "reject", comment)


def override(ctx: Any, action_id: str, role: str, name: str, comment: Optional[str] = None) -> Action:
    """Release an OUT_OF_WINDOW hold with a written reason; send approvals are still required."""
    return _decide(ctx, action_id, role, name, "override", comment)
