"""E4 alert outbox: seven events queue once, D-41 no re-alert, A9 sink down, at-most-once, minimal content."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from pdr import actions, approval, send
from pdr.adapters.k7_outbox import LocalOutbox
from pdr.alerts import events, outbox
from pdr.alerts.sinks import LocalAlertSink, WhatsAppSink, mask
from pdr.codes import Flag
from tests.unit.actions import build


def _ctx(tmp_path, **appr):
    return build.ctx(store=build.make_store("sqlite", tmp_path), tmp_path=tmp_path, **appr)


def _events(keys, event):
    return [k for k in keys if k.split("~")[1] == event]


def _setup(event, tmp_path):
    """Build the store state for one event; returns (ctx, now)."""
    now = datetime.now(timezone.utc)
    if event == "second_approval":
        c = _ctx(tmp_path, above_threshold_inr=1000)
    else:
        c = _ctx(tmp_path)
    flags = [Flag.OUT_OF_WINDOW] if event == "out_of_window_held" else []
    v = build.fm_verdict(c.store, flags=flags)
    if event == "missing_document":
        actions.draft_document_request(c, v.dispute_key, ["Annexure C"])
        return c, now
    if event == "budget_stopped":
        c.store.execute("INSERT INTO runs(run_id, settings_version, started_at, ended_at, body) VALUES(?,?,?,?,?)",
                        ("r-7", "x", now.isoformat(), now.isoformat(), json.dumps({"stopped_at_budget": True, "undecided": 6})))
        return c, now
    a = actions.draft_for_verdict(v, c)
    if event == "reminder":
        return c, now + timedelta(hours=25)
    if event == "dispute_sent":
        for role in approval.missing_roles(c.store, a):
            approval.approve(c, a.action_id, role, "Asha Rao")
        send.run(c, LocalOutbox(tmp_path / "outbox"))
    return c, now


@pytest.mark.parametrize("event", events.EVENTS)
def test_each_event_queues_exactly_one_alert(tmp_path, event):
    c, now = _setup(event, tmp_path)
    first = events.scan(c.store, c.settings, now=now)
    assert len(_events(first, event)) == 1, first
    assert events.scan(c.store, c.settings, now=now) == []


def test_settings_edit_new_verdict_version_does_not_realert(tmp_path):
    c = _ctx(tmp_path)
    v = build.fm_verdict(c.store)
    actions.draft_for_verdict(v, c)
    assert len(_events(events.scan(c.store, c.settings), "verdict_ready")) == 1
    c2 = build.ctx(store=c.store, tmp_path=tmp_path)
    c2.settings = c2.settings.model_copy()  # same object kind
    from pdr.settings import with_overrides
    c2.settings = with_overrides(c.settings, {"alerts": {"reminder_hours": 48}})
    assert c2.settings.version != c.settings.version
    v2 = v.model_copy(update={"verdict_id": v.verdict_id + "-s2", "created_at": v.created_at + timedelta(seconds=5)})
    c.store.put(v.model_copy(update={"superseded_by": v2.verdict_id}))
    c.store.put(v2)
    actions.draft_for_verdict(v2, c2)
    assert events.scan(c.store, c2.settings) == []


def test_sink_failure_never_blocks_core_and_ends_failed(tmp_path):
    class Down:
        def capabilities(self):
            return {"can_check": True, "available": True}

        def exists(self, key):
            return False

        def deliver(self, key, message):
            raise ConnectionError("provider down")

    c = _ctx(tmp_path)
    v = build.fm_verdict(c.store)
    a = actions.draft_for_verdict(v, c)
    keys = events.scan(c.store, c.settings)
    now = datetime.now(timezone.utc)
    for i in range(3):
        outbox.safe_send(c.store, c.settings, Down(), now=now + timedelta(hours=i))
    approval.approve(c, a.action_id, "kam", "Asha Rao")
    res = send.run(c, LocalOutbox(tmp_path / "outbox"))
    assert res["send_sent"] == [a.action_id]
    rows = {r["alert_key"]: r for r in outbox.rows(c.store)}
    assert rows[keys[0]]["status"] == "failed" and "PROVIDER_FAILED" in rows[keys[0]]["last_error"]


def test_blind_sink_in_sending_becomes_unconfirmed_and_is_not_resent(tmp_path):
    calls = []

    class Blind:
        def capabilities(self):
            return {"can_check": False, "available": True}

        def exists(self, key):
            return False

        def deliver(self, key, message):
            calls.append(key)

    c = _ctx(tmp_path)
    actions.draft_for_verdict(build.fm_verdict(c.store), c)
    key = events.scan(c.store, c.settings)[0]
    c.store.execute("UPDATE alert_outbox SET status='sending' WHERE alert_key=?", (key,))  # as after a kill
    outbox.send_pending(c.store, c.settings, Blind())
    outbox.send_pending(c.store, c.settings, Blind())
    assert calls == [] and outbox.rows(c.store, "unconfirmed")[0]["alert_key"] == key


def test_local_sink_delivers_once_and_message_is_minimal(tmp_path):
    c = _ctx(tmp_path)
    actions.draft_for_verdict(build.fm_verdict(c.store), c)
    events.scan(c.store, c.settings, now=datetime.now(timezone.utc) + timedelta(hours=25))
    sink = LocalAlertSink(tmp_path / "alerts")
    outbox.send_pending(c.store, c.settings, sink)
    outbox.send_pending(c.store, c.settings, sink)
    files = sink.messages()
    assert len(files) == len(outbox.rows(c.store)) == len(outbox.rows(c.store, "sent")) >= 2
    blob = "".join(f.read_text() for f in files)
    for secret in (build.CANARY_NAME, build.CANARY_PHONE, "98765", build.FM_CLAUSE, build.PASSAGE, "Kini"):
        assert secret not in blob
    msg = json.loads(files[0].read_text())
    assert msg["link"].startswith("http://localhost:8000/case/")
    assert not (tmp_path / "alerts" / ".tmp").exists()


def test_whatsapp_sink_off_without_credentials_and_no_network():
    s = WhatsAppSink(env={})
    assert s.capabilities()["available"] is False and s.capabilities()["can_check"] is False
    assert mask("+919876543210").endswith("10") and "98765" not in mask("+919876543210")


def test_memstore_fallback_works():
    c = build.ctx()
    actions.draft_for_verdict(build.fm_verdict(c.store), c)
    assert len(events.scan(c.store, c.settings)) == 1
    assert events.scan(c.store, c.settings) == []
