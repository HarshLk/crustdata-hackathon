"""Settings schema (INTERFACES.md section 4). Owner: lane C.

No field here may hold a contract value (rate, cap, window, notice period, FM list).
Sections allow extra keys so a lane or the hour-3 change can add a key in settings.yaml
before the schema catches up; lane C may tighten this.
The refuse-to-start rule for zero "set at kickoff" values is lane C's (not implemented here).
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from pdr.codes import EventType
from pdr.models import canonical_json, sha256_hex


class _Section(BaseModel):
    model_config = ConfigDict(extra="allow")


class Budget(_Section):
    run_cost_credits: Decimal
    per_deduction_credits: Decimal
    run_time_seconds: int
    credit_price_usd: Decimal
    usd_inr: Decimal


class CrustdataProvider(_Section):
    base_url: str
    api_version: str
    rate_per_minute: int
    retry_limit: int
    backoff_seconds: list[float]
    timeout_seconds: float


class LlmProvider(_Section):
    provider: str
    model_extract: str
    model_parse: str
    retry_limit: int
    timeout_seconds: float


class Providers(_Section):
    crustdata: CrustdataProvider
    llm: LlmProvider
    paidcall_mode: Literal["detached", "inline"] = "detached"
    paid_concurrency: int = 3


class EvidenceSettings(_Section):
    disruption_types: dict[EventType, list[str]]
    window_allowance_hours: float
    max_searches_per_deduction: int
    max_fetches_per_deduction: int
    max_blocked_fetches: int
    min_page_chars: int
    block_phrases: list[str]
    human_mode_retry: bool
    min_qualifying_sources: int
    regional_query: bool
    search_when_corroboration_missing: bool
    search_for_referrals: bool
    refuter: bool
    sources: list[str]


class Economics(_Section):
    search_gate_ratio: float
    estimated_search_credits: Decimal
    fee_rate_pct: float = 0
    assumed_win_rate_pct: float = 0


class Records(_Section):
    conflict_allowance_minutes: float


class Causation(_Section):
    allowance_hours: float


class Approvals(_Section):
    send_dispute: list[str]
    above_threshold_inr: Decimal
    above_threshold_roles: list[str]
    override_out_of_window: list[str]


class Escalation(_Section):
    legal_notice_above_inr: Decimal
    arbitration_above_inr: Decimal


class Letter(_Section):
    response_days: int
    grouping: Literal["per_dispute", "monthly_per_client"]


class Privacy(_Section):
    retention_days: int


class OutputRename(_Section):
    outcome: dict[str, str] = Field(default_factory=dict)
    fields: dict[str, str] = Field(default_factory=dict)


class CaseFile(_Section):
    dir: str = "out"
    regenerate_after_each_verdict: bool = True


class Output(_Section):
    format: str = "jsonl"
    fields: list[tuple[str, str]]  # (output name, Verdict path or "derived:<name>")
    rename: OutputRename = Field(default_factory=OutputRename)
    case_file: CaseFile = Field(default_factory=CaseFile)


class FieldMapAdapter(_Section):
    field_map: dict[str, str] = Field(default_factory=dict)


class K7Adapter(_Section):
    kind: str = "local_outbox"
    path: str = "data/outbox"


class Adapters(_Section):
    k3: FieldMapAdapter = Field(default_factory=FieldMapAdapter)
    k4: FieldMapAdapter = Field(default_factory=FieldMapAdapter)
    k7: K7Adapter = Field(default_factory=K7Adapter)


class EventWatch(_Section):
    states: list[str] = Field(default_factory=list)
    queries_per_state: list[str] = Field(default_factory=list)
    lookahead_days: int = 7
    daily_credit_cap: Decimal = Decimal("0")


class Settings(_Section):
    version_note: str = ""
    budget: Budget
    providers: Providers
    evidence: EvidenceSettings
    economics: Economics
    records: Records
    causation: Causation
    approvals: Approvals
    escalation: Escalation
    letter: Letter
    privacy: Privacy
    output: Output
    adapters: Adapters
    event_watch: EventWatch

    _version: str = PrivateAttr(default="")

    def model_post_init(self, __context: Any) -> None:
        self._version = sha256_hex(canonical_json(self.model_dump(mode="json")))[:12]

    @property
    def version(self) -> str:
        """First 12 hex of sha256 over the canonical JSON of the validated settings."""
        return self._version

    def section(self, name: str) -> dict:
        """One top-level section as a plain dict (used by inputs_hash and the check registry)."""
        value = getattr(self, name)
        if isinstance(value, BaseModel):
            return value.model_dump(mode="json")
        return {name: value}


def load_settings(path: str | Path = "settings.yaml") -> Settings:
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return Settings.model_validate(data)


def settings_from_dict(data: dict) -> Settings:
    return Settings.model_validate(data)
