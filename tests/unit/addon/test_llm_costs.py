"""E2: model calls record tokens from the usage block and are priced from providers.llm.prices."""
from __future__ import annotations

from decimal import Decimal

import pytest

from pdr import broker, economics, llm
from pdr.broker import ledger
from pdr.broker.transport import Response
from tests.unit.broker.conftest import make_ctx

OK_BODY = {"content": [{"type": "text", "text": '{"reason_code": "late_delivery", "quote": "late"}'}],
           "usage": {"input_tokens": 2000, "output_tokens": 500}}


class LlmFake:
    def __init__(self, body=OK_BODY, status=200):
        self.body, self.status, self.calls = body, status, []

    def send(self, endpoint, request, *, cache_key=None, timeout=60.0):
        self.calls.append((endpoint, request))
        return Response(self.status, {}, self.body)


@pytest.fixture
def ctx(tmp_path):
    return make_ctx(tmp_path / "pdr.db")


def _price(ctx, model, pin, pout):
    ctx.settings.providers.llm.prices = {model: {"usd_per_mtok_in": Decimal(pin), "usd_per_mtok_out": Decimal(pout)}}


def test_usage_priced_into_ledger_row(ctx, monkeypatch):
    monkeypatch.setitem(broker.TRANSPORTS, "llm", LlmFake())
    model = ctx.settings.providers.llm.model_parse
    _price(ctx, model, "3", "15")
    assert llm.llm_json("map_reason", "late delivery", llm.ReasonMap, ctx) is not None
    [row] = ledger.rows(ctx.store)
    assert (row.provider, row.model, row.tokens_in, row.tokens_out, row.priced) == ("llm", model, 2000, 500, True)
    usd = Decimal("2000") * 3 / 1000000 + Decimal("500") * 15 / 1000000  # 0.0135
    assert row.cost_usd == usd
    assert row.cost_inr == usd * Decimal(str(ctx.settings.budget.usd_inr))
    assert row.units == 0  # model calls never use Crustdata credits
    t = ledger.totals(ctx.store)[ctx.dispute_key]
    assert (t["tokens_in"], t["tokens_out"], t["unpriced_calls"]) == (2000, 500, 0)


def test_price_zero_marks_row_unpriced(ctx, monkeypatch):
    monkeypatch.setitem(broker.TRANSPORTS, "llm", LlmFake())
    assert llm.llm_json("map_reason", "late delivery", llm.ReasonMap, ctx) is not None
    [row] = ledger.rows(ctx.store)
    assert row.priced is False and row.cost_usd == 0 and row.cost_inr == 0
    assert (row.tokens_in, row.tokens_out) == (2000, 500)
    assert ledger.totals(ctx.store)[ctx.dispute_key]["unpriced_calls"] == 1
    text = economics.format_report({**_report_stub(), "model": economics.model_usage([row])})
    assert "2000 tokens in" in text and "unpriced" in text


def test_cache_hit_is_zero_cost(ctx, monkeypatch):
    fake = LlmFake()
    monkeypatch.setitem(broker.TRANSPORTS, "llm", fake)
    _price(ctx, ctx.settings.providers.llm.model_parse, "3", "15")
    llm.llm_json("map_reason", "late delivery", llm.ReasonMap, ctx)
    assert llm.llm_json("map_reason", "late delivery", llm.ReasonMap, ctx) is not None
    assert len(fake.calls) == 1
    first, hit = ledger.rows(ctx.store)
    assert first.cost_usd > 0 and not first.cache_hit
    assert hit.cache_hit and hit.cost_usd == 0 and hit.cost_inr == 0


def _report_stub() -> dict:
    return {"by_outcome": {}, "fee_inr": None, "total_cost_inr": 0, "total_credits": 0, "overhead_cost_inr": 0,
            "amount_at_stake": 0, "amount_disputable": 0, "expected_recovery": 0, "break_even_fee_rate_pct": None,
            "assumptions": {"win_rate_used": "x", "fee_rate_pct": 0, "note": "n"}}
