"""Dispute letter rendered only from the Verdict, its Evidence and Terms (D-14). No model text."""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

import jinja2

from pdr.codes import Ground
from pdr.models import Agreement, Client, Deduction, Evidence, Term, Verdict, sha256_hex

TEMPLATES = Path(__file__).parent / "templates"

# One sentence per ground, written as evidence, not accusation.
GROUND_SENTENCES: dict[str, str] = {
    Ground.NO_CONTRACTUAL_BASIS: "The agreement's penalty schedule contains no provision for a deduction of this kind.",
    Ground.WRONG_RATE: "The rate applied differs from the rate in the agreement in force on the trip date.",
    Ground.WRONG_BASE: "The penalty appears to have been calculated on a base other than the one the agreement specifies.",
    Ground.OVER_CAP: "The amount deducted exceeds the maximum the agreement sets for this penalty.",
    Ground.DUPLICATE: "The same trip and event appear to have been penalised already on another payment advice.",
    Ground.WRONG_DAY_COUNT: "The number of days counted differs from the day count the agreement specifies.",
    Ground.NO_DELAY: "Our records show the trip was completed within the time the agreement allows.",
    Ground.WRONG_START_POINT: "The delay appears to have been measured from a point other than the one the agreement defines.",
    Ground.CLIENT_CAUSED: "Our records show the delay arose while the vehicle was at the buyer's site, which the agreement places with the buyer.",
    Ground.REGULATORY_HOLD: "Our records show the vehicle was held by a government authority, a delay the agreement excuses.",
    Ground.FORCE_MAJEURE: "Published reports and our trip records show a disruption on the route during the trip of a kind the agreement's force-majeure clause covers.",
}

PLACEHOLDERS = ("[[SENT_DATE]]", "[[RESPOND_BY]]", "[[APPROVERS]]")


def inr(value: Any) -> str:
    """Indian digit grouping with two decimals: 123456.5 -> 1,23,456.50."""
    if value is None:
        return "-"
    q = Decimal(str(value)).quantize(Decimal("0.01"))
    sign = "-" if q < 0 else ""
    whole, frac = f"{abs(q):.2f}".split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    return f"{sign}{whole}.{frac}"


def env() -> jinja2.Environment:
    e = jinja2.Environment(loader=jinja2.FileSystemLoader(str(TEMPLATES)), autoescape=jinja2.select_autoescape(["html.j2", "html"], default_for_string=False),
                           undefined=jinja2.StrictUndefined, keep_trailing_newline=True)
    e.filters["inr"] = inr
    return e


def _ts(d: datetime | None) -> str:
    return d.isoformat(timespec="minutes") if d else "not stated"


def source_ref_text(ref: Any) -> str:
    if ref is None:
        return "-"
    parts = [ref.file]
    if ref.sheet:
        parts.append(f"sheet {ref.sheet}")
    if ref.row is not None:
        parts.append(f"row {ref.row}")
    if ref.page is not None:
        parts.append(f"page {ref.page}")
    if ref.field:
        parts.append(f"field {ref.field}")
    return ", ".join(parts)


def gather(store: Any, verdict: Verdict) -> dict:
    """Everything the letter and case page need, read from the store."""
    ded = store.get(Deduction, verdict.dispute_key)
    client = store.get(Client, ded.client_id) if ded else None
    evidence = [e for e in (store.get(Evidence, i) for i in verdict.evidence_ids) if e is not None]
    terms = [t for t in (store.get(Term, i) for i in verdict.terms_applied) if t is not None]
    agreements = {t.agreement_id: store.get(Agreement, t.agreement_id) for t in terms}
    return {"verdict": verdict, "deduction": ded, "client": client, "evidence": evidence,
            "terms": terms, "agreements": agreements}


def clauses(bundle: dict) -> list[dict]:
    """Contract terms applied, from Terms and contract_term Evidence, without duplicates."""
    out, seen = [], set()
    for t in bundle["terms"]:
        ag = bundle["agreements"].get(t.agreement_id)
        doc = ag.title if ag else t.agreement_id
        if ag is not None and ag.is_stand_in:
            doc += " (stand-in terms)"
        out.append({"doc": doc, "page": t.page, "clause": t.clause_ref, "verbatim": t.verbatim,
                    "effective": str(t.effective_from), "term_id": t.term_id, "stand_in": bool(ag and ag.is_stand_in)})
        seen.add(t.term_id)
    for e in bundle["evidence"]:
        if e.kind == "contract_term" and (e.term_id is None or e.term_id not in seen):
            out.append({"doc": e.doc_title or "-", "page": e.page, "clause": e.clause_ref, "verbatim": e.verbatim or "",
                        "effective": "", "term_id": e.term_id, "stand_in": False, "link": e.link})
            if e.term_id:
                seen.add(e.term_id)
    return out


