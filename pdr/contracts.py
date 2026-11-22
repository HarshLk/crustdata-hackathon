"""Lane seams (INTERFACES.md section 3). Frozen after W0: additive changes only, by PR labelled `contract`.

Implementations live elsewhere:
  Store            pdr/store.py (lane C, SQLite)      stub: pdr/store_mem.MemStore
  paid_call        pdr/broker/ (lane A)               stub raises NotImplementedError
  find_evidence    pdr/evidence/ (lane A)             stub returns status="none"
  engine           pdr/engine/ (lane B)
  adapters         pdr/adapters/ (lane D; K5, K7 lane C)
  llm_json         pdr/llm.py (lane A)                stub returns None
  guard            pdr/guard.py (lane D)              stub passes everything
  crashpoint       pdr/crashpoints.py (lane C)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, ContextManager, Literal, Optional, Protocol, runtime_checkable

from pdr.codes import ActionStatus, EventType, Stage
from pdr.models import (
    Action,
    Agreement,
    AuditFinding,
    CheckResult,
    Client,
    Deduction,
    Evidence,
    RecomputeLine,
    Term,
    Trip,
    Verdict,
    canonical_json,
    sha256_hex,
)
from pdr.settings import Settings


# --------------------------------------------------------------------------- errors

class BudgetReached(Exception):
    """Raised by paid_call when the run's credit budget or deadline would be exceeded."""


class GuardViolation(Exception):
    """Raised by guard.check when a payload may not leave the machine."""


# --------------------------------------------------------------------------- 3.1 Store

@runtime_checkable
class Store(Protocol):
    def tx(self) -> ContextManager: ...  # one SQLite transaction
    def put(self, entity: Any) -> None: ...  # upsert by models.entity_id(entity)
    def get(self, cls: Any, id: str) -> Any | None: ...  # cls: entity class or its name
    def find(self, cls: Any, **eq: Any) -> list[Any]: ...  # equality filters on top-level fields
    # stages, memoised by input hash (DESIGN 5.3)
    def save_stage(self, dispute_key: str, stage: Stage, inputs_hash: str, results: list[CheckResult]) -> None: ...
    def load_stage(self, dispute_key: str, stage: Stage, inputs_hash: str) -> list[CheckResult] | None: ...
    def unfinished(self) -> list[Deduction]: ...  # no current verdict
    # actions and trail
    def actions_in(self, status: ActionStatus) -> list[Action]: ...
    def trail_append(self, event: str, actor: str, subject_id: str, detail: dict) -> None: ...  # hash-chained
    def trail_verify(self) -> bool: ...
    # snapshots
    def snapshot_put(self, content_hash: str, url: str, text: str, html_path: str | None) -> None: ...
    def snapshot_get(self, content_hash: str) -> dict | None: ...
    # decision log (add-on E1, additive)
    def stage_saved_at(self, dispute_key: str, stage: Stage, inputs_hash: str) -> str | None: ...
    def evidence_considered_put(self, group_key: str, dispute_key: str, run_id: str, rows: list[dict]) -> None: ...
    def evidence_considered(self, group_key: str) -> list[dict]: ...
    def decision_log_append(self, entries: list[dict]) -> None: ...  # append-only; repeated entry ids ignored
    def decision_log(self, dispute_key: str, verdict_id: str | None = None) -> list[dict]: ...
    def decision_log_versions(self, dispute_key: str) -> list[str]: ...  # verdict ids, oldest first


# --------------------------------------------------------------------------- 3.2 broker

@dataclass
class CallResult:
    ok: bool
    status: int | None
    body: Any
    cache_hit: bool
    credits: Decimal
    ledger_entry_id: str
    error: str | None = None
    headers: dict | None = None  # raw response headers (additive, lane A: redirect Location)


@dataclass
class RunContext:
    run_id: str
    dispute_key: str | None
    settings: Settings
    store: Store
    deadline: datetime
    spent_credits: Decimal = Decimal("0")  # mutated by the broker as calls are ledgered

    def remaining_credits(self) -> Decimal:
        return Decimal(self.settings.budget.run_cost_credits) - self.spent_credits

    def past_deadline(self, now: datetime | None = None) -> bool:
        return (now or datetime.now(timezone.utc)) >= self.deadline


