"""find_evidence (DESIGN 8.3): ladder -> search -> snippet filter -> resolve -> fetch -> page check -> snapshot
-> date -> candidates -> proposal -> deterministic test. Lane A reports what sources state; lane B decides."""
from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit

from pdr.contracts import BudgetReached, EvidenceRequest, EvidenceResult, RejectedSource, RunContext, SearchLogLine
from pdr.evidence import candidates as cand
from pdr.evidence.pagecheck import Page, fetch_checked, snapshot
from pdr.evidence.qualify import assess
from pdr.evidence.queries import Query, plan
from pdr.llm import PassageProposals, llm_json

log = logging.getLogger(__name__)
SEARCH_CREDITS = Decimal("1")  # a search page with results; zero-result searches are free (F-5)


def _crust():
    from pdr.evidence import crustdata
    return crustdata


def _host(url: str) -> str:
    return urlsplit(url).netloc.lower()


def _is_google(url: str) -> bool:
    h = _host(url)
    return h == "google.com" or h.endswith(".google.com") or ".google." in f".{h}"


def _resolve(url: str, ctx: RunContext) -> str | None:
    """Publisher URL; google.com redirect links are resolved locally (free), never fetched through Crustdata."""
    if not _is_google(url):
        return url
    try:
        from pdr.evidence import redirect
        return redirect.resolve(url, ctx)
    except ImportError:
        return None


def _last_call(ctx: RunContext) -> tuple[Decimal, bool, str | None]:
    """Credits, cache hit and ledger id of the client's last call (the client leaves CallResults on ctx.last_calls)."""
    calls = list(getattr(ctx, "last_calls", None) or [])
    if not calls:
        return Decimal("0"), False, None
    credits = sum((Decimal(str(getattr(c, "credits", 0) or 0)) for c in calls), Decimal("0"))
    return credits, all(getattr(c, "cache_hit", False) for c in calls), getattr(calls[0], "ledger_entry_id", None)


def snippet_ok(result: dict, req: EvidenceRequest) -> bool:
    """Free pre-filter: title or snippet names a route place or route state."""
    hay = f"{result.get('title') or ''} {result.get('snippet') or ''}"
    return any(cand.mentions(hay, n) for n in list(req.route_places) + list(req.route_states))


def read_page(page: Page, req: EvidenceRequest, ctx: RunContext):
    """Candidates -> model proposal (or deterministic fallback) -> test. Returns (Assessment|None, reason)."""
    s = ctx.settings
    words = [w for et in req.event_types for w in cand.type_words(et, s)]
    cands = cand.candidate_sentences(page.text, req.route_places, req.route_states, words)
    if not cands:
        return None, "no_candidates"
    proposals = None
    got = llm_json("propose_passages", page.text, PassageProposals, ctx)
    if got is not None:
        proposals = got.proposals
    if not proposals:
        proposals = cand.propose(page.text, page.title, page.published_at, list(req.route_places),
                                 list(req.route_states), list(req.event_types), s)
    return assess(proposals, page, req, s), ""


def find_evidence(req: EvidenceRequest, ctx: RunContext) -> EvidenceResult:
    s = ctx.settings.evidence
    res = EvidenceResult(status="none")
    if not req.route_places:
        res.status = "no_route_places"
        return res
    if ctx.remaining_credits() < SEARCH_CREDITS:
        res.status = "budget"
        return res
    queries = plan(req, ctx.settings)
    fetched = blocked = 0
    seen_urls: set[str] = set()
    provider_failed = undated_with_candidates = gated = False
    try:
        for rung in sorted({q.rung for q in queries}):
            for q in [q for q in queries if q.rung == rung]:
                if fetched >= s.max_fetches_per_deduction or blocked >= s.max_blocked_fetches:
                    break
                if ctx.remaining_credits() < SEARCH_CREDITS:
                    raise BudgetReached("below one search")
                ctx.last_call_failed = False
                ctx.last_calls = []
                try:
                    results = _crust().search(q.text, q.start, q.end, ctx, step="search") or []
                except BudgetReached:
                    raise
                except Exception as e:  # noqa: BLE001  a provider or transport fault is not a finding
                    log.warning("search failed (%s)", type(e).__name__)
                    results, ctx.last_call_failed = [], True
                credits, hit, lid = _last_call(ctx)
                res.search_log.append(SearchLogLine(query=q.text, window=(q.start, q.end), n_results=len(results),
                                                    credits=credits, cache_hit=hit, ledger_entry_id=lid))
                if not results and getattr(ctx, "last_call_failed", False):
                    provider_failed = True
                    continue
                counts = res.search_log[-1].redirects
                urls, pending = _pick_urls(results, req, ctx, res, seen_urls, s.max_fetches_per_deduction - fetched,
                                           counts, allow_paid=not gated)
                if not urls and not pending:
                    continue
                try:
                    pages = fetch_checked(urls, ctx, _crust()) if urls else []
                    fetched += len(urls)
                    more, paid, wasted, was_gated = _resolve_by_fetch(pending, ctx, res, seen_urls, counts)
                    pages += more
                    fetched += paid
                    blocked += wasted  # a paid goto fetch with no citable page counts as a blocked fetch
                    gated = gated or was_gated  # Google is gating these links: no more paid goto fetches here
                except BudgetReached:
                    raise
                except Exception as e:  # noqa: BLE001
                    log.warning("fetch failed (%s)", type(e).__name__)
                    provider_failed = True
                    continue
                for page in pages:
                    if page.blocked:
                        blocked += 1
                        res.rejected.append(RejectedSource(page.url, "blocked" if page.reason == "block_phrase"
                                                           else page.reason, page.title[:120], page.ledger_entry_id))
                        continue
                    snapshot(page, ctx.store)
                    if page.published_at is None:
                        words = [w for et in req.event_types for w in cand.type_words(et, ctx.settings)]
                        if cand.candidate_sentences(page.text, req.route_places, req.route_states, words):
                            undated_with_candidates = True
                        res.rejected.append(RejectedSource(page.url, "no_date", "", page.ledger_entry_id))
                        continue
                    a, why = read_page(page, req, ctx)
                    if a is None:
                        res.rejected.append(RejectedSource(page.url, why, "", page.ledger_entry_id))
                        continue
                    res.supporting.extend(e for e in a.supports if e.evidence_id not in {x.evidence_id for x in res.supporting})
                    res.contradicting.extend(e for e in a.contradicts
                                             if e.evidence_id not in {x.evidence_id for x in res.contradicting})
                    if a.interval:
                        res.event_interval = a.interval if res.event_interval is None else (
                            min(res.event_interval[0], a.interval[0]), max(res.event_interval[1], a.interval[1]))
                    if not a.supports:
                        res.rejected.append(RejectedSource(page.url, a.reasons[0] if a.reasons else "no_proposal",
                                                           ",".join(a.reasons), page.ledger_entry_id))
            if res.supporting:
                break  # first qualifying rung
    except BudgetReached:
        res.status = "found" if res.supporting else "budget"
        return res
    res.supporting = [e for e in res.supporting if not _is_google(e.url)]  # never cite a google.com URL
    res.contradicting = [e for e in res.contradicting if not _is_google(e.url)]
    if res.supporting:
        res.status = "found"
    elif undated_with_candidates:
        res.status = "undated_only"
    elif provider_failed and not any(l.n_results for l in res.search_log):
        res.status = "provider_failed"
    else:
        res.status = "none"
    return res


