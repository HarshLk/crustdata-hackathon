# Architecture

A set of narrow readers around a deterministic adjudicator. **Models read, code decides, people authorise.**
Every box below is a real module in `pdr/`; the diagrams follow the code on `main`, and the last section lists
where the code differs from `docs/context/DESIGN.md`.

Labels used throughout: **DATA CALL** (something leaves the machine), **APPROVAL** (a named person must act),
**RETRIES** (automatic retry with backoff), **COST LEDGER** (one row per call), **PERSONAL DATA** (the guard and
vault boundary).

## 1. System overview

Files come in on the left: the client's deduction advice (K4), trip records (K3), client list (K1) and agreements
(K2). Agreement terms are proposed by a reader and used only after a person confirms them. The adjudicator then
works through seven stages; only stage 6 (evidence) spends money, and every outside call goes through one broker
that checks the guard, cache and budget, retries, and writes the cost ledger. Results come out as a verdict file
and an HTML case file; a dispute letter is sent only after approval, exactly once, into the outbox (K7).

```mermaid
flowchart LR
  subgraph IN["Inputs"]
    K4["K4 deductions"]
    K3["K3 trips"]
    K1["K1 clients and lanes"]
    K2["K2 agreements PDF or text"]
  end
  RR["Request reader + schema mapper<br/>adapters/reader.py, schema_map.py"]
  IR["Agreement intake<br/>intake/extract.py proposes Terms"]
  CONF{{"APPROVAL 1<br/>person confirms terms<br/>intake/confirm.py"}}
  K4 --> RR
  K3 --> RR
  K1 --> RR
  K2 --> IR --> CONF
  RR -->|"driver name, phone"| VAULT[("PERSONAL DATA<br/>vault.db")]

  subgraph ADJ["Adjudicator pdr/engine/"]
    P1["Pass 1, free<br/>stages 1 to 5"]
    GATE{"economic gate<br/>+ budget"}
    S6["Pass 2<br/>stage 6 evidence"]
    S7["Stage 7 verdict<br/>recompute, combine"]
    AUD["Auditor<br/>engine/audit.py"]
    P1 -->|"settled"| S7
    P1 -->|"open delay question"| GATE --> S6 --> S7
    S7 --> AUD
  end
  RR --> P1
  CONF --> P1

  subgraph BRK["Paid-call broker pdr/broker/"]
    GUARD["PERSONAL DATA<br/>guard.check"]
    CB["cache, budget, intent"]
    EX["execute, throttle<br/>RETRIES + backoff"]
    GUARD --> CB --> EX
  end
  S6 --> GUARD
  RR -->|"unmapped headers, reasons"| GUARD
  IR -->|"model term reading"| GUARD
  AUD -->|"refuter read"| GUARD
  EX -->|"DATA CALL"| CRUST["Crustdata<br/>search, fetch, identify"]
  EX -->|"DATA CALL"| LLM["Model provider"]
  EX -->|"DATA CALL, free"| WEB["Web: google redirect probe"]
  EX --> LEDGER[("COST LEDGER<br/>ledger table")]
  EX --> CACHE[("call_cache + snapshots")]

  AUD --> K5["verdicts.jsonl<br/>outputs/k5.py"]
  AUD --> HTML["HTML case file<br/>outputs/report.py"]
  AUD --> ACT["Action drafted<br/>actions.py + outputs/letter.py"]
  ACT --> APR{{"APPROVAL 2<br/>approval.py"}}
  APR --> SEND["send-once + re-audit<br/>send.py"]
  SEND --> G2["PERSONAL DATA<br/>guard.check k7"]
  G2 --> K7[("K7 outbox<br/>adapters/k7_outbox.py")]

  ADJ <--> STATE[("state.db<br/>entities, stage_results")]
  CONF --> TRAIL[("audit_trail<br/>hash-chained")]
  APR --> TRAIL
  SEND --> TRAIL
  ADJ --> LOGS["JSONL logs<br/>log.py"]
```

