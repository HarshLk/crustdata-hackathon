"""Deterministic test of a passage proposal (DESIGN 8.3 steps 9-10). The same test applies to model and fallback proposals."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any

from pdr.contracts import EvidenceRequest
from pdr.evidence.candidates import (
    _CALLED_OFF, _STATEWIDE, _UNAFFECTED, affected_place, has_effect, mentions, norm_dash, norm_ws, split_sentences,
)
from pdr.evidence.pagecheck import Page
from pdr.evidence.pubdate import IST
from pdr.llm import PassageProposal
from pdr.models import Evidence, sha256_hex


@dataclass
class Assessment:
    supports: list[Evidence] = field(default_factory=list)
    contradicts: list[Evidence] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)  # why each proposal failed, first failure each
    interval: tuple[datetime, datetime] | None = None


def _n(s: str) -> str:
    return norm_ws(norm_dash(s or "")).lower()


def verbatim(quote: str, snapshot_text: str) -> bool:
    """Quote is a substring of the snapshot text after whitespace (and dash) normalisation."""
    q = _n(quote)
    return bool(q) and q in _n(snapshot_text)


def _quotes(p: PassageProposal) -> list[str]:
    qs = [p.event_quote, p.status_quote, p.date_quote, p.effect_quote, p.statewide_stop_quote]
    qs += [c.quote for c in p.places_affected] + [c.quote for c in p.unaffected]
    return [q for q in qs if q]


def _route_place(value: str, req: EvidenceRequest) -> str | None:
    v = _n(value)
    return next((rp for rp in req.route_places if _n(rp) == v), None)


def _region_named(place: str, page: Page, req: EvidenceRequest) -> bool:
    """The place's state or district appears in the article (Srirampur WB is not Shrirampur MH)."""
    regions = list(req.route_states)
    gaz = getattr(req, "place_regions", None)
    if isinstance(gaz, dict) and gaz.get(place):
        regions = list(gaz[place])
    hay = f"{page.title}\n{page.text}"
    return any(mentions(hay, r) for r in regions if r)


def _day_bounds(d1: date, d2: date) -> tuple[datetime, datetime]:
    return datetime.combine(d1, time(0, 0), IST), datetime.combine(d2, time(23, 59, 59), IST)


def _parse_day(s: str | None) -> date | None:
    try:
        return date.fromisoformat(str(s)[:10]) if s else None
    except ValueError:
        return None


def _evidence(page: Page, passage: str, stance: str, place: str | None, d1: date | None, d2: date | None) -> Evidence:
    from pdr.evidence.links import passage_link
    eid = "EV-" + sha256_hex(f"{page.url}|{norm_ws(passage)}|{stance}")[:16]
    return Evidence(
        evidence_id=eid, kind="external_news", url=page.url, passage=norm_ws(passage), published_at=page.published_at,
        event_from=d1, event_to=d2, place_named=place, retrieved_at=page.retrieved_at,
        retrieved_via="api", fetch_status="ok", content_hash=page.content_hash, stance=stance,
        ledger_entry_id=page.ledger_entry_id, link=passage_link(page.url, norm_ws(passage)),
        source_ref=None,
    )


def test_support(p: PassageProposal, page: Page, req: EvidenceRequest, settings: Any) -> tuple[Evidence | None, str]:
    """(Evidence, "") when the proposal qualifies, else (None, reason)."""
    allow = timedelta(hours=float(settings.evidence.window_allowance_hours))
    # event type
    if p.event_type is None or p.event_type not in req.event_types:
        return None, "event_type_not_covered"
    # status
    if p.status != "happened":
        return None, "not_happened" if p.status == "unclear" else p.status
    if any(_CALLED_OFF.search(s) for s in split_sentences(page.text)) or _CALLED_OFF.search(page.title or ""):
        return None, "called_off"
    # place
    names = list(req.route_places) + list(req.route_states)
    place_hit: tuple[str, str] | None = None
    for c in p.places_affected:
        rp = _route_place(c.value, req)
        if rp and mentions(c.quote, rp) and affected_place(c.quote, rp, names) and _region_named(rp, page, req):
            place_hit = (rp, c.quote)
            break
    if place_hit is None and p.statewide_stop_quote:
        q = p.statewide_stop_quote
        st = next((s for s in req.route_states if mentions(f"{page.title}\n{page.text}", s)), None)
        if st and _STATEWIDE.search(q) and has_effect(q):
            place_hit = (st, q)
    if place_hit is None:
        any_named = any(mentions(c.quote, rp) for c in p.places_affected for rp in req.route_places)
        return None, "region_not_named" if any_named else "place_not_on_route"
    # date
    d1, d2 = _parse_day(p.event_from), _parse_day(p.event_to or p.event_from)
    if d1 is None or d2 is None:
        return None, "no_event_date"
    if d2 < d1:
        d1, d2 = d2, d1
    ev_start, ev_end = _day_bounds(d1, d2)
    w0, w1 = req.window
    if ev_end < w0 or ev_start > w1:
        return None, "out_of_window"
    if page.published_at is None:
        return None, "no_date"
    if page.published_at < ev_start or page.published_at > w1 + allow:
        return None, "published_out_of_window"
    # effect
    if not p.effect_quote or not has_effect(p.effect_quote):
        return None, "no_effect"
    return _evidence(page, place_hit[1], "supports", place_hit[0], d1, d2), ""


def test_contradictions(p: PassageProposal, page: Page, req: EvidenceRequest, settings: Any) -> list[Evidence]:
    """Each 'unaffected' statement naming a route place or state, on a page published around the window."""
    allow = timedelta(hours=float(settings.evidence.window_allowance_hours))
    if page.published_at is None:
        return []
    w0, w1 = req.window
    if page.published_at < w0 - allow or page.published_at > w1 + allow:
        return []
    out: list[Evidence] = []
    for c in p.unaffected:
        target = _route_place(c.value, req) or next((s for s in req.route_states if _n(s) == _n(c.value)), None)
        if target and mentions(c.quote, target) and _UNAFFECTED.search(c.quote):
            d = page.published_at.astimezone(IST).date()
            ev = _evidence(page, c.quote, "contradicts", target, _parse_day(p.event_from) or d, _parse_day(p.event_to) or d)
            if all(e.evidence_id != ev.evidence_id for e in out):
                out.append(ev)
    return out


def assess(proposals: list[PassageProposal], page: Page, req: EvidenceRequest, settings: Any) -> Assessment:
    """Run the test on every proposal for one page; a proposal with any non-verbatim quote is discarded."""
    a = Assessment()
    for p in proposals:
        if not all(verbatim(q, page.text) for q in _quotes(p)):
            a.reasons.append("quote_not_verbatim")
            continue
        ev, why = test_support(p, page, req, settings)
        if ev is not None:
            if all(e.evidence_id != ev.evidence_id for e in a.supports):
                a.supports.append(ev)
            iv = _day_bounds(ev.event_from, ev.event_to)
            a.interval = iv if a.interval is None else (min(a.interval[0], iv[0]), max(a.interval[1], iv[1]))
        else:
            a.reasons.append(why)
        for c in test_contradictions(p, page, req, settings):
            if all(e.evidence_id != c.evidence_id for e in a.contradicts):
                a.contradicts.append(c)
    return a


__all__ = ["Assessment", "assess", "verbatim", "test_support", "test_contradictions"]
