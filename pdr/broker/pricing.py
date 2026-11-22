"""Model-call pricing: token counts from the provider's usage block, priced from providers.llm.prices."""
from __future__ import annotations

from decimal import Decimal
from typing import Any

MTOK = Decimal("1000000")


def usage_tokens(body: Any) -> tuple[int, int] | None:
    """(input, output) tokens from an Anthropic or OpenAI-style usage block; None when there is none."""
    u = body.get("usage") if isinstance(body, dict) else None
    if not isinstance(u, dict):
        return None
    tin = u.get("input_tokens", u.get("prompt_tokens"))
    tout = u.get("output_tokens", u.get("completion_tokens"))
    if tin is None and tout is None:
        return None
    try:
        return int(tin or 0), int(tout or 0)
    except (TypeError, ValueError):
        return None


def model_price(settings: Any, model: str | None) -> tuple[Decimal, Decimal]:
    """(usd per million input tokens, usd per million output tokens); (0, 0) when unset."""
    prices = getattr(getattr(settings.providers, "llm", None), "prices", None) or {}
    p = prices.get(model or "") or {}
    return Decimal(str(p.get("usd_per_mtok_in", 0) or 0)), Decimal(str(p.get("usd_per_mtok_out", 0) or 0))


def llm_usage(settings: Any, provider: str, request: dict, body: Any) -> dict | None:
    """Ledger fields for one model response: model, tokens, priced, cost_usd. None for other providers."""
    if provider != "llm":
        return None
    model = request.get("model") if isinstance(request, dict) else None
    toks = usage_tokens(body)
    if toks is None:
        return {"model": model, "tokens_in": None, "tokens_out": None, "priced": False, "cost_usd": Decimal("0")}
    pin, pout = model_price(settings, model)
    priced = pin > 0 or pout > 0
    cost = (Decimal(toks[0]) * pin + Decimal(toks[1]) * pout) / MTOK if priced else Decimal("0")
    return {"model": model, "tokens_in": toks[0], "tokens_out": toks[1], "priced": priced, "cost_usd": cost}


def prices_set(settings: Any, models: list[str]) -> bool:
    """True when every named model has a non-zero price."""
    return all(any(v > 0 for v in model_price(settings, m)) for m in models)


__all__ = ["usage_tokens", "model_price", "llm_usage", "prices_set"]
