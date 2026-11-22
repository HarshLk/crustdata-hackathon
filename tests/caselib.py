"""Load a PRD test-case folder (INTERFACES 5.4) straight into a Store as logical-model objects."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import yaml

from pdr.models import Agreement, Client, Deduction, Term, Trip, make_dispute_key, make_event_fp

CASES_DIR = Path(__file__).parent / "cases"
REPO_DIR = Path(__file__).resolve().parent.parent
EXPECTED_KEYS = {"outcome", "grounds", "primary_reason", "amount_disputable", "flags",
                 "missing_item_contains", "max_paid_calls"}


def case_dirs() -> list[Path]:
    """Every case folder (those holding a case.yaml), sorted by name."""
    return sorted(p.parent for p in CASES_DIR.glob("*/case.yaml"))


def _yaml(path: Path) -> Any:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _json(path: Path) -> Any:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def read_case(case_dir: str | Path) -> dict:
    """Parse every file of a case folder into model objects without touching a store."""
    d = Path(case_dir)
    meta = _yaml(d / "case.yaml")
    clients_doc = _yaml(d / "clients.yaml") if (d / "clients.yaml").exists() else {}
    terms_doc = _yaml(d / "terms.yaml") if (d / "terms.yaml").exists() else {}
    clients = [Client.model_validate(c) for c in clients_doc.get("clients", [])]
    agreements = [Agreement.model_validate(a) for a in terms_doc.get("agreements", [])]
    terms = [Term.model_validate(t) for t in terms_doc.get("terms", [])]
    trips = [Trip.model_validate(t) for t in (_json(d / "trips.json") if (d / "trips.json").exists() else [])]
    deductions = [Deduction.model_validate(_fill_keys(r)) for r in _json(d / "deductions.json")]
    expected = _json(d / "expected.json")
    return {"meta": meta, "clients": clients, "agreements": agreements, "terms": terms,
            "trips": trips, "deductions": deductions, "expected": expected}


def _fill_keys(row: dict) -> dict:
    """Derive dispute_key and event_fp from fixed fields when the file leaves them out."""
    row = dict(row)
    if not row.get("dispute_key"):
        row["dispute_key"] = make_dispute_key(row["client_id"], row.get("advice_no"), row.get("line_no"),
                                              row.get("client_deduction_no"))
    if not row.get("event_fp"):
        trip = (row.get("trip_refs") or [""])[0]
        row["event_fp"] = make_event_fp(row["client_id"], trip, row["reason_code"])
    row.setdefault("deduction_id", row["dispute_key"])
    return row


def register_cassette(case_dir: str | Path, faults: list | None = None) -> Any | None:
    """Register the folder's cassette/ with a1's fake transport, if that module exists."""
    try:
        from tests.fakes.transport import FakeTransport  # type: ignore
        from pdr.broker import register_transport
    except ImportError:
        return None
    cdir = Path(case_dir) / "cassette"
    t = FakeTransport(cassette_dir=cdir if cdir.is_dir() else None, faults=faults or [])
    for name in ("crustdata", "llm", "web"):
        register_transport(name, t)
    return t


def load_case(case_dir: str | Path, store: Any) -> tuple[list[str], dict, dict]:
    """Put a case's clients, agreements, terms, trips and deductions into store.

    Returns (dispute_keys in file order, expected.json, settings_overrides from case.yaml).
    """
    c = read_case(case_dir)
    with store.tx():
        for group in ("clients", "agreements", "terms", "trips", "deductions"):
            for obj in c[group]:
                store.put(obj)
    register_cassette(case_dir, faults=c["meta"].get("faults"))
    keys = [d.dispute_key for d in c["deductions"]]
    return keys, c["expected"], dict(c["meta"].get("settings_overrides") or {})


def merge_settings(base: dict, overrides: dict) -> dict:
    """Deep-merge settings overrides (dicts merge, everything else replaces)."""
    out = copy.deepcopy(base)
    for k, v in (overrides or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = merge_settings(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def case_settings(overrides: dict, path: str | Path | None = None):
    """Settings object for a case: settings.yaml with the case's overrides applied."""
    from pdr.settings import settings_from_dict
    base = _yaml(Path(path) if path else REPO_DIR / "settings.yaml")
    return settings_from_dict(merge_settings(base, overrides))