| Mark | Where in code | What it means |
| --- | --- | --- |
| DATA CALL | `pdr/broker/core.py` `paid_call` | The only way anything leaves the machine. Crustdata (`/web/search/live`, `/web/enrich/live`, `/company/identify`) through `pdr/evidence/crustdata.py`; the model through `pdr/llm.py` `llm_json`; the free web redirect probe through `pdr/evidence/redirect.py`. |
| APPROVAL 1 | `pdr/intake/confirm.py` `confirm`, `complete_set` | No extracted term is used until a named person confirms it; "no contractual basis" needs the set marked complete. |
| APPROVAL 2 | `pdr/approval.py` `approve`, `reject`, `override` | Approval rows are bound to the `action_id`, which contains the content hash; editing the letter voids them. Roles come from `settings.yaml`. |
| RETRIES | `pdr/broker/core.py` `execute` | Timeouts, 429 and 5xx retried up to `retry_limit` with backoff, jitter and `retry-after`. Exhausted retries give `PROVIDER_FAILED` for that deduction only. Blocked pages get one extra `human_mode` fetch (`pdr/evidence/pagecheck.py`). |
| COST LEDGER | `ledger` table, `pdr/broker/ledger.py`, `pdr/economics.py` | One row per attempt, per cache hit (zero cost) and per call killed in flight (`in_flight_at_crash`, assumed charged). |
| PERSONAL DATA | `pdr/guard.py`, `pdr/vault.py` | Driver names and phones are split into `vault.db` at the door; the engine sees only `driver_ref`. The guard checks every outbound payload. |

## 2. Verdict pipeline

Each deduction runs through seven stages. Stages 1 to 5 are free and run for every deduction in pass 1; anything
that settles early (no match, missing terms, no delay left to excuse) skips straight to the verdict. Deductions that
still need public evidence are grouped by route and dates for pass 2, where an economic gate refuses searches that
cost more than they could recover. A stage whose inputs have not changed is reused from `stage_results`, and a
disputable verdict is audited before it is written.

```mermaid
flowchart TB
  START["scheduler.run: pass 1<br/>process upto notice_checked"] --> MEMO{"stage_results has<br/>dispute_key, stage, inputs_hash?"}
  MEMO -->|"yes, not transient"| REUSE["reuse saved CheckResults"]
  MEMO -->|"no"| BARRIER["missing_check_modules<br/>fail closed: INTERNAL_ERROR"]
  BARRIER --> S1["1 received<br/>received_instruction_scan"]
  REUSE --> S1
  S1 --> S2["2 matched<br/>gate_match"]
  S2 -->|"INCOMPLETE: TRIP_NOT_FOUND, AMBIGUOUS_MATCH,<br/>TERMS_MISSING, TERMS_UNCONFIRMED"| S7
  S2 --> S3["3 terms_checked<br/>contractual_basis, rate_version, wrong_base,<br/>over_cap, day_count, duplicate, dispute_window,<br/>explanation_waiver, legal_judgment"]
  S3 --> S4["4 records_checked<br/>delay_at_all, start_point, client_caused,<br/>regulatory_hold, delay_remaining"]
  S4 --> UD{"unexplained_delay?"}
  UD -->|"no: settled, void ground,<br/>INCOMPLETE or penalty 0"| S7
  UD -->|"yes"| S5["5 notice_checked<br/>notice_in_window, fm_wording_check"]
  S5 --> PLAN{"evidence_plan?"}
  PLAN -->|"none: NOTICE_EVIDENCE_MISSING,<br/>NO_FM_CLAUSE"| S7
  PLAN -->|"pending"| GROUP["group by EvidenceRequest.key<br/>GROUP_TOTALS per route and dates"]
  GROUP --> PASS2["pass 2: process upto verdict"]
  PASS2 --> EG{"economic gate<br/>at stake below ratio x cost?"}
  EG -->|"yes: SEARCH_NOT_ECONOMICAL"| S7
  EG -->|"no"| S6["6 evidence_searched<br/>evidence_search"]
  S6 --> S7["7 verdict: decide<br/>penalty.recompute, combine"]
  S7 --> AUDQ{"outcome disputable?"}
  AUDQ -->|"yes"| AUD["audited_verdict<br/>audit, up to 4 rounds,<br/>downgrade only"]
  AUDQ -->|"no"| SAVE
  AUD --> SAVE["one transaction:<br/>put Verdict, supersede old,<br/>save_stage verdict"]
  DL["run deadline or budget reached"] -->|"BUDGET_REACHED"| BV["budget_verdict"]
  BV --> SAVE
```

- **Memoisation** (`pdr/engine/pipeline.py` `run_stage`, `stage_hash`, `pdr/engine/hashing.py`): the hash covers
  the deduction, trip, confirmed terms, the settings sections the stage's checks declare, and the hashes of earlier
  stages. Results with a transient reason (for example `PROVIDER_FAILED`) are re-run.
