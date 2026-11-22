"""Stage 5 (notice_checked): is a disruption asserted, notice in the clause's window, FM wording (DESIGN 5.5, D-03)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

from pdr.codes import CheckStatus, EventType, Flag, Reason, Stage, TsSource
from pdr.contracts import CheckContext
from pdr.engine.registry import check
from pdr.models import Citation, Evidence, SourceRef, Term, Trip, sha256_hex

COVERED, ARGUABLE, EXCLUDED, NOT_COVERED, NO_CLAUSE = "COVERED", "ARGUABLE", "EXCLUDED", "NOT_COVERED", "NO_CLAUSE"
PEOPLE_SOURCES = {TsSource.driver_report, TsSource.ops_note}


# ----------------------------------------------------------------------------- citations

def term_evidence(term: Term, agreement=None) -> Evidence:
    """Contract-term Evidence (same id scheme as the timeline module: term-<term_id>)."""
    try:
        from pdr.engine.timeline import term_evidence as _te
        return _te(term, agreement)
    except ImportError:
        title = agreement.title if agreement is not None else term.agreement_id
        file_ref = agreement.file_ref if agreement is not None else term.agreement_id
        return Evidence(evidence_id=f"term-{term.term_id}", kind="contract_term", client_id=term.client_id,
                        term_id=term.term_id, doc_title=title, page=term.page, clause_ref=term.clause_ref,
                        verbatim=term.verbatim, link=f"{file_ref}#page={term.page}")


def record_evidence(trip: Trip, record_type: str, fld: str, value, ts: Optional[datetime], ref: Optional[SourceRef]) -> Evidence:
    """Internal-record Evidence for a notice, halt, hold or report (deterministic id)."""
    eid = "rec-" + sha256_hex(f"{trip.trip_id}|{record_type}|{fld}|{value}|{ref.file if ref else ''}|{ref.row if ref else ''}")[:16]
    return Evidence(evidence_id=eid, kind="internal_record", client_id=trip.client_id, record_type=record_type,
                    record_id=trip.trip_id, field=fld, value=value, timestamp=ts, ref=ref, link=f"#ev-{eid}")


def cite(ev: Evidence) -> Citation:
    """Citation for any Evidence."""
    if ev.kind == "contract_term":
        label = f"{ev.doc_title}, clause {ev.clause_ref}, p.{ev.page}: \"{ev.verbatim}\""
        return Citation(kind="contract_term", evidence_id=ev.evidence_id, label=label, link=ev.link)
    if ev.kind == "internal_record":
        where = f" ({ev.ref.file}" + (f" row {ev.ref.row}" if ev.ref.row else "") + ")" if ev.ref else ""
        return Citation(kind="internal_record", evidence_id=ev.evidence_id,
                        label=f"{ev.record_type}: {ev.field} = {ev.value}{where}", link=ev.link)
    pub = ev.published_at.isoformat() if ev.published_at else "undated"
    return Citation(kind="external_source", evidence_id=ev.evidence_id,
                    label=f"{ev.url} (published {pub}): \"{ev.passage or ''}\"", link=ev.link)


def keep(c: CheckContext, evs: list[Evidence]) -> list[Citation]:
    """Store Evidence (idempotent upsert) and return citations to it."""
    out = []
    for e in evs:
        try:
            c.ctx.store.put(e)
        except Exception:  # noqa: BLE001 - citing must not fail the check; the audit re-verifies
            pass
        out.append(cite(e))
    return out


# ----------------------------------------------------------------------------- facts

@dataclass
class Claim:
    kind: str
    event_type: Optional[EventType]
    at: Optional[datetime]
    place: Optional[str]
    evidence: Evidence


@dataclass
class Assessment:
    asserted: bool = False
    claims: list[Claim] = field(default_factory=list)
    fm_term: Optional[Term] = None
    notice_term: Optional[Term] = None
    wording: str = NO_CLAUSE
    by_type: dict = field(default_factory=dict)  # event type -> wording
    covered_types: list[EventType] = field(default_factory=list)
    mode: Optional[str] = None  # "asserted" | "discovery" | None (no search)


def _in_force(t: Term, d: date) -> bool:
    return t.effective_from <= d and (t.effective_to is None or d <= t.effective_to)


def _trip_date(c: CheckContext) -> date:
    from pdr.engine.gate import trip_date
    return trip_date(c.trip, c.deduction) if c.trip else c.deduction.deduction_date


def latest_term(terms, term_type: str, d: date, pred=None) -> Optional[Term]:
    """The confirmed term of a type in force on d (latest effective_from wins)."""
    ts = [t for t in terms if t.term_type == term_type and t.status == "confirmed" and _in_force(t, d)
          and (pred is None or pred(t))]
    return max(ts, key=lambda t: (t.effective_from, t.term_id)) if ts else None


def claims(trip: Trip) -> list[Claim]:
    """Disruption assertions in the 3PL's records: ops notes, driver reports, notices, holds, flagged halts."""
    out = []
    for a in trip.assertions:
        out.append(Claim(a.kind, a.event_type, a.at, a.place,
                         record_evidence(trip, a.kind, "text", a.text, a.at, a.ref)))
    for n in trip.notices:
        out.append(Claim("notice", None, n.sent_at, None,
                         record_evidence(trip, "notice", "sent_at", n.sent_at.isoformat(), n.sent_at, n.ref)))
    for h in trip.holds:
        out.append(Claim("hold", EventType.govt_detention, h.held_from, h.place,
                         record_evidence(trip, "hold", "held_from", h.held_from.isoformat(), h.held_from, h.memo_ref)))
    for h in trip.halts:
        if h.source in PEOPLE_SOURCES:
            out.append(Claim("flagged_halt", None, h.start, h.place,
                             record_evidence(trip, h.source.value, "halt", f"{h.start.isoformat()}/{h.end.isoformat()}",
                                             h.start, h.ref)))
    return out


