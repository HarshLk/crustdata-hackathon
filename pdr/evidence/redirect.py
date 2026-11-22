"""Resolve google.com/goto search links to the publisher URL (F-2): our own request, redirects off,
read the Location header. Free; cached through the broker under provider "web"."""
from __future__ import annotations

from decimal import Decimal
from urllib.parse import urljoin, urlparse

from pdr.broker import paid_call
from pdr.broker.transport import header
from pdr.contracts import RunContext


def is_redirect_link(url: str) -> bool:
    p = urlparse(url or "")
    host = p.netloc.lower()
    return (host == "google.com" or host.endswith(".google.com") or host.startswith("google.")) and \
        p.path in ("/goto", "/url")


def resolve(url: str, ctx: RunContext) -> str | None:
    """Publisher URL; the input itself when it is not a redirect link; None when it does not resolve."""
    ctx.last_call_failed = False  # type: ignore[attr-defined]
    if not url:
        return None
    if not is_redirect_link(url):
        return url
    r = paid_call("web", "GET", {"method": "GET", "url": url, "follow_redirects": False}, ctx=ctx,
                  step="resolve", worst_case_credits=Decimal(0))
    if not r.ok:
        ctx.last_call_failed = True  # type: ignore[attr-defined]
        return None
    loc = header(r.headers or {}, "location") if r.status and 300 <= r.status < 400 else None
    if not loc:
        ctx.last_call_failed = True  # type: ignore[attr-defined]  # e.g. Google's "Verifying" interstitial (200)
        return None
    loc = urljoin(url, loc)
    if is_redirect_link(loc) or urlparse(loc).netloc.lower().endswith("google.com"):
        return None  # e.g. a /sorry/ page: Google is blocking us
    return loc


__all__ = ["resolve", "is_redirect_link"]
