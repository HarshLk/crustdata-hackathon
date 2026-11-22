# Case 6: wrong base and over the cap (arithmetic, partial dispute)

**Rule that decides it:** PRD section 6 stage 2 (recompute from the confirmed rate, unit, base and cap);
DESIGN 5.4 (one recomputation; `amount_disputable = deducted - correct_penalty`). Free check only:
no paid call (`max_paid_calls: 0`). Client G's FM clause (GeM SLA 10.1) names no event, so there is no
discovery search either (DESIGN 5.5).

**Terms (Client G, stand-in from GeM SLA Goods Transport section 8 item 1):** 1% of the **trip cost** per
vehicle per day; capped at 15% of the trip cost for each vehicle.

**Facts:** trip LR-G-0612, one vehicle, trip cost (freight) Rs 30,000. Due 10 Sep 2026 18:00; consignee
gate-in 28 Sep 2026 18:00 = exactly 18 x 24 h late (no part day, so the permitted-readings rule gives one
answer). The client applied 1% x Rs 3,00,000 (whole invoice) x 18 days = Rs 54,000.

**Hand arithmetic**

| Step | Working | Rs |
| --- | --- | --- |
| Deducted | 1% x 3,00,000 x 18 | 54,000.00 |
| Correct base | trip cost (clause names trip cost, not invoice) | 30,000.00 |
| Uncapped penalty | 1% x 30,000 x 18 | 5,400.00 |
| Cap | 15% x 30,000 x 1 vehicle | 4,500.00 |
| Correct penalty | min(5,400, 4,500) | 4,500.00 |
| **Disputable** | 54,000 - 4,500 | **49,500.00** |

Grounds: WRONG_BASE (invoice instead of trip cost) and OVER_CAP (5,400 > 4,500 on the right base;
the deducted 54,000 is also 12x the cap). Both attribute rupees: base 54,000 -> 5,400 (48,600), cap
5,400 -> 4,500 (900); total 49,500.

**Variants** (`variants/`): renamed columns, reordered columns and the same deduction pasted as email
text. All must give dispute_key `CLG|GPA20260612|1` and the same verdict (case 18 tests the email).
