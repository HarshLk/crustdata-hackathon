"""Resolve google.com/goto search links to the publisher URL (F-2), as a ladder set in
`evidence.redirect_strategies` (docs/redirect_resolution.md):

  direct          a publisher link is used as is (no call);
  location        our own plain GET, redirects off, read the Location header: one short attempt, free,
                  ledgered under provider "web" step "resolve"; only a redirect to a non-Google host is cached;
  crustdata_fetch Crustdata fetches the goto link itself (human_mode by default, step "resolve_fetch"): the
                  article comes back under the goto URL, and the publisher URL is the page's own canonical /
                  og:url. The fetched content is used as the page, so the article is never paid for twice.

Anything else is `unresolved_redirect`. A google.com URL is never returned as a resolution. Google's
"Verifying your request" page carries no destination (only another goto link), so there is no rung that
reads the interstitial body.
"""
from __future__ import annotations

import html as htmllib
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from urllib.parse import urljoin, urlparse

from pdr.broker import forget, paid_call
from pdr.broker.transport import header
from pdr.contracts import RunContext

STRATEGIES = ("direct", "location", "crustdata_fetch")
FETCH = "/web/enrich/live"
DIRECT, LOCATION, FETCHED, UNRESOLVED = "direct", "location", "crustdata_fetch", "unresolved_redirect"


def is_google(url: str) -> bool:
    h = urlparse(url or "").netloc.lower()
    return h == "google.com" or h.endswith(".google.com") or h.startswith("google.") or ".google." in h


def is_redirect_link(url: str) -> bool:
    p = urlparse(url or "")
    return is_google(url) and p.path in ("/goto", "/url")


def strategies(settings: Any) -> list[str]:
    """The configured ladder (known names only, in order)."""
    got = getattr(getattr(settings, "evidence", None), "redirect_strategies", None)
    got = list(STRATEGIES) if got is None else list(got)
    return [s for s in dict.fromkeys(got) if s in STRATEGIES]


def _publisher(url: str | None) -> str | None:
    """An absolute http(s) URL on a non-Google host with a real path, else None."""
    if not url:
        return None
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.netloc or is_google(url):
        return None
    if p.path in ("", "/") and not p.query:
        return None  # a homepage canonical says nothing about which article this is
    return url


@dataclass
class Resolution:
    source: str  # the link as it came in the search result
    url: str | None  # publisher URL; None when unresolved
    rung: str  # direct | location | crustdata_fetch | unresolved_redirect
    page: Any = None  # pagecheck.Page from the crustdata_fetch rung (content already paid for)
    detail: str = ""
    ledger_entry_id: str | None = None
    spent_fetch: bool = False  # a paid fetch came back without a citable page (counts as a blocked fetch)
    paid: bool = False  # a fetch credit was used on this link
    gated: bool = False  # Google's check page (or skipped because the probe link got it)


def _location(url: str, ctx: RunContext) -> tuple[str | None, str]:
    r = paid_call("web", "GET", {"method": "GET", "url": url, "follow_redirects": False}, ctx=ctx,
                  step="resolve", worst_case_credits=Decimal(0))
    if not r.ok:
        return None, f"location: {r.error or 'failed'}"
    loc = header(r.headers or {}, "location") if r.status and 300 <= r.status < 400 else None
    if not loc:
        return None, f"location: http {r.status} without redirect (Google interstitial)"
    loc = _publisher(urljoin(url, loc))
    return (loc, "") if loc else (None, "location: redirect to a Google page")


def resolve_free(url: str, ctx: RunContext) -> Resolution:
    """The free rungs (direct, location) in the configured order."""
    if not url:
        return Resolution(url, None, UNRESOLVED, detail="no url")
    order = strategies(ctx.settings)
    if not is_google(url):
        if DIRECT in order:
            return Resolution(url, url, DIRECT)
        return Resolution(url, None, UNRESOLVED, detail="direct links switched off")
    if not is_redirect_link(url):
        return Resolution(url, None, UNRESOLVED, detail="google page, not a redirect link")
    detail = "no free rung configured"
    if LOCATION in order:
        loc, detail = _location(url, ctx)
        if loc:
            return Resolution(url, loc, LOCATION)
    return Resolution(url, None, UNRESOLVED, detail=detail)


