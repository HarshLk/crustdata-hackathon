"""Ledger and economics views (D-29, DESIGN 13): plain dicts for the money page, tables for the CLI."""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Iterable

from pdr.models import Deduction, LedgerEntry, Verdict

ZERO = Decimal("0")
CENT = Decimal("0.01")
ASSUMPTION_NOTE = ("ASSUMPTION set by the team in settings.economics; never read by any verdict. "
                   "Measured cost comes from the ledger.")


def ledger_rows(store: Any, run_id: str | None = None) -> list[LedgerEntry]:
    """The broker's ledger rows ([] when the broker lane is not present)."""
    try:
        from pdr.broker import ledger
        return list(ledger.rows(store, run_id=run_id))
    except Exception:  # noqa: BLE001 - no ledger yet means no measured spend
        return []


def _cost_inr(e: LedgerEntry, settings: Any) -> Decimal:
    if e.cost_inr or e.cache_hit or e.unit_kind != "credits":
        return Decimal(e.cost_inr)
    b = settings.budget  # rows written without a rupee cost: price them from settings
    return Decimal(e.units) * Decimal(b.credit_price_usd) * Decimal(b.usd_inr)


def _current(store: Any) -> dict[str, Verdict]:
    from pdr.outputs.k5 import current_verdicts
    return {v.dispute_key: v for v in current_verdicts(store)}


def _blank() -> dict:
    return {"credits": ZERO, "cost_inr": ZERO, "calls": 0, "paid_calls": 0, "cache_hits": 0}


def _add(t: dict, e: LedgerEntry, settings: Any) -> None:
    t["credits"] += Decimal(e.units) if (e.unit_kind == "credits" and not e.cache_hit) else ZERO
    t["cost_inr"] += _cost_inr(e, settings)
    t["calls"] += 1
    t["cache_hits"] += int(e.cache_hit)
    t["paid_calls"] += int(not e.cache_hit)


def ledger_view(store: Any, settings: Any, by: str = "deduction", rows: Iterable[LedgerEntry] | None = None,
                run_id: str | None = None) -> list[dict]:
    """Totals per deduction, run or client. Deductions with no paid calls appear with zero cost."""
    if by not in ("deduction", "run", "client"):
        raise ValueError("by must be deduction, run or client")
    rows = list(rows) if rows is not None else ledger_rows(store, run_id)
    clients = {d.dispute_key: d.client_id for d in store.find(Deduction)}
    out: dict[str, dict] = {}
    if by == "deduction":
        for k in clients:
            out[k] = _blank()
    for e in rows:
        key = {"deduction": e.dispute_key, "run": e.run_id,
               "client": e.client_id or clients.get(e.dispute_key or "")}[by] or "(none)"
        _add(out.setdefault(key, _blank()), e, settings)
    return [{by: k, **{f: (v.quantize(CENT) if f == "cost_inr" else v) for f, v in t.items()}}
            for k, t in out.items()]


