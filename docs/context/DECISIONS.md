# DECISIONS: design choices, PRD gaps and open questions

Written 22 Nov 2026 while turning the PRD into a design. Parts:

1. **Findings**: places where the context documents disagree with each other or with our own test data. Fix these first.
2. **Decisions** D-01 to D-19: choices the design makes where the PRD is silent or ambiguous. Each has a D-number that `DESIGN.md` cites. Any teammate can overturn one; change it here and in the design together.
   - **2b. Architecture review** D-20 to D-34: the decisions that fixed the architecture, each tied to the points it is meant to win.
   - **2c. Departures from the PRD**: the short list of places where the design deliberately differs.
3. **Open questions**: for the organisers and for the team.

## 1. Findings in the context documents

| # | Finding | Evidence | What to do |
| --- | --- | --- | --- |
| F-1 | **Query-length rule is not supported by our raw data.** PRD §6a and CLAUDE.md say "six-word queries returned 0; keep to 2–3 words". But the test run found the events with 5–7 word queries, 10 results each. The only zero-result long query was the one sent with `location: "IN"`. | `harsh/crustdata_test/raw/report.json`: "NH-48 Kini toll plaza sugarcane blockade trucks" n=10; "trucks stranded Assam West Bengal border Srirampur" n=7 | Treat "2–3 words" as a starting rung, not a law. Run task P-5 (short vs long on the same windows) and record the result in REFERENCE.md §1. See D-01. |
| F-2 | **Redirect links cannot be resolved by reading a query parameter.** REFERENCE.md §1 says to read the `url` or `q` parameter. The real links are `https://www.google.com/goto?url=CAES…`, where `url` is an opaque token. | `raw/run.log`; `resolve_test.py` | Resolve by requesting the link ourselves with redirects off and reading the `Location` header (68 of 68 worked, free). DESIGN §8.3 does this. |
| F-3 | **`success: true` is returned on blocked pages.** REFERENCE.md says "blocked if `success` is false, or…". In testing, Google's "Verifying your request" page came back with `success: true`. | `RESULTS.md` rule 3; `raw/run.log` | Never trust the flag. Use the page check in DESIGN §8.3 (empty, under 1,500 characters, or block phrase). |
| F-4 | **`human_mode: true` retry is missing from PRD and REFERENCE.** It recovered orissapost.com, which is the source for case 8. | `RESULTS.md` rule 3 | One retry with `human_mode`, then give up. In settings as `human_mode_retry`. |
| F-5 | **Zero-result searches are free**, and the real cost of every call is in the `x-credits-used` response header. Neither is in the PRD. | `RESULTS.md` "Cost and speed"; ledger lines in `run.log` | The ledger reads the header (measured cost for the finance judge). Discovery searches for "valid" cases cost nothing when nothing is found. |
| F-6 | **Identify cost.** REFERENCE says 0.2 credits per match; the header showed 1 credit for a five-name call. Consistent, but only if names are batched. | `run.log` ledger | Ledger the header value; do not hard-code 0.2. |
| F-7 | **The model provider is not in the PRD's list of data sources**, though agreement text, deduction text and article text are sent to it. The PDF requires the README to list every data source the service calls. | PRD §1 "Data sources called" | Add it to the README list with what it receives. See D-16. |
| F-8 | **No model API key exists in the environment**; only `CRUSTDATA_API_KEY` is in `harsh/.env`. The PDF says each team brings its own model keys. | `.env` key names; shell environment | Task P-3. Without a key, intake, free-text parsing and passage proposals do not run. |
| F-9 | **The repo in our folders is a personal one** (`HarshLk/crustdata-hackathon`, one placeholder commit). The PDF says code goes in "the organizer repository that the team receives at kickoff". | `git remote -v` in each folder | Question Q-1. Plan for both: `GIT_WORKFLOW` section of WORKSTREAMS.md. |
| F-10 | **Clone layout is uneven.** `harsh/crust-hackathon`, `gujju/crustdata-hackathon`, `jindal/` is itself a clone *and* contains a nested clone, `pushkar/` is empty. `harsh/.env` and `harsh/crustdata_test/` sit outside any repo. | `ls`, `git remote -v` | Task P-7. |
| F-11 | **PRD case 7 and the "free checks first" rule pull against each other.** Stage 3 says "no memo: cannot verify, naming the memo", which a free check settles. But case 7's expected output cites news reports, so a paid search must have run. Case 21 is the same. | PRD §6 stage 3; §11 cases 7, 21 | D-11: a setting, default matching the expected outputs. |
| F-12 | **PRD case 2 and 3 expect a search although nobody asserts a disruption**, while stage 4 is written only for the asserted case. | PRD §11 cases 2, 3, 17 | D-03: a "discovery" search path. |

