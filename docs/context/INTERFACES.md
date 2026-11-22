# INTERFACES: the contracts every lane codes against

These are the seams between the four lanes in `WORKSTREAMS.md`. They exist so that four people (or agents) can build in parallel and have the pieces fit at the first integration.

**Rules for this file**

- It lands in the repo as code in task W0 (`pdr/models.py`, `pdr/codes.py`, `pdr/contracts.py`). After W0 merges, the code is the source of truth and this file is its explanation.
- Changing a contract after W0 needs a pull request labelled `contract`, announced in the team channel, and merged before anyone depends on it. Adding an optional field is fine; renaming or removing one is a contract change.
- Until the real implementation of a contract exists, the owning lane ships a **stub with the right signature** in its first hour, so other lanes are never blocked.

Types below are pydantic v2 models written in shorthand. `?` means optional. All datetimes are timezone-aware.

## 1. Vocabulary (`pdr/codes.py`)

```python
Outcome      = "valid" | "disputable" | "cannot_verify"

Ground       = NO_CONTRACTUAL_BASIS | WRONG_RATE | WRONG_BASE | OVER_CAP | DUPLICATE
             | WRONG_DAY_COUNT | NO_DELAY | WRONG_START_POINT | CLIENT_CAUSED
             | REGULATORY_HOLD | FORCE_MAJEURE

Reason       = TERMS_MISSING | TERMS_UNCONFIRMED | TRIP_NOT_FOUND | AMBIGUOUS_MATCH
             | RECORDS_CONFLICT | SOURCES_CONFLICT | EVIDENCE_PARTIAL | FM_WORDING_ARGUABLE
             | NO_FM_CLAUSE | EXPLANATION_IGNORED | SOURCE_UNDATED | NOTICE_EVIDENCE_MISSING
             | CAUSATION_PARTIAL | NO_EVIDENCE_FOUND | BUDGET_REACHED | PROVIDER_FAILED
             | LEGAL_JUDGMENT
             | SEARCH_NOT_ECONOMICAL | INTERNAL_ERROR          # added by D-28, D-25 (not in the PRD list)

Flag         = OUT_OF_WINDOW | INSTRUCTION_IN_INPUT | CLIENT_ADMISSION_CHECK
             | STAND_IN_TERMS | RESUBMISSION

CheckStatus  = GROUND | NO_GROUND | NOT_APPLICABLE | INCOMPLETE | REFER

Stage        = received | matched | terms_checked | records_checked | notice_checked
             | evidence_searched | verdict
             # free pass = received..notice_checked; evidence pass = evidence_searched; verdict includes the audit
ActionStatus = drafted | awaiting_approval | approved | sending | sent | held | rejected
ActionKind   = dispute_letter | document_request | fm_notice | payment_test
RequestType  = check_deductions | intake_agreement | status | unsupported

EventType    = strike | bandh | hartal | chakka_jam | blockade | agitation | flood | landslide
             | road_closure | curfew | govt_detention | riot | other
             # the vocabulary only; which ones a clause covers comes from its confirmed Term

ReasonCode   = late_delivery | late_placement | non_placement | late_reporting | late_paperwork
             | detention_charge | shortage | damage | other        # mapped from reason_text

RateUnit     = pct_per_week_or_part | pct_per_day | pct_per_vehicle_day | flat_per_day
             | per_quintal_day | per_instance
Base         = delayed_quantity_value | trip_cost | daily_hire | contract_value | invoice_value | none
DayCount     = calendar | working
TsSource     = client_gate_log | consignee_gate_log | weighbridge | pod_signed | eway_bill
             | gps | fastag | driver_report | client_stated | ops_note
```

`REASON_PRECEDENCE` also lives here: gate reasons, then `TERMS_*`, `RECORDS_CONFLICT`, `NOTICE_EVIDENCE_MISSING`, `FM_WORDING_ARGUABLE`, `NO_FM_CLAUSE`, `SOURCES_CONFLICT`, `SOURCE_UNDATED`, `EVIDENCE_PARTIAL`, `CAUSATION_PARTIAL`, `NO_EVIDENCE_FOUND`, `EXPLANATION_IGNORED`, `LEGAL_JUDGMENT`, `SEARCH_NOT_ECONOMICAL`, `BUDGET_REACHED`, `PROVIDER_FAILED`, `INTERNAL_ERROR`.

