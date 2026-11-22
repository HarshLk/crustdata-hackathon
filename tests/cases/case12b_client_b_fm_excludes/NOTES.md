# Case 12b: same blockade, Client B

**Facts (identical to case 1, D-13 timings):** Pune -> Kolhapur on NH-48. Gate-out Pune 5 Nov 2026 01:00 (client
gate log). Driver report 5 Nov 06:30: NH-48 blocked by sugarcane farmers' agitation at Kini toll plaza (event type
`agitation`). GPS halt near Kini (Kolhapur district) 5 Nov 06:00 -> 6 Nov 04:00. Due 5 Nov 18:00; consignee gate-in
6 Nov 07:00. Notice email to the client 5 Nov 11:00. Deducted Rs 2,000 (1 day @ Rs 2,000).

**Terms:** fixtures/clients/client_b.yaml (stand-in).

**Rule:** PRD section 6 FM wording rule: the confirmed clause **excludes** the event (CWC style: "Strikes,
agitations or lockouts ... shall not be treated as force majeure") -> valid, unless the client's own letters admit
the delay: flag CLIENT_ADMISSION_CHECK for a person (DESIGN 5.5). No search (the clause settles it for free).

**Hand arithmetic:** delay 5 Nov 18:00 -> 6 Nov 07:00 = 13 h; part day counts as a full day -> 1 day x Rs 2,000 =
Rs 2,000 = deducted. Correct penalty Rs 2,000; disputable 0. Outcome valid.
