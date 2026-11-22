# Demo script (3 minutes) and screening run sheet

Commands assume `export PYTHONPATH=.` and `alias pdr='python -m pdr.cli'`, with a clean `$PDR_DATA_DIR`. The case file
(`out/index.html`) is open in a browser next to the terminal. Roles: **Driver** types, kills and restarts; **Narrator**
speaks and never touches the keyboard; **Fixer** owns the live fix; **Answers**: lane D for finance and ethics, the
lead for design.

## Before the slot

```bash
pdr doctor && pdr init
pdr load-terms fixtures/clients/client_a.yaml   # or tests/cases/<case>/terms.yaml; confirmed stand-in terms
pdr run --in tests/cases/case01_kini_blockade_fm   # warms the cache: the live run reuses it at zero cost
```

For the live-data decision, run case 1 once against the real API before the slot (no fake transport is registered
outside tests). Its search and fetches are then in the cache, and the replay on stage costs nothing.

## The 3 minutes (PRD section 13 mapped to commands)

| Time | On screen | Command | Said |
| --- | --- | --- | --- |
| 0:00-0:20 | Title; one line on TCI v Indian Oil | — | "TCI fought a Rs 62.8 lakh deduction for 12 years and lost because its evidence wasn't recorded at the time. We record it the day the deduction arrives." |
| 0:20-0:50 | Drop the unseen payment advice holding three deductions; the queue fills | `pdr run --in <their advice or folder>` then `pdr status` | "Free checks first: terms, arithmetic, our own records. Paid search only when they leave the question open." |
| 0:50-1:30 | Case 6 briefly (WRONG_BASE + OVER_CAP, Rs 49,500, Rs 0 of paid calls), then case 1: clause with page, notice email, GPS halt, the Outlook passage with publication date and retrieval time (click: opens at the passage). Then case 2 (valid) and case 7 (cannot verify, named gap) | `pdr report`; open `out/index.html`; `pdr pack 'CLA\|APA20261105\|1'` | "Arithmetic and records first; news only finds the event, our GPS and notice carry it. Same route, different week: different verdict. When we can't prove it, we say what's missing." |
| 1:30-2:10 | Approve case 1 as account manager; it enters `sending`; **kill the process**; restart | `pdr actions`; `pdr approve '<action_id>' --role kam --name "A. Manager"`; `PDR_CRASH_AT=send.after_intent pdr send` (dies); `pdr send`; `ls data/outbox`; `pdr ledger --by deduction` | "One message in the outbox, one id. The ledger shows the search came from cache at zero cost." |
| 2:10-2:40 | Clients B and C, same blockade, different agreements: valid, then cannot verify for missing notice | `pdr run --in tests/cases/case12b_client_b_fm_excludes`; `pdr run --in tests/cases/case12c_client_c_notice_missing`; `pdr status` | "No default rules. Each client's own terms decide." |
| 2:40-3:00 | Ledger total per deduction next to the fee; the outbound pack with driver data removed | `pdr economics`; open the case page's evidence pack | "Cost measured, not estimated. Nothing leaves without a named person." |

If the judges kill the process at a moment of their choosing, run the same command again and show `pdr status` (stage
table) and `ls data/outbox`. Rehearse a kill mid-search (`PDR_CRASH_AT=call.after_intent`) and mid-send
(`PDR_CRASH_AT=send.after_intent`).

## Screening run sheet (PLAN section 8)

1. `pdr doctor`, then `pdr init`, then `pdr run --in <their input>`. If the input shape is new, the schema map appears:
   read it out and confirm it.
2. While it runs, free-check verdicts appear first. Show `pdr status` and the tail of the JSONL log
   (`tail -f data/logs/*.jsonl`).
3. When they stop it, run the same command again. Show `ls data/outbox`, then `pdr ledger --by deduction` (cache hits
   at zero cost), then `wc -l out/verdicts.jsonl` (one line per deduction).
4. Open the case file and click one source link: the article opens at the passage.
5. Show the README, `docs/architecture.md`, and the commit history by hour (`git log --format='%h %ad %an %s' --date=format:%H:%M`).
6. For any cannot verify, read out the named missing item.

Questions to be ready for:
- Why is a case cannot verify rather than disputable? Name the missing item.
- Where are the retries? The broker's detached executor (`providers.crustdata.retry_limit`, `backoff_seconds`).
- What if K7 is down? The action stays `sending` and is reconciled by id on the next start; it is never resent blind.
- What leaves the machine? See the README data-source table and `docs/ethics.md`.
- What is the model allowed to do? Read and propose, or veto evidence. It never decides a verdict.

## Live fix (5:00)

Drill these three in rehearsal, each under 3 minutes, from [`livefix.md`](livefix.md): add a disruption type
(settings), add an output field (settings + one derived function), lower the approval threshold (settings). After the
re-run, show the new settings version on the verdict and no new paid calls in the ledger.