# def paid_call(provider: str, endpoint: str, request: dict, *,
#               ctx: RunContext, step: str, worst_case_credits: Decimal) -> CallResult
#     raises BudgetReached, GuardViolation; returns ok=False after retries (-> PROVIDER_FAILED)
class PaidCall(Protocol):
    def __call__(self, provider: str, endpoint: str, request: dict, *,
                 ctx: RunContext, step: str, worst_case_credits: Decimal) -> CallResult: ...


# --------------------------------------------------------------------------- 3.3 evidence

class PlaceRegions(dict):
    """Hashable, read-only mapping place -> tuple of its regions (district, state, regional state name)."""

    def __init__(self, data=None):
        items = data.items() if isinstance(data, dict) else (data or ())
        super().__init__((str(k), tuple(str(x) for x in (v or ()) if x)) for k, v in items)

    def __hash__(self) -> int:  # type: ignore[override]
        return hash(tuple(sorted(self.items())))

    def _read_only(self, *a, **k):
        raise TypeError("PlaceRegions is read-only")

    __setitem__ = __delitem__ = update = pop = popitem = clear = setdefault = _read_only


@dataclass(frozen=True)
class EvidenceRequest:
    """Deliberately no deduction, trip id, client or amount: only places, dates, event words.

    Lists passed in are stored as tuples so the request is hashable; key() is the evidence group key.
    """
    route_places: tuple[str, ...]
    route_states: tuple[str, ...]
    window: tuple[datetime, datetime]  # in-transit window, before allowance
    event_types: tuple[EventType, ...]  # only what the clause covers
    mode: Literal["asserted", "discovery"] = "asserted"
    # additive (b1): place -> (district, state, ...) so an ambiguous place name must appear with its region (D-27)
    place_regions: PlaceRegions = field(default_factory=PlaceRegions)

    def __post_init__(self) -> None:
        object.__setattr__(self, "place_regions", PlaceRegions(self.place_regions))
        object.__setattr__(self, "route_places", tuple(self.route_places))
        object.__setattr__(self, "route_states", tuple(self.route_states))
        object.__setattr__(self, "window", tuple(self.window))
        object.__setattr__(self, "event_types", tuple(EventType(e) for e in self.event_types))

    def key(self) -> str:
        return sha256_hex(canonical_json({
            "route_places": list(self.route_places),
            "route_states": list(self.route_states),
            "window": [w.isoformat() for w in self.window],
            "event_types": [e.value for e in self.event_types],
            "mode": self.mode,
            **({"place_regions": {k: list(v) for k, v in sorted(self.place_regions.items())}}
               if self.place_regions else {}),
        }))[:16]


@dataclass
class RejectedSource:
    url: str
    reason: str  # e.g. "blocked", "no_date", "out_of_window", "place_not_named", "short_page"
    detail: str = ""
    ledger_entry_id: str | None = None


@dataclass
class SearchLogLine:
    query: str
    window: tuple[datetime, datetime] | None
    n_results: int
    credits: Decimal = Decimal("0")
    cache_hit: bool = False
    ledger_entry_id: str | None = None
    # additive (a3): how this query's result links were resolved: direct / location / crustdata_fetch /
    # unresolved_redirect -> count
    redirects: dict[str, int] = field(default_factory=dict)


EvidenceStatus = Literal["found", "none", "undated_only", "budget", "provider_failed", "no_route_places"]


@dataclass
class EvidenceResult:
    status: EvidenceStatus
    supporting: list[Evidence] = field(default_factory=list)
    contradicting: list[Evidence] = field(default_factory=list)
    rejected: list[RejectedSource] = field(default_factory=list)
    search_log: list[SearchLogLine] = field(default_factory=list)  # cited by "valid" verdicts
    event_interval: tuple[datetime, datetime] | None = None  # from supporting passages


