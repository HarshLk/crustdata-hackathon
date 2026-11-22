"""Entities shared by every lane (INTERFACES.md section 2), plus key and hashing helpers.

Rules:
- Money is Decimal. Datetimes are timezone-aware (naive datetimes are rejected; adapters
  read offset-less input as IST and set Observation.tz_assumed).
- Every entity has client_id, created_at, source_ref (see Entity).
- Term.params is a plain dict validated against the typed class for its term_type;
  call term.typed_params() to get the typed object.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from pdr.codes import (
    ActionKind,
    ActionStatus,
    Base,
    CheckStatus,
    DayCount,
    EventType,
    Flag,
    Ground,
    Outcome,
    RateUnit,
    Reason,
    ReasonCode,
    RequestType,
    Stage,
    TsSource,
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PdrModel(BaseModel):
    """Common base: unknown keys are ignored so older/newer JSON still loads."""
    model_config = ConfigDict(extra="ignore", coerce_numbers_to_str=True)


class SourceRef(PdrModel):
    file: str
    sheet: Optional[str] = None
    row: Optional[int] = None
    page: Optional[int] = None
    field: Optional[str] = None


class Entity(PdrModel):
    """Fields every entity carries. client_id is required on the client-scoped entities."""
    client_id: Optional[str] = None
    created_at: AwareDatetime = Field(default_factory=utcnow)
    source_ref: Optional[SourceRef] = None


# --------------------------------------------------------------------------- clients, agreements

class Client(Entity):
    client_id: str
    legal_name: str
    aliases: list[str] = Field(default_factory=list)
    crustdata_company_id: Optional[int] = None
    identify_confidence: Optional[float] = None
    sites: list[str] = Field(default_factory=list)
    lanes: list[str] = Field(default_factory=list)
    approvers: dict[str, str] = Field(default_factory=dict)  # role -> name
    settings_overrides: dict[str, Any] = Field(default_factory=dict)


class Agreement(Entity):
    agreement_id: str
    client_id: str
    doc_type: str
    precedence_rank: int = 0
    title: str
    file_ref: str
    effective_from: date
    effective_to: Optional[date] = None
    supersedes: Optional[str] = None
    sites: list[str] = Field(default_factory=list)
    lanes: list[str] = Field(default_factory=list)
    contract_value: Optional[Decimal] = None
    completeness: Literal["complete", "missing"] = "complete"
    missing_documents: list[str] = Field(default_factory=list)
    is_stand_in: bool = False
    confirmed_by: Optional[str] = None
    confirmed_at: Optional[AwareDatetime] = None


# --------------------------------------------------------------------------- term params

class PenaltyRule(PdrModel):
    event: ReasonCode
    rate: Decimal
    unit: RateUnit
    base: Base
    part_unit_counts_as_full: Optional[bool] = None  # None = clause silent -> permitted readings
    day_count: Optional[DayCount] = None
    measured_from: Optional[str] = None
    measured_to: Optional[str] = None


class Cap(PdrModel):
    value: Decimal
    kind: Literal["pct", "abs"]
    base: Base
    scope: Optional[Literal["per_trip", "per_vehicle", "per_period", "contract"]] = None


class ForceMajeure(PdrModel):
    events_named: list[EventType] = Field(default_factory=list)
    general_words: list[str] = Field(default_factory=list)
    events_excluded: list[EventType] = Field(default_factory=list)
    decided_by: Optional[str] = None
    relief: Literal["excuse_delay"] = "excuse_delay"


class Notice(PdrModel):
    deadline_hours: Optional[float] = None
    immediate: bool = False
    method: Optional[str] = None
    addressee: Optional[str] = None
    applies_to: Literal["force_majeure", "delay"]


class TimeLimit(PdrModel):
    window_days: int
    day_count: DayCount
    counted_from: Literal["deduction_date", "received_date", "contract_expiry"]


class BuyerDelay(PdrModel):
    buyer_bears: bool
    free_loading_hours: Optional[float] = None
    free_unloading_hours: Optional[float] = None
    min_indent_notice_hours: Optional[float] = None


class Definition(PdrModel):
    name: Literal["placement", "reporting", "delivery"]
    event: str  # which timeline event the word means


class Waiver(PdrModel):
    condition: Literal["satisfactory_explanation", "involuntary_breach"]
    decided_by: Optional[str] = None


class HoldRule(PdrModel):
    covers_govt_detention: bool


# term_type -> params class. Lanes may add a new term type here (additive contract change).
TERM_PARAM_TYPES: dict[str, type[PdrModel]] = {
    "penalty_rule": PenaltyRule,
    "cap": Cap,
    "force_majeure": ForceMajeure,
    "notice": Notice,
    "time_limit": TimeLimit,
    "buyer_delay": BuyerDelay,
    "definition": Definition,
    "waiver": Waiver,
    "hold_rule": HoldRule,
}
TermParams = PenaltyRule | Cap | ForceMajeure | Notice | TimeLimit | BuyerDelay | Definition | Waiver | HoldRule


class Term(Entity):
    term_id: str
    agreement_id: str
    client_id: str
    term_type: str  # a key of TERM_PARAM_TYPES
    params: dict[str, Any]
    page: int
    clause_ref: str
    verbatim: str
    effective_from: date
    effective_to: Optional[date] = None
    extraction_confidence: float = 0.0
    status: Literal["unconfirmed", "confirmed", "rejected"] = "unconfirmed"
    confirmed_by: Optional[str] = None
    confirmed_at: Optional[AwareDatetime] = None

    @model_validator(mode="before")
    @classmethod
    def _params_to_dict(cls, data: Any) -> Any:
        if isinstance(data, dict) and isinstance(data.get("params"), BaseModel):
            data = {**data, "params": data["params"].model_dump(mode="json")}
        return data

    @model_validator(mode="after")
    def _validate_params(self) -> "Term":
        cls = TERM_PARAM_TYPES.get(self.term_type)
        if cls is not None:
            # store the normalised JSON form so round-trips and hashes are stable
            self.params = cls.model_validate(self.params).model_dump(mode="json")
        return self

    def typed_params(self) -> PdrModel:
        cls = TERM_PARAM_TYPES.get(self.term_type)
        if cls is None:
            raise KeyError(f"unknown term_type {self.term_type!r}")
        return cls.model_validate(self.params)


# --------------------------------------------------------------------------- trip records

class Observation(PdrModel):
    value: AwareDatetime
    source: TsSource
    ref: SourceRef
    tz_assumed: bool = False


class Halt(PdrModel):
    start: AwareDatetime
    end: AwareDatetime
    place: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    source: TsSource
    ref: SourceRef


class NoticeRecord(PdrModel):
    sent_at: AwareDatetime
    channel: str
    addressee: str
    subject: Optional[str] = None
    ref: SourceRef


class Assertion(PdrModel):
    kind: Literal["ops_note", "driver_report", "notice", "memo"]
    event_type: Optional[EventType] = None
    text: str
    at: Optional[AwareDatetime] = None
    place: Optional[str] = None
    ref: SourceRef


class Hold(PdrModel):
    authority: str
    place: str
    held_from: AwareDatetime
    released_at: Optional[AwareDatetime] = None
    memo_ref: Optional[SourceRef] = None
    reason: Optional[str] = None


class Trip(Entity):
    trip_id: str
    client_id: str
    lr_no: Optional[str] = None
    invoice_no: Optional[str] = None
    eway_bill_no: Optional[str] = None
    origin: str
    destination: str
    lane: str
    route_places: list[str] = Field(default_factory=list)
    route_states: list[str] = Field(default_factory=list)
    vehicle_no: str
    indent_issued_at: list[Observation] = Field(default_factory=list)
    placement_required_at: Optional[AwareDatetime] = None
    reporting_at: list[Observation] = Field(default_factory=list)
    gate_out_at: list[Observation] = Field(default_factory=list)
    delivery_due_at: Optional[AwareDatetime] = None
    arrival_at: list[Observation] = Field(default_factory=list)
    unloaded_at: list[Observation] = Field(default_factory=list)
    freight: Optional[Decimal] = None
    quantity: Optional[Decimal] = None
    quantity_unit: Optional[str] = None
    delayed_quantity_value: Optional[Decimal] = None
    halts: list[Halt] = Field(default_factory=list)
    holds: list[Hold] = Field(default_factory=list)
    notices: list[NoticeRecord] = Field(default_factory=list)
    assertions: list[Assertion] = Field(default_factory=list)
    driver_ref: Optional[str] = None  # never a name or phone


# --------------------------------------------------------------------------- deductions

class ClientCalc(PdrModel):
    """The client's own stated calculation, if given."""
    rate: Optional[Decimal] = None
    unit: Optional[RateUnit] = None
    base_amount: Optional[Decimal] = None
    units_counted: Optional[Decimal] = None
    from_ts: Optional[AwareDatetime] = None
    to_ts: Optional[AwareDatetime] = None


