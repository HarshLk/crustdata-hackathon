"""Stage 3 (terms_checked): basis, rate version, base, cap, day count, duplicate, window, waiver, legal."""
from __future__ import annotations

import os
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Iterable, Optional

from pdr.codes import Base, CheckStatus as CS, DayCount, Flag, Ground, RateUnit, Reason, ReasonCode, Stage
from pdr.contracts import CheckContext
from pdr.engine import penalty as P
from pdr.engine import timeline as TL
from pdr.engine.calendar import IST, weekdays_in, working_days
from pdr.engine.registry import check
from pdr.models import Agreement, CheckResult, Citation, Deduction, Evidence, Term

ST = Stage.terms_checked
TOL = Decimal("1.00")  # rupee tolerance when matching a client's figure to a reading
LEGAL_REASONS = {ReasonCode.shortage, ReasonCode.damage}
_LOSS_WORDS = re.compile(r"\b(actual|real|consequential)\s+loss|\bcompensation\b|\bdamages\b|\bloss suffered\b", re.I)


# ----------------------------------------------------------------------------- shared helpers

def res(check_id, status, stage=ST, *, ground=None, reason=None, missing=None, cits=(), deps=(),
        corr=None, flags=(), note="") -> CheckResult:
    return CheckResult(check_id=check_id, stage=stage, status=status, ground=ground, reason=reason,
                       missing_item=missing, citations=list(cits), depends_on=list(deps), correction=corr,
                       flags=list(flags), note=note)


def keep(c: CheckContext, evs: Iterable[Evidence]) -> list[Citation]:
    """Store Evidence (idempotent) and return citations to it."""
    out = []
    for e in evs:
        if e is None:
            continue
        try:
            c.ctx.store.put(e)
        except Exception:  # noqa: BLE001 - citing must not fail the check; the audit re-verifies
            pass
        out.append(TL.cite(e))
    return out


def agreement_of(c: CheckContext, term: Term) -> Optional[Agreement]:
    if c.agreement is not None and c.agreement.agreement_id == term.agreement_id:
        return c.agreement
    try:
        return c.ctx.store.get(Agreement, term.agreement_id) or c.agreement
    except Exception:  # noqa: BLE001
        return c.agreement


def term_cits(c: CheckContext, terms: Iterable[Term]) -> list[Citation]:
    return keep(c, [TL.term_evidence(t, agreement_of(c, t)) for t in terms])


def superseded_cits(c: CheckContext, terms: Iterable[Term]) -> list[Citation]:
    """Cite a version not in force on the trip date (for comparison); the auditor checks it is NOT in force."""
    evs = []
    for t in terms:
        e = TL.term_evidence(t, agreement_of(c, t))
        evs.append(e.model_copy(update={"evidence_id": "term-superseded-" + t.term_id}))
    return keep(c, evs)


def tdate(c: CheckContext) -> date:
    return P.trip_date(c.deduction, c.trip)


def rules_now(c: CheckContext) -> list[Term]:
    return P.rules_for(c.terms, c.deduction.reason_code, tdate(c))


def figure_reason(name: str) -> Reason:
    return Reason.TERMS_MISSING if ("contract" in name or "penalty rule" in name) else Reason.EVIDENCE_PARTIAL


def incomplete_figure(check_id: str, err: P.MissingFigure, stage=ST, deps=()) -> CheckResult:
    return res(check_id, CS.INCOMPLETE, stage, reason=figure_reason(err.name), missing=err.name, deps=deps,
               note="figure needed for this check is not held")


def agreements_in_force(c: CheckContext) -> list[Agreement]:
    idn = getattr(c, "identity", None)
    ags = list(getattr(idn, "agreements", []) or []) if idn is not None else []
    return ags or ([c.agreement] if c.agreement is not None else [])


def client_calc(c: CheckContext):
    return c.deduction.client_calc


# ----------------------------------------------------------------------------- basis

