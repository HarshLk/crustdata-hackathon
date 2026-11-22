# Case 11: kill while sending

**Rule:** PRD section 9 / DESIGN 7: approve -> `sending` -> deliver with the message id as idempotency key -> `sent`. After a kill at any crash point in `case.yaml` and a restart: exactly one message with that id at K7, status sent, and no paid call repeated (the search and fetches come from cache at zero cost). The verdict is case 1's.

**Hand arithmetic (D-13 timings, robust to every reading of the excused interval)**

| Step | Working | Value |
| --- | --- | --- |
| Delay | due 5 Nov 18:00 -> consignee gate-in 6 Nov 07:00 | 13 h |
| Penalty as deducted | 13 h, part day counts as a full day -> 1 day x Rs 2,000 | Rs 2,000 |
| Corroborated halt | GPS at Nagthane (Satara district), the north end of the blocked stretch, 5 Nov 06:00 -> 6 Nov 04:00 | 22 h |
| Halt inside the event's dates | blockade from 3 Nov 00:15 (TOI); still on 5 Nov (Outlook "third day"); end date not stated, so count only to 5 Nov 24:00 | 18 h |
| Allowance | `causation.allowance_hours` (settings.yaml) | 12 h |
| Reading 1: union shifts the due time | 18:00 + 18 h + 12 h = 7 Nov 00:00 > gate-in 6 Nov 07:00 | delay 0 |
| Reading 2: only halt hours inside the late window count | halt in [5 Nov 18:00, 6 Nov 00:00] = 6 h, + 12 h = 18 h >= 13 h | delay 0 |
| Correct penalty | | Rs 0.00 |
| **Disputable** | 2,000 - 0 | **Rs 2,000.00** |

Notice: email 5 Nov 11:00, within 10 days of occurrence (clause 15(iv)). FM clause names "road blockades and
agitations"; the driver report classes the event as `blockade` (settings.yaml has search words for
`blockade` but none for `agitation`).
