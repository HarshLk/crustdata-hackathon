"""Adjudicator: check registry, memoised pipeline, two-pass run (DESIGN 5)."""
from __future__ import annotations

import importlib
import logging

from pdr.engine.registry import check, checks_for, run_check  # noqa: F401

_log = logging.getLogger("pdr.engine")

# Check modules register themselves on import. Missing modules never break the package.
for _mod in ("gate", "checks_terms", "checks_records", "checks_notice", "evidence_stage"):
    try:
        importlib.import_module(f"pdr.engine.{_mod}")
    except ImportError as _e:  # module not on main yet
        if getattr(_e, "name", None) == f"pdr.engine.{_mod}":
            _log.debug("engine module %s not present", _mod)
        else:
            _log.warning("engine module %s failed to import: %r", _mod, _e)


def run(dispute_keys, ctx):
    """Two-pass run over dispute keys (pdr.engine.scheduler.run)."""
    from pdr.engine.scheduler import run as _run
    return _run(dispute_keys, ctx)


def process(dispute_key, ctx, upto=None):
    """Run one deduction through the memoised stages (pdr.engine.pipeline.process)."""
    from pdr.engine.pipeline import process as _process
    from pdr.codes import Stage
    return _process(dispute_key, ctx, upto=upto or Stage.verdict)