def resolve(url: str, ctx: RunContext) -> str | None:
    """Publisher URL; the input itself when it is a publisher link; None when the free rungs do not resolve it."""
    ctx.last_call_failed = False  # type: ignore[attr-defined]
    r = resolve_free(url, ctx)
    if r.url is None:
        ctx.last_call_failed = True  # type: ignore[attr-defined]
    return r.url


_CANON = [re.compile(p, re.I | re.S) for p in (
    r'<link\b[^>]*\brel=["\']?canonical["\']?[^>]*\bhref=["\']([^"\']+)["\']',
    r'<link\b[^>]*\bhref=["\']([^"\']+)["\'][^>]*\brel=["\']?canonical\b',
    r'<meta\b[^>]*\bproperty=["\']og:url["\'][^>]*\bcontent=["\']([^"\']+)["\']',
    r'<meta\b[^>]*\bcontent=["\']([^"\']+)["\'][^>]*\bproperty=["\']og:url["\']')]


def canonical_url(raw_html: str, base: str = "") -> str | None:
    """The page's own canonical link, else og:url, as an absolute publisher URL."""
    head = (raw_html or "")[:400000]
    for pat in _CANON:
        m = pat.search(head)
        if m:
            val = htmllib.unescape(m.group(1).strip())
            u = _publisher(urljoin(base, val) if base else val)
            if u:
                return u
    return None


def resolve_via_fetch(urls: list[str], ctx: RunContext, client: Any = None) -> list[Resolution]:
    """crustdata_fetch rung for goto links the free rungs left; the fetched page is kept.

    With evidence.redirect_fetch_probe_first the first link is fetched alone: if Google answers it with its
    check page, the rest are not paid for (marked gated); otherwise the rest go in one batch."""
    if client is None:
        from pdr.evidence import crustdata as client
    urls = list(dict.fromkeys(u for u in urls if u))
    if not urls:
        return []
    probe = bool(getattr(ctx.settings.evidence, "redirect_fetch_probe_first", True)) and len(urls) > 1
    if not probe:
        return _fetch_batch(urls, ctx, client)
    first = _fetch_batch(urls[:1], ctx, client)
    if first[0].gated:
        return first + [Resolution(u, None, UNRESOLVED, gated=True,
                                   detail="crustdata_fetch: not tried, the first link got Google's check page")
                        for u in urls[1:]]
    return first + _fetch_batch(urls[1:], ctx, client)


def _fetch_batch(urls: list[str], ctx: RunContext, client: Any) -> list[Resolution]:
    from pdr.evidence.pagecheck import check_page
    human = bool(getattr(ctx.settings.evidence, "redirect_fetch_human_mode", True))
    ctx.last_calls = []  # type: ignore[attr-defined]
    items = client.fetch(urls, ctx, human_mode=human, step="resolve_fetch") or []
    ids = [getattr(c, "ledger_entry_id", None) for c in (getattr(ctx, "last_calls", None) or [])]
    by_url = {it.get("url"): it for it in items if isinstance(it, dict)}
    out, failed_batches = [], []
    for i, u in enumerate(urls):
        lid = ids[min(i // 10, len(ids) - 1)] if ids else None
        it = by_url.get(u)
        if it is None:
            out.append(Resolution(u, None, UNRESOLVED, detail="crustdata_fetch: no result (call failed)",
                                  ledger_entry_id=lid))
            continue
        content = it.get("content") or ""
        final = _publisher(it.get("final_url")) or canonical_url(content, "")
        page = check_page(dict(it, url=final or u), ctx.settings)
        page.human_mode, page.ledger_entry_id = human, page.ledger_entry_id or lid
        if final is None:
            interstitial = "Verifying your request" in content[:20000]
            if interstitial and it.get("batch_request") and it["batch_request"] not in failed_batches:
                failed_batches.append(it["batch_request"])
            why = "Google interstitial" if interstitial else f"{page.reason or 'page'} without a publisher URL"
            out.append(Resolution(u, None, UNRESOLVED, detail=f"crustdata_fetch: {why}", ledger_entry_id=lid,
                                  spent_fetch=True, paid=True, gated=interstitial))
            continue
        out.append(Resolution(u, final, FETCHED, page=page, ledger_entry_id=lid, paid=True))
    for req in failed_batches:  # never keep Google's interstitial as the answer: a later run asks again
        forget(ctx, "crustdata", FETCH, req)
    return out


__all__ = ["resolve", "resolve_free", "resolve_via_fetch", "canonical_url", "is_redirect_link", "is_google",
           "strategies", "Resolution", "STRATEGIES"]