- **Fail closed**: a stage whose check module did not import, or which has no registered checks, yields
  `INCOMPLETE / INTERNAL_ERROR`, never `NO_GROUND`. An audit that raises downgrades every ground to `INTERNAL_ERROR`.
- **Always answer**: `scheduler.run` gives every key still undecided a `budget_verdict` (`BUDGET_REACHED`).
- Stage 6 exits (`pdr/engine/evidence_stage.py`): `EVIDENCE_PARTIAL`, `NO_EVIDENCE_FOUND`, `SOURCE_UNDATED`,
  `SOURCES_CONFLICT`, `PROVIDER_FAILED`, `BUDGET_REACHED`, `CAUSATION_PARTIAL`, `FM_WORDING_ARGUABLE` (refer).

## 3. A paid call

Every outside call follows the same steps. The guard runs first, so a payload with personal data is never sent.
A cached answer is reused at zero cost (still ledgered). Otherwise the worst-case cost is reserved against the
run and per-deduction budgets, an intent row is written, and a detached child process does the call so that a kill
of the main process cannot lose a paid response.

```mermaid
sequenceDiagram
  participant E as caller crustdata.py or llm.py or redirect.py
  participant B as broker paid_call
  participant G as guard.check
  participant DB as state.db broker tables
  participant X as detached child pdr.broker.exec
  participant P as provider
  E->>B: paid_call provider, endpoint, request, worst_case_credits
  B->>G: check destination, payload
  G-->>B: ok, or GuardViolation and nothing is sent
  B->>DB: call_cache lookup by cache_key
  alt cache hit
    DB-->>B: stored response
    B->>DB: COST LEDGER row, cost 0, cache_hit
    B-->>E: CallResult cache_hit
  else miss
    B->>DB: _recover stale intent, ledger in_flight_at_crash
    B->>B: _reserve worst case, else BudgetReached
    B->>DB: call_intents row pending
    B->>X: run_detached, web calls run inline
    loop each attempt up to retry_limit plus 1
      X->>DB: throttle.acquire, 10 a minute
      X->>P: DATA CALL
      P-->>X: response, 429, 5xx or timeout
      X->>DB: COST LEDGER row per failed attempt, RETRIES with backoff
    end
    X->>DB: one transaction: call_cache + ledger + intent done
    DB-->>B: parent polls cache
    B-->>E: CallResult, ok false after retries means PROVIDER_FAILED
  end
  Note over B,X: Kill mid-call: the child finishes and caches, so the re-run is a cache hit.<br/>If the child died too, the pending intent is ledgered in_flight_at_crash and re-issued.
```

Crash points named in the code: `call.after_intent`, `call.after_response_before_cache`, `call.after_cache`
(`pdr/crashpoints.py`, triggered with `PDR_CRASH_AT`).

## 4. Evidence search

When a 3PL says a disruption (a strike, a flood, a road closure) caused the delay, the scout looks for a dated
public report. Searches use only route places, dates and event words. Pages are checked for block pages, saved as
snapshots, and read: a model may propose passages, but code decides whether a passage qualifies by a four-part test
(verbatim quote, place, date, effect). News alone never wins a dispute; it must be corroborated by the 3PL's own
records.

```mermaid
flowchart TB
  REQ["EvidenceRequest<br/>engine/evidence_stage.build_request"] --> LAD["query ladder<br/>evidence/queries.py plan, ladder"]
  LAD --> SRCH["crustdata.search<br/>DATA CALL, COST LEDGER"]
  SRCH -->|"no results, call failed"| PF["provider_failed"]
  SRCH --> SNIP{"snippet_ok?<br/>names a route place or state"}
  SNIP -->|"no"| REJ["RejectedSource<br/>snippet_no_place"]
  SNIP -->|"yes"| RES["redirect.resolve<br/>free web probe, 3xx Location only"]
  RES -->|"unresolved or google page"| REJ2["RejectedSource<br/>unresolved_redirect"]
  RES --> FETCH["pagecheck.fetch_checked<br/>crustdata.fetch, batches of 10"]
  FETCH --> PC{"check_page: blocked?"}
  PC -->|"yes, once"| HUM["fetch again human_mode<br/>RETRIES"]
  HUM --> PC2{"still blocked?"}
  PC2 -->|"yes"| REJ3["RejectedSource blocked"]
  PC2 -->|"no"| SNAP
  PC -->|"no"| SNAP["snapshot<br/>snapshots table by content_hash"]
  SNAP --> PUB{"pubdate found?"}
  PUB -->|"no"| UND["no_date, may give SOURCE_UNDATED"]
  PUB -->|"yes"| CAND["candidates.candidate_sentences"]
  CAND -->|"none"| REJ4["no_candidates, no model call"]
  CAND --> PROP["llm_json propose_passages<br/>else candidates.propose fallback"]
  PROP --> TEST["qualify.test_support<br/>verbatim, place, date, effect"]
  TEST --> CONTRA["qualify.test_contradictions"]
  CONTRA --> RESULT["EvidenceResult<br/>supporting, contradicting, rejected"]
  RESULT --> CORR["evidence_stage.corroborate<br/>own records at the place in the interval"]
  CORR --> CAUS["causation: excused hours<br/>vs penalised delay"]
```

