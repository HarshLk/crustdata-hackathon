# Ethics: what the service may do, what it never does, and how we prove it

The service puts claims in front of a paying client under the 3PL's name, and it touches drivers' personal data.
Three rules govern it: **nothing leaves the 3PL without a named person's approval of that exact version; no output
claims more than a source states; driver data never leaves the engine.**

## 1. Who is allowed to decide what

| Part | May | May never |
| --- | --- | --- |
| Model (intake, parsing, passage reading, refuter) | Propose a term, a field mapping or a passage; veto evidence | Decide a verdict, write a letter, approve anything, supply a value not found in the source text |
| Deterministic adjudicator | Compute the verdict from confirmed terms and cited records | Use a fact without a citation; treat an unfinished check as "no ground" |
| Auditor | Re-verify every citation; downgrade or hold | Upgrade a verdict (property test `test_property_audit_never_upgrades`) |
| People | Confirm terms, approve, override with a written reason | — |

## 2. Approvals

| Action | Undoable? | Approver (from `settings.yaml`) | Enforced by |
| --- | --- | --- | --- |
| Verdict and evidence pack | Yes, internal | None; shown to the billing team | — |
| Confirming an extracted term | Yes, but every verdict rests on it | Billing lead (legal for disputed clauses) | Unconfirmed terms give `TERMS_UNCONFIRMED`, never a ground |
| Marking an agreement set complete | Yes, logged | Billing lead | "No contractual basis" requires it (case 20) |
| Releasing an out-of-window case | Yes, logged with a reason | Account manager (`approvals.override_out_of_window`) | `pdr override`; held until then (case 9) |
| Sending a dispute | **No** | Key account manager (`approvals.send_dispute`) | Approval rows bound to `action_id`, which contains the content hash; editing voids them |
| Dispute above `approvals.above_threshold_inr` | **No** | Finance controller and legal (`approvals.above_threshold_roles`) | Extra approval rows; unsent actions gain them when the threshold changes |
| MSME ODR filing, arbitration, blaming a subcontracted truck owner | No | Legal / operations head | Out of scope for the build |

Text inside a deduction, email or document that tries to approve, send or skip a check ("approve all disputes") is
data. It is flagged `INSTRUCTION_IN_INPUT`, logged, and changes nothing (case 10; `test_instruction_in_deduction_creates_no_approval`).

## 3. Data: what is kept and what goes where

| Data | Kept | Sent to client (K7) | Sent to Crustdata | Sent to the model |
| --- | --- | --- | --- | --- |
| Driver name, phone, licence | `vault.db` only; the engine sees `driver_ref` (HMAC); every vault read logged | Never | Never | Never (pseudonymised before any call) |
| GPS trace | Halts inside the disputed trip window only; deleted after `privacy.retention_days` | Only the points that prove the timeline, without driver identity | Never | Never |
| Agreement text | `state.db` | Clauses relied on, quoted in the letter | Never | Yes, for term extraction at intake only |
| Amounts, trip ids, LR numbers, client relationship | `state.db` | Yes, in the dispute | Never | Deduction text for parsing an unseen format |
| Route places, date windows, event words | — | — | Yes: the only search content, built from an allow-list | — |
| Public article URLs and text | Snapshots by content hash | Passage and link in the evidence pack | URLs (fetch) | Article text only, for passage reading |
| A company's public name | `state.db` | — | Yes, alone (identify, for lookalikes) | — |
| Personal contact data from Crustdata | Never requested | — | — | — |

The model provider is a **data processor** (README, data sources). The processor terms we expect it, and offer the
3PL, are in [`processor_agreement_template.md`](processor_agreement_template.md). Under the DPDP Act 2023 and its
Rules (main duties from May 2027), the 3PL is the data fiduciary and this service is its processor.

## 4. What the tests prove

| Test | Proves |
| --- | --- |
| Canary: `tests/unit/privacy/test_guard.py::test_canary_blocked_for_every_destination`, `test_canary_blocked_inside_nested_payload`, `tests/unit/actions/test_report.py::test_canary_never_rendered`, `test_canary_in_ops_note_pseudonymised_via_vault`; case 10's trip carries a canary driver phone | A planted driver name and phone never reach Crustdata, the model, the outbox, the K5 output or the case file. The guard blocks it if anything tries |
| Guard: `test_guard_violation_blocks_before_anything`, `test_search_never_sends_location_and_uses_unix_seconds`, `test_search_rules`, `test_identify_rules` | Searches carry only place + event word + dates; identify carries a name alone; a violation stops the call before cache, budget or network |
| Audit: `tests/unit/engine/test_audit.py` (`test_record_tamper_downgrades`, `test_term_status_tamper_downgrades`, `test_snapshot_tamper_and_redirect_fail`, `test_property_audit_never_upgrades`); `test_audit_failure_holds`, `test_tampered_snapshot_holds_at_send` | Every citation of a disputable verdict is re-verified before it is written and again before sending; a changed record, term or page downgrades or holds; the auditor never upgrades |
| Trail: `test_trail_tamper_detected`, `test_sqlite_tamper_detected_and_pragmas` | Confirmations, approvals, overrides and sends are hash-chained; an edit after the fact is detected by `pdr trail --verify` |
| Approvals: `test_redraft_same_content_is_noop_and_edit_voids_approvals`, `test_only_approval_module_constructs_approvals` | An approval binds to one content version; only the approval command creates approval rows |
| Cases 7, 7b, 21, 23 | News alone, an undated page, or an unreported claim never becomes a dispute: cannot verify, naming the missing record |
| Case 8 | Conflicting evidence is shown side by side (the police said the highways were unaffected) even though it hurts our case |
| Cases 12a-12d | No default rules: the same blockade gives disputable, valid, or cannot verify depending on each client's own wording and notice |

## 5. Honest evidence

- A source supports a dispute only if its fetched passage states the event type, a place on the route, a date in the
  in-transit window, and an effect on road or goods movement. It must also have a readable publication date.
- News finds the event, but our own records (GPS, FASTag, timed driver report) and the notice must carry it. In TCI v
  IOCL, newspaper reports were held to be hearsay.
- Every cannot verify names the item that would decide it. Every disputable lists what, if shown, would defeat it.
- Letters are rendered from a template and the verdict's citations. They ask for a reversal on evidence; they do
  not accuse.

## 6. Stand-in labels

All agreements in the fixtures are **stand-in terms** built from real public clauses (GeM GTC and SLA, CWC, BPCL,
Delhi and Odisha civil supplies tenders; REFERENCE.md section 3). They carry `is_stand_in: true`. Every verdict that
uses them carries the flag `STAND_IN_TERMS`, and the case file and explanation say "Terms used are stand-in public
terms, not the client's signed agreement." The clients are invented ("Alpha Demo Agro Industries Pvt Ltd" and so on),
and driver data in fixtures is obviously fake. Case 17's two pages are labelled SAMPLE pages on `example.test`.