class Deduction(Entity):
    deduction_id: str
    dispute_key: str
    event_fp: str
    client_id: str
    client_name_raw: str
    advice_no: Optional[str] = None
    line_no: Optional[str] = None
    client_deduction_no: Optional[str] = None
    deduction_date: date
    received_date: Optional[date] = None
    amount: Decimal
    currency: Literal["INR"] = "INR"
    reason_text: str
    reason_code: ReasonCode
    trip_refs: list[str] = Field(default_factory=list)
    invoice_ref: Optional[str] = None
    period: Optional[tuple[date, date]] = None
    client_calc: Optional[ClientCalc] = None
    stage: Stage = Stage.received
    flags: list[Flag] = Field(default_factory=list)


# --------------------------------------------------------------------------- checks, evidence, verdicts

class Citation(PdrModel):
    kind: Literal["contract_term", "internal_record", "external_source"]
    evidence_id: str
    label: str
    link: str  # opens at the passage, page or row (DESIGN 8.6)


class Evidence(Entity):
    evidence_id: str
    kind: Literal["contract_term", "internal_record", "external_news", "external_official"]
    # contract_term
    term_id: Optional[str] = None
    doc_title: Optional[str] = None
    page: Optional[int] = None
    clause_ref: Optional[str] = None
    verbatim: Optional[str] = None
    # internal_record
    record_type: Optional[str] = None
    record_id: Optional[str] = None
    field: Optional[str] = None
    value: Optional[Any] = None
    timestamp: Optional[AwareDatetime] = None
    ref: Optional[SourceRef] = None
    # external
    url: Optional[str] = None
    passage: Optional[str] = None
    published_at: Optional[AwareDatetime] = None
    event_from: Optional[date] = None
    event_to: Optional[date] = None
    place_named: Optional[str] = None
    retrieved_at: Optional[AwareDatetime] = None
    retrieved_via: Optional[Literal["api", "cache"]] = None
    fetch_status: Optional[Literal["ok", "blocked", "no_date"]] = None
    content_hash: Optional[str] = None
    stance: Optional[Literal["supports", "contradicts", "partial"]] = None
    ledger_entry_id: Optional[str] = None
    link: str  # url + text fragment | file#page=N | case-file anchor
    audit: Optional[Literal["passed", "failed", "refuted"]] = None


