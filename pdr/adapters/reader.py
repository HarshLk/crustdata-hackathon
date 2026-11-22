"""Request reader (D-26): load a request bundle into the store, and classify an instruction sentence."""
from __future__ import annotations

import calendar
import logging
import re
from datetime import date
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel

from pdr.codes import RequestType
from pdr.models import Client, Deduction, Request, norm_key

from .k1_clients import k1_load_clients, load_lanes
from .k2_agreements import load_terms_file
from .k3_trips import k3_load_trips
from .k4_deductions import load_deductions_ex
from .schema_map import parse_date

log = logging.getLogger("pdr.adapters.reader")
DEDUCTION_FILES = ["deductions.csv", "deductions.xlsx", "deductions.tsv", "deductions.txt", "deductions.pdf",
                   "deductions.eml", "deductions.json"]
UNSUPPORTED_MESSAGE = ("Unsupported request. This service checks penalty deductions a client made against a "
                       "transporter's invoices (is each one valid, disputable or cannot be verified, with cited "
                       "evidence), takes in a client's agreement to extract its penalty terms, and shows status. "
                       "To check deductions, send the deductions (sheet, PDF or pasted email) with the trips and "
                       "the client's agreement.")


def _as_path(x: Any) -> Optional[Path]:
    if isinstance(x, Path):
        return x
    if isinstance(x, str) and "\n" not in x and len(x) < 4096:
        try:
            p = Path(x)
            return p if p.exists() else None
        except OSError:
            return None
    return None


def deductions_file(folder: Path) -> Optional[Path]:
    for n in DEDUCTION_FILES:
        if (folder / n).exists():
            return folder / n
    return None


def load_request_ex(path_or_text: Any, ctx: Any) -> tuple[list[Deduction], dict]:
    """Loads K1 -> K2 confirmed terms -> K3 -> K4 into ctx.store. Returns (deductions, summary)."""
    store = ctx.store
    settings = getattr(ctx, "settings", None)
    summary: dict[str, Any] = {"clients": 0, "agreements": 0, "terms": 0, "trips": 0, "deductions": 0,
                               "resubmissions": 0, "problems": []}
    p = _as_path(path_or_text)
    default_client = None
    if p is not None and p.is_dir():
        clients = k1_load_clients(p, settings)
        for c in clients:
            store.put(c)
        summary["clients"] = len(clients)
        known = store.find(Client)
        if len(known) == 1:
            default_client = known[0].client_id
        lanes = load_lanes(p)
        ags, terms = load_terms_file(p)
        for a in ags:
            store.put(a)
        for t in terms:
            store.put(t)
        summary["agreements"], summary["terms"] = len(ags), len(terms)
        trips = k3_load_trips(p, settings, ctx, lanes=lanes, clients=known)
        for t in trips:
            store.put(t)
        summary["trips"] = len(trips)
        src: Any = deductions_file(p)
        if src is None:
            summary["problems"].append("no deductions file in bundle")
            return [], summary
    else:
        src = p if p is not None else str(path_or_text)
        known = store.find(Client)
        default_client = known[0].client_id if len(known) == 1 else None
    deds, problems = load_deductions_ex(src, settings, ctx, default_client=default_client)
    summary["problems"] += problems
    out: list[Deduction] = []
    for d in deds:
        if store.get(Deduction, d.dispute_key) is not None:
            summary["resubmissions"] += 1  # same dispute_key again: nothing new created (DESIGN 5.7)
        else:
            store.put(d)
        out.append(d)
    summary["deductions"] = len(out)
    log.info("load_request: %s", {k: v for k, v in summary.items() if k != "problems"})
    return out, summary


def load_request(path_or_text: Any, ctx: Any) -> list[Deduction]:
    """Stores clients/terms/trips/deductions found at the path (or in pasted text) and returns the deductions."""
    return load_request_ex(path_or_text, ctx)[0]


# --------------------------------------------------------------------------- request interpreter

