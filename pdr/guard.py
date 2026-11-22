"""Outbound guard (DESIGN 10, INTERFACES 3.7): nothing leaves the machine without check()."""
from __future__ import annotations

import re
from typing import Any

from pdr import vault
from pdr.contracts import GuardDestination, GuardViolation
from pdr.models import canonical_json

# Indian mobile (optionally +91 / 0 prefixed, spaces or dashes allowed) and driving licence numbers.
PHONE_RE = re.compile(r"(?<![\d\w])(?P<pre>\+91[\s-]?|91[\s-]?|0[\s-]?)?(?P<num>[6-9]\d{4}[\s-]?\d{5}|[6-9]\d{2}[\s-]?\d{3}[\s-]?\d{4})(?![\d\w])")
# A bare 10-digit number is a phone only with phone context (business references are often 10 digits too).
_PHONE_CONTEXT = re.compile(r"\b(mobile|mob|phone|ph|contact|driver|call|called|whatsapp|tel|telephone|cell|sms)\b", re.I)
_REF_LABEL = re.compile(r"\b(advice|lr|gr|invoice|inv|e-?way|ewb|bill|ref|reference|dispute|action|deduction|note|"
                        r"voucher|docket|trip|no\.?|number)\W*$", re.I)
REF_KEYS = {"advice_no", "lr_no", "invoice_no", "invoice_ref", "eway_bill_no", "dispute_key", "action_id",
            "client_deduction_no", "trip_refs", "trip_id", "deduction_id", "line_no", "content_version"}
LICENCE_RE = re.compile(r"\b[A-Z]{2}[\s-]?\d{2}[\s-]?(?:19|20)\d{2}[\s-]?\d{7}\b")
_AMOUNTISH = re.compile(r"(?:₹|\bRs\.?|\bINR\b)|\d{1,3}(?:,\d{2,3})+|\d{5,}")
_ID_LIKE = re.compile(r"\b[A-Z]{1,5}[-/]?\d{3,}\b|\b\d+[-/]\d+[-/]?\d*\b", re.I)
_RELATIONSHIP_WORDS = re.compile(r"\b(penalty|deduction|dispute|invoice|advice|LR|lorry receipt|debit|amount|trip)\b", re.I)
_QUERY_KEYS = ("query", "q", "keyword", "keywords", "search")
MAX_QUERY_WORDS = 10
MAX_QUERY_CHARS = 120
MAX_NAME_WORDS = 10


def _text(payload: Any) -> str:
    if isinstance(payload, str):
        return payload
    if isinstance(payload, bytes):
        return payload.decode("utf-8", "replace")
    try:
        return canonical_json(payload)
    except TypeError:
        return str(payload)


def _norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def _vault_hits(text: str, client_id: str | None) -> list[str]:
    """Which kinds of vault value occur in the text (all clients scanned when client_id is None)."""
    hits: list[str] = []
    low = _norm_ws(text)
    digits_runs = re.sub(r"[\s\-+()]", "", text)
    alnum_up = re.sub(r"[^A-Z0-9]", "", text.upper())
    rows = vault.all_drivers(None)  # superset: scan every client's values
    for r in rows:
        if r.get("name") and len(r["name"].strip()) >= 3 and _norm_ws(r["name"]) in low:
            hits.append("vault_name")
        p = vault.norm_phone(r.get("phone"))
        if p and len(p) >= 8 and p in digits_runs:
            hits.append("vault_phone")
        lic = re.sub(r"[^A-Z0-9]", "", (r.get("licence") or "").upper())
        if lic and len(lic) >= 6 and lic in alnum_up:
            hits.append("vault_licence")
    return hits


def _payload_refs(payload: Any, out: set[str] | None = None) -> set[str]:
    """Digit runs of business references named in the payload (advice_no, lr_no, dispute_key, action_id, ...)."""
    out = set() if out is None else out
    if isinstance(payload, dict):
        for k, v in payload.items():
            if str(k) in REF_KEYS:
                for x in (v if isinstance(v, (list, tuple)) else [v]):
                    out.update(re.findall(r"\d{8,}", re.sub(r"[\s-]", "", str(x))))
            else:
                _payload_refs(v, out)
    elif isinstance(payload, (list, tuple)):
        for v in payload:
            _payload_refs(v, out)
    else:
        try:  # pydantic models
            _payload_refs(payload.model_dump(mode="json"), out)
        except AttributeError:
            pass
    return out


def phone_matches(text: str, allow: set[str] | None = None) -> list[re.Match]:
    """Phone-number matches that are not known references and carry phone context (+91 / 0 prefix or a phone word)."""
    allow_d = {re.sub(r"\D", "", a)[-10:] for a in (allow or set()) if re.sub(r"\D", "", a)}
    out = []
    for m in PHONE_RE.finditer(text):
        digits = re.sub(r"\D", "", m.group("num"))
        if digits in allow_d or any(digits in a for a in allow_d):
            continue
        pre = m.group("pre") or ""
        strong_pre = pre.startswith("+") or pre.startswith("0") or (pre.startswith("91") and pre[2:3] in (" ", "-"))
        before, after = text[max(0, m.start() - 20):m.start()], text[m.end():m.end() + 20]
        if not strong_pre and _REF_LABEL.search(before) and not _PHONE_CONTEXT.search(before[-12:]):
            continue
        if strong_pre or _PHONE_CONTEXT.search(before) or _PHONE_CONTEXT.search(after):
            out.append(m)
    return out


