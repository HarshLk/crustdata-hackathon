"""JSONL event log at $PDR_DATA_DIR/logs/<date>.jsonl (INTERFACES 5.2), plus terse stderr lines for the CLI."""
from __future__ import annotations

import json
import os
import sys
import threading
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

KINDS = ("stage", "call", "send", "approval", "guard", "audit", "run", "output", "error")
_lock = threading.Lock()
_bound: dict[str, Any] = {}
_console = {"on": False}


def bind(**fields: Any) -> None:
    """Set fields (e.g. run_id) added to every later line; None removes a field."""
    for k, v in fields.items():
        if v is None:
            _bound.pop(k, None)
        else:
            _bound[k] = v


def set_console(on: bool) -> None:
    """When on, stage changes (and errors) also print one human line to stderr."""
    _console["on"] = on


def log_dir() -> Path:
    return Path(os.environ.get("PDR_DATA_DIR") or "data") / "logs"


def _default(o: Any) -> Any:
    if isinstance(o, Decimal):
        return str(o)
    if isinstance(o, datetime):
        return o.isoformat()
    if hasattr(o, "value"):
        return o.value
    return str(o)


def log_event(kind: str, name: str, outcome: str, **fields: Any) -> dict:
    """Append one line: ts, run_id, dispute_key, kind, name, outcome, cost_credits, cost_inr, ms, extras."""
    now = datetime.now(timezone.utc)
    line = {"ts": now.isoformat(), "run_id": None, "dispute_key": None, **_bound,
            "kind": kind, "name": name, "outcome": outcome,
            "cost_credits": None, "cost_inr": None, "ms": None}
    line.update(fields)
    text = json.dumps(line, default=_default, ensure_ascii=False)
    try:
        d = log_dir()
        d.mkdir(parents=True, exist_ok=True)
        with _lock, open(d / f"{now.date().isoformat()}.jsonl", "a", encoding="utf-8") as fh:
            fh.write(text + "\n")
    except OSError as e:  # logging must never stop a run
        print(f"[log] could not write log line: {e}", file=sys.stderr)
    if _console["on"] and kind in ("stage", "error", "run"):
        who = line.get("dispute_key") or ""
        print(f"  {kind:<5} {who:<28} {name} -> {outcome}", file=sys.stderr, flush=True)
    return line


def read_lines(date: str | None = None) -> list[dict]:
    """All lines of one day's log (today by default)."""
    p = log_dir() / f"{date or datetime.now(timezone.utc).date().isoformat()}.jsonl"
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