None of these changes the idea. F-1 to F-5 change Crustdata client code; get them into REFERENCE.md so nobody builds from the older wording.

## 2. Decisions

| # | Decision | Why | Alternative rejected |
| --- | --- | --- | --- |
| D-01 | Query ladder starts with `<most specific route place> <event word>`, then state, then regional language. Query length is tuned by test, not fixed at 2–3 words. | F-1. Short queries are also what keeps the query allow-listed (place + event word), which is the confidentiality guarantee. | Free-form queries written by a model: could leak client data and are not reproducible. |
| D-02 | Deterministic engine; models only propose (terms, parsed fields, passages) and code verifies every proposal against source text. | "No facts from model memory" and "wrong disputable is the worst outcome" become mechanical checks. Verdicts are reproducible, which matters for the hidden set and for restarts. | An agent loop that reasons to a verdict: hard to restart, hard to cite, hard to defend to the ethics judge. |
| D-03 | Two FM paths: **asserted** (notice, wording, then search) and **discovery** (nobody asserts; search only for event types the clause names; a hit can only produce `INCOMPLETE`, never a ground). | F-12. Matches cases 2, 3, 17 (valid with a search log) and 12c (no search). Zero-result searches are free. | Search only when asserted: cases 2 and 3 could not cite a search log. Search always: breaks "free checks first" for clients whose clause excludes the event. |
| D-04 | One supported ground plus one unresolved check gives **disputable for the supported amount**, with the open item listed under `unresolved`. | The amount is correct either way; hiding a certain over-cap error behind an uncertain FM question helps nobody. | Downgrading everything to cannot verify: safe but throws away arithmetic wins, which are the surest. If the hidden set marks these as cannot verify we lose nothing worse than a "correct or honest" mark; if it marks them disputable we gain. **Revisit at kickoff** once the K5 format shows whether one deduction can carry both. |
| D-05 | One recomputation model: corrections and the union of excused intervals shift the due time; `amount_disputable = deducted − correct_penalty`. | No double counting by construction (PRD step 7). One function to test instead of fifteen. Mirrors GeM GTC 15(i) "delivery date re-fixed". | Per-ground amounts added up: needs overlap rules between every pair of grounds. |
| D-06 | **Permitted-readings rule**: where a confirmed clause is silent on something that changes the figure, compute every permitted reading and treat the highest penalty as correct. | Keeps arithmetic disputes to what no reading allows. | Pick the reading that favours the 3PL: that is how a wrong "disputable" happens. |
| D-07 | Paid calls execute in a **detached child process** that writes the cache itself, so a kill of the main process mid-request does not lose a paid result. Honest `in_flight_at_crash` ledger row if the child dies too. | Searches take 6–21 s, fetches 10–25 s: a random kill is likely to land inside one. About 40 lines. | Inline calls only: a kill mid-request means paying twice, which is one of our four hard zeros. Keep `paidcall_mode: inline` as a fallback switch. |
| D-08 | `dispute_key` from normalised advice number and line; separate `event_fp` for duplicate detection; "first" decided by deduction date, never arrival order. | Case 18 (same deduction in a new format must give the same key) and case 4 (two advices, one trip) are different things and need different identities. | One identity for both: either resubmissions create second disputes or duplicates are missed. |
| D-09 | Route places come from trip records, a person-confirmed `lanes.yaml`, or (Should) OpenStreetMap. Never from a model. | Route geography from a model is a fact from memory. | Asking the model which districts a route crosses. |
| D-10 | A highway name or number alone never satisfies the place test; the passage must name a physically affected place that is on the route. | The Kini reports say "Pune–Bengaluru highway". A naive match on "Pune" would make case 3 (Pune → Mumbai) disputable: the worst kind of error. | Fuzzy place matching. |
| D-11 | `search_when_corroboration_missing` setting, default **true**. | F-11. The PRD's expected outputs for cases 7 and 21 include the news. False saves 5–7 credits per such case and is the first thing to flip if the hour-3 change cuts the budget. | Hard-coding either behaviour. |
| D-12 | Working-day counts treat **Sundays** as non-working when the clause says "working days". Any other holiday needs a holiday list from the agreement set or a cited official page; without one, the day-count check is `INCOMPLETE` naming the list, for the days in question only. | A holiday calendar from model memory is not a source. | A built-in holiday table. |
| D-13 | Fixture timings for case 1 (and 12a, 15, 21, 22) are designed so that the corroborated halt plus the causation allowance fully covers the delay; the allowance itself stays a setting set at kickoff. | Under D-05, a 14-hour halt does not by itself excuse a penalty counted in whole days unless the shifted due time lands after delivery. The fixtures must make the expected answer unambiguous. | Rounding excused time up to whole days: over-claims. |
| D-14 | Letters are rendered from a template and the verdict's citations. No model writes a letter. | Nothing uncited can enter an irreversible output. Letter version = content hash, which approvals bind to. | Model-drafted letters: need a second fact-check pass we do not have time for. |
| D-15 | Local outbox is one file per message id, written by atomic rename. | Idempotent by construction; "one message" is visible with `ls` on stage. | Appending to a single outbox file: a crash mid-append needs repair logic. |
| D-16 | The model provider is treated as a data processor: listed in the README with what it receives; vault values are pseudonymised before any model call; nothing about a client goes with article text. | F-7, PRD §8. | Sending raw ops notes with driver names. |
| D-17 | One generic `entities` table with a JSON body and two lookup keys, rather than a table per entity. | Lane C can finish the store in the first hour. | Full relational schema: better at scale, wrong trade for 7 hours. Say so in the design question. |
| D-18 | One writer for entities and stages; deductions are adjudicated one at a time. Only paid calls inside one evidence group run concurrently (see D-22). | No locking bugs on stage. Full parallelism is the 100x answer, described in DESIGN §15. | Thread pool over deductions now. |
| D-19 | Settings marked "set at kickoff" default to `0` and the service refuses to start until they are set. | No number we made up can pass as the 3PL's policy; the ethics judge can be shown this. | Plausible-looking defaults. |

