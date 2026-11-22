"""K5 output: verdict lines rendered from the store, fields from settings.output (INTERFACES 5.1)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pdr.models import Deduction, Evidence, Verdict
from pdr.outputs.atomic import atomic_write, cleanup_stale
from pdr.outputs.derived import REGISTRY

OUT_NAME = "verdicts.jsonl"


def _path_get(data: Any, path: str) -> Any:
    for part in path.split("."):
        if isinstance(data, dict):
            data = data.get(part)
        elif isinstance(data, list) and part.isdigit() and int(part) < len(data):
            data = data[int(part)]
        else:
            return None
    return data


def render_line(verdict: Verdict, evidence: list[Evidence], settings: Any, store: Any = None) -> dict:
    """One output object: field list, order and renames all from settings.output."""
    out_cfg = settings.output
    vj = verdict.model_dump(mode="json")
    rename_fields = out_cfg.rename.fields or {}
    rename_outcome = out_cfg.rename.outcome or {}
    line: dict[str, Any] = {}
    for name, source in out_cfg.fields:
        if source.startswith("derived:"):
            fn = REGISTRY.get(source[len("derived:"):])
            value = fn(verdict, evidence, settings, store) if fn else None
        elif source in REGISTRY and source not in vj:
            value = REGISTRY[source](verdict, evidence, settings, store)
        else:
            value = _path_get(vj, source)
        if source == "outcome" and isinstance(value, str):
            value = rename_outcome.get(value, value)
        line[rename_fields.get(name, name)] = value
    return line


def current_verdicts(store: Any) -> list[Verdict]:
    """Non-superseded verdicts, one per deduction (latest if several), in deduction order."""
    best: dict[str, Verdict] = {}
    for v in store.find(Verdict):
        if v.superseded_by is not None:
            continue
        old = best.get(v.dispute_key)
        if old is None or v.created_at >= old.created_at:
            best[v.dispute_key] = v
    order = [d.dispute_key for d in store.find(Deduction)]
    ordered = [best.pop(k) for k in order if k in best]
    return ordered + sorted(best.values(), key=lambda v: v.dispute_key)


def evidence_for(store: Any, verdict: Verdict) -> list[Evidence]:
    out = []
    for eid in verdict.evidence_ids:
        ev = store.get(Evidence, eid)
        if ev is not None:
            out.append(ev)
    return out


def render_all(store: Any, settings: Any) -> list[dict]:
    return [render_line(v, evidence_for(store, v), settings, store) for v in current_verdicts(store)]


def write_all(store: Any, out_dir: str | Path, settings: Any) -> Path:
    """Re-render verdicts.jsonl from the store and replace it atomically. Never appends."""
    cleanup_stale(out_dir)
    lines = render_all(store, settings)
    text = "".join(json.dumps(l, ensure_ascii=False, default=str) + "\n" for l in lines)
    return atomic_write(Path(out_dir) / OUT_NAME, text)
