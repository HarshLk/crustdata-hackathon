# Case 12c: same blockade, Client C

**Facts (identical to case 1, D-13 timings):** Pune -> Kolhapur on NH-48. Gate-out Pune 5 Nov 2026 01:00 (client
gate log). Driver report 5 Nov 06:30: NH-48 blocked by sugarcane farmers' agitation at Kini toll plaza (event type
`agitation`). GPS halt near Kini (Kolhapur district) 5 Nov 06:00 -> 6 Nov 04:00. Due 5 Nov 18:00; consignee gate-in
6 Nov 07:00. No notice record. Deducted Rs 2,000 (1 day @ Rs 2,000).

**Terms:** fixtures/clients/client_c.yaml (stand-in).

**Rule:** PRD section 6 stage 4 (notice is checked, free, before any paid search): Client C's clause 14 requires
notice within 24 hours (BPCL style). This trip has **no notice record** at all -> `INCOMPLETE /
NOTICE_EVIDENCE_MISSING` naming "notice to client within 24 hours"; no search is run (`max_paid_calls: 0`).

**Hand arithmetic:** delay 13 h -> 1 day x Rs 2,000 = Rs 2,000 = deducted; nothing disputable until the notice is
produced. If the account manager finds the 5 Nov 11:00 email (5 h after the halt began), the case re-runs.
