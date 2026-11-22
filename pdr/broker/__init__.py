"""Paid-call broker (lane A): guard, cache, budget, throttle, retries, ledger.

Every call that costs money or leaves the machine goes through paid_call.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from pdr.broker.transport import Response, TransportError, TransportTimeout, cache_key
from pdr.contracts import BudgetReached, CallResult, GuardViolation, RunContext

# provider name ("crustdata", "llm", "web") -> transport object. Tests register FakeTransport here.
TRANSPORTS: dict[str, Any] = {}


def register_transport(name: str, transport: Any) -> None:
    """Use this transport for a provider (None removes it, so the real one is used again)."""
    if transport is None:
        TRANSPORTS.pop(name, None)
    else:
        TRANSPORTS[name] = transport


def paid_call(provider: str, endpoint: str, request: dict, *,
              ctx: RunContext, step: str, worst_case_credits: Decimal) -> CallResult:
    """Raises BudgetReached, GuardViolation; returns ok=False after retries (-> PROVIDER_FAILED)."""
    from pdr.broker.core import paid_call as _impl
    return _impl(provider, endpoint, request, ctx=ctx, step=step, worst_case_credits=worst_case_credits)


__all__ = ["paid_call", "register_transport", "TRANSPORTS", "CallResult", "BudgetReached", "GuardViolation",
           "Response", "TransportError", "TransportTimeout", "cache_key"]
