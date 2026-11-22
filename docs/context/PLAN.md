# PLAN: end to end, from now to the finals

**Version 2, 22 Nov 2026.** How we get from the context documents to a screened, demo-ready service. `DESIGN.md` says how the system works; `WORKSTREAMS.md` splits this plan into four parallel lanes; `DECISIONS.md` holds the reasoning and open questions.

Version 2 follows the architecture review (DECISIONS §2b). What it changes in the plan: an unseen-request reader and a static case file join the Must list; restart and re-run share one mechanism, built in the first two hours; an auditor sits before every dispute; and the first 30 minutes of kickoff include a fit check with a decision rule.

Times are **T+h:mm** from the start of the 7-hour build. Clock times assume a 09:00 start, which is **unconfirmed** (question Q-5).

## 1. The hackathon in brief

**ContextCon 05, BITS Pilani: "Services, Replaced."** Build an agent system that does a whole paid service job from request to result for one fictional client, a mid-size Indian 3PL whose name, records and requests are released at kickoff.

Our job: **recover penalty deductions that the client's own agreement does not allow.** For each deduction, return valid, disputable or cannot verify with every fact sourced, and send a dispute exactly once after a named person approves.

| Phase | What happens | What it tests |
| --- | --- | --- |
| Kickoff | Client name, records, requests, input and output formats, organiser repo | Our adapters and the request reader |
| Build, 7 hours | We build; commit throughout | Repository history |
| 11:00 AM | Open-track teams submit 3 test cases with correct answers | Cases 1, 2, 7 |
| Hour 3 | One requirement changes for everyone | Settings and memoised re-run |
| Code freeze | End of build. Afterwards only the live fix | — |
| Screening | Unseen request + hidden test set (duplicate, lookalike name, unsettleable question; all dated after 1 Sep 2026), stop and restart, README, diagram, logs, history. Wrong answers count more than cannot verify. Top 8–12 go through | Correctness, restart, engineering |
| Finals, 12-minute slot | 3 min demo including a kill, 5 min live fix, 2 min questions with one system-design question | 100 points |

**Finals points:** live run 20, robustness 15, design 15 (computer-science judges), ethics 25, financial feasibility 25.

## 2. How we intend to win

Screening is a gate with no score; the finals are scored by four judges who each look at one thing. The plan puts a specific mechanism behind each.

| Where points or the gate are | What wins it | What we build for it |
| --- | --- | --- |
| Screening: hidden set | Never a wrong answer; honest cannot verify that names the gap | Typed check results, strict matching, permitted-readings rule, auditor |
| Screening and live run (20): unseen request | Takes input it has never seen to a sourced result without a code change | Request reader with validated schema mapping; fail-closed barrier; always-answer rule |
| Robustness (15) | Killed anywhere, restarted with one command, every action once | Memoised stages, paid-call broker, action outbox, outputs rendered from the store |
| Design (15) | Live fix in under 5 minutes; crisp 100x and second-client answers | Settings, check registry, memoised re-run that shows the change in seconds, `docs/livefix.md` |
| Ethics (25) | Personal data visibly contained; nothing irreversible without a named person; decisions that explain themselves | Vault and guard with a canary test; approvals bound to the exact version; auditor before send; audit trail; "what would change this" on every verdict |
| Finance (25) | A buyer, a price in rupees, and cost per job that is measured | Ledger from the provider's own header; economic gate; `pdr economics` with break-even rate; pay-on-recovery pricing |
| All four judges, and People's Choice | They can read the result | Static case file with links that open at the passage |

Two things we deliberately do not spend time on: an agent framework, and a live web app as the main interface (DECISIONS §2b, "considered and rejected").

## 3. Deliverables and what "done" means

