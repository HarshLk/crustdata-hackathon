# Penalty-Deduction Recovery (`pdr`)

A service that checks every penalty a client deducts from a 3PL's payment against the client's own agreement, the
3PL's records and dated public evidence. It says for each deduction whether it is **valid**, **disputable** (with the
amount to recover) or **cannot verify** (naming what is missing), and sends each approved dispute exactly once.

Built at ContextCon 05 (22 Nov 2026). Design: [`docs/architecture.md`](docs/architecture.md); context documents in
`docs/context/`.

## The six things the brief asks for

| Field | Answer |
| --- | --- |
| **Job** | Decide, for each penalty a client deducts from a 3PL's payment, whether the client's own agreement and the evidence allow it. Then recover what they don't. |
| **Who pays for it today** | In India, the 3PL's billing staff spend hours per case, so most deductions are written off; lawyers and arbitration follow for large sums (TCI fought a Rs 62.8 lakh deduction for 12 years and lost for want of records made at the time). In the US, suppliers pay recovery firms a share of what they recover. Buyer here: the 3PL's finance controller (see [`docs/finance.md`](docs/finance.md)). |
| **Input** | (1) The client's payment advice, deduction note, spreadsheet, PDF or pasted email (K4). (2) The 3PL's trip records: dispatch, reporting, loading, delivery times, POD, gate logs, GPS halts, notices sent, ops notes (K3). (3) The client's agreement set: master, schedules, amendments, referenced general terms (K2), with terms confirmed by a person. (4) Client list and lanes (K1). Formats: [`docs/input_format.md`](docs/input_format.md). |
| **Finished result** | Per deduction: one verdict line (K5, `out/verdicts.jsonl`) citing clause, page and verbatim text, record rows, and dated sources with passage, publication date and retrieval time; the amount to recover; an evidence pack in a static case file (`out/index.html`). Per approved dispute: one letter in the K7 outbox, sent exactly once. Per paid call: one cost-ledger row. |
| **Pass mark** | On the hidden set: zero wrong "disputable" verdicts; every other verdict correct or an honest "cannot verify" that names what is missing; no dispute without a source that states the event on that route and date; no dispute sent twice, including after a kill; no paid call repeated after a restart. |
| **Data sources called** | Listed in full below. |

## Every data source the service calls

Nothing leaves the machine except through `pdr.broker.paid_call`, which runs the outbound guard first and writes one
ledger row per call (including retries, blocked fetches and zero-cost cache hits).

| Source | Endpoint | Used for | What is sent | Cost |
| --- | --- | --- | --- | --- |
| Crustdata web search | `POST https://api.crustdata.com/web/search/live` (`sources: ["news"]`, Unix-second date window, never `location`) | Finding dated reports of a disruption on a route and date window (evidence stage, pass 2 only) | A query built from an allow-list: route place or state + event word (e.g. "Nagthane blockade"), plus the date window. Never agreement text, amounts, trip ids, driver data or the client's name | 1 credit per page with results; zero-result searches are free (read from `x-credits-used`) |
| Crustdata web fetch | `POST https://api.crustdata.com/web/enrich/live` (up to 10 URLs; one `human_mode` retry when blocked) | Fetching each source we would cite, to read the passage and the publication date; pages are snapshotted by content hash | Public article URLs only | 1 credit per URL, charged even when blocked |
| Crustdata company identify | `POST https://api.crustdata.com/company/identify` | Showing why a lookalike issuer differs from the client (never resolving the match) | A company's public name, alone | about 1 credit per batched call |
| Model provider (Anthropic Messages API, `POST /v1/messages`; models in `settings.yaml` `providers.llm`) | Data processor | Reading only: proposing contract terms from agreement text (intake), parsing unseen deduction formats and column names, proposing passages from article text, an independent refuter read. It never decides a verdict, writes a letter or approves anything | Agreement text (intake), deduction text (parsing), article text (passage reading). Driver identifiers are pseudonymised first; nothing about the client goes with article text | tokens, ledgered. No model key was available during the build: all model calls are faked in tests and the deterministic reader runs instead |
| Publisher pages (redirect resolution) | Plain `GET` with redirects off on `google.com/goto?...` links | Turning search-result redirect links into the real article URL before fetching (the article is then fetched through Crustdata) | The redirect URL only | free, ledgered |
| Official pages (district magistrate orders, IMD press releases, traffic-police advisories) | fetched through Crustdata `/web/enrich/live` when found | Preferred evidence over news (`evidence.sources` in settings) | Public URLs | as fetch |
| The client's own records | K1 clients and lanes, K2 agreements and confirmed terms, K4 deductions (local files) | Identity, terms, the client's stated calculation | read locally, never sent anywhere | free |
| The 3PL's own records | K3 trips, gate logs, GPS, notices, ops notes (local files) | Timeline, corroboration, notice | read locally; driver names and phones go to `vault.db` at the door | free |

