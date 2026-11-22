"""Refuter (DESIGN 8.5, D-23): an independent second read of a passage against the claims drawn from it.

Model read first; when no model answers, a deterministic re-check. None = did not finish (never a pass).
"""
from __future__ import annotations

import re

from pdr.contracts import RunContext
from pdr.evidence.candidates import norm_dash, norm_ws
from pdr.llm import RefuteAnswers, llm_json
from pdr.models import Evidence

_STOP = {"the", "and", "was", "were", "with", "that", "this", "from", "into", "have", "has", "had", "for", "are",
         "been", "being", "their", "they", "said", "says", "over", "near", "along"}


def _n(s: str) -> str:
    return norm_ws(norm_dash(s or "")).lower()


def _tokens(claim: str) -> list[str]:
    return [t for t in re.findall(r"[\w]+", _n(claim)) if (len(t) >= 4 or t.isdigit()) and t not in _STOP]


def recheck(passage: str, claims: list[str]) -> list[bool]:
    """Deterministic: a claim is stated if it is in the passage verbatim, or every content word of it is."""
    p = _n(passage)
    words = set(re.findall(r"[\w]+", p))
    out = []
    for c in claims:
        toks = _tokens(c)
        out.append(bool(_n(c)) and (_n(c) in p or (bool(toks) and all(t in words for t in toks))))
    return out


def refute(evidence: Evidence, claims: list[str], ctx: RunContext) -> list[bool] | None:
    """For each claim, True if the passage states it. None if the read did not finish."""
    passage = evidence.passage or ""
    if not passage or not claims:
        return None
    if evidence.content_hash and ctx.store is not None:
        snap = ctx.store.snapshot_get(evidence.content_hash)
        if snap is not None and _n(passage) not in _n(snap.get("text", "")):
            return [False] * len(claims)  # passage no longer in its snapshot
    text = passage + "\n\nCLAIMS:\n" + "\n".join(f"{i}. {c}" for i, c in enumerate(claims, 1))
    got = llm_json("refute", text, RefuteAnswers, ctx)
    if got is not None and len(got.answers) == len(claims):
        # an answer counts as stated only if its quote really is in the passage
        return [a.stated and bool(a.quote) and _n(a.quote) in _n(passage) for a in got.answers]
    if not getattr(ctx.settings.evidence, "refuter", True):
        return None
    return recheck(passage, claims)


__all__ = ["refute", "recheck"]
