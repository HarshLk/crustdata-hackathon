# Finance view

For the finance judge. Measured figures come from the cost ledger (`pdr ledger`, `pdr economics`). Anything not
measured is labelled **ASSUMPTION**.

## Who buys and who pays today

- **Buyer: the 3PL's finance controller / CFO.** They own receivables. They see deductions eat margin and have no
  team to fight them line by line. They set the thresholds and the fee, and co-approve large disputes.
- **Daily user: the billing / receivables team.** They drop in payment advices and records, and confirm terms.
- **Who pays for this job today.** In India the 3PL's billing staff do it by hand, taking hours per deduction, so most
  small deductions are written off. Large ones go to lawyers and arbitration: a legal notice costs Rs 1,500-7,500
  (over Rs 25,000 in complex matters), and the model arbitrator fee for Rs 10 lakh in dispute is Rs 62,500 before
  lawyers' fees (REFERENCE.md section 6). TCI Freight fought a Rs 62.8 lakh IOCL deduction for 12 years and lost
  because its evidence was not recorded at the time. In the US, suppliers pay recovery firms a share of what is
  recovered. Public clients deduct by default: CAG treats a penalty not levied as an "undue favour" to the
  contractor.

## Pricing shape: pay on recovery

**A share of the amount actually recovered; nothing for checking** (DECISIONS D-29). This matches the brief's "the
client pays when the job is done" and how recovery firms already charge. The 3PL carries no risk on deductions that
turn out valid or that cannot be verified.

| Parameter | Value | Status |
| --- | --- | --- |
| Fee rate `f` (share of amount recovered) | set by the team (`economics.fee_rate_pct` in settings) | **ASSUMPTION**, no public benchmark for Indian 3PLs |
| Win rate `w` (share of disputed rupees the client reverses) | `economics.assumed_win_rate_pct` | **ASSUMPTION**, no public source |
| Share of invoices carrying deductions | not used in any calculation | **ASSUMPTION**, no public source |

Neither setting is ever read by a verdict. Only `pdr economics` and the economic gate read the cost side.

## Break-even fee rate

For a group of deductions, with `C` the total measured cost of checking all of them (credits x
`budget.credit_price_usd` x `budget.usd_inr`, plus model tokens), `D` the total amount found disputable, and `w` the
win rate:

```
revenue  = f x w x D
margin   = f x w x D - C
break-even fee rate  f* = C / (w x D)
```

`pdr economics` prints `C` per verdict type, `D`, the fee at the configured `f`, the margin, and `f*` from the
measured ledger. The price can then be defended with our own numbers.

## Cost per deduction by verdict type

Until a measured run replaces them (`pdr economics` after `pdr run`), these are the **tested figures** from REFERENCE.md
section 1 and DECISIONS F-5 (Crustdata test run, 22 Nov 2026): about $0.30 per credit, at `usd_inr` 88.0 (a demo
value, to be set from a dated source).

| Verdict type (how it is reached) | Paid calls | Credits | Approx. Rs |
| --- | --- | --- | --- |
| Settled by free checks: arithmetic (cases 4, 5, 6, 9, 13, 20), terms missing (14), notice missing (12c), wording (12b, 12d) | 0 | 0 | 0 |
| Discovery search finds nothing (cases 2, 3, 17): zero-result searches are free | 1-3 searches | 0-3 | 0-80 |
| Contested force majeure, found and fetched (cases 1, 7, 8, 21): 1-2 searches + 3-5 fetches including blocked ones | 4-7 | 6-8 | 160-210 |
| Same route and dates as a deduction already checked | cache hits | 0 | 0 |
| Model calls (passage reading, parsing) | tokens | — | measured once a key exists; zero in the build (no model key) |

Example from the test cases: case 6 recovers Rs 49,500 for Rs 0 of paid calls. Case 1 recovers Rs 2,000 for about
Rs 180. At a 15% fee and a 50% win rate (**both ASSUMPTIONS**, for illustration only), case 1 earns Rs 150 against
Rs 180 of cost. That is exactly what the economic gate is for: with `economics.search_gate_ratio` set, the service
declines a search that costs more than it could recover, and says so (`SEARCH_NOT_ECONOMICAL`), unless a person
forces it.

## Why the cost stays low at volume

Cost follows distinct route-date pairs, not deductions. One blockade is searched once for every trip it touched, and
the free pass settles most deductions with no paid call. The ledger reads Crustdata's own `x-credits-used` header, so
the cost per deduction is measured, not estimated.