def _pii_patterns(text: str, allow: set[str] | None = None) -> list[str]:
    out = []
    if phone_matches(text, allow):
        out.append("phone_pattern")
    if LICENCE_RE.search(text.upper()):
        out.append("licence_pattern")
    return out


def _query_texts(payload: Any) -> list[str]:
    if isinstance(payload, str):
        return [payload]
    if isinstance(payload, dict):
        found = [str(payload[k]) for k in _QUERY_KEYS if k in payload and payload[k] is not None]
        return found or [_text(payload)]
    if isinstance(payload, (list, tuple)):
        return [str(x) for x in payload]
    return [_text(payload)]


def _names(payload: Any) -> list[str]:
    if isinstance(payload, str):
        return [payload]
    if isinstance(payload, dict):
        for k in ("names", "name", "company_name", "query"):
            if k in payload:
                v = payload[k]
                return [str(x) for x in v] if isinstance(v, (list, tuple)) else [str(v)]
        return [_text(payload)]
    if isinstance(payload, (list, tuple)):
        return [str(x) for x in payload]
    return [_text(payload)]


def _check_search(payload: Any) -> list[str]:
    problems = []
    for q in _query_texts(payload):
        if len(q) > MAX_QUERY_CHARS or len(q.split()) > MAX_QUERY_WORDS:
            problems.append("search query too long")
        if _AMOUNTISH.search(q):
            problems.append("search query has an amount-like number")
        if _ID_LIKE.search(q):
            problems.append("search query has an id-like reference")
        if _RELATIONSHIP_WORDS.search(q):
            problems.append("search query mentions the commercial relationship")
    return problems


def _check_identify(payload: Any) -> list[str]:
    problems = []
    for n in _names(payload):
        if len(n.split()) > MAX_NAME_WORDS or len(n) > 100:
            problems.append("identify payload is not a bare company name")
        if _AMOUNTISH.search(n) or re.search(r"\d{4,}", n) or _ID_LIKE.search(n):
            problems.append("identify payload has numbers or ids")
        if _RELATIONSHIP_WORDS.search(n) or "@" in n:
            problems.append("identify payload mentions the relationship")
    return problems


def _check_urls(payload: Any) -> list[str]:
    if isinstance(payload, dict):
        urls = [str(u) for u in (payload.get("urls") or ([payload["url"]] if payload.get("url") else []))]
    else:
        urls = _names(payload)
    return [f"not a public URL: {u[:60]}" for u in urls if not re.match(r"^https?://", u.strip())]


def check(destination: GuardDestination, payload: Any, client_id: str | None = None,
          allow_refs: Any = None) -> None:
    """Raises GuardViolation when the payload may not go to destination.

    allow_refs: business references (advice/LR/invoice/e-way bill numbers, dispute keys, action ids) that may look
    like phone numbers; those named in the payload under REF_KEYS are allowed automatically. Vault values never are.
    """
    text = _text(payload)
    allow = _payload_refs(payload) | {str(a) for a in (allow_refs or [])}
    problems = [f"contains a {h.replace('_', ' ')}" for h in _vault_hits(text, client_id)]
    if destination == "crustdata_search":
        problems += _check_search(payload) + _pii_patterns(text, allow)
    elif destination == "crustdata_identify":
        problems += _check_identify(payload) + _pii_patterns(text, allow)
    elif destination in ("crustdata_fetch", "web"):
        problems += _check_urls(payload) + _pii_patterns(text, allow)
    elif destination in ("llm", "k7"):
        problems += _pii_patterns(text, allow)
    else:
        problems.append(f"unknown destination {destination!r}")
    if problems:
        # never echo the offending value
        raise GuardViolation(f"{destination}: " + "; ".join(sorted(set(problems))))


def pseudonymise(text: str, client_id: str | None = None) -> str:
    """Replaces vault values with driver_ref, and any other phone or licence number with a placeholder."""
    if not text:
        return text
    out = text
    for r in vault.all_drivers(None):
        ref = r["driver_ref"]
        if r.get("name") and len(r["name"].strip()) >= 3:
            parts = [re.escape(p) for p in r["name"].split()]
            out = re.sub(r"\s+".join(parts), f"[driver {ref}]", out, flags=re.I)
        p = vault.norm_phone(r.get("phone"))
        if p and len(p) >= 8:
            pat = r"(?:\+?91[\s-]?|0)?" + r"[\s-]?".join(re.escape(c) for c in p)
            out = re.sub(pat, f"[driver {ref}]", out)
        lic = re.sub(r"[^A-Z0-9]", "", (r.get("licence") or "").upper())
        if lic and len(lic) >= 6:
            pat = r"[\s-]?".join(re.escape(c) for c in lic)
            out = re.sub(pat, f"[driver {ref}]", out, flags=re.I)
    for m in reversed(phone_matches(out)):
        out = out[:m.start()] + "[phone]" + out[m.end():]
    out = LICENCE_RE.sub("[licence]", out)
    return out


def vault_read(driver_ref: str, who: str, purpose: str) -> dict:
    """Logs to pii_access_log."""
    return vault.vault_read(driver_ref, who, purpose)


__all__ = ["check", "pseudonymise", "vault_read", "GuardViolation"]
