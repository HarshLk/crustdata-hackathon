"""Outbound guard and vault (lane D). Stub: passes everything, with a loud log line."""
from __future__ import annotations

import logging
from typing import Any

from pdr.contracts import GuardDestination, GuardViolation

log = logging.getLogger("pdr.guard")
_warned = False


def check(destination: GuardDestination, payload: Any, client_id: str | None = None) -> None:
    """Raises GuardViolation when the payload may not go to destination. Stub: allows all."""
    global _warned
    if not _warned:
        log.warning("GUARD STUB ACTIVE: pdr.guard.check passes everything (lane D replaces this)")
        _warned = True


def pseudonymise(text: str, client_id: str) -> str:
    """Replaces vault values with driver_ref. Stub: identity."""
    return text


def vault_read(driver_ref: str, who: str, purpose: str) -> dict:
    """Logs to pii_access_log."""
    raise NotImplementedError("lane D")


__all__ = ["check", "pseudonymise", "vault_read", "GuardViolation"]