@check(stage=ST, needs_terms=["penalty_rule"])
def contractual_basis(c: CheckContext) -> CheckResult:
    """The stated reason must map to a penalised event in the confirmed schedule (case 20)."""
    ded = c.deduction
    if ded.reason_code in LEGAL_REASONS:
        return res("contractual_basis", CS.NOT_APPLICABLE, note="loss claim, not a scheduled penalty; see legal check")
    d = tdate(c)
    if P.rules_for(c.terms, ded.reason_code, d):
        return res("contractual_basis", CS.NO_GROUND, deps=[t.term_id for t in P.rules_for(c.terms, ded.reason_code, d)],
                   note=f"{ded.reason_code.value} is a penalised event in the confirmed schedule")
    ags = agreements_in_force(c)
    missing_docs = sorted({m for a in ags for m in a.missing_documents})
    complete = bool(ags) and all(a.completeness == "complete" for a in ags) and not missing_docs
    if P.rules_for(c.terms, ded.reason_code):
        return res("contractual_basis", CS.INCOMPLETE, reason=Reason.TERMS_MISSING,
                   missing=f"penalty rule for {ded.reason_code.value} in force on {d.isoformat()} (only other versions on file)")
    if not complete:
        return res("contractual_basis", CS.INCOMPLETE, reason=Reason.TERMS_MISSING,
                   missing="complete agreement set (missing: " + (", ".join(missing_docs) or "documents not confirmed complete")
                   + f"); no confirmed term penalises {ded.reason_code.value}")
    schedule = [t for t in c.terms if t.term_type == "penalty_rule"]
    if schedule:
        cits = term_cits(c, schedule)
    else:
        a = ags[0]
        ev = Evidence(evidence_id=f"agr-{a.agreement_id}", kind="contract_term", client_id=a.client_id,
                      doc_title=a.title, clause_ref="whole agreement set", page=1,
                      verbatim="(no clause penalises this event)", link=f"{a.file_ref}#page=1")
        cits = keep(c, [ev])
    return res("contractual_basis", CS.GROUND, ground=Ground.NO_CONTRACTUAL_BASIS, cits=cits,
               deps=[t.term_id for t in schedule],
               corr=P.correction("void", ground="NO_CONTRACTUAL_BASIS", citations=cits,
                                 label=f"No confirmed term penalises '{ded.reason_text}' ({ded.reason_code.value}); "
                                       "agreement set marked complete"),
               note="reason maps to no penalised event in a complete agreement set")


# ----------------------------------------------------------------------------- rate version

@check(stage=ST, needs_terms=["penalty_rule"])
def rate_version(c: CheckContext) -> CheckResult:
    """The rate in force on the trip date, not an older or later version (case 13)."""
    cc, rules = client_calc(c), rules_now(c)
    if not rules:
        return res("rate_version", CS.NOT_APPLICABLE, note="no penalty rule in force; see basis check")
    rule = max(rules, key=lambda t: Decimal(t.typed_params().rate))  # several in force: highest is permitted
    p = rule.typed_params()
    if cc is None or cc.rate is None:
        return _rate_by_amount(c, rule)
    c_unit = RateUnit(cc.unit) if cc.unit else p.unit
    if c_unit != p.unit:
        return res("rate_version", CS.NO_GROUND, deps=[rule.term_id],
                   note=f"client used unit {c_unit.value}, clause {p.unit.value}; settled by the day-count and base checks")
    if Decimal(cc.rate) <= Decimal(p.rate):
        return res("rate_version", CS.NO_GROUND, deps=[rule.term_id], note=f"client rate {cc.rate} within the rate in force {p.rate}")
    others = [t for t in _all_versions(c) if t.term_id != rule.term_id and Decimal(t.typed_params().rate) == Decimal(cc.rate)]
    cits = term_cits(c, [rule]) + superseded_cits(c, others[:1])
    return res("rate_version", CS.GROUND, ground=Ground.WRONG_RATE, cits=cits, deps=[rule.term_id],
               corr=P.correction("rate", term_id=rule.term_id, citations=cits),
               note=f"client applied {cc.rate}; rate in force on {tdate(c).isoformat()} is {p.rate} (clause {rule.clause_ref}, "
                    f"from {rule.effective_from})" + (f"; client's rate is version {others[0].clause_ref} "
                                                       f"({others[0].effective_from}..{others[0].effective_to})" if others else ""))


