"""Evidence service (lane A). Stubs except passage_link.

Lane A only reports what sources state; lane B applies corroboration, notice and causation.
"""
from __future__ import annotations

import re
from decimal import Decimal
from urllib.parse import quote

from pdr.contracts import EvidenceRequest, EvidenceResult, RunContext
from pdr.models import Evidence

_FRAGMENT_WORDS = 6


def find_evidence(req: EvidenceRequest, ctx: RunContext) -> EvidenceResult:
    return EvidenceResult(status="none")


def estimate_credits(req: EvidenceRequest, ctx: RunContext) -> Decimal:
    """For the economic gate: ledger average, else settings (stub: settings only)."""
    return Decimal(ctx.settings.economics.estimated_search_credits)


def refute(evidence: Evidence, claims: list[str], ctx: RunContext) -> list[bool] | None:
    """Independent read; None = did not finish."""
    return None


def _enc(s: str) -> str:
    # text fragments need '-', ',' and '&' escaped as well as the usual characters
    return quote(s, safe="").replace("-", "%2D")


def passage_link(url: str, passage: str) -> str:
    """url#:~:text=<first ~6 words>,<last ~6 words> (whole passage if short)."""
    base = url.split("#", 1)[0]
    words = re.sub(r"\s+", " ", passage or "").strip().split(" ")
    words = [w for w in words if w]
    if not words:
        return base
    if len(words) <= 2 * _FRAGMENT_WORDS:
        return f"{base}#:~:text={_enc(' '.join(words))}"
    start = " ".join(words[:_FRAGMENT_WORDS])
    end = " ".join(words[-_FRAGMENT_WORDS:])
    return f"{base}#:~:text={_enc(start)},{_enc(end)}"


__all__ = ["find_evidence", "estimate_credits", "refute", "passage_link"]
