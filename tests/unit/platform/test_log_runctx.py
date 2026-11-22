"""Logs and run context."""
from __future__ import annotations

from pathlib import Path

from pdr import log
from pdr.runctx import finish_run, new_run
from pdr.settings import load_settings
from pdr.store import SqliteStore
from pdr.store_mem import MemStore

ROOT = Path(__file__).resolve().parents[3]


def test_log_line_fields(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PDR_DATA_DIR", str(tmp_path))
    log.bind(run_id="r-1")
    log.set_console(True)
    try:
        log.log_event("stage", "terms_checked", "ok", dispute_key="ACME|PA1|3", ms=12)
        log.log_event("call", "crustdata:/web/search/live", "ok", cost_credits=1)
    finally:
        log.set_console(False)
        log.bind(run_id=None)
    lines = log.read_lines()
    assert len(lines) == 2
    for k in ("ts", "run_id", "dispute_key", "kind", "name", "outcome", "cost_credits", "cost_inr", "ms"):
        assert k in lines[0]
    assert lines[0]["run_id"] == "r-1" and lines[0]["dispute_key"] == "ACME|PA1|3"
    err = capsys.readouterr().err
    assert "terms_checked" in err and "crustdata" not in err  # only stage changes go to the console


def test_new_run_writes_row(tmp_path, monkeypatch):
    monkeypatch.setenv("PDR_DATA_DIR", str(tmp_path))
    s = load_settings(ROOT / "settings.yaml")
    st = SqliteStore(tmp_path / "state.db")
    ctx = new_run(st, s, time_limit=30)
    assert (ctx.deadline - __import__("datetime").datetime.now(ctx.deadline.tzinfo)).total_seconds() <= 30
    row = st.query("SELECT * FROM runs WHERE run_id=?", (ctx.run_id,))[0]
    assert row["settings_version"] == s.version and row["ended_at"] is None
    finish_run(ctx, {"verdicts": 3})
    assert st.query("SELECT ended_at FROM runs")[0]["ended_at"]
    ctx2 = new_run(MemStore(), s)  # MemStore: no runs table, still works
    assert ctx2.store.path == ":memory:"
    log.bind(run_id=None)
