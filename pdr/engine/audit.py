"""The auditor (DESIGN 8.5, D-23): re-verify every cited fact; it can only downgrade.

audit(verdict, evidence, ctx) checks each Evidence whose evidence_id is in verdict.evidence_ids
(all of `evidence` if that list is empty). Everything is looked up in ctx.store. The trip date is
penalty.trip_date(deduction, trip), with the deduction = store.get(Deduction, verdict.dispute_key) and
the trip = store.get(Trip, ref), else a unique store.find(Trip, lr_no|invoice_no|eway_bill_no=ref), for ref in trip_refs.

contract_term (rule "contract_term")
  term_id set: store.get(Term, term_id) exists, status == "confirmed", in force on the trip date
    (effective_from <= date <= effective_to), and Evidence.verbatim equals Term.verbatim (whitespace,
    dashes and case normalised); Evidence.page (if set) == Term.page; Evidence.clause_ref (if set) == Term.clause_ref.
    evidence_id starting "term-superseded-": same, except the term must NOT be in force on the trip date.
  term_id None: evidence_id is "agr-<agreement_id>"; store.get(Agreement, id) exists with
    completeness == "complete" and no missing_documents.
internal_record (rule "internal_record")
  record_type "deduction": store.get(Deduction, record_id) exists and its advice_no (or
    client_deduction_no) appears in str(Evidence.value).
  otherwise: store.get(Trip, record_id.split("#")[0]) exists; wanted = Evidence.timestamp, else Evidence.value.
    If Evidence.field is a Trip attribute: a list field (reporting_at, arrival_at, halts, holds, notices,
    assertions, ...) must hold an item whose value/start/sent_at/held_from/at equals wanted (and whose
    source equals record_type when record_type is a TsSource value); a scalar field must equal wanted or value.
    If field is not a Trip attribute (e.g. "start/end"), Evidence.ref is required: some item in any list field
    must have ref.file, ref.row and ref.page equal to Evidence.ref's, and a time attribute equal to wanted.
external_news / external_official (rules external_snapshot, external_passage, external_date, external_url)
  snap = store.snapshot_get(Evidence.content_hash) must exist and sha256_hex(snap["text"]) == content_hash;
  Evidence.passage (normalised) is a substring of snap["text"] (normalised);
  Evidence.published_at is set, and if snap["html_path"] is an existing file, pdr.evidence.pubdate.published_at(html)
  re-parses to exactly that value; Evidence.url is not a google.com redirect (pdr.evidence.redirect.is_redirect_link).
  With with_refuter: pdr.evidence.refute(ev, [place_named or passage], ctx); any False fails ("refuter"),
  None (did not finish) is recorded as passed with a note, keeping the mechanical result.
Any rule that raises fails closed (rule "audit_error").
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.parse import urlparse

from pdr.codes import CheckStatus, Reason
from pdr.engine import penalty as P
from pdr.models import (Agreement, AuditFinding, CheckResult, Deduction, Evidence, Term, Trip, Verdict,
                        sha256_hex)

SUPERSEDED = "term-superseded-"  # a superseded version cited for comparison (case 13): must NOT be in force
NATURAL = {"contract_term": Reason.TERMS_UNCONFIRMED, "internal_record": Reason.RECORDS_CONFLICT,
           "external_source": Reason.EVIDENCE_PARTIAL}


def _n(s: Optional[str]) -> str:
    s = re.sub(r"[‐-―−]", "-", s or "")
    return re.sub(r"\s+", " ", s).strip().lower()


def _f(ev: Evidence, rule: str, ok: bool, detail: str = "") -> AuditFinding:
    return AuditFinding(citation_evidence_id=ev.evidence_id, rule=rule, passed=bool(ok), detail=detail)


def _subject(verdict: Verdict, store) -> tuple[Optional[Deduction], Optional[Trip]]:
    ded = store.get(Deduction, verdict.dispute_key)
    trip = None
    if ded is not None:
        for ref in ded.trip_refs:
            trip = store.get(Trip, ref)
            if trip is None:
                for fld in ("lr_no", "invoice_no", "eway_bill_no"):
                    found = store.find(Trip, **{fld: ref})
                    if len(found) == 1:
                        trip = found[0]
                        break
            if trip is not None:
                break
    return ded, trip


# ----------------------------------------------------------------------------- the three rules

def check_term(ev: Evidence, store, on: Optional[Any]) -> AuditFinding:
    """Term confirmed, in force on the trip date, and its verbatim text unchanged in the store."""
    if not ev.term_id:
        ag = store.get(Agreement, ev.evidence_id.removeprefix("agr-"))
        ok = ag is not None and ag.completeness == "complete" and not ag.missing_documents
        return _f(ev, "contract_term", ok, "agreement set complete" if ok else "agreement set not marked complete")
    t = store.get(Term, ev.term_id)
    if t is None:
        return _f(ev, "contract_term", False, f"term {ev.term_id} no longer stored")
    if t.status != "confirmed":
        return _f(ev, "contract_term", False, f"term {ev.term_id} is {t.status}")
    superseded = ev.evidence_id.startswith(SUPERSEDED)
    if on is not None and P.in_force(t, on) == superseded:
        return _f(ev, "contract_term", False, f"term {ev.term_id} {'is' if superseded else 'not'} in force on {on}")
    if _n(t.verbatim) != _n(ev.verbatim) or (ev.page is not None and t.page != ev.page) or \
            (ev.clause_ref and t.clause_ref != ev.clause_ref):
        return _f(ev, "contract_term", False, "cited verbatim text, page or clause differs from the stored term")
    return _f(ev, "contract_term", True, "confirmed, in force, verbatim matches")


def _same(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return False
    if isinstance(a, datetime) and isinstance(b, datetime):
        return a == b
    return str(a) == str(b)


def check_record(ev: Evidence, store) -> AuditFinding:
    """The cited record still exists and its field still holds the cited value."""
    if ev.record_type == "deduction":
        d = store.get(Deduction, ev.record_id)
        ok = d is not None and (d.advice_no or d.client_deduction_no or "") in str(ev.value or "")
        return _f(ev, "internal_record", ok, "advice on file" if ok else "deduction record missing or changed")
    trip = store.get(Trip, str(ev.record_id).split("#")[0]) if ev.record_id else None
    if trip is None:
        return _f(ev, "internal_record", False, f"record {ev.record_id} no longer stored")
    want = ev.timestamp if ev.timestamp is not None else ev.value
    if not hasattr(trip, ev.field or "") and ev.ref is not None:
        return _by_ref(ev, trip, want)
    cur = getattr(trip, ev.field or "", None)
    if isinstance(cur, list):
        for item in cur:
            vals = [getattr(item, a, None) for a in ("value", "start", "sent_at", "held_from", "at")]
            src = getattr(getattr(item, "source", None), "value", None)
            source_ok = src is None or ev.record_type not in _TS or src == ev.record_type
            if source_ok and any(_same(v, want) for v in vals):
                return _f(ev, "internal_record", True, f"{ev.field} still holds the cited value")
        return _f(ev, "internal_record", False, f"{ev.field} no longer holds {want}")
    ok = _same(cur, want) or _same(cur, ev.value)
    return _f(ev, "internal_record", ok, f"{ev.field} {'unchanged' if ok else 'changed or missing'}")


def _by_ref(ev: Evidence, trip: Trip, want: Any) -> AuditFinding:
    """Find the cited row by its source file and row among the trip's records; its time must be unchanged."""
    for name in type(trip).model_fields:
        cur = getattr(trip, name)
        if not isinstance(cur, list):
            continue
        for item in cur:
            ref = getattr(item, "ref", None)
            if ref is not None and ref.file == ev.ref.file and ref.row == ev.ref.row and ref.page == ev.ref.page:
                vals = [getattr(item, a, None) for a in ("value", "start", "sent_at", "held_from", "at")]
                ok = any(_same(v, want) for v in vals)
                return _f(ev, "internal_record", ok, f"{ev.ref.file} row {ev.ref.row} " + ("unchanged" if ok else "changed"))
    return _f(ev, "internal_record", False, f"{ev.ref.file} row {ev.ref.row} no longer on the trip")


