"""Penalty recomputation (DESIGN 5.4; D-05, D-06, D-12). Pure: no store, no settings.

Correction.value conventions (build them with `correction(...)`):
  void             {"ground": str, "label": str}
  rate|base|day_count|cap  {"term_id": str}          (the confirmed term to apply; optional)
  timestamp        {"which": "from"|"to", "at": iso datetime, "label": str}
  excused_interval {"start": iso, "end": iso, "label": str}
Every value may carry "citations": [Citation dicts], copied onto the recomputation line.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Iterable, Optional

from pdr.codes import Base, DayCount, RateUnit, ReasonCode
from pdr.engine import timeline
from pdr.engine.calendar import calendar_days, to_ist, union_length, working_days
from pdr.models import Citation, Correction, Deduction, RecomputeLine, Term, Trip

PAISE = Decimal("0.01")
ORDER: dict[str, int] = {"void": 0, "rate": 10, "base": 20, "day_count": 30,
                         "timestamp": 50, "excused_interval": 60, "cap": 70}
PCT_UNITS = {RateUnit.pct_per_week_or_part, RateUnit.pct_per_day, RateUnit.pct_per_vehicle_day}
DAY_UNITS = {RateUnit.pct_per_day, RateUnit.pct_per_vehicle_day, RateUnit.flat_per_day, RateUnit.per_quintal_day}
_QUINTALS_PER = {"quintal": 1, "quintals": 1, "qtl": 1, "q": 1, "tonne": 10, "tonnes": 10, "t": 10,
                 "mt": 10, "kg": Decimal("0.01")}

# default measured_from / measured_to per penalised event; Definition terms refine the "to" event
DEFAULT_SPAN: dict[ReasonCode, tuple[str, str, Optional[str]]] = {
    ReasonCode.late_delivery: ("delivery_due", "arrival", "delivery"),
    ReasonCode.late_placement: ("placement_required", "reporting", "placement"),
    ReasonCode.late_reporting: ("placement_required", "reporting", "reporting"),
}


class MissingFigure(Exception):
    """A figure the calculation needs is not held; the calling check returns INCOMPLETE naming it."""

    def __init__(self, name: str):
        super().__init__(name)
        self.name = name


def correction(kind: str, *, citations: Iterable[Citation] = (), **value: Any) -> Correction:
    """Build a Correction with the fixed order and JSON-safe value."""
    v = {k: (x.isoformat() if isinstance(x, datetime) else x) for k, x in value.items()}
    v["citations"] = [c.model_dump(mode="json") if isinstance(c, Citation) else c for c in citations]
    return Correction(kind=kind, value=v, order=ORDER[kind])


def q(x: Decimal) -> Decimal:
    return Decimal(x).quantize(PAISE, rounding=ROUND_HALF_UP)


def amount_disputable(deducted: Decimal, correct: Decimal) -> Decimal:
    """clamp(deducted - correct, 0, deducted)."""
    return min(max(Decimal(deducted) - Decimal(correct), Decimal(0)), Decimal(deducted))


# --------------------------------------------------------------------------- terms in force

def trip_date(deduction: Deduction, trip: Optional[Trip]) -> date:
    """The date the penalised event happened (decides the term version in force)."""
    if trip is not None:
        for t in (trip.delivery_due_at, trip.placement_required_at):
            if t is not None:
                return to_ist(t).date()
        for ev in ("arrival", "reporting", "gate_out"):
            o = timeline.pick(trip, ev)
            if o is not None:
                return to_ist(o.value).date()
    if deduction.client_calc and deduction.client_calc.from_ts:
        return to_ist(deduction.client_calc.from_ts).date()
    return deduction.deduction_date


def in_force(term: Term, d: date) -> bool:
    return term.status == "confirmed" and term.effective_from <= d and (term.effective_to is None or d <= term.effective_to)


def rules_for(terms: Iterable[Term], event: ReasonCode, d: Optional[date] = None) -> list[Term]:
    """Confirmed penalty rules for an event (in force on d when given)."""
    out = []
    for t in terms:
        if t.term_type == "penalty_rule" and t.status == "confirmed" and t.typed_params().event == event:
            if d is None or in_force(t, d):
                out.append(t)
    return out


def caps_for(terms: Iterable[Term], d: date) -> list[Term]:
    return [t for t in terms if t.term_type == "cap" and in_force(t, d)]


def definition_event(terms: Iterable[Term], name: Optional[str], d: date) -> Optional[str]:
    for t in terms:
        if name and t.term_type == "definition" and in_force(t, d) and t.typed_params().name == name:
            return timeline.norm_event(t.typed_params().event)
    return None


def holidays_from(terms: Iterable[Term], d: date) -> Optional[set[date]]:
    """Holiday list from a confirmed `holiday_list` term ({"dates": [...]}); None if the set has none."""
    found = None
    for t in terms:
        if t.term_type == "holiday_list" and in_force(t, d):
            found = (found or set()) | {date.fromisoformat(str(x)) for x in t.params.get("dates", [])}
    return found


# --------------------------------------------------------------------------- figures

def base_amount(base: Base, trip: Optional[Trip], agreement=None, figures: Optional[dict] = None) -> Decimal:
    """The amount a clause's base names; raises MissingFigure when it is not held."""
    figures = figures or {}
    base = Base(base)
    if base == Base.none:
        return Decimal(1)
    val = None
    if base == Base.trip_cost:
        val = trip.freight if trip is not None else None
    elif base == Base.delayed_quantity_value:
        val = trip.delayed_quantity_value if trip is not None else None
    elif base == Base.contract_value:
        val = agreement.contract_value if agreement is not None else None
    val = figures.get(base.value, val) if val is None else val
    if val is None:
        raise MissingFigure({"trip_cost": "trip cost (freight) for the trip",
                             "delayed_quantity_value": "value of the delayed quantity",
                             "contract_value": "contract value of the agreement",
                             "invoice_value": "invoice value", "daily_hire": "daily hire cost"}[base.value])
    return Decimal(val)


