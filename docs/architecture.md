# Architecture

A set of narrow agents around a deterministic adjudicator. **Models read, code decides, people authorise.**
Source: DESIGN.md sections 1, 3, 5.3, 6 and 7. Every box below is a module in `pdr/`.

## Components and data flow

```mermaid
flowchart TB
  REQ["Request: files + optional instruction"] --> RR["Request reader<br/>schema mapper, validated<br/>pdr/adapters/reader.py"]
  RR --> K4["K4 deductions"]
  RR --> K3["K3 trips"]
  RR --> K1["K1 clients and lanes"]
  K3 -->|"driver name, phone"| VAULT[("vault.db")]
  K2["K2 agreements"] --> IR["Intake reader<br/>proposes Terms<br/>pdr/intake/"]
  IR --> CONFIRM{{"APPROVAL POINT 1<br/>person confirms terms,<br/>marks set complete"}}
  CONFIRM --> STORE

  K4 --> P1
  subgraph ADJ["Adjudicator: deterministic, stages memoised by input hash (pdr/engine/)"]
    direction TB
    P1["Pass 1, free, all deductions:<br/>1 received, 2 matched, 3 terms,<br/>4 records, 5 notice + FM wording"]
    P1 -->|"settled"| V["7 verdict: recompute + combine"]
    P1 -->|"open question, clause covers, notice ok"| G{"Economic gate<br/>+ budget"}
    G -->|"grouped by route and dates"| S6["Pass 2: 6 evidence"]
    S6 --> V
  end
  ADJ <--> STORE[("state.db")]

  S6 --> SCOUT["Evidence scout<br/>pdr/evidence/"]
  SCOUT --> BROKER
  RR --> BROKER
  IR --> BROKER
  subgraph BROKER["Paid-call broker (pdr/broker/)"]
    direction TB
    B1["guard"] --> B2["cache"] --> B3["budget"] --> B4["throttle 10/min"] --> B5["detached executor<br/>RETRIES + backoff"]
  end
  B5 -->|"DATA CALL"| CRUST["Crustdata<br/>/web/search/live<br/>/web/enrich/live<br/>/company/identify"]
  B5 -->|"DATA CALL"| LLM["Model provider<br/>/v1/messages"]
  B5 -->|"DATA CALL, free"| OFF["Publisher pages<br/>redirect resolution, official pages"]
  BROKER --> CACHE[("call cache<br/>+ page snapshots")]
  BROKER --> LEDGER[("COST LEDGER<br/>one row per call")]

  V --> AUD["Auditor: every citation re-verified,<br/>independent refuter read"]
  AUD -->|"pass"| OUT["K5 verdicts + HTML case file"]
  AUD -->|"fail: downgrade"| OUT
  OUT --> ACT["Action drafted<br/>letter, document request, notice"]
  ACT --> APPROVAL{{"APPROVAL POINT 2<br/>roles from settings,<br/>bound to content version"}}
  APPROVAL --> AUD2["Auditor re-check, free"]
  AUD2 --> CLERK["Clerk: send once<br/>sending, then sent"]
  CLERK --> GUARD2["guard"] --> K7[("K7 outbox")]
  APPROVAL --> TRAIL[("audit trail, hash-chained")]
  CONFIRM --> TRAIL
  CLERK --> TRAIL
  ADJ --> LOG["JSONL logs"]
  BROKER --> LOG
  CLERK --> LOG
```

### Legend

| Mark | Where | What it means |
| --- | --- | --- |
| DATA CALL | Broker -> Crustdata, model provider, publisher pages | The only places the service leaves the machine. Each goes through `pdr.broker.paid_call`: guard, cache, budget, throttle, retries, ledger. |
| APPROVAL POINT 1 | Term confirmation | No extracted term is used until a named person confirms it; the agreement set must be marked complete for a "no contractual basis" ground. |
| APPROVAL POINT 2 | Approval gate before K7 | Nothing irreversible happens without approval rows for that exact action id (the id contains the content hash; editing the letter voids the approval). Roles come from `settings.yaml` (`approvals.*`). |
| RETRIES | Detached executor | Timeouts, 429 and 5xx are retried with backoff (`providers.crustdata.retry_limit`, `backoff_seconds`); one ledger row per attempt; exhausted retries give `PROVIDER_FAILED` for that deduction only. |
| COST LEDGER | Broker | One row per external or model call, including retries, blocked fetches and zero-cost cache hits; cost read from Crustdata's `x-credits-used` header. `pdr ledger`, `pdr economics`. |
| Auditor | Before writing a disputable verdict, and again before sending | Can only downgrade (to cannot verify) or hold; never upgrades. |
| vault.db | K3 adapter | Driver names and phones are split off at the door; the engine sees only `driver_ref`. |

## Send-once state machine (one Action)

```mermaid
stateDiagram-v2
  [*] --> drafted: verdict disputable, letter rendered from template + citations
  drafted --> awaiting_approval: required roles computed from settings
  awaiting_approval --> approved: approval rows for this action_id (all roles)
  awaiting_approval --> rejected: pdr reject
  awaiting_approval --> held: OUT_OF_WINDOW flag (until pdr override)
  approved --> held: auditor re-check fails
  approved --> sending: one transaction, attempt + 1
  sending --> sent: K7 deliver(action_id) then one transaction
  sending --> sent: on restart, K7 exists(action_id) is true
  sending --> sending: on restart, K7 has no message, so redeliver with the same id
  sending --> held: K7 can neither check nor de-duplicate
  sent --> [*]
  rejected --> [*]
```

Every command starts by reconciling actions left in `sending` against K7 by `action_id`. The local K7 outbox writes
one file per `action_id` by atomic rename, so "one message per dispute" is `ls data/outbox`.

## Memoised stages (restart = re-run)

```mermaid
flowchart LR
  IN["stage inputs:<br/>deduction, trip, confirmed terms,<br/>settings sections the stage reads,<br/>hashes of earlier stages"] --> H["inputs_hash"]
  H --> Q{"stage_results has<br/>(dispute_key, stage, inputs_hash)?"}
  Q -->|"yes"| REUSE["reuse saved CheckResults<br/>no work, no paid call"]
  Q -->|"no"| RUN["run the stage's checks<br/>paid calls go through broker cache"]
  RUN --> SAVE["save CheckResults in one transaction"]
  REUSE --> NEXT["next stage"]
  SAVE --> NEXT
```

A kill, a settings change, a new document and a changed client record are all handled the same way: run the same
command again. Unchanged stages are reused. Changed ones recompute, and any paid call they repeat is a cache hit
ledgered at zero cost.
