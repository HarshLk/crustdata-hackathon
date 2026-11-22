"""Crustdata client: news search, page fetch, company identify. All calls go through the broker.

Rules from the live test (RESULTS.md): search news WITHOUT `location` (it returns 0 results with news);
dates are unix seconds; fetch takes up to 10 URLs per request; cost is read from x-credits-used.
After a call, ctx.last_call_failed tells whether retries were exhausted and ctx.last_calls holds the
CallResults (ledger ids, cache hits) of that client call.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from pdr.broker import paid_call
from pdr.contracts import CallResult, RunContext

SEARCH, FETCH, IDENTIFY = "/web/search/live", "/web/enrich/live", "/company/identify"
IST = timezone(timedelta(hours=5, minutes=30))
FETCH_BATCH = 10


def unix_seconds(dt: datetime) -> int:
    """Naive datetimes are taken as IST."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    return int(dt.timestamp())


def search_request(query: str, start: datetime, end: datetime, page: int = 1) -> dict:
    """The exact search body; never has a `location` key (F-1, rule 1)."""
    return {"query": query, "sources": ["news"], "start_date": unix_seconds(start),
            "end_date": unix_seconds(end), "page": page}


def _note(ctx: RunContext, results: list[CallResult], failed: bool) -> None:
    ctx.last_calls = results  # type: ignore[attr-defined]
    ctx.last_call_failed = failed  # type: ignore[attr-defined]


def search(query: str, start: datetime, end: datetime, ctx: RunContext, *, step: str = "search") -> list[dict]:
    """News results [{title, url, snippet, position, source}]; [] when none or when the call failed."""
    req = search_request(query, start, end)
    assert "location" not in req
    r = paid_call("crustdata", SEARCH, req, ctx=ctx, step=step, worst_case_credits=Decimal(1))
    _note(ctx, [r], not r.ok)
    if not r.ok or not isinstance(r.body, dict):
        return []
    out = []
    for it in r.body.get("results") or []:
        if isinstance(it, dict) and it.get("url"):
            out.append({"title": it.get("title") or "", "url": it["url"], "snippet": it.get("snippet") or "",
                        "position": it.get("position"), "source": it.get("source")})
    return out


def _item(requested: str, it: Any, req: dict | None = None) -> dict:
    it = it if isinstance(it, dict) else {}
    success = it.get("success")
    if isinstance(success, str):
        success = success.strip().lower() == "true"
    return {"url": requested, "final_url": it.get("url") or requested, "success": bool(success),
            "title": it.get("title") or "", "content": it.get("content") or "", "timestamp": it.get("timestamp"),
            "http_status": it.get("http_status"), "batch_request": req}


def fetch(urls: list[str], ctx: RunContext, *, human_mode: bool = False, step: str = "fetch") -> list[dict]:
    """[{url (as requested), final_url, success, title, content (raw HTML), timestamp, http_status, batch_request}].

    Never trust `success` (blocked pages say true); the page check is the caller's. Failed batches are
    left out and set ctx.last_call_failed.
    """
    urls = list(dict.fromkeys(u for u in urls if u))
    out, results, failed = [], [], False
    for i in range(0, len(urls), FETCH_BATCH):
        batch = urls[i:i + FETCH_BATCH]
        req: dict = {"urls": batch}
        if human_mode:
            req["human_mode"] = True
        r = paid_call("crustdata", FETCH, req, ctx=ctx, step=step, worst_case_credits=Decimal(len(batch)))
        results.append(r)
        if not r.ok or not isinstance(r.body, list):
            failed = True
            continue
        by_url = {}
        for it in r.body:
            if isinstance(it, dict) and it.get("url"):
                by_url.setdefault(it["url"], it)
        # match by URL; items whose url is none of the requested ones (a final URL, or null on failure) go to
        # the unmatched requests in order (the docs say order may differ, so never reuse a matched item)
        spare = [it for it in r.body if not (isinstance(it, dict) and it.get("url") in batch)]
        for u in batch:
            it = by_url.get(u) or (spare.pop(0) if spare else None)
            out.append(_item(u, it, req))
    _note(ctx, results, failed)
    return out


def identify(names: list[str], ctx: RunContext) -> list[dict]:
    """[{matched_on, match_type, matches: [{confidence_score, company_data}]}]; batched in one call (F-6)."""
    names = [n for n in names if n]
    if not names:
        _note(ctx, [], False)
        return []
    r = paid_call("crustdata", IDENTIFY, {"names": names}, ctx=ctx, step="identify",
                  worst_case_credits=Decimal("0.2") * len(names))
    _note(ctx, [r], not r.ok)
    if not r.ok or not isinstance(r.body, list):
        return []
    return [it for it in r.body if isinstance(it, dict)]


__all__ = ["search", "fetch", "identify", "search_request", "unix_seconds"]