def _all_versions(c: CheckContext) -> list[Term]:
    """Every confirmed version of the event's rule for this client (amendments may sit in other agreements)."""
    seen = {t.term_id: t for t in c.terms}
    try:
        for t in c.ctx.store.find(Term, client_id=c.deduction.client_id):
            seen.setdefault(t.term_id, t)
    except Exception:  # noqa: BLE001
        pass
    return P.rules_for(seen.values(), c.deduction.reason_code)


def _rate_by_amount(c: CheckContext, rule: Term) -> CheckResult:
    """No stated rate: a ground only if the amount equals another version's figure exactly."""
    try:
        correct, _ = P.recompute(c.deduction, c.trip, [t for t in c.terms if t.term_type != "cap"], [], agreement=c.agreement)
    except P.MissingFigure:
        return res("rate_version", CS.NO_GROUND, deps=[rule.term_id], note="client's rate not stated; amount not matched")
    for other in _all_versions(c):
        if other.term_id == rule.term_id or Decimal(other.typed_params().rate) <= Decimal(rule.typed_params().rate):
            continue
        try:
            fig, _ = P.recompute(c.deduction, c.trip, [other], [P.correction("rate", term_id=other.term_id)], agreement=c.agreement)
        except P.MissingFigure:
            continue
        if abs(fig - Decimal(c.deduction.amount)) <= TOL and fig > correct:
            cits = term_cits(c, [rule]) + superseded_cits(c, [other])
            return res("rate_version", CS.GROUND, ground=Ground.WRONG_RATE, cits=cits, deps=[rule.term_id],
                       corr=P.correction("rate", term_id=rule.term_id, citations=cits),
                       note=f"amount {c.deduction.amount} equals the figure at superseded version {other.clause_ref} "
                            f"({other.typed_params().rate}); rate in force is {rule.typed_params().rate}")
    return res("rate_version", CS.NO_GROUND, deps=[rule.term_id], note="client's rate not stated; no other version matches")


# ----------------------------------------------------------------------------- base

@check(stage=ST, needs_terms=["penalty_rule"])
def wrong_base(c: CheckContext) -> CheckResult:
    """The penalty must be applied to the base the clause names (case 6)."""
    cc, rules = client_calc(c), rules_now(c)
    if not rules:
        return res("wrong_base", CS.NOT_APPLICABLE, note="no penalty rule in force")
    rule = rules[0]
    p = rule.typed_params()
    if p.unit in (RateUnit.flat_per_day,) or (p.unit == RateUnit.per_instance and p.base == Base.none):
        return res("wrong_base", CS.NOT_APPLICABLE, deps=[rule.term_id], note="flat rate: no base")
    if cc is None or cc.base_amount is None:
        return res("wrong_base", CS.NO_GROUND, deps=[rule.term_id], note="client's base not stated")
    try:
        mult = P._multiplier(p.unit, p.base, c.trip, c.agreement, None)
    except P.MissingFigure as e:
        return incomplete_figure("wrong_base", e, deps=[rule.term_id])
    if Decimal(cc.base_amount) <= mult:
        return res("wrong_base", CS.NO_GROUND, deps=[rule.term_id], note=f"client base {cc.base_amount} within clause base {mult}")
    cits = term_cits(c, [rule]) + _base_record_cits(c, p.base)
    return res("wrong_base", CS.GROUND, ground=Ground.WRONG_BASE, cits=cits, deps=[rule.term_id],
               corr=P.correction("base", term_id=rule.term_id, citations=cits),
               note=f"client applied the rate to {cc.base_amount}; clause {rule.clause_ref} names {p.base.value} = {mult}")


