"""Paid-call broker (lane A). Stub until lane A lands core.py / exec.py / ledger.py / budget.py.

Every call that costs money goes through paid_call: guard, cache, budget, throttle, retries, ledger.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from pdr.contracts import BudgetReached, CallResult, GuardViolation, RunContext

# provider name ("crustdata", "llm", "web") -> transport object. Tests register FakeTransport here.
TRANSPORTS: dict[str, Any] = {}


def register_transport(name: str, transport: Any) -> None:
    TRANSPORTS[name] = transport


def paid_call(provider: str, endpoint: str, request: dict, *,
              ctx: RunContext, step: str, worst_case_credits: Decimal) -> CallResult:
    """Raises BudgetReached, GuardViolation; returns ok=False after retries (-> PROVIDER_FAILED)."""
    raise NotImplementedError("lane A")


__all__ = ["paid_call", "register_transport", "TRANSPORTS", "CallResult", "BudgetReached", "GuardViolation"]
