"""RunContext construction: one `runs` row per run."""
from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from pdr.contracts import RunContext, Store
from pdr.settings import Settings, record_version


def new_run(store: Store, settings: Settings, *, time_limit: float | None = None,
            dispute_key: str | None = None, note: str = "") -> RunContext:
    """Create a RunContext (deadline = now + time_limit or budget.run_time_seconds) and record the run."""
    now = datetime.now(timezone.utc)
    run_id = f"r-{now:%Y%m%d-%H%M%S}-{secrets.token_hex(2)}"
    seconds = float(time_limit if time_limit is not None else settings.budget.run_time_seconds)
    ctx = RunContext(run_id=run_id, dispute_key=dispute_key, settings=settings, store=store,
                     deadline=now + timedelta(seconds=seconds))
    record_version(store, settings)
    if hasattr(store, "execute"):
        store.execute("INSERT OR REPLACE INTO runs(run_id, settings_version, started_at, ended_at, body) VALUES(?,?,?,?,?)",
                      (run_id, settings.version, now.isoformat(), None,
                       json.dumps({"time_limit_s": seconds, "deadline": ctx.deadline.isoformat(), "note": note})))
    try:
        from pdr.log import bind, log_event
        bind(run_id=run_id)
        log_event("run", "start", "ok", settings_version=settings.version, time_limit_s=seconds)
    except Exception:  # noqa: BLE001 - logging never blocks a run
        pass
    return ctx


def finish_run(ctx: RunContext, summary: dict[str, Any] | None = None) -> None:
    """Stamp ended_at and a summary on the run row."""
    now = datetime.now(timezone.utc).isoformat()
    store = ctx.store
    if hasattr(store, "execute"):
        rows = store.query("SELECT body FROM runs WHERE run_id=?", (ctx.run_id,))
        body = json.loads(rows[0]["body"]) if rows and rows[0]["body"] else {}
        body.update(summary or {})
        store.execute("UPDATE runs SET ended_at=?, body=? WHERE run_id=?", (now, json.dumps(body, default=str), ctx.run_id))
    try:
        from pdr.log import log_event
        log_event("run", "end", "ok", **(summary or {}))
    except Exception:  # noqa: BLE001
        pass
