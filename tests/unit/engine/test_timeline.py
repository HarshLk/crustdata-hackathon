"""Timeline resolver: source priority, conflict allowance, record citations."""
from __future__ import annotations

from pdr.codes import TsSource as S
from pdr.engine.timeline import norm_event, resolve

from .b2_fixtures import ist, obs, settings, trip


def test_first_choice_wins_within_allowance():
    # consignee gate log 10:00 vs GPS 10:40: 40 min within the 60 min allowance -> gate log chosen, no conflict
    t = trip(arrival_at=[obs(ist(2026, 11, 6, 10, 40), S.gps, 2), obs(ist(2026, 11, 6, 10), S.consignee_gate_log, 1)])
    tl = resolve(t, settings())
    assert tl.value("arrival") == ist(2026, 11, 6, 10) and not tl.conflicted
    assert tl.citations("arrival")[0].label.startswith("Consignee gate log")


def test_fallback_beyond_allowance_conflicts_and_keeps_both():
    t = trip(arrival_at=[obs(ist(2026, 11, 6, 10), S.consignee_gate_log, 1), obs(ist(2026, 11, 6, 14), S.gps, 2)])
    tl = resolve(t, settings())
    assert tl.conflicted == {"arrival"} and len(tl.evidence("arrival")) == 2
    assert "disagree" in tl.conflict_text("arrival")


def test_client_statement_never_conflicts_and_is_last_resort():
    t = trip(reporting_at=[obs(ist(2026, 11, 3, 15), S.client_stated), obs(ist(2026, 11, 3, 8), S.driver_report)])
    tl = resolve(t, settings())
    assert tl.value("reporting") == ist(2026, 11, 3, 8) and not tl.conflicted


def test_fixed_events_and_names():
    t = trip(delivery_due_at=ist(2026, 11, 5, 10))
    tl = resolve(t, settings())
    assert tl.value("delivery_due") == ist(2026, 11, 5, 10) and tl.evidence("delivery_due")[0].field == "delivery_due_at"
    assert norm_event("arrival_at") == "arrival" and norm_event("gate_in") == "reporting" and norm_event("x") is None