The scout stops at the first ladder rung that yields a qualifying source, and respects `max_fetches_per_deduction`
and `max_blocked_fetches`. Link resolution (`pdr/evidence/redirect.py`) on `main` sends our own GET with redirects
off through the broker as provider `web`, caches only 3xx answers (a Google "Verifying" interstitial is retried
later), and rejects anything that lands on google.com. This module is being revised; the diagram shows `main` today.

## 5. Action lifecycle

A dispute letter, a document request and a force-majeure notice are all Actions with one protocol. The letter is
rendered from a template, never written by a model. It waits for approval from the roles the settings require,
is re-audited for free before sending, and is sent exactly once: after a crash, every action left in `sending` is
checked against the outbox by its id before anything else happens.

```mermaid
stateDiagram-v2
  [*] --> drafted: draft_for_verdict, guard k7 on body
  drafted --> awaiting_approval: required_roles from settings
  awaiting_approval --> approved: approval rows for this action_id, all roles
  awaiting_approval --> rejected: pdr reject
  awaiting_approval --> held: OUT_OF_WINDOW flag
  held --> approved: pdr override
  drafted --> rejected: content changed, superseded draft
  approved --> held: reaudit fails, verdict changed, deduction already sent
  approved --> sending: one transaction, send_attempts plus 1
  sending --> sent: k7.deliver then mark sent
  sending --> sent: reconcile, k7.exists is true
  sending --> sending: reconcile, absent, redeliver same id
  sending --> held: reconcile, K7 cannot check or dedupe, or guard violation
  sent --> [*]
  rejected --> [*]
```

`pdr/send.py` `send_all` crash points: `send.after_lookup`, `send.after_intent`, `send.after_deliver`,
`send.after_mark_sent`. `pdr/cli.py` calls `send.reconcile` at the start of every command. The local outbox writes
one file per `action_id` by temp file and atomic rename, so "one message per dispute" is `ls data/outbox`.

## 6. Data model

Logical entities (`pdr/models.py`) are stored as JSON bodies in one `entities` table keyed by `(kind, id)`. The
other tables hold memoised stage results, the broker's write-ahead intents, cache and ledger, page snapshots, the
hash-chained audit trail, and housekeeping. Driver identity lives in a separate `vault.db`.

```mermaid
erDiagram
  Client ||--o{ Agreement : "has"
  Agreement ||--o{ Term : "yields, confirmed by a person"
  Client ||--o{ Trip : "has"
  Client ||--o{ Deduction : "issues"
  Deduction }o--o{ Trip : "trip_refs"
  Deduction ||--o{ STAGE_RESULTS : "dispute_key"
  Deduction ||--o{ Verdict : "dispute_key, superseded_by"
  Verdict }o--o{ Evidence : "evidence_ids via Citation"
  Verdict }o--o{ Term : "terms_applied"
  Verdict ||--o{ Action : "dispute_keys"
  Action ||--o{ Approval : "action_id"
  Evidence }o--o| SNAPSHOTS : "content_hash"
  Trip }o--o| DRIVERS : "driver_ref, vault.db"
  CALL_INTENTS ||--o| CALL_CACHE : "cache_key"
  CALL_CACHE ||--o{ LEDGER : "cache_key"
  RUNS ||--o{ LEDGER : "run_id"
  Deduction ||--o{ LEDGER : "dispute_key"
  RUNS }o--|| SETTINGS_VERSIONS : "settings_version"
  Client { string client_id PK }
  Agreement { string agreement_id PK }
  Term { string term_id PK }
  Trip { string trip_id PK }
  Deduction { string deduction_id PK }
  Verdict { string verdict_id PK }
  Evidence { string evidence_id PK }
  Action { string action_id PK }
  Approval { string approval_id PK }
  STAGE_RESULTS { string dispute_key PK }
  SNAPSHOTS { string content_hash PK }
  CALL_INTENTS { string cache_key PK }
  CALL_CACHE { string cache_key PK }
  LEDGER { string entry_id PK }
  RUNS { string run_id PK }
  SETTINGS_VERSIONS { string settings_version PK }
  DRIVERS { string driver_ref PK }
```