def _base_record_cits(c: CheckContext, base: Base) -> list[Citation]:
    fld = {Base.trip_cost: "freight", Base.delayed_quantity_value: "delayed_quantity_value"}.get(Base(base))
    if c.trip is None or fld is None or getattr(c.trip, fld) is None:
        return []
    from pdr.models import sha256_hex
    val = getattr(c.trip, fld)
    eid = "rec-" + sha256_hex(f"{c.trip.trip_id}|{fld}|{val}")[:16]
    ev = Evidence(evidence_id=eid, kind="internal_record", client_id=c.trip.client_id, record_type="trip",
                  record_id=c.trip.trip_id, field=fld, value=str(val), ref=c.trip.source_ref, link=f"#ev-{eid}")
    return keep(c, [ev])


# ----------------------------------------------------------------------------- cap

@check(stage=ST, needs_terms=["penalty_rule", "cap"])
def over_cap(c: CheckContext) -> CheckResult:
    """Deducted amount above the confirmed cap (cap base and scope as the clause states)."""
    caps = P.caps_for(c.terms, tdate(c))
    if not caps:
        return res("over_cap", CS.NOT_APPLICABLE, note="no confirmed cap clause: no over-cap ground for this client")
    ceilings = []
    for t in caps:
        cp = t.typed_params()
        try:
            ceil_ = Decimal(cp.value) if cp.kind == "abs" else Decimal(cp.value) / 100 * P.base_amount(cp.base, c.trip, c.agreement)
        except P.MissingFigure as e:
            return incomplete_figure("over_cap", e, deps=[t.term_id])
        ceilings.append((ceil_, t))
    ceil_, t = min(ceilings, key=lambda x: x[0])
    if Decimal(c.deduction.amount) <= ceil_:
        return res("over_cap", CS.NO_GROUND, deps=[t.term_id], note=f"deducted within the cap {P.q(ceil_)}")
    cp = t.typed_params()
    cits = term_cits(c, [t]) + _base_record_cits(c, cp.base)
    return res("over_cap", CS.GROUND, ground=Ground.OVER_CAP, cits=cits, deps=[t.term_id],
               corr=P.correction("cap", term_id=t.term_id, citations=cits),
               note=f"deducted {c.deduction.amount} exceeds the cap {cp.value}{'%' if cp.kind == 'pct' else ''} of "
                    f"{cp.base.value} = {P.q(ceil_)} (clause {t.clause_ref}); excess {P.q(Decimal(c.deduction.amount) - ceil_)}")


# ----------------------------------------------------------------------------- day count

@check(stage=ST, needs_terms=["penalty_rule"], needs_events=["delivery_due", "arrival"])
def day_count(c: CheckContext) -> CheckResult:
    """Days counted by the clause's rule (working days skip Sundays; other holidays need a list, D-12)."""
    cc, rules = client_calc(c), rules_now(c)
    if not rules:
        return res("day_count", CS.NOT_APPLICABLE, note="no penalty rule in force")
    rule = rules[0]
    p = rule.typed_params()
    if p.unit == RateUnit.per_instance:
        return res("day_count", CS.NOT_APPLICABLE, deps=[rule.term_id], note="per-instance penalty")
    if cc is None or cc.units_counted is None:
        return res("day_count", CS.NO_GROUND, deps=[rule.term_id], note="client's day count not stated; recomputed at verdict")
    start, end, deps = _span(c, p)
    if start is None or end is None:
        return res("day_count", CS.INCOMPLETE, reason=Reason.EVIDENCE_PARTIAL, deps=[rule.term_id] + deps,
                   missing="start and end times of the delay (client's timestamps or trip records)")
    d = tdate(c)
    working = p.day_count == DayCount.working
    hol = P.holidays_from(c.terms, d) if working else set()
    unit = RateUnit(cc.unit) if cc.unit else p.unit
    part = p.part_unit_counts_as_full is not False  # silent -> highest reading (part counts)
    ours = P.count_units(unit, start, end, day_count=p.day_count or DayCount.calendar, part_full=part, holidays=hol or ())
    if unit == RateUnit.pct_per_week_or_part and p.unit != unit:
        ours = P.count_units(unit, start, end, day_count=DayCount.calendar, part_full=True)
    cits = term_cits(c, [rule])
    if Decimal(cc.units_counted) > ours:
        return res("day_count", CS.GROUND, ground=Ground.WRONG_DAY_COUNT, cits=cits, deps=[rule.term_id] + deps,
                   corr=P.correction("day_count", term_id=rule.term_id, citations=cits,
                                     holidays=sorted(x.isoformat() for x in (hol or ()))),
                   note=f"client counted {cc.units_counted}; clause ({(p.day_count or DayCount.calendar).value} days) "
                        f"gives {ours} from {start.astimezone(IST):%d %b %H:%M} to {end.astimezone(IST):%d %b %H:%M}")
    if working and hol is None and weekdays_in(start, end):
        days = ", ".join(x.isoformat() for x in weekdays_in(start, end))
        return res("day_count", CS.INCOMPLETE, reason=Reason.TERMS_MISSING, deps=[rule.term_id] + deps,
                   missing=f"holiday list from the agreement set for the working-day count ({days})",
                   note="Sundays excluded; other holidays need the agreement's list (D-12)")
    return res("day_count", CS.NO_GROUND, deps=[rule.term_id] + deps, note=f"client's count {cc.units_counted} within {ours}")


