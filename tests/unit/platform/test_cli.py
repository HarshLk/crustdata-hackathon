"""CLI: doctor, init refusing to start, run wiring end to end with stubbed lanes, views."""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from pdr import cli
from pdr.codes import Stage
from pdr.models import Deduction, LedgerEntry
from pdr.store import open_store
from tests.unit.platform.fixtures import fill, verdict

ROOT = Path(__file__).resolve().parents[3]
SETTINGS = str(ROOT / "settings.yaml")
runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("PDR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.chdir(tmp_path)
    cli._state.clear()
    yield
    cli._state.clear()


def invoke(*args):
    return runner.invoke(cli.app, ["--settings", SETTINGS, *args])


def test_doctor_fails_clearly_without_crustdata_key(tmp_path, monkeypatch):
    monkeypatch.delenv("CRUSTDATA_API_KEY", raising=False)
    env = tmp_path / "empty.env"
    env.write_text("OTHER=1\nCRUSTDATA_API_KEY=\n")
    monkeypatch.setenv("PDR_ENV_FILE", str(env))
    r = invoke("doctor")
    assert r.exit_code == 1
    assert "FAIL  key CRUSTDATA_API_KEY" in r.output and "not set in the environment or" in r.output
    env.write_text("CRUSTDATA_API_KEY=secret-value-xyz\n")
    r = invoke("doctor")
    assert r.exit_code == 0, r.output
    assert "secret-value-xyz" not in r.output


def test_init_prints_version_and_refuses_unset_kickoff(tmp_path):
    r = invoke("init")
    assert r.exit_code == 0 and "settings version:" in r.output
    data = yaml.safe_load(Path(SETTINGS).read_text())
    data["budget"]["usd_inr"] = 0
    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.safe_dump(data))
    cli._state.clear()
    r = runner.invoke(cli.app, ["--settings", str(bad), "init"])
    assert r.exit_code == 2 and "budget.usd_inr" in r.output


def _fake_engine(keys, ctx):
    out = []
    for i, k in enumerate(keys):
        d = ctx.store.get(Deduction, k)
        for st in (Stage.matched, Stage.terms_checked, Stage.verdict):
            ctx.store.save_stage(k, st, f"h-{st.value}", [])
        v = verdict(d, "disputable" if i == 0 else "cannot_verify")
        ctx.store.put(v)
        out.append(v)
    return out


def test_run_end_to_end_with_stubbed_lanes(tmp_path, monkeypatch):
    store = open_store()
    deds = fill(store)
    monkeypatch.setattr(cli, "_load", lambda path, ctx: deds)
    monkeypatch.setattr(cli, "_engine_run", _fake_engine)
    r = invoke("run", "--in", str(tmp_path), "--out", str(tmp_path / "out"))
    assert r.exit_code == 0, r.output
    assert deds[0].dispute_key in r.output and "disputable" in r.output and "TERMS_MISSING" in r.output
    assert "terms_checked" in r.output  # one line per stage change
    lines = [json.loads(l) for l in (tmp_path / "out" / "verdicts.jsonl").read_text().splitlines()]
    assert [l["dispute_key"] for l in lines] == [d.dispute_key for d in deds]
    runs = open_store().query("SELECT ended_at FROM runs")
    assert len(runs) == 1 and runs[0]["ended_at"]
    # views
    assert invoke("status").exit_code == 0
    r = invoke("status", deds[0].dispute_key)
    assert r.exit_code == 0 and "verdict" in r.output
    assert invoke("ledger", "--by", "client").exit_code == 0
    assert invoke("ledger", "--by", "nonsense").exit_code == 2
    r = invoke("economics")
    assert r.exit_code == 0 and "break-even fee rate" in r.output


def test_run_without_lanes_degrades_and_exits_nonzero(tmp_path, monkeypatch):
    store = open_store()
    fill(store)

    def missing(*a, **k):
        raise ImportError("No module named 'pdr.engine.scheduler'")
    monkeypatch.setattr(cli, "_engine_run", missing)
    monkeypatch.setattr(cli, "_load", missing)
    r = invoke("run", "--in", str(tmp_path), "--out", str(tmp_path / "out"))
    assert r.exit_code == 1
    assert "[engine] not available" in r.output and "no verdict:" in r.output
    assert (tmp_path / "out" / "verdicts.jsonl").read_text() == ""
