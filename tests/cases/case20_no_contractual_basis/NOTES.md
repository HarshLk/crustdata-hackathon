# Case 20: reason not in the penalty schedule (completeness rule)

**Rule:** PRD section 6 "How each type is decided": no contractual basis is disputable in full **only** when the
reason maps to no penalised event **and** a person has marked the agreement set complete. Otherwise TERMS_MISSING.

**Terms:** Client G (set marked `completeness: complete`, confirmed by kam.demo) penalises only `late_delivery`
(GeM SLA 8 item 1). Client J has the same terms but its set is marked `completeness: missing`, naming
"Schedule of other charges referred to in clause 8 of the SLA".

**Facts:** both trips delivered on time (due 8 Oct 2026 18:00, gate-in 15:00). Each client deducts Rs 2,500 as a
"Detention charge for vehicle at site" (reason code `detention_charge`).

**Hand arithmetic**

| Deduction | Working | Rs |
| --- | --- | --- |
| G, GPA/2026/1020 | no penalised event for detention in a complete set; correct penalty 0 | **2,500.00** disputable |
| J, JPA/2026/2020 | schedule missing; cannot test the basis | 0 (cannot verify, TERMS_MISSING) |

`max_paid_calls: 0` for both.
