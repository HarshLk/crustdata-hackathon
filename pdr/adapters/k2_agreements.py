"""K2: agreement files and already-confirmed terms (terms.yaml). New terms come only from `pdr intake`."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import yaml

from pdr.models import Agreement, Term

DOC_SUFFIXES = {".pdf", ".txt", ".md"}


def _struct(p: Path) -> Any:
    t = p.read_text(encoding="utf-8")
    return json.loads(t) if p.suffix == ".json" else yaml.safe_load(t)


def _candidates(path: Path, stem: str) -> list[Path]:
    dirs = [path] if path.is_dir() else [path.parent]
    if (dirs[0] / "agreements").is_dir():
        dirs.append(dirs[0] / "agreements")
    return [d / f"{stem}{e}" for d in dirs for e in (".yaml", ".yml", ".json") if (d / f"{stem}{e}").exists()]


def load_terms_file(path: str | Path) -> tuple[list[Agreement], list[Term]]:
    """terms.yaml: a list of Terms, or {agreements: [...], terms: [...]}; plus agreements.yaml if present."""
    path = Path(path)
    ags: list[Agreement] = []
    terms: list[Term] = []
    files = [path] if path.is_file() else _candidates(path, "terms")
    for f in files:
        data = _struct(f) or {}
        if isinstance(data, list):
            data = {"terms": data}
        ags += [Agreement.model_validate(a) for a in data.get("agreements", []) or []]
        terms += [Term.model_validate(t) for t in data.get("terms", []) or []]
    if path.is_dir():
        for f in _candidates(path, "agreements"):
            data = _struct(f) or []
            data = data.get("agreements", []) if isinstance(data, dict) else data
            ags += [Agreement.model_validate(a) for a in data]
    return ags, terms


def save_terms_file(path: str | Path, agreements: list[Agreement], terms: list[Term]) -> None:
    data = {"agreements": [a.model_dump(mode="json", exclude={"created_at"}) for a in agreements],
            "terms": [t.model_dump(mode="json", exclude={"created_at"}) for t in terms]}
    Path(path).write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")


def agreement_files(path: str | Path) -> list[Path]:
    p = Path(path)
    d = p / "agreements" if (p / "agreements").is_dir() else p
    return sorted(f for f in d.iterdir() if f.suffix.lower() in DOC_SUFFIXES) if d.is_dir() else []


def k2_load_agreements(path: str | Path, client_id: Optional[str] = None) -> list[Agreement]:
    """Agreement records found at path (agreements.yaml / terms.yaml), optionally for one client."""
    ags, _ = load_terms_file(path)
    return [a for a in ags if client_id is None or a.client_id == client_id]
