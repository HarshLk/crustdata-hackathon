"""K3: trip records -> Trip with sourced Observations, halts, notices, assertions, holds.

The only code that sees driver names, phones and licences: they go to vault.db, the Trip gets driver_ref,
and free-text notes are pseudonymised before they are stored.
"""
from __future__ import annotations

import json
import logging
import math
import re
from datetime import timedelta
from pathlib import Path
from typing import Any, Optional

from pdr import guard, vault
from pdr.codes import EventType, TsSource
from pdr.models import Assertion, Client, Halt, Hold, NoticeRecord, Observation, SourceRef, Trip, norm_key

from .k1_clients import attach_routes, resolve_client
from .k4_deductions import read_table
from .schema_map import mapping_for, parse_amount, parse_dt, split_refs

log = logging.getLogger("pdr.adapters.k3")
OBS_FIELDS = ["indent_issued_at", "reporting_at", "gate_out_at", "arrival_at", "unloaded_at"]
PLAIN_DT = ["placement_required_at", "delivery_due_at"]
DRIVER_KEYS = {"driver_name": "name", "driver": "name", "driver_phone": "phone", "driver_mobile": "phone",
               "driver_licence": "licence", "driver_license": "licence", "dl_no": "licence"}
GATE_EVENTS = {"indent_issued": "indent_issued_at", "indent": "indent_issued_at", "reporting": "reporting_at",
               "reported": "reporting_at", "gate_in": "reporting_at", "gate_out": "gate_out_at", "dispatch": "gate_out_at",
               "arrival": "arrival_at", "arrived": "arrival_at", "unloaded": "unloaded_at", "pod": "unloaded_at",
               "delivered": "unloaded_at"}
EVENT_WORDS: list[tuple[EventType, str]] = [
    (EventType.chakka_jam, r"chakka\s*jam"), (EventType.bandh, r"\bbandh"), (EventType.hartal, r"hartal"),
    (EventType.strike, r"strike"), (EventType.blockade, r"blockade|rasta\s*roko"), (EventType.agitation, r"agitation|protest"),
    (EventType.flood, r"flood"), (EventType.landslide, r"landslide"), (EventType.road_closure, r"road\s+clos|highway\s+clos"),
    (EventType.curfew, r"curfew"), (EventType.govt_detention, r"detain|detention|seiz|check\s*post"), (EventType.riot, r"riot"),
]


def detect_event(text: str) -> Optional[EventType]:
    for et, pat in EVENT_WORDS:
        if re.search(pat, text or "", re.I):
            return et
    return None


def _ts_source(v: Any, default: str = "ops_note") -> TsSource:
    try:
        return TsSource(str(v).strip()) if v not in (None, "") else TsSource(default)
    except ValueError:
        return TsSource(default)


def _obs(v: Any, src: TsSource, ref: SourceRef) -> Optional[Observation]:
    p = parse_dt(v)
    return Observation(value=p[0], source=src, ref=ref, tz_assumed=p[1]) if p else None


def _split_driver(d: dict, client_id: Optional[str]) -> Optional[str]:
    """Pops driver identifiers out of a record into the vault; returns driver_ref."""
    got: dict[str, str] = {}
    for k in list(d):
        nk = re.sub(r"[^a-z]", "_", str(k).lower()).strip("_")
        if nk in DRIVER_KEYS:
            v = d.pop(k)
            if v not in (None, ""):
                got[DRIVER_KEYS[nk]] = str(v)
    if not got:
        return d.pop("driver_ref", None)
    d.pop("driver_ref", None)
    return vault.put_driver(client_id, got.get("name"), got.get("phone"), got.get("licence"))


def _client_for(raw: Any, clients: list[Client]) -> str:
    cid, _ = resolve_client(str(raw or ""), clients)
    if cid:
        return cid
    if not raw and len(clients) == 1:
        return clients[0].client_id
    return norm_key(raw) or "UNKNOWN"


# --------------------------------------------------------------------------- trips

def _trip_from_json(d: dict, i: int, fname: str, clients: list[Client]) -> Trip:
    d = dict(d)
    cid = d.get("client_id") or _client_for(d.get("client"), clients)
    d["client_id"] = cid
    ref = _split_driver(d, cid)
    if ref:
        d["driver_ref"] = ref
    for a in d.get("assertions", []) or []:
        if isinstance(a, dict) and a.get("text"):
            a["text"] = guard.pseudonymise(a["text"], cid)
    d.setdefault("source_ref", SourceRef(file=fname, row=i).model_dump())
    return Trip.model_validate(d)


