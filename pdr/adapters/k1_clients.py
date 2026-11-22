"""K1: clients, confirmed aliases, sites, lanes, approvers; lanes.yaml route geography (never invented)."""
from __future__ import annotations

import difflib
import json
from pathlib import Path
from typing import Any, Optional

import yaml

from pdr.models import Client, SourceRef, Trip, norm_key


def _read_struct(p: Path) -> Any:
    text = p.read_text(encoding="utf-8")
    return json.loads(text) if p.suffix.lower() == ".json" else yaml.safe_load(text)


def _find(path: Path, stem: str) -> Optional[Path]:
    if path.is_file():
        return path if path.stem.lower() == stem else None
    for ext in (".yaml", ".yml", ".json"):
        p = path / f"{stem}{ext}"
        if p.exists():
            return p
    return None


def k1_load_clients(path: str | Path, settings: Any = None) -> list[Client]:
    """clients.yaml|json (a list, or {clients: [...]}) -> list[Client]."""
    p = _find(Path(path), "clients")
    if p is None:
        return []
    data = _read_struct(p) or []
    if isinstance(data, dict):
        data = data.get("clients", [data] if "legal_name" in data else [])
    out = []
    for i, c in enumerate(data):
        c = dict(c)
        c.setdefault("client_id", norm_key(c.get("legal_name")))
        c.setdefault("source_ref", SourceRef(file=p.name, row=i + 1).model_dump())
        out.append(Client.model_validate(c))
    return out


def load_lanes(path: str | Path) -> dict[str, dict]:
    """lanes.yaml -> {lane_id: {origin, destination, places: [{name, district, state, state_local, lat, lon, radius_km}]}}."""
    p = _find(Path(path), "lanes")
    if p is None:
        return {}
    data = _read_struct(p) or {}
    lanes = data.get("lanes", data) if isinstance(data, dict) else {}
    if isinstance(lanes, list):
        lanes = {str(x.get("lane") or x.get("lane_id") or x.get("id")): x for x in lanes}
    return {str(k): dict(v or {}) for k, v in lanes.items()}


def place_label(pl: dict) -> str:
    """'Name, District, State' with duplicates dropped (an ambiguous place carries its district/state)."""
    parts = [pl.get("name"), pl.get("district"), pl.get("state")]
    out: list[str] = []
    for x in parts:
        if x and str(x) not in out:
            out.append(str(x))
    return ", ".join(out)


def lane_geo(lane: dict) -> tuple[list[str], list[str]]:
    """Route places and distinct route states (in route order) for one lane entry."""
    places = [pl for pl in lane.get("places", []) if isinstance(pl, dict) and pl.get("name")]
    states: list[str] = [str(s) for s in lane.get("route_states", []) or []]
    for pl in places:
        for s in (pl.get("state"), pl.get("state_local")):
            if s and s not in states:
                states.append(str(s))
    for s in (lane.get("regional_state_names") or {}).values():
        if s and s not in states:
            states.append(str(s))
    return [place_label(pl) for pl in places], states


def lane_for_trip(trip: Trip, lanes: dict[str, dict]) -> Optional[dict]:
    if trip.lane in lanes:
        return lanes[trip.lane]
    for lid, lane in lanes.items():
        if norm_key(lid) == norm_key(trip.lane):
            return lane
    for lane in lanes.values():
        if lane.get("origin") and norm_key(lane.get("origin")) == norm_key(trip.origin) \
                and norm_key(lane.get("destination")) == norm_key(trip.destination):
            return lane
    return None


def attach_routes(trips: list[Trip], lanes: dict[str, dict]) -> list[Trip]:
    """Fills route_places / route_states from lanes.yaml for trips lacking them."""
    out = []
    for t in trips:
        lane = lane_for_trip(t, lanes) if (not t.route_places or not t.route_states) else None
        if lane:
            places, states = lane_geo(lane)
            t = t.model_copy(update={"route_places": t.route_places or places,
                                     "route_states": t.route_states or states})
        out.append(t)
    return out


def resolve_client(raw: str | None, clients: list[Client]) -> tuple[Optional[str], list[str]]:
    """Strict match (D-27): exact normalised legal name, client_id or confirmed alias. Returns (client_id, candidates)."""
    n = norm_key(raw)
    if not n:
        return None, []
    hits = [c.client_id for c in clients
            if n in {norm_key(c.legal_name), norm_key(c.client_id), *(norm_key(a) for a in c.aliases)}]
    if len(set(hits)) == 1:
        return hits[0], []
    if hits:
        return None, sorted(set(hits))
    cands = [c.client_id for c in clients
             if difflib.SequenceMatcher(None, n, norm_key(c.legal_name)).ratio() >= 0.7]
    return None, cands
