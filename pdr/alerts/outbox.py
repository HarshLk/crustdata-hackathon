"""`alert_outbox` (D-36): queued -> sending -> sent | unconfirmed | failed. At most once (D-40); never blocks the core."""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from pdr.crashpoints import crashpoint

log = logging.getLogger("pdr.alerts")

DDL = ("CREATE TABLE IF NOT EXISTS alert_outbox (alert_key TEXT PRIMARY KEY, dispute_key TEXT, event TEXT, "
       "version TEXT, roles TEXT, message TEXT, status TEXT, attempts INTEGER DEFAULT 0, next_attempt_at TEXT, "
       "last_error TEXT, created_at TEXT, updated_at TEXT)")
STATUSES = ("queued", "sending", "sent", "unconfirmed", "failed")


class _Db:
    """SQL on the store's own connection; a private in-memory database for stores without SQL (MemStore)."""

    def __init__(self, store: Any) -> None:
        if hasattr(store, "query") and hasattr(store, "execute"):
            self.q, self.x = store.query, store.execute
        else:
            conn = getattr(store, "_alert_db", None)
            if conn is None:
                conn = sqlite3.connect(":memory:", check_same_thread=False)
                conn.row_factory = sqlite3.Row
                store._alert_db = conn
            self.q = lambda sql, p=(): [dict(r) for r in conn.execute(sql, p).fetchall()]
            self.x = lambda sql, p=(): (conn.execute(sql, p), conn.commit())
        self.x(DDL)


def _now(now: Optional[datetime] = None) -> datetime:
    return now or datetime.now(timezone.utc)


def _log(event: str, outcome: str, **kw: Any) -> None:
    try:
        from pdr.log import log_event
        log_event("alert", event, outcome, **kw)
    except Exception:  # noqa: BLE001 - logging never blocks
        pass


def queue(store: Any, key: str, dispute_key: str, event: str, version: str, roles: list[str], message: dict,
          now: Optional[datetime] = None) -> bool:
    """Insert once; an existing key is a no-op. Returns True when a new row was queued."""
    db = _Db(store)
    if db.q("SELECT 1 FROM alert_outbox WHERE alert_key=?", (key,)):
        return False
    t = _now(now).isoformat()
    db.x("INSERT OR IGNORE INTO alert_outbox(alert_key,dispute_key,event,version,roles,message,status,attempts,"
         "next_attempt_at,last_error,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
         (key, dispute_key, event, version, json.dumps(roles), json.dumps(message, default=str), "queued", 0, None, None, t, t))
    return True


def rows(store: Any, status: Optional[str] = None) -> list[dict]:
    db = _Db(store)
    sql = "SELECT * FROM alert_outbox" + (" WHERE status=?" if status else "") + " ORDER BY created_at, alert_key"
    out = db.q(sql, (status,) if status else ())
    for r in out:
        r["roles"] = json.loads(r["roles"] or "[]")
        r["message"] = json.loads(r["message"] or "{}")
    return out


def _set(db: _Db, key: str, now: Optional[datetime] = None, **cols: Any) -> None:
    cols["updated_at"] = _now(now).isoformat()
    names = ",".join(f"{k}=?" for k in cols)
    db.x(f"UPDATE alert_outbox SET {names} WHERE alert_key=?", (*cols.values(), key))


def _sink(settings: Any, sink: Any) -> Any:
    if sink is not None:
        return sink
    from pdr.alerts.sinks import get_sink
    return get_sink(settings)


def _cfg(settings: Any, name: str, default: Any) -> Any:
    return getattr(getattr(settings, "alerts", None), name, default)


