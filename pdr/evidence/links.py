"""Links that open at the passage (DESIGN 8.6, D-24): url#:~:text=<start>,<end>."""
from __future__ import annotations

import re
from urllib.parse import quote

_FRAGMENT_WORDS = 6


def _enc(s: str) -> str:
    # text fragments need '-', ',' and '&' escaped as well as the usual characters
    return quote(s, safe="").replace("-", "%2D")


def passage_link(url: str, passage: str) -> str:
    """url#:~:text=<first ~6 words>,<last ~6 words> (whole passage if short)."""
    base = url.split("#", 1)[0]
    words = [w for w in re.sub(r"\s+", " ", passage or "").strip().split(" ") if w]
    if not words:
        return base
    if len(words) <= 2 * _FRAGMENT_WORDS:
        return f"{base}#:~:text={_enc(' '.join(words))}"
    start = " ".join(words[:_FRAGMENT_WORDS])
    end = " ".join(words[-_FRAGMENT_WORDS:])
    return f"{base}#:~:text={_enc(start)},{_enc(end)}"


__all__ = ["passage_link"]