## 2. Entities (`pdr/models.py`)

Every entity has `client_id`, `created_at`, `source_ref: SourceRef`.

```python
SourceRef    { file: str, sheet?: str, row?: int, page?: int, field?: str }

Client       { client_id, legal_name, aliases: [str], crustdata_company_id?: int,
               identify_confidence?: float, sites: [str], lanes: [str],
               approvers: {role: name}, settings_overrides: dict }

Agreement    { agreement_id, client_id, doc_type, precedence_rank: int, title, file_ref,
               effective_from: date, effective_to?: date, supersedes?: str,
               sites: [str], lanes: [str], contract_value?: Decimal,
               completeness: "complete" | "missing", missing_documents: [str],
               is_stand_in: bool, confirmed_by?, confirmed_at? }

Term         { term_id, agreement_id, client_id, term_type, params: TermParams,
               page: int, clause_ref: str, verbatim: str,
               effective_from: date, effective_to?: date,
               extraction_confidence: float, status: "unconfirmed"|"confirmed"|"rejected",
               confirmed_by?, confirmed_at? }

# TermParams is one of (discriminated on term_type):
PenaltyRule  { event: ReasonCode, rate: Decimal, unit: RateUnit, base: Base,
               part_unit_counts_as_full?: bool,          # None = clause silent -> permitted readings
               day_count?: DayCount, measured_from?: str, measured_to?: str }
Cap          { value: Decimal, kind: "pct"|"abs", base: Base, scope: "per_trip"|"per_vehicle"|"per_period"|"contract"|None }
ForceMajeure { events_named: [EventType], general_words: [str], events_excluded: [EventType],
               decided_by?: str, relief: "excuse_delay" }
Notice       { deadline_hours?: float, immediate: bool, method?: str, addressee?: str, applies_to: "force_majeure"|"delay" }
TimeLimit    { window_days: int, day_count: DayCount, counted_from: "deduction_date"|"received_date"|"contract_expiry" }
BuyerDelay   { buyer_bears: bool, free_loading_hours?: float, free_unloading_hours?: float, min_indent_notice_hours?: float }
Definition   { name: "placement"|"reporting"|"delivery", event: str }       # which timeline event the word means
Waiver       { condition: "satisfactory_explanation"|"involuntary_breach", decided_by?: str }
HoldRule     { covers_govt_detention: bool }

Observation  { value: datetime, source: TsSource, ref: SourceRef, tz_assumed: bool }
Halt         { start: datetime, end: datetime, place?: str, lat?: float, lon?: float, source: TsSource, ref: SourceRef }
NoticeRecord { sent_at: datetime, channel: str, addressee: str, subject?: str, ref: SourceRef }
Assertion    { kind: "ops_note"|"driver_report"|"notice"|"memo", event_type?: EventType, text: str,
               at?: datetime, place?: str, ref: SourceRef }
Hold         { authority: str, place: str, held_from: datetime, released_at?: datetime, memo_ref?: SourceRef, reason?: str }

Trip         { trip_id, client_id, lr_no?, invoice_no?, eway_bill_no?, origin, destination, lane,
               route_places: [str], route_states: [str], vehicle_no,
               indent_issued_at: [Observation], placement_required_at?: datetime,
               reporting_at: [Observation], gate_out_at: [Observation],
               delivery_due_at?: datetime, arrival_at: [Observation], unloaded_at: [Observation],
               freight: Decimal?, quantity?: Decimal, quantity_unit?: str, delayed_quantity_value?: Decimal,
               halts: [Halt], holds: [Hold], notices: [NoticeRecord], assertions: [Assertion],
               driver_ref?: str }                       # never a name or phone

ClientCalc   { rate?: Decimal, unit?: RateUnit, base_amount?: Decimal, units_counted?: Decimal,
               from_ts?: datetime, to_ts?: datetime }   # the client's own stated calculation, if given

Deduction    { deduction_id, dispute_key, event_fp, client_id, client_name_raw,
               advice_no?, line_no?, client_deduction_no?, deduction_date: date, received_date?: date,
               amount: Decimal, currency: "INR", reason_text: str, reason_code: ReasonCode,
               trip_refs: [str], invoice_ref?, period?: (date, date), client_calc?: ClientCalc,
               stage: Stage, flags: [Flag] }

Citation     { kind: "contract_term"|"internal_record"|"external_source", evidence_id: str, label: str,
               link: str }                       # opens at the passage, page or row (DESIGN §8.6)

Evidence     { evidence_id, kind: "contract_term"|"internal_record"|"external_news"|"external_official",
               # contract_term:
               term_id?, doc_title?, page?, clause_ref?, verbatim?,
               # internal_record:
               record_type?, record_id?, field?, value?, timestamp?, ref?: SourceRef,
               # external:
               url?, passage?, published_at?: datetime, event_from?: date, event_to?: date,
               place_named?: str, retrieved_at?: datetime, retrieved_via?: "api"|"cache",
               fetch_status?: "ok"|"blocked"|"no_date", content_hash?, stance?: "supports"|"contradicts"|"partial",
               ledger_entry_id?,
               link: str,                        # url + text fragment | file#page=N | case-file anchor
               audit?: "passed"|"failed"|"refuted" }

CheckResult  { check_id: str, stage: Stage, status: CheckStatus, ground?: Ground, reason?: Reason,
               missing_item?: str, citations: [Citation], depends_on: [str],      # timeline events and term_ids
               correction?: Correction, flags: [Flag], note: str }

Correction   { kind: "rate"|"base"|"day_count"|"cap"|"timestamp"|"excused_interval"|"void",
               value: Any, order: int }            # consumed by the recomputation, DESIGN §5.4

Verdict      { verdict_id, deduction_id, dispute_key, outcome: Outcome, grounds: [Ground],
               primary_reason?: Reason, reasons: [Reason], flags: [Flag],
               amount_deducted: Decimal, recomputed_penalty?: Decimal, amount_disputable: Decimal,
               recomputation: [RecomputeLine], terms_applied: [str], evidence_ids: [str],
               missing_items: [str], unresolved: [str], explanation: str, next_action: str,
               what_would_change: [str], checks_run: [str], inputs_hash: str, audit: [AuditFinding],
               rule_version: str, settings_version: str, run_id: str, run_cost_credits: Decimal,
               run_cost_inr: Decimal, superseded_by?: str }

RecomputeLine{ step: str, description: str, value: Decimal, citations: [Citation] }

AuditFinding { citation_evidence_id: str, rule: str, passed: bool, detail: str }

Approval     { approval_id, action_id, role_required, approver?, decision?: "approve"|"reject"|"override",
               comment?, decided_at? }           # bound to action_id, which contains the content version

Action       { action_id,                        # idempotency key. dispute_letter: dispute_key + ":" + content_version[:12]
               kind: ActionKind, client_id, content_version: str,     # hash of the rendered content
               deduction_ids: [str], amount_claimed?: Decimal, body_ref: str, attachments: [str],
               required_roles: [str], status: ActionStatus, sent_at?, send_attempts: int }
               # The PRD's "Dispute" entity is an Action of kind dispute_letter.

SchemaMap    { header_hash, source_kind: "k3"|"k4", mapping: {column: field}, validated: bool,
               confirmed_by?, confirmed_at? }

Request      { request_type: RequestType, client_hint?: str, period?: (date, date), files: [str], raw_text: str }

LedgerEntry  { entry_id, run_id, dispute_key?: str, cache_key: str, step: str, provider: str, endpoint: str,
               units: Decimal, unit_kind: "credits"|"tokens_in"|"tokens_out"|"pages", unit_price_usd: Decimal,
               cost_usd: Decimal, cost_inr: Decimal, cache_hit: bool, attempt_no: int,
               outcome: "ok"|"empty"|"blocked"|"retry"|"failed"|"in_flight_at_crash",
               request_summary: str, started_at, ended_at }
```

