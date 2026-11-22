"""Static case file (D-30): index.html queue, case_<key>.html per deduction, money.html. No server, no assets."""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from pdr import approval
from pdr.adapters.k7_outbox import safe_name
from pdr.codes import ActionStatus, Flag, TsSource
from pdr.contracts import GuardViolation
from pdr.models import Action, Deduction, Trip, Verdict
from pdr.outputs import letter

log = logging.getLogger("pdr.report")

try:
    from pdr.outputs.atomic import atomic_write
except ImportError:  # c1's writer not on this branch yet
    import os
    import tempfile

    def atomic_write(path, data, *, crash_name="output.before_rename"):  # type: ignore[no-redef]
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data.encode("utf-8") if isinstance(data, str) else data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        return path

CLIENT_SOURCES = {TsSource.client_gate_log, TsSource.consignee_gate_log, TsSource.client_stated}
PHONE = re.compile(r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}(?!\d)")
OUTCOME_LABEL = {"disputable": "disputable", "valid": "valid", "cannot_verify": "cannot verify", "none": "not decided"}


def case_page(dispute_key: str) -> str:
    return f"case_{safe_name(dispute_key)}.html"


def scrub(text: Any, client_id: str | None = None) -> str:
    """Free text from records: vault values to driver_ref (guard), then phone numbers removed."""
    s = "" if text is None else str(text)
    try:
        from pdr import guard
        s = guard.pseudonymise(s, client_id or "")
    except Exception:  # noqa: BLE001 - fall through to the phone scrub
        pass
    return PHONE.sub("[phone removed]", s)


def _ts(d: datetime | None) -> str:
    return d.isoformat(timespec="minutes") if d else "-"


def _current_verdict(store: Any, key: str) -> Verdict | None:
    vs = [v for v in store.find(Verdict, dispute_key=key) if not v.superseded_by]
    return max(vs, key=lambda v: v.created_at) if vs else None


def _ledger(store: Any, dispute_key: str | None = None) -> list[Any]:
    try:
        from pdr.broker import ledger as L
        return list(L.rows(store, dispute_key=dispute_key))
    except Exception:  # noqa: BLE001 - ledger not on this branch, or no ledger table
        return []


def _economics(store: Any, settings: Any) -> list[dict]:
    try:
        from pdr import economics as E
    except Exception:  # noqa: BLE001
        return []
    for name in ("view", "summary", "compute", "economics", "report", "build"):
        fn = getattr(E, name, None)
        if callable(fn):
            try:
                return _sections(fn(store, settings))
            except Exception as exc:  # noqa: BLE001 - the page still renders
                log.warning("economics view failed: %s", exc)
                return []
    return []


def _cell(v: Any) -> str:
    if isinstance(v, Decimal):
        return letter.inr(v)
    if isinstance(v, (list, tuple)):
        return ", ".join(_cell(x) for x in v)
    if isinstance(v, dict):
        return "; ".join(f"{k}: {_cell(x)}" for k, x in v.items())
    return "-" if v is None else str(v)


def _sections(obj: Any) -> list[dict]:
    """Turn the economics dicts into simple titled tables."""
    if hasattr(obj, "model_dump"):
        obj = obj.model_dump()
    if not isinstance(obj, dict):
        return [{"title": "Economics", "header": None, "rows": [[_cell(obj)]]}]
    out, summary = [], []
    for k, v in obj.items():
        title = str(k).replace("_", " ").capitalize()
        if isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
            header = list(dict.fromkeys(h for x in v for h in x))
            out.append({"title": title, "header": header, "rows": [[_cell(x.get(h)) for h in header] for x in v]})
        elif isinstance(v, dict) and v and all(isinstance(x, dict) for x in v.values()):
            header = list(dict.fromkeys(h for x in v.values() for h in x))
            out.append({"title": title, "header": [""] + header, "rows": [[str(r)] + [_cell(x.get(h)) for h in header] for r, x in v.items()]})
        elif isinstance(v, dict):
            out.append({"title": title, "header": None, "rows": [[str(a), _cell(b)] for a, b in v.items()]})
        else:
            summary.append([title, _cell(v)])
    if summary:
        out.insert(0, {"title": "Summary", "header": None, "rows": summary})
    return out


