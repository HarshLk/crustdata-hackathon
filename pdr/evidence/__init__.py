"""Evidence service (lane A): find_evidence, estimate_credits, refute, passage_link.

Lane A only reports what sources state; lane B applies corroboration, notice and causation.
"""
from __future__ import annotations

from pdr.evidence.links import passage_link
from pdr.evidence.refuter import refute
from pdr.evidence.scout import estimate_credits, find_evidence

__all__ = ["find_evidence", "estimate_credits", "refute", "passage_link"]