class Correction(PdrModel):
    kind: Literal["rate", "base", "day_count", "cap", "timestamp", "excused_interval", "void"]
    value: Any = None
    order: int = 0  # consumed by the recomputation, DESIGN 5.4


class CheckResult(PdrModel):
    check_id: str
    stage: Stage
    status: CheckStatus
    ground: Optional[Ground] = None
    reason: Optional[Reason] = None
    missing_item: Optional[str] = None
    citations: list[Citation] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)  # timeline events and term_ids
    correction: Optional[Correction] = None
    flags: list[Flag] = Field(default_factory=list)
    note: str = ""


class RecomputeLine(PdrModel):
    step: str
    description: str
    value: Decimal
    citations: list[Citation] = Field(default_factory=list)


class AuditFinding(PdrModel):
    citation_evidence_id: str
    rule: str
    passed: bool
    detail: str = ""


class Verdict(Entity):
    verdict_id: str
    deduction_id: str
    dispute_key: str
    outcome: Outcome
    grounds: list[Ground] = Field(default_factory=list)
    primary_reason: Optional[Reason] = None
    reasons: list[Reason] = Field(default_factory=list)
    flags: list[Flag] = Field(default_factory=list)
    amount_deducted: Decimal
    recomputed_penalty: Optional[Decimal] = None
    amount_disputable: Decimal = Decimal("0")
    recomputation: list[RecomputeLine] = Field(default_factory=list)
    terms_applied: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    missing_items: list[str] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)
    explanation: str = ""
    next_action: str = ""
    what_would_change: list[str] = Field(default_factory=list)
    checks_run: list[str] = Field(default_factory=list)
    inputs_hash: str = ""
    audit: list[AuditFinding] = Field(default_factory=list)
    rule_version: str = ""
    settings_version: str = ""
    run_id: str = ""
    run_cost_credits: Decimal = Decimal("0")
    run_cost_inr: Decimal = Decimal("0")
    superseded_by: Optional[str] = None


