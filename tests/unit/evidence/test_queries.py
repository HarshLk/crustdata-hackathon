"""A6: the query ladder is allow-listed: route places, states and event words only; never location."""
from __future__ import annotations

from datetime import datetime, timedelta

from pdr.contracts import EvidenceRequest
from pdr.evidence.queries import event_words, ladder, plan, search_window

from .conftest import IST

W = (datetime(2026, 11, 4, 8, tzinfo=IST), datetime(2026, 11, 5, 20, tzinfo=IST))


def _req(**kw):
    base = dict(route_places=["Pune", "Satara", "Kini", "Kolhapur"], route_states=["Maharashtra"], window=W,
                event_types=["blockade", "chakka_jam"])
    base.update(kw)
    return EvidenceRequest(**base)


def test_every_query_is_place_plus_event_word(settings):
    req = _req()
    words = event_words(req, settings)
    names = set(req.route_places) | set(req.route_states) | set(settings.evidence.regional_state_names.values())
    regional = {w for ws in settings.evidence.regional_event_words.values() for w in ws}
    qs = ladder(req, settings)
    assert qs
    for q in qs:
        assert any(q.text == f"{n} {w}" for n in names for w in set(words) | regional), q.text
        assert set(q.request_fields()) == {"query", "start", "end"}  # no location, ever


def test_order_window_and_rungs(settings):
    qs = ladder(_req(), settings)
    assert qs[0].text == "Satara blockade" and qs[0].rung == 1  # intermediate places first
    assert [q.rung for q in qs] == sorted(q.rung for q in qs)
    assert any(q.text == "Maharashtra chakka jam" and q.rung == 2 for q in qs)
    assert any(q.rung == 3 and q.text.startswith("महाराष्ट्र") for q in qs)
    allow = timedelta(hours=settings.evidence.window_allowance_hours)
    assert search_window(_req(), settings) == (W[0] - allow, W[1] + allow)
    assert all((q.start, q.end) == (W[0] - allow, W[1] + allow) for q in qs)


def test_regional_rung_skipped_without_names(settings):
    settings.evidence.regional_query = False
    assert all(q.rung < 3 for q in ladder(_req(), settings))


def test_only_requested_event_types(settings):
    qs = ladder(_req(event_types=["flood"]), settings)
    assert qs and all(q.text.endswith(" flood") for q in qs if q.rung < 3)
    assert ladder(_req(event_types=["other"]), settings) == []


def test_plan_respects_max_searches_and_keeps_state_rung(settings):
    p = plan(_req(), settings)
    assert len(p) == settings.evidence.max_searches_per_deduction
    assert {q.rung for q in p} == {1, 2, 3}