def quintals(trip: Optional[Trip]) -> Decimal:
    if trip is None or trip.quantity is None:
        raise MissingFigure("quantity carried (quintals)")
    factor = _QUINTALS_PER.get((trip.quantity_unit or "").strip().lower())
    if factor is None:
        raise MissingFigure(f"quantity in quintals (unit {trip.quantity_unit!r} not convertible)")
    return Decimal(trip.quantity) * Decimal(factor)


def _multiplier(unit: RateUnit, base: Base, trip, agreement, figures) -> Decimal:
    if unit == RateUnit.per_quintal_day:
        return quintals(trip)
    if unit == RateUnit.flat_per_day:
        return Decimal(1)
    return base_amount(base, trip, agreement, figures)


def _is_pct(unit: RateUnit, base: Base) -> bool:
    return unit in PCT_UNITS or (unit == RateUnit.per_instance and Base(base) != Base.none)


def count_units(unit: RateUnit, start: Optional[datetime], end: Optional[datetime], *, day_count: DayCount,
                part_full: bool, holidays: Iterable[date] = (), instances: Decimal = Decimal(1)) -> Decimal:
    """Days, weeks or instances the rate is multiplied by."""
    if unit == RateUnit.per_instance:
        if start is not None and end is not None and end <= start:
            return Decimal(0)
        return Decimal(instances)
    if start is None or end is None:
        raise MissingFigure("start or end time of the delay")
    days = working_days(start, end, holidays) if day_count == DayCount.working else calendar_days(start, end)
    if days <= 0:
        return Decimal(0)
    if unit == RateUnit.pct_per_week_or_part:
        return Decimal(math.ceil(days / 7))
    return Decimal(math.ceil(days)) if part_full else Decimal(math.floor(days))


@dataclass(frozen=True)
class _State:
    rate: Decimal
    unit: RateUnit
    pct: bool
    mult: Decimal
    units: Decimal

    def figure(self) -> Decimal:
        r = self.rate / 100 if self.pct else self.rate
        return r * self.mult * self.units


def _cits(c: Optional[Correction]) -> list[Citation]:
    if c is None or not isinstance(c.value, dict):
        return []
    return [Citation.model_validate(x) for x in c.value.get("citations", [])]


def _dt(x: Any) -> datetime:
    return x if isinstance(x, datetime) else datetime.fromisoformat(str(x))


def _fmt(dt: Optional[datetime]) -> str:
    return to_ist(dt).strftime("%d %b %Y %H:%M") if dt else "?"


# --------------------------------------------------------------------------- recompute

