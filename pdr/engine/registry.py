"""Check registry and fail-closed barrier (DESIGN 5.8, 5.9; D-25, D-33)."""
from __future__ import annotations

import logging
import traceback
from typing import Callable

from pdr.codes import CheckStatus, Reason, Stage, STAGE_ORDER
from pdr.contracts import CheckContext, CheckSpec
from pdr.models import CheckResult

log = logging.getLogger("pdr.engine")

_REGISTRY: dict[str, CheckSpec] = {}

__all__ = ["check", "checks_for", "run_check", "all_checks", "settings_for_stage", "CheckContext", "CheckSpec"]


def check(stage, needs_terms=None, needs_events=None, settings=None, check_id: str | None = None) -> Callable:
    """Register a check function for a stage; re-registering the same id replaces it."""
    st = Stage(stage)

    def deco(fn):
        cid = check_id or fn.__name__
        spec = CheckSpec(check_id=cid, stage=st, fn=fn, needs_terms=list(needs_terms or []),
                         needs_events=list(needs_events or []), settings=list(settings or []))
        _REGISTRY[cid] = spec
        fn.check_spec = spec
        return fn

    return deco


def checks_for(stage) -> list[CheckSpec]:
    """Registered checks of a stage, in registration order."""
    st = Stage(stage)
    return [s for s in _REGISTRY.values() if s.stage == st]


def all_checks() -> list[CheckSpec]:
    """Every registered check, in pipeline stage order."""
    return sorted(_REGISTRY.values(), key=lambda s: STAGE_ORDER.index(s.stage))


def settings_for_stage(stage) -> list[str]:
    """Sorted union of settings sections declared by a stage's checks."""
    return sorted({sec for s in checks_for(stage) for sec in s.settings})


def _internal_error(spec: CheckSpec, err: BaseException) -> CheckResult:
    return CheckResult(check_id=spec.check_id, stage=spec.stage, status=CheckStatus.INCOMPLETE,
                       reason=Reason.INTERNAL_ERROR,
                       missing_item=f"check {spec.check_id} failed: {type(err).__name__}",
                       note="internal error; logged with trace")


def run_check(fn, c: CheckContext) -> list[CheckResult]:
    """Run one check inside the barrier: any exception or malformed return becomes INCOMPLETE/INTERNAL_ERROR."""
    spec = fn if isinstance(fn, CheckSpec) else getattr(fn, "check_spec", None)
    if spec is None:
        spec = CheckSpec(check_id=getattr(fn, "__name__", "anonymous"), stage=Stage.received, fn=fn)
    try:
        out = spec.fn(c)
        if out is None:
            return []
        items = out if isinstance(out, (list, tuple)) else [out]
        for r in items:
            if not isinstance(r, CheckResult):
                raise TypeError(f"check returned {type(r).__name__}, not CheckResult")
        return list(items)
    except Exception as err:  # noqa: BLE001 - the barrier is the point
        log.error("check %s failed for %s: %s\n%s", spec.check_id,
                  getattr(c.deduction, "dispute_key", "?"), type(err).__name__, traceback.format_exc())
        return [_internal_error(spec, err)]
