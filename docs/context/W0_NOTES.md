# W0 notes: choices made where INTERFACES.md was ambiguous

Read with INTERFACES.md. The code (`pdr/codes.py`, `pdr/models.py`, `pdr/contracts.py`) is now the source of truth.

## Vocabulary (`pdr/codes.py`)
- All enums are `str` Enums; value == name (so `Stage.matched == "matched"`, `Ground.OVER_CAP == "OVER_CAP"`).
- `REASON_PRECEDENCE`: gate reasons are `AMBIGUOUS_MATCH`, `TRIP_NOT_FOUND` (in that order), then the list from INTERFACES. Helper `primary_reason(reasons)` picks the first present.
- Added `STAGE_ORDER`, `FREE_STAGES` (received..notice_checked), `GATE_REASONS`.
- No `TermType` enum: `Term.term_type` is a plain `str` key of `models.TERM_PARAM_TYPES` (`penalty_rule, cap, force_majeure, notice, time_limit, buyer_delay, definition, waiver, hold_rule`), so a live fix can add a term type without an enum change.

## Entities (`pdr/models.py`)
- Common base `PdrModel` (`extra="ignore"`, `coerce_numbers_to_str=True` so `line_no=3` becomes `"3"`).
- `Entity` base gives every entity `client_id`, `created_at` (default now, UTC), `source_ref`. `client_id` is **required** on Client, Agreement, Term, Trip, Deduction, Action; **optional** elsewhere (Verdict, Evidence, Approval, SchemaMap, Request, LedgerEntry). `source_ref` is **optional** on entities (Verdicts/Actions have no source file). On the value objects (Observation, Halt, NoticeRecord, Assertion) `ref` is **required**: they are what records cite.
- All datetimes are pydantic `AwareDatetime`: naive datetimes are **rejected**. Adapters must attach +05:30 to offset-less input and set `Observation.tz_assumed=True`.
- Money is `Decimal`; it serialises to JSON as a string (`"2000.00"`), as in K5.
- `Term.params` is a `dict`, validated on construction against `TERM_PARAM_TYPES[term_type]` and stored in normalised JSON form (Decimals as strings). Use `term.typed_params()` for the typed object. You may pass a typed object (e.g. `params=Cap(...)`). Unknown term types are kept unvalidated; `typed_params()` raises `KeyError` for them.
- `Deduction.line_no` is `str | None`. `Deduction.stage` defaults to `received`.
- Defaults chosen so fixtures stay short: lists default empty; `Verdict.amount_disputable`/`run_cost_*` default 0; `Action.status` defaults `drafted`; `Term.status` defaults `unconfirmed`; `Agreement.completeness` defaults `complete`; `Correction.order` 0.
- Added `LedgerEntry.ended_at` optional (in-flight entries have no end).

## Ids and keys
- `ENTITY_ID_FIELD` / `entity_id(obj)` / `entity_kind(x)`: entities are keyed by (class name, id). **Deduction is keyed by `dispute_key`** (not `deduction_id`). LedgerEntry by `entry_id`. Request and CheckResult are not stored as entities.
- `norm_key(s)`: upper-case, keep ASCII A-Z0-9 only; `None -> ""`.
- `make_dispute_key(client_id, advice_no, line_no, client_deduction_no=None, fallback_fields=None)`:
  1. advice_no present -> `CLIENT|ADVICE|LINE` (line is `""` if absent), e.g. `ACME|PA20260412|3`;
  2. else client_deduction_no -> `CLIENT|N:DEDNO`;
  3. else fallback_fields -> `CLIENT|H:<16 hex>` over the norm_key'd field values;
  4. else `ValueError`. The `N:`/`H:` markers cannot collide with normalised parts.
- `make_event_fp(client_id, trip_id, reason_code)`: first 16 hex of sha256 of `NORM(client)|NORM(trip)|reason_code`.
- `canonical_json(obj)`: sorted keys, compact, Decimal -> str (not re-quantised: `1.0` and `1.00` hash differently), datetime/date -> ISO, Enum -> value, tuple/set -> list, pydantic models and dataclasses dumped.

## Settings (`pdr/settings.py`, `settings.yaml`)
- Sections allow extra keys (so the hour-3 change can add a key before the schema). Lane C may tighten to `forbid`.
- `Settings.version`: first 12 hex of sha256 over the canonical JSON of the validated settings, computed at load. Mutating a loaded Settings object does not recompute it; reload instead.
- `settings.section(name)` returns the section as a JSON-mode dict (Decimals as strings).
- Demo values: `usd_inr 88.0`, `above_threshold_inr 100000`, `causation.allowance_hours 12`, `legal_notice_above_inr 50000`, `arbitration_above_inr 500000`, `retention_days 180`. Refuse-to-start rule not implemented (lane C).
- `evidence.disruption_types` keys are validated as `EventType`.

## Seams (`pdr/contracts.py`)
- `Store` is a `runtime_checkable` Protocol. `get`/`find` accept the class or its name. `find` filters on top-level attribute equality. `snapshot_put(..., html_path)` accepts `None`.
- `RunContext` is a dataclass with mutable `spent_credits`; `remaining_credits() = budget.run_cost_credits - spent_credits`; `past_deadline()` helper added.
- `EvidenceRequest` is a frozen dataclass; list arguments are converted to **tuples** (needed for hashing) and event types to `EventType`. Order is preserved, not sorted: callers should pass places in route order consistently. `key()` = 16 hex of canonical JSON. `mode` defaults to `"asserted"`.
- `RejectedSource(url, reason, detail, ledger_entry_id)` and `SearchLogLine(query, window, n_results, credits, cache_hit, ledger_entry_id)` field lists were not specified; these are W0's choice.
- `CheckSpec` dataclass added as what `@check` registers (lane B owns the decorator in `pdr/engine/registry.py`).
- `CallResult.error` defaults to `None`.

## Stubs
- `MemStore.put` enforces verdict immutability (only `superseded_by` may change). `save_stage` advances `Deduction.stage` forward only. `unfinished()` = deductions with no non-superseded Verdict for their dispute_key.
- `LocalOutbox` file name replaces characters other than alnum, `-`, `_`, `.` with `_`; plain write, skips if the file exists. Lane C makes it atomic.
- `guard.check` logs a warning once ("GUARD STUB ACTIVE").
