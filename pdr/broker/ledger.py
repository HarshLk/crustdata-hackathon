"""Read-only view of the paid-call ledger for other lanes."""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from pdr.broker.db import connect
from pdr.models import LedgerEntry


def rows(store: Any, run_id: str | None = None, dispute_key: str | None = None) -> list[LedgerEntry]:
    """Ledger entries, oldest first, optionally filtered."""
    sql, args = "SELECT body FROM ledger WHERE 1=1", []
    if run_id is not None:
        sql += " AND run_id=?"
        args.append(run_id)
    if dispute_key is not None:
        sql += " AND dispute_key=?"
        args.append(dispute_key)
    sql += " ORDER BY started_at, rowid"
    return [LedgerEntry.model_validate_json(r[0]) for r in connect(store).execute(sql, args).fetchall()]


def totals(store: Any, by: Literal["deduction", "run", "client"] = "deduction", *,
           run_id: str | None = None) -> dict[str, dict]:
    """{key: {credits, cost_usd, cost_inr, calls, paid_calls, cache_hits, tokens_in, tokens_out, unpriced_calls}}; key "" when the row has none."""
    out: dict[str, dict] = {}
    for e in rows(store, run_id=run_id):
        k = {"deduction": e.dispute_key, "run": e.run_id, "client": e.client_id}[by] or ""
        t = out.setdefault(k, {"credits": Decimal("0"), "cost_usd": Decimal("0"), "cost_inr": Decimal("0"),
                               "calls": 0, "paid_calls": 0, "cache_hits": 0,
                               "tokens_in": 0, "tokens_out": 0, "unpriced_calls": 0})
        t["credits"] += e.units
        t["cost_usd"] += e.cost_usd
        t["cost_inr"] += e.cost_inr
        t["calls"] += 1
        t["cache_hits"] += int(e.cache_hit)
        t["paid_calls"] += int(not e.cache_hit)
        t["tokens_in"] += e.tokens_in or 0
        t["tokens_out"] += e.tokens_out or 0
        t["unpriced_calls"] += int(e.priced is False and not e.cache_hit)
    return out


__all__ = ["rows", "totals"]
