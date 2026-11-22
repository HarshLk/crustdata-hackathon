"""Store contract, parametrised over MemStore and SqliteStore: both must behave identically."""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from pdr.codes import STAGE_ORDER, ActionStatus, CheckStatus, Stage
from pdr.contracts import Store
from pdr.models import Action, CheckResult, Client, Deduction, Term, Trip, Verdict
from pdr.store import SqliteStore, open_store
from pdr.store_mem import MemStore
from tests.unit.test_w0 import _instances

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(params=["mem", "sqlite"])
def store(request, tmp_path):
    if request.param == "mem":
        return MemStore()
    return SqliteStore(tmp_path / "state.db")


def _fill(st):
    objs = [o for o in _instances() if type(o).__name__ not in ("Request", "CheckResult")]
    with st.tx():
        for o in objs:
            st.put(o)
    return objs


def test_protocol_and_path(store):
    assert isinstance(store, Store)
    assert store.path == ":memory:" or store.path.endswith("state.db")


def test_put_get_find_round_trip(store):
    objs = _fill(store)
    for o in objs:
        from pdr.models import entity_id
        assert store.get(type(o), entity_id(o)) == o
        assert store.get(type(o).__name__, entity_id(o)) == o
    assert store.get(Client, "nope") is None
    assert len(store.find(Term, client_id="ACME", status="confirmed")) == 9
    assert [t.term_type for t in store.find(Term, term_type="cap")] == ["cap"]
    assert [t.term_id for t in store.find(Term, agreement_id="A1")] == [f"T{i}" for i in range(9)]
    assert len(store.find(Trip, lr_no="LR-1")) == 1 and store.find(Trip, lr_no="LR-2") == []
    assert store.find(Trip, invoice_no=None)[0].trip_id == "TRIP-0091"
    assert len(store.find(Deduction, reason_code="late_delivery")) == 1


def test_upsert_keeps_order_and_replaces(store):
    store.put(Client(client_id="B", legal_name="B1"))
    store.put(Client(client_id="A", legal_name="A1"))
    store.put(Client(client_id="B", legal_name="B2"))
    assert [(c.client_id, c.legal_name) for c in store.find(Client)] == [("B", "B2"), ("A", "A1")]
    assert store.find(Client, legal_name="B1") == []


def test_stages_memoised_and_marker(store):
    objs = _fill(store)
    ded = next(o for o in objs if isinstance(o, Deduction))
    cr = CheckResult(check_id="basis", stage=Stage.terms_checked, status=CheckStatus.NO_GROUND)
    assert store.load_stage(ded.dispute_key, Stage.terms_checked, "h") is None
    store.save_stage(ded.dispute_key, Stage.terms_checked, "h", [cr])
    assert store.load_stage(ded.dispute_key, "terms_checked", "h") == [cr]
    assert store.load_stage(ded.dispute_key, Stage.terms_checked, "other") is None
    assert store.get(Deduction, ded.dispute_key).stage == Stage.terms_checked
    store.save_stage(ded.dispute_key, Stage.matched, "h0", [])  # never moves backwards
    assert store.get(Deduction, ded.dispute_key).stage == Stage.terms_checked
    assert store.load_stage(ded.dispute_key, Stage.matched, "h0") == []
    store.save_stage("UNKNOWN|X|1", Stage.matched, "h", [cr])  # no deduction row: still memoised
    assert store.load_stage("UNKNOWN|X|1", Stage.matched, "h") == [cr]


def test_verdict_immutable_and_unfinished(store):
    objs = _fill(store)
    ded = next(o for o in objs if isinstance(o, Deduction))
    assert store.unfinished() == []
    v = store.get(Verdict, "V1")
    store.put(v)  # identical re-put is fine
    with pytest.raises(ValueError):
        store.put(v.model_copy(update={"outcome": "valid"}))
    assert store.get(Verdict, "V1").outcome == v.outcome
    store.put(v.model_copy(update={"superseded_by": "V2"}))
    assert store.get(Verdict, "V1").superseded_by == "V2"
    assert [d.dispute_key for d in store.unfinished()] == [ded.dispute_key]


