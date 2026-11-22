"""Small builders for b2's engine unit tests (sample terms only; no real contract values in code)."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from pdr.codes import ReasonCode
from pdr.models import Agreement, ClientCalc, Deduction, Observation, SourceRef, Term, Trip, make_event_fp

IST = timezone(timedelta(hours=5, minutes=30))
ROOT = Path(__file__).resolve().parents[3]


def ist(y, m, d, hh=0, mm=0) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=IST)


def obs(dt, source, row=1, file="trips.xlsx"):
    return Observation(value=dt, source=source, ref=SourceRef(file=file, row=row))


def term(term_type, params, term_id=None, *, eff_from=date(2026, 1, 1), eff_to=None, status="confirmed",
         clause="8.1", page=3, verbatim="sample clause text", agreement_id="AG1"):
    return Term(term_id=term_id or f"T-{term_type}", agreement_id=agreement_id, client_id="ACME",
                term_type=term_type, params=params, page=page, clause_ref=clause, verbatim=verbatim,
                effective_from=eff_from, effective_to=eff_to, status=status)


def rule(rate, unit, base, *, part=True, day_count="calendar", event="late_delivery", **kw):
    p = {"event": event, "rate": str(rate), "unit": unit, "base": base, "day_count": day_count,
         "part_unit_counts_as_full": part}
    return term("penalty_rule", p, kw.pop("term_id", "T-rule"), **kw)


def cap(value, base, kind="pct", scope=None, **kw):
    return term("cap", {"value": str(value), "kind": kind, "base": base, "scope": scope},
                kw.pop("term_id", "T-cap"), **kw)


def agreement(**kw):
    base = dict(agreement_id="AG1", client_id="ACME", doc_type="SLA", title="Sample transport SLA",
                file_ref="agreements/acme_sla.pdf", effective_from=date(2026, 1, 1))
    base.update(kw)
    return Agreement(**base)


def trip(**kw):
    base = dict(trip_id="TRIP1", client_id="ACME", lr_no="LR100", origin="Pune", destination="Kolhapur",
                lane="PNQ-KOP", vehicle_no="MH12AB1234", freight=Decimal("40000"))
    base.update(kw)
    return Trip(**base)


def deduction(amount, *, reason=ReasonCode.late_delivery, calc=None, advice="PA1", line="1",
              ded_date=date(2026, 11, 15), trip_id="TRIP1", **kw):
    return Deduction(deduction_id=f"D-{advice}-{line}", dispute_key=f"ACME|{advice}|{line}",
                     event_fp=make_event_fp("ACME", trip_id, reason), client_id="ACME",
                     client_name_raw="Acme Ltd", advice_no=advice, line_no=line, deduction_date=ded_date,
                     amount=Decimal(str(amount)), reason_text="late delivery", reason_code=reason,
                     trip_refs=[trip_id], client_calc=ClientCalc(**calc) if calc else None, **kw)


def settings():
    from pdr.settings import load_settings
    return load_settings(ROOT / "settings.yaml")