def records(evidence: Iterable[Evidence]) -> list[dict]:
    return [{"record": f"{e.record_type or 'record'} {e.record_id or ''}".strip(), "field": e.field or "-",
             "value": e.value if e.value is not None else "-", "timestamp": _ts(e.timestamp) if e.timestamp else "",
             "source": source_ref_text(e.ref), "link": e.link, "evidence_id": e.evidence_id}
            for e in evidence if e.kind == "internal_record"]


def sources(evidence: Iterable[Evidence]) -> list[dict]:
    return [{"url": e.url or "-", "passage": e.passage or "", "published": _ts(e.published_at),
             "retrieved": _ts(e.retrieved_at), "place": e.place_named, "link": e.link, "stance": e.stance,
             "event": f"{e.event_from or '?'} to {e.event_to or '?'}" if (e.event_from or e.event_to) else "",
             "snapshot": e.content_hash, "fetch_status": e.fetch_status, "audit": e.audit, "evidence_id": e.evidence_id}
            for e in evidence if e.kind in ("external_news", "external_official")]


def render_letter(verdict: Verdict, store: Any, settings: Any) -> str:
    """Letter markdown with send-time placeholders; its sha256 is the content version."""
    b = gather(store, verdict)
    ded = b["deduction"]
    if ded is None:
        raise ValueError(f"no deduction stored for {verdict.dispute_key}")
    support = [e for e in b["evidence"] if e.kind not in ("external_news", "external_official") or e.stance != "contradicts"]
    ctx = {
        "ref": verdict.dispute_key,
        "client_name": b["client"].legal_name if b["client"] else ded.client_name_raw,
        "deductions": [ded],
        "grounds": [{"code": g, "sentence": GROUND_SENTENCES.get(Ground(g), str(g))} for g in verdict.grounds],
        "clauses": clauses(b),
        "records": records(support),
        "sources": sources(support),
        "recomputation": verdict.recomputation,
        "amount_deducted": verdict.amount_deducted,
        "recomputed": verdict.recomputed_penalty if verdict.recomputed_penalty is not None else verdict.amount_deducted - verdict.amount_disputable,
        "amount_claimed": verdict.amount_disputable,
        "response_days": settings.letter.response_days,
    }
    return env().get_template("letter.md.j2").render(**ctx)


def content_version(body_md: str) -> str:
    return sha256_hex(body_md)


def finalize(body_md: str, approvals: list[Any], sent_at: datetime, response_days: int) -> str:
    """Fill send-time placeholders: date, respond-by date, approver names and roles."""
    names = "\n".join(f"{a.approver}, {a.role_required}" for a in approvals) or "(no approver)"
    return (body_md.replace("[[SENT_DATE]]", sent_at.date().isoformat())
            .replace("[[RESPOND_BY]]", (sent_at + timedelta(days=response_days)).date().isoformat())
            .replace("[[APPROVERS]]", names))


def render_simple(kind: str, **fields: Any) -> str:
    """Plain templates for document requests and force-majeure notices."""
    if kind == "document_request":
        lines = [f"# Request for documents {fields['ref']}", "", f"**To:** {fields['to']}", "**Date:** [[SENT_DATE]]", "",
                 "Dear Sir or Madam,", "", "To complete our review of the deduction referenced above we request the following:", ""]
        lines += [f"- {item}" for item in fields["items"]]
        lines += ["", "Please send them by [[RESPOND_BY]].", "", "Yours faithfully,", "", "[[APPROVERS]]", ""]
    elif kind == "fm_notice":
        lines = [f"# Notice of force-majeure event {fields['ref']}", "", f"**To:** {fields['to']}", "**Date:** [[SENT_DATE]]", "",
                 "Dear Sir or Madam,", "", f"Under {fields['clause']} we give notice that the following event is affecting performance:", "",
                 f"- {fields['event']}", "", "Yours faithfully,", "", "[[APPROVERS]]", ""]
    else:
        raise ValueError(f"no simple template for {kind}")
    return "\n".join(lines)
