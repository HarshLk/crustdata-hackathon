# Case 12a: same blockade, Client A

Identical to case 1 (LR-A-1206 instead of LR-A-1105). Compare with 12b (valid: exclusion), 12c (notice missing) and 12d (wording arguable): no default rules, each client's own terms decide.

**Hand arithmetic (D-13 timings, robust to every reading of the excused interval)**

| Step | Working | Value |
| --- | --- | --- |
| Delay | due 5 Nov 18:00 -> consignee gate-in 6 Nov 07:00 | 13 h |
| Penalty as deducted | 13 h, part day counts as a full day -> 1 day x Rs 2,000 | Rs 2,000 |
| Corroborated halt | GPS at Nagthane (Satara district), the north end of the blocked stretch, 5 Nov 06:00 -> 6 Nov 04:00 | 22 h |
| Halt inside the event's dates | blockade from 3 Nov 00:15 (TOI); still on 5 Nov (Outlook "third day"); end date not stated, so count only to 5 Nov 24:00 | 18 h |
| Allowance | `causation.allowance_hours` (settings.yaml) | 12 h |
| Reading 1: union shifts the due time | 18:00 + 18 h + 12 h = 7 Nov 00:00 > gate-in 6 Nov 07:00 | delay 0 |
| Reading 2: only halt hours inside the late window count | halt in [5 Nov 18:00, 6 Nov 00:00] = 6 h, + 12 h = 18 h >= 13 h | delay 0 |
| Correct penalty | | Rs 0.00 |
| **Disputable** | 2,000 - 0 | **Rs 2,000.00** |

Notice: email 5 Nov 11:00, within 10 days of occurrence (clause 15(iv)). FM clause names "road blockades and
agitations"; the driver report classes the event as `blockade` (settings.yaml has search words for
`blockade` but none for `agitation`).

**Sources in the cassette (real responses from Crustdata on 22 Nov 2026, trimmed to the article text):**

| Source | URL | Publication date (meta tag) | Retrieved (UTC) | Passage |
| --- | --- | --- | --- | --- |
| Outlook | https://www.outlookindia.com/national/maharashtra-farmers-protest-blocks-nh-48-strands-1500-trucks | 2026-11-06T12:38:46+05:30 | 2026-11-22T09:31:08Z | "The blockade has disrupted traffic along nearly 120 km between Nagthane in Satara district and Sangli Phata in Kolhapur district. Heavy vehicles remained stranded" |
| Times of India | timesofindia.indiatimes.com/city/kolhapur/shetti-led-blockade-reduce-traffic-to-a-crawl-on-pune-bengaluru-highway-... | 2026-11-03T23:12:00+05:30 | 2026-11-22T09:19:41Z | "The blockade started at 12.15am." (event start, 3 Nov; the page text also places the Kini toll booth "about 20km from Kolhapur towards Pune") |
| ETInfra | infra.economictimes.indiatimes.com/news/roads-highways/pune-bengaluru-national-highway-blocked-by-farmers-agitating-for-sugarcane-prices/135531083 | none machine-readable ("Published On Nov 05, 2026" only in text) | 2026-11-22T09:3x | rejected: no_date |

The search results are google.com/goto links (as in the real response), resolved by the `web` redirect entries.