## 3. Lane seams (`pdr/contracts.py`)

### 3.1 Store (owner: lane C; used by everyone)

```python
class Store:
    def tx(self) -> ContextManager                      # one SQLite transaction
    def put(self, entity) -> None                       # upsert by id, any entity above
    def get(self, cls, id) -> entity | None
    def find(self, cls, **eq) -> list[entity]           # equality filters on indexed columns
    # stages
    # stages, memoised by input hash (DESIGN §5.3)
    def save_stage(self, dispute_key, stage: Stage, inputs_hash: str, results: list[CheckResult]) -> None   # atomic with stage marker
    def load_stage(self, dispute_key, stage: Stage, inputs_hash: str) -> list[CheckResult] | None            # None = must run
    def unfinished(self) -> list[Deduction]             # no current verdict
    # actions and trail
    def actions_in(self, status: ActionStatus) -> list[Action]
    def trail_append(self, event: str, actor: str, subject_id: str, detail: dict) -> None    # hash-chained
    def trail_verify(self) -> bool
    # snapshots
    def snapshot_put(self, content_hash, url, text, html_path) -> None
    def snapshot_get(self, content_hash) -> dict | None
```

Stub for hour one: an in-memory dict version with the same methods (`pdr/store_mem.py`), shipped in W0.