def recompute(deduction: Deduction, trip: Optional[Trip], terms: list[Term], corrections: list[Correction],
              *, agreement=None, figures: Optional[dict] = None) -> tuple[Decimal, list[RecomputeLine]]:
    """Correct penalty and the line-by-line recomputation. Raises MissingFigure."""
    deducted = Decimal(deduction.amount)
    corrections = sorted(corrections or [], key=lambda c: (c.order, ORDER.get(c.kind, 99)))
    head = [RecomputeLine(step="deducted", description="Amount deducted by the client", value=q(deducted))]
    voids = [c for c in corrections if c.kind == "void"]
    if voids:
        v = voids[0]
        label = (v.value or {}).get("label") or f"{(v.value or {}).get('ground', 'ground')}: no penalty is due"
        return Decimal("0.00"), head + [
            RecomputeLine(step="void", description=label, value=Decimal("0.00"), citations=_cits(v)),
            RecomputeLine(step="result", description="Correct penalty", value=Decimal("0.00"))]

    d = trip_date(deduction, trip)
    by_kind: dict[str, list[Correction]] = {}
    for c in corrections:
        by_kind.setdefault(c.kind, []).append(c)
    forced = (by_kind.get("rate") or [None])[0]
    forced_id = (forced.value or {}).get("term_id") if forced else None
    rules = [t for t in terms if t.term_id == forced_id] if forced_id else rules_for(terms, deduction.reason_code, d)
    if not rules:
        raise MissingFigure(f"confirmed penalty rule for {deduction.reason_code.value} in force on {d.isoformat()}")

    best: Optional[tuple[Decimal, list[RecomputeLine]]] = None
    readings: list[str] = []
    for rule in rules:
        p = rule.typed_params()
        parts = [True, False] if (p.part_unit_counts_as_full is None and p.unit in DAY_UNITS) \
            else [p.part_unit_counts_as_full is not False]
        for part in parts:
            val, lines = _one_reading(deduction, trip, terms, by_kind, rule, part, d, agreement, figures)
            readings.append(f"{rule.clause_ref} part-day {'counts' if part else 'ignored'}: {q(val)}")
            if best is None or val > best[0]:
                best = (val, lines)
    val, lines = best
    if len(readings) > 1:
        lines.append(RecomputeLine(step="readings", value=q(val),
                                   description="Clause silent: every permitted reading computed, highest taken ("
                                               + "; ".join(readings) + ")"))
    final = q(val)
    lines.append(RecomputeLine(step="result", description="Correct penalty", value=final))
    return final, head + lines