def fm_wording(term: Optional[Term], event_type) -> str:
    """Lookup over the confirmed term's own lists: EXCLUDED, COVERED, ARGUABLE, NOT_COVERED or NO_CLAUSE."""
    if term is None:
        return NO_CLAUSE
    p = term.typed_params()
    if event_type is None:
        return ARGUABLE if p.general_words else (COVERED if p.events_named else NOT_COVERED)
    et = EventType(event_type)
    if et in p.events_excluded:
        return EXCLUDED
    if et in p.events_named:
        return COVERED
    if p.general_words:
        return ARGUABLE
    return NOT_COVERED


def searchable(types, settings) -> list[EventType]:
    allowed = set((settings.evidence.disruption_types or {}).keys()) if settings else set()
    return [EventType(t) for t in types if EventType(t) in allowed]


def assess(c: CheckContext) -> Assessment:
    """Everything stage 5 and the stage-6 plan need, from the trip and the confirmed terms."""
    a = Assessment()
    if c.trip is None:
        return a
    d = _trip_date(c)
    a.claims = claims(c.trip)
    a.asserted = bool(a.claims)
    a.fm_term = latest_term(c.terms, "force_majeure", d)
    a.notice_term = latest_term(c.terms, "notice", d, lambda t: t.params.get("applies_to") == "force_majeure")
    settings = c.ctx.settings if c.ctx else None
    if not a.asserted:
        if a.fm_term is not None:
            named = [e for e in a.fm_term.typed_params().events_named
                     if e not in a.fm_term.typed_params().events_excluded]
            a.covered_types = searchable(named, settings)
            a.wording = COVERED if named else NOT_COVERED
            a.mode = "discovery" if a.covered_types else None
        return a
    types = list(dict.fromkeys(cl.event_type for cl in a.claims if cl.event_type is not None))
    if not types:
        a.wording = fm_wording(a.fm_term, None)
        if a.wording == COVERED:
            a.covered_types = searchable(a.fm_term.typed_params().events_named, settings)
    else:
        a.by_type = {t: fm_wording(a.fm_term, t) for t in types}
        covered = [t for t, w in a.by_type.items() if w == COVERED]
        for w in (COVERED, ARGUABLE, EXCLUDED, NOT_COVERED, NO_CLAUSE):
            if w in a.by_type.values():
                a.wording = w
                break
        a.covered_types = searchable(covered, settings)
    a.mode = "asserted" if a.wording == COVERED else None
    return a


def _transit_start(trip: Trip) -> Optional[datetime]:
    for lst in (trip.gate_out_at, trip.reporting_at):
        if lst:
            return min(o.value for o in lst)
    return None


def notice_window(a: Assessment, trip: Trip) -> tuple[Optional[datetime], Optional[datetime], str]:
    """(earliest, latest, description) for a valid notice under the clause; latest None = no deadline."""
    p = a.notice_term.typed_params()
    times = [cl.at for cl in a.claims if cl.at is not None and cl.kind != "notice"]
    times += [h.start for h in trip.halts]
    anchor = min(times) if times else _transit_start(trip)
    earliest = _transit_start(trip) or anchor
    if p.deadline_hours is not None and anchor is not None:
        return earliest, anchor + timedelta(hours=float(p.deadline_hours)), f"within {p.deadline_hours:g} hours of {anchor.isoformat()}"
    if p.immediate:
        arrivals = [o.value for o in trip.arrival_at]
        latest = min(arrivals) if arrivals else None
        return earliest, latest, "immediately (taken as before delivery)"
    if p.deadline_hours is not None:
        return None, None, "deadline set but the disruption's start time is not recorded"
    return earliest, None, "no deadline stated"


# ----------------------------------------------------------------------------- checks

def _r(cid, status, **kw) -> "CheckResult":
    from pdr.models import CheckResult
    return CheckResult(check_id=cid, stage=Stage.notice_checked, status=status, **kw)


