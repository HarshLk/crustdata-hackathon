# Case 18: an unseen input format (pasted email)

**Rule:** DESIGN 5.7 / D-08: `dispute_key = norm(client)|norm(advice_no)|norm(line_no)`, so the same deduction as a
sheet and as an email gives the same key `CLG|GPA20260612|1`; a second arrival is a **resubmission** (nothing new created,
no second dispute).

**Input:** `deductions.txt` holds the email (d1's labelled-line format). `deductions.json` holds the logical-model
row the reader must produce from it (identical to case 6's), so `load_case` and the adapter path give the same key.

**Hand arithmetic (same as case 6)**

| Step | Working | Rs |
| --- | --- | --- |
| Deducted | 1% x 3,00,000 x 18 | 54,000.00 |
| Uncapped on trip cost | 1% x 30,000 x 18 | 5,400.00 |
| Cap | 15% x 30,000 | 4,500.00 |
| **Disputable** | 54,000 - 4,500 | **49,500.00** |

`max_paid_calls: 0`.