| Table (`pdr/schema.sql`) | Key | Holds |
| --- | --- | --- |
| `entities` | `(kind, id)` | Client, Agreement, Term, Trip, Deduction, Evidence, Verdict, Approval, Action, SchemaMap, Request, LedgerEntry bodies |
| `stage_results` | `(dispute_key, stage, inputs_hash)` | CheckResults of one stage for one set of inputs |
| `call_intents`, `call_cache` | `cache_key` | write-ahead record and raw response of each paid call (broker only) |
| `ledger` | `entry_id` | COST LEDGER: one row per attempt, cache hit, blocked fetch, killed call |
| `throttle` | none | timestamps for the shared 10-a-minute limiter |
| `snapshots` | `content_hash` | extracted text and raw HTML path of each fetched page |
| `schema_maps` | `header_hash` | confirmed column-to-field mappings |
| `audit_trail` | `seq` | confirmations, approvals, overrides, sends, each with `prev_hash` and `row_hash` |
| `runs`, `settings_versions`, `pii_access_log` | `run_id`, `settings_version`, `id` | housekeeping |
| `vault.db`: `drivers`, `pii_access_log` | `driver_ref` | driver name, phone, licence; every read logged |

## 7. What leaves the machine

Only four destinations exist, and `pdr/guard.py` `check` inspects every payload before it goes. Every destination
is scanned for any driver name, phone or licence held in the vault. Search queries are kept short and generic so
that nothing about the client, the amount or the dispute reaches a search provider.

| Destination (guard name) | What is sent | Never sent | Enforced where |
| --- | --- | --- | --- |
| Crustdata search (`crustdata_search`) | route place names, dates, event words | amounts, ids, words like penalty or invoice, queries over 10 words or 120 chars, phones, licences, vault values | `guard._check_search`, `_pii_patterns`, `_vault_hits`; called in `broker.paid_call` |
| Crustdata identify (`crustdata_identify`) | bare company names | numbers, ids, relationship words, emails, vault values | `guard._check_identify` |
| Crustdata fetch and web probe (`crustdata_fetch`, `web`) | public `http(s)` URLs | anything that is not a public URL, vault values | `guard._check_urls` |
| Model provider (`llm`) | article text, agreement text, column headers, reason text | phones, licences, vault values | `guard.check` on `llm_json` payload via broker |
| K7 outbox (`k7`) | letter body, action id, business references | phones, licences, vault values | `actions.draft_for_verdict`, `adapters/k7_outbox.py` `deliver` |

```mermaid
flowchart LR
  PAY["outbound payload"] --> VH{"_vault_hits:<br/>driver name, phone, licence?"}
  VH -->|"hit"| STOP["GuardViolation<br/>nothing sent"]
  VH -->|"clean"| DEST{"destination"}
  DEST -->|"crustdata_search"| QS{"_check_search<br/>short, no amount, id or relationship"}
  DEST -->|"crustdata_identify"| QI{"_check_identify<br/>bare company name"}
  DEST -->|"crustdata_fetch or web"| QU{"_check_urls<br/>public URL only"}
  DEST -->|"llm or k7"| QP{"_pii_patterns<br/>no phone or licence"}
  QS -->|"fail"| STOP
  QI -->|"fail"| STOP
  QU -->|"fail"| STOP
  QP -->|"fail"| STOP
  QS -->|"pass"| OUT["sent through broker or K7"]
  QI -->|"pass"| OUT
  QU -->|"pass"| OUT
  QP -->|"pass"| OUT
```

## 8. Module map

Lanes: **A** everything that costs money, **B** the adjudicator, **C** what must happen once and what people see,
**D** what comes in, personal data and documentation.

