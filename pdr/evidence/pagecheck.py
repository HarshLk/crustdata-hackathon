"""Page check, article text, snapshot (DESIGN 8.3 steps 4-6). `success: true` is never trusted (F-3)."""
from __future__ import annotations

import html as htmllib
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pdr.evidence.pubdate import published_at
from pdr.models import sha256_hex

log = logging.getLogger(__name__)
OPENING_CHARS = 600


@dataclass
class Page:
    url: str
    title: str
    html: str
    text: str
    blocked: bool
    reason: str = ""  # "empty", "short_page", "block_phrase", "" when ok
    published_at: datetime | None = None
    retrieved_at: datetime | None = None
    content_hash: str | None = None
    ledger_entry_id: str | None = None
    human_mode: bool = False


_SCRIPT = re.compile(r"<(script|style|noscript|svg)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t\r\f\v]+")


def strip_tags(raw: str) -> str:
    """Fallback extraction: drop scripts and tags, unescape, collapse spaces."""
    t = _SCRIPT.sub(" ", raw or "")
    t = re.sub(r"<(br|/p|/div|/h\d|/li)[^>]*>", "\n", t, flags=re.IGNORECASE)
    t = htmllib.unescape(_TAG.sub(" ", t))
    lines = [_WS.sub(" ", ln).strip() for ln in t.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def extract_text(raw: str) -> str:
    """Article text via trafilatura; tag-strip fallback when it finds nothing."""
    if not raw or not raw.strip():
        return ""
    if "<" not in raw:
        return raw.strip()
    try:
        import trafilatura
        t = trafilatura.extract(raw, include_comments=False, include_tables=False)
    except Exception as e:  # noqa: BLE001
        log.info("trafilatura failed (%s); falling back to tag strip", type(e).__name__)
        t = None
    return (t or strip_tags(raw)).strip()


def _html_title(raw: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", raw or "", re.IGNORECASE | re.DOTALL)
    return htmllib.unescape(m.group(1)).strip() if m else ""


def block_reason(title: str, raw: str, text: str, settings: Any) -> str:
    """'' if the page is usable, else why it is not."""
    ev = settings.evidence
    opening = " ".join([title or "", _html_title(raw), text[:OPENING_CHARS], strip_tags(raw[:4000])[:OPENING_CHARS]])
    low = opening.lower()
    for phrase in ev.block_phrases:
        if phrase.lower() in low:
            return "block_phrase"
    if not text.strip():
        return "empty"
    if len(text) < int(ev.min_page_chars):
        return "short_page"
    return ""


def _ts(v: Any) -> datetime | None:
    if v in (None, ""):
        return None
    try:
        f = float(v)
        if f > 1e11:  # milliseconds
            f /= 1000
        return datetime.fromtimestamp(f, tz=timezone.utc)
    except (TypeError, ValueError):
        return None


def check_page(item: dict, settings: Any) -> Page:
    """One fetched item -> Page with block verdict, text and publication date (no snapshot yet)."""
    raw = item.get("content") or ""
    title = item.get("title") or ""
    text = extract_text(raw)
    reason = block_reason(title, raw, text, settings)
    retrieved = _ts(item.get("timestamp")) or datetime.now(timezone.utc)
    page = Page(url=item.get("url") or "", title=title, html=raw, text=text, blocked=bool(reason), reason=reason,
                retrieved_at=retrieved,
                ledger_entry_id=item.get("ledger_entry_id") or item.get("_ledger_entry_id"))
    if not page.blocked:
        page.published_at = published_at(raw, not_after=retrieved)
    return page


def content_hash(text: str) -> str:
    return sha256_hex(text)


def snapshot(page: Page, store: Any) -> str:
    """Store extracted text (and raw HTML next to a file-backed store) under the content hash."""
    h = content_hash(page.text)
    html_path = None
    path = getattr(store, "path", ":memory:")
    if path and path != ":memory:" and page.html:
        d = Path(path).resolve().parent / "snapshots"
        try:
            d.mkdir(parents=True, exist_ok=True)
            p = d / f"{h}.html"
            if not p.exists():
                p.write_text(page.html, encoding="utf-8")
            html_path = str(p)
        except OSError as e:
            log.warning("snapshot html not written (%s)", type(e).__name__)
    if store.snapshot_get(h) is None:
        store.snapshot_put(h, page.url, page.text, html_path)
    page.content_hash = h
    return h


FETCH_BATCH = 10


def _ledger_ids(ctx: Any) -> list[str]:
    return [getattr(c, "ledger_entry_id", None) for c in (getattr(ctx, "last_calls", None) or [])]


def fetch_checked(urls: list[str], ctx: Any, client: Any = None) -> list[Page]:
    """Fetch (batched), check each page, retry blocked ones once with human_mode; every attempt is ledgered."""
    if client is None:
        from pdr.evidence import crustdata as client
    crustdata = client
    if not urls:
        return []
    items = crustdata.fetch(list(urls), ctx, step="fetch")
    ids = _ledger_ids(ctx)
    by_url = {it.get("url"): it for it in items or []}
    pages = [check_page(by_url.get(u) or {"url": u}, ctx.settings) for u in urls]
    for i, p in enumerate(pages):
        p.ledger_entry_id = p.ledger_entry_id or (ids[min(i // FETCH_BATCH, len(ids) - 1)] if ids else None)
    retry = [p.url for p in pages if p.blocked]
    if retry and ctx.settings.evidence.human_mode_retry:
        again = crustdata.fetch(retry, ctx, human_mode=True, step="fetch_human")
        ids2 = _ledger_ids(ctx)
        by_url2 = {it.get("url"): it for it in again or []}
        for i, p in enumerate(pages):
            if p.blocked and p.url in by_url2:
                p2 = check_page(by_url2[p.url], ctx.settings)
                p2.human_mode = True
                k = retry.index(p.url) // FETCH_BATCH
                p2.ledger_entry_id = p2.ledger_entry_id or (ids2[min(k, len(ids2) - 1)] if ids2 else None)
                if not p2.blocked:
                    pages[i] = p2
    return pages


__all__ = ["Page", "extract_text", "strip_tags", "block_reason", "check_page", "snapshot", "content_hash",
           "fetch_checked"]