_RULES: list[tuple[RequestType, re.Pattern]] = [
    (RequestType.intake_agreement, re.compile(
        r"\b(intake|take\s+in|load|add|upload|read|ingest|extract|import|register|onboard)\b.{0,60}"
        r"\b(agreement|contract|rate\s*card|tender|terms|sla|bid|amendment)s?\b", re.I | re.S)),
    (RequestType.status, re.compile(
        r"^\s*(status|progress)\b|\b(status|progress)\s+(of|for|on)\b|\bwhat(?:'s| is)\s+the\s+status\b|"
        r"\bhow\s+many\s+(disputes?|deductions?|verdicts?)\b", re.I)),
    (RequestType.check_deductions, re.compile(
        r"\b(check|verify|review|validate|audit|process|examine|assess|look\s+(?:at|into)|go\s+through|dispute|"
        r"contest|challenge|recover|evaluate|analy[sz]e)\b.{0,80}"
        r"\b(deductions?|penalt(?:y|ies)|advices?|debit\s+notes?|short\s+payments?|ld|liquidated\s+damages|"
        r"recoveries|withheld|this|these|them|it)\b", re.I | re.S)),
]
_DEDUCTION_WORDS = re.compile(r"\b(deduction|penalt|debit\s+note|payment\s+advice)", re.I)


class _Classified(BaseModel):
    request_type: str


def classify(text: str, ctx: Any = None) -> RequestType:
    """Keyword rules first; model if available; anything else is unsupported (never a guess)."""
    t = (text or "").strip()
    if not t:
        return RequestType.unsupported
    for rt, rx in _RULES:
        if rx.search(t):
            return rt
    if ctx is not None:
        try:
            from pdr.llm import llm_json
            got = llm_json("classify_request", t, _Classified, ctx)  # type: ignore[arg-type]
            if got is not None:
                rt = RequestType(getattr(got, "request_type"))
                if rt in (RequestType.check_deductions, RequestType.intake_agreement, RequestType.status):
                    return rt
        except Exception:
            pass
    return RequestType.unsupported


_MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m} | \
          {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}


def _period(text: str) -> Optional[tuple[date, date]]:
    m = re.search(r"\bfrom\s+(.+?)\s+(?:to|till|until|-)\s+(.+?)(?:[.,;]|$)", text, re.I)
    if m:
        a, b = parse_date(m.group(1).strip()), parse_date(m.group(2).strip())
        if a and b:
            return a, b
    m = re.search(r"\b(" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\.?\s+(\d{4})\b", text, re.I)
    if m:
        mo, yr = _MONTHS[m.group(1).lower()], int(m.group(2))
        return date(yr, mo, 1), date(yr, mo, calendar.monthrange(yr, mo)[1])
    return None


def _client_hint(text: str, ctx: Any) -> Optional[str]:
    store = getattr(ctx, "store", None)
    if store is None:
        return None
    nt = norm_key(text)
    hits = {c.client_id for c in store.find(Client)
            for n in [c.legal_name, *c.aliases] if len(norm_key(n)) >= 4 and norm_key(n) in nt}
    return hits.pop() if len(hits) == 1 else None


def read_request(path_or_text: Any, ctx: Any) -> Request:
    """Classifies the request: a bundle with no instruction and a deductions file is check_deductions."""
    p = _as_path(path_or_text)
    files: list[str] = []
    instruction = ""
    has_deductions = False
    if p is not None and p.is_dir():
        files = sorted(str(f.relative_to(p)) for f in p.rglob("*") if f.is_file())
        if (p / "request.txt").exists():
            instruction = (p / "request.txt").read_text(encoding="utf-8").strip()
        has_deductions = deductions_file(p) is not None
    elif p is not None:
        files = [str(p)]
        has_deductions = True
    else:
        instruction = str(path_or_text or "").strip()
        # a pasted deduction email (labelled amount + advice/deduction no) is itself a check request
        has_deductions = bool(re.search(r"(?im)^\s*(?:amount|deduction amount|penalty)\s*[:=]", instruction)) and \
            bool(_DEDUCTION_WORDS.search(instruction) or re.search(r"(?i)\badvice\b", instruction))
    if instruction and not (p is None and has_deductions):
        rt = classify(instruction, ctx)
    else:
        rt = RequestType.check_deductions if has_deductions else RequestType.unsupported
    return Request(request_type=rt, client_hint=_client_hint(instruction, ctx) if instruction else None,
                   period=_period(instruction) if instruction else None, files=files, raw_text=instruction)


def describe(req: Request) -> str:
    """One line for the operator; the unsupported answer states what the service does."""
    if req.request_type == RequestType.unsupported:
        return UNSUPPORTED_MESSAGE
    extra = f" client={req.client_hint}" if req.client_hint else ""
    extra += f" period={req.period[0]}..{req.period[1]}" if req.period else ""
    return f"{req.request_type.value}{extra} files={len(req.files)}"
