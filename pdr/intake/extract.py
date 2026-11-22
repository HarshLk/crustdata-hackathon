"""Propose Terms from agreement page text: model (if available) plus a deterministic clause-pattern extractor.

Every proposed term must have its verbatim text found on the stated page, or it is rejected.
"As per annexure" references give low confidence and a missing-document record.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Optional

from pydantic import BaseModel

from pdr.codes import Base, DayCount, EventType, RateUnit, ReasonCode
from pdr.models import Term, norm_key, sha256_hex

from .pdftext import norm_ws, verbatim_on_page

DET_CONFIDENCE = 0.6
ANNEX_CONFIDENCE = 0.2

_ABBREV = re.compile(r"(?:\bRs|\bNo|\bNos|\bviz|\bi\.e|\be\.g|\bSr|\bCl|\betc|\bp|\bpp|\bMr|\bDr|\bSt|\bvs|\d)\.$", re.I)
_CLAUSE_NO = re.compile(r"(?m)^\s*(?:clause\s+)?(\d+(?:\.\d+)*[A-Za-z]?|\([ivxlc]+\)|\([a-z]\)|[IVXL]+)[.)]?\s+(?=\S)", re.I)
_ANNEX = re.compile(r"\b(?:as\s+per|in|under|vide|per|refer(?:red)?\s+to\s+in|given\s+in|specified\s+in|mentioned\s+in|"
                    r"set\s+out\s+in|at)\s+(?:the\s+)?(Annexure|Annex|Schedule|Appendix|Attachment)[\s\-–]*([A-Z]|[IVX]+|\d+)\b", re.I)


@dataclass
class Segment:
    text: str
    start: int
    para: str


@dataclass
class IntakeResult:
    terms: list[Term] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)  # {"verbatim", "page", "why"}
    missing_documents: list[str] = field(default_factory=list)
    requests: list[str] = field(default_factory=list)  # drafted document-request texts


# --------------------------------------------------------------------------- segmentation

def paragraphs(text: str) -> list[tuple[int, str]]:
    """(start offset, paragraph) split on blank lines and on numbered clause lines."""
    out, pos = [], 0
    starts = sorted({0, *[m.end() for m in re.finditer(r"\n\s*\n", text)], *[m.start() for m in _CLAUSE_NO.finditer(text)]})
    for i, s in enumerate(starts):
        e = starts[i + 1] if i + 1 < len(starts) else len(text)
        chunk = text[s:e]
        if chunk.strip():
            lead = len(chunk) - len(chunk.lstrip())
            out.append((s + lead, chunk.strip()))
    return out


def sentences(text: str) -> list[Segment]:
    segs: list[Segment] = []
    for pstart, para in paragraphs(text):
        cur = 0
        for m in re.finditer(r"[.;](?=\s+[A-Z(\"'0-9])|[.;]\s*$", para):
            piece = para[cur:m.end()]
            if _ABBREV.search(piece.rstrip()) and m.end() < len(para):
                continue
            if piece.strip():
                lead = len(piece) - len(piece.lstrip())
                segs.append(Segment(piece.strip(), pstart + cur + lead, para))
            cur = m.end()
        tail = para[cur:]
        if tail.strip():
            lead = len(tail) - len(tail.lstrip())
            segs.append(Segment(tail.strip(), pstart + cur + lead, para))
    return segs


def clause_ref_at(text: str, offset: int, page: int) -> str:
    last = None
    for m in _CLAUSE_NO.finditer(text[:offset + 1]):
        last = m.group(1)
    return last or f"page {page}"


# --------------------------------------------------------------------------- patterns

def _base(words: str) -> Optional[Base]:
    w = (words or "").lower()
    for key, b in (("delayed quantity", Base.delayed_quantity_value), ("delayed", Base.delayed_quantity_value),
                   ("trip cost", Base.trip_cost), ("freight", Base.trip_cost), ("daily hire", Base.daily_hire),
                   ("hire charge", Base.daily_hire), ("invoice", Base.invoice_value), ("contract value", Base.contract_value),
                   ("contract price", Base.contract_value), ("order value", Base.contract_value)):
        if key in w:
            return b
    return None


_RATE_PCT = re.compile(r"(\d+(?:\.\d+)?)\s*%\s*(?:of\s+(?P<base>[^%;]{3,80}?)\s*)?(?:per|for\s+every|for\s+each|a)\s+"
                       r"(?P<unit>vehicle\s+per\s+day|vehicle[\s-]+day|week(?:\s+or\s+part(?:\s+of\s+a\s+week|\s+thereof|\s+week)?)?|day)", re.I)
_RATE_FLAT = re.compile(r"(?:Rs\.?|INR|₹)\s*([\d,]+(?:\.\d+)?)\s*(?:/-)?\s*(?:\([^)]{0,40}\)\s*)?"
                        r"(?:per|a)\s+(?P<unit>day\s+per\s+quintal|quintal\s+per\s+day|qtl\.?\s+per\s+day|day|instance|occasion)", re.I)
_CAP = re.compile(r"(?:subject\s+to\s+(?:a\s+|the\s+)?maximum\s+of|maximum\s+of|not\s+(?:to\s+)?exceed(?:ing)?|capped\s+at|"
                  r"ceiling\s+of|limited\s+to|upto\s+a\s+maximum\s+of|up\s+to\s+a\s+maximum\s+of)\s*(?:@\s*)?"
                  r"(?:(\d+(?:\.\d+)?)\s*%\s*(?:of\s+(?P<base>[^;.]{3,80}))?|(?:Rs\.?|INR|₹)\s*(?P<abs>[\d,]+(?:\.\d+)?))", re.I)
_NOTICE_WORDS = re.compile(r"\b(notice|notif|intimat|inform|report|advise)", re.I)
_WITHIN = re.compile(r"within\s*(\d+(?:\.\d+)?)\s*(hours?|hrs?|days?|working\s+days?|calendar\s+days?)", re.I)
_TIME_LIMIT = re.compile(r"within\s*(\d+)\s*(calendar\s+|working\s+)?days?\s+(?:of|from)\s+(?:the\s+)?(?:date\s+of\s+)?(?:the\s+)?"
                         r"(receipt|deduction|expiry|communication|intimation|notice\s+of\s+deduction)", re.I)
_DISPUTE_WORDS = re.compile(r"\b(dispute|claim|represent|object|contest|appeal|challenge)", re.I)
_FM = re.compile(r"force\s*majeure", re.I)
_EXCL = re.compile(r"\b(exclud|shall\s+not\s+(?:be\s+)?includ|not\s+be\s+included|does\s+not\s+(?:cover|include)|"
                   r"shall\s+not\s+(?:cover|apply)|except|other\s+than|not\s+(?:be\s+)?(?:treated|considered|deemed)\s+as)", re.I)
FM_EVENTS: list[tuple[EventType, str]] = [
    (EventType.chakka_jam, r"chakka\s*jam"), (EventType.bandh, r"\bbandhs?\b"), (EventType.hartal, r"\bhartals?\b"),
    (EventType.strike, r"\bstrikes?\b"), (EventType.blockade, r"\bblockades?\b|rasta\s+roko"),
    (EventType.agitation, r"\bagitations?\b"), (EventType.flood, r"\bfloods?\b"), (EventType.landslide, r"\blandslides?\b"),
    (EventType.road_closure, r"road\s+closures?|closure\s+of\s+(?:roads?|highways?)"), (EventType.curfew, r"\bcurfews?\b"),
    (EventType.govt_detention, r"detention\s+by\s+(?:any\s+)?government|seizure\s+by"), (EventType.riot, r"\briots?\b"),
]
GENERAL_WORDS = [r"wars?", r"hostilit(?:y|ies)", r"epidemics?", r"pandemics?", r"fires?", r"acts?\s+of\s+god", r"civil\s+commotion",
                 r"civil\s+disturbances?", r"earthquakes?", r"lock[\s-]?outs?", r"natural\s+disasters?", r"government\s+orders?",
                 r"acts?\s+of\s+(?:the\s+)?government", r"cyclones?", r"quarantine", r"explosions?", r"beyond\s+(?:the\s+)?(?:reasonable\s+)?control"]
_DECIDED = re.compile(r"(?:decision|opinion|view)\s+of\s+(?:the\s+)?([A-Z][\w&.\- ]{1,40}?)\s+(?:shall\s+be|is|will\s+be)\s+(?:final|binding)|"
                      r"\b([A-Z][\w&.\- ]{1,40}?)\s+shall\s+be\s+the\s+(?:final|sole)\s+judge", re.I)


def _events(text: str) -> list[EventType]:
    return [et for et, pat in FM_EVENTS if re.search(pat, text, re.I)]


def _general(text: str) -> list[str]:
    out = []
    for pat in GENERAL_WORDS:
        m = re.search(r"\b" + pat + r"\b", text, re.I)
        if m:
            out.append(m.group(0).lower())
    return out


def _term(client_id: str, agreement_id: str, term_type: str, params: Any, page: int, clause_ref: str,
          verbatim: str, eff: date, conf: float) -> Term:
    tid = f"{agreement_id}-{term_type}-p{page}-{sha256_hex(norm_ws(verbatim) + term_type)[:6]}"
    return Term(term_id=tid, agreement_id=agreement_id, client_id=client_id, term_type=term_type, params=params,
                page=page, clause_ref=clause_ref, verbatim=verbatim, effective_from=eff, extraction_confidence=conf)


def deterministic_terms(client_id: str, agreement_id: str, page_text: str, page: int, eff: date) -> list[Term]:
    """Clause-pattern extractor for the common shapes in REFERENCE.md section 3."""
    out: list[Term] = []
    segs = sentences(page_text)
    fm_paras = {s.para for s in segs if _FM.search(s.para)}

    def add(tt: str, params: Any, seg_text: str, start: int, scope_text: str) -> None:
        conf = ANNEX_CONFIDENCE if _ANNEX.search(scope_text) else DET_CONFIDENCE
        try:
            out.append(_term(client_id, agreement_id, tt, params, page, clause_ref_at(page_text, start, page), seg_text, eff, conf))
        except Exception:  # noqa: BLE001 - a malformed proposal is dropped, never guessed
            pass

    for s in segs:
        t = s.text
        # penalty rule: percent per unit
        for m in _RATE_PCT.finditer(t):
            if re.search(r"(maximum|exceed|cap|ceiling|limited)\W*(?:\w+\W+){0,3}$", t[:m.start()], re.I):
                continue
            u = m.group("unit").lower()
            unit = (RateUnit.pct_per_vehicle_day if "vehicle" in u else
                    RateUnit.pct_per_week_or_part if "week" in u else RateUnit.pct_per_day)
            base = _base(m.group("base") or "") or _base(t)
            if base is None:
                continue
            ev = _event_for(t, s.para)
            add("penalty_rule", {"event": ev, "rate": m.group(1), "unit": unit, "base": base,
                                 "part_unit_counts_as_full": True if re.search(r"or\s+part", u + t[m.end():m.end() + 30], re.I) else None,
                                 "day_count": _day_count(t)}, t, s.start, s.para)
            break
        else:
            m = _RATE_FLAT.search(t)
            if m and re.search(r"penalt|liquidated|damages|recover|deduct|charge", t, re.I):
                u = m.group("unit").lower()
                unit = (RateUnit.per_quintal_day if ("quintal" in u or "qtl" in u) else
                        RateUnit.per_instance if u in ("instance", "occasion") else RateUnit.flat_per_day)
                add("penalty_rule", {"event": _event_for(t, s.para), "rate": m.group(1).replace(",", ""), "unit": unit,
                                     "base": Base.none, "day_count": _day_count(t)}, t, s.start, s.para)
        # cap
        m = _CAP.search(t)
        if m:
            if m.group(1):
                base = _base(m.group("base") or "") or _base(t)
                if base is not None:
                    scope = "per_vehicle" if re.search(r"each\s+vehicle|per\s+vehicle", t, re.I) else (
                        "contract" if base == Base.contract_value else None)
                    add("cap", {"value": m.group(1), "kind": "pct", "base": base, "scope": scope}, t, s.start, s.para)
            elif m.group("abs"):
                add("cap", {"value": m.group("abs").replace(",", ""), "kind": "abs", "base": Base.none}, t, s.start, s.para)
        # time limit for disputes / claims
        m = _TIME_LIMIT.search(t)
        if m and _DISPUTE_WORDS.search(t):
            frm = m.group(3).lower()
            cf = "deduction_date" if frm.startswith("deduction") else "contract_expiry" if frm == "expiry" else "received_date"
            add("time_limit", {"window_days": int(m.group(1)),
                               "day_count": DayCount.working if (m.group(2) or "").lower().startswith("working") else DayCount.calendar,
                               "counted_from": cf}, t, s.start, s.para)
            continue
        # notice
        if _NOTICE_WORDS.search(t):
            w = _WITHIN.search(t)
            in_fm = s.para in fm_paras or bool(_FM.search(t))
            if w:
                n, unit = float(w.group(1)), w.group(2).lower()
                hours = n if unit.startswith("h") else n * 24
                add("notice", {"deadline_hours": hours, "applies_to": "force_majeure" if in_fm else "delay",
                               "method": _method(t)}, t, s.start, s.para)
            elif re.search(r"\bimmediately\b", t, re.I):
                add("notice", {"immediate": True, "applies_to": "force_majeure" if in_fm else "delay",
                               "method": _method(t)}, t, s.start, s.para)
    # force majeure: one term per FM paragraph
    for para in fm_paras:
        start = page_text.find(para)
        incl, excl = [], []
        gen: list[str] = []
        for s in sentences(para):
            ev = _events(s.text)
            if _EXCL.search(s.text):
                # words after the exclusion cue are excluded; before it, included
                cue = _EXCL.search(s.text)
                before, after = s.text[:cue.start()], s.text[cue.start():]
                excl += _events(after) if _events(after) else ev
                incl += [e for e in _events(before) if e not in excl]
                gen += _general(before)
            else:
                incl += ev
                gen += _general(s.text)
        incl = [e for e in dict.fromkeys(incl) if e not in excl]
        dm = _DECIDED.search(para)
        decided = (dm.group(1) or dm.group(2)).strip() if dm else None
        add("force_majeure", {"events_named": incl, "general_words": list(dict.fromkeys(gen)),
                              "events_excluded": list(dict.fromkeys(excl)), "decided_by": decided},
            para, max(start, 0), para)
    return out


def _event_for(sentence: str, para: str) -> ReasonCode:
    from pdr.adapters.reason_map import table_reason
    return table_reason(sentence) or table_reason(para) or ReasonCode.other


def _day_count(t: str) -> Optional[DayCount]:
    if re.search(r"working\s+days?", t, re.I):
        return DayCount.working
    if re.search(r"calendar\s+days?", t, re.I):
        return DayCount.calendar
    return None


def _method(t: str) -> Optional[str]:
    for word in ("registered post", "speed post", "e-mail", "email", "fax", "in writing", "written"):
        if word in t.lower():
            return word
    return None


# --------------------------------------------------------------------------- missing documents

def missing_documents(pages: list[str], file_names: list[str]) -> list[tuple[str, int, str]]:
    """[(document label, page, referring sentence)] for annexure/schedule references not present in the set."""
    have = {norm_key(n) for n in file_names}
    out: list[tuple[str, int, str]] = []
    seen: set[str] = set()
    for n, txt in enumerate(pages, start=1):
        for s in sentences(txt):
            for m in _ANNEX.finditer(s.text):
                label = f"{m.group(1).title()} {m.group(2).upper()}"
                key = norm_key(label)
                if key in seen:
                    continue
                heading = re.compile(r"(?im)^\s*" + re.escape(m.group(1)) + r"[\s\-–]*" + re.escape(m.group(2)) + r"\b\s*(?:[:\-–]|$)")
                present = any(key in h for h in have) or any(heading.search(p) for p in pages)
                if not present:
                    seen.add(key)
                    out.append((label, n, s.text))
    return out


def draft_request(client_name: str, agreement_title: str, label: str, page: int, clause_ref: str, sentence: str) -> str:
    return (f"Request for missing document: {label}\n\nTo: {client_name}\n\n"
            f"The agreement \"{agreement_title}\" refers to {label} (clause {clause_ref}, page {page}): \"{sentence.strip()}\"\n"
            f"{label} was not in the documents we received. Please send a copy of {label} as in force for the "
            f"contract period, so that the deductions that depend on it can be checked.\n")


# --------------------------------------------------------------------------- model path

class _Proposed(BaseModel):
    term_type: str
    params: dict
    clause_ref: str = ""
    verbatim: str
    confidence: float = 0.5


class _ProposedList(BaseModel):
    terms: list[_Proposed] = []


def model_terms(client_id: str, agreement_id: str, page_text: str, page: int, eff: date, ctx: Any) -> list[Term]:
    if ctx is None:
        return []
    try:
        from pdr.guard import pseudonymise
        from pdr.llm import llm_json
        got = llm_json("extract_terms", pseudonymise(page_text, client_id), _ProposedList, ctx)
    except Exception:  # noqa: BLE001
        return []
    out = []
    for p in getattr(got, "terms", []) or []:
        try:
            out.append(_term(client_id, agreement_id, p.term_type, p.params, page, p.clause_ref or f"page {page}",
                             p.verbatim, eff, min(float(p.confidence), 0.9)))
        except Exception:  # noqa: BLE001 - schema-invalid params: dropped
            continue
    return out


def propose_terms(client_id: str, agreement_id: str, pages: list[str], eff: date, ctx: Any = None,
                  file_names: Optional[list[str]] = None, extra: Optional[list[Term]] = None) -> IntakeResult:
    """Model + deterministic proposals per page; each must have its verbatim on its page; annexure refs recorded."""
    res = IntakeResult()
    seen: set[tuple[str, str]] = set()
    proposals: list[Term] = list(extra or [])
    for n, txt in enumerate(pages, start=1):
        proposals += model_terms(client_id, agreement_id, txt, n, eff, ctx)
        proposals += deterministic_terms(client_id, agreement_id, txt, n, eff)
    for t in proposals:
        page_text = pages[t.page - 1] if 1 <= t.page <= len(pages) else ""
        if not verbatim_on_page(t.verbatim, page_text):
            res.rejected.append({"term_id": t.term_id, "term_type": t.term_type, "page": t.page,
                                 "verbatim": t.verbatim[:200], "why": "verbatim text not found on the stated page"})
            continue
        key = (t.term_type, norm_ws(t.verbatim))
        if key in seen:
            continue
        seen.add(key)
        if _ANNEX.search(t.verbatim):
            t = t.model_copy(update={"extraction_confidence": min(t.extraction_confidence, ANNEX_CONFIDENCE)})
        res.terms.append(t)
    for label, page, sentence in missing_documents(pages, file_names or []):
        res.missing_documents.append(f"{label} (referred to on page {page}: \"{sentence.strip()[:160]}\")")
    return res


_EFF = re.compile(r"(?:with\s+effect\s+from|w\.?\s*e\.?\s*f\.?|effective\s+(?:from|date)\s*:?)\s*(\d{1,2}[\-/.]\d{1,2}[\-/.]\d{2,4}|\d{4}-\d{2}-\d{2}|\d{1,2}\s+\w+\s+\d{4})", re.I)


def detect_effective_from(pages: list[str]) -> Optional[date]:
    from pdr.adapters.schema_map import parse_date
    for p in pages:
        m = _EFF.search(p)
        if m:
            d = parse_date(m.group(1))
            if d:
                return d
    return None


# --------------------------------------------------------------------------- workflow

def run_intake(client_id: str, files: list[str], ctx: Any, *, who: str = "intake", effective_from: Optional[date] = None,
               doc_type: str = "agreement", title: Optional[str] = None) -> list[tuple[Any, IntakeResult]]:
    """Stores files per client, extracts page text, proposes unconfirmed Terms, records missing documents."""
    import shutil
    from pathlib import Path

    from pdr.models import Action, Agreement, Client
    from pdr.codes import ActionKind
    from pdr.vault import data_dir

    from .pdftext import page_texts

    store = ctx.store
    client = store.get(Client, client_id)
    dest = data_dir() / "agreements" / norm_key(client_id)
    dest.mkdir(parents=True, exist_ok=True)
    names = [Path(f).name for f in files]
    results = []
    for f in files:
        src = Path(f)
        stored = dest / src.name
        if src.resolve() != stored.resolve():
            shutil.copy2(src, stored)
        pages = page_texts(stored)
        eff = effective_from or detect_effective_from(pages) or date(2000, 1, 1)
        ag_id = f"{norm_key(client_id)}-{norm_key(src.stem)[:24]}"
        res = propose_terms(client_id, ag_id, pages, eff, ctx, file_names=names)
        ag = Agreement(agreement_id=ag_id, client_id=client_id, doc_type=doc_type, title=title or src.stem,
                       file_ref=str(stored), effective_from=eff, completeness="missing",
                       missing_documents=res.missing_documents)
        for md in res.missing_documents:
            label = md.split(" (")[0]
            page = int(re.search(r"page (\d+)", md).group(1))
            sentence = md.split(": \"", 1)[1].rstrip("\")") if ": \"" in md else ""
            text = draft_request(client.legal_name if client else client_id, ag.title, label, page,
                                 clause_ref_at(pages[page - 1], max(pages[page - 1].find(sentence[:40]), 0), page), sentence)
            res.requests.append(text)
            req_dir = dest / "requests"
            req_dir.mkdir(exist_ok=True)
            body = req_dir / f"{ag_id}_{norm_key(label)}.txt"
            body.write_text(text, encoding="utf-8")
            cv = sha256_hex(text)
            store.put(Action(action_id=f"DOCREQ:{ag_id}:{norm_key(label)}:{cv[:12]}", kind=ActionKind.document_request,
                             client_id=client_id, content_version=cv, body_ref=str(body), required_roles=["kam"]))
        with store.tx():
            store.put(ag)
            for t in res.terms:
                if store.get(Term, t.term_id) is None:  # never overwrite a confirmed term
                    store.put(t)
        store.trail_append("intake", who, ag_id, {"file": src.name, "pages": len(pages), "proposed": len(res.terms),
                                                  "rejected": len(res.rejected), "missing_documents": res.missing_documents})
        results.append((ag, res))
    return results