def report(store: Any, settings: Any, rows: Iterable[LedgerEntry] | None = None) -> dict:
    """Cost per deduction by verdict type, amounts at stake and disputable, break-even fee rate, fee and margin."""
    rows = list(rows) if rows is not None else ledger_rows(store)
    verdicts = _current(store)
    per_key: dict[str, dict] = {}
    for e in rows:
        _add(per_key.setdefault(e.dispute_key or "(run overhead)", _blank()), e, settings)
    eco = settings.economics
    fee_pct, win_pct = Decimal(str(eco.fee_rate_pct or 0)), Decimal(str(eco.assumed_win_rate_pct or 0))
    win = win_pct / 100 if win_pct > 0 else Decimal("1")
    by_outcome: dict[str, dict] = {}
    for d in store.find(Deduction):
        v = verdicts.get(d.dispute_key)
        outcome = str(v.outcome.value if hasattr(v.outcome, "value") else v.outcome) if v else "undecided"
        t = by_outcome.setdefault(outcome, {"count": 0, "credits": ZERO, "cost_inr": ZERO,
                                            "amount_at_stake": ZERO, "amount_disputable": ZERO})
        c = per_key.get(d.dispute_key, _blank())
        t["count"] += 1
        t["credits"] += c["credits"]
        t["cost_inr"] += c["cost_inr"]
        t["amount_at_stake"] += Decimal(d.amount)
        t["amount_disputable"] += Decimal(v.amount_disputable) if v else ZERO
    overhead = per_key.get("(run overhead)", _blank())
    for t in by_outcome.values():
        t["cost_per_deduction_inr"] = (t["cost_inr"] / t["count"]).quantize(CENT) if t["count"] else ZERO
        t["credits_per_deduction"] = (t["credits"] / t["count"]).quantize(CENT) if t["count"] else ZERO
        t["cost_inr"] = t["cost_inr"].quantize(CENT)
    total_cost = sum((t["cost_inr"] for t in by_outcome.values()), ZERO) + overhead["cost_inr"]
    disputable = sum((t["amount_disputable"] for t in by_outcome.values()), ZERO)
    expected = disputable * win
    out = {
        "deductions": sum(t["count"] for t in by_outcome.values()),
        "by_outcome": by_outcome,
        "total_credits": sum((t["credits"] for t in by_outcome.values()), ZERO) + overhead["credits"],
        "total_cost_inr": total_cost.quantize(CENT),
        "overhead_cost_inr": overhead["cost_inr"].quantize(CENT),
        "amount_at_stake": sum((t["amount_at_stake"] for t in by_outcome.values()), ZERO),
        "amount_disputable": disputable,
        "expected_recovery": expected.quantize(CENT),
        "break_even_fee_rate_pct": (total_cost / expected * 100).quantize(Decimal("0.001")) if expected else None,
        "assumptions": {"fee_rate_pct": fee_pct, "assumed_win_rate_pct": win_pct, "note": ASSUMPTION_NOTE,
                        "win_rate_used": "assumed" if win_pct > 0 else "every disputable rupee recovered (no win rate set)"},
        "fee_inr": None,
        "margin_inr": None,
    }
    if fee_pct > 0:
        fee = expected * fee_pct / 100
        out["fee_inr"] = fee.quantize(CENT)
        out["margin_inr"] = (fee - total_cost).quantize(CENT)
        for t in by_outcome.values():
            f = t["amount_disputable"] * win * fee_pct / 100
            t["fee_inr"] = f.quantize(CENT)
            t["margin_inr"] = (f - t["cost_inr"]).quantize(CENT)
    return out


def table(rows: list[dict], cols: list[str] | None = None) -> str:
    """Plain fixed-width table."""
    if not rows:
        return "(no rows)"
    cols = cols or list(rows[0])
    cells = [[str(r.get(c, "") if r.get(c) is not None else "-") for c in cols] for r in rows]
    widths = [max(len(c), *(len(x[i]) for x in cells)) for i, c in enumerate(cols)]
    line = lambda xs: "  ".join(x.ljust(w) for x, w in zip(xs, widths))  # noqa: E731
    return "\n".join([line(cols), line(["-" * w for w in widths]), *(line(x) for x in cells)])


def format_report(r: dict) -> str:
    rows = [{"verdict": k, **{f: v for f, v in t.items()}} for k, t in r["by_outcome"].items()]
    cols = ["verdict", "count", "credits_per_deduction", "cost_per_deduction_inr", "amount_at_stake",
            "amount_disputable"] + (["fee_inr", "margin_inr"] if r["fee_inr"] is not None else [])
    a = r["assumptions"]
    lines = [table(rows, cols), "",
             f"total measured cost: Rs {r['total_cost_inr']} ({r['total_credits']} credits; overhead Rs {r['overhead_cost_inr']})",
             f"amount at stake: Rs {r['amount_at_stake']}   disputable: Rs {r['amount_disputable']}   "
             f"expected recovery: Rs {r['expected_recovery']} ({a['win_rate_used']})",
             f"break-even fee rate: {r['break_even_fee_rate_pct']}% of recovered amount" if r["break_even_fee_rate_pct"]
             is not None else "break-even fee rate: n/a (nothing disputable yet)"]
    if r["fee_inr"] is not None:
        lines.append(f"fee at {a['fee_rate_pct']}% [ASSUMPTION]: Rs {r['fee_inr']}   margin: Rs {r['margin_inr']}")
    else:
        lines.append("fee and margin: not shown (economics.fee_rate_pct is 0; set it as a team assumption)")
    lines.append(a["note"])
    return "\n".join(lines)
