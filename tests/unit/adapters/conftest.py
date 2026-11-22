"""Shared fixtures for adapter tests: a RunContext over a MemStore in a temp data dir."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from pdr.contracts import RunContext
from pdr.settings import load_settings
from pdr.store_mem import MemStore

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("PDR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("PDR_VAULT_SECRET", "test-secret")
    s = load_settings(ROOT / "settings.yaml")
    return RunContext(run_id="t", dispute_key=None, settings=s, store=MemStore(),
                      deadline=datetime.now(timezone.utc) + timedelta(hours=1))
