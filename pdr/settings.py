"""Settings schema (INTERFACES.md section 4). Owner: lane C.

No field here may hold a contract value (rate, cap, window, notice period, FM list).
Sections allow extra keys so a lane or the hour-3 change can add a key in settings.yaml
before the schema catches up; lane C may tighten this.
Unset (0) "set at kickoff" values refuse to start via load_for_start (D-19).
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
    base_url: str = ""  # openai_compatible only: e.g. https://api.openai.com/v1
    api_key_env: str = ""  # name of the env var holding the key; "" = ANTHROPIC_API_KEY / OPENAI_API_KEY
    prices: dict[str, dict[str, Decimal]] = Field(default_factory=dict)  # model -> usd_per_mtok_in / usd_per_mtok_out


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


class SettingsError(ValueError):
    """Settings are invalid or a kickoff value is unset: the service refuses to start."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("settings refused:\n  - " + "\n  - ".join(problems))


# D-19: values a person must set at kickoff. At 0 the service refuses to start.
# economics.fee_rate_pct and economics.assumed_win_rate_pct are deliberately absent (0 = not assumed).
KICKOFF_FIELDS: list[str] = [
    "budget.run_cost_credits",
    "budget.per_deduction_credits",
    "budget.run_time_seconds",
    "budget.credit_price_usd",
    "budget.usd_inr",
    "causation.allowance_hours",
    "approvals.above_threshold_inr",
    "escalation.legal_notice_above_inr",
    "escalation.arbitration_above_inr",
    "privacy.retention_days",
]

DEFAULT_PATH = Path(__file__).resolve().parents[1] / "settings.yaml"


def default_settings_path() -> Path:
    """$PDR_SETTINGS, else ./settings.yaml, else the repository's settings.yaml."""
    import os
    env = os.environ.get("PDR_SETTINGS")
    if env:
        return Path(env)
    return Path("settings.yaml") if Path("settings.yaml").exists() else DEFAULT_PATH


def deep_merge(base: dict, over: dict) -> dict:
    """Recursive dict merge; `over` wins; lists are replaced, not merged."""
    out = dict(base)
    for k, v in (over or {}).items():
        out[k] = deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def _validate(data: dict) -> Settings:
    from pydantic import ValidationError
    try:
        return Settings.model_validate(data)
    except ValidationError as e:
        raise SettingsError([f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()]) from None


def load_settings(path: str | Path = "settings.yaml", overrides: dict | None = None) -> Settings:
    """Load and validate; `overrides` is a per-run dict merged over the file (tests, client overrides)."""
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return _validate(deep_merge(data, overrides or {}))


def settings_from_dict(data: dict, overrides: dict | None = None) -> Settings:
    return _validate(deep_merge(data, overrides or {}))


def with_overrides(settings: Settings, overrides: dict) -> Settings:
    """A new Settings with `overrides` merged in (version recomputed)."""
    return _validate(deep_merge(settings.model_dump(mode="json"), overrides or {}))


def kickoff_problems(settings: Settings) -> list[str]:
    """Kickoff values still at 0 (D-19)."""
    problems = []
    for dotted in KICKOFF_FIELDS:
        value: Any = settings
        for part in dotted.split("."):
            value = getattr(value, part)
        if value is None or Decimal(str(value)) == 0:
            problems.append(f"{dotted} is 0: set it at kickoff (D-19); the service will not start with it unset")
    return problems


def load_for_start(path: str | Path | None = None, overrides: dict | None = None) -> Settings:
    """What every command uses: validate the file, then refuse to start on unset kickoff values."""
    p = Path(path) if path else default_settings_path()
    if not p.exists():
        raise SettingsError([f"settings file not found: {p}"])
    try:
        s = load_settings(p, overrides)
    except yaml.YAMLError as e:
        raise SettingsError([f"{p}: not valid YAML: {e}"]) from None
    problems = kickoff_problems(s)
    if problems:
        raise SettingsError(problems)
    return s


def record_version(store: Any, settings: Settings) -> str:
    """Store this settings version in `settings_versions` (SQLite stores only); returns the version."""
    if hasattr(store, "execute"):
        from datetime import datetime, timezone
        store.execute("INSERT OR IGNORE INTO settings_versions(settings_version, body, loaded_at) VALUES(?,?,?)",
                      (settings.version, canonical_json(settings.model_dump(mode="json")),
                       datetime.now(timezone.utc).isoformat()))
    return settings.version