### 3.2 Paid-call broker (owner: lane A; used by A, D's intake, C never)

```python
@dataclass
class CallResult:
    ok: bool; status: int | None; body: Any; cache_hit: bool
    credits: Decimal; ledger_entry_id: str; error: str | None

def paid_call(provider: str, endpoint: str, request: dict, *,
              ctx: RunContext, step: str, worst_case_credits: Decimal) -> CallResult
    # raises BudgetReached, GuardViolation; returns ok=False after retries (-> PROVIDER_FAILED)

class RunContext:  run_id; dispute_key | None; settings: Settings; store: Store; deadline: datetime
    def remaining_credits(self) -> Decimal
```

Lives in `pdr/broker/` (`core.py`, `exec.py` for the detached executor, `ledger.py`, `budget.py`). Provider transports register under a name (`crustdata`, `llm`, `web`). Tests register `FakeTransport(cassette_dir, faults=[...])` under the same names.

### 3.3 Evidence service (owner: lane A; caller: lane B's stage 6)

```python
@dataclass
class EvidenceRequest:
    route_places: list[str]; route_states: list[str]
    window: tuple[datetime, datetime]                    # in-transit window, before allowance
    event_types: list[EventType]                         # only what the clause covers
    mode: "asserted" | "discovery"

@dataclass
class EvidenceResult:
    status: "found" | "none" | "undated_only" | "budget" | "provider_failed" | "no_route_places"
    supporting: list[Evidence]; contradicting: list[Evidence]; rejected: list[RejectedSource]
    search_log: list[SearchLogLine]                      # query, window, n results, credits — cited by "valid" verdicts
    event_interval: tuple[datetime, datetime] | None     # from supporting passages

def find_evidence(req: EvidenceRequest, ctx: RunContext) -> EvidenceResult
def estimate_credits(req: EvidenceRequest, ctx: RunContext) -> Decimal      # for the economic gate; ledger average, else settings
def refute(evidence: Evidence, claims: list[str], ctx: RunContext) -> list[bool] | None   # independent read; None = did not finish
def passage_link(url: str, passage: str) -> str                             # url#:~:text=start,end
```

`EvidenceRequest` is hashable; its hash is the evidence group key used by the two-pass run.

`EvidenceRequest` deliberately has **no** deduction, trip id, client or amount in it. That is how "only route places, dates and event words go to Crustdata" is enforced by type.

Lane B applies corroboration, notice and causation; lane A only reports what the sources state.

### 3.4 Engine (owner: lane B; callers: lane C's CLI, lane D's runner)

```python
def run(dispute_keys: list[str], ctx: RunContext) -> list[Verdict]   # two passes: free stages for all, then evidence by group
def process(dispute_key: str, ctx: RunContext, *, upto: Stage = "verdict") -> Verdict | None   # memoised; idempotent
def recompute(deduction, trip, terms, corrections) -> tuple[Decimal, list[RecomputeLine]]   # pure
def combine(deduction, checks: list[CheckResult], recomputed) -> Verdict                      # pure
def audit(verdict, evidence: list[Evidence], ctx, *, with_refuter: bool) -> list[AuditFinding]   # can only downgrade
def inputs_hash(stage: Stage, deduction, trip, terms, settings, prior_hashes: list[str]) -> str  # pure

# the check registry (pdr/engine/registry.py)
@check(stage="terms_checked", needs_terms=["penalty_rule", "cap"], needs_events=[], settings=["records"])
def over_cap(c: CheckContext) -> CheckResult: ...
```

Every check runs inside a barrier: an exception becomes `CheckResult(status=INCOMPLETE, reason=INTERNAL_ERROR, missing_item="check <id> failed: <error class>")`. Checks must be safe to run twice.

