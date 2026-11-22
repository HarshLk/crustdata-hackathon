# Case 2: lookalike date (must not stretch the blockade)

**Rule:** D-03 discovery path: nobody at the 3PL asserts a disruption, the clause names event types, so a discovery
search runs. Nothing qualifying -> `NO_GROUND` with the search log cited -> valid. A discovery hit could only ever be
`INCOMPLETE`, never a ground.

**Cassette design (deliberate):** the fake search returns the **Kini** reports for any query. Their publication dates
(3 Nov and 6 Nov 2026) and the event start (3 Nov 00:15) are after the in-transit window widened by the 24 h allowance
(27 Oct 01:00 -> 30 Oct 12:00), so the date test must reject them. Real Crustdata with the 27-29 Oct window would
probably return nothing (zero-result searches are free); this cassette is the harder test. TO CONFIRM live by a2.

**Hand arithmetic**

| Step | Working | Value |
| --- | --- | --- |
| Delay | due 28 Oct 18:00 -> gate-in 29 Oct 12:00 | 18 h |
| Penalty | part day counts -> 1 day x Rs 2,000 | Rs 2,000 = deducted |
| Disputable | | 0 (valid) |

**Sources in the cassette (real responses from Crustdata on 22 Nov 2026, trimmed to the article text):**

| Source | URL | Publication date (meta tag) | Retrieved (UTC) | Passage |
| --- | --- | --- | --- | --- |
| Outlook | https://www.outlookindia.com/national/maharashtra-farmers-protest-blocks-nh-48-strands-1500-trucks | 2026-11-06T12:38:46+05:30 | 2026-11-22T09:31:08Z | "The blockade has disrupted traffic along nearly 120 km between Nagthane in Satara district and Sangli Phata in Kolhapur district. Heavy vehicles remained stranded" |
| Times of India | timesofindia.indiatimes.com/city/kolhapur/shetti-led-blockade-reduce-traffic-to-a-crawl-on-pune-bengaluru-highway-... | 2026-11-03T23:12:00+05:30 | 2026-11-22T09:19:41Z | "The blockade started at 12.15am. The road is blocked from both sides at the Kini toll booth" (TO CONFIRM exact wording in the page text) |
| ETInfra | infra.economictimes.indiatimes.com/news/roads-highways/pune-bengaluru-national-highway-blocked-by-farmers-agitating-for-sugarcane-prices/135531083 | none machine-readable ("Published On Nov 05, 2026" only in text) | 2026-11-22T09:3x | rejected: no_date |

The search results are google.com/goto links (as in the real response), resolved by the `web` redirect entries.