Not called: OpenStreetMap (lanes come from a person-checked `fixtures/lanes.yaml`), Crustdata watchers, any
personal-data enrichment.

## Agents: models read, code decides, people authorise

| Part | Kind | Does | Can never |
| --- | --- | --- | --- |
| **Request reader** | Model + validation | Maps an unseen input (sheet, PDF, pasted email, a sentence of instruction) onto the logical model | Supply a value that is not in the source text |
| **Intake reader** | Model + validation | Proposes contract Terms with page, clause and verbatim text | Confirm a term; only a person does |
| **Evidence scout** | Code-planned search + model reader | Finds dated public reports for a route and window; proposes which sentences state event, place, date, effect | Write its own queries from client data; decide a verdict |
| **Adjudicator** | Deterministic code | Runs the checks, recomputes the correct penalty, combines results into a verdict | Use a fact without a citation; treat an unfinished check as "no ground" |
| **Auditor** | Code + an independent model read | Re-verifies every citation of a disputable verdict before it is written, and again before anything is sent | Upgrade a verdict; it can only downgrade or hold |
| **Clerk** | Deterministic code | Executes approved actions exactly once | Act on anything without approval rows for that exact version |
| **People** | Billing lead, account manager, finance, legal | Confirm terms, approve actions, override with a logged reason | — |

The only component that can move a verdict toward "disputable" is deterministic code working from confirmed terms
and cited records. Models can only propose evidence or veto it.

## Quick start

```bash
export PYTHONPATH=.                     # or: pip install -e .
python -m pdr.cli doctor                # preflight: settings, data dir, keys (names only), providers
python -m pdr.cli init                  # create data/state.db, print the settings version
python -m pdr.cli run --in tests/cases/case06_wrong_base_over_cap      # a folder, a file or pasted text
python -m pdr.cli status                # queue and stage table
python -m pdr.cli actions               # drafted letters and the roles they wait for
python -m pdr.cli approve '<action_id>' --role kam --name "A. Manager"
python -m pdr.cli send                  # reconcile, re-audit, send approved actions exactly once
python -m pdr.cli report                # static case file in out/ (open out/index.html)
python -m pdr.cli ledger --by deduction # measured cost per deduction
python -m pdr.cli economics             # cost by verdict type, break-even fee rate
python -m pdr.cli trail --verify        # hash-chained audit trail
```

Agreements: `pdr intake <client> <files...>`, then `pdr confirm <client>` and `pdr complete-set <client>`, or load
already confirmed terms with `pdr load-terms <terms.yaml>`.

Runtime data lives under `$PDR_DATA_DIR` (default `./data`, git-ignored). The Crustdata key is read from the
environment or the `.env` file (never logged, never committed).

## Restart after a kill

Run the same command again. Every command first reconciles actions left in `sending` against the K7 outbox by
`action_id`. Stages are memoised by input hash, so finished stages are reused. Paid calls already made are cache hits,
ledgered at zero cost; a call that was in flight when the parent died finishes in its detached child and lands in the
cache. Check it on stage:

```bash
ls data/outbox                          # one file per action id
python -m pdr.cli ledger --by deduction # no cache key paid twice; cache hits at zero cost
wc -l out/verdicts.jsonl                # one line per deduction (rendered from the store, never appended)
```

## Limits and ledger

- Run cost limit `budget.run_cost_credits`, per-deduction limit `budget.per_deduction_credits`, wall-clock limit
  `budget.run_time_seconds` (or `--time-limit`). When a limit is reached every undecided deduction still gets a verdict
  (`cannot_verify / BUDGET_REACHED`); free-check verdicts are unaffected (test case 19).
- Economic gate: no paid search when the recoverable amount is below `economics.search_gate_ratio` times the estimated
  search cost (`SEARCH_NOT_ECONOMICAL`, a person can force it).