### 3.5 Adapters (owner: lane D; K5 and K7 owner: lane C)

```python
# in
def k1_load_clients(path, settings) -> list[Client]
def k2_load_agreements(path, client_id) -> list[Agreement]             # files only; terms come from intake
def k3_load_trips(path, settings) -> list[Trip]                        # writes driver data to the vault, returns driver_ref
def k4_load_deductions(path_or_text, settings, ctx) -> list[Deduction] # spreadsheet, PDF, pasted text
# request reader (pdr/adapters/reader.py)
def read_request(path_or_text, ctx) -> Request                         # unsupported -> RequestType.unsupported, never a guess
def propose_schema_map(headers: list[str], sample_rows: list[dict], source_kind, ctx) -> SchemaMap | None
def validate_schema_map(m: SchemaMap, rows: list[dict]) -> list[str]   # problems; empty list = valid
# out
def k5_render(verdict: Verdict, evidence: list[Evidence], settings) -> dict   # field list, order and renames from settings
class K7:                                                                # LocalOutbox now; organiser endpoint at kickoff
    def capabilities(self) -> {"can_check": bool, "dedupes": bool}
    def exists(self, action_id: str) -> bool
    def deliver(self, action_id: str, body: dict, attachments: list[Path]) -> None   # idempotent on action_id
```

### 3.6 Model adapter (owner: lane A)

```python
def llm_json(task: "extract_terms"|"parse_deduction"|"propose_passages"|"map_reason",
             source_text: str, schema: type[BaseModel], ctx: RunContext) -> BaseModel | None
```

Goes through `paid_call`. Returns `None` on failure or schema mismatch; callers treat `None` as "the check did not finish", never as a negative finding. Prompts live in `pdr/prompts/` as files so a live fix can edit wording without touching code.

### 3.7 Guard and vault (owner: lane D, who also owns the K3 split)

```python
def check(destination: "crustdata_search"|"crustdata_fetch"|"crustdata_identify"|"llm"|"k7"|"web",
          payload: Any, client_id: str | None) -> None          # raises GuardViolation
def pseudonymise(text: str, client_id: str) -> str               # replaces vault values with driver_ref
def vault_read(driver_ref, who, purpose) -> dict                 # logs to pii_access_log
```

### 3.8 Crash points (owner: lane C)

```python
def crashpoint(name: str) -> None     # os._exit(137) if os.environ["PDR_CRASH_AT"] == name
```

Names in use, one line each in `pdr/crashpoints.py` so the kill test can enumerate them:
`stage.before_save.<stage>`, `stage.after_save.<stage>`, `output.before_rename`, `call.after_intent`, `call.after_response_before_cache`, `call.after_cache`, `send.after_lookup`, `send.after_intent`, `send.after_deliver`, `send.after_mark_sent`. Other lanes call `crashpoint()` at these places in their own code.

## 4. Settings (`settings.yaml`, schema in `pdr/settings.py`; owner: lane C)

