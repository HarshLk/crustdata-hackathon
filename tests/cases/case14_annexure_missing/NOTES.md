# Case 14: penalty schedule not on file

**Rule:** PRD section 6 "When the answer is cannot verify": TERMS_MISSING, naming "Annexure C (penalty matrix)
referenced in clause 14.2". No default rate exists anywhere, so the penalty cannot be recomputed and the basis cannot
be tested. A request for Annexure C is drafted (document_request action).

**Terms (Client K, stand-in):** the agreement set is marked `completeness: missing` with
`missing_documents: ["Annexure C (penalty matrix) referenced in clause 14.2"]`. The only confirmed term is the
definition of delivery (clause 2.3). There is no penalty_rule term.

**Facts:** LR-K-1014 due 2 Oct 2026 18:00, gate-in 4 Oct 06:00. Deducted Rs 3,000 "as per Annexure C".

**Arithmetic:** none possible; amount_disputable 0. `max_paid_calls: 0`.