def _span(c: CheckContext, p) -> tuple[Optional[datetime], Optional[datetime], list[str]]:
    """Delay interval on the client's own timestamps, falling back to the trip records."""
    cc = client_calc(c)
    frm, to, defn = P.DEFAULT_SPAN.get(c.deduction.reason_code, (None, None, None))
    frm = TL.norm_event(p.measured_from) or frm
    to = TL.norm_event(p.measured_to) or P.definition_event(c.terms, defn, tdate(c)) or to
    deps = []

    def rec(ev):
        if c.trip is None or ev is None:
            return None
        deps.append(ev)
        if ev in TL.FIXED_FIELD:
            return TL.fixed(c.trip, ev)
        o = TL.pick(c.trip, ev)
        return o.value if o else None

    start = cc.from_ts if cc and cc.from_ts else rec(frm)
    end = cc.to_ts if cc and cc.to_ts else rec(to)
    return start, end, deps


# ----------------------------------------------------------------------------- duplicate

def _order_key(d: Deduction):
    return (d.deduction_date, d.advice_no or "", _num(d.line_no), d.dispute_key)


def _num(x):
    try:
        return (0, int(str(x)), "")
    except (TypeError, ValueError):
        return (1, 0, str(x or ""))


def _advice_evidence(d: Deduction) -> Evidence:
    eid = "rec-ded-" + d.dispute_key.replace("|", "-")
    return Evidence(evidence_id=eid, kind="internal_record", client_id=d.client_id, record_type="deduction",
                    record_id=d.dispute_key, field="advice_no", value=f"{d.advice_no or d.client_deduction_no} line {d.line_no or '-'}"
                    f" dated {d.deduction_date.isoformat()} for Rs {d.amount}", ref=d.source_ref, link=f"#ev-{eid}")


@check(stage=ST)
def duplicate(c: CheckContext) -> CheckResult:
    """Same trip and penalised event already deducted on another advice (D-08)."""
    ded = c.deduction
    try:
        same = [d for d in c.ctx.store.find(Deduction, event_fp=ded.event_fp) if d.dispute_key != ded.dispute_key]
    except Exception:  # noqa: BLE001
        return res("duplicate", CS.INCOMPLETE, reason=Reason.INTERNAL_ERROR, missing="deduction store not readable")
    if not same:
        return res("duplicate", CS.NO_GROUND, note="no other deduction for this trip and event")
    first = min(same + [ded], key=_order_key)
    if first.dispute_key == ded.dispute_key:
        return res("duplicate", CS.NO_GROUND, note=f"earliest of {len(same) + 1} deductions for this trip and event; "
                                                   "follows its own verdict")
    cits = keep(c, [_advice_evidence(first), _advice_evidence(ded)])
    return res("duplicate", CS.GROUND, ground=Ground.DUPLICATE, cits=cits,
               corr=P.correction("void", ground="DUPLICATE", citations=cits,
                                 label=f"Same trip and event already deducted on advice {first.advice_no} "
                                       f"line {first.line_no} ({first.deduction_date})"),
               note=f"duplicate of {first.dispute_key}")