def _one_reading(deduction, trip, terms, by_kind, rule: Term, part_full: bool, d: date, agreement, figures):
    p = rule.typed_params()
    rule_cits = [timeline.cite(timeline.term_evidence(rule, agreement))]
    cc = deduction.client_calc
    lines: list[RecomputeLine] = []

    # endpoints: client's own timestamps first, else the governing records
    frm_ev, to_ev, def_name = DEFAULT_SPAN.get(deduction.reason_code, (None, None, None))
    frm_ev = timeline.norm_event(p.measured_from) or frm_ev
    to_ev = timeline.norm_event(p.measured_to) or definition_event(terms, def_name, d) or to_ev

    def rec(ev):
        if trip is None or ev is None:
            return None
        if ev in timeline.FIXED_FIELD:
            return timeline.fixed(trip, ev)
        o = timeline.pick(trip, ev)
        return o.value if o else None

    start = cc.from_ts if cc and cc.from_ts else rec(frm_ev)
    end = cc.to_ts if cc and cc.to_ts else rec(to_ev)
    day_count = p.day_count or DayCount.calendar  # silent -> calendar is the highest permitted reading
    hol = set()
    dc_corr = (by_kind.get("day_count") or [None])[0]
    if day_count == DayCount.working:
        hol = set(holidays_from(terms, d) or set())
        if dc_corr and isinstance(dc_corr.value, dict):
            hol |= {date.fromisoformat(str(x)) for x in dc_corr.value.get("holidays", [])}
    instances = Decimal(cc.units_counted) if (cc and cc.units_counted and p.unit == RateUnit.per_instance) else Decimal(1)

    def units_for(unit, s, e, keep=None):
        if (s is None or e is None) and unit != RateUnit.per_instance and keep is not None:
            return keep  # no timestamps held: the client's own count stands (never disputed)
        return count_units(unit, s, e, day_count=day_count, part_full=part_full, holidays=hol, instances=instances)

    # step 0: the client's own calculation (missing parts filled from the clause)
    c_unit = RateUnit(cc.unit) if cc and cc.unit else p.unit
    c_rate = Decimal(cc.rate) if cc and cc.rate is not None else Decimal(p.rate)
    if cc and cc.base_amount is not None:
        c_mult = Decimal(cc.base_amount)
    else:
        c_mult = _multiplier(p.unit, p.base, trip, agreement, figures)
    if cc and cc.units_counted is not None:
        c_units = Decimal(cc.units_counted)
    else:
        c_units = units_for(c_unit, start, end)
    st = _State(rate=c_rate, unit=c_unit, pct=_is_pct(c_unit, p.base), mult=c_mult, units=c_units)
    if cc is not None:
        lines.append(RecomputeLine(step="client_calc", value=q(st.figure()),
                                   description=f"Client's calculation: rate {st.rate} ({st.unit.value}) x base "
                                               f"{st.mult} x {st.units} units"))

    # step 1: rate in force (and its unit)
    corr = (by_kind.get("rate") or [None])[0]
    if st.rate != Decimal(p.rate) or st.unit != p.unit or corr:
        units = st.units if st.unit == p.unit else units_for(p.unit, start, end, keep=st.units)
        st = replace(st, rate=Decimal(p.rate), unit=p.unit, pct=_is_pct(p.unit, p.base), units=units)
        lines.append(RecomputeLine(step="rate", value=q(st.figure()), citations=_cits(corr) or rule_cits,
                                   description=f"Rate in force on {d.isoformat()}: {p.rate} ({p.unit.value}), "
                                               f"clause {rule.clause_ref}"))
    # step 2: base the clause names
    corr = (by_kind.get("base") or [None])[0]
    mult = _multiplier(p.unit, p.base, trip, agreement, figures)
    if mult != st.mult or corr:
        st = replace(st, mult=mult)
        lines.append(RecomputeLine(step="base", value=q(st.figure()), citations=_cits(corr) or rule_cits,
                                   description=f"Base named by clause {rule.clause_ref}: {p.base.value} = {mult}"))
    # step 3: day count by the clause's rule, on the client's timestamps
    units = units_for(p.unit, start, end, keep=st.units if cc and cc.units_counted is not None else None)
    if units != st.units or dc_corr:
        st = replace(st, units=units)
        lines.append(RecomputeLine(step="day_count", value=q(st.figure()), citations=_cits(dc_corr) or rule_cits,
                                   description=f"Units counted by the clause ({day_count.value} days, part day "
                                               f"{'counts' if part_full else 'ignored'}) from {_fmt(start)} to "
                                               f"{_fmt(end)}: {units}"))
    # step 4: fact corrections (governing records replace the client's timestamps)
    for corr in by_kind.get("timestamp", []):
        v = corr.value or {}
        if v.get("which") == "from":
            start = _dt(v["at"])
        else:
            end = _dt(v["at"])
        st = replace(st, units=units_for(p.unit, start, end))
        lines.append(RecomputeLine(step="timestamp", value=q(st.figure()), citations=_cits(corr),
                                   description=(v.get("label") or f"{v.get('which')} time corrected")
                                   + f": now {_fmt(start)} to {_fmt(end)}, {st.units} units"))
    # step 5: union of excused intervals shifts the due time
    exc = by_kind.get("excused_interval", [])
    if exc:
        ivs = [(_dt(c.value["start"]), _dt(c.value["end"])) for c in exc]
        shift = union_length(ivs)
        if start is not None:
            start = start + shift
            st = replace(st, units=units_for(p.unit, start, end))
        cits = [x for c in exc for x in _cits(c)]
        labels = "; ".join((c.value or {}).get("label", "excused interval") for c in exc)
        lines.append(RecomputeLine(step="excused_interval", value=q(st.figure()), citations=cits,
                                   description=f"Excused time (union, counted once) {shift}: {labels}. "
                                               f"Due time moves to {_fmt(start)}; {st.units} units"))
    # step 6: caps (every confirmed cap in force; scope does not change a single trip's ceiling)
    val = st.figure()
    cap_corr = (by_kind.get("cap") or [None])[0]
    for cap_t in caps_for(terms, d):
        cp = cap_t.typed_params()
        ceiling = Decimal(cp.value) if cp.kind == "abs" else Decimal(cp.value) / 100 * base_amount(cp.base, trip, agreement, figures)
        if val > ceiling or (cap_corr and (cap_corr.value or {}).get("term_id") == cap_t.term_id):
            scope = cp.scope or "unstated (per-trip and aggregate readings give the same ceiling for one trip)"
            val = min(val, ceiling)
            lines.append(RecomputeLine(step="cap", value=q(val),
                                       citations=_cits(cap_corr) or [timeline.cite(timeline.term_evidence(cap_t, agreement))],
                                       description=f"Cap {cp.value}{'%' if cp.kind == 'pct' else ''} of "
                                                   f"{cp.base.value} (scope {scope}) = {q(ceiling)}"))
    return val, lines
