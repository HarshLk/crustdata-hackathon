"""Page text of agreement files: PDF via pypdf; .txt/.md split on form feeds or '--- page N ---' lines."""
from __future__ import annotations

import re
from pathlib import Path

_PAGE_MARK = re.compile(r"(?im)^\s*-{2,}\s*page\s+\d+\s*-{2,}\s*$")


def page_texts(path: str | Path) -> list[str]:
    """One string per page (1-based page n is element n-1). Unreadable PDF -> []."""
    p = Path(path)
    if p.suffix.lower() == ".pdf":
        try:
            from pypdf import PdfReader
            return [pg.extract_text() or "" for pg in PdfReader(str(p)).pages]
        except Exception:  # noqa: BLE001
            return []
    text = p.read_text(encoding="utf-8", errors="replace")
    if "\f" in text:
        pages = text.split("\f")
    elif _PAGE_MARK.search(text):
        pages = _PAGE_MARK.split(text)
        if pages and not pages[0].strip():
            pages = pages[1:]
    else:
        pages = [text]
    return pages


def norm_ws(s: str) -> str:
    """Whitespace-collapsed, de-hyphenated-at-line-break form used for the verbatim-on-page test."""
    s = re.sub(r"-\s*\n\s*", "-", s or "")
    return re.sub(r"\s+", " ", s).strip().lower()


def verbatim_on_page(verbatim: str, page_text: str) -> bool:
    v = norm_ws(verbatim)
    return bool(v) and v in norm_ws(page_text)