## 2b. Architecture review, 22 Nov 2026: decisions D-20 to D-34

A second pass over the design asked one question of every part: does it raise the chance of passing screening and scoring in the finals, for what it costs in a 7-hour build? These decisions are **fixed** unless marked otherwise. `DESIGN.md` version 2 is written to them.

The review found four weaknesses in version 1:

1. It was built for inputs we had seen. Screening and the finals both use a request we have not.
2. Half the finals points come from an ethics judge and a finance judge, and version 1 gave them a terminal to read.
3. Restart, re-run after a settings change and re-run after new data were three separate pieces of logic.
4. The one place a model could cause a wrong "disputable" (reading a news passage) had a single line of defence.

| # | Decision | What it buys | Cost | Tier |
| --- | --- | --- | --- | --- |
| D-20 | **Memoised stages.** Each stage result is stored under a hash of the inputs it read. Restart, settings change, new document and changed client record are all "run again, reuse what is unchanged". | Robustness (15) and design (15) from one mechanism. A live fix re-runs in seconds with zero paid calls, and the new settings version shows on the verdict. Handles "organisers can change client information during the build". | Small: one hash function and a key on `stage_results` | Must |
| D-21 | **Action outbox.** Dispute letters, missing-document requests, force-majeure notices and test-mode payments are one entity with one approve-then-send-once protocol. | "Each action happens once" holds for every irreversible thing we do, not only letters. The proactive-notice feature and any pivot to payments reuse it unchanged. | Rename and generalise what was the Dispute lifecycle | Must |
| D-22 | **Two passes.** All deductions go through the free stages first; the rest are grouped by route and dates, one search per group, paid calls in a group run concurrently through the executor. | Answers appear in seconds on stage; fewer paid calls; the run fits a tight time limit. | Scheduler of about 40 lines | Must (grouping); Should (concurrency) |
| D-23 | **Auditor.** Every citation of a would-be disputable verdict is mechanically re-verified before the verdict is written and again before sending; an independent model read tries to refute each external passage. The auditor can only downgrade. | Second line of defence on the worst outcome. A concrete answer for the ethics judge: "before anything irreversible, every fact is re-checked". | One pure function plus one prompt | Must (mechanical); Should (refuter) |
| D-24 | **Links open at the passage; pages are snapshotted.** Web citations carry a text-fragment link; agreements link to the page; records link to file, row and field; every fetched page is stored by content hash. | Meets the PDF's source rule to the letter ("the link shows the exact passage or field"). Snapshots are the product's own pitch: evidence recorded on the day. | One link function; snapshots fall out of the cache | Must |
| D-25 | **Fail closed, always answer.** An unhandled error in a check becomes `INTERNAL_ERROR` for that deduction only. At the deadline or budget, every undecided deduction still gets a verdict. Outputs are rendered from the store, never appended. | A bug or one malformed row cannot stop a live run, cannot yield a wrong valid or disputable, and cannot duplicate an output line after a kill. | A try/except barrier and atomic file writes | Must |
| D-26 | **Request reader.** A schema mapper proposes column-to-field mappings for unseen input shapes; code validates them by parsing the real values; confirmed mappings are stored. A request interpreter classifies a sentence of instruction, and says "unsupported" rather than guessing. | The live run (20) and the unseen request at screening. Without it, a renamed column is a live-fix-sized problem during the 3-minute demo. | One model task, validation code, one table | Must |
| D-27 | **Strict identity.** Clients match on exact normalised name or a person-confirmed alias; trips on exact reference; an ambiguous place name must appear with its state or district. Identify explains a lookalike, never resolves one. | The hidden set includes a lookalike name. A wrong match is a wrong answer; `AMBIGUOUS_MATCH` naming the candidates is an honest one. | Less code than fuzzy matching | Must |
| D-28 | **Economic gate.** No paid search when the amount that could be recovered across the group is below the estimated search cost times a ratio (setting). Result: cannot verify `SEARCH_NOT_ECONOMICAL`, which a person can force. | Finance (25): we can show the service declining to spend Rs 200 to chase Rs 150. Cuts cost if the hour-3 change lowers the limit. | One comparison | Should. Default ratio 1.0, so it only affects deductions smaller than one search |
| D-29 | **Pay-on-recovery pricing, measured cost.** Fee is a share of the amount recovered; `pdr economics` prints measured cost per deduction by verdict type, fee, margin and the break-even fee rate. | Finance (25): price in rupees defended with our own ledger. Matches the problem statement's "the client pays when the job is done". | One report over the ledger. The rate itself is the team's call (T-2) | Must (report) |
| D-30 | **Static case file is Must; live page is Should.** `pdr report` renders queue, case pages and a money page as plain HTML from the store. | Ethics and finance judges (50 points) and People's Choice can read it. Nothing to crash during the kill. Promotes the PRD's "billing-team queue view" from Should without taking on a server. | Jinja templates over existing objects | Must |
| D-31 | **Tamper-evident audit trail.** Confirmations, approvals, overrides and sends are appended with the hash of the previous row. | Ethics and engineering: who approved what, on which version, provably unedited. | About 15 lines | Should |
| D-32 | **`pdr doctor` and degraded mode.** A preflight checks keys, settings, provider reachability and the data directory. With a provider down, free verdicts and cached route-dates still work; the rest are `PROVIDER_FAILED` with their stage saved. | Protects the demo from the venue's network and from a missing key on the presenting laptop. | Small | Must (doctor) |
| D-33 | **Check registry.** Checks are registered functions that declare their stage, dependencies and settings sections. | Design (15): a live fix that adds a rule is one function. Feeds memoisation and the "checks run" table. | A decorator | Must |
| D-34 | **Presented as a system of narrow agents** (request reader, intake reader, evidence scout, adjudicator, auditor, clerk) around a deterministic core. | The brief says "build an agent". This is the honest description of what we have, and "models read, code decides, people authorise" is the line all four judges need. | Naming and one table in the README | Must |