| Module | Responsibility | Lane |
| --- | --- | --- |
| `pdr/broker/core.py` | `paid_call`: guard, cache, budget, intent, execute with retries, cache + ledger | A |
| `pdr/broker/exec.py` | detached child executor, `paid_calls_parallel` | A |
| `pdr/broker/db.py`, `throttle.py`, `transport.py`, `http.py` | broker connection, shared rate limit, transports | A |
| `pdr/broker/ledger.py` | `rows`, `totals` readers of the COST LEDGER | A |
| `pdr/evidence/crustdata.py` | Crustdata `search`, `fetch`, `identify` | A |
| `pdr/evidence/redirect.py` | resolve google redirect links, free | A |
| `pdr/evidence/scout.py` | `find_evidence`: ladder to EvidenceResult | A |
| `pdr/evidence/queries.py`, `pagecheck.py`, `pubdate.py` | query ladder, block-page check and snapshot, publication date | A |
| `pdr/evidence/candidates.py`, `qualify.py` | candidate sentences, deterministic proposer, four-part test | A |
| `pdr/evidence/refuter.py`, `links.py` | independent re-read, passage links | A |
| `pdr/llm.py`, `pdr/prompts/` | `llm_json` model adapter and prompts | A |
| `pdr/engine/registry.py`, `pipeline.py`, `scheduler.py`, `hashing.py` | check registry, stages, memoisation, two passes | B |
| `pdr/engine/gate.py` | stages 1 and 2 | B |
| `pdr/engine/checks_terms.py`, `checks_records.py`, `checks_notice.py` | stages 3, 4, 5 | B |
| `pdr/engine/evidence_stage.py` | stage 6, economic gate, corroboration, causation | B |
| `pdr/engine/penalty.py`, `combine.py`, `audit.py`, `explain.py` | stage 7: recompute, combine, auditor, explanation | B |
| `pdr/engine/timeline.py`, `calendar.py` | which record wins, day counting | B |
| `pdr/store.py`, `store_mem.py`, `schema.sql` | SQLite and in-memory stores | C |
| `pdr/settings.py`, `runctx.py`, `log.py`, `crashpoints.py` | settings, run context, JSONL logs, crash points | C |
| `pdr/actions.py`, `approval.py`, `send.py` | draft, approve, send once, reconcile | C |
| `pdr/outputs/k5.py`, `derived.py`, `report.py`, `letter.py`, `atomic.py` | verdicts.jsonl, HTML case file, letter, atomic writes | C |
| `pdr/cli.py`, `cli_actions.py`, `doctor.py`, `economics.py` | CLI, preflight, money views | C |
| `pdr/adapters/reader.py`, `schema_map.py`, `reason_map.py`, `k1` to `k4` | request reader and input adapters | D |
| `pdr/adapters/k5_output.py`, `k6_runner.py`, `k7_outbox.py` | output, runner and outbox adapters | D |
| `pdr/intake/`, `cli_intake.py` | agreement intake and term confirmation | D |
| `pdr/guard.py`, `vault.py` | outbound guard and driver vault | D |

## Differences from the design doc

- **Throttle sits inside each attempt.** DESIGN 6 lists throttle as a step before execute; in `broker/core.py`
  `execute` acquires the throttle before every attempt, including retries.
- **Web calls never run detached.** `paidcall_mode` forces `inline` for provider `web`, for in-memory stores and on
  Windows; Crustdata and model calls are `detached` by default (`settings.yaml`).
- **Redirect probes cache only 3xx.** A 200 (Google interstitial) is not cached and the link is rejected as
  `unresolved_redirect`; DESIGN 8.3 describes a plain Location read.
- **The verdict is audited up to four rounds** and can only be downgraded (`pipeline.no_raise`); an audit that
  raises turns grounds into `INTERNAL_ERROR`. DESIGN 8.5 describes a single pass.
- **Stage 5 runs only if a delay is unexplained** (`pipeline.unexplained_delay`), and stage 6 additionally needs an
  `evidence_plan`; DESIGN 5.3 runs stage 5 for every deduction in pass 1.
- **Pass 2 runs group members one after another.** DESIGN 5.3 describes bounded concurrency inside a group; in
  `scheduler.run` the first member pays for the search and the others reuse it through the call cache and the
  run memo (`evidence_stage.reset_run_memo`), with the gate applied to the group total (`GROUP_TOTALS`).
- **Two `pii_access_log` tables** exist: one in `state.db` (schema) and the one actually written in `vault.db`.
- **Undated pages** are rejected as `no_date`; `SOURCE_UNDATED` is returned only when such a page also had candidate
  sentences.