- Ledger: one row per call with provider, endpoint, units, cost in USD and INR (`budget.usd_inr`), cache hit, attempt,
  outcome, and the request summary, so what left the machine can be audited.

## Settings

`settings.yaml`, validated at start; its hash is the `settings_version` stamped on every verdict. It has **no place for a
contract value**: rates, caps, windows, notice periods and force-majeure lists come only from a client's confirmed
terms. What a live fix usually touches (full map: [`docs/livefix.md`](docs/livefix.md)): `evidence.disruption_types`,
`output.fields` / `output.rename`, `approvals.*`, `budget.*`, `causation.allowance_hours`,
`records.conflict_allowance_minutes`, `adapters.k3/k4.field_map`.

## Tests

```bash
python -m pytest -q                       # everything, offline (recorded responses, fake model)
python -m pytest -q tests/test_cases.py   # the PRD test cases as folders
python -m pytest -q tests/test_caselib.py # every case folder validates against the models
```

`tests/cases/caseNN_slug/` holds one folder per PRD case (1-23, 7b, 12a-12d): input (`deductions.csv` / `.txt`,
`trips.json`, `clients.yaml`, `terms.yaml`), recorded Crustdata responses (`cassette/`, trimmed from real responses
saved on 22 Nov 2026), `expected.json` and a `NOTES.md` with the hand arithmetic and the rule that decides it. Case 6
also has `variants/` (renamed columns, reordered columns, pasted email) that must give the same key and verdict.
Folders are generated from `tests/cases/_specs.py` (`python -m tests.cases._build`). The Open-track submission is
[`docs/open_track_cases.md`](docs/open_track_cases.md).

More: [`docs/ethics.md`](docs/ethics.md), [`docs/finance.md`](docs/finance.md),
[`docs/processor_agreement_template.md`](docs/processor_agreement_template.md),
[`docs/demo_script.md`](docs/demo_script.md).

## Changes during the build

- **Hour-3 change:** _to be filled in when announced (settings or adapter only)._
- Case 15 uses the real Kini blockade instead of a 12-hour bandh. The only bandh with saved real pages is the Odisha
  one, and that is the SOURCES_CONFLICT case (case 8).
- Case 12d expects no search (DESIGN 5.5: no search for an arguable clause unless `evidence.search_for_referrals`).
  The PRD row mentions "evidence found".
- Client B's stand-in exclusion covers strikes, agitations and road blockades. The driver report in cases 1/12 is
  typed `blockade`, because `settings.yaml` has search words for `blockade` but none for `agitation`.
- The case 1 GPS halt is placed at Nagthane, the north end of the blocked stretch named in the Outlook report. The
  engine matches corroboration by place name and does not interpolate along a stretch.
- The contradicting OrissaPOST passage for case 8 was confirmed by one live fetch (human mode) on 22 Nov 2026.

## What is stubbed or not done

Checked against `main` at the end of lane D's work (22 Nov 2026):

- **Model provider: no key.** `pdr/llm.py` has a real Anthropic transport, but no model key exists in the environment.
  Every model call in tests is a hand-written fake (`cassette/llm.json` in cases 7, 8, 17). Without a key, passage
  reading falls back to the deterministic proposer, and agreement intake and free-text parsing do not run live.
  Confirmed terms are loaded with `pdr load-terms`.
- **Official-source adapters: none yet.** `evidence.sources` lists only `crustdata_news`. District orders, IMD and
  traffic-police pages are fetched only if a news search returns them.
- **Live web page (`pdr serve`): not built.** `pdr/ui/` is empty. The static case file (`pdr report`) is the interface;
  approvals happen in the CLI.
- **Event watch (`pdr watch`): settings only** (`event_watch.*`), no command.
- **Retention deletion: setting only** (`privacy.retention_days`). No job deletes trip-level personal data yet.
- **Stretch interpolation: not built.** Corroboration matches a GPS place to a place named in the passage. A halt
  between two named ends of a blocked stretch (e.g. at Kini, inside "Nagthane to Sangli Phata") is not matched. The
  case 1 halt is placed at Nagthane for that reason.
- **Lanes:** `fixtures/lanes.yaml` was written from map knowledge and needs a second person's check against a map
  (marked in the file).
- **Stand-in terms and clients:** all fixtures use stand-in terms built from public clauses, labelled `is_stand_in`
  and flagged `STAND_IN_TERMS`. Replace them with the kickoff client's confirmed agreements.