def _trip_from_row(r: dict, row_no: int, fname: str, clients: list[Client]) -> Optional[Trip]:
    f = dict(r)
    cid = _client_for(f.get("client") or f.get("client_id"), clients)
    driver_ref = _split_driver(f, cid)
    trip_id = str(f.get("trip_id") or f.get("lr_no") or "").strip()
    if not trip_id:
        log.warning("k3 %s row %s: no trip_id or lr_no; skipped", fname, row_no)
        return None
    t: dict[str, Any] = {
        "trip_id": trip_id, "client_id": cid, "driver_ref": driver_ref,
        "source_ref": SourceRef(file=fname, row=row_no),
        **{k: (str(f[k]).strip() or None) if f.get(k) not in (None, "") else None for k in ("lr_no", "invoice_no", "eway_bill_no")},
        "origin": str(f.get("origin") or ""), "destination": str(f.get("destination") or ""),
        "vehicle_no": str(f.get("vehicle_no") or ""), "quantity_unit": f.get("quantity_unit") or None,
    }
    t["lane"] = str(f.get("lane") or (f"{t['origin']}-{t['destination']}" if t["origin"] else ""))
    for k in OBS_FIELDS:
        o = _obs(f.get(k), _ts_source(f.get(f"{k}_source")), SourceRef(file=fname, row=row_no, field=k))
        t[k] = [o] if o else []
    for k in PLAIN_DT:
        p = parse_dt(f.get(k))
        t[k] = p[0] if p else None
    for k in ("freight", "quantity", "delayed_quantity_value"):
        t[k] = parse_amount(f.get(k))
    for k in ("route_places", "route_states"):
        t[k] = [x.strip() for x in str(f.get(k) or "").split(";") if x.strip()]
    asserts = []
    for k, kind in (("ops_note", "ops_note"), ("driver_report", "driver_report")):
        if f.get(k):
            txt = guard.pseudonymise(str(f[k]), cid)
            asserts.append(Assertion(kind=kind, text=txt, event_type=detect_event(txt),
                                     ref=SourceRef(file=fname, row=row_no, field=k)))
    t["assertions"] = asserts
    return Trip.model_validate(t)


# --------------------------------------------------------------------------- side files

def _index(trips: list[Trip]) -> dict[str, list[int]]:
    idx: dict[str, list[int]] = {}
    for i, t in enumerate(trips):
        for v in {t.trip_id, t.lr_no, t.invoice_no, t.eway_bill_no, t.vehicle_no}:
            if v:
                idx.setdefault(norm_key(v), []).append(i)
    return idx


def _find_trip(r: dict, idx: dict[str, list[int]]) -> Optional[int]:
    for k in ("trip_id", "lr_no", "eway_bill_no", "vehicle_no"):
        hits = idx.get(norm_key(r.get(k)), []) if r.get(k) else []
        if len(set(hits)) == 1:
            return hits[0]
    return None


def _haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def _geofence(lat: float, lon: float, lanes: dict[str, dict]) -> Optional[str]:
    for lane in lanes.values():
        for pl in lane.get("places", []):
            if pl.get("lat") is not None and pl.get("lon") is not None:
                if _haversine_km((lat, lon), (float(pl["lat"]), float(pl["lon"]))) <= float(pl.get("radius_km", 10)):
                    return " ".join(str(pl["name"]).split())
    return None


def halts_from_pings(pings: list[dict], trip: Trip, fname: str, lanes: dict[str, dict], min_minutes: float,
                     radius_km: float = 0.5) -> list[Halt]:
    """Stationary runs (within radius_km of the run's first ping) lasting >= min_minutes inside the trip window."""
    starts = [o.value for o in trip.gate_out_at] or [o.value for o in trip.reporting_at]
    ends = [o.value for o in trip.arrival_at] or [o.value for o in trip.unloaded_at]
    lo, hi = (min(starts) if starts else None), (max(ends) if ends else None)
    pts = []
    for p in pings:
        ts, lat, lon = parse_dt(p.get("at") or p.get("timestamp") or p.get("time")), p.get("lat"), p.get("lon")
        if not ts or lat in (None, "") or lon in (None, ""):
            continue
        if (lo and ts[0] < lo) or (hi and ts[0] > hi):
            continue
        pts.append((ts[0], float(lat), float(lon), (p.get("place") or "").strip() or None, p.get("_row")))
    pts.sort(key=lambda x: x[0])
    halts, i = [], 0
    while i < len(pts):
        j = i
        while j + 1 < len(pts) and _haversine_km(pts[i][1:3], pts[j + 1][1:3]) <= radius_km:
            j += 1
        if j > i and (pts[j][0] - pts[i][0]) >= timedelta(minutes=min_minutes):
            place = next((x[3] for x in pts[i:j + 1] if x[3]), None) or _geofence(pts[i][1], pts[i][2], lanes)
            halts.append(Halt(start=pts[i][0], end=pts[j][0], place=place, lat=pts[i][1], lon=pts[i][2],
                              source=TsSource.gps, ref=SourceRef(file=fname, row=pts[i][4])))
            i = j + 1
        else:
            i += 1
    return halts


def _rows(p: Path) -> list[dict]:
    _, rows, _ = read_table(p)
    for n, r in enumerate(rows, start=2):
        r["_row"] = n
    return rows