def test_actions_trail_snapshots(store):
    _fill(store)
    assert len(store.actions_in(ActionStatus.awaiting_approval)) == 1
    assert store.actions_in("sent") == []
    assert store.trail_verify()  # empty trail verifies
    store.trail_append("approve", "kam:asha", "A1", {"x": 1})
    store.trail_append("send", "clerk", "A1", {"amount": "2000.00", "nested": {"k": [1, 2]}})
    assert store.trail_verify()
    rows = store.trail()
    assert [r["seq"] for r in rows] == [1, 2] and rows[1]["prev_hash"] == rows[0]["row_hash"]
    store.snapshot_put("c1", "https://x", "text", None)
    store.snapshot_put("c1", "https://other", "changed", None)  # first write wins
    assert store.snapshot_get("c1")["url"] == "https://x" and store.snapshot_get("c2") is None


def test_tx_rolls_back(store, tmp_path):
    if isinstance(store, MemStore):
        pytest.skip("MemStore tx is a no-op")
    with pytest.raises(RuntimeError):
        with store.tx():
            store.put(Client(client_id="X", legal_name="X"))
            raise RuntimeError("boom")
    assert store.get(Client, "X") is None


def test_sqlite_tamper_detected_and_pragmas(tmp_path):
    st = SqliteStore(tmp_path / "s.db")
    st.trail_append("approve", "kam", "A1", {"x": 1})
    st.trail_append("send", "clerk", "A1", {})
    con = sqlite3.connect(st.path)
    con.execute("UPDATE audit_trail SET detail='{\"x\":2}' WHERE seq=1")
    con.commit()
    con.close()
    assert not st.trail_verify()
    assert st.query("PRAGMA journal_mode")[0]["journal_mode"] == "wal"
    assert st.query("PRAGMA foreign_keys")[0]["foreign_keys"] == 1
    tables = {r["name"] for r in st.query("SELECT name FROM sqlite_master WHERE type='table'")}
    for t in ("entities", "stage_results", "snapshots", "schema_maps", "audit_trail", "call_intents",
              "call_cache", "ledger", "throttle", "runs", "settings_versions", "pii_access_log"):
        assert t in tables
    st2 = SqliteStore(st.path)  # reopening re-applies the schema harmlessly
    assert len(st2.trail()) == 2


def test_open_store_honours_env(tmp_path, monkeypatch):
    monkeypatch.setenv("PDR_DATA_DIR", str(tmp_path / "dd"))
    st = open_store()
    assert Path(st.path) == tmp_path / "dd" / "state.db"
    assert Path(open_store(tmp_path / "x").path) == tmp_path / "x" / "state.db"


WRITER = textwrap.dedent("""
    import sys
    from pdr.codes import STAGE_ORDER, CheckStatus
    from pdr.models import CheckResult
    from pdr.store import SqliteStore
    from tests.unit.test_w0 import _instances
    st = SqliteStore(sys.argv[1])
    for o in _instances():
        if type(o).__name__ == "Deduction":
            st.put(o.model_copy(update={"dispute_key": "K"}))
    i = 0
    while True:
        stage = STAGE_ORDER[1 + i % (len(STAGE_ORDER) - 1)]
        st.save_stage("K", stage, f"h{i}", [CheckResult(check_id=f"c{i}", stage=stage, status=CheckStatus.NO_GROUND)] * 20)
        print(i, flush=True)
        i += 1
""")


def test_kill_minus_9_between_saves_leaves_consistent_db(tmp_path):
    db = tmp_path / "state.db"
    script = tmp_path / "writer.py"
    script.write_text(WRITER)
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    p = subprocess.Popen([sys.executable, str(script), str(db)], stdout=subprocess.PIPE, env=env, cwd=ROOT)
    seen = 0
    for line in p.stdout:
        seen = int(line)
        if seen >= 25:
            break
    p.kill()  # SIGKILL
    p.wait()
    con = sqlite3.connect(db)
    assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    con.close()
    st = SqliteStore(db)
    n = st.query("SELECT count(*) AS n FROM stage_results WHERE dispute_key='K'")[0]["n"]
    assert n >= seen + 1
    ded = st.get(Deduction, "K")
    assert ded is not None
    saved = {r["stage"] for r in st.query("SELECT stage FROM stage_results WHERE dispute_key='K'")}
    # the stage marker never runs ahead of what is saved, and matches the furthest saved stage
    assert ded.stage.value in saved
    assert max(STAGE_ORDER.index(Stage(s)) for s in saved) == STAGE_ORDER.index(ded.stage)
    for r in st.query("SELECT stage, inputs_hash FROM stage_results WHERE dispute_key='K'"):
        assert len(st.load_stage("K", r["stage"], r["inputs_hash"])) == 20
