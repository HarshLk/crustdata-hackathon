"""Read-only scan of the store for the seven alert events; queues each alert key once. No engine/send hooks."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import quote

from pdr import approval
from pdr.alerts import outbox
from pdr.codes import ActionKind, ActionStatus, Outcome
from pdr.models import Action, Deduction, Verdict

EVENTS = ("verdict_ready", "second_approval", "out_of_window_held", "missing_document", "reminder",
          "dispute_sent", "budget_stopped")
TEXT = {
    "verdict_ready": "{id}: Rs {amount} {outcome}. Review and approve: {link}",
    "second_approval": "{id} needs {roles} approval: {link}",
    "out_of_window_held": "{id} is past the dispute window and held for you: {link}",
    "missing_document": "{id} cannot be checked until missing documents are uploaded: {link}",
    "reminder": "{id} still waiting since {since}: {link}",
    "dispute_sent": "{id} sent to the client: {link}",
    "budget_stopped": "Run {id} stopped at its cost limit; {n} cases unfinished: {link}",
}


def _cfg(settings: Any) -> Any:
    return getattr(settings, "alerts", None)


def _on(settings: Any, event: str) -> bool:
    a = _cfg(settings)
    if a is None:
        return True
    if not getattr(a, "enabled", True):
        return False
    return bool((getattr(a, "events", None) or {}).get(event, True))


def link(settings: Any, ref: str, kind: str = "case") -> str:
    base = str(getattr(_cfg(settings), "app_link_base", None) or "http://localhost:8000").rstrip("/")
    return f"{base}/{kind}/{quote(ref, safe='')}"


def _current(store: Any, dispute_key: str) -> Optional[Verdict]:
    vs = [v for v in store.find(Verdict, dispute_key=dispute_key) if not v.superseded_by]
    return max(vs, key=lambda v: v.created_at) if vs else None


def message(settings: Any, event: str, ref: str, *, outcome: str = "", amount: Any = None, deadline: Any = None,
            roles: list[str] | None = None, since: str = "", n: int = 0, kind: str = "case") -> dict:
    """Minimal content only: id, event, outcome, amount, deadline, link (no evidence, clauses, driver data)."""
    url = link(settings, ref, kind)
    amt = f"{amount}" if amount is not None else ""
    text = TEXT[event].format(id=ref, amount=amt, outcome=outcome, link=url, roles=" and ".join(roles or []),
                              since=since, n=n)
    return {"dispute_id": ref, "event": event, "outcome": outcome or None, "amount": amt or None,
            "deadline": str(deadline) if deadline else None, "link": url, "text": text}


def _guarded(store: Any, key: str, ref: str, event: str, version: str, roles: list[str], msg: dict,
             client_id: Optional[str], extra_refs: list[str], now: Optional[datetime]) -> bool:
    from pdr import guard
    try:
        guard.check("k7", msg, client_id, allow_refs=[ref, version, *extra_refs])
    except Exception:  # noqa: BLE001 - a blocked alert is simply not queued; the core carries on
        return False
    return outbox.queue(store, key, ref, event, version, roles, msg, now)


def _drafted_at(store: Any, action_id: str) -> Optional[datetime]:
    try:
        ts = [r["at"] for r in store.trail() if r.get("subject_id") == action_id and r.get("event") == "drafted"]
    except Exception:  # noqa: BLE001
        return None
    if not ts:
        return None
    t = datetime.fromisoformat(str(ts[-1]))
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def scan(store: Any, settings: Any, now: Optional[datetime] = None) -> list[str]:
    """Queue alerts for the seven events; returns the keys newly queued (a second scan queues none)."""
    now = now or datetime.now(timezone.utc)
    new: list[str] = []
    th_roles = list(getattr(settings.approvals, "above_threshold_roles", []) or [])
    reminder_s = float(getattr(_cfg(settings), "reminder_hours", 24) or 0) * 3600

    def q(ref: str, event: str, version: str, roles: list[str], msg: dict, client_id: Optional[str],
          extra: list[str]) -> None:
        if not _on(settings, event):
            return
        key = f"{ref}~{event}~{version}"
        if _guarded(store, key, ref, event, version, roles, msg, client_id, extra, now):
            new.append(key)

    for a in store.find(Action):
        st, kind = ActionStatus(a.status), ActionKind(a.kind)
        ref = "+".join(a.dispute_keys) or a.action_id
        v = _current(store, a.dispute_keys[0]) if a.dispute_keys else None
        outcome = Outcome(v.outcome).value if v else ""
        refs = [a.action_id, a.content_version, *a.dispute_keys, *a.deduction_ids]
        override = [r for r in a.required_roles if r.startswith(approval.OVERRIDE_PREFIX)]
        if kind == ActionKind.dispute_letter and st == ActionStatus.awaiting_approval:
            # D-41: keyed on the action (content), so a settings edit with an unchanged letter never re-alerts
            q(ref, "verdict_ready", a.action_id, ["kam"],
              message(settings, "verdict_ready", ref, outcome=outcome, amount=a.amount_claimed), a.client_id, refs)
            needed = [r for r in th_roles if r in a.required_roles]
            if needed:
                q(ref, "second_approval", a.action_id, needed,
                  message(settings, "second_approval", ref, amount=a.amount_claimed, roles=needed), a.client_id, refs)
            started = _drafted_at(store, a.action_id)
            if reminder_s > 0 and started is not None and (now - started).total_seconds() >= reminder_s:
                nth = int((now - started).total_seconds() // reminder_s)
                missing = [r for r in approval.missing_roles(store, a)] or ["kam"]
                q(ref, "reminder", f"{a.action_id}#{nth}", missing,
                  message(settings, "reminder", ref, since=started.date().isoformat()), a.client_id, refs)
        if kind == ActionKind.dispute_letter and override and st in (ActionStatus.awaiting_approval, ActionStatus.held):
            q(ref, "out_of_window_held", a.action_id, ["kam"],
              message(settings, "out_of_window_held", ref, amount=a.amount_claimed), a.client_id, refs)
        if kind == ActionKind.document_request and st not in (ActionStatus.rejected,):
            q(ref, "missing_document", a.action_id, ["billing"],
              message(settings, "missing_document", ref), a.client_id, refs)
        if kind == ActionKind.dispute_letter and st == ActionStatus.sent:
            q(ref, "dispute_sent", a.action_id, ["kam"],
              message(settings, "dispute_sent", ref, amount=a.amount_claimed), a.client_id, refs)

    # missing documents without a drafted request: a cannot-verify verdict naming missing items
    for d in store.find(Deduction):
        v = _current(store, d.dispute_key)
        if v is None or Outcome(v.outcome) != Outcome.cannot_verify or not v.missing_items:
            continue
        ver = hashlib.sha256(json.dumps(sorted(v.missing_items)).encode()).hexdigest()[:12]
        q(d.dispute_key, "missing_document", f"items:{ver}", ["billing"],
          message(settings, "missing_document", d.dispute_key, outcome=Outcome(v.outcome).value), d.client_id,
          [d.deduction_id, d.advice_no or "", d.line_no or ""])

    if hasattr(store, "query"):
        try:
            runs = store.query("SELECT run_id, ended_at, body FROM runs")
        except Exception:  # noqa: BLE001
            runs = []
        for r in runs:
            body = json.loads(r["body"]) if r.get("body") else {}
            stopped = body.get("stopped_at_budget") or body.get("budget_reached")
            if not stopped and not (r.get("ended_at") and body.get("budget_stopped")):
                continue
            n = int(body.get("undecided") or body.get("unfinished") or 0)
            q(r["run_id"], "budget_stopped", r["run_id"], ["finance"],
              message(settings, "budget_stopped", r["run_id"], n=n, kind="run"), None, [])
    return new