def _obs(o: Any) -> dict:
    return {"at": _ts(o.value), "source": TsSource(o.source).value + (" (time zone assumed IST)" if o.tz_assumed else ""),
            "ref": letter.source_ref_text(o.ref)}


def _timeline(trip: Trip, ded: Deduction) -> list[dict]:
    rows = []
    multi = [("Indent issued", trip.indent_issued_at), ("Reporting at loading point", trip.reporting_at),
             ("Gate out (dispatch)", trip.gate_out_at), ("Arrival at destination", trip.arrival_at),
             ("Unloaded", trip.unloaded_at)]
    for name, obs in multi:
        rows.append({"event": name, "client": [_obs(o) for o in obs if TsSource(o.source) in CLIENT_SOURCES],
                     "ours": [_obs(o) for o in obs if TsSource(o.source) not in CLIENT_SOURCES]})
    if trip.placement_required_at:
        rows.insert(1, {"event": "Placement required", "client": [{"at": _ts(trip.placement_required_at), "source": "indent", "ref": "trip record"}], "ours": []})
    if trip.delivery_due_at:
        rows.insert(-2, {"event": "Delivery due", "client": [{"at": _ts(trip.delivery_due_at), "source": "indent", "ref": "trip record"}], "ours": []})
    cc = ded.client_calc
    if cc and (cc.from_ts or cc.to_ts):
        rows.append({"event": "Delay as counted by the client", "ours": [],
                     "client": [{"at": f"{_ts(cc.from_ts)} to {_ts(cc.to_ts)}", "source": "client_stated", "ref": "payment advice"}]})
    return [r for r in rows if r["client"] or r["ours"]]


def _window(trip: Trip, settings: Any) -> tuple[datetime, datetime] | None:
    """The disputed trip window: first to last trip timestamp, widened by the evidence allowance."""
    pts = [o.value for obs in (trip.indent_issued_at, trip.reporting_at, trip.gate_out_at, trip.arrival_at, trip.unloaded_at) for o in obs]
    pts += [d for d in (trip.placement_required_at, trip.delivery_due_at) if d]
    if not pts:
        return None
    allow = timedelta(hours=float(settings.evidence.window_allowance_hours))
    return min(pts) - allow, max(pts) + allow


def _trip_view(trip: Trip | None, ded: Deduction, settings: Any) -> dict:
    if trip is None:
        return {"trip": None, "timeline": [], "halts": [], "hidden_gps": 0, "window": "", "holds": [], "notices": [], "assertions": []}
    win = _window(trip, settings)
    shown, hidden = [], 0
    for h in trip.halts:
        if win and TsSource(h.source) == TsSource.gps and (h.end < win[0] or h.start > win[1]):
            hidden += 1  # GPS outside the disputed window never leaves the engine (PRD 8)
            continue
        shown.append({"start": _ts(h.start), "end": _ts(h.end), "place": scrub(h.place, trip.client_id) or "-",
                      "source": TsSource(h.source).value, "ref": letter.source_ref_text(h.ref)})
    holds = [f"{scrub(h.authority)} at {scrub(h.place)} from {_ts(h.held_from)} to {_ts(h.released_at)}"
             + (f"; memo {letter.source_ref_text(h.memo_ref)}" if h.memo_ref else "; no memo on file") for h in trip.holds]
    notices = [f"{_ts(n.sent_at)} by {scrub(n.channel)} to {scrub(n.addressee)}"
               + (f", subject \"{scrub(n.subject)}\"" if n.subject else "") + f" ({letter.source_ref_text(n.ref)})" for n in trip.notices]
    notes = []
    for a in trip.assertions:
        head = f"{a.kind.replace('_', ' ')} {_ts(a.at)}" + (f" at {scrub(a.place)}" if a.place else "") + f" ({letter.source_ref_text(a.ref)})"
        if a.kind == "driver_report":
            notes.append(head + ": text withheld (may identify the driver)" + (f"; event type {a.event_type}" if a.event_type else ""))
        else:
            notes.append(head + ": " + scrub(a.text, trip.client_id))
    return {"trip": trip, "timeline": _timeline(trip, ded), "halts": shown, "hidden_gps": hidden,
            "window": f"{_ts(win[0])} to {_ts(win[1])}" if win else "", "holds": holds, "notices": notices, "assertions": notes}


