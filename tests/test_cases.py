"""PRD test cases as folders (INTERFACES 5.4): run the engine and compare with expected.json, incl. variants/."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from pdr.models import Deduction

CASES_DIR = Path(__file__).parent / "cases"
CASES = sorted(p.parent for p in CASES_DIR.glob("*/case.yaml"))


def _caselib():
    try:
        from tests import caselib
        return caselib
    except ImportError:
        pytest.skip("tests/caselib.py (lane D) not on main yet")


def _ctx(store, overrides, run_id):
    from pdr.contracts import RunContext
    caselib = _caselib()
    settings = caselib.case_settings(overrides)
    return RunContext(run_id=run_id, dispute_key=None, settings=settings, store=store,
                      deadline=datetime.now(timezone.utc) + timedelta(seconds=600))


def _store(tmp_path):
    try:
        from pdr.store import SqliteStore
        return SqliteStore(str(tmp_path / "state.db"))
    except ImportError:
        from pdr.store_mem import MemStore
        return MemStore()


def _paid_calls(store, key) -> int | None:
    try:
        from pdr.broker import ledger
        return sum(1 for e in ledger.rows(store, dispute_key=key) if not e.cache_hit)
    except Exception:  # noqa: BLE001
        return None


def _needs_transport(case_dir: Path) -> bool:
    cdir = case_dir / "cassette"
    return cdir.is_dir() and any(cdir.iterdir())


def _check(v, exp: dict, key: str, store):
    assert v is not None, f"{key}: no verdict"
    assert str(v.outcome) == exp["outcome"], f"{key}: outcome {v.outcome} != {exp['outcome']}; {v.explanation}"
    if "grounds" in exp:
        assert sorted(str(g) for g in v.grounds) == sorted(exp["grounds"]), f"{key}: grounds {v.grounds}"
    if "primary_reason" in exp:
        got = str(v.primary_reason) if v.primary_reason else None
        assert got == exp["primary_reason"], f"{key}: primary_reason {got}; {v.explanation}"
    if exp.get("amount_disputable") is not None:
        assert Decimal(str(v.amount_disputable)) == Decimal(str(exp["amount_disputable"])), \
            f"{key}: amount {v.amount_disputable} != {exp['amount_disputable']}; {v.recomputation}"
    if "flags" in exp:
        assert set(exp["flags"]) <= {str(f) for f in v.flags}, f"{key}: flags {v.flags} lack {exp['flags']}"
    if exp.get("missing_item_contains"):
        blob = " ".join(v.missing_items + v.unresolved).lower()
        assert exp["missing_item_contains"].lower() in blob, f"{key}: missing items {v.missing_items}"
    if exp.get("max_paid_calls") is not None:
        n = _paid_calls(store, key)
        if n is not None:
            assert n <= int(exp["max_paid_calls"]), f"{key}: {n} paid calls > {exp['max_paid_calls']}"


def _run_case(case_dir: Path, tmp_path: Path):
    caselib = _caselib()
    import importlib.util
    for mod in ("pdr.engine.checks_terms", "pdr.engine.checks_records"):
        if importlib.util.find_spec(mod) is None:
            pytest.skip(f"{mod} (lane B, b2) not on main yet")
    if _needs_transport(case_dir):
        try:
            import tests.fakes.transport  # noqa: F401
        except ImportError:
            pytest.skip("fake transport (lane A) not on main yet")
    from pdr.engine import run
    store = _store(tmp_path)
    keys, expected, overrides = caselib.load_case(case_dir, store)
    ctx = _ctx(store, overrides, run_id=f"case-{case_dir.name}")
    verdicts = {v.dispute_key: v for v in run(keys, ctx)}
    for key in keys:
        _check(verdicts.get(key), expected[key], key, store)
    return store, keys, expected, overrides, verdicts


@pytest.fixture(autouse=True)
def _inline_paid_calls(monkeypatch):
    """Verdict cases run paid calls inline: a detached child rebuilds the fake transport, resetting its faults."""
    monkeypatch.setenv("PDR_PAIDCALL_MODE", "inline")


@pytest.mark.parametrize("case_dir", CASES, ids=lambda p: p.name)
def test_case(case_dir, tmp_path):
    _run_case(case_dir, tmp_path)


VARIANTS = [(c, v) for c in CASES if (c / "variants").is_dir() for v in sorted((c / "variants").iterdir()) if v.is_file()]


@pytest.mark.parametrize("case_dir,variant", VARIANTS, ids=lambda x: x.name)
def test_case_variant(case_dir, variant, tmp_path):
    """The same deduction in another shape gives the same dispute_key and verdict."""
    try:
        from pdr.adapters.k4_deductions import k4_load_deductions
    except ImportError:
        pytest.skip("K4 adapter (lane D) not on main yet")
    store, keys, expected, overrides, verdicts = _run_case(case_dir, tmp_path)
    ctx = _ctx(store, overrides, run_id=f"variant-{variant.name}")
    src = variant.read_text(encoding="utf-8") if variant.suffix == ".txt" else variant
    deds = k4_load_deductions(src, ctx.settings, ctx)
    assert deds, f"variant {variant.name} produced no deductions"
    for d in deds:
        assert d.dispute_key in expected, f"variant key {d.dispute_key} not in {list(expected)}"
        store.put(d)
    from pdr.engine import run
    for v in run([d.dispute_key for d in deds], ctx):
        _check(v, expected[v.dispute_key], v.dispute_key, store)
        assert v.outcome == verdicts[v.dispute_key].outcome
