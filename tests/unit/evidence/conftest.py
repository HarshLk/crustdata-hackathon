"""Shared fixtures for the evidence lane tests."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from pdr.contracts import RunContext
from pdr.settings import load_settings
from pdr.store_mem import MemStore

ROOT = Path(__file__).resolve().parents[3]
CASSETTES = ROOT / "tests" / "cassettes"
PAGES = CASSETTES / "pages"
IST = timezone(timedelta(hours=5, minutes=30))


@pytest.fixture
def settings():
    return load_settings(ROOT / "settings.yaml")


@pytest.fixture
def ctx(settings):
    return RunContext(run_id="t", dispute_key=None, settings=settings, store=MemStore(),
                      deadline=datetime.now(timezone.utc) + timedelta(hours=1))