# def find_evidence(req: EvidenceRequest, ctx: RunContext) -> EvidenceResult
# def estimate_credits(req: EvidenceRequest, ctx: RunContext) -> Decimal
# def refute(evidence: Evidence, claims: list[str], ctx: RunContext) -> list[bool] | None   # None = did not finish
# def passage_link(url: str, passage: str) -> str                                           # url#:~:text=start,end


# --------------------------------------------------------------------------- 3.4 engine

@dataclass
class CheckContext:
    """What a registered check receives."""
    deduction: Deduction
    trip: Optional[Trip]
    client: Optional[Client]
    agreement: Optional[Agreement]
    terms: list[Term]
    prior: dict[Stage, list[CheckResult]]
    ctx: RunContext


CheckFn = Callable[[CheckContext], CheckResult]


@dataclass
class CheckSpec:
    """What @check registers (pdr/engine/registry.py, lane B)."""
    check_id: str
    stage: Stage
    fn: CheckFn
    needs_terms: list[str] = field(default_factory=list)  # term_type keys of models.TERM_PARAM_TYPES
    needs_events: list[str] = field(default_factory=list)  # timeline events
    settings: list[str] = field(default_factory=list)  # settings sections read (feeds inputs_hash)


# @check(stage="terms_checked", needs_terms=["penalty_rule", "cap"], needs_events=[], settings=["records"])
# def over_cap(c: CheckContext) -> CheckResult: ...
#
# def run(dispute_keys: list[str], ctx: RunContext) -> list[Verdict]
# def process(dispute_key: str, ctx: RunContext, *, upto: Stage = Stage.verdict) -> Verdict | None
# def recompute(deduction, trip, terms, corrections) -> tuple[Decimal, list[RecomputeLine]]    # pure
# def combine(deduction, checks: list[CheckResult], recomputed) -> Verdict                       # pure
# def audit(verdict, evidence: list[Evidence], ctx, *, with_refuter: bool) -> list[AuditFinding] # can only downgrade
# def inputs_hash(stage: Stage, deduction, trip, terms, settings, prior_hashes: list[str]) -> str # pure
# Every check runs inside a barrier: an exception becomes
#   CheckResult(status=INCOMPLETE, reason=INTERNAL_ERROR, missing_item="check <id> failed: <error class>")
_ENGINE_TYPES = (Verdict, RecomputeLine, AuditFinding)  # referenced by the signatures above


# --------------------------------------------------------------------------- 3.5 adapters

# def k1_load_clients(path, settings) -> list[Client]
# def k2_load_agreements(path, client_id) -> list[Agreement]
# def k3_load_trips(path, settings) -> list[Trip]
# def k4_load_deductions(path_or_text, settings, ctx) -> list[Deduction]
# def read_request(path_or_text, ctx) -> Request              # unsupported -> RequestType.unsupported
# def propose_schema_map(headers, sample_rows, source_kind, ctx) -> SchemaMap | None
# def validate_schema_map(m: SchemaMap, rows: list[dict]) -> list[str]   # empty = valid
# def k5_render(verdict: Verdict, evidence: list[Evidence], settings) -> dict

@runtime_checkable
class K7(Protocol):
    def capabilities(self) -> dict: ...  # {"can_check": bool, "dedupes": bool}
    def exists(self, action_id: str) -> bool: ...
    def deliver(self, action_id: str, body: dict, attachments: list[Path]) -> None: ...  # idempotent on action_id


# --------------------------------------------------------------------------- 3.6 - 3.8

# def llm_json(task: "extract_terms"|"parse_deduction"|"propose_passages"|"map_reason",
#              source_text: str, schema: type[BaseModel], ctx: RunContext) -> BaseModel | None
LlmTask = Literal["extract_terms", "parse_deduction", "propose_passages", "map_reason"]

# def check(destination, payload, client_id=None) -> None      # raises GuardViolation
# def pseudonymise(text, client_id) -> str
# def vault_read(driver_ref, who, purpose) -> dict
GuardDestination = Literal["crustdata_search", "crustdata_fetch", "crustdata_identify", "llm", "k7", "web"]

# def crashpoint(name: str) -> None   # os._exit(137) if os.environ["PDR_CRASH_AT"] == name