**Considered and rejected**

| Idea | Why not |
| --- | --- |
| A model-driven agent loop that plans its own searches and reasons to a verdict | Not restartable to the same answer, cannot guarantee client data stays out of queries, and puts a model on the path to "disputable" |
| An agent framework or an MCP server in front of the CLI | No points attach to it; every hour spent there is an hour not spent on the hidden set |
| Fuzzy matching for names and references | The hidden set is built to punish it |
| Building the reverse direction (carrier invoices, detention claims) now | On the PRD's Won't list. The action outbox keeps the door open; see the kickoff fit check in PLAN §5.0 |
| A live web app as the main interface | A server is one more thing to die on stage; the static case file gives the judges the same view |

## 2c. Where the design now departs from the PRD

The PRD remains the build spec. These are the deliberate differences; if the team disagrees with one, change it here first.

| PRD says | Design does | Decision |
| --- | --- | --- |
| Reason-code list in §6 | Adds `SEARCH_NOT_ECONOMICAL` and `INTERNAL_ERROR` | D-28, D-25 |
| Queue view is a Should | A static case file is a Must; the live page stays a Should | D-30 |
| Dispute is its own entity with its own send protocol | Dispute letters are one kind of Action | D-21 |
| "Saved stages… on restart skip" | Stages are memoised by input hash; restart is one case of re-run | D-20 |
| Lookalike resolution "via Crustdata identify" | Identify only explains; exact match or a confirmed alias resolves | D-27 |
| Search runs when conditions (a)–(d) hold | Also needs the economic gate to pass | D-28 |
| Verdict written after stage 6 | Audited before it is written; actions re-audited before sending | D-23 |
| Stage 4 only for an asserted disruption | Discovery path when nobody asserts one | D-03 |