def _outbox_names(settings: Any) -> set[str] | None:
    """Delivered K7 message file names (LocalOutbox.messages), or None when K7 cannot list."""
    try:
        from pdr.adapters.k7_outbox import get_k7
        k7 = get_k7(settings)
        return {p.name for p in k7.messages()} if hasattr(k7, "messages") else None
    except Exception:  # noqa: BLE001
        return None


def _action_view(store: Any, a: Action, outbox: set[str] | None = None) -> dict:
    missing = approval.missing_roles(store, a)
    status = ActionStatus(a.status).value
    label = status.replace("_", " ")
    if status == "held" and any(m.startswith(approval.OVERRIDE_PREFIX) for m in missing):
        label = "held: out of window, needs an override"
    rows = approval.approvals_for(store, a.action_id)
    plain = [m for m in missing if not m.startswith(approval.OVERRIDE_PREFIX)]
    cmd = ""
    if status in ("awaiting_approval", "held", "drafted") and plain:
        cmd = f"pdr approve '{a.action_id}' --role {plain[0]} --name \"<your name>\""
    return {"action_id": a.action_id, "kind": a.kind.value if hasattr(a.kind, "value") else a.kind, "status": status,
            "status_label": label, "amount": a.amount_claimed, "roles": a.required_roles, "missing": missing,
            "approvals": [{"decision": r.decision, "approver": r.approver, "role_required": r.role_required,
                           "decided_at": _ts(r.decided_at), "comment": scrub(r.comment)} for r in rows],
            "sent_at": _ts(a.sent_at) if a.sent_at else "", "attempts": a.send_attempts, "body": a.body_md, "approve_cmd": cmd,
            "outbox": None if outbox is None else (f"{safe_name(a.action_id)}.json" if f"{safe_name(a.action_id)}.json" in outbox else "")}


def _actions_for(store: Any, key: str) -> list[Action]:
    acts = [a for a in store.find(Action) if key in a.dispute_keys]
    order = {s: i for i, s in enumerate(["sent", "sending", "approved", "awaiting_approval", "held", "drafted", "rejected"])}
    return sorted(acts, key=lambda a: (order.get(ActionStatus(a.status).value, 9), a.action_id))


def _decision_log(store: Any, key: str, v: Verdict | None) -> dict:
    """Entries of the current verdict's decision log (add-on E1) and its version number."""
    try:
        if v is None or not hasattr(store, "decision_log"):
            return {"entries": [], "version": None}
        entries = store.decision_log(key, v.verdict_id)
        return {"entries": entries, "version": entries[0]["version"] if entries else None}
    except Exception:  # noqa: BLE001 - the log section never breaks the case page
        return {"entries": [], "version": None}


def case_context(store: Any, ded: Deduction, settings: Any) -> dict:
    v = _current_verdict(store, ded.dispute_key)
    bundle = letter.gather(store, v) if v else {"evidence": [], "terms": [], "agreements": {}, "client": None}
    from pdr.models import Client
    client = bundle.get("client") or store.get(Client, ded.client_id)
    trip = store.get(Trip, ded.trip_refs[0]) if ded.trip_refs else None
    srcs = letter.sources(bundle["evidence"])
    for s in srcs:
        snap = store.snapshot_get(s["snapshot"]) if s["snapshot"] else None
        s["snapshot_at"] = snap.get("stored_at") if snap else ""
    supporting = [s for s in srcs if s["stance"] != "contradicts"]
    contradicting = [s for s in srcs if s["stance"] == "contradicts"]
    clauses = letter.clauses(bundle) if v else []
    flags = list(dict.fromkeys([str(f) for f in (v.flags if v else [])] + [str(f) for f in ded.flags]))
    outcome = v.outcome.value if v else "none"
    ctx = {
        "key": ded.dispute_key, "ded": ded, "v": v, "outcome": outcome, "outcome_label": OUTCOME_LABEL.get(outcome, outcome),
        "grounds": [str(g) for g in (v.grounds if v else [])], "flags": flags,
        "stand_in": Flag.STAND_IN_TERMS.value in flags or any(c.get("stand_in") for c in clauses),
        "out_of_window": Flag.OUT_OF_WINDOW.value in flags,
        "client_name": client.legal_name if client else scrub(ded.client_name_raw),
        "reason_text": scrub(ded.reason_text, ded.client_id), "explanation": scrub(v.explanation, ded.client_id) if v else "",
        "clauses": clauses, "records": letter.records(bundle["evidence"]),
        "supporting": supporting, "contradicting": contradicting, "conflict": bool(supporting and contradicting),
        "actions": [_action_view(store, a, _outbox_names(settings)) for a in _actions_for(store, ded.dispute_key)],
        "ledger": _ledger(store, ded.dispute_key),
        "decision_log": _decision_log(store, ded.dispute_key, v),
    }
    ctx.update(_trip_view(trip, ded, settings))
    return ctx


