"""K5 renderer: settings-driven fields, derived fields, rendered from the store, atomic replace."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from decimal import Decimal
from pathlib import Path

import pytest

from pdr.outputs.derived import REGISTRY, amount_words, indian_words
from pdr.outputs.k5 import current_verdicts, render_all, write_all
from pdr.settings import load_settings
from pdr.store import SqliteStore
from pdr.store_mem import MemStore
from tests.unit.platform.fixtures import fill, verdict

ROOT = Path(__file__).resolve().parents[3]
S = load_settings(ROOT / "settings.yaml")


def _lines(p):
    return [json.loads(l) for l in Path(p).read_text().splitlines()]


def test_default_line_shape():
    st = MemStore()
    d1, d2 = fill(st)
    st.put(verdict(d1))
    st.put(verdict(d2, "cannot_verify"))
    lines = render_all(st, S)
    assert [l["dispute_key"] for l in lines] == [d1.dispute_key, d2.dispute_key]
    a = lines[0]
    assert list(a) == [n for n, _ in S.output.fields]
    assert a["outcome"] == "disputable" and a["grounds"] == ["OVER_CAP"] and a["amount_disputable"] == "1500.00"
    assert a["cites"][0] == {"kind": "contract_term", "doc": "Transport Agreement", "effective": "2026-04-01",
                             "page": 7, "clause": "14.2", "text": "capped at 10% of trip cost", "link": "ta.pdf#page=7"}
    assert a["cites"][1]["kind"] == "internal_record" and a["cites"][1]["source"] == "trips.csv:row 4"
    assert lines[1]["reason"] == "TERMS_MISSING" and lines[1]["cites"] == []


def test_added_field_and_renames_need_no_code(tmp_path):
    st = SqliteStore(tmp_path / "state.db")
    d1, _ = fill(st)
    st.put(verdict(d1))
    fields = [list(f) for f in S.output.fields] + [["in_words", "derived:amount_in_words"],
                                                    ["confidence", "derived:confidence_label"],
                                                    ["deduction", "deduction_id"]]
    s2 = load_settings(ROOT / "settings.yaml", {"output": {"fields": fields,
                                                         "rename": {"outcome": {"disputable": "DISPUTE"},
                                                                    "fields": {"grounds": "why"}}}})
    write_all(st, tmp_path / "out", S)
    before = _lines(tmp_path / "out" / "verdicts.jsonl")
    write_all(st, tmp_path / "out", s2)
    after = _lines(tmp_path / "out" / "verdicts.jsonl")
    assert "in_words" not in before[0]
    assert after[0]["in_words"] == "Rupees One Thousand Five Hundred only"
    assert after[0]["confidence"] in ("high", "medium") and after[0]["deduction"] == "D1"
    assert after[0]["outcome"] == "DISPUTE" and after[0]["why"] == ["OVER_CAP"] and "grounds" not in after[0]


def test_only_current_verdicts_one_per_deduction(tmp_path):
    st = SqliteStore(tmp_path / "state.db")
    d1, d2 = fill(st)
    v_old = verdict(d1, "cannot_verify", vid="V-old")
    st.put(v_old)
    st.put(verdict(d1, vid="V-new"))
    st.put(v_old.model_copy(update={"superseded_by": "V-new"}))
    assert [v.verdict_id for v in current_verdicts(st)] == ["V-new"]  # d2 has no verdict yet
    for _ in range(3):
        write_all(st, tmp_path, S)
    lines = _lines(tmp_path / "verdicts.jsonl")
    assert len(lines) == 1 and lines[0]["outcome"] == "disputable"


def test_indian_words():
    assert indian_words(0) == "Zero"
    assert indian_words(1_50_000) == "One Lakh Fifty Thousand"
    assert indian_words(2_03_45_678) == "Two Crore Three Lakh Forty Five Thousand Six Hundred Seventy Eight"
    assert amount_words(Decimal("2000.50")) == "Rupees Two Thousand and Fifty Paise only"
    assert indian_words(1000_00_00_000) == "One Thousand Crore"


def test_derived_registry_complete():
    for name in ("citations", "confidence_label", "amount_in_words", "escalation_hint", "sources_count",
                 "checks_summary", "what_would_change", "run_cost_inr"):
        assert name in REGISTRY
    st = MemStore()
    d1, d2 = fill(st)
    v = verdict(d1)
    assert REGISTRY["escalation_hint"](v, [], S, st) == "dispute letter"
    big = verdict(d1, amount_disputable=Decimal("600000"), vid="Vb")
    assert REGISTRY["escalation_hint"](big, [], S, st) == "arbitration"
    assert REGISTRY["escalation_hint"](verdict(d2, "valid"), [], S, st) is None
    assert REGISTRY["confidence_label"](verdict(d2, "cannot_verify"), [], S, st) == "not decided"
    assert REGISTRY["checks_summary"](v, [], S, st).startswith("2 checks run; grounds: OVER_CAP")


KILLER = textwrap.dedent("""
    import sys
    from pdr.outputs.k5 import write_all
    from pdr.settings import load_settings
    from pdr.store import SqliteStore
    write_all(SqliteStore(sys.argv[1]), sys.argv[2], load_settings(sys.argv[3]))
""")


def test_kill_before_rename_keeps_old_file(tmp_path):
    db = tmp_path / "state.db"
    st = SqliteStore(db)
    d1, d2 = fill(st)
    st.put(verdict(d1))
    out = tmp_path / "out"
    write_all(st, out, S)
    old = (out / "verdicts.jsonl").read_bytes()
    st.put(verdict(d2, "cannot_verify"))
    script = tmp_path / "k.py"
    script.write_text(KILLER)
    env = dict(os.environ, PYTHONPATH=str(ROOT), PDR_CRASH_AT="output.before_rename")
    r = subprocess.run([sys.executable, str(script), str(db), str(out), str(ROOT / "settings.yaml")], env=env, cwd=ROOT)
    assert r.returncode == 137
    assert (out / "verdicts.jsonl").read_bytes() == old  # old file intact
    assert list((out / ".tmp").iterdir())  # the killed write left its temp file in .tmp/, not beside outputs
    assert sorted(p.name for p in out.iterdir()) == [".tmp", "verdicts.jsonl"]
    env.pop("PDR_CRASH_AT")
    assert subprocess.run([sys.executable, str(script), str(db), str(out), str(ROOT / "settings.yaml")], env=env,
                          cwd=ROOT).returncode == 0
    lines = _lines(out / "verdicts.jsonl")
    assert sorted(l["dispute_key"] for l in lines) == sorted([d1.dispute_key, d2.dispute_key])
    assert sorted(p.name for p in out.iterdir()) == ["verdicts.jsonl"]  # `ls -a out`: only real outputs


def test_cleanup_stale_and_clean_write(tmp_path):
    from pdr.outputs.atomic import atomic_write, cleanup_stale
    atomic_write(tmp_path / "a.txt", "x")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.txt"]
    (tmp_path / ".tmp").mkdir()
    (tmp_path / ".tmp" / "a.txt.123.part").write_text("half")
    (tmp_path / ".verdicts.jsonl.abc.tmp").write_text("old layout")
    assert cleanup_stale(tmp_path) == 2
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.txt"]
    assert cleanup_stale(tmp_path / "missing") == 0
