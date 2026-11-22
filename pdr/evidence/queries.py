"""Query ladder (DESIGN 8.2, D-01): allow-listed `<place> <event word>` only, never `location`."""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from pdr.contracts import EvidenceRequest


@dataclass(frozen=True)
class Query:
    text: str
    rung: int  # 1 place, 2 state, 3 regional-language state
    start: datetime
    end: datetime

    def request_fields(self) -> dict:
        """What the Crustdata client receives: query and window only (no location, nothing about a client)."""
        return {"query": self.text, "start": self.start, "end": self.end}


def search_window(req: EvidenceRequest, settings: Any) -> tuple[datetime, datetime]:
    """In-transit window widened by the allowance on both sides."""
    allow = timedelta(hours=float(settings.evidence.window_allowance_hours))
    return req.window[0] - allow, req.window[1] + allow


def event_words(req: EvidenceRequest, settings: Any) -> list[str]:
    """Configured query words for the requested event types, in request order, de-duplicated."""
    vocab = settings.evidence.disruption_types
    out: list[str] = []
    for et in req.event_types:
        for w in vocab.get(et, []) or []:
            w = " ".join(str(w).split())
            if w and w not in out:
                out.append(w)
    return out


def _script(s: str) -> str:
    for ch in s:
        if ch.isalpha():
            name = unicodedata.name(ch, "")
            return name.split(" ")[0] if name else ""
    return ""


def regional_names(req: EvidenceRequest, settings: Any) -> dict[str, str]:
    """state -> regional-language name: from the request if it carries them, else from settings."""
    given = getattr(req, "route_states_regional", None)
    table = given if isinstance(given, dict) and given else (getattr(settings.evidence, "regional_state_names", None) or {})
    return {s: table[s] for s in req.route_states if s in table and table[s]}


def regional_words(req: EvidenceRequest, settings: Any, script: str) -> list[str]:
    table = getattr(settings.evidence, "regional_event_words", None) or {}
    out: list[str] = []
    for et in req.event_types:
        for w in table.get(et.value, table.get(et, [])) or []:
            if _script(w) == script and w not in out:
                out.append(w)
    return out


def _place_order(places: tuple[str, ...]) -> list[str]:
    """Most specific first: intermediate places in route order, then origin and destination."""
    ps = [p for p in dict.fromkeys(" ".join(p.split()) for p in places) if p]
    if len(ps) <= 2:
        return ps
    return ps[1:-1] + [ps[0], ps[-1]]


def ladder(req: EvidenceRequest, settings: Any) -> list[Query]:
    """All rungs in order; the scout stops at the first qualifying rung or a limit."""
    start, end = search_window(req, settings)
    words = event_words(req, settings)
    if not words:
        return []
    qs: list[Query] = []
    seen: set[str] = set()

    def add(text: str, rung: int) -> None:
        if text.lower() not in seen:
            seen.add(text.lower())
            qs.append(Query(text, rung, start, end))

    for w in words:  # first word across every place before the next word
        for place in _place_order(req.route_places):
            add(f"{place} {w}", 1)
    for state in dict.fromkeys(req.route_states):
        for w in words:
            add(f"{state} {w}", 2)
    if settings.evidence.regional_query:
        for state, name in regional_names(req, settings).items():
            for w in regional_words(req, settings, _script(name)):
                add(f"{name} {w}", 3)
    return qs


def plan(req: EvidenceRequest, settings: Any) -> list[Query]:
    """The ladder cut to max_searches_per_deduction: one slot kept for the state rung, the rest to places.

    The regional rung runs only when slots remain (live test, 22 Nov: regional results cannot pass the
    English place test without regional place names, so a slot spent there is a slot lost to a place query).
    """
    qs = ladder(req, settings)
    cap = int(settings.evidence.max_searches_per_deduction)
    if len(qs) <= cap:
        return qs
    places = [q for q in qs if q.rung == 1]
    states = [q for q in qs if q.rung == 2]
    regional = [q for q in qs if q.rung == 3]
    keep_state = 1 if states and cap >= 2 else 0
    chosen = places[:cap - keep_state] + states[:keep_state]
    rest = [q for q in places + states + regional if q not in chosen]
    chosen += rest[:cap - len(chosen)]
    return sorted(chosen, key=lambda q: (q.rung, qs.index(q)))


__all__ = ["Query", "ladder", "plan", "search_window", "event_words", "regional_names"]
