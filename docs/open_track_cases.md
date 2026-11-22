# Open-track test cases (submission form)

Three cases with their answers, all on real events after 1 Sep 2026: a real dated disruption (case 1), a lookalike
date (case 2), and an honest "cannot verify" (case 7). The shipment details and agreements are samples built from real
public clauses (stand-in terms, labelled as such); the disruptions and sources are real.

Each case is also an automated test: `tests/cases/case01_kini_blockade_fm/`, `case02_lookalike_date/`,
`case07_srirampur_hold_gap/` (input, trips, terms, recorded Crustdata responses, `expected.json`, `NOTES.md` with
the arithmetic). Run them with `python -m pytest -q tests/test_cases.py`.

Source status: every URL, passage and publication date below was read from a real Crustdata `/web/enrich/live`
response saved on 22 Nov 2026 (retrieval times in UTC from the response's `timestamp`). Items marked
**TO CONFIRM** were not in a fetched page and have not been checked live.

Notation: `outcome | grounds | amount_disputable | cites | flags | next action`.

---

## Case 1: Kini toll plaza blockade on NH-48 (disputable)

**Request.** A client (stand-in "Alpha Demo Agro Industries Pvt Ltd") deducted Rs 2,000 from the 3PL's payment
(advice APA/2026/1105, line 1, dated 12 Nov 2026) as a late-delivery penalty of 1 day on LR-A-1105, Pune -> Kolhapur on
NH-48. Is the deduction allowed?

**3PL's records.**
- Gate-out at Pune 5 Nov 2026 01:00 (client gate log). Delivery due 5 Nov 18:00. Consignee gate-in 6 Nov 07:00 (13 h late).
- Driver report 5 Nov 06:30: NH-48 blocked by sugarcane farmers' agitation from Nagthane towards Kini toll plaza.
- GPS halt at Nagthane (Satara district, the north end of the blocked stretch) 5 Nov 06:00 -> 6 Nov 04:00.
- Force-majeure notice emailed to the client 5 Nov 11:00.

**Sample terms stated (stand-in, built from GeM GTC 4.0 cl. 15(iv) and CWC model tender cl. 13).**
- Penalty: Rs 2,000 per day of delay, a part of a day counted as a full day (cl. 13, p. 7).
- Force majeure names "bandhs, road blockades and agitations" (cl. 15(iv), p. 35).
- Notice in writing within 10 days of the occurrence (cl. 15(iv), p. 35).
- Causation allowance (a service setting, not a contract value): 12 hours.

**Expected answer.**
`disputable | FORCE_MAJEURE | Rs 2,000.00 | FM and notice clauses; trip timeline; GPS halt; notice email; Outlook report with passage, publication date, retrieval time | STAND_IN_TERMS | hold for account manager's approval`

**Why.** Notice met (5 h after the halt began). The clause names the event. The Outlook report states the event, a
place on the route (Nagthane, Satara district, to Sangli Phata, Kolhapur district), a date inside the in-transit
window, and an effect on goods movement. Our GPS places the truck at Nagthane during the event. The halt inside the
event's dates (18 h, counting only to 5 Nov 24:00) plus the 12 h allowance moves the due time to 7 Nov 00:00, after
the 6 Nov 07:00 delivery. The correct penalty is Rs 0, so Rs 2,000 is disputable.

**Cited sources.**

| Source | URL | Passage | Published | Retrieved (UTC) |
| --- | --- | --- | --- | --- |
| Outlook | https://www.outlookindia.com/national/maharashtra-farmers-protest-blocks-nh-48-strands-1500-trucks | "The blockade has disrupted traffic along nearly 120 km between Nagthane in Satara district and Sangli Phata in Kolhapur district. Heavy vehicles remained stranded, while traffic diverted onto bypasses and smaller roads caused congestion across the region." | 2026-11-06T12:38:46+05:30 (`article:published_time`) | 2026-11-22T09:31:08Z |
| Times of India (event start) | https://timesofindia.indiatimes.com/city/kolhapur/shetti-led-blockade-reduce-traffic-to-a-crawl-on-pune-bengaluru-highway-stranded-midway-without-food-say-affected-truckers/articleshow/135508988.cms | "The blockade started at 12.15am." (from the article text) | 2026-11-03T23:12:00+05:30 | 2026-11-22T09:19:41Z |
| NewsBytes (PRD's second source) | https://www.newsbytesapp.com/news/india/sugarcane-farmers-block-pune-bengaluru-highway-in-maharashtra-demanding-better-prices/tldr | TO CONFIRM | TO CONFIRM | not fetched |

Blockade end date: not stated in the fetched pages. A search result titled "Maharashtra farmers end NH-48 blockade as
sugar mills accept Rs 4,200" (thefederal.com) exists but was not fetched: **TO CONFIRM**. The case does not depend on
the end date (only the halt up to 5 Nov 24:00 is counted).

---

## Case 2: same route, lookalike date (valid)

**Request.** Same client, same route. Advice APA/2026/1028 line 1 (4 Nov 2026) deducts Rs 2,000 for LR-A-1028: due
28 Oct 2026 18:00, consignee gate-in 29 Oct 12:00 (18 h late). Nobody at the 3PL claims a disruption: no driver
report, no GPS halt flagged, no notice.

**Sample terms stated.** Same as case 1.

**Expected answer.**
`valid | — | Rs 0 | terms applied; trip timeline; search log showing no qualifying source for 27-29 Oct | STAND_IN_TERMS | accept and record`

**Why.** Nobody asserts a disruption, so a discovery search runs for the event types the clause names. The Kini
reports exist, but the event started on 3 Nov and the reports were published on 3 and 6 Nov. That is outside the
in-transit window widened by the 24 h allowance (27 Oct 01:00 -> 30 Oct 12:00), so they are rejected and the
blockade is not stretched backwards. Penalty recomputed: 18 h -> 1 day x Rs 2,000 = Rs 2,000, equal to the amount
deducted.

**Cited sources.** The search log: queries, window, result count and the rejection reason for each Kini report (as
in case 1, rejected on date). A live search for 27-29 Oct is expected to return nothing relevant: **TO CONFIRM** live
(the test cassette deliberately returns the Kini reports to prove they are rejected).

---

## Case 7: trucks held at the Srirampur check point (cannot verify)

**Request.** Advice APA/2026/1007 (26 Oct 2026) deducts Rs 18,000 (9 days x Rs 2,000) for LR-A-1007, a raw
petroleum-coke load from Guwahati (Assam) to Haldia (West Bengal). Gate-out 3 Oct 2026 08:00; due 7 Oct 18:00;
consignee gate-in 16 Oct 12:00. Ops note 4 Oct 20:00 (pseudonymised): "truck held at the Srirampur check point on
the Assam-West Bengal border by officials; no written order given". Notice sent 5 Oct 10:00. No detention memo, no
GPS extract on file.

**Sample terms stated.** FM covers "seizure or detention by a government agency"; notice within 10 days, which was
sent; Rs 2,000 per day of delay.

**Expected answer.**
`cannot_verify | EVIDENCE_PARTIAL | Rs 0 | reports of 300+ trucks held around Srirampur and Bakshirhat since 17 Sep, which say no written order was issued; ops note | STAND_IN_TERMS | to a person: request the detention memo or written order, and the GPS extract for 3-16 Oct`

**Why.** The event is real and dated, but news alone never carries a dispute. Our own records must place the truck
there, and an ops note that only repeats the claim does not. A regulatory hold also needs the detention memo, and
the reports themselves say no written order was issued. Nothing is disputed until the memo and the GPS extract are
on file.

**Cited sources.**

| Source | URL | Passage | Published | Retrieved (UTC) |
| --- | --- | --- | --- | --- |
| Logistics Insider | https://www.logisticsinsider.in/trucks-stranded-assam-bengal-border-petroleum-coke/ | "The vehicles have reportedly been held around Srirampur and Bakshirhat since September 17." / "Transporters have alleged that police and GST officials on the ground cited only "orders from above" and served no written order." | 2026-10-23T06:01:58+00:00 | 2026-11-22T09:31:11Z |
| Times of India | https://timesofindia.indiatimes.com/city/kolkata/sos-to-pmo-after-orders-from-above-hold-up-300-raw-petroleum-coke-trucks-at-assam-bengal-border/articleshow/134754712.cms | "...have been stranded at the Assam-West Bengal border for more than a fortnight, with authorities in West Bengal allegedly refusing passage without issuing any written order or citing a specific reason." | 2026-10-07T08:39:00+05:30 | 2026-11-22T09:19:41Z |
| India Shipping News (PRD's source) | https://indiashippingnews.com/logistics/over-300-trucks-reportedly-stranded-at-assam-wb-border-aploa-seek-centres-intervention/ | TO CONFIRM | 23 Oct 2026 per REFERENCE.md; TO CONFIRM | not fetched |

---

## Also confirmed live (supports test case 8, not an Open-track case)

OrissaPOST, "12-hour bandh disrupts traffic, daily life across Odisha":
https://www.orissapost.com/12-hour-bandh-disrupts-traffic-daily-life-across-odisha/, published
2026-10-08T12:18:00+00:00, retrieved 2026-11-22T10:51:35Z (live fetch; needed `human_mode: true`). Passage: "The
major roads, including national highways, and train lines have remained unaffected." (quoting the
Bhubaneswar-Cuttack Police Commissioner). The same article and ThePrint (PTI) also say roads and national highways
were blocked in Bhubaneswar and Cuttack. That conflict is why case 8 is SOURCES_CONFLICT.
