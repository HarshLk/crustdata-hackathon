"""Intake: deterministic clause extraction with page + verbatim, verbatim check rejects, annexure -> missing document (case 14)."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from pdr.codes import ActionKind, Base, EventType, RateUnit, ReasonCode
from pdr.contracts import RunContext
from pdr.intake import confirm as cf
from pdr.intake.extract import propose_terms, run_intake, _term
from pdr.intake.pdftext import page_texts
from pdr.models import Action, Agreement, Client, Term
from pdr.settings import load_settings
from pdr.store_mem import MemStore

ROOT = Path(__file__).resolve().parents[3]

AGREEMENT = """TRANSPORT SERVICE AGREEMENT
This agreement is effective from 01-04-2026 between Acme Foods Private Limited and the Service Provider.

15. Penalty for delay
(iii) If the Service Provider fails to deliver within the delivery period, the Buyer shall deduct liquidated damages @ 0.5% of the contract value of delayed quantity per week or part thereof, subject to a maximum of 5% of the contract value.
--- page 2 ---
16. Force Majeure
Neither party shall be liable for delay caused by force majeure, which means war, epidemics, civil commotion, fire, floods, strikes, lockouts and acts of God. Force majeure shall not include negligence of the Service Provider or bandh called by transporters' unions. The Service Provider shall notify the Buyer in writing within 10 days from the date of occurrence thereof, with supporting documents.

17. Disputes
The Service Provider may dispute any deduction within 30 days from the date of receipt of the deduction advice.

18. Detention
Detention charges shall be payable as per Annexure C.
"""


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("PDR_DATA_DIR", str(tmp_path / "data"))
    s = MemStore()
    s.put(Client(client_id="ACME", legal_name="Acme Foods Private Limited"))
    return RunContext(run_id="t", dispute_key=None, settings=load_settings(ROOT / "settings.yaml"), store=s,
                      deadline=datetime.now(timezone.utc) + timedelta(hours=1))


def _write(tmp_path):
    p = tmp_path / "acme_sla.txt"
    p.write_text(AGREEMENT)
    return p


def test_deterministic_terms_with_page_and_verbatim(tmp_path):
    pages = page_texts(_write(tmp_path))
    assert len(pages) == 2
    res = propose_terms("ACME", "AG", pages, date(2026, 4, 1))
    by = {}
    for t in res.terms:
        by.setdefault(t.term_type, []).append(t)
        assert t.verbatim.strip() in pages[t.page - 1] or t.verbatim.split()[0] in pages[t.page - 1]
        assert t.status == "unconfirmed"
    pen = by["penalty_rule"][0].typed_params()
    assert (pen.rate, pen.unit, pen.base, pen.event, pen.part_unit_counts_as_full) == (
        Decimal("0.5"), RateUnit.pct_per_week_or_part, Base.delayed_quantity_value, ReasonCode.late_delivery, True)
    assert by["penalty_rule"][0].page == 1 and by["penalty_rule"][0].clause_ref in ("(iii)", "15")
    cap = by["cap"][0].typed_params()
    assert (cap.value, cap.kind, cap.base) == (Decimal("5"), "pct", Base.contract_value)
    fm = by["force_majeure"][0].typed_params()
    assert EventType.strike in fm.events_named and EventType.flood in fm.events_named
    assert EventType.bandh in fm.events_excluded and EventType.bandh not in fm.events_named
    assert "civil commotion" in fm.general_words and by["force_majeure"][0].page == 2
    notice = by["notice"][0].typed_params()
    assert notice.deadline_hours == 240 and notice.applies_to == "force_majeure"
    tl = by["time_limit"][0].typed_params()
    assert (tl.window_days, tl.counted_from) == (30, "received_date")
    assert len(res.missing_documents) == 1 and res.missing_documents[0].startswith("Annexure C")


def test_verbatim_not_on_page_is_rejected(tmp_path):
    pages = page_texts(_write(tmp_path))
    fake = _term("ACME", "AG", "cap", {"value": "10", "kind": "pct", "base": "contract_value"}, 1, "15",
                 "subject to a maximum of 10% of the contract value", date(2026, 4, 1), 0.9)
    wrong_page = _term("ACME", "AG", "cap", {"value": "5", "kind": "pct", "base": "contract_value"}, 2, "15",
                       "subject to a maximum of 5% of the contract value", date(2026, 4, 1), 0.9)
    res = propose_terms("ACME", "AG", pages, date(2026, 4, 1), extra=[fake, wrong_page])
    assert {r["term_id"] for r in res.rejected} == {fake.term_id, wrong_page.term_id}
    assert all(t.term_id not in (fake.term_id, wrong_page.term_id) for t in res.terms)


def test_run_intake_confirm_complete_set(ctx, tmp_path):
    p = _write(tmp_path)
    [(ag, res)] = run_intake("ACME", [str(p)], ctx, who="kam1")
    s = ctx.store
    assert ag.effective_from == date(2026, 4, 1) and ag.completeness == "missing"
    stored = s.get(Agreement, ag.agreement_id)
    assert stored.missing_documents and "Annexure C" in stored.missing_documents[0]
    reqs = s.find(Action, kind=ActionKind.document_request)
    assert len(reqs) == 1 and "Annexure C" in Path(reqs[0].body_ref).read_text()
    assert (tmp_path / "data" / "agreements" / "ACME" / "acme_sla.txt").exists()
    terms = s.find(Term, client_id="ACME")
    assert terms and all(t.status == "unconfirmed" for t in terms)
    changed, skipped = cf.confirm(s, "ACME", "kam1", all_=True)
    assert changed and all(t.confirmed_by == "kam1" and t.confirmed_at for t in changed)
    assert not cf.unconfirmed(s, "ACME") or all(t.extraction_confidence < cf.LOW_CONFIDENCE for t in cf.unconfirmed(s, "ACME"))
    assert "page" in cf.render(changed[0]) and changed[0].verbatim[:20] in cf.render(changed[0])
    done, missing = cf.complete_set(s, "ACME", "kam1")
    assert not done and missing  # Annexure C still missing -> set stays incomplete
    events = [r["event"] for r in s.trail()]
    assert "intake" in events and "term_confirmed" in events and "complete_set" in events and s.trail_verify()


def test_annexure_sentence_terms_low_confidence(tmp_path):
    pages = ["9. Penalty\nLate delivery penalty of 1% of the trip cost per day as per Annexure B."]
    res = propose_terms("ACME", "AG", pages, date(2026, 4, 1))
    assert res.terms and all(t.extraction_confidence < 0.5 for t in res.terms)
    assert res.missing_documents and res.missing_documents[0].startswith("Annexure B")