_TS = {"client_gate_log", "consignee_gate_log", "weighbridge", "pod_signed", "eway_bill", "gps", "fastag",
       "driver_report", "client_stated", "ops_note"}


def _is_redirect(url: str) -> bool:
    try:
        from pdr.evidence.redirect import is_redirect_link
        if is_redirect_link(url):
            return True
    except Exception:  # noqa: BLE001
        pass
    host = urlparse(url or "").netloc.lower()
    return not host or host == "google.com" or host.endswith(".google.com")


def _reparse(html: str):
    from pdr.evidence.pubdate import published_at
    return published_at(html)


def check_external(ev: Evidence, store) -> list[AuditFinding]:
    """Snapshot hash, passage in snapshot, publication date re-parses, URL is the article."""
    out = []
    snap = store.snapshot_get(ev.content_hash) if ev.content_hash else None
    if snap is None:
        return [_f(ev, "external_snapshot", False, "no snapshot stored for the cited page")]
    out.append(_f(ev, "external_snapshot", sha256_hex(snap.get("text") or "") == ev.content_hash,
                  "snapshot hash re-computed"))
    out.append(_f(ev, "external_passage", bool(_n(ev.passage)) and _n(ev.passage) in _n(snap.get("text")),
                  "passage found in snapshot" if _n(ev.passage) in _n(snap.get("text")) else "passage not in snapshot"))
    if ev.published_at is None:
        out.append(_f(ev, "external_date", False, "no publication date"))
    else:
        html_path = snap.get("html_path")
        html = None
        if html_path and Path(html_path).exists():
            html = Path(html_path).read_text(encoding="utf-8", errors="replace")
        if html is None:
            out.append(_f(ev, "external_date", True, "no stored HTML to re-parse; parsed date kept"))
        else:
            again = _reparse(html)
            out.append(_f(ev, "external_date", again == ev.published_at, f"re-parsed {again}"))
    out.append(_f(ev, "external_url", not _is_redirect(ev.url or ""), "resolved article URL"
                  if not _is_redirect(ev.url or "") else "URL is a redirect, not the article"))
    return out


