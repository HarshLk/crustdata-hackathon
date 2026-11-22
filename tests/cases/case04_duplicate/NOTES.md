# Case 4: duplicate deduction

**Rule:** DESIGN 5.7 / D-08. Same `event_fp` (client, trip, reason code) under a different `dispute_key` is a
duplicate. "First" is decided by deduction date, then advice number, then line (never arrival order). The first
follows its own verdict; each later one is `GROUND / DUPLICATE` for its full amount.

**Terms:** Client G, 1% of trip cost per vehicle per day, cap 15% of trip cost (GeM SLA 8 item 1, stand-in).

**Facts:** LR-G-0704, trip cost Rs 30,000, due 15 Sep 2026 18:00, consignee gate-in 17 Sep 2026 18:00 = exactly 48 h
(2 days, no part day). Advice GPA/2026/0704 line 1 dated 5 Oct 2026; advice GPA/2026/0781 line 2 dated 19 Oct 2026;
both Rs 600 for the same LR and the same reason.

**Hand arithmetic**

| Step | Working | Rs |
| --- | --- | --- |
| Correct penalty | 1% x 30,000 x 2 days | 600.00 |
| Cap check | 15% x 30,000 = 4,500 > 600 | no effect |
| First (5 Oct) deducted | 600 | valid, disputable 0 |
| Second (19 Oct) deducted | 600, already recovered on GPA/2026/0704 | correct penalty 0 |
| **Second disputable** | 600 - 0 | **600.00** |

Client G's FM clause names no event, so neither deduction triggers a search: `max_paid_calls: 0`.
