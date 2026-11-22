"""Candidate sentences (DESIGN 8.3 step 7) and the deterministic fallback proposer used when no model answers.

Everything here reads article text only. The proposer is conservative: when unsure it leaves a claim empty,
and an empty claim fails the deterministic test in qualify.py.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Iterable

from pdr.codes import EventType
from pdr.evidence.pubdate import IST
from pdr.llm import Claim, PassageProposal

# --------------------------------------------------------------------------- text helpers

_WS = re.compile(r"\s+")


def norm_ws(s: str) -> str:
    return _WS.sub(" ", s or "").strip()


def norm_dash(s: str) -> str:
    return re.sub(r"[‐-―−]", "-", s)


_SENT_END = re.compile(r"(?<=[.!?।])[\"'”’)]*\s+(?=[\"'“‘(]*[A-Z0-9ऀ-෿])|\n+")


def split_sentences(text: str) -> list[str]:
    """Sentences as exact substrings of norm_ws(text) (lines first, then sentence ends)."""
    out: list[str] = []
    for line in re.split(r"\n+", text or ""):
        line = norm_ws(line)
        if not line:
            continue
        for s in _SENT_END.split(line):
            s = s.strip()
            if s:
                out.append(s)
    return out


def place_rx(name: str) -> re.Pattern:
    """Exact place name, case-insensitive, word-bounded; spaces and hyphens interchangeable. No fuzzy match (D-10)."""
    parts = [re.escape(p) for p in re.split(r"[\s\-]+", norm_dash(name).strip()) if p]
    return re.compile(r"(?<![\w])" + r"[\s\-]+".join(parts) + r"(?![\w])", re.IGNORECASE)


def mentions(text: str, name: str) -> bool:
    return bool(name) and bool(place_rx(name).search(norm_dash(text)))


# --------------------------------------------------------------------------- vocabularies (reading only, never queries)

# Words that identify each event type when READING an article. Queries use settings.evidence.disruption_types only.
READ_WORDS: dict[EventType, list[str]] = {
    EventType.bandh: ["bandh", "shutdown", "hartal"],
    EventType.strike: ["strike", "hartal", "stir"],
    EventType.hartal: ["hartal", "bandh"],
    EventType.chakka_jam: ["chakka jam", "rasta roko", "road blockade"],
    EventType.blockade: ["blockade", "blocked", "blocking", "rasta roko", "road roko", "chakka jam"],
    EventType.agitation: ["agitation", "protest", "stir", "blockade"],
    EventType.flood: ["flood", "floods", "flooding", "inundated", "waterlogged", "waterlogging"],
    EventType.landslide: ["landslide", "landslides", "landslip"],
    EventType.road_closure: ["closed", "closure", "shut"],
    EventType.curfew: ["curfew", "prohibitory orders"],
    EventType.govt_detention: ["stranded", "detained", "held up", "check post", "checkpost", "held at", "been held",
                               "being held", "were held", "stopped at", "not allowed"],
    EventType.riot: ["riot", "riots", "violence", "clashes"],
}

ROAD_NOUNS = r"(?:traffic|vehicles?|vehicular|trucks?|lorr(?:y|ies)|trailers?|buses|bus services|tankers?|highways?|roads?|nh[\s\-]?\d+|goods|transport(?:ation)?|movement|motorists|commuters|containers?|consignments?|carriers?)"
EFFECT_WORDS = (r"(?:stranded|stuck|blocked|halted|halt|standstill|disrupted|disrupts?|diverted|diversions?|"
                r"stopped|paralysed|paralyzed|crippled|off the roads?|did not ply|remained off|held up|"
                r"came to a (?:halt|standstill)|deserted|jammed|detained|(?:been|being|are|were|was|is|remain|remained) held|backed up|queued|suspended|hit)")
_EFFECT = re.compile(r"\b" + EFFECT_WORDS + r"\b", re.IGNORECASE)
_ROAD = re.compile(r"\b" + ROAD_NOUNS + r"\b", re.IGNORECASE)
_FUTURE = re.compile(r"\b(?:will|would|may|might|could|likely to|set to|plans? to|planning to|is to|are to|"
                     r"tomorrow|to observe|calls? for|called for|has called|have called|appeal(?:s|ed)? (?:to|for)|"
                     r"urged?|threaten(?:s|ed)?|warn(?:s|ed)?|announc(?:e|es|ed|ing)|proposed|scheduled|"
                     r"if the|ahead of)\b", re.IGNORECASE)
_OCCURRED = re.compile(r"\b(?:continued|continues|began|entered|enters|staged|launched|observed|held|went on|"
                       r"(?:was|were|is|are) (?:on|underway|being observed)|(?:remained|remains) (?:blocked|shut|closed))\b",
                       re.IGNORECASE)
_EVENT_GENERIC = r"(?:bandh|strike|hartal|protest|agitation|blockade|stir|shutdown|chakka jam|rasta roko)"
_CALLED_OFF = re.compile(
    r"\b(?:called off|calling off|withdrawn|withdrew|withdrawing|postponed|deferred|suspended)\b[^.]{0,80}\b"
    + _EVENT_GENERIC + r"\b|\b" + _EVENT_GENERIC + r"\b[^.]{0,80}\b(?:called off|withdrawn|postponed|deferred|"
    r"(?:was|were|has been|have been) suspended)\b",
    re.IGNORECASE)
_UNAFFECTED = re.compile(r"\b(?:unaffected|not affected|remained normal|was normal|were normal|normal traffic|"
                         r"traffic (?:was|remained) (?:normal|smooth)|(?:plied|plying|running|ran|moving|moved|"
                         r"functioned|functioning|operated|operating) (?:normally|smoothly|as usual)|no impact|"
                         r"no disruption|free[\s\-]flowing)\b", re.IGNORECASE)
_STATEWIDE = re.compile(r"\b(?:across the state|statewide|state-wide|throughout the state|all over the state|"
                        r"across (?:the )?entire state)\b", re.IGNORECASE)

# highway names and directions are never a physically affected place (D-10)
_HIGHWAY = re.compile(
    r"\b[\w.]+(?:\s*-\s*[\w.]+)+\s+(?:national\s+|state\s+)?(?:highway|expressway|road|bypass|nh|stretch|route|corridor|"
    r"section|segment)\b(?:\s*\((?:nh|sh)[\s\-]*\d+\w*\))?"
    r"|\b(?:nh|sh)[\s\-]*\d+\w*\b", re.IGNORECASE)
_DIRECTION = r"(?:from|towards?|to|for|via|bound for|heading (?:to|towards)|travell?ing (?:to|towards)|destined for)"
_LOCATIVE_BEFORE = r"(?:in|at|near|between|across|around|along|outside|on|within)"
_LOCATIVE_AFTER = r"(?:district|town|city|taluka|tehsil|block|toll(?:\s+plaza)?|area|border|check\s*post|checkpost|bypass|junction|square|chowk|naka|phata|village|region|division)"


def event_rx(words: Iterable[str]) -> re.Pattern | None:
    ws = sorted({w.lower() for w in words if w}, key=len, reverse=True)
    if not ws:
        return None
    return re.compile(r"\b(?:" + "|".join(re.escape(w).replace(r"\ ", r"\s+") for w in ws) + r")\b", re.IGNORECASE)


def type_words(et: EventType, settings) -> list[str]:
    configured = list(settings.evidence.disruption_types.get(et, []) or [])
    return configured + READ_WORDS.get(et, [])


def has_effect(sentence: str) -> bool:
    """A road or goods movement noun together with a stop/divert word."""
    return bool(_EFFECT.search(sentence) and _ROAD.search(sentence))


def is_future(sentence: str) -> bool:
    return bool(_FUTURE.search(sentence))


def mask_non_places(sentence: str, places: Iterable[str] = ()) -> str:
    """Blank out highway names and directional mentions ('from Pune', 'towards Kolhapur', 'via Karad')."""
    s = _HIGHWAY.sub(" # ", norm_dash(sentence))
    for p in places:
        rx = place_rx(p).pattern
        s = re.sub(r"\b" + _DIRECTION + r"\s+(?:the\s+)?" + rx, " # ", s, flags=re.IGNORECASE)
        # 'X, Y and Z' after a direction word: 'towards Kolhapur, Sangli and Satara'
        s = re.sub(r"(#\s*(?:,|and)\s*)" + rx, r"\1#", s, flags=re.IGNORECASE)
    return s


def affected_place(sentence: str, place: str, places: Iterable[str] = ()) -> bool:
    """The place is named in a locative position once highway names and directions are removed."""
    s = mask_non_places(sentence, places)
    rx = place_rx(place).pattern
    if re.search(r"\b" + _LOCATIVE_BEFORE + r"\s+(?:the\s+)?" + rx, s, re.IGNORECASE):
        return True
    if re.search(rx + r"(?:'s)?\s+" + _LOCATIVE_AFTER + r"\b", s, re.IGNORECASE):
        return True
    if re.search(r"\bbetween\s+[^,.;#]{1,60}?\s+and\s+(?:the\s+)?" + rx, s, re.IGNORECASE):
        return True
    # a capitalised list after a locative word: 'in Bhubaneswar, Cuttack, Bargarh and Sambalpur'
    item = r"[A-Z][\w\-]*(?:\s+[A-Z][\w\-]*){0,2}"
    lst = r"(?i:\b" + _LOCATIVE_BEFORE + r"|\bplaces?,?\s+including|\blocations?,?\s+including)\s+(?:" + item + r"\s*,\s*(?:and\s+)?|" + item + r"\s+and\s+){0,15}(?i:" + rx + ")"
    return bool(re.search(lst, s))


def candidate_sentences(text: str, places: Iterable[str], states: Iterable[str], words: Iterable[str]) -> list[str]:
    """Sentences naming a route place or state together with an event word (step 7). Exact substrings of norm_ws(text)."""
    erx = event_rx(words)
    if erx is None:
        return []
    names = [n for n in list(places) + list(states) if n]
    return [s for s in split_sentences(text) if erx.search(s) and any(mentions(s, n) for n in names)]


# --------------------------------------------------------------------------- dates

_MONTHS = {m: i for i, ms in enumerate(
    [("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"), ("may",), ("jun", "june"),
     ("jul", "july"), ("aug", "august"), ("sep", "sept", "september"), ("oct", "october"), ("nov", "november"),
     ("dec", "december")], start=1) for m in ms}
_MON = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?"
_DM = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+" + _MON + r"(?:,?\s+(\d{4}))?\b", re.IGNORECASE)
_MD = re.compile(r"\b" + _MON + r"\s+(\d{1,2})(?:st|nd|rd|th)?\b(?:,?\s+(\d{4}))?", re.IGNORECASE)
_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_WD = re.compile(r"\b(" + "|".join(_WEEKDAYS) + r")\b", re.IGNORECASE)
_ORD = {"second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7, "2nd": 2, "3rd": 3, "4th": 4,
        "5th": 5, "6th": 6, "7th": 7}
_NTH_DAY = re.compile(r"\b(second|third|fourth|fifth|sixth|seventh|2nd|3rd|4th|5th|6th|7th)\s+(?:consecutive\s+|straight\s+)?day\b",
                      re.IGNORECASE)
_SINCE = re.compile(r"\bsince\s+(?:last\s+)?(" + "|".join(_WEEKDAYS) + r")\b", re.IGNORECASE)


def _mk(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _year_for(m: int, d: int, pub: date) -> int:
    y = pub.year
    cand = _mk(y, m, d)
    if cand and cand > pub + timedelta(days=7):  # "Dec 30" read in a January article
        y -= 1
    return y


def dates_in(sentence: str, pub: date) -> list[date]:
    """Calendar dates a sentence refers to, resolved against the publication date. Future-looking text is skipped."""
    out: list[date] = []
    for m in _DM.finditer(sentence):
        mon = _MONTHS[m.group(2).lower().rstrip(".")[:3]]
        d = int(m.group(1))
        y = int(m.group(3)) if m.group(3) else _year_for(mon, d, pub)
        if (x := _mk(y, mon, d)):
            out.append(x)
    for m in _MD.finditer(sentence):
        mon = _MONTHS[m.group(1).lower().rstrip(".")[:3]]
        d = int(m.group(2))
        y = int(m.group(3)) if m.group(3) else _year_for(mon, d, pub)
        if (x := _mk(y, mon, d)):
            out.append(x)
    anchor = None
    for m in _WD.finditer(sentence):
        wd = _WEEKDAYS.index(m.group(1).lower())
        back = (pub.weekday() - wd) % 7
        anchor = pub - timedelta(days=back)
        out.append(anchor)
    low = sentence.lower()
    if re.search(r"\btoday\b|\bthis morning\b|\bon the day\b", low):
        out.append(pub)
        anchor = anchor or pub
    if re.search(r"\byesterday\b|\blast night\b", low):
        out.append(pub - timedelta(days=1))
        anchor = anchor or pub - timedelta(days=1)
    for m in _NTH_DAY.finditer(sentence):
        n = _ORD[m.group(1).lower()]
        a = anchor or (max(out) if out else pub)
        out.append(a - timedelta(days=n - 1))
        out.append(a)
    if re.search(r"\bsince\s+(?:\d{1,2}(?:st|nd|rd|th)?\s+)?" + _MON, sentence, re.IGNORECASE) and out:
        out.append(pub)  # 'since September 17': the state continues to the publication date
    for m in _SINCE.finditer(sentence):
        wd = _WEEKDAYS.index(m.group(1).lower())
        back = (pub.weekday() - wd) % 7 or 7
        out.append(pub - timedelta(days=back))
        out.append(pub)
    return [d for d in out if d <= pub + timedelta(days=0)]


# --------------------------------------------------------------------------- deterministic proposer

def propose(text: str, title: str, published: datetime | None, places: list[str], states: list[str],
            event_types: list[EventType], settings) -> list[PassageProposal]:
    """One proposal per requested event type found in the candidate sentences; quotes are exact sentences."""
    sentences = split_sentences(text)
    pub = (published.astimezone(IST).date() if published else None)
    out: list[PassageProposal] = []
    names = list(places) + list(states)
    for et in event_types:
        words = type_words(et, settings)
        erx = event_rx(words)
        if erx is None:
            continue
        cands = [s for s in sentences if erx.search(s) and any(mentions(s, n) for n in names)]
        if not cands:
            continue
        p = PassageProposal(event_type=et, event_quote=cands[0])
        # status
        event_sents = [s for s in sentences if erx.search(s) or _CALLED_OFF.search(s)]
        if any(_CALLED_OFF.search(s) for s in event_sents + [title or ""] if s):
            off = next(s for s in event_sents if _CALLED_OFF.search(s)) if any(_CALLED_OFF.search(s) for s in event_sents) else ""
            p.status, p.status_quote = "called_off", off
        else:
            happened = [s for s in cands if has_effect(s) and not is_future(s)] or \
                [s for s in sentences if erx.search(s) and has_effect(s) and not is_future(s)] or \
                [s for s in cands if _OCCURRED.search(s) and not is_future(s)]
            if happened:
                p.status, p.status_quote = "happened", happened[0]
            elif any(is_future(s) for s in cands):
                p.status, p.status_quote = "announced", next(s for s in cands if is_future(s))
        # places physically affected: route place, locative, in a sentence with an effect or event word
        for s in sentences:
            if is_future(s) or not (has_effect(s) or erx.search(s)):
                continue
            for pl in places:
                if mentions(s, pl) and affected_place(s, pl, names) and not any(c.value == pl for c in p.places_affected):
                    if has_effect(s) or erx.search(s):
                        p.places_affected.append(Claim(value=pl, quote=s))
        # effect
        eff = [s for s in cands if has_effect(s) and not is_future(s)] or \
              [s for s in sentences if erx.search(s) and has_effect(s) and not is_future(s)] or \
              ([s for s in sentences if has_effect(s) and not is_future(s)] if p.status == "happened" else [])
        if eff:
            p.effect_quote = eff[0]
        # statewide stop
        for s in sentences:
            if _STATEWIDE.search(s) and has_effect(s) and not is_future(s) and (erx.search(s) or any(mentions(s, st) for st in states)):
                p.statewide_stop_quote = s
                break
        # dates, resolved against the publication date
        if pub is not None:
            ds: list[date] = []
            dq = ""
            for s in cands + [s for s in sentences if erx.search(s) and s not in cands]:
                if is_future(s):
                    continue
                got = dates_in(s, pub)
                if got:
                    ds.extend(got)
                    dq = dq or s
            if ds:
                p.event_from, p.event_to, p.date_quote = min(ds).isoformat(), max(ds).isoformat(), dq
        # unaffected statements naming a route place
        for s in sentences:
            if _UNAFFECTED.search(s) and _ROAD.search(s):
                for pl in names:
                    if mentions(s, pl) and not any(c.quote == s for c in p.unaffected):
                        p.unaffected.append(Claim(value=pl, quote=s))
        out.append(p)
    return out


__all__ = ["norm_ws", "split_sentences", "place_rx", "mentions", "candidate_sentences", "has_effect", "is_future",
           "affected_place", "mask_non_places", "dates_in", "propose", "type_words", "READ_WORDS"]
