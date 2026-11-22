# Case 12d: same blockade, Client D

**Facts (identical to case 1, D-13 timings):** Pune -> Kolhapur on NH-48. Gate-out Pune 5 Nov 2026 01:00 (client
gate log). Driver report 5 Nov 06:30: NH-48 blocked by sugarcane farmers' agitation at Kini toll plaza (event type
`agitation`). GPS halt near Kini (Kolhapur district) 5 Nov 06:00 -> 6 Nov 04:00. Due 5 Nov 18:00; consignee gate-in
6 Nov 07:00. Notice email to the client 5 Nov 11:00. Deducted Rs 2,000 (1 day @ Rs 2,000).

**Terms:** fixtures/clients/client_d.yaml (stand-in).

**Rule:** PRD section 6 FM wording rule: the clause covers the event only through general words ("civil
commotion") -> `REFER / FM_WORDING_ARGUABLE` with the exact wording, for a person. Notice (10 days) was given in time.

**Departure from the PRD row, following DESIGN 5.5 and settings:** the PRD row says "exact wording plus evidence
found"; DESIGN 5.5 says an arguable clause gets **no search unless `evidence.search_for_referrals`** is true, and
settings.yaml has it false. So no evidence is attached and `max_paid_calls: 0`.

**Hand arithmetic:** delay 13 h -> 1 day x Rs 2,000 = Rs 2,000 = deducted; disputable 0 pending a person.
