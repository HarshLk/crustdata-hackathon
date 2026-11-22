"""Crash points for the kill test (lane C). Other lanes call crashpoint(name) at these places."""
from __future__ import annotations

import os

from pdr.codes import STAGE_ORDER

KNOWN: list[str] = (
    [f"stage.before_save.{s.value}" for s in STAGE_ORDER]
    + [f"stage.after_save.{s.value}" for s in STAGE_ORDER]
    + [
        "output.before_rename",
        "call.after_intent",
        "call.after_response_before_cache",
        "call.after_cache",
        "send.after_lookup",
        "send.after_intent",
        "send.after_deliver",
        "send.after_mark_sent",
        "send.outbox_before_rename",  # c2: K7 LocalOutbox, temp written, not yet renamed
        "alert.after_intent",  # e4: alert row marked sending, not yet delivered
        "alert.after_send",  # e4: alert delivered, not yet marked sent
    ]
)


def crashpoint(name: str) -> None:
    """Kill the process (exit 137, no cleanup) when PDR_CRASH_AT names this point."""
    if os.environ.get("PDR_CRASH_AT") == name:
        os._exit(137)
