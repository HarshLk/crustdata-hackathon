"""recompute(): hand-worked figures for every unit, base, cap and the D-05/D-06/D-12 rules."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from pdr.codes import ReasonCode, TsSource
from pdr.engine.penalty import MissingFigure, amount_disputable, correction, recompute

from .b2_fixtures import agreement, cap, deduction, ist, obs, rule, term, trip

D = Decimal


def _trip(due, arrived, **kw):
    return trip(delivery_due_at=due, arrival_at=[obs(arrived, TsSource.consignee_gate_log)], **kw)


def steps(lines):
    return [l.step for l in lines]


# ------------------------------------------------------------------ GeM SLA: 1% trip cost / vehicle / day, cap 15%

def test_gem_sla_within_cap():
    # 3 days late (5 Nov 10:00 -> 8 Nov 10:00); 1% x 50,000 x 3 = 1,500; cap 15% x 50,000 = 7,500 not reached
    t = _trip(ist(2026, 11, 5, 10), ist(2026, 11, 8, 10), freight=D("50000"))
    terms = [rule(1, "pct_per_vehicle_day", "trip_cost"), cap(15, "trip_cost", scope="per_vehicle")]
    val, lines = recompute(deduction(1500), t, terms, [])
    assert val == D("1500.00")
    assert "cap" not in steps(lines)


def test_gem_sla_capped():
    # 20 days late; 1% x 50,000 x 20 = 10,000 > cap 15% x 50,000 = 7,500 -> 7,500
    t = _trip(ist(2026, 10, 10, 10), ist(2026, 10, 30, 10), freight=D("50000"))
    terms = [rule(1, "pct_per_vehicle_day", "trip_cost"), cap(15, "trip_cost", scope="per_vehicle")]
    val, lines = recompute(deduction(10000), t, terms, [])
    assert val == D("7500.00")
    assert lines[-2].step == "cap" and lines[-2].citations[0].kind == "contract_term"


# ------------------------------------------------------------------ GeM GTC: 0.5% / week or part on delayed-qty value, cap 5%

def test_gem_gtc_week_or_part():
    # 10 days late -> 2 weeks (part week counts); 0.5% x 4,00,000 x 2 = 4,000
    t = _trip(ist(2026, 10, 1, 12), ist(2026, 10, 11, 12), delayed_quantity_value=D("400000"))
    terms = [rule("0.5", "pct_per_week_or_part", "delayed_quantity_value", part=None),
             cap(5, "delayed_quantity_value")]
    val, _ = recompute(deduction(4000), t, terms, [])
    assert val == D("4000.00")


def test_gem_gtc_capped_on_contract_value():
    # 80 days -> ceil(80/7) = 12 weeks; 0.5% x 4,00,000 x 12 = 24,000
    # cap 5% of contract value 3,00,000 = 15,000 -> 15,000
    t = _trip(ist(2026, 9, 1, 12), ist(2026, 11, 20, 12), delayed_quantity_value=D("400000"))
    terms = [rule("0.5", "pct_per_week_or_part", "delayed_quantity_value"), cap(5, "contract_value")]
    val, _ = recompute(deduction(24000), t, terms, [], agreement=agreement(contract_value=D("300000")))
    assert val == D("15000.00")


def test_cap_needs_contract_value():
    t = _trip(ist(2026, 9, 1, 12), ist(2026, 11, 20, 12), delayed_quantity_value=D("400000"))
    terms = [rule("0.5", "pct_per_week_or_part", "delayed_quantity_value"), cap(5, "contract_value")]
    with pytest.raises(MissingFigure) as e:
        recompute(deduction(24000), t, terms, [], agreement=agreement())
    assert "contract value" in e.value.name


# ------------------------------------------------------------------ flat, quintal-day, per instance

def test_flat_per_day_part_counts():
    # 2 days 3 hours late, part day counts -> 3 days x Rs 2,000 = 6,000
    t = _trip(ist(2026, 11, 5, 9), ist(2026, 11, 7, 12))
    val, _ = recompute(deduction(6000), t, [rule(2000, "flat_per_day", "none")], [])
    assert val == D("6000.00")


def test_flat_per_day_part_ignored():
    # 2 days 3 hours, clause ignores part days -> 2 x 2,000 = 4,000
    t = _trip(ist(2026, 11, 5, 9), ist(2026, 11, 7, 12))
    val, _ = recompute(deduction(6000), t, [rule(2000, "flat_per_day", "none", part=False)], [])
    assert val == D("4000.00")


def test_per_quintal_day():
    # 25 tonnes = 250 quintals; 2 days late; Rs 10 x 250 x 2 = 5,000
    t = _trip(ist(2026, 11, 5, 9), ist(2026, 11, 7, 9), quantity=D("25"), quantity_unit="tonne")
    val, _ = recompute(deduction(5000), t, [rule(10, "per_quintal_day", "none")], [])
    assert val == D("5000.00")


def test_per_instance_flat_and_pct():
    t = _trip(ist(2026, 11, 5, 9), ist(2026, 11, 5, 11))
    # Rs 5,000 per instance, one instance
    val, _ = recompute(deduction(5000), t, [rule(5000, "per_instance", "none")], [])
    assert val == D("5000.00")
    # 5% of daily hire 8,000 per instance = 400
    val, _ = recompute(deduction(400), t, [rule(5, "per_instance", "daily_hire")], [], figures={"daily_hire": D("8000")})
    assert val == D("400.00")


def test_missing_base_figure_is_named():
    t = _trip(ist(2026, 11, 5, 9), ist(2026, 11, 7, 9), freight=None)
    with pytest.raises(MissingFigure) as e:
        recompute(deduction(800), t, [rule(1, "pct_per_vehicle_day", "trip_cost")], [])
    assert "trip cost" in e.value.name


# ------------------------------------------------------------------ PRD cases 6, 13, 5

def test_case6_wrong_base_and_over_cap():
    # Clause: 1% of trip cost per vehicle per day, max 15% of trip cost. Trip cost 40,000; invoice 5,00,000; 20 days late.
    # Client: 1% x 5,00,000 x 20 = 1,00,000.
    # Correct base: 1% x 40,000 x 20 = 8,000; cap 15% x 40,000 = 6,000 -> correct 6,000; disputable 94,000.
    t = _trip(ist(2026, 10, 10, 10), ist(2026, 10, 30, 10))
    terms = [rule(1, "pct_per_vehicle_day", "trip_cost"), cap(15, "trip_cost", scope="per_vehicle")]
    calc = {"rate": "1", "unit": "pct_per_vehicle_day", "base_amount": "500000", "units_counted": "20"}
    corr = [correction("base", term_id="T-rule"), correction("cap", term_id="T-cap")]
    val, lines = recompute(deduction(100000, calc=calc), t, terms, corr)
    assert val == D("6000.00")
    by = {l.step: l.value for l in lines}
    assert by["client_calc"] == D("100000.00") and by["base"] == D("8000.00") and by["cap"] == D("6000.00")
    assert amount_disputable(D("100000"), val) == D("94000.00")


def test_case13_old_rate_after_amendment():
    # Old rate 1%/vehicle-day to 30 Jun; amendment 0.5% from 1 Jul. Trip due 5 Nov, 3 days late, trip cost 40,000.
    # Client (old rate): 1% x 40,000 x 3 = 1,200. Correct: 0.5% x 40,000 x 3 = 600. Difference 600.
    t = _trip(ist(2026, 11, 5, 10), ist(2026, 11, 8, 10))
    old = rule(1, "pct_per_vehicle_day", "trip_cost", term_id="T-old", eff_to=date(2026, 6, 30))
    new = rule("0.5", "pct_per_vehicle_day", "trip_cost", term_id="T-new", eff_from=date(2026, 7, 1), clause="8.1A")
    calc = {"rate": "1", "unit": "pct_per_vehicle_day", "base_amount": "40000", "units_counted": "3"}
    val, lines = recompute(deduction(1200, calc=calc), t, [old, new], [correction("rate", term_id="T-new")])
    assert val == D("600.00")
    assert "rate" in steps(lines)


def test_case5_client_caused_loading_shifts_due_time():
    # Reported 3 Nov 08:00, gate-out 17:00 = 9 h loading; SOP free time 4 h -> excused 12:00-17:00 = 5 h.
    # Due 5 Nov 10:00, arrived 7 Nov 12:00 = 50 h -> 3 days x 2,000 = 6,000.
    # Due moves to 15:00 -> 45 h -> 2 days x 2,000 = 4,000. Disputable 2,000.
    t = _trip(ist(2026, 11, 5, 10), ist(2026, 11, 7, 12))
    exc = correction("excused_interval", start=ist(2026, 11, 3, 12), end=ist(2026, 11, 3, 17), label="loading beyond 4 h")
    val, lines = recompute(deduction(6000), t, [rule(2000, "flat_per_day", "none")], [exc])
    assert val == D("4000.00")
    assert lines[-2].step == "excused_interval"


def test_overlapping_excused_intervals_counted_once():
    # Delay 33 h (5 Nov 10:00 -> 6 Nov 19:00); flat 1,000/day, part days ignored.
    # Excuses 10:00-15:00 and 13:00-18:00 on 4 Nov: union 8 h (not 10 h) -> 25 h -> 1 day = 1,000.
    # Double counting would give 23 h -> 0.
    t = _trip(ist(2026, 11, 5, 10), ist(2026, 11, 6, 19))
    excs = [correction("excused_interval", start=ist(2026, 11, 4, 10), end=ist(2026, 11, 4, 15)),
            correction("excused_interval", start=ist(2026, 11, 4, 13), end=ist(2026, 11, 4, 18))]
    val, _ = recompute(deduction(2000), t, [rule(1000, "flat_per_day", "none", part=False)], excs)
    assert val == D("1000.00")


def test_part_day_silent_takes_highest_reading():
    # 30 h late, clause silent on part days: readings 2 days (2,000) and 1 day (1,000) -> 2,000 is correct.
    t = _trip(ist(2026, 11, 5, 10), ist(2026, 11, 6, 16))
    val, lines = recompute(deduction(2000), t, [rule(1000, "flat_per_day", "none", part=None)], [])
    assert val == D("2000.00")
    assert "readings" in steps(lines)


def test_working_days_skip_sunday():
    # Due Sat 7 Nov 10:00, arrived Mon 9 Nov 10:00: calendar 2 days; working: Sat 14 h + Mon 10 h = 1 day.
    # Clause counts working days: 1 x 2,000 = 2,000 (client charged 2 days = 4,000).
    t = _trip(ist(2026, 11, 7, 10), ist(2026, 11, 9, 10))
    calc = {"rate": "2000", "unit": "flat_per_day", "units_counted": "2"}
    val, lines = recompute(deduction(4000, calc=calc), t, [rule(2000, "flat_per_day", "none", day_count="working")], [])
    assert val == D("2000.00")
    assert "day_count" in steps(lines)


def test_listed_holiday_is_excluded():
    # Due Sat 7 Nov 10:00, arrived Tue 10 Nov 10:00; Mon 9 Nov listed as a holiday in the agreement set.
    t = _trip(ist(2026, 11, 7, 10), ist(2026, 11, 10, 10))
    hol = term("holiday_list", {"dates": ["2026-11-09"]}, "T-hol")
    # Sat 14 h + Sun 0 + Mon (holiday) 0 + Tue 10 h = 24 h -> 1 day
    val, _ = recompute(deduction(6000), t, [rule(2000, "flat_per_day", "none", day_count="working"), hol], [])
    assert val == D("2000.00")


def test_void_sets_zero_without_figures():
    val, lines = recompute(deduction(5000), None, [], [correction("void", ground="DUPLICATE", label="already deducted")])
    assert val == D("0.00") and lines[-1].value == 0


def test_client_timestamp_corrected_by_record():
    # Client counted from 4 Nov 10:00 (wrong); record due 5 Nov 10:00; arrived 7 Nov 10:00 -> 2 days x 1,000.
    t = _trip(ist(2026, 11, 5, 10), ist(2026, 11, 7, 10))
    calc = {"from_ts": ist(2026, 11, 4, 10).isoformat()}
    assert recompute(deduction(3000, calc=calc), t, [rule(1000, "flat_per_day", "none")], [])[0] == D("3000.00")
    fix = correction("timestamp", which="from", at=ist(2026, 11, 5, 10), label="due time per trip record")
    assert recompute(deduction(3000, calc=calc), t, [rule(1000, "flat_per_day", "none")], [fix])[0] == D("2000.00")


def test_amount_disputable_clamped():
    assert amount_disputable(D("100"), D("150")) == 0
    assert amount_disputable(D("100"), D("-5")) == D("100")
    assert amount_disputable(D("100"), D("40")) == D("60")