# ----------------------------------------------------------------------------- dispute window

@check(stage=ST, needs_terms=["time_limit"])
def dispute_window(c: CheckContext) -> CheckResult:
    """Flag OUT_OF_WINDOW when the contract's time limit has passed; never changes the outcome."""
    d = tdate(c)
    lims = [t for t in c.terms if t.term_type == "time_limit" and P.in_force(t, d)]
    if not lims:
        return res("dispute_window", CS.NOT_APPLICABLE, note="no confirmed time limit")
    t = lims[0]
    tl = t.typed_params()
    ded = c.deduction
    start = {"deduction_date": ded.deduction_date, "received_date": ded.received_date or ded.deduction_date,
             "contract_expiry": c.agreement.effective_to if c.agreement else None}[tl.counted_from]
    if start is None:
        return res("dispute_window", CS.NOT_APPLICABLE, note="window counted from contract expiry; agreement open-ended")
    today = _today(c)
    s = datetime.combine(start, datetime.min.time(), IST)
    e = datetime.combine(today, datetime.min.time(), IST)
    elapsed = working_days(s, e) if tl.day_count == DayCount.working else Decimal((today - start).days)
    cits = term_cits(c, [t])
    if elapsed > tl.window_days:
        return res("dispute_window", CS.NO_GROUND, cits=cits, deps=[t.term_id], flags=[Flag.OUT_OF_WINDOW],
                   note=f"{elapsed} {tl.day_count.value} days since {tl.counted_from} {start}; window {tl.window_days} "
                        f"(clause {t.clause_ref}): held for the account manager")
    return res("dispute_window", CS.NO_GROUND, deps=[t.term_id], note=f"within the {tl.window_days}-day window")


def _today(c: CheckContext) -> date:
    now = getattr(c.ctx, "today", None) or os.environ.get("PDR_TODAY")
    if isinstance(now, str):
        now = date.fromisoformat(now)
    if isinstance(now, date):
        return now
    return datetime.now(timezone.utc).astimezone(IST).date()


# ----------------------------------------------------------------------------- waiver, legal

@check(stage=ST, needs_terms=["waiver"])
def explanation_waiver(c: CheckContext) -> CheckResult:
    """Waiver on a satisfactory explanation with our timed explanation on file: a person decides."""
    d = tdate(c)
    ws = [t for t in c.terms if t.term_type == "waiver" and P.in_force(t, d)
          and t.typed_params().condition == "satisfactory_explanation"]
    if not ws:
        return res("explanation_waiver", CS.NOT_APPLICABLE, note="no waiver-on-explanation clause")
    trip = c.trip
    sent = []
    if trip is not None:
        sent = [n for n in trip.notices]
    if not sent:
        return res("explanation_waiver", CS.NO_GROUND, deps=[ws[0].term_id], note="no explanation on file")
    n = min(sent, key=lambda x: x.sent_at)
    from pdr.engine.checks_records import notice_evidence
    cits = term_cits(c, ws[:1]) + keep(c, [notice_evidence(trip, n)])
    return res("explanation_waiver", CS.REFER, reason=Reason.EXPLANATION_IGNORED, cits=cits, deps=[ws[0].term_id],
               missing=f"our explanation of {n.sent_at.astimezone(IST):%d %b %Y} was sent to {n.addressee}; "
                       "the client decides if it is satisfactory")


@check(stage=ST)
def legal_judgment(c: CheckContext) -> CheckResult:
    """Loss claims and 'no real loss' arguments are never decided automatically."""
    ded = c.deduction
    if ded.reason_code in LEGAL_REASONS or _LOSS_WORDS.search(ded.reason_text or ""):
        return res("legal_judgment", CS.REFER, reason=Reason.LEGAL_JUDGMENT,
                   missing=f"loss or damages question ('{ded.reason_text[:80]}'); for legal")
    return res("legal_judgment", CS.NOT_APPLICABLE, note="not a loss claim")
