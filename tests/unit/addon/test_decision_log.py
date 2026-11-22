"""Add-on E1: decision log built from saved stage results, written with the verdict, append-only."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from tests.test_cases import CASES_DIR, _ctx, _run_case

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def _inline(monkeypatch):
    monkeypatch.setenv("PDR_PAIDCALL_MODE", "inline")
    monkeypatch.setenv("PDR_OFFLINE", "1")


def _lines(store, key):
    from pdr.engine.decision_log import line
    return [line(e) for e in store.decision_log(key)]


def test_steps_in_order_and_one_set_per_verdict(tmp_path):
    store, keys, *_ , verdicts = _run_case(CASES_DIR / "case06_wrong_base_over_cap", tmp_path)
    for k in keys:
        entries = store.decision_log(k)
        steps = [e["step"] for e in entries]
        order = ["gate", "terms", "records", "notice", "fm_wording", "evidence", "combine"]
        assert list(dict.fromkeys(steps)) == order
        assert {e["verdict_id"] for e in entries} == {verdicts[k].verdict_id}
        assert [e["seq"] for e in entries] == list(range(1, len(entries) + 1))
        assert entries[-1]["numbers"], "combine entry carries the recomputation lines"


def test_update_and_delete_refused(tmp_path):
    store, keys, *_ = _run_case(CASES_DIR / "case04_duplicate", tmp_path)
    con = sqlite3.connect(store.path, isolation_level=None)
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        con.execute("UPDATE decision_log SET body='{}'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        con.execute("DELETE FROM decision_log")
    con.close()
    n = len(store.decision_log(keys[0]))
    store.decision_log_append(store.decision_log(keys[0]))  # duplicate inserts ignored
    assert len(store.decision_log(keys[0])) == n


def test_settings_change_adds_new_version(tmp_path):
    from pdr.engine import run
    store, keys, _, overrides, verdicts = _run_case(CASES_DIR / "case06_wrong_base_over_cap", tmp_path)
    before = _ctx(store, overrides, run_id="rerun").settings.version
    changed = {**overrides, "causation": {**(overrides.get("causation") or {}), "allowance_hours": 7}}  # no effect on case 6
    ctx = _ctx(store, changed, run_id="rerun")
    assert ctx.settings.version != before
    again = {v.dispute_key: v for v in run(keys, ctx)}
    k = keys[0]
    assert again[k].verdict_id != verdicts[k].verdict_id
    assert again[k].outcome == verdicts[k].outcome
    assert store.decision_log_versions(k) == [verdicts[k].verdict_id, again[k].verdict_id]
    assert {e["version"] for e in store.decision_log(k, again[k].verdict_id)} == {2}
    run(keys, ctx)  # same verdict id again: nothing appended
    assert store.decision_log_versions(k) == [verdicts[k].verdict_id, again[k].verdict_id]


def test_case3_shows_place_rejection(tmp_path):
    store, keys, *_ = _run_case(CASES_DIR / "case03_wrong_stretch", tmp_path)
    ev = [c for e in store.decision_log(keys[0]) if e["step"] == "evidence" for c in e["evidence_considered"]]
    assert any(c.startswith("rejected: place") for c in ev), ev


def test_case19_shows_not_run_budget(tmp_path):
    store, keys, *_ = _run_case(CASES_DIR / "case19_budget_below_one_search", tmp_path)
    k = next(k for k in keys if k.startswith("CLA"))
    assert any(l.startswith("[evidence]") and "not run: budget" in l for l in _lines(store, k)), _lines(store, k)


def test_case_page_has_how_we_decided(tmp_path):
    from pdr.outputs import report
    store, keys, _, overrides, _v = _run_case(CASES_DIR / "case04_duplicate", tmp_path)
    report.render(store, tmp_path / "out", _ctx(store, overrides, "r").settings)
    pages = [p.read_text() for p in (tmp_path / "out").rglob("*.html")]
    assert any("How we decided" in p for p in pages)


def test_kill_before_verdict_save_leaves_no_log(tmp_path):
    from tests.test_kill import _run
    from pdr.store import SqliteStore
    assert _run(["pipe-setup", str(tmp_path)]).returncode == 0
    died = _run(["pipe-run", str(tmp_path)], crash_at="stage.before_save.verdict")
    assert died.returncode == 137, died.stderr[-2000:]
    db = sqlite3.connect(tmp_path / "state.db")
    assert db.execute("SELECT COUNT(*) FROM decision_log").fetchone()[0] == 0
    again = _run(["pipe-run", str(tmp_path)])
    assert again.returncode == 0, again.stderr[-2000:]
    store = SqliteStore(tmp_path / "state.db")
    for k in json.loads((tmp_path / "keys.json").read_text()):
        assert len(store.decision_log_versions(k)) == 1, k
        seqs = [e["seq"] for e in store.decision_log(k)]
        assert seqs == list(range(1, len(seqs) + 1))