| # | Deliverable | Required by | Done when |
| --- | --- | --- | --- |
| 1 | Working service in the organiser repo, committed throughout | PDF | `pdr run --in <kickoff input>` produces K5 output for every deduction; history shows commits from every hour and every member |
| 2 | README: job, who pays today, input, finished result, pass mark, **every data source called** | PDF | All six present; sources list Crustdata search, fetch, identify, the model provider, any official pages, OpenStreetMap if used, the client's records. Includes the agents table from DESIGN §1 |
| 3 | Architecture diagram: components, data flow, data calls, approvals, retries, cost ledger | PDF | `docs/architecture.md` (mermaid from DESIGN §3) renders on GitHub and matches the code |
| 4 | Three Open-track test cases with correct answers, by 11:00 AM | PDF | Cases 1, 2, 7 submitted with live-verified passages, dates and URLs |
| 5 | Live data in at least one decision | PDF | Case 1 runs against the real Crustdata API in the demo |
| 6 | Sourced facts, or "cannot verify" naming what is missing | PDF | Tests: every disputable verdict has a term citation and a fact citation, each with a working link; every cannot verify has a non-empty `missing_items`; every disputable passed the audit |
| 7 | Approval before irreversible actions | PDF | Test: no outbox file exists without approval rows for that action id |
| 8 | Time and cost limit per run; cost of each call recorded | PDF | Ledger row per call; case 19 passes; a run past its deadline still returns a full answer set |
| 9 | Hour-3 change met at screening | PDF | Merged, tested, noted in README |
| 10 | Logs | Screening | JSONL, one line per stage change, call, approval, send |
| 11 | Survives stop and restart; each action once | Screening, finals | Kill tests green; rehearsed by hand three times on the presenting laptop |
| 12 | Handles an unseen request | Screening, finals | Case 6 with renamed columns, reordered columns and as pasted text gives the same key and verdict; an out-of-scope request gets "unsupported", not a guess |
| 13 | Case file the judges can read | All judges | `pdr report` output opens in a browser; case 1's source link opens the article at the passage |
| 14 | Ethics page | Ethics judge | `docs/ethics.md`: data table, approval table, what the canary and audit tests prove, processor-agreement template |
| 15 | Finance page | Finance judge | `docs/finance.md`: buyer, price in rupees, measured cost per deduction by verdict type, break-even rate, margin; assumptions labelled |
| 16 | Demo, live-fix drill, design answers | Finals | Rehearsed against a timer; `docs/livefix.md` written |

## 4. Phase P: before kickoff (starts now)

Everything here is allowed under any reading of the rules: documents, test data, verified sources, environment. Whether we may also write code early is question Q-1.

| # | Task | Owner | Output | Cost |
| --- | --- | --- | --- | --- |
| P-1 | Ask the organisers Q-1 to Q-7 (DECISIONS §3). Confirm build date, start time, repo rules | Harsh | Answers written into DECISIONS.md | — |
| P-2 | Environment on all four laptops: Python 3.10+, one `requirements.txt` (`pydantic>=2`, `httpx`, `pyyaml`, `typer`, `trafilatura`, `pypdf`, `openpyxl`, `jinja2`, `pytest`, model SDK; `fastapi`, `uvicorn` optional), wheels cached for offline install | Each; Jindal checks | A one-line import check of every dependency passes on all four laptops | — |
| P-3 | Model key: choose the provider, get the key, put it in each `.env`, make one test call. Configure a second provider as fallback | Harsh | Key works; F-8 closed | small |
| P-4 | **Verify cases 1, 2 and 7 live.** Run the searches, resolve links, fetch, confirm each passage, publication date, URL and the Kini blockade's start and end dates. Save every raw response. Put trip dates inside the confirmed window. Write the three cases in submission form | Harsh | `tests/cases/case01,02,07/` with recorded responses; a ready-to-submit document | ~20–25 credits |
| P-5 | Settle query length (F-1): the same event and window with 2-, 3- and 5-word queries. Correct REFERENCE.md §1 and the "Crustdata, in short" section of CLAUDE.md with F-1 to F-6 | Harsh | Corrected docs | ~8 credits |
| P-6 | Sample clients and terms: Clients A–D of case 12 plus the GeM-style client for cases 5 and 6, as confirmed `terms.yaml` built from the real clauses in REFERENCE §3 (marked stand-in). One sample agreement as a PDF for the intake demo | Pushkar | `fixtures/clients/`, `fixtures/agreements/` | — |
| P-7 | `lanes.yaml` for the test-case lanes: Pune–Kolhapur (NH-48 via Satara, Karad, Kini), Pune–Mumbai, Cuttack–Bhubaneswar, Assam–Haldia via Srirampur. Each place with its district and state, from a citable map source, reviewed by a second person | Pushkar | `fixtures/lanes.yaml` | — |
| P-8 | Fixtures for the free-check cases (4, 5, 6, 9, 10, 13, 14, 18, 20), arithmetic worked by hand and checked by a second person. For case 6, also the three **variants** (renamed columns, reordered columns, pasted text) | Gujju | `tests/cases/` | — |
| P-9 | Repo hygiene: one clone per member folder (fix the `jindal/` double clone, clone into `pushkar/`), `.env` in each, `.gitignore`, branch rules, CI workflow file ready | Jindal | Four working clones; CI file | — |
| P-10 | Finance and ethics drafts: buyer; pay-on-recovery structure (D-29) with the rate left open until measured; processor-agreement template; approval table | Pushkar | Drafts in `docs/` | — |
| P-11 | Case-file mock: one hand-written HTML case page for case 1 so the templates have a target and the team agrees what judges will see | Jindal | `fixtures/mock_case.html` | — |
| P-12 | Everyone reads CLAUDE.md, PRD, DESIGN, INTERFACES and their lane; objections go into DECISIONS.md. One 30-minute call to settle the lane split | All | — | — |
| P-13 | **If Q-1 says early code is fine:** W0, then lane tasks in WORKSTREAMS order. Otherwise stop at fixtures | All | — | — |