def _queue_row(store: Any, ded: Deduction, settings: Any) -> dict:
    v = _current_verdict(store, ded.dispute_key)
    acts = _actions_for(store, ded.dispute_key)
    led = _ledger(store, ded.dispute_key)
    cost = v.run_cost_inr if v and v.run_cost_inr else sum((Decimal(str(l.cost_inr)) for l in led), Decimal("0"))
    if v and v.grounds:
        reason = ", ".join(str(g) for g in v.grounds)
    elif v and v.primary_reason:
        reason = str(v.primary_reason)
    else:
        reason = scrub(ded.reason_text, ded.client_id)[:80]
    outcome = v.outcome.value if v else "none"
    return {"key": ded.dispute_key, "page": case_page(ded.dispute_key), "client": scrub(ded.client_name_raw),
            "outcome": outcome, "outcome_label": OUTCOME_LABEL.get(outcome, outcome),
            "flags": list(dict.fromkeys([str(f) for f in (v.flags if v else [])] + [str(f) for f in ded.flags])),
            "amount": ded.amount, "recover": v.amount_disputable if v else Decimal("0"), "reason": reason,
            "stage": str(ded.stage), "cost": cost,
            "action": _action_view(store, acts[0])["status_label"] if acts else "-"}


def _guarded(html: str, client_id: str | None) -> str:
    """Every page passes the K7 guard (it may be attached to a letter); a blocked page is withheld, not written."""
    try:
        from pdr import guard
        guard.check("k7", html, client_id)
    except GuardViolation as exc:
        log.warning("case page withheld by guard: %s", exc)
        return ("<!doctype html><meta charset='utf-8'><title>Withheld</title><body style='font-family:sans-serif;padding:16px'>"
                "<h1>Page withheld</h1><p>The outbound guard found personal data on this page; it was not written. "
                "See the logs.</p></body>")
    return html


def render(store: Any, out_dir: str | Path, settings: Any) -> list[Path]:
    """Render the whole case file from the store, each page replaced atomically; returns the paths written."""
    out = Path(out_dir)
    env = letter.env()
    common = {"generated": datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=5, minutes=30))).isoformat(timespec="seconds"),
              "settings_version": getattr(settings, "version", "")}
    deds = sorted(store.find(Deduction), key=lambda d: d.dispute_key)
    written: list[Path] = []
    rows = []
    for d in deds:
        try:
            ctx = case_context(store, d, settings)
            html = env.get_template("case.html.j2").render(title=f"Case {d.dispute_key}", **common, **ctx)
        except Exception as exc:  # noqa: BLE001 - one broken case never blocks the rest
            log.exception("case page failed for %s", d.dispute_key)
            html = f"<!doctype html><meta charset='utf-8'><title>Case</title><p>Case page could not be rendered ({type(exc).__name__}).</p>"
        written.append(atomic_write(out / case_page(d.dispute_key), _guarded(html, d.client_id)))
        rows.append(_queue_row(store, d, settings))
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["outcome"]] = counts.get(r["outcome"], 0) + 1
    html = env.get_template("index.html.j2").render(
        title="Deduction queue", rows=rows, counts=counts, **common,
        total_deducted=sum((r["amount"] for r in rows), Decimal("0")), total_recover=sum((r["recover"] for r in rows), Decimal("0")))
    written.append(atomic_write(out / "index.html", _guarded(html, None)))
    led = _ledger(store)
    html = env.get_template("money.html.j2").render(
        title="Money", ledger=led, cache_hits=sum(1 for l in led if l.cache_hit), **common,
        total_inr=sum((Decimal(str(l.cost_inr)) for l in led), Decimal("0")),
        total_recover=sum((r["recover"] for r in rows), Decimal("0")), economics=_economics(store, settings))
    written.append(atomic_write(out / "money.html", _guarded(html, None)))
    return written
