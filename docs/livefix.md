# Live-fix map

Each likely request, mapped to the exact file and key to edit, in the order to do it on stage. Say what kind of change
it is before typing ("settings only", "one registered check"). After any change, run the same `pdr run` again.
Memoised stages recompute only what the change touches, with zero new paid calls. Show the new `settings_version` on
the verdict and the unchanged ledger.

Keep open before the slot: `settings.yaml`, `pdr/outputs/derived.py`, `pdr/prompts/`, `pdr/engine/registry.py`, this
file.

| Request | Kind | Edit | Then show |
| --- | --- | --- | --- |
| "Treat port congestion as a disruption" | Settings, plus one enum value | `settings.yaml` -> `evidence.disruption_types`: add `port_congestion: ["port congestion", "container pile-up"]`. The keys are validated as `EventType`, so first add `port_congestion = "port_congestion"` to `EventType` in `pdr/codes.py` (an additive change). Without that, use the existing `other` type | It counts only where a client's confirmed FM clause names it. Show one client where the verdict changes and one where it does not |
| "Add a confidence field to the output" | Settings (derived field exists) | `settings.yaml` -> `output.fields`: add `- [confidence, derived:confidence_label]` | The label comes from the check results (all source tests met, no conflict, terms confirmed), never a model score, and it can never turn cannot verify into disputable |
| "Any other output field" | Settings, plus one function if new | `output.fields` with an existing derived field (`sources_count`, `amount_in_words`, `escalation_hint`, `checks_summary`, `what_would_change`, `run_cost_inr`, `citations`), or add `@derived("name")` in `pdr/outputs/derived.py` | `out/verdicts.jsonl` regenerated from the store |
| "Rename the outcome values or fields" | Settings | `output.rename.outcome: {disputable: "dispute"}` / `output.rename.fields: {amount_disputable: "recoverable"}` | K5 lines only; verdicts unchanged |
| "Legal must approve anything above a lower amount" | Settings | `approvals.above_threshold_inr: 25000` (roles in `approvals.above_threshold_roles`) | Unsent disputes above it gain a legal approval row (`pdr actions`); sent ones are untouched |
| "Add a second approver for every dispute" | Settings | `approvals.send_dispute: [kam, finance]` | `pdr actions` shows the extra role waiting |
| "Lower the cost or time limit" | Settings | `budget.run_cost_credits`, `budget.per_deduction_credits`, `budget.run_time_seconds` (or `pdr run --time-limit`). To save credits, also set `evidence.search_when_corroboration_missing: false`, `evidence.max_fetches_per_deduction: 3` or `evidence.regional_query: false` | Free-check verdicts unchanged; search-dependent ones become `BUDGET_REACHED` with the stage saved (case 19) |
| "Require two independent sources" | Settings | `evidence.min_qualifying_sources: 2` | Case 1 still needs our GPS as well |
| "Stricter conflict between our records and theirs" | Settings | `records.conflict_allowance_minutes: 30` | Disagreements beyond it become `RECORDS_CONFLICT` |
| "Change the causation allowance" | Settings | `causation.allowance_hours` | Case 1 re-runs with no paid call; the recomputation table shows the new shifted due time |
| "Their file has different column names" | Adapter settings | `adapters.k4.field_map: {"Penalty Amt": "amount", "Consignment (LR)": "lr_no"}` (K3: `adapters.k3.field_map`), or let the schema mapper propose and confirm it | Same `dispute_key` and verdict as before (case 6 variants) |
| "Group letters per client per month" | Settings | `letter.grouping: monthly_per_client` | Drafted actions regroup; approvals bind to the new versions |
| "Give the client more days to respond" | Settings | `letter.response_days` | New letter version; earlier approvals are void |
| "Escalation thresholds for notices or arbitration" | Settings | `escalation.legal_notice_above_inr`, `escalation.arbitration_above_inr` | `escalation_hint` in the output |
| "Search an official source too" | Adapter | `evidence.sources: [crustdata_news, <new>]` plus one small adapter class in `pdr/evidence/` | Official orders are preferred over news |
| "Change how passages are read" | Prompt | `pdr/prompts/propose_passages.md` (wording only; the deterministic four-part test still decides) | Re-run is a model cache miss only for changed prompts |
| "A new rule" (e.g. a city no-entry window) | One registered check | New function in `pdr/engine/` with `@check(stage=Stage.records_checked, needs_terms=[...], settings=[...])`; import it from `pdr/engine/__init__.py` | Nothing in `combine` changes; the check appears in "checks run" |

Never edit to make a live fix: a contract value in settings or code (rates, caps, windows, notice periods and FM lists
come only from confirmed terms), `pdr/engine/combine.py`, or the auditor.
