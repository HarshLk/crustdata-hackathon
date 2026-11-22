"""Stage 5: assertion, notice window, FM wording; PRD cases 12b, 12c, 12d with zero paid calls."""
from __future__ import annotations

import pytest

from pdr.codes import EventType, Flag, Outcome, Reason
from pdr.engine import run
from pdr.engine.checks_notice import ARGUABLE, COVERED, EXCLUDED, NO_CLAUSE, NOT_COVERED, fm_wording
from pdr.contracts import EvidenceResult

from tests.unit.engine.fixtures_b1 import (
    FakeFinder, ctx, deduction, fm_excludes, fm_general, fm_named, notice_term, penalty_term, stub_free_checks, trip,
    unregister, world,
)


@pytest.fixture(autouse=True)
def finder(monkeypatch):
    import pdr.evidence as ev
    f = FakeFinder(lambda req: EvidenceResult(status="none"))
    monkeypatch.setattr(ev, "find_evidence", f)
    ids = stub_free_checks()
    yield f
    unregister(ids)


def test_fm_wording_table():
    assert fm_wording(fm_named(), EventType.blockade) == COVERED
    assert fm_wording(fm_excludes(), EventType.blockade) == EXCLUDED
    assert fm_wording(fm_general(), EventType.blockade) == ARGUABLE
    assert fm_wording(fm_named(), EventType.flood) == NOT_COVERED
    assert fm_wording(None, EventType.blockade) == NO_CLAUSE


def test_case_12b_exclusion_is_valid_with_admission_flag(finder):
    store, keys = world(terms=[penalty_term(), fm_excludes(), notice_term()])
    v = run(keys, ctx(store))[0]
    assert v.outcome == Outcome.valid and Flag.CLIENT_ADMISSION_CHECK in v.flags
    assert finder.calls == []


def test_case_12c_notice_missing_no_search(finder):
    store, keys = world(terms=[penalty_term(), fm_named(), notice_term(hours=24)], trips=[trip(notice=False)])
    v = run(keys, ctx(store))[0]
    assert v.outcome == Outcome.cannot_verify and v.primary_reason == Reason.NOTICE_EVIDENCE_MISSING
    assert "notice" in v.missing_items[0].lower()
    assert finder.calls == []


def test_notice_late_is_missing(finder):
    from pdr.models import NoticeRecord, SourceRef
    from tests.unit.engine.fixtures_b1 import ist
    t = trip(notice=False).model_copy(update={"notices": [NoticeRecord(sent_at=ist(7, 9), channel="email",
                                                                       addressee="x", ref=SourceRef(file="m.eml"))]})
    store, keys = world(terms=[penalty_term(), fm_named(), notice_term(hours=24)], trips=[t])
    v = run(keys, ctx(store))[0]
    assert v.primary_reason == Reason.NOTICE_EVIDENCE_MISSING and finder.calls == []


def test_case_12d_general_words_refer_no_search(finder):
    store, keys = world(terms=[penalty_term(), fm_general(), notice_term()])
    v = run(keys, ctx(store))[0]
    assert v.outcome == Outcome.cannot_verify and v.primary_reason == Reason.FM_WORDING_ARGUABLE
    assert "civil commotion" in v.missing_items[0]
    assert finder.calls == []


def test_no_fm_clause_refers(finder):
    store, keys = world(terms=[penalty_term(), notice_term()])
    v = run(keys, ctx(store))[0]
    assert v.primary_reason == Reason.NO_FM_CLAUSE and finder.calls == []


def test_asserted_covered_searches_in_asserted_mode(finder):
    store, keys = world()
    v = run(keys, ctx(store))[0]
    assert len(finder.calls) == 1 and finder.calls[0].mode == "asserted"
    assert EventType.blockade in finder.calls[0].event_types
    assert v.primary_reason == Reason.NO_EVIDENCE_FOUND


def test_not_asserted_discovery_none_is_valid(finder):
    store, keys = world(trips=[trip(notice=False, assertion=False)])
    v = run(keys, ctx(store))[0]
    assert len(finder.calls) == 1 and finder.calls[0].mode == "discovery"
    assert v.outcome == Outcome.valid


def test_not_asserted_no_named_events_no_search(finder):
    store, keys = world(terms=[penalty_term(), fm_general()], trips=[trip(notice=False, assertion=False)])
    v = run(keys, ctx(store))[0]
    assert finder.calls == [] and v.outcome == Outcome.valid


def test_no_delay_skips_fm(finder):
    from tests.unit.engine.fixtures_b1 import deduction as d
    store, keys = world(deds=[d(code="shortage", reason="shortage")])
    run(keys, ctx(store))
    assert finder.calls == []