P-4 cannot slip: the 11:00 submission needs sources that have been fetched and read.

## 5. Phase B: the build

### 5.0 Kickoff fit check (T+0:00 to T+0:30)

All four read the kit together for 10 minutes, then answer three questions in order. The lead decides and writes the answer in the README.

| Question | If yes | If no |
| --- | --- | --- |
| 1. Do the client's records include penalties, deductions, short payments or debit notes taken by *its* customers? | Core case. Load them through K4; the kickoff client's lanes and agreements replace our samples | Go to 2 |
| 2. Do they include agreements or rate contracts plus trip or shipment records, with no deductions? | Build our deductions on the client's real lanes and trips, labelled as samples; stand-in terms labelled. Open track stands on cases 1, 2, 7 | Go to 3 |
| 3. Is there a client request about charges under an agreement in the other direction (carrier invoices, penalties the 3PL levies)? | Discuss for five minutes whether to serve it with the same adjudicator: it needs new K-adapters, the carrier's terms, and an action of kind `payment_test`. Budget 60–90 minutes; only do it if questions 1 and 2 are both "no" | Open track only, on our own cases and labelled samples |

**Second track.** Join "Buying and paying" (or any other) only if its request is literally about deductions or short payments against an agreement, so its hidden set exercises the checks we built. A second track means a second hidden set, and a wrong answer there counts against us.

Also in these 30 minutes: ask Q-2 to Q-4; set the kickoff settings (T-3); lane C makes the first commit by T+0:15; lanes A and B land W0.

### 5.1 Timeline

| Time | Milestone | Exit test |
| --- | --- | --- |
| T+0:30 | **M0: contracts merged.** Models, codes, contracts, stubs, settings schema, check registry skeleton, empty CLI, CI | `pytest` green; all four clones on the same main |
| T+2:00 | **M1: walking skeleton.** Case 6 goes from a fixture to a K5 line through the real store and the memoised pipeline; killing and re-running skips finished stages. Broker returns dated Kini evidence live with ledger rows. A fake action survives the crash-point loop | `pytest -k "case06 or kill or broker or memo"` green |
| T+2:00 (11:00) | **Open-track submission** of cases 1, 2, 7 | Submitted; receipt committed |
| T+3:00 | **Hour-3 change announced.** 15-minute triage (Section 6) | Classified; owner named |
| T+3:30 | **M2: case 1 end to end, live.** Kickoff-format input, confirmed terms, GPS halt, notice, live search and fetch, audit passed, disputable with every citation linked. Case 6 variants pass (unseen shapes) | Run by hand against the real API; ledger shows 5–8 credits; a second run is all cache hits |
| T+4:15 | Hour-3 change merged | Tests green; re-run made zero paid calls |
| T+5:00 | **M3: critical path complete.** Approve, re-audit, send once; kill at search and at send; restart. Cases 1, 2, 4, 5, 6, 7, 10, 11, 12a–d green. Static case file renders. Dry run on the kickoff client's records | The four hard zeros (PRD §12) pass in CI |
| T+5:00–6:15 | **Hardening.** Cases 3, 8, 9, 13–23. README, diagram, finance and ethics pages with measured numbers. Refuter, audit trail, economics view. Live page only if lanes are free | Suite green except items consciously cut (Section 7) |
| T+6:15 | **Feature stop.** Fixes, docs and tests only | — |
| T+6:15–6:45 | Full rehearsal on the presenting laptop from a fresh clone: `pdr doctor`, init, run, kill twice, restart, live-fix drill, open the case file | Works with only `.env` added |
| T+6:45 | **Release candidate tagged.** Two people agree on every merge | — |
| T+7:00 | **Code freeze** | `main` is what screening runs |

