"""Ledger and economics views (D-29)."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from pdr.economics import format_report, ledger_view, report
from pdr.models import LedgerEntry
from pdr.settings import load_settings
from pdr.store_mem import MemStore
from tests.unit.platform.fixtures import fill, verdict

ROOT = Path(__file__).resolve().parents[3]
T = datetime(2026, 11, 22, tzinfo=timezone.utc)


def _row(i, key, run="r1", units="1", inr="26.40", hit=False, client=None):
    return LedgerEntry(entry_id=f"L{i}", run_id=run, dispute_key=key, cache_key=f"c{i}", step="search",
                       provider="crustdata", endpoint="/web/search/live", units=Decimal(units),
                       cost_inr=Decimal(inr), cache_hit=hit, outcome="ok", started_at=T, client_id=client)


def _setup():
    st = MemStore()
    d1, d2 = fill(st)
    st.put(verdict(d1))  # disputable 1500
    st.put(verdict(d2, "cannot_verify"))
    rows = [_row(1, d1.dispute_key), _row(2, d1.dispute_key), _row(3, d1.dispute_key, hit=True, inr="0"),
            _row(4, None, run="r2", inr="10.00")]
    return st, d1, d2, rows


def test_ledger_by_deduction_run_client():
    st, d1, d2, rows = _setup()
    s = load_settings(ROOT / "settings.yaml")
    by_d = {r["deduction"]: r for r in ledger_view(st, s, "deduction", rows)}
    assert by_d[d1.dispute_key]["cost_inr"] == Decimal("52.80") and by_d[d1.dispute_key]["cache_hits"] == 1
    assert by_d[d2.dispute_key]["cost_inr"] == Decimal("0.00")  # free deduction still listed
    by_r = {r["run"]: r for r in ledger_view(st, s, "run", rows)}
    assert by_r["r2"]["cost_inr"] == Decimal("10.00")
    by_c = {r["client"]: r for r in ledger_view(st, s, "client", rows)}
    assert by_c["ACME"]["paid_calls"] == 2


def test_report_break_even_and_assumptions():
    st, d1, d2, rows = _setup()
    s = load_settings(ROOT / "settings.yaml")
    r = report(st, s, rows)
    assert r["by_outcome"]["disputable"]["cost_per_deduction_inr"] == Decimal("52.80")
    assert r["by_outcome"]["cannot_verify"]["cost_per_deduction_inr"] == Decimal("0.00")
    assert r["total_cost_inr"] == Decimal("62.80") and r["amount_disputable"] == Decimal("1500.00")
    assert r["break_even_fee_rate_pct"] == Decimal("4.187")
    assert r["fee_inr"] is None and "fee_rate_pct is 0" in format_report(r)
    s2 = load_settings(ROOT / "settings.yaml", {"economics": {"fee_rate_pct": 20, "assumed_win_rate_pct": 50}})
    r2 = report(st, s2, rows)
    assert r2["expected_recovery"] == Decimal("750.00") and r2["fee_inr"] == Decimal("150.00")
    assert r2["margin_inr"] == Decimal("87.20")
    assert "[ASSUMPTION]" in format_report(r2)
