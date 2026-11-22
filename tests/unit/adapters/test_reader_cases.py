"""Every test-case folder read through the real adapters gives the same deductions as tests/caselib (keys and core fields)."""
from __future__ import annotations

import pytest

from pdr.adapters import reader
from pdr.models import Deduction
from pdr.store_mem import MemStore

caselib = pytest.importorskip("tests.caselib")
CORE = ("client_id", "amount", "deduction_date", "trip_refs", "reason_code", "event_fp", "advice_no", "line_no")


@pytest.mark.parametrize("case_dir", caselib.case_dirs(), ids=lambda p: p.name)
def test_reader_matches_caselib(case_dir, ctx):
    deds = {d.dispute_key: d for d in reader.load_request(case_dir, ctx)}
    ref = MemStore()
    keys, _, _ = caselib.load_case(case_dir, ref)
    assert set(deds) == set(keys)
    for k in keys:
        want, got = ref.get(Deduction, k), deds[k]
        assert {f: getattr(got, f) for f in CORE} == {f: getattr(want, f) for f in CORE}
        assert not got.input_problems