def apply_side_files(trips: list[Trip], folder: Path, lanes: dict[str, dict], settings: Any) -> list[Trip]:
    data = [t.model_dump() for t in trips]
    idx = _index(trips)
    problems: list[str] = []

    def target(r: dict, fname: str) -> Optional[dict]:
        i = _find_trip(r, idx)
        if i is None:
            problems.append(f"{fname} row {r.get('_row')}: no unique trip")
            return None
        return data[i]

    if (folder / "gate_log.csv").exists():
        for r in _rows(folder / "gate_log.csv"):
            t = target(r, "gate_log.csv")
            fld = GATE_EVENTS.get(norm_key(r.get("event")).lower()) or GATE_EVENTS.get(str(r.get("event") or "").strip().lower())
            if t is None or not fld:
                continue
            o = _obs(r.get("at"), _ts_source(r.get("source"), "client_gate_log"),
                     SourceRef(file="gate_log.csv", row=r["_row"], field=fld))
            if o:
                t[fld].append(o.model_dump())
    if (folder / "notices.csv").exists():
        for r in _rows(folder / "notices.csv"):
            t, p = target(r, "notices.csv"), parse_dt(r.get("sent_at"))
            if t is not None and p:
                t["notices"].append(NoticeRecord(sent_at=p[0], channel=str(r.get("channel") or ""),
                                                 addressee=str(r.get("addressee") or ""), subject=r.get("subject") or None,
                                                 ref=SourceRef(file="notices.csv", row=r["_row"])).model_dump())
    if (folder / "notes.csv").exists():
        for r in _rows(folder / "notes.csv"):
            t = target(r, "notes.csv")
            if t is None or not r.get("text"):
                continue
            txt = guard.pseudonymise(str(r["text"]), t["client_id"])
            kind = str(r.get("kind") or "ops_note").strip()
            kind = kind if kind in ("ops_note", "driver_report", "notice", "memo") else "ops_note"
            try:
                et = EventType(str(r.get("event_type")).strip()) if r.get("event_type") else detect_event(txt)
            except ValueError:
                et = detect_event(txt)
            p = parse_dt(r.get("at"))
            t["assertions"].append(Assertion(kind=kind, text=txt, event_type=et, at=p[0] if p else None,
                                             place=r.get("place") or None,
                                             ref=SourceRef(file="notes.csv", row=r["_row"])).model_dump())
    if (folder / "holds.csv").exists():
        for r in _rows(folder / "holds.csv"):
            t, p = target(r, "holds.csv"), parse_dt(r.get("held_from"))
            if t is None or not p:
                continue
            rel = parse_dt(r.get("released_at"))
            t["holds"].append(Hold(authority=str(r.get("authority") or ""), place=str(r.get("place") or ""),
                                   held_from=p[0], released_at=rel[0] if rel else None, reason=r.get("reason") or None,
                                   memo_ref=SourceRef(file="holds.csv", row=r["_row"], field="memo")).model_dump())
    out = [Trip.model_validate(d) for d in data]
    if (folder / "gps.csv").exists():
        k3s = getattr(getattr(settings, "adapters", None), "k3", None)
        mins = float(getattr(k3s, "halt_minutes", None) or 60) if k3s is not None else 60.0
        by_trip: dict[int, list[dict]] = {}
        for r in _rows(folder / "gps.csv"):
            i = _find_trip(r, idx)
            if i is not None:
                by_trip.setdefault(i, []).append(r)
        for i, pings in by_trip.items():
            out[i] = out[i].model_copy(update={"halts": out[i].halts + halts_from_pings(pings, out[i], "gps.csv", lanes, mins)})
    for m in problems:
        log.warning("k3: %s", m)
    return out


# --------------------------------------------------------------------------- entry point

def k3_load_trips(path: str | Path, settings: Any = None, ctx: Any = None, lanes: Optional[dict] = None,
                  clients: Optional[list[Client]] = None) -> list[Trip]:
    """Folder (trips.* + side files) or a single trips file -> list[Trip]. Driver identifiers go to the vault."""
    path = Path(path)
    folder = path if path.is_dir() else path.parent
    if clients is None:
        store = getattr(ctx, "store", None)
        clients = store.find(Client) if store is not None else []
    lanes = lanes or {}
    if path.is_dir():
        cands = [path / n for n in ("trips.json", "trips.csv", "trips.xlsx", "trips.tsv") if (path / n).exists()]
        if not cands:
            return []
        path = cands[0]
    trips: list[Trip] = []
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        data = data.get("trips", [data]) if isinstance(data, dict) else data
        trips = [_trip_from_json(d, i, path.name, clients) for i, d in enumerate(data, start=1)]
    else:
        headers, rows, _ = read_table(path)
        mapping, probs = mapping_for(headers, rows, "k3", ctx)
        for p in probs:
            log.warning("k3 %s: %s", path.name, p)
        for n, r in enumerate(rows, start=2):
            f = {fld: r.get(col) for col, fld in mapping.items()}
            for col, v in r.items():  # pass through _source columns and driver columns the mapper left alone
                nc = re.sub(r"[^a-z_]", "", str(col).lower().replace(" ", "_"))
                if nc.endswith("_source") or nc in DRIVER_KEYS:
                    f.setdefault(nc, v)
            t = _trip_from_row(f, n, path.name, clients)
            if t:
                trips.append(t)
    if folder.is_dir() and path.parent == folder:
        trips = apply_side_files(trips, folder, lanes, settings)
    return attach_routes(trips, lanes)