@check(stage=Stage.notice_checked, needs_terms=["force_majeure", "notice"], settings=["evidence"])
def notice_in_window(c: CheckContext):
    """Asserted disruption: a notice record within the clause's window, or INCOMPLETE and no search."""
    a = assess(c)
    if not a.asserted:
        return _r("notice_in_window", CheckStatus.NOT_APPLICABLE, note="no disruption asserted in the 3PL's records")
    if a.notice_term is None:
        return _r("notice_in_window", CheckStatus.NOT_APPLICABLE,
                  note="no confirmed notice term for force majeure; notice not required by the agreement")
    earliest, latest, desc = notice_window(a, c.trip)
    term_cits = keep(c, [term_evidence(a.notice_term, c.agreement)])
    if earliest is None and latest is None and "not recorded" in desc:
        return _r("notice_in_window", CheckStatus.INCOMPLETE, reason=Reason.NOTICE_EVIDENCE_MISSING,
                  missing_item=f"time the disruption began, to test notice under clause {a.notice_term.clause_ref}",
                  citations=term_cits, depends_on=[a.notice_term.term_id], note=desc)
    ok = [n for n in c.trip.notices if (earliest is None or n.sent_at >= earliest) and (latest is None or n.sent_at <= latest)]
    if not ok:
        return _r("notice_in_window", CheckStatus.INCOMPLETE, reason=Reason.NOTICE_EVIDENCE_MISSING,
                  missing_item=f"notice to the client {desc} (clause {a.notice_term.clause_ref}, p.{a.notice_term.page})",
                  citations=term_cits, depends_on=[a.notice_term.term_id],
                  note="no notice record in the clause's window; no paid search run")
    n = ok[0]
    ev = record_evidence(c.trip, "notice", "sent_at", n.sent_at.isoformat(), n.sent_at, n.ref)
    return _r("notice_in_window", CheckStatus.NO_GROUND, citations=term_cits + keep(c, [ev]),
              depends_on=[a.notice_term.term_id],
              note=f"notice sent {n.sent_at.isoformat()} by {n.channel} to {n.addressee}, {desc}")


@check(stage=Stage.notice_checked, needs_terms=["force_majeure"], settings=["evidence"])
def fm_wording_check(c: CheckContext):
    """FM wording rule from the term's own lists (PRD 6): covered, arguable, excluded or no clause."""
    a = assess(c)
    fm_cits = keep(c, [term_evidence(a.fm_term, c.agreement)]) if a.fm_term else []
    dep = [a.fm_term.term_id] if a.fm_term else []
    if not a.asserted:
        if a.mode == "discovery":
            return _r("fm_wording", CheckStatus.NOT_APPLICABLE, citations=fm_cits, depends_on=dep,
                      note="no disruption asserted; discovery search for event types the clause names: "
                           + ", ".join(t.value for t in a.covered_types))
        return _r("fm_wording", CheckStatus.NOT_APPLICABLE, citations=fm_cits, depends_on=dep,
                  note="no disruption asserted and no named event types to search for")
    claim_cits = keep(c, [cl.evidence for cl in a.claims])
    types = ", ".join(t.value for t in a.by_type) or "unstated"
    if a.wording == COVERED:
        return _r("fm_wording", CheckStatus.NO_GROUND, citations=fm_cits + claim_cits, depends_on=dep,
                  note=f"clause {a.fm_term.clause_ref} names the asserted event ({types}); external evidence decides")
    if a.wording == ARGUABLE:
        words = ", ".join(a.fm_term.typed_params().general_words)
        return _r("fm_wording", CheckStatus.REFER, reason=Reason.FM_WORDING_ARGUABLE, citations=fm_cits + claim_cits,
                  depends_on=dep, missing_item=f"clause {a.fm_term.clause_ref} covers the asserted event ({types}) only "
                  f"through general words ('{words}'): \"{a.fm_term.verbatim}\"; a person decides",
                  note="wording arguable; referred without a paid search")
    if a.wording in (EXCLUDED, NOT_COVERED):
        verb = "excludes" if a.wording == EXCLUDED else "does not name"
        return _r("fm_wording", CheckStatus.NO_GROUND, citations=fm_cits + claim_cits, depends_on=dep,
                  flags=[Flag.CLIENT_ADMISSION_CHECK],
                  note=f"clause {a.fm_term.clause_ref} {verb} the asserted event ({types}): \"{a.fm_term.verbatim}\"; "
                       "a person checks the client's letters for an admission")
    return _r("fm_wording", CheckStatus.REFER, reason=Reason.NO_FM_CLAUSE, citations=claim_cits,
              missing_item="agreement has no force-majeure clause; a person decides",
              note="disruption asserted but no confirmed force-majeure term")


def evidence_plan(c: CheckContext, prior: dict) -> Optional[dict]:
    """Whether stage 6 runs and how (DESIGN 5.5 table): {'mode', 'event_types'} or None."""
    notice = prior.get(Stage.notice_checked, [])
    if any(r.status == CheckStatus.INCOMPLETE for r in notice):
        return None
    refer = any(r.status == CheckStatus.REFER for r in notice)
    settings = c.ctx.settings if c.ctx else None
    if refer and not (settings and settings.evidence.search_for_referrals):
        return None
    a = assess(c)
    if a.mode is None and not refer:
        return None
    if refer:
        named = a.fm_term.typed_params().events_named if a.fm_term else []
        types = searchable(list(a.by_type) or named, settings)
        return {"mode": "asserted", "event_types": types, "referral": True} if types else None
    return {"mode": a.mode, "event_types": list(a.covered_types)}