def _pick_urls(results: list[dict], req: EvidenceRequest, ctx: RunContext, res: EvidenceResult,
               seen: set[str], room: int, counts: dict | None = None, *,
               allow_paid: bool = True) -> tuple[list[str], list[str]]:
    """Snippet pre-filter and the free redirect rungs, in rank order, up to the fetch room left.

    Returns (publisher URLs to fetch, goto links left for the paid crustdata_fetch rung)."""
    counts = {} if counts is None else counts
    paid = allow_paid and _paid_rung(ctx)
    urls: list[str] = []
    pending: list[str] = []
    for r in results:
        if len(urls) + len(pending) >= room:
            break
        url = r.get("url") or ""
        if not url or url in seen:
            continue
        seen.add(url)
        if not snippet_ok(r, req):
            res.rejected.append(RejectedSource(url, "snippet_no_place", (r.get("title") or "")[:120]))
            continue
        real = _resolve(url, ctx)
        if not real or _is_google(real):
            if paid and _is_redirect(url):
                pending.append(url)
                continue
            counts["unresolved_redirect"] = counts.get("unresolved_redirect", 0) + 1
            res.rejected.append(RejectedSource(url, "unresolved_redirect", ""))
            continue
        rung = "direct" if real == url else "location"
        counts[rung] = counts.get(rung, 0) + 1
        if real in seen and real != url:
            continue
        seen.add(real)
        urls.append(real)
    return urls, pending


def _paid_rung(ctx: RunContext) -> bool:
    try:
        from pdr.evidence import redirect
        return "crustdata_fetch" in redirect.strategies(ctx.settings)
    except ImportError:
        return False


def _is_redirect(url: str) -> bool:
    from pdr.evidence import redirect
    return redirect.is_redirect_link(url)


def _resolve_by_fetch(pending: list[str], ctx: RunContext, res: EvidenceResult, seen: set[str],
                      counts: dict) -> tuple[list[Page], int, int, bool]:
    """crustdata_fetch rung: the goto link fetched through Crustdata; its page is kept (no second fetch).

    Returns (pages under their publisher URL, fetches paid, paid fetches with nothing citable, Google gating)."""
    if not pending:
        return [], 0, 0, False
    from pdr.evidence import redirect
    pages: list[Page] = []
    paid = wasted = 0
    gated = False
    for rz in redirect.resolve_via_fetch(pending, ctx, _crust()):
        paid += int(rz.paid)
        gated = gated or rz.gated
        if rz.url is None:
            wasted += int(rz.spent_fetch)
            counts["unresolved_redirect"] = counts.get("unresolved_redirect", 0) + 1
            res.rejected.append(RejectedSource(rz.source, "unresolved_redirect", rz.detail, rz.ledger_entry_id))
            continue
        counts[rz.rung] = counts.get(rz.rung, 0) + 1
        if rz.url in seen:
            continue
        seen.add(rz.url)
        pages.append(rz.page)
    return pages, paid, wasted, gated


def estimate_credits(req: EvidenceRequest, ctx: RunContext) -> Decimal:
    """For the economic gate: ledger average per evidence group if the ledger has one, else the setting."""
    try:
        from pdr.broker import ledger
        rows = [r for r in ledger.rows(ctx.store) if r.step in ("search", "fetch", "fetch_human", "resolve_fetch")
                and not r.cache_hit]
        groups = {r.dispute_key for r in rows if r.dispute_key}
        if rows and groups:
            return (sum((Decimal(str(r.units)) for r in rows), Decimal("0")) / len(groups)).quantize(Decimal("0.01"))
    except Exception:  # noqa: BLE001  no ledger yet
        pass
    return Decimal(str(ctx.settings.economics.estimated_search_credits))


__all__ = ["find_evidence", "estimate_credits", "read_page", "snippet_ok"]
