"""Static case file (C12): content, privacy and atomic regeneration."""
from __future__ import annotations

import html as H
import re

import pytest

from pdr import actions, approval, send
from pdr.outputs import report
from tests.unit.actions import build


@pytest.fixture(params=["mem", "sqlite"])
def c(request, tmp_path):
    return build.ctx(store=build.make_store(request.param, tmp_path), tmp_path=tmp_path)


def _all(c, tmp_path):
    fm = build.fm_verdict(c.store)
    cap = build.cap_verdict(c.store)
    conf = build.conflict_verdict(c.store)
    a = actions.draft_for_verdict(fm, c)
    approval.approve(c, a.action_id, "kam", "Asha Rao")
    send.run(c)
    actions.draft_for_verdict(cap, c)
    paths = report.render(c.store, tmp_path / "out", c.settings)
    return fm, cap, conf, a, paths


def _text(p):
    return H.unescape(p.read_text())


def test_pages_written(c, tmp_path):
    fm, cap, conf, a, paths = _all(c, tmp_path)
    names = {p.name for p in paths}
    assert {"index.html", "money.html", report.case_page(fm.dispute_key), report.case_page(cap.dispute_key),
            report.case_page(conf.dispute_key)} <= names
    for p in paths:
        t = p.read_text()
        assert "<script" not in t and "http://" not in t.split("<body>")[0] and 'rel="stylesheet"' not in t
    assert not list((tmp_path / "out").glob(".*tmp"))


def test_fm_case_page(c, tmp_path):
    fm, *_ , paths = _all(c, tmp_path)
    t = _text(tmp_path / "out" / report.case_page(fm.dispute_key))
    for s in [build.FM_CLAUSE, build.PASSAGE, "2026-11-06T09:00+05:30", "2026-11-22T09:41+05:30",
              "https://www.outlookindia.com/kini-blockade#:~:text=", "abc123snap", "STAND-IN TERMS", "page 7, clause 14.1",
              "Kini toll plaza", "gps_2026-11.csv, row 412", "client_gate_log", "pod_signed", "sent", "Asha Rao (kam)",
              "Delay excused by force majeure", "A report that the blockade ended before 5 Nov", "agreements/acme_ta.pdf#page=7"]:
        assert s in t, s
    assert "Far away depot" not in t and "outside the disputed window" in t  # GPS trimmed


def test_cap_case_page(c, tmp_path):
    _, cap, *_ = _all(c, tmp_path)
    t = _text(tmp_path / "out" / report.case_page(cap.dispute_key))
    for s in [build.CAP_CLAUSE, build.RATE_CLAUSE, "Trip cost per clause 9.2", "Cap 15% of trip cost per clause 9.3",
              "40,000.00", "6,000.00", "8,200.00", "WRONG_BASE", "OVER_CAP", "awaiting approval", "pdr approve"]:
        assert s in t, s


def test_conflict_case_page(c, tmp_path):
    _, _, conf, *_ = _all(c, tmp_path)
    t = _text(tmp_path / "out" / report.case_page(conf.dispute_key))
    for s in ["Supporting", "Contradicting", "Trucks were stopped in several districts.", "highways were unaffected",
              "2026-10-08T20:00+05:30", "https://police.example.gov.in/statement#:~:text=unaffected", "SOURCES_CONFLICT",
              "a person to weigh"]:
        assert s in t, s


def test_index_queue(c, tmp_path):
    fm, cap, conf, *_ = _all(c, tmp_path)
    t = _text(tmp_path / "out" / "index.html")
    for s in [fm.dispute_key, cap.dispute_key, conf.dispute_key, "cannot verify", "8,200.00", "sent", "awaiting approval"]:
        assert s in t, s


def test_canary_never_rendered(c, tmp_path):
    _all(c, tmp_path)
    ob = tmp_path / "outbox"
    files = list((tmp_path / "out").glob("*.html")) + list(ob.glob("*.json"))
    assert len(files) >= 6
    for p in files:
        t = H.unescape(p.read_text())
        assert build.CANARY_NAME not in t, p
        assert not re.search(r"98765\s?43210", t), p


def test_regenerate_is_safe(c, tmp_path):
    _all(c, tmp_path)
    first = (tmp_path / "out" / "index.html").read_text()
    report.render(c.store, tmp_path / "out", c.settings)
    assert (tmp_path / "out" / "index.html").read_text().split("Generated")[1][30:] == first.split("Generated")[1][30:]


def test_canary_in_ops_note_pseudonymised_via_vault(c, tmp_path, monkeypatch):
    monkeypatch.setenv("PDR_DATA_DIR", str(tmp_path / "data"))
    from pdr import vault
    from pdr.models import Trip
    vault.put_driver("ACME", build.CANARY_NAME, build.CANARY_PHONE)
    fm = build.fm_verdict(c.store)
    trip = c.store.get(Trip, "TRIP-0091")
    trip.assertions[1].text = f"{build.CANARY_NAME} called in; blockade confirmed"
    c.store.put(trip)
    report.render(c.store, tmp_path / "out", c.settings)
    t = H.unescape((tmp_path / "out" / report.case_page(fm.dispute_key)).read_text())
    assert build.CANARY_NAME not in t and "blockade confirmed" in t and "[driver " in t