def _claims(ev: Evidence) -> list[str]:
    """Claims the ground draws from the passage: the affected place it names (dates are checked by qualify)."""
    return [ev.place_named] if ev.place_named else [ev.passage or ""]


# ----------------------------------------------------------------------------- entry points

def audit(verdict: Verdict, evidence: list[Evidence], ctx, *, with_refuter: bool = False) -> list[AuditFinding]:
    """Mechanical re-verification of every cited Evidence (plus the refuter read when asked)."""
    store = ctx.store
    ded, trip = _subject(verdict, store)
    on = P.trip_date(ded, trip) if ded is not None else None
    wanted = set(verdict.evidence_ids)
    out: list[AuditFinding] = []
    for ev in evidence:
        if wanted and ev.evidence_id not in wanted:
            continue
        try:
            if ev.kind == "contract_term":
                out.append(check_term(ev, store, on))
            elif ev.kind == "internal_record":
                out.append(check_record(ev, store))
            else:
                out.extend(check_external(ev, store))
                if with_refuter and ev.stance in (None, "supports"):
                    out.append(_refute(ev, ctx))
        except Exception as err:  # noqa: BLE001 - a rule that cannot run fails closed
            out.append(_f(ev, "audit_error", False, f"{type(err).__name__}"))
    return out


def _refute(ev: Evidence, ctx) -> AuditFinding:
    from pdr.evidence import refute
    claims = _claims(ev)
    got = refute(ev, claims, ctx)
    if got is None:
        return _f(ev, "refuter", True, "refuter did not finish: not refuted, not confirmed; mechanical result kept")
    bad = [c for c, ok in zip(claims, got) if not ok]
    return _f(ev, "refuter", not bad, ("not stated: " + "; ".join(bad)) if bad else "all claims stated")


def failed_ids(findings: Iterable[AuditFinding]) -> set[str]:
    return {f.citation_evidence_id for f in findings if not f.passed}


def apply_audit(checks: list[CheckResult], findings: list[AuditFinding]) -> list[CheckResult]:
    """Downgrade GROUND checks citing a failed Evidence to INCOMPLETE with their natural reason."""
    bad = failed_ids(findings)
    detail = {f.citation_evidence_id: f"{f.rule}: {f.detail}" for f in findings if not f.passed}
    out = []
    for c in checks:
        hit = [ct for ct in c.citations if ct.evidence_id in bad]
        if c.status == CheckStatus.GROUND and hit:
            ct = hit[0]
            c = c.model_copy(update={"status": CheckStatus.INCOMPLETE, "reason": NATURAL.get(ct.kind, Reason.EVIDENCE_PARTIAL),
                                     "correction": None, "missing_item": f"{ct.label}: {detail.get(ct.evidence_id, 'failed audit')}",
                                     "note": (c.note + " | downgraded by the citation audit").strip(" |")})
        out.append(c)
    return out
