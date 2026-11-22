"""Rate limit shared through the database: at most N calls per provider in any 60 s window."""
from __future__ import annotations

import sqlite3
import time
from typing import Callable

from pdr.broker.db import tx

# injectable for tests (instant throttle test)
CLOCK: Callable[[], float] = time.time
SLEEP: Callable[[float], None] = time.sleep
WINDOW = 60.0


def acquire(conn: sqlite3.Connection, provider: str, rate_per_minute: int | None) -> float:
    """Take one slot, waiting as needed; returns seconds waited."""
    if not rate_per_minute or rate_per_minute <= 0:
        return 0.0
    waited = 0.0
    while True:
        with tx(conn) as c:
            now = CLOCK()
            c.execute("DELETE FROM throttle WHERE provider=? AND at<=?", (provider, now - WINDOW))
            rows = c.execute("SELECT at FROM throttle WHERE provider=? ORDER BY at", (provider,)).fetchall()
            if len(rows) < rate_per_minute:
                c.execute("INSERT INTO throttle(provider, at) VALUES (?, ?)", (provider, now))
                return waited
            wait = rows[len(rows) - rate_per_minute][0] + WINDOW - now
        wait = max(wait, 0.01)
        SLEEP(wait)
        waited += wait