# --------------------------------------------------------------------------- approvals, actions

class Approval(Entity):
    approval_id: str
    action_id: str  # bound to action_id, which contains the content version
    role_required: str
    approver: Optional[str] = None
    decision: Optional[Literal["approve", "reject", "override"]] = None
    comment: Optional[str] = None
    decided_at: Optional[AwareDatetime] = None


class Action(Entity):
    """The PRD's "Dispute" entity is an Action of kind dispute_letter."""
    action_id: str  # idempotency key. dispute_letter: dispute_key + ":" + content_version[:12]
    kind: ActionKind
    client_id: str
    content_version: str  # hash of the rendered content
    deduction_ids: list[str] = Field(default_factory=list)
    amount_claimed: Optional[Decimal] = None
    body_ref: str = ""
    attachments: list[str] = Field(default_factory=list)
    required_roles: list[str] = Field(default_factory=list)
    status: ActionStatus = ActionStatus.drafted
    sent_at: Optional[AwareDatetime] = None
    send_attempts: int = 0


class SchemaMap(Entity):
    header_hash: str
    source_kind: Literal["k3", "k4"]
    mapping: dict[str, str] = Field(default_factory=dict)  # column -> field
    validated: bool = False
    confirmed_by: Optional[str] = None
    confirmed_at: Optional[AwareDatetime] = None


class Request(Entity):
    request_type: RequestType
    client_hint: Optional[str] = None
    period: Optional[tuple[date, date]] = None
    files: list[str] = Field(default_factory=list)
    raw_text: str = ""


class LedgerEntry(Entity):
    entry_id: str
    run_id: str
    dispute_key: Optional[str] = None
    cache_key: str
    step: str
    provider: str
    endpoint: str
    units: Decimal = Decimal("0")
    unit_kind: Literal["credits", "tokens_in", "tokens_out", "pages"] = "credits"
    unit_price_usd: Decimal = Decimal("0")
    cost_usd: Decimal = Decimal("0")
    cost_inr: Decimal = Decimal("0")
    cache_hit: bool = False
    attempt_no: int = 1
    outcome: Literal["ok", "empty", "blocked", "retry", "failed", "in_flight_at_crash"]
    request_summary: str = ""
    started_at: AwareDatetime
    ended_at: Optional[AwareDatetime] = None


# --------------------------------------------------------------------------- ids

