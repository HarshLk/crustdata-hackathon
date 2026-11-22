# Case 13: old rate applied after an amendment

**Rule:** PRD section 6 stage 2 "Version: the rate in force on the trip date, not the latest" (and not the oldest).
Each term carries its own effective dates.

**Terms (Client H, stand-in):** base agreement clause 13 Rs 2,000 per day, effective 1 Apr 2026 to 30 Sep 2026
(term CLH-PEN-1, `effective_to: 2026-09-30`). Amendment No. 1 para 2: Rs 1,500 per day from 1 Oct 2026 (term CLH-PEN-2,
on a separate agreement document of type `amendment`, which does not supersede the whole agreement).

**Facts:** LR-H-1013 left 12 Oct 2026 06:00, due 12 Oct 18:00, consignee gate-in 14 Oct 18:00 = exactly 48 h (2 days).
Trip date 12 Oct 2026 is after 1 Oct, so the amended rate applies.

**Hand arithmetic**

| Step | Working | Rs |
| --- | --- | --- |
| Deducted | 2,000 x 2 days | 4,000.00 |
| Correct | 1,500 x 2 days | 3,000.00 |
| **Disputable** | 4,000 - 3,000 | **1,000.00** |

Citations: both versions with their effective dates. No FM clause: no search. `max_paid_calls: 0`.
