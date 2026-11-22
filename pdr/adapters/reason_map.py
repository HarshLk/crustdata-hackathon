"""Reason text -> ReasonCode (keyword table first, model only for unmapped text), client_calc, instruction scan."""
from __future__ import annotations

import re
from typing import Any, Optional

from pydantic import BaseModel

from pdr.codes import RateUnit, ReasonCode
from pdr.models import ClientCalc

from .schema_map import parse_amount

# Order matters: the first matching row wins (non-placement before late placement, etc.).
REASON_TABLE: list[tuple[ReasonCode, list[str]]] = [
    (ReasonCode.non_placement, [r"non[\s-]?placement", r"not\s+placed", r"vehicle\s+not\s+(?:provided|deployed|supplied)",
                                r"failure\s+to\s+place", r"non[\s-]?deployment", r"not\s+deployed", r"non[\s-]?supply\s+of\s+(?:truck|vehicle)"]),
    (ReasonCode.late_placement, [r"late\s+placement", r"delay(?:ed)?\s+(?:in\s+)?placement", r"placed\s+late",
                                 r"placement\s+delay", r"delay(?:ed)?\s+(?:in\s+)?(?:vehicle|truck)\s+placement"]),
    (ReasonCode.late_reporting, [r"late\s+reporting", r"reported\s+late", r"delay(?:ed)?\s+(?:in\s+)?reporting", r"reporting\s+delay"]),
    (ReasonCode.late_paperwork, [r"\bpod\b.*\b(?:late|delay)", r"(?:late|delay(?:ed)?)\s+(?:in\s+)?(?:submission|pod|documents?|paperwork)",
                                 r"paperwork", r"delivery\s+receipts?\s+not", r"\bwcm\b"]),
    (ReasonCode.detention_charge, [r"detention", r"demurrage", r"halting\s+charge", r"waiting\s+charge"]),
    (ReasonCode.shortage, [r"shortage", r"short\s+(?:delivery|receipt|supply|quantity)", r"\bshort\b"]),
    (ReasonCode.damage, [r"(?<!liquidated )damage", r"breakage", r"leakage", r"broken", r"spoil"]),
    (ReasonCode.late_delivery, [r"late\s+delivery", r"delay(?:ed)?\s+(?:in\s+)?delivery", r"delivery\s+delay", r"transit\s+delay",
                                r"delay(?:ed)?\s+(?:in\s+)?transit", r"liquidated\s+damages", r"\bld\b", r"late\s+arrival",
                                r"delayed\s+(?:arrival|supply|transport)", r"delay\s+in\s+(?:supply|transport)", r"\bdelay(?:ed)?\b"]),
]

_COMPILED = [(code, [re.compile(p, re.I) for p in pats]) for code, pats in REASON_TABLE]

# Imperative text aimed at the system (DESIGN 10). Flagged, never obeyed.
INSTRUCTION_PATTERNS = [
    r"approve\s+(?:all|every|this|these|the)", r"ignore\s+(?:all\s+)?(?:previous|prior|above|earlier|the\s+above)",
    r"mark\s+(?:as|it\s+as|them\s+as|all\s+as)\s+(?:sent|valid|approved|paid|resolved)", r"do\s+not\s+dispute",
    r"don'?t\s+dispute", r"disregard\s+(?:the|all|previous|prior|above)", r"you\s+(?:must|should)\s+(?:approve|accept|mark)",
    r"system\s*:", r"as\s+an\s+ai", r"override\s+(?:the|all)", r"auto[\s-]?approve", r"send\s+(?:it|this|all)\s+now",
    r"treat\s+(?:this|all|these)\s+as\s+valid", r"new\s+instructions?",
]
_INSTR = [re.compile(p, re.I) for p in INSTRUCTION_PATTERNS]


def has_instruction(text: Any) -> bool:
    s = str(text or "")
    return any(p.search(s) for p in _INSTR)


def table_reason(text: str) -> Optional[ReasonCode]:
    s = str(text or "")
    for code, pats in _COMPILED:
        if any(p.search(s) for p in pats):
            return code
    return None


class _ReasonOut(BaseModel):
    reason_code: str


def map_reason(text: str, ctx: Any = None) -> ReasonCode:
    """Keyword table; then llm_json('map_reason') for unmapped text; else other."""
    code = table_reason(text)
    if code is not None:
        return code
    if ctx is not None and str(text or "").strip():
        try:
            from pdr.llm import llm_json
            got = llm_json("map_reason", str(text), _ReasonOut, ctx)
            if got is not None:
                return ReasonCode(getattr(got, "reason_code"))
        except Exception:
            pass
    return ReasonCode.other


_RATE_PCT = re.compile(r"@?\s*(\d+(?:\.\d+)?)\s*%\s*(?:of\s+[\w\s]+?)?\s*(?:per|/|a)\s*(vehicle\s*(?:per\s*)?day|week|day)", re.I)
_RATE_PCT_LOOSE = re.compile(r"@\s*(\d+(?:\.\d+)?)\s*%", re.I)
_RATE_FLAT = re.compile(r"(?:rs\.?|inr|₹)\s*([\d,]+(?:\.\d+)?)\s*(?:/-)?\s*(?:per|/|a)\s*(quintal\s*(?:per\s*)?day|day|instance)", re.I)
_UNITS = re.compile(r"(\d+(?:\.\d+)?)\s*(days?|weeks?)\b", re.I)
_BASE = re.compile(r"(?:of|on)\s+(?:trip\s+cost|freight|value|invoice\s+value|contract\s+value)?\s*(?:of\s+)?(?:rs\.?|inr|₹)\s*([\d,]+(?:\.\d+)?)", re.I)


def parse_client_calc(text: str, cols: dict | None = None) -> Optional[ClientCalc]:
    """The client's stated calculation, from calc_* columns and/or the reason text (regex; values verbatim)."""
    cols = cols or {}
    out: dict[str, Any] = {}
    s = str(text or "")
    m = _RATE_PCT.search(s)
    if m:
        out["rate"] = parse_amount(m.group(1))
        u = m.group(2).lower()
        out["unit"] = (RateUnit.pct_per_vehicle_day if "vehicle" in u else
                       RateUnit.pct_per_week_or_part if "week" in u else RateUnit.pct_per_day)
    else:
        f = _RATE_FLAT.search(s)
        if f:
            out["rate"] = parse_amount(f.group(1))
            u = f.group(2).lower()
            out["unit"] = (RateUnit.per_quintal_day if "quintal" in u else
                           RateUnit.per_instance if "instance" in u else RateUnit.flat_per_day)
        else:
            lm = _RATE_PCT_LOOSE.search(s)
            if lm:
                out["rate"] = parse_amount(lm.group(1))
    um = _UNITS.search(s)
    if um:
        out["units_counted"] = parse_amount(um.group(1))
    bm = _BASE.search(s)
    if bm:
        out["base_amount"] = parse_amount(bm.group(1))
    for col, key in (("calc_rate", "rate"), ("calc_base_amount", "base_amount"), ("calc_units", "units_counted")):
        if cols.get(col) not in (None, ""):
            v = parse_amount(cols[col])
            if v is not None:
                out[key] = v
    if cols.get("calc_unit"):
        try:
            out["unit"] = RateUnit(str(cols["calc_unit"]).strip())
        except ValueError:
            pass
    from .schema_map import parse_dt
    for col, key in (("calc_from", "from_ts"), ("calc_to", "to_ts")):
        p = parse_dt(cols.get(col))
        if p:
            out[key] = p[0]
    out = {k: v for k, v in out.items() if v is not None}
    return ClientCalc(**out) if out else None
