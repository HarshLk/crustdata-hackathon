# Case 19: budget below one search

**Rule:** DESIGN 5.9 / D-25: when the budget cannot cover a search, every deduction that needs one still gets a verdict,
cannot verify BUDGET_REACHED ("search not completed within the run's cost limit"). Free-check verdicts are unchanged.
`settings_overrides: budget.run_cost_credits = 0.5` (one search costs at least 1 credit).

**Deductions:** case 1's facts (needs a search) -> BUDGET_REACHED, no paid call. Case 6's facts (LR-G-1119) -> unchanged:

| Step | Working | Rs |
| --- | --- | --- |
| Deducted | 1% x 3,00,000 x 18 | 54,000.00 |
| Uncapped on trip cost | 1% x 30,000 x 18 | 5,400.00 |
| Cap | 15% x 30,000 | 4,500.00 |
| **Disputable** | 54,000 - 4,500 | **49,500.00** |