```yaml
version_note: "kickoff"
budget:
  run_cost_credits: 40            # set at kickoff
  per_deduction_credits: 8
  run_time_seconds: 600
  credit_price_usd: 0.30
  usd_inr: 0                      # set at kickoff from a dated source; 0 refuses to start
providers:
  crustdata: { base_url: "https://api.crustdata.com", api_version: "2025-11-01", rate_per_minute: 10,
               retry_limit: 3, backoff_seconds: [2, 5, 12], timeout_seconds: 60 }
  llm:       { provider: anthropic, model_extract: claude-sonnet-5-5, model_parse: claude-haiku-5-5,
               retry_limit: 2, timeout_seconds: 60 }
  paidcall_mode: detached         # detached | inline (inline is automatic on Windows)
  paid_concurrency: 3             # paid calls in flight within one evidence group
evidence:
  disruption_types:               # type -> query words; limits what we SEARCH, never what a clause covers
    bandh: ["bandh"]
    strike: ["strike", "hartal"]
    chakka_jam: ["chakka jam"]
    blockade: ["blockade", "rasta roko"]
    flood: ["flood"]
    landslide: ["landslide"]
    road_closure: ["road closed", "highway closed"]
    curfew: ["curfew"]
    govt_detention: ["trucks stranded", "check post"]
  window_allowance_hours: 24
  max_searches_per_deduction: 3
  max_fetches_per_deduction: 5
  max_blocked_fetches: 3
  min_page_chars: 1500
  block_phrases: ["Verifying your request", "Just a moment", "Access Denied", "The request could not be satisfied"]
  human_mode_retry: true
  min_qualifying_sources: 1
  regional_query: true
  search_when_corroboration_missing: true
  search_for_referrals: false
  refuter: true                   # independent second read of passages that would carry a dispute
  sources: [crustdata_news]       # plus official-source adapters as added
economics:                        # never read by a verdict; only the gate reads the cost side
  search_gate_ratio: 1.0          # skip paid search when recoverable amount < ratio x estimated search cost; 0 disables
  estimated_search_credits: 7     # used until the ledger has its own average
  fee_rate_pct: 0                 # ASSUMPTION, set by the team (T-2); used only by `pdr economics`
  assumed_win_rate_pct: 0         # ASSUMPTION; used only by `pdr economics`
records:
  conflict_allowance_minutes: 60
causation:
  allowance_hours: 0              # set at kickoff; debated in the design question
approvals:
  send_dispute: [kam]
  above_threshold_inr: 0          # set at kickoff; 0 disables
  above_threshold_roles: [finance, legal]
  override_out_of_window: [kam]
escalation:
  legal_notice_above_inr: 0       # set by the 3PL
  arbitration_above_inr: 0
letter:
  response_days: 15
  grouping: per_dispute           # per_dispute | monthly_per_client
privacy:
  retention_days: 0               # set by the 3PL
output:
  format: jsonl
  fields:                         # name, then source path in Verdict or a derived-field name
    - [dispute_key, dispute_key]
    - [outcome, outcome]
    - [grounds, grounds]
    - [reason, primary_reason]
    - [amount_deducted, amount_deducted]
    - [recomputed_penalty, recomputed_penalty]
    - [amount_disputable, amount_disputable]
    - [cites, derived:citations]
    - [missing_items, missing_items]
    - [flags, flags]
    - [next_action, next_action]
    - [explanation, explanation]
    - [settings_version, settings_version]
    - [run_cost_inr, run_cost_inr]
  rename: { outcome: {}, fields: {} }
  case_file: { dir: out, regenerate_after_each_verdict: true }
adapters:
  k3: { field_map: {} }
  k4: { field_map: {} }
  k7: { kind: local_outbox, path: data/outbox }
event_watch:
  states: []
  queries_per_state: ["bandh call", "chakka jam"]
  lookahead_days: 7
  daily_credit_cap: 20
```

Values of `0` marked "set at kickoff" are deliberate: the service refuses to start until a person sets them, so no number we invented can pass as a 3PL's policy. None of these is a contract value.

`fee_rate_pct` and `assumed_win_rate_pct` are the exception to the refuse-to-start rule: at `0` the economics view prints only measured cost and the break-even rate.

Derived fields registered in `pdr/outputs/derived.py`: `citations`, `confidence_label`, `amount_in_words`, `escalation_hint`, `sources_count`, `checks_summary`, `what_would_change`.

## 5. File formats

### 5.1 K5 verdict line (default mapping)

```json
{"dispute_key":"ACME|PA20260412|3","outcome":"disputable","grounds":["FORCE_MAJEURE"],"reason":null,
 "amount_deducted":"2000.00","recomputed_penalty":"0.00","amount_disputable":"2000.00",
 "cites":[
   {"kind":"contract_term","doc":"Transport Agreement (stand-in)","effective":"2026-04-01","page":7,"clause":"14.1","text":"…road blockades and agitations…"},
   {"kind":"internal_record","record":"gps_halt","id":"TRIP-0091#halt2","field":"start/end","value":"2026-11-05T06:10+05:30/2026-11-05T21:40+05:30","source":"gps_2026-11.csv:row 412"},
   {"kind":"external_source","url":"https://…","passage":"…","published":"2026-11-06T…","event":"2026-11-03/2026-11-06","place":"Kini toll plaza","retrieved":"2026-11-23T09:41:10+05:30"}],
 "missing_items":[],"flags":["STAND_IN_TERMS"],"next_action":"hold for account manager",
 "explanation":"…","settings_version":"a41c…","run_cost_inr":"0.00"}
```

### 5.2 Log line

