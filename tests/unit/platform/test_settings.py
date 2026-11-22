"""Settings: validation refuses to start, D-19 kickoff rule, overrides, version, no contract-value fields."""
from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from pdr.settings import (
    KICKOFF_FIELDS, Settings, SettingsError, deep_merge, kickoff_problems, load_for_start, load_settings,
    record_version, with_overrides,
)
from pdr.store import SqliteStore

ROOT = Path(__file__).resolve().parents[3]
YAML = ROOT / "settings.yaml"


def _write(tmp_path, overrides):
    data = deep_merge(yaml.safe_load(YAML.read_text()), overrides)
    p = tmp_path / "s.yaml"
    p.write_text(yaml.safe_dump(data))
    return p


def test_shipped_settings_start():
    s = load_for_start(YAML)
    assert len(s.version) == 12 and kickoff_problems(s) == []


@pytest.mark.parametrize("dotted", KICKOFF_FIELDS)
def test_kickoff_zero_refuses_to_start(tmp_path, dotted):
    sec, key = dotted.split(".")
    with pytest.raises(SettingsError) as e:
        load_for_start(_write(tmp_path, {sec: {key: 0}}))
    assert dotted in str(e.value)


def test_economics_assumptions_may_be_zero(tmp_path):
    s = load_for_start(_write(tmp_path, {"economics": {"fee_rate_pct": 0, "assumed_win_rate_pct": 0}}))
    assert s.economics.fee_rate_pct == 0


def test_invalid_refuses_with_readable_message(tmp_path):
    with pytest.raises(SettingsError) as e:
        load_for_start(_write(tmp_path, {"letter": {"grouping": "weekly"}}))
    assert "letter.grouping" in str(e.value)
    with pytest.raises(SettingsError):
        load_for_start(tmp_path / "missing.yaml")
    bad = tmp_path / "bad.yaml"
    bad.write_text("budget: [unclosed")
    with pytest.raises(SettingsError):
        load_for_start(bad)


def test_overrides_and_version(tmp_path):
    base = load_settings(YAML)
    s = load_settings(YAML, {"records": {"conflict_allowance_minutes": 30}})
    assert s.records.conflict_allowance_minutes == 30 and s.version != base.version
    assert s.evidence.min_page_chars == base.evidence.min_page_chars  # merge, not replace
    s2 = with_overrides(base, {"records": {"conflict_allowance_minutes": 30}})
    assert s2.version == s.version
    assert base.section("budget")["usd_inr"] == "88.0"
    st = SqliteStore(tmp_path / "state.db")
    assert record_version(st, s) == s.version
    record_version(st, s)
    assert st.query("SELECT settings_version FROM settings_versions") == [{"settings_version": s.version}]


# Names that would look like a contract value (rate, cap, window, notice period, penalty).
CONTRACT_WORDS = re.compile(r"(^|_)(rate|cap|notice|penalty|penalties)(_|$)|window_days")
ALLOWED_SECTIONS = ("budget", "approvals", "economics")
ALLOWED_PATHS = {
    "evidence.window_allowance_hours",        # search-window padding, not a contract window
    "providers.crustdata.rate_per_minute",    # the API's request rate limit
    "event_watch.daily_credit_cap",           # our own spend limit
    "escalation.legal_notice_above_inr",      # the 3PL's own escalation threshold
}


def _paths(obj, prefix=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{prefix}.{k}" if prefix else str(k)
            yield p
            if not (prefix == "evidence" and k == "disruption_types") and not (prefix == "output"):
                yield from _paths(v, p)


def test_no_settings_field_looks_like_a_contract_value():
    names = set(_paths(load_settings(YAML).model_dump(mode="json")))

    def walk(model, prefix=""):
        for name, f in model.model_fields.items():
            p = f"{prefix}.{name}" if prefix else name
            names.add(p)
            ann = f.annotation
            if isinstance(ann, type) and issubclass(ann, __import__("pydantic").BaseModel):
                walk(ann, p)
    walk(Settings)
    offenders = [n for n in names
                 if CONTRACT_WORDS.search(n.split(".")[-1])
                 and n.split(".")[0] not in ALLOWED_SECTIONS and n not in ALLOWED_PATHS]
    assert offenders == [], f"settings fields that look like contract values: {offenders}"
