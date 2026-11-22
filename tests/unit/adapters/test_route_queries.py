"""Real reader + lanes.yaml -> plain route place names, so evidence queries are `<place> <event word>` only."""
from __future__ import annotations

import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from pdr.adapters import reader
from pdr.codes import EventType
from pdr.contracts import EvidenceRequest
from pdr.models import Trip

ROOT = Path(__file__).resolve().parents[3]
queries = pytest.importorskip("pdr.evidence.queries")


def test_reader_lanes_give_plain_place_queries(ctx, tmp_path):
    shutil.copy(ROOT / "fixtures" / "lanes.yaml", tmp_path / "lanes.yaml")
    (tmp_path / "clients.yaml").write_text("clients:\n- {client_id: CLA, legal_name: Alpha Demo Agro Industries Pvt Ltd}\n")
    (tmp_path / "trips.csv").write_text(
        "trip_id,lr_no,origin,destination,lane,vehicle_no,gate_out_at,arrival_at\n"
        "T1,LR-1,Pune,Kolhapur,PUNE-KOLHAPUR,MH12AB1,2026-11-03 08:00,2026-11-06 18:00\n")
    (tmp_path / "deductions.csv").write_text("client,advice_no,line_no,deduction_date,amount,reason,lr_no\n"
                                             "Alpha Demo Agro Industries Pvt Ltd,A1,1,2026-11-10,2000,late delivery,LR-1\n")
    reader.load_request(tmp_path, ctx)
    trip = ctx.store.get(Trip, "T1")
    assert trip.route_places and all("," not in p for p in trip.route_places)
    assert "Kini" in trip.route_places and trip.route_states == ["Maharashtra"]
    assert trip.route_place_details["Kini"]["district"] == "Kolhapur"
    t0 = datetime(2026, 11, 3, 8, tzinfo=timezone.utc)
    req = EvidenceRequest(route_places=list(trip.route_places), route_states=list(trip.route_states),
                          window=(t0, t0 + timedelta(days=3)), event_types=[EventType.bandh, EventType.agitation])
    qs = queries.ladder(req, ctx.settings)
    assert qs
    words = queries.event_words(req, ctx.settings)
    regional = set(queries.regional_names(req, ctx.settings).values())
    for q in qs:
        head = next((w for w in words if q.text.endswith(" " + w)), None)
        if head is None:  # regional rung: a regional state name and a regional word
            assert q.rung == 3 and q.text.split()[0] in regional
            continue
        subject = q.text[: -len(head) - 1]
        assert "," not in subject
        assert subject in trip.route_places or subject in trip.route_states, q.text
