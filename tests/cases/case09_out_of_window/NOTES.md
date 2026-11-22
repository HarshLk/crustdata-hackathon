# Case 9: dispute window passed

**Rule:** PRD section 6 stage 2: if the window has passed, add flag OUT_OF_WINDOW and route to a person before any
letter is drafted. Flags never change the outcome (DESIGN 5.9): the verdict comes from the checks as usual, and the
next action is "held for account manager"; no letter until the account manager overrides.

**Terms (Client E, stand-in from Client A's penalty clause, CWC cl.13 style):** Rs 2,000 per day of delay, part day
counts as a full day; disputes within 30 calendar days of receipt of the payment advice (stand-in built from GeM GTC 16's
30-day amicable period). No FM clause, so no discovery search.

**Facts:** LR-E-0909 due 12 Sep 2026 18:00, consignee gate-in 13 Sep 10:00 (16 h late = 1 day). Advice EPA/2026/0909
dated 20 Sep, received 22 Sep 2026, deducts Rs 3,000 at Rs 3,000 per day. Window closed 22 Oct 2026 (22 Sep + 30 days);
this case was written on 22 Nov 2026, so it is out of window on any later run date.

**Hand arithmetic**

| Step | Working | Rs |
| --- | --- | --- |
| Deducted | 3,000 x 1 day | 3,000.00 |
| Correct | 2,000 x 1 day (confirmed rate) | 2,000.00 |
| **Disputable** | 3,000 - 2,000 | **1,000.00** |

Ground WRONG_RATE; flags OUT_OF_WINDOW and STAND_IN_TERMS. `max_paid_calls: 0`.
