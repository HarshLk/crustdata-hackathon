# Case 15: partial causation

**Departure from the PRD row, stated:** the PRD describes a 12-hour bandh. The only bandh with saved real pages (Odisha,
8 Oct) is the SOURCES_CONFLICT case, so this case reuses the real Kini blockade with case 1's halt and a much later
delivery. The rule tested is the same: only the overlap plus the allowance is excused (DESIGN 8.4).

**Facts:** as case 1 (GPS halt at Nagthane 5 Nov 06:00 -> 6 Nov 04:00; notice 5 Nov 11:00), but consignee gate-in
9 Nov 2026 12:00. Client A penalty Rs 2,000 per day (part day counts); Client F (otherwise Client A's terms) Rs 5,000
per instance.

**Hand arithmetic, Client A (per day)**

| Step | Working | Value |
| --- | --- | --- |
| Delay | 5 Nov 18:00 -> 9 Nov 12:00 | 90 h -> 4 days (part day counts) |
| Deducted | 4 x 2,000 | Rs 8,000 |
| Reading 1 (shift by halt-in-event + allowance) | halt inside event dates 18 h (to 5 Nov 24:00) + 12 h = 30 h; due -> 7 Nov 00:00; delay 60 h | 3 days |
| Reading 1 with the full 22 h halt | 22 + 12 = 34 h; due -> 7 Nov 04:00; delay 56 h | 3 days |
| Reading 2 (only halt hours inside the late window) | 5 Nov 18:00 -> 6 Nov 00:00 = 6 h (or 10 h to 04:00) + 12 h = 18-22 h; 90 - 18..22 = 68-72 h | 3 days |
| Correct penalty | 3 x 2,000 | Rs 6,000 |
| **Disputable** | 8,000 - 6,000 | **Rs 2,000.00** |

**Client F (per instance):** the delay (90 h) is longer than the excused interval (at most 34 h), and a per-event
penalty cannot be split -> `INCOMPLETE / CAUSATION_PARTIAL`, cannot verify, disputable 0.

**Sources in the cassette (real responses from Crustdata on 22 Nov 2026, trimmed to the article text):**

| Source | URL | Publication date (meta tag) | Retrieved (UTC) | Passage |
| --- | --- | --- | --- | --- |
| Outlook | https://www.outlookindia.com/national/maharashtra-farmers-protest-blocks-nh-48-strands-1500-trucks | 2026-11-06T12:38:46+05:30 | 2026-11-22T09:31:08Z | "The blockade has disrupted traffic along nearly 120 km between Nagthane in Satara district and Sangli Phata in Kolhapur district. Heavy vehicles remained stranded" |
| Times of India | timesofindia.indiatimes.com/city/kolhapur/shetti-led-blockade-reduce-traffic-to-a-crawl-on-pune-bengaluru-highway-... | 2026-11-03T23:12:00+05:30 | 2026-11-22T09:19:41Z | "The blockade started at 12.15am. The road is blocked from both sides at the Kini toll booth" (TO CONFIRM exact wording in the page text) |
| ETInfra | infra.economictimes.indiatimes.com/news/roads-highways/pune-bengaluru-national-highway-blocked-by-farmers-agitating-for-sugarcane-prices/135531083 | none machine-readable ("Published On Nov 05, 2026" only in text) | 2026-11-22T09:3x | rejected: no_date |

The search results are google.com/goto links (as in the real response), resolved by the `web` redirect entries.