### 5.2 Integration rhythm

- Integrate at **:00 every hour**: each lane rebases on main, pushes, merges what is green. Five minutes, standing.
- Main is always runnable. Whoever breaks it fixes or reverts within 10 minutes.
- At M1, M2 and M3 the lead runs the exit test from a clean clone.

### 5.3 What each lane is doing when

| Window | Lane A: paid calls and evidence | Lane B: adjudicator | Lane C: once-only, outputs, platform | Lane D: inputs, privacy, documents |
| --- | --- | --- | --- | --- |
| T+0:00–0:30 | W0 with B; organisers' questions | W0 with A | Repo skeleton, CI, first commit | Kickoff kit; fit-check notes |
| T+0:30–2:00 | Broker (inline), fake transport, Crustdata client, link resolver, page check, snapshots, publication date | Check registry, memoised pipeline with barrier, penalty calculator, combine, gate | SQLite store, settings, logs, crash points, K5 renderer with atomic output, CLI, `doctor` | K4 and K3 with vault split, K1, schema mapper, kickoff client loaded |
| T+2:00–3:30 | Model adapter, candidate sentences, passage test, contradiction test, query ladder, `find_evidence` | Term checks, timeline, record checks, notice and wording, two-pass run, evidence wiring | Actions and approvals, letter, send-once, K7, kill tests | Open-track submission (with A); guard and canary; intake and confirm flow |
| T+3:30–5:00 | Detached executor and concurrency; fault cases; refuter | Auditor (mechanical); economic gate; hard cases | Static case file; ledger and economics views; hour-3 change if settings or output | Kickoff client's terms confirmed; request interpreter and K6; fixtures; hour-3 change if an adapter |
| T+5:00–6:15 | Lookalike explain via identify; official source or event watch only if all green | Remaining cases; explanations; "what would change" | Audit trail; live page (Should) | README, diagram, finance and ethics pages from the real ledger; `docs/livefix.md`; demo script |
| T+6:15–7:00 | Rehearsal: kill mid-search | Rehearsal: live fix | Rehearsal: kill mid-send | Rehearsal: narration; final README check |

## 6. The hour-3 change

1. **T+3:00–3:15, triage** (lead plus the owning lane). Classify:
   - **Settings only** (budget, threshold, roles, disruption type, rename): edit `settings.yaml`, add a test.
   - **Output field**: `output.fields`; a new derived field is one function in `outputs/derived.py`. Lane C.
   - **Input or sink format**: schema map or one adapter. Lane D.
   - **A new rule**: one registered check returning a `CheckResult`; never a special case inside `combine`. Lane B, with a second reviewer.
2. Re-run. Memoisation recomputes only what the change touches and makes no paid calls; confirm that in the ledger.
3. Record the change and its classification in the README under "Changes during the build", and add a test case folder for it.

If the change is a lower cost limit, the levers in order: `search_when_corroboration_missing: false`, `economics.search_gate_ratio` up, `max_fetches_per_deduction` down, `regional_query: false`, `refuter: false`, then the run limit itself.

## 7. Cut list

If we are behind at a milestone, cut from the top. Nothing on the PRD §3 Must list is cut.

| Behind at | Cut |
| --- | --- |
| M1 | Detached executor (stay inline; keep the honest ledger row). Paid-call concurrency. Live page. Event watch. OpenStreetMap routes |
| M2 | Model-based term extraction (hand-confirmed `terms.yaml`; show the confirm step on a file). Request interpreter for free-text instructions (keep the schema mapper). Regional-language queries. Identify (any alias miss goes to a person) |
| M3 | Refuter read (keep the mechanical audit). Audit-trail hash chain (keep the rows). Economic gate. Monthly letter grouping. Escalation hint. Official-source adapters. Cases 15, 17, 23 as tests (their code paths fall to cannot verify) |
| T+6:15 | Anything not merged |

**Never cut:** memoised stages and the store; the action outbox; the ledger; the guard; "INCOMPLETE is not NO_GROUND"; the fail-closed barrier; the mechanical audit; the schema mapper; the static case file; README and diagram.

## 8. Phase S: screening

One person runs the service and answers questions (T-4). Run sheet:

1. `pdr doctor`, then `pdr init`, then `pdr run --in <their input>`. If the input shape is new, the schema map appears: read it out, confirm it.
2. While it runs: free-check verdicts appear first. Show `pdr status` and the tail of the JSONL log.
3. When they stop it: run the same command again. Show `ls data/outbox`, then `pdr ledger --by deduction` with cache hits at zero cost, then that `verdicts.jsonl` has one line per deduction.
4. Open the case file. Click one source link: the article opens at the passage.
5. README, `docs/architecture.md`, commit history by hour.
6. For any cannot verify: read out the named missing item.

Questions to be ready for: why a case is cannot verify rather than disputable; where retries are; what happens if K7 is down; what leaves the machine; what the model is allowed to do.

## 9. Phase F: finals

**Demo (3:00)** follows PRD §13, shown in the case file with the terminal beside it.

| Role | Person | Does |
| --- | --- | --- |
| Driver | Whoever ran screening | Types, kills, restarts |
| Narrator | Lead | Speaks the script; never touches the keyboard |
| Fixer | Lane B or C owner (T-4) | Makes the live fix with `settings.yaml`, `outputs/derived.py`, `prompts/`, the check registry and `docs/livefix.md` open before the slot starts |
| Answers | Lane D owner for finance and ethics; lead for design | Two-sentence answers from DESIGN §15 and `docs/finance.md` |

**Live fix (5:00).** Drill three requests in rehearsal, each under 3 minutes: add a disruption type (settings), add an output field (settings plus one derived function), lower the approval threshold (settings). Say what kind of change it is before typing; after the re-run, show the new settings version on the verdict and zero new paid calls in the ledger.

**Judge by judge**

| Judge | What we show | Where it comes from |
| --- | --- | --- |
| CS: live run (20) | Unseen request read and mapped, then a sourced verdict: clause page, record row, passage with publication date and retrieval time, link opening at the passage | Request reader; case file |
| CS: robustness (15) | Kill at a random moment; same command again; one outbox file; ledger with no repeated paid call; no duplicate output line | DESIGN §5.3, §6, §7 |
| CS: design (15) | Live fix as a settings edit or one registered check; 100x and second-client answers | DESIGN §9, §5.8, §15 |
| Ethics (25) | Driver data never leaves (vault, guard, canary test). Nothing sent without a named approver for that exact version. Every fact re-verified before sending. Verdicts say what is missing and what would change them. Stand-in terms labelled. Conflicting evidence shown even when it hurts our case (case 8). The model can read and veto, never decide | DESIGN §1, §8.5, §10; `docs/ethics.md` |
| Finance (25) | Buyer: the finance controller. Price: a share of what is recovered, nothing to check. Cost per deduction by verdict type, **measured** from the provider's own billing header. Break-even rate and margin. The service declining a search that would cost more than it could recover | `pdr economics`; `docs/finance.md` |

## 10. Risks specific to this plan

| Risk | Sign | Response |
| --- | --- | --- |
| Kickoff records contain no deductions or trip times | Fit check, question 1 is "no" | Fit check rows 2 and 3; Open track stands on our three cases |
| The unseen request arrives in a shape nothing maps | Schema map fails validation | The operator edits the proposed map in one screen; unmapped required fields give cannot verify naming the field, never a crash |
| Venue network drops during the live run | `pdr doctor` or a call fails | Degraded mode: free verdicts and cached route-dates still answer; phone hotspot ready; the demo's case 1 is warm in the cache with one live call shown |
| Contracts in W0 turn out wrong | A lane needs a missing field | Additive change, `contract` label, merged at the next hourly integration |
| One lane blocks the others | A stub is still a stub at M1 | Lead reassigns; the blocked lane codes against the stub and fixtures |
| No model key, or the provider is slow | P-3 open; timeouts | Known input shapes and hand-confirmed terms need no model; passage reading falls to cannot verify `PROVIDER_FAILED`; second provider configured |
| Pages cited in cases 1, 2, 7 start blocking | Live smoke fails | Snapshots and recorded responses show the answer was correct when submitted; a second source is listed per case; `human_mode` retry |
| Scope added by the review overruns | M1 or M2 missed | Cut list, in order. The review's Must items are small by design; the Should items are the first cuts |
| Demo laptop differs from dev machines | Rehearsal fails from a fresh clone | Rehearsal at T+6:15 is on the presenting laptop; executor falls back to inline automatically |
| Credits run out in rehearsal | Balance low | Tests replay recorded responses; live runs only at P-4, M2 and one rehearsal |