```json
{"ts":"…","run_id":"r-20261123-0941","dispute_key":"…","kind":"call","name":"crustdata:/web/search/live","outcome":"ok","cost_credits":1,"cost_inr":"…","ms":6012}
```

### 5.3 Outbox message (`data/outbox/<action_id>.json`)

```json
{"action_id":"…","kind":"dispute_letter","dispute_keys":["…"],"to":"<client legal name>","subject":"…","body_md":"…",
 "attachments":["pack.html"],"approved_by":[{"role":"kam","name":"…","at":"…"}],"content_version":"…","sent_at":"…"}
```

Written to a temp name, fsynced, renamed. The same rule applies to every output file (`verdicts.jsonl`, case file pages): rendered from the store, replaced atomically, never appended.

### 5.4 Test case folder (`tests/cases/caseNN_slug/`)

```
case.yaml            # id, title, PRD case number, settings overrides, which clients
deductions.csv|.txt  # the K4 input as the service would receive it
trips.json           # list[Trip] in logical-model JSON (kickoff shapes go through K3 tests instead)
clients.yaml         # client + lanes with route places
terms.yaml           # confirmed Terms for each client in the case (built from REFERENCE §3 clauses, is_stand_in: true)
cassette/            # recorded provider responses keyed by cache_key; empty for free-check cases
expected.json        # {dispute_key: {outcome, grounds, primary_reason, amount_disputable, flags, missing_item_contains, max_paid_calls}}
variants/            # optional: the same input with renamed or reordered columns, or as pasted text; must give the same key and verdict
```

`expected.json` is compared field by field. `max_paid_calls: 0` on free-check cases is how "free checks before paid calls" is tested.

## 6. SQL (lane C, `pdr/schema.sql`)

```sql
CREATE TABLE entities   (kind TEXT, id TEXT, client_id TEXT, k1 TEXT, k2 TEXT, body TEXT NOT NULL, created_at TEXT, PRIMARY KEY(kind,id));
CREATE INDEX entities_k ON entities(kind, client_id, k1, k2);   -- k1/k2: per-kind lookup keys (e.g. trip: lr_no, invoice_no; deduction: dispute_key, event_fp)
CREATE TABLE stage_results (dispute_key TEXT, stage TEXT, inputs_hash TEXT, results TEXT NOT NULL, settings_version TEXT, saved_at TEXT, PRIMARY KEY(dispute_key, stage, inputs_hash));
CREATE TABLE snapshots     (content_hash TEXT PRIMARY KEY, url TEXT, text TEXT, html_path TEXT, stored_at TEXT);
CREATE TABLE schema_maps   (header_hash TEXT PRIMARY KEY, body TEXT NOT NULL, confirmed_at TEXT);
CREATE TABLE audit_trail   (seq INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, event TEXT, actor TEXT, subject_id TEXT, detail TEXT, prev_hash TEXT, row_hash TEXT);
CREATE TABLE call_intents  (cache_key TEXT PRIMARY KEY, provider TEXT, endpoint TEXT, request TEXT, status TEXT, run_id TEXT, dispute_key TEXT, created_at TEXT);
CREATE TABLE call_cache    (cache_key TEXT PRIMARY KEY, status INTEGER, headers TEXT, body TEXT, stored_at TEXT);
CREATE TABLE ledger        (entry_id TEXT PRIMARY KEY, run_id TEXT, dispute_key TEXT, cache_key TEXT, body TEXT NOT NULL, cost_inr TEXT, cache_hit INTEGER, started_at TEXT);
CREATE TABLE throttle      (provider TEXT, at REAL);
CREATE TABLE runs          (run_id TEXT PRIMARY KEY, settings_version TEXT, started_at TEXT, ended_at TEXT, body TEXT);
CREATE TABLE settings_versions (settings_version TEXT PRIMARY KEY, body TEXT, loaded_at TEXT);
CREATE TABLE pii_access_log (id INTEGER PRIMARY KEY, at TEXT, who TEXT, trip_id TEXT, purpose TEXT);
-- vault.db
CREATE TABLE drivers (driver_ref TEXT PRIMARY KEY, client_id TEXT, name TEXT, phone TEXT, licence TEXT);
```

Clients, agreements, terms, trips, deductions, evidence, verdicts, approvals and actions all live in `entities` (one generic table keeps lane C's work small). Verdict immutability is enforced in `Store.put`.