## 3. Open questions

### For the organisers (ask at or before kickoff)

| # | Question | Why it matters | Our default until answered |
| --- | --- | --- | --- |
| Q-1 | Is code written before kickoff allowed in the organiser repository? Is our own repo usable at all, or only theirs? | Decides whether we pre-build (PLAN Phase P) or only prepare specs and fixtures | Prepare docs, fixtures, recorded responses and verified test cases now (allowed under any reading). Write code before kickoff only if they say yes, and then bring it in openly in a clearly labelled first commit. |
| Q-2 | What is K7 (mailbox, file, endpoint)? Does it accept an idempotency key? Can we query it by message id? | Exactly-once after a crash | Local outbox; `held` on restart if K7 can neither check nor de-duplicate |
| Q-3 | How does the hidden set call us (K6) and in what format must answers come back (K5)? Is there a required run time limit? | Adapters, settings | Folder drop plus `pdr run`; JSONL |
| Q-4 | For an Open-track team, what is the "unseen request" at screening and in the finals: one of the client's kickoff requests, or something shaped like our three submitted cases? | Whether the engine will ever be exercised on screening day | Read the client's requests at kickoff; if any concerns deductions, payments or invoices, also join "Buying and paying" |
| Q-5 | When exactly is 11:00 AM relative to the build start, and how are the three Open-track cases submitted? | The submission needs live-verified sources | Cases 1, 2, 7 are verified and written **before** build day (P-4) |
| Q-6 | Does "stop and restart" mean one process, or everything we started? | Whether the detached executor (D-07) is in play | Works either way; say which case the ledger row shows |
| Q-7 | Are Crustdata credits per team capped, and what is the balance? | Budget settings, rehearsal runs | About 54 credits used so far; assume a few hundred |

### For the team

| # | Question | Owner | Needed by |
| --- | --- | --- | --- |
| T-1 | Which model provider and whose key? | Lead | Before kickoff |
| T-2 | The fee rate, in rupees per Rs 100 recovered. The shape is decided (D-29: pay on recovery, nothing for checking). Pick the rate after `pdr economics` shows the break-even rate from measured cost; state the assumed win rate and label it an assumption | Lane D with lead | Hour 5 |
| T-6 | Which laptop presents, and does the detached executor work on it? (It is automatic-inline on Windows) | Lead | Rehearsal, T+6:15 |
| T-3 | Demo values for the settings marked "set at kickoff" (budget, thresholds, causation allowance, USD–INR rate with its source) | Lead | Kickoff + 30 min |
| T-4 | Who runs the service at screening and who does the live fix? They must be the person who knows `settings.yaml` and the engine best | Lead | Hour 5 |
| T-5 | Is D-04 (partial dispute with an unresolved item) acceptable, or do we want the stricter reading? **Harsh agreed to D-04 on 22 Nov 2026**; still to be re-checked against the K5 format | All | Kickoff + 30 min, after seeing K5 |
