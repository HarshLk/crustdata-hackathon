"""K3: sourced observations, halts from GPS, notes pseudonymised, driver data only in vault.db."""
from __future__ import annotations

import sqlite3

import yaml

from pdr.adapters import k3_trips as k3
from pdr.adapters.k1_clients import load_lanes
from pdr.codes import EventType, TsSource
from pdr.models import Client, canonical_json

NAME, PHONE = "Canary Bhimrao Patil", "98220 11223"


def _bundle(tmp_path):
    (tmp_path / "trips.csv").write_text(
        "Trip ID,LR No,Client,Origin,Destination,Lane,Truck No,Gate Out,Arrival,arrival_at_source,Freight,Driver Name,Driver Mobile,Remarks\n"
        f"T1,LR-88121,Acme Foods Private Limited,Pune,Nagpur,PUN-NAG,MH12AB1234,10-10-2026 08:00,2026-10-12T18:00:00+05:30,consignee_gate_log,50000,{NAME},{PHONE},"
        f"\"Driver {NAME} ({PHONE}) stuck in bandh at Aurangabad\"\n")
    (tmp_path / "gate_log.csv").write_text("lr_no,event,at,source\nLR-88121,arrival,12-10-2026 17:30,consignee_gate_log\n")
    (tmp_path / "gps.csv").write_text(
        "vehicle_no,at,lat,lon\n"
        "MH12AB1234,2026-10-10 09:00,18.52,73.85\n"
        "MH12AB1234,2026-10-11 06:00,19.876,75.343\n"
        "MH12AB1234,2026-10-11 08:00,19.877,75.344\n"
        "MH12AB1234,2026-10-11 14:30,19.876,75.343\n"
        "MH12AB1234,2026-10-12 12:00,21.14,79.08\n")
    (tmp_path / "notes.csv").write_text(f"trip_id,kind,text\nT1,driver_report,Called from {PHONE}: road blocked\n")
    (tmp_path / "lanes.yaml").write_text(yaml.safe_dump({"lanes": {"PUN-NAG": {"origin": "Pune", "destination": "Nagpur", "places": [
        {"name": "Pune", "district": "Pune", "state": "Maharashtra"},
        {"name": "Aurangabad", "district": "Chhatrapati Sambhajinagar", "state": "Maharashtra", "lat": 19.88, "lon": 75.34, "radius_km": 10},
        {"name": "Nagpur", "district": "Nagpur", "state": "Maharashtra"}]}}}))


def test_trips_split_and_sourced(ctx, tmp_path):
    _bundle(tmp_path)
    ctx.store.put(Client(client_id="ACME", legal_name="Acme Foods Private Limited"))
    trips = k3.k3_load_trips(tmp_path, ctx.settings, ctx, lanes=load_lanes(tmp_path))
    assert len(trips) == 1
    t = trips[0]
    for x in trips:
        ctx.store.put(x)
    blob = canonical_json([x.model_dump(mode="json") for x in ctx.store.find("Trip")])
    assert "Bhimrao" not in blob and "11223" not in blob and "9822011223" not in blob
    assert t.driver_ref and t.driver_ref in blob
    con = sqlite3.connect(tmp_path / "data" / "vault.db")
    assert con.execute("select name, phone from drivers").fetchone() == (NAME, PHONE)
    assert t.client_id == "ACME" and t.freight == 50000
    go = t.gate_out_at[0]
    assert go.tz_assumed and go.source == TsSource.ops_note and go.ref.row == 2 and go.ref.field == "gate_out_at"
    assert {o.source for o in t.arrival_at} == {TsSource.consignee_gate_log}
    assert len(t.arrival_at) == 2 and not t.arrival_at[0].tz_assumed
    assert len(t.halts) == 1 and t.halts[0].place.startswith("Aurangabad") and t.halts[0].source == TsSource.gps
    assert t.route_places == ["Pune", "Aurangabad", "Nagpur"] and t.route_states == ["Maharashtra"]
    assert t.route_place_details["Aurangabad"] == {"district": "Chhatrapati Sambhajinagar", "state": "Maharashtra"}
    kinds = {a.kind: a for a in t.assertions}
    assert kinds["ops_note"].event_type == EventType.bandh and t.driver_ref in kinds["ops_note"].text
    assert "[driver" in kinds["driver_report"].text


def test_logical_json_trips_load_and_split(ctx, tmp_path):
    import json
    (tmp_path / "trips.json").write_text(json.dumps([{
        "trip_id": "T9", "client_id": "ACME", "origin": "A", "destination": "B", "lane": "A-B", "vehicle_no": "V1",
        "driver_name": NAME, "driver_phone": PHONE,
        "assertions": [{"kind": "ops_note", "text": f"{NAME} late", "ref": {"file": "x"}}]}]))
    t = k3.k3_load_trips(tmp_path, ctx.settings, ctx)[0]
    assert t.driver_ref and NAME not in canonical_json(t.model_dump(mode="json"))
    assert t.route_places == []  # no lane geography -> none invented
