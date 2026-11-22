"""Derived K5 fields (INTERFACES 4). Each is fn(verdict, evidence, settings, store) -> JSON value.

Register more with @derived("name"); settings.output.fields refers to them as "derived:<name>".
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Callable

from pdr.models import Evidence, Term, Verdict

DerivedFn = Callable[[Verdict, list[Evidence], Any, Any], Any]
REGISTRY: dict[str, DerivedFn] = {}


def derived(name: str) -> Callable[[DerivedFn], DerivedFn]:
    def deco(fn: DerivedFn) -> DerivedFn:
        REGISTRY[name] = fn
        return fn
    return deco


def _iso(x: Any) -> Any:
    return x.isoformat() if x is not None and hasattr(x, "isoformat") else x


def _jsonable(x: Any) -> Any:
    if isinstance(x, Decimal):
        return str(x)
    if hasattr(x, "isoformat"):
        return x.isoformat()
    if hasattr(x, "model_dump"):
        return x.model_dump(mode="json")
    return x


def cite(ev: Evidence, store: Any = None) -> dict:
    """One K5 citation (INTERFACES 5.1) for an Evidence row."""
    if ev.kind == "contract_term":
        term = store.get(Term, ev.term_id) if (store is not None and ev.term_id) else None
        doc = ev.doc_title
        if doc is None and term is not None and store is not None:
            from pdr.models import Agreement
            ag = store.get(Agreement, term.agreement_id)
            doc = ag.title if ag else None
        return {"kind": "contract_term", "doc": doc, "effective": _iso(term.effective_from) if term else None,
                "page": ev.page if ev.page is not None else (term.page if term else None),
                "clause": ev.clause_ref or (term.clause_ref if term else None),
                "text": ev.verbatim or (term.verbatim if term else None), "link": ev.link}
    if ev.kind == "internal_record":
        src = None
        if ev.ref is not None:
            src = ev.ref.file + (f":row {ev.ref.row}" if ev.ref.row is not None else "") + \
                (f":page {ev.ref.page}" if ev.ref.page is not None else "")
        return {"kind": "internal_record", "record": ev.record_type, "id": ev.record_id, "field": ev.field,
                "value": _jsonable(ev.value), "source": src, "link": ev.link}
    event = None
    if ev.event_from or ev.event_to:
        event = f"{_iso(ev.event_from) or ''}/{_iso(ev.event_to) or ''}"
    return {"kind": "external_source", "url": ev.url, "passage": ev.passage, "published": _iso(ev.published_at),
            "event": event, "place": ev.place_named, "retrieved": _iso(ev.retrieved_at), "link": ev.link}


@derived("citations")
def citations(v: Verdict, evidence: list[Evidence], settings: Any, store: Any = None) -> list[dict]:
    return [cite(e, store) for e in evidence]


def _external(evidence: list[Evidence]) -> list[Evidence]:
    return [e for e in evidence if e.kind in ("external_news", "external_official") and e.stance != "contradicts"]


@derived("sources_count")
def sources_count(v: Verdict, evidence: list[Evidence], settings: Any, store: Any = None) -> int:
    return len(_external(evidence))


@derived("confidence_label")
def confidence_label(v: Verdict, evidence: list[Evidence], settings: Any, store: Any = None) -> str:
    """From check outcomes and the audit, never from a model score."""
    if v.outcome == "cannot_verify":
        return "not decided"
    audit_ok = all(a.passed for a in v.audit)
    if not audit_ok or v.unresolved:
        return "low"
    contradicted = any(e.stance == "contradicts" for e in evidence)
    ext = _external(evidence)
    needs_min = int(getattr(getattr(settings, "evidence", None), "min_qualifying_sources", 1) or 1)
    if v.outcome == "disputable" and ext and (len(ext) <= needs_min or contradicted):
        return "medium"
    if v.flags:
        return "medium"
    return "high"


_ONES = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven", "Twelve",
         "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen"]
_TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]


def _below_1000(n: int) -> str:
    parts = []
    if n >= 100:
        parts.append(f"{_ONES[n // 100]} Hundred")
        n %= 100
    if n >= 20:
        parts.append(_TENS[n // 10] + (f" {_ONES[n % 10]}" if n % 10 else ""))
    elif n:
        parts.append(_ONES[n])
    return " ".join(parts)


def indian_words(n: int) -> str:
    """Integer in words with Indian grouping: crore, lakh, thousand."""
    if n == 0:
        return "Zero"
    parts = []
    crore, n = divmod(n, 10_000_000)
    if crore:
        parts.append(f"{indian_words(crore)} Crore")
    lakh, n = divmod(n, 100_000)
    if lakh:
        parts.append(f"{_below_1000(lakh)} Lakh")
    thousand, n = divmod(n, 1000)
    if thousand:
        parts.append(f"{_below_1000(thousand)} Thousand")
    if n:
        parts.append(_below_1000(n))
    return " ".join(parts)


def amount_words(amount: Decimal) -> str:
    amt = Decimal(amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    rupees, paise = int(amt), int((amt - int(amt)) * 100)
    text = f"Rupees {indian_words(rupees)}"
    if paise:
        text += f" and {indian_words(paise)} Paise"
    return text + " only"


@derived("amount_in_words")
def amount_in_words(v: Verdict, evidence: list[Evidence], settings: Any, store: Any = None) -> str:
    return amount_words(v.amount_disputable)


@derived("escalation_hint")
def escalation_hint(v: Verdict, evidence: list[Evidence], settings: Any, store: Any = None) -> str | None:
    if v.outcome != "disputable":
        return None
    esc = settings.escalation
    amt = Decimal(v.amount_disputable)
    if esc.arbitration_above_inr and amt > Decimal(esc.arbitration_above_inr):
        return "arbitration"
    if esc.legal_notice_above_inr and amt > Decimal(esc.legal_notice_above_inr):
        return "legal notice"
    return "dispute letter"


@derived("checks_summary")
def checks_summary(v: Verdict, evidence: list[Evidence], settings: Any, store: Any = None) -> str:
    g = ", ".join(str(x.value if hasattr(x, "value") else x) for x in v.grounds) or "none"
    r = ", ".join(str(x.value if hasattr(x, "value") else x) for x in v.reasons) or "none"
    return f"{len(v.checks_run)} checks run; grounds: {g}; reasons: {r}"


@derived("what_would_change")
def what_would_change(v: Verdict, evidence: list[Evidence], settings: Any, store: Any = None) -> list[str]:
    return list(v.what_would_change)


@derived("run_cost_inr")
def run_cost_inr(v: Verdict, evidence: list[Evidence], settings: Any, store: Any = None) -> str:
    """Ledger total for this deduction when the broker's ledger is available, else the verdict's stamp."""
    if store is not None:
        try:
            from pdr.broker import ledger
            rows = ledger.rows(store, dispute_key=v.dispute_key)
            if rows:
                return str(sum((Decimal(r.cost_inr) for r in rows), Decimal("0")).quantize(Decimal("0.01")))
        except Exception:  # noqa: BLE001 - ledger not built yet or unreadable: fall back
            pass
    return str(v.run_cost_inr)
