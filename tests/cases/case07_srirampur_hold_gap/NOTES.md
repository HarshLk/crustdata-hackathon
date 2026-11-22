# Case 7: honest gap on a real event (Srirampur check point)

**Rule:** DESIGN 8.4: news alone never carries a dispute; an ops note that merely repeats the claim is not
corroboration. PRD stage 3: a regulatory hold needs the detention memo; none is on file. D-11
(`search_when_corroboration_missing: true`) runs the search anyway so the case file shows the event is real.
Result: `cannot_verify / EVIDENCE_PARTIAL`, naming the GPS extract (and the detention memo or written order).

**Terms (Client A):** FM names "seizure or detention by a government agency"; notice within 10 days, sent
5 Oct 10:00 (about 14 h after the ops note); hold rule covers government detention.

**Facts:** gate-out Guwahati 3 Oct 2026 08:00; ops note 4 Oct 20:00 "held at the Srirampur check point ... no written
order given"; due Haldia 7 Oct 18:00; consignee gate-in 16 Oct 12:00. No GPS extract, no memo. (Trip dates are set
inside what the TOI report of 7 Oct itself states: lorries stopped around Srirampur and stranded "for more than a
fortnight" at publication. The Logistics Insider report of 23 Oct says held "since September 17".)

**Which source qualifies, and why:** the fake model reading (`cassette/llm.json`, one response for every model call;
quotes not found in a page are discarded per page) gives one proposal per report. TOI: "have been stopped around
Srirampur and Bakshirhat" states the effect, event 22 Sep (a fortnight before publication) to 7 Oct (publication),
overlapping the in-transit window 3-16 Oct -> qualifies. Logistics Insider (published 23 Oct, after the window plus the
24 h allowance) -> rejected `published_out_of_window`; its only effect wording is "held", which the deterministic effect
test would not accept either (engine vocabulary gap reported by d2). One
qualifying source and no GPS/FASTag/driver record -> EVIDENCE_PARTIAL naming the GPS extract.

**Arithmetic:** delay 7 Oct 18:00 -> 16 Oct 12:00 = 8 days 18 h -> 9 days (part day counts) x Rs 2,000 = Rs 18,000 =
deducted. Nothing disputable until the memo and GPS extract arrive.

**Sources (real responses, 22 Nov 2026):**

| Source | URL | Published | Retrieved (UTC) | Passage |
| --- | --- | --- | --- | --- |
| Logistics Insider | https://www.logisticsinsider.in/trucks-stranded-assam-bengal-border-petroleum-coke/ | 2026-10-23T06:01:58+00:00 | 2026-11-22T09:31:11Z | "The vehicles have reportedly been held around Srirampur and Bakshirhat since September 17." |
| Times of India | timesofindia.indiatimes.com/city/kolkata/sos-to-pmo-after-orders-from-above-hold-up-300-raw-petroleum-coke-trucks-at-assam-bengal-border/articleshow/134754712.cms | 2026-10-07T08:39:00+05:30 | 2026-11-22T09:19:41Z | "...stranded at the Assam-West Bengal border for more than a fortnight, with authorities in West Bengal allegedly refusing passage without issuing any written order or citing a specific reason." |

The PRD row cites India Shipping News (23 Oct 2026); that page is not in the saved responses: TO CONFIRM.
