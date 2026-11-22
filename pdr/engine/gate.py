"""Stage 1 (received) and stage 2 (matched) checks: strict identity, agreement in force, terms confirmed (D-27)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional

from pdr.codes import CheckStatus, Flag, Reason, Stage
from pdr.contracts import CheckContext
from pdr.engine.registry import check
from pdr.models import Agreement, CheckResult, Client, Deduction, Term, Trip, norm_key

# word-level normalisation only: punctuation, case and standard legal-suffix spellings. Never fuzzy.
_SUFFIX = {"PRIVATE": "PVT", "LIMITED": "LTD", "COMPANY": "CO", "CORPORATION": "CORP", "AND": "&",
           "INCORPORATED": "INC", "PVTLTD": "PVT LTD"}

_INSTRUCTION = re.compile(
    r"\b(approve|ignore|disregard|override|mark\s+(?:as|all)|set\s+(?:the\s+)?(?:verdict|outcome)|"
    r"you\s+(?:must|should)|system\s*:|instructions?)\b.*\b(all|dispute|disputes|verdict|valid|previous|"
    r"above|rules?|disputable|outcome|this)\b", re.I)


def norm_name(s: str | None) -> str:
    """Upper-case, punctuation removed, legal suffixes spelled one way."""
    words = re.sub(r"[^A-Z0-9& ]", " ", (s or "").upper().replace(".", "")).split()
    return " ".join(_SUFFIX.get(w, w) for w in words)


def has_instruction(text: str | None) -> bool:
    """True when free text holds imperative text aimed at the system (it is flagged, never obeyed)."""
    return bool(text and _INSTRUCTION.search(text))


@dataclass
class Identity:
    client: Optional[Client] = None
    trip: Optional[Trip] = None
    agreement: Optional[Agreement] = None
    agreements: list[Agreement] = field(default_factory=list)
    terms: list[Term] = field(default_factory=list)  # confirmed terms of the agreement set (all versions)
    unconfirmed: list[Term] = field(default_factory=list)  # in force on the trip date, not confirmed
    trip_date: Optional[date] = None
    problems: list[CheckResult] = field(default_factory=list)
    notes: list[CheckResult] = field(default_factory=list)

    def summary(self) -> dict:
        """What the identity resolved to (feeds inputs_hash)."""
        return {
            "client": self.client.model_dump(mode="json", exclude={"created_at"}) if self.client else None,
            "agreements": [a.model_dump(mode="json", exclude={"created_at"}) for a in self.agreements],
            "unconfirmed": sorted(t.term_id for t in self.unconfirmed),
            "problems": [p.model_dump(mode="json") for p in self.problems],
        }


def _res(check_id: str, status: CheckStatus, reason=None, missing=None, note="", flags=None) -> CheckResult:
    return CheckResult(check_id=check_id, stage=Stage.matched, status=status, reason=reason,
                       missing_item=missing, note=note, flags=list(flags or []))


def _first_time(trip: Trip):
    for obs_list in (trip.gate_out_at, trip.reporting_at, trip.indent_issued_at):
        if obs_list:
            return min(o.value for o in obs_list)
    return trip.placement_required_at or trip.delivery_due_at


def trip_date(trip: Trip, deduction: Deduction) -> date:
    """The date that decides which agreement and rate are in force (dispatch, else due, else deduction date)."""
    t = _first_time(trip) if trip else None
    return t.date() if t else deduction.deduction_date


def _in_force(eff_from: date, eff_to: date | None, d: date) -> bool:
    return eff_from <= d and (eff_to is None or d <= eff_to)


def _match_client(ded: Deduction, store) -> tuple[Client | None, CheckResult]:
    clients = store.find(Client)
    raw = norm_name(ded.client_name_raw)
    if not raw:
        c = store.get(Client, ded.client_id) if ded.client_id else None
        if c:
            return c, _res("gate_client", CheckStatus.NO_GROUND, note=f"client {c.client_id} by id")
        return None, _res("gate_client", CheckStatus.INCOMPLETE, Reason.AMBIGUOUS_MATCH,
                          "client name on the deduction", "deduction names no client")
    hits = [c for c in clients if raw == norm_name(c.legal_name) or raw in {norm_name(a) for a in c.aliases}]
    if ded.client_id:
        hits_same = [c for c in hits if c.client_id == ded.client_id]
        if hits and not hits_same:
            names = ", ".join(f"{c.legal_name} ({c.client_id})" for c in hits)
            return None, _res("gate_client", CheckStatus.INCOMPLETE, Reason.AMBIGUOUS_MATCH,
                              f"issuer '{ded.client_name_raw}' matches {names}, not client {ded.client_id}",
                              "issuing entity differs from the client the deduction was filed under")
    if len(hits) == 1:
        c = hits[0]
        return c, _res("gate_client", CheckStatus.NO_GROUND,
                       note=f"client '{ded.client_name_raw}' is {c.legal_name} ({c.client_id}), exact name or confirmed alias")
    if len(hits) > 1:
        names = ", ".join(f"{c.legal_name} ({c.client_id})" for c in hits)
        return None, _res("gate_client", CheckStatus.INCOMPLETE, Reason.AMBIGUOUS_MATCH,
                          f"'{ded.client_name_raw}' matches several clients: {names}", "more than one client matches")
    words = set(raw.split()) - {"PVT", "LTD", "CO", "&", "INDIA", "THE"}
    cands = [c for c in clients if words & set(norm_name(c.legal_name).split())]
    cand_txt = ", ".join(f"{c.legal_name} ({c.client_id})" for c in cands) or "none"
    return None, _res("gate_client", CheckStatus.INCOMPLETE, Reason.AMBIGUOUS_MATCH,
                      f"legal entity '{ded.client_name_raw}' has no agreement on file; lookalike candidates: {cand_txt}",
                      "no exact legal-name or confirmed-alias match; lookalikes are never auto-resolved")


def _match_trip(ded: Deduction, client: Client, store) -> tuple[Trip | None, CheckResult]:
    refs = [norm_key(r) for r in list(ded.trip_refs) + [ded.invoice_ref] if r and norm_key(r)]
    if not refs:
        return None, _res("gate_trip", CheckStatus.INCOMPLETE, Reason.TRIP_NOT_FOUND,
                          "trip reference (LR, invoice or e-way bill no.) on the deduction", "deduction cites no trip")
    hits = {}
    for t in store.find(Trip, client_id=client.client_id):
        keys = {norm_key(x) for x in (t.lr_no, t.invoice_no, t.eway_bill_no) if x}
        if keys & set(refs):
            hits[t.trip_id] = t
    shown = ", ".join(r for r in list(ded.trip_refs) + [ded.invoice_ref] if r)
    if not hits:
        return None, _res("gate_trip", CheckStatus.INCOMPLETE, Reason.TRIP_NOT_FOUND,
                          f"no trip with LR, invoice or e-way bill no. {shown}", "exact reference match only")
    if len(hits) > 1:
        return None, _res("gate_trip", CheckStatus.INCOMPLETE, Reason.AMBIGUOUS_MATCH,
                          f"{len(hits)} trips match {shown}: {', '.join(sorted(hits))}", "more than one trip matches")
    t = next(iter(hits.values()))
    return t, _res("gate_trip", CheckStatus.NO_GROUND, note=f"trip {t.trip_id} (LR {t.lr_no}) matched exactly")


def _site_lane_ok(a: Agreement, trip: Trip) -> bool:
    if a.lanes and norm_key(trip.lane) not in {norm_key(x) for x in a.lanes}:
        return False
    if a.sites and not ({norm_key(trip.origin), norm_key(trip.destination)} & {norm_key(x) for x in a.sites}):
        return False
    return True


def resolve(ded: Deduction, store) -> Identity:
    """Strict identity: client, trip, agreement set in force, confirmed terms. Problems become gate results."""
    idn = Identity()
    client, r = _match_client(ded, store)
    (idn.problems if r.status == CheckStatus.INCOMPLETE else idn.notes).append(r)
    if client is None:
        return idn
    idn.client = client
    trip, r = _match_trip(ded, client, store)
    (idn.problems if r.status == CheckStatus.INCOMPLETE else idn.notes).append(r)
    if trip is None:
        return idn
    idn.trip = trip
    d = idn.trip_date = trip_date(trip, ded)
    all_ags = store.find(Agreement, client_id=client.client_id)
    cands = [a for a in all_ags if _in_force(a.effective_from, a.effective_to, d) and _site_lane_ok(a, trip)]
    superseded = {a.supersedes for a in cands if a.supersedes}
    cands = [a for a in cands if a.agreement_id not in superseded]
    if not cands:
        others = ", ".join(f"{a.title} ({a.effective_from}..{a.effective_to or 'open'})" for a in all_ags) or "none on file"
        idn.problems.append(_res("gate_agreement", CheckStatus.INCOMPLETE, Reason.TERMS_MISSING,
                                 f"agreement with {client.legal_name} in force on {d} for lane {trip.lane} "
                                 f"({trip.origin} to {trip.destination}); on file: {others}"))
        return idn
    groups: dict[tuple, list[Agreement]] = {}
    for a in cands:
        groups.setdefault((a.doc_type.lower(), a.precedence_rank), []).append(a)
    clash = [g for g in groups.values() if len(g) > 1]
    if clash:
        names = ", ".join(f"{a.title} ({a.agreement_id})" for a in clash[0])
        idn.problems.append(_res("gate_agreement", CheckStatus.INCOMPLETE, Reason.AMBIGUOUS_MATCH,
                                 f"two agreements in force on {d} for lane {trip.lane}: {names}"))
        return idn
    cands.sort(key=lambda a: (a.precedence_rank, a.effective_from, a.agreement_id))
    idn.agreements, idn.agreement = cands, cands[0]
    stand_in = any(a.is_stand_in for a in cands)
    idn.notes.append(_res("gate_agreement", CheckStatus.NO_GROUND,
                          note="agreement set in force: " + ", ".join(f"{a.title} ({a.agreement_id})" for a in cands),
                          flags=[Flag.STAND_IN_TERMS] if stand_in else []))
    ids = {a.agreement_id for a in cands}
    terms = [t for t in store.find(Term, client_id=client.client_id) if t.agreement_id in ids and t.status != "rejected"]
    idn.terms = sorted([t for t in terms if t.status == "confirmed"], key=lambda t: t.term_id)
    idn.unconfirmed = sorted([t for t in terms if t.status != "confirmed" and _in_force(t.effective_from, t.effective_to, d)],
                             key=lambda t: t.term_id)
    if not terms:
        idn.problems.append(_res("gate_terms", CheckStatus.INCOMPLETE, Reason.TERMS_MISSING,
                                 f"terms of {idn.agreement.title} have not been taken in (no extracted terms)"))
    elif idn.unconfirmed:
        items = "; ".join(f"{t.term_type} term on page {t.page} (clause {t.clause_ref}) not yet confirmed"
                          for t in idn.unconfirmed)
        idn.problems.append(_res("gate_terms", CheckStatus.INCOMPLETE, Reason.TERMS_UNCONFIRMED, items))
    else:
        idn.notes.append(_res("gate_terms", CheckStatus.NO_GROUND, note=f"{len(idn.terms)} confirmed terms in scope"))
    missing = sorted({m for a in cands if a.completeness == "missing" for m in a.missing_documents})
    incomplete = any(a.completeness == "missing" for a in cands)
    idn.notes.append(_res("gate_set_completeness", CheckStatus.NOT_APPLICABLE,
                          note=("agreement set marked incomplete; missing: " + (", ".join(missing) or "unnamed documents"))
                          if incomplete else "agreement set marked complete by a person"))
    return idn


# ----------------------------------------------------------------------------- registered checks

@check(stage=Stage.received)
def received_instruction_scan(c: CheckContext) -> CheckResult:
    """Flag imperative text in the deduction; it has no other effect."""
    flagged = Flag.INSTRUCTION_IN_INPUT in c.deduction.flags or has_instruction(c.deduction.reason_text)
    return CheckResult(check_id="received_instruction_scan", stage=Stage.received, status=CheckStatus.NOT_APPLICABLE,
                       flags=[Flag.INSTRUCTION_IN_INPUT] if flagged else [],
                       note="instruction-like text in the deduction is treated as data and ignored" if flagged
                       else "no instruction-like text in the deduction")


@check(stage=Stage.matched)
def gate_match(c: CheckContext) -> list[CheckResult]:
    """Stage 2 gate: client, trip, agreement in force, terms confirmed, set completeness."""
    idn = getattr(c, "identity", None) or resolve(c.deduction, c.ctx.store)
    return list(idn.notes) + list(idn.problems)
