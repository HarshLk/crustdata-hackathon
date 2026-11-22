"""CLI sub-app end to end on a SQLite store in a temp data dir."""
from __future__ import annotations

import pytest
from typer.testing import CliRunner

from pdr.models import Action
from tests.unit.actions import build


def test_cli_flow(tmp_path, monkeypatch):
    monkeypatch.setenv("PDR_DATA_DIR", str(tmp_path))
    monkeypatch.chdir(build.ROOT)
    try:
        from pdr import cli
        from pdr.store import open_store
    except ImportError:
        pytest.skip("c1 cli/store not here")
    cli._state.clear()
    store = open_store(tmp_path)
    c = build.ctx(store=store, tmp_path=tmp_path)
    from pdr import actions
    a = actions.draft_for_verdict(build.fm_verdict(store), c)
    s = cli.settings_and_store()[0]
    monkeypatch.setattr(s.adapters.k7, "path", str(tmp_path / "outbox"))
    r = CliRunner()
    res = r.invoke(cli.app, ["approve", a.action_id, "--role", "legal", "--name", "X"])
    assert res.exit_code == 1 and "refused" in res.output
    res = r.invoke(cli.app, ["approve", a.action_id, "--role", "kam", "--name", "Asha Rao"])
    assert res.exit_code == 0, res.output
    res = r.invoke(cli.app, ["send"])
    assert res.exit_code == 0 and a.action_id in res.output, res.output
    from pdr.adapters.k7_outbox import LocalOutbox
    assert len(LocalOutbox(tmp_path / "outbox").messages()) == 1
    res = r.invoke(cli.app, ["outbox"])
    assert res.exit_code == 0 and "1 message(s)" in res.output, res.output
    res = r.invoke(cli.app, ["report", "--out", str(tmp_path / "out")])
    assert res.exit_code == 0 and (tmp_path / "out" / "index.html").exists(), res.output
    res = r.invoke(cli.app, ["pack", a.dispute_keys[0], "--out", str(tmp_path / "out")])
    assert res.exit_code == 0 and "case_" in res.output
    res = r.invoke(cli.app, ["trail", "--verify"])
    assert res.exit_code == 0 and "OK" in res.output
    assert cli.settings_and_store()[1].get(Action, a.action_id).status == "sent"
    cli._state.clear()


def test_trail_tamper_detected(tmp_path):
    try:
        from pdr.store import SqliteStore
    except ImportError:
        pytest.skip("no sqlite store")
    from pdr import actions, approval
    store = SqliteStore(tmp_path / "s.db")
    c = build.ctx(store=store, tmp_path=tmp_path)
    a = actions.draft_for_verdict(build.fm_verdict(store), c)
    approval.approve(c, a.action_id, "kam", "Asha Rao")
    assert store.trail_verify()
    store.execute("UPDATE audit_trail SET actor='Mallory (kam)' WHERE event='approve'")
    assert not store.trail_verify()
