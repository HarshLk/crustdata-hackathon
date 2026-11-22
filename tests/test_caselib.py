"""Every case folder loads into MemStore, validates against the models, and has a consistent expected.json."""
from __future__ import annotations

import json
from decimal import Decimal

import pytest
import yaml

from pdr.codes import Flag, Ground, Outcome, Reason
from pdr.models import Deduction, Term, make_dispute_key
from pdr.store_mem import MemStore
from tests.caselib import EXPECTED_KEYS, case_dirs, load_case, read_case

CASES = case_dirs()
FREE_KINDS = {"free_check"}


def test_there_are_cases():
    assert CASES, "no case folders found"


@pytest.mark.parametrize("case_dir", CASES, ids=lambda p: p.name)
def test_case_loads(case_dir):
    store = MemStore()
    keys, expected, overrides = load_case(case_dir, store)
    assert keys and len(set(keys)) == len(keys)
    assert set(expected) == set(keys), "expected.json keys must equal the deductions' dispute keys"
    for k in keys:
        assert store.get(Deduction, k) is not None
    assert isinstance(overrides, dict)


@pytest.mark.parametrize("case_dir", CASES, ids=lambda p: p.name)
def test_case_files_are_consistent(case_dir):
    c = read_case(case_dir)
    meta = c["meta"]
    for f in ("case.yaml", "deductions.json", "deductions.csv", "expected.json", "NOTES.md"):
        assert (case_dir / f).exists(), f
    client_ids = {x.client_id for x in c["clients"]}
    for d in c["deductions"]:
        assert d.dispute_key == make_dispute_key(d.client_id, d.advice_no, d.line_no, d.client_deduction_no)
        assert d.deduction_date.year == 2026 and d.deduction_date.month >= 9
    raw_trips = (case_dir / "trips.json").read_text(encoding="utf-8")
    assert "driver_name" not in raw_trips and "driver_phone" not in raw_trips
    for t in c["trips"]:
        if t.delivery_due_at:
            assert t.delivery_due_at.isoformat() >= "2026-09-01"
    for term in c["terms"]:
        assert term.status == "confirmed"
        term.typed_params()  # validates params against the term type
        assert term.verbatim and term.clause_ref and term.page
    for a in c["agreements"]:
        assert a.is_stand_in, f"{a.agreement_id} must be labelled stand-in"
    for key, e in c["expected"].items():
        assert set(e) == EXPECTED_KEYS, key
        Outcome(e["outcome"])
        [Ground(g) for g in e["grounds"]]
        [Flag(f) for f in e["flags"]]
        if e["primary_reason"]:
            Reason(e["primary_reason"])
        Decimal(e["amount_disputable"])
        if e["outcome"] == "disputable":
            assert e["grounds"] and Decimal(e["amount_disputable"]) > 0
        else:
            assert Decimal(e["amount_disputable"]) == 0
        if e["outcome"] == "cannot_verify":
            assert e["primary_reason"], key
        if meta.get("kind") in FREE_KINDS:
            assert e["max_paid_calls"] == 0, f"{key}: free-check case must allow no paid call"
    notes = (case_dir / "NOTES.md").read_text(encoding="utf-8")
    for e in c["expected"].values():
        amt = Decimal(e["amount_disputable"])
        if amt:
            assert f"{amt:,.2f}" in notes, f"NOTES.md must show the arithmetic for {amt:,.2f}"
    assert client_ids or meta.get("kind") == "lookalike"