def reconcile(store: Any, settings: Any, sink: Any = None, now: Optional[datetime] = None) -> dict:
    """Every `sending` alert: checkable sink -> present: sent, absent: deliver once; blind sink -> unconfirmed."""
    db, out = _Db(store), {"sent": [], "redelivered": [], "unconfirmed": [], "failed": []}
    try:
        sink = _sink(settings, sink)
        if hasattr(sink, "cleanup"):
            sink.cleanup()
        caps = sink.capabilities()
    except Exception as exc:  # noqa: BLE001 - never raises into the core
        log.warning("alert sink unusable at reconcile: %s", type(exc).__name__)
        return out
    for r in rows(store, "sending"):
        key = r["alert_key"]
        if not caps.get("can_check"):
            _set(db, key, now, status="unconfirmed", last_error="left in sending; sink cannot check (D-40)")
            _log("reconcile", "unconfirmed", alert_key=key)
            out["unconfirmed"].append(key)
            continue
        try:
            if sink.exists(key):
                _set(db, key, now, status="sent")
                out["sent"].append(key)
                continue
            sink.deliver(key, r["message"])
        except Exception as exc:  # noqa: BLE001
            _set(db, key, now, status="failed", last_error=f"reconcile: {type(exc).__name__}"[:200])
            _log("reconcile", "PROVIDER_FAILED", alert_key=key, error=type(exc).__name__)
            out["failed"].append(key)
            continue
        _set(db, key, now, status="sent")
        out["redelivered"].append(key)
    return out


def send_pending(store: Any, settings: Any, sink: Any = None, now: Optional[datetime] = None) -> dict:
    """Reconcile, then deliver due `queued` alerts once each; retry with backoff only on a clear non-acceptance."""
    from pdr.alerts.sinks import SinkRejected, SinkUnavailable
    out = {"reconcile": reconcile(store, settings, sink, now), "sent": [], "retry": [], "failed": [],
           "unconfirmed": [], "skipped": []}
    db = _Db(store)
    try:
        sink = _sink(settings, sink)
        caps = sink.capabilities()
    except Exception as exc:  # noqa: BLE001
        log.warning("alert sink unusable: %s", type(exc).__name__)
        return out
    if not caps.get("available", True):
        out["skipped"] = [r["alert_key"] for r in rows(store, "queued")]
        return out
    max_attempts = int(_cfg(settings, "max_attempts", 3))
    backoff = float(_cfg(settings, "backoff_seconds", 60))
    t = _now(now)
    for r in rows(store, "queued"):
        key = r["alert_key"]
        if r["next_attempt_at"] and datetime.fromisoformat(r["next_attempt_at"]) > t:
            continue
        if caps.get("can_check"):
            try:
                if sink.exists(key):
                    _set(db, key, now, status="sent")
                    out["sent"].append(key)
                    continue
            except Exception:  # noqa: BLE001
                pass
        attempts = int(r["attempts"] or 0) + 1
        _set(db, key, now, status="sending", attempts=attempts)
        crashpoint("alert.after_intent")
        try:
            sink.deliver(key, r["message"])
        except Exception as exc:  # noqa: BLE001 - a sink failure never reaches the core
            clear = isinstance(exc, (SinkRejected, SinkUnavailable))
            if not clear and caps.get("can_check"):
                try:
                    clear = not sink.exists(key)
                except Exception:  # noqa: BLE001
                    clear = False
            if not clear:
                _set(db, key, now, status="unconfirmed", last_error=f"ambiguous: {type(exc).__name__}"[:200])
                _log("deliver", "unconfirmed", alert_key=key, error=type(exc).__name__)
                out["unconfirmed"].append(key)
            elif attempts >= max_attempts:
                _set(db, key, now, status="failed", last_error=f"PROVIDER_FAILED: {type(exc).__name__}"[:200])
                _log("deliver", "PROVIDER_FAILED", alert_key=key, attempts=attempts, error=type(exc).__name__)
                log.warning("alert %s failed after %d attempts: PROVIDER_FAILED", key, attempts)
                out["failed"].append(key)
            else:
                nxt = t + timedelta(seconds=backoff * (2 ** (attempts - 1)))
                _set(db, key, now, status="queued", next_attempt_at=nxt.isoformat(),
                     last_error=type(exc).__name__[:200])
                out["retry"].append(key)
            continue
        crashpoint("alert.after_send")
        _set(db, key, now, status="sent")
        _log("deliver", "ok", alert_key=key, roles=r["roles"])
        out["sent"].append(key)
    return out


def safe_send(store: Any, settings: Any, sink: Any = None, now: Optional[datetime] = None) -> dict:
    """send_pending that never raises (for callers in the core path)."""
    try:
        return send_pending(store, settings, sink, now)
    except Exception as exc:  # noqa: BLE001
        log.warning("alerts: %s", type(exc).__name__)
        return {"error": type(exc).__name__}
