"""Timeline resolution by the PRD's "which record wins" table (DESIGN 5.4)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from pdr.codes import TsSource as S
from pdr.engine.calendar import to_ist
from pdr.models import Citation, Evidence, Observation, Trip, sha256_hex

# event -> trip field holding its observations
EVENT_FIELD: dict[str, str] = {
    "indent": "indent_issued_at",
    "reporting": "reporting_at",
    "gate_out": "gate_out_at",
    "arrival": "arrival_at",
    "unloaded": "unloaded_at",
}
# events that are a single agreed time on the trip, not observations
FIXED_FIELD: dict[str, str] = {
    "placement_required": "placement_required_at",
    "delivery_due": "delivery_due_at",
}
TIMELINE_EVENTS = frozenset(EVENT_FIELD) | frozenset(FIXED_FIELD)
_SYNONYMS = {"gate_in": "reporting", "gate_in_at": "reporting", "pod": "unloaded", "unloading": "unloaded",
             "loading_complete": "gate_out", "departure": "gate_out", "due": "delivery_due",
             "indent_issued": "indent", "placement": "placement_required"}


def norm_event(name) -> "str | None":
    """Timeline event for a name a term uses ("arrival", "arrival_at", "gate_in"); None if unknown."""
    if not name:
        return None
    s = str(name).strip().lower()
    if s in TIMELINE_EVENTS:
        return s
    for ev, fld in {**EVENT_FIELD, **FIXED_FIELD}.items():
        if s == fld:
            return ev
    return _SYNONYMS.get(s)

# PRD section 6 "which record wins": first choice(s) first, then the fallbacks the 3PL controls.
PRIORITY: dict[str, list[S]] = {
    "reporting": [S.client_gate_log, S.driver_report, S.gps],
    "gate_out": [S.client_gate_log, S.weighbridge, S.eway_bill, S.gps],
    "arrival": [S.consignee_gate_log, S.gps, S.fastag],
    "unloaded": [S.pod_signed, S.gps],
    "indent": [S.client_gate_log, S.eway_bill, S.ops_note],
}
# never compared for conflict: a client's claim is tested by the checks, an ops note is not a clock
_NOT_A_RECORD = {S.client_stated, S.ops_note}

LABEL: dict[str, str] = {
    "client_gate_log": "Client gate log", "consignee_gate_log": "Consignee gate log",
    "weighbridge": "Weighbridge slip", "pod_signed": "Signed POD", "eway_bill": "E-way bill",
    "gps": "GPS", "fastag": "FASTag", "driver_report": "Driver report",
    "client_stated": "Client's statement", "ops_note": "Ops note",
}


def _rank(event: str, obs: Observation) -> int:
    order = PRIORITY.get(event, [])
    return order.index(obs.source) if obs.source in order else len(order) + (obs.source in _NOT_A_RECORD)


def observations(trip: Trip, event: str) -> list[Observation]:
    """All observations of an event, best source first (stable for equal sources)."""
    obs = list(getattr(trip, EVENT_FIELD[event], []) or []) if event in EVENT_FIELD else []
    return sorted(obs, key=lambda o: _rank(event, o))


def pick(trip: Trip, event: str) -> Optional[Observation]:
    """Best observation of an event without conflict testing (pure; no settings)."""
    obs = observations(trip, event)
    return obs[0] if obs else None


def fixed(trip: Trip, event: str) -> Optional[datetime]:
    return getattr(trip, FIXED_FIELD[event], None) if event in FIXED_FIELD else None


def _fmt(dt: datetime) -> str:
    return to_ist(dt).strftime("%d %b %Y %H:%M IST")


def record_evidence(trip: Trip, event: str, obs: Observation) -> Evidence:
    """Internal-record Evidence for one observation (deterministic id)."""
    fld = EVENT_FIELD.get(event, event)
    eid = "rec-" + sha256_hex(f"{trip.trip_id}|{fld}|{obs.source.value}|{obs.value.isoformat()}|"
                              f"{obs.ref.file}|{obs.ref.row}")[:16]
    return Evidence(evidence_id=eid, kind="internal_record", client_id=trip.client_id,
                    record_type=obs.source.value, record_id=trip.trip_id, field=fld,
                    value=obs.value.isoformat(), timestamp=obs.value, ref=obs.ref,
                    link=f"#ev-{eid}")


def fixed_evidence(trip: Trip, event: str) -> Optional[Evidence]:
    """Internal-record Evidence for an agreed time held on the trip (due time, placement time)."""
    fld = FIXED_FIELD[event]
    val = getattr(trip, fld, None)
    if val is None:
        return None
    eid = "rec-" + sha256_hex(f"{trip.trip_id}|{fld}|{val.isoformat()}")[:16]
    return Evidence(evidence_id=eid, kind="internal_record", client_id=trip.client_id,
                    record_type="trip", record_id=trip.trip_id, field=fld, value=val.isoformat(),
                    timestamp=val, ref=trip.source_ref, link=f"#ev-{eid}")


def term_evidence(term, agreement=None) -> Evidence:
    """Contract-term Evidence: document title, page, clause and verbatim text."""
    title = agreement.title if agreement is not None else term.agreement_id
    file_ref = agreement.file_ref if agreement is not None else term.agreement_id
    return Evidence(evidence_id=f"term-{term.term_id}", kind="contract_term", client_id=term.client_id,
                    term_id=term.term_id, doc_title=title, page=term.page, clause_ref=term.clause_ref,
                    verbatim=term.verbatim, link=f"{file_ref}#page={term.page}")


def cite(ev: Evidence) -> Citation:
    """Citation for an internal-record or contract-term Evidence."""
    if ev.kind == "internal_record":
        where = f" ({ev.ref.file}" + (f" row {ev.ref.row}" if ev.ref and ev.ref.row else "") + ")" if ev.ref else ""
        label = f"{LABEL.get(ev.record_type or '', ev.record_type)}: {ev.field} {_fmt(ev.timestamp)}{where}" \
            if ev.timestamp else f"{ev.record_type}: {ev.field} = {ev.value}"
        return Citation(kind="internal_record", evidence_id=ev.evidence_id, label=label, link=ev.link)
    if ev.kind == "contract_term":
        label = f"{ev.doc_title}, clause {ev.clause_ref}, p.{ev.page}: \"{ev.verbatim}\""
        return Citation(kind="contract_term", evidence_id=ev.evidence_id, label=label, link=ev.link)
    return Citation(kind="external_source", evidence_id=ev.evidence_id, label=ev.url or "", link=ev.link)


@dataclass
class ResolvedTimeline:
    trip: Trip
    chosen: dict[str, Observation] = field(default_factory=dict)
    conflicts: dict[str, tuple[Observation, Observation]] = field(default_factory=dict)

    @property
    def conflicted(self) -> set[str]:
        return set(self.conflicts)

    def value(self, event: str) -> Optional[datetime]:
        """Chosen time of an event (or the agreed time for delivery_due/placement_required)."""
        if event in FIXED_FIELD:
            return fixed(self.trip, event)
        o = self.chosen.get(event)
        return o.value if o else None

    def evidence(self, event: str) -> list[Evidence]:
        """Evidence for the chosen observation; both observations when the event is conflicted."""
        if event in FIXED_FIELD:
            ev = fixed_evidence(self.trip, event)
            return [ev] if ev else []
        if event in self.conflicts:
            return [record_evidence(self.trip, event, o) for o in self.conflicts[event]]
        o = self.chosen.get(event)
        return [record_evidence(self.trip, event, o)] if o else []

    def citations(self, event: str) -> list[Citation]:
        return [cite(e) for e in self.evidence(event)]

    def conflict_text(self, event: str) -> str:
        a, b = self.conflicts[event]
        return (f"{LABEL.get(a.source.value, a.source.value)} ({_fmt(a.value)}) and "
                f"{LABEL.get(b.source.value, b.source.value)} ({_fmt(b.value)}) disagree on {event}")


def resolve(trip: Trip, settings) -> ResolvedTimeline:
    """Choose each event's record by source priority; mark it conflicted beyond the allowance."""
    allowance = timedelta(minutes=float(settings.records.conflict_allowance_minutes))
    out = ResolvedTimeline(trip=trip)
    for event in EVENT_FIELD:
        obs = observations(trip, event)
        if not obs:
            continue
        best = obs[0]
        out.chosen[event] = best
        if best.source in _NOT_A_RECORD:
            continue
        for other in obs[1:]:
            if other.source in _NOT_A_RECORD:
                continue
            if abs(other.value - best.value) > allowance:
                out.conflicts[event] = (best, other)
                break
    return out
