"""inputs_hash: what a stage reads, hashed (DESIGN 5.3, D-20). Pure."""
from __future__ import annotations

from typing import Any

from pdr.codes import Stage
from pdr.models import canonical_json, sha256_hex

RULE_VERSION = "b1-rules-2026-11-22.1"

# fields that change on every read of the same input and must not force a recompute
_VOLATILE = {"created_at", "stage", "source_ref"}
_DEDUCTION_VOLATILE = _VOLATILE | {"flags"}


def _strip(obj: Any, drop: set[str]) -> Any:
    if obj is None:
        return None
    data = obj.model_dump(mode="json") if hasattr(obj, "model_dump") else dict(obj)
    return {k: v for k, v in data.items() if k not in drop}


def deduction_view(deduction) -> dict | None:
    """Deduction fields that decide a verdict (flags other than instruction-in-input are bookkeeping)."""
    d = _strip(deduction, _DEDUCTION_VOLATILE)
    if d is not None:
        d["instruction_flag"] = "INSTRUCTION_IN_INPUT" in [str(f) for f in (deduction.flags or [])]
    return d


def results_hash(results) -> str:
    """Hash of one stage's check results (feeds later stages' inputs_hash)."""
    return sha256_hex(canonical_json([r.model_dump(mode="json") for r in (results or [])]))[:24]


def inputs_hash(stage: Stage, deduction, trip, terms, settings, prior_hashes: list[str], *,
                extra: Any = None, settings_sections: list[str] | None = None,
                check_ids: list[str] | None = None) -> str:
    """sha256 over the deduction, matched trip, terms in scope with stamps, declared settings, prior results."""
    if settings_sections is None:
        from pdr.engine.registry import settings_for_stage
        settings_sections = settings_for_stage(stage)
    if check_ids is None:
        from pdr.engine.registry import checks_for
        check_ids = [s.check_id for s in checks_for(stage)]
    sections = {}
    for name in sorted(set(settings_sections)):
        try:
            sections[name] = settings.section(name)
        except AttributeError:
            sections[name] = None
    term_views = sorted((_strip(t, _VOLATILE - {"source_ref"}) for t in (terms or [])),
                        key=lambda t: str(t.get("term_id")))
    body = {
        "rule_version": RULE_VERSION,
        "stage": Stage(stage).value,
        "checks": sorted(check_ids),
        "deduction": deduction_view(deduction),
        "trip": _strip(trip, _VOLATILE),
        "terms": term_views,
        "settings": sections,
        "prior": list(prior_hashes),
        "extra": extra,
    }
    return sha256_hex(canonical_json(body))[:32]
