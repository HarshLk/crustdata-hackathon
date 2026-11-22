"""K5 adapter (INTERFACES 3.5): k5_render(verdict, evidence, settings) -> dict."""
from __future__ import annotations

from typing import Any

from pdr.models import Evidence, Verdict
from pdr.outputs.k5 import render_line, write_all


def k5_render(verdict: Verdict, evidence: list[Evidence], settings: Any, store: Any = None) -> dict:
    return render_line(verdict, evidence, settings, store)


__all__ = ["k5_render", "write_all"]