# The one id rule every lane uses: entities are keyed by (class name, value of this field).
ENTITY_ID_FIELD: dict[str, str] = {
    "Client": "client_id",
    "Agreement": "agreement_id",
    "Term": "term_id",
    "Trip": "trip_id",
    "Deduction": "dispute_key",
    "Evidence": "evidence_id",
    "Verdict": "verdict_id",
    "Approval": "approval_id",
    "Action": "action_id",
    "SchemaMap": "header_hash",
    "LedgerEntry": "entry_id",
}

ENTITY_CLASSES: dict[str, type[Entity]] = {
    c.__name__: c
    for c in (Client, Agreement, Term, Trip, Deduction, Evidence, Verdict, Approval, Action, SchemaMap, LedgerEntry)
}


def entity_kind(cls_or_obj: Any) -> str:
    """Class name used as the entity kind; accepts a class, an instance or a name."""
    if isinstance(cls_or_obj, str):
        return cls_or_obj
    if isinstance(cls_or_obj, type):
        return cls_or_obj.__name__
    return type(cls_or_obj).__name__


def entity_id(obj: Any) -> str:
    kind = entity_kind(obj)
    try:
        return str(getattr(obj, ENTITY_ID_FIELD[kind]))
    except KeyError:
        raise TypeError(f"{kind} is not a stored entity") from None


# --------------------------------------------------------------------------- keys and hashing

_NON_ALNUM = re.compile(r"[^A-Z0-9]")


def norm_key(s: Any) -> str:
    """Upper-case and keep only ASCII letters and digits. None -> ''."""
    if s is None:
        return ""
    return _NON_ALNUM.sub("", str(s).upper())


def _json_default(o: Any) -> Any:
    if isinstance(o, BaseModel):
        return o.model_dump(mode="json")
    if isinstance(o, Decimal):
        return str(o)
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    if isinstance(o, Enum):
        return o.value
    if isinstance(o, (set, frozenset)):
        return sorted(o, key=lambda x: canonical_json(x))
    if isinstance(o, tuple):
        return list(o)
    if is_dataclass(o) and not isinstance(o, type):
        return asdict(o)
    raise TypeError(f"not JSON serialisable: {type(o).__name__}")


def canonical_json(obj: Any) -> str:
    """Stable JSON: sorted keys, no whitespace, Decimal as str, datetimes ISO 8601."""
    return json.dumps(obj, default=_json_default, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(s: str | bytes) -> str:
    if isinstance(s, str):
        s = s.encode("utf-8")
    return hashlib.sha256(s).hexdigest()


def make_dispute_key(
    client_id: str,
    advice_no: Optional[str],
    line_no: Optional[Any],
    client_deduction_no: Optional[str] = None,
    fallback_fields: Optional[dict[str, Any]] = None,
) -> str:
    """Deterministic dispute key (DESIGN 5.7).

    1. advice_no present      -> "CLIENT|ADVICE|LINE"        (line '' if absent)
    2. client_deduction_no    -> "CLIENT|N:DEDNO"
    3. fallback_fields        -> "CLIENT|H:<16 hex of normalised fields>"
    Every part is norm_key'd, so "PA/2026/0412" and "pa-2026-0412" give the same key.
    """
    c = norm_key(client_id)
    if not c:
        raise ValueError("client_id is required for a dispute_key")
    if norm_key(advice_no):
        return f"{c}|{norm_key(advice_no)}|{norm_key(line_no)}"
    if norm_key(client_deduction_no):
        return f"{c}|N:{norm_key(client_deduction_no)}"
    if fallback_fields:
        normed = {str(k): norm_key(v) for k, v in fallback_fields.items()}
        return f"{c}|H:{sha256_hex(canonical_json(normed))[:16]}"
    raise ValueError("need advice_no, client_deduction_no or fallback_fields for a dispute_key")


def make_event_fp(client_id: str, trip_id: str, reason_code: Any) -> str:
    """Duplicate-detection fingerprint: 16 hex of hash(client, trip, reason_code)."""
    rc = reason_code.value if isinstance(reason_code, Enum) else str(reason_code)
    return sha256_hex(f"{norm_key(client_id)}|{norm_key(trip_id)}|{rc}")[:16]
