# Evidence scout: live validation (22 Nov 2026, agent a2)

`find_evidence` was run against the live Crustdata API for the real events in REFERENCE.md §5. Every raw
response (searches, fetches, redirect lookups) is saved under `tests/cassettes/live/<scenario>/cassette.json`.
`expected.json` beside each holds the request, the status, the queries and the passages.
`tests/unit/evidence/test_live_cassettes.py` replays them offline. Model proposals were off (there is no model key), so every
result below comes from the deterministic fallback proposer plus the four-part test.

Settings: `settings.yaml` defaults (allowance 24 h, 3 searches, 5 fetches, 3 blocked fetches, 1,500-char page minimum).

| Scenario | Route places (states) | In-transit window (IST) | Event types | Status | Expected |
| --- | --- | --- | --- | --- | --- |
| Kini, Pune→Kolhapur | Pune, Satara, Karad, Kini, Kolhapur (Maharashtra) | 4 Nov 08:00 – 5 Nov 20:00 | blockade | **found** | found |
| Kini, Pune→Mumbai | Pune, Lonavala, Khopoli, Panvel, Navi Mumbai, Mumbai (Maharashtra) | 4 Nov 08:00 – 5 Nov 20:00 | blockade | **none** | none |
| Kini route, 27–29 Oct | Pune→Kolhapur as above | 27 Oct 08:00 – 29 Oct 20:00 | blockade | **none** | none |
| Odisha bandh, Cuttack→Bhubaneswar | Cuttack, Bhubaneswar (Odisha) | 8 Oct 06:00 – 20:00 | bandh | **found** (no contradiction found live) | support, and a contradiction if the police statement is found |
| Srirampur hold, Assam→West Bengal | Guwahati, Bongaigaon, Srirampur, Siliguri (Assam, West Bengal) | 21 Oct 08:00 – 23 Oct 20:00 | govt_detention | **found** | a report of the hold |

## Supporting passages found

| Scenario | Source and URL | Published | Event dates read | Place | Passage |
| --- | --- | --- | --- | --- | --- |
| Kini, Pune→Kolhapur | Outlook, https://www.outlookindia.com/national/maharashtra-farmers-protest-blocks-nh-48-strands-1500-trucks | 2026-11-06T12:38:46+05:30 | 3–6 Nov | Satara | "The blockade has disrupted traffic along nearly 120 km between Nagthane in Satara district and Sangli Phata in Kolhapur district." |
| Odisha bandh | India Today, https://www.indiatoday.in/india/story/odisha-bandh-disrupts-transport-shops-india-bloc-protests-mining-law-ptag-3012135-2026-10-08 | 2026-10-08T10:53:46+05:30 | 8 Oct | Cuttack | "Supporters of the Congress, CPI, CPI(M), CPI(ML) Liberation, Forward Bloc, NCP (Sharad Pawar), Samajwadi Party, RJD and JMM staged demonstrations and blocked roads at several places, including Bhubaneswar, Cuttack, Bargarh, Bhadrak, Balasore, Rayagada, Kendrapara, Bolangir, Berhampur and Sambalpur." |
| Srirampur hold | Logistics Insider, https://www.logisticsinsider.in/trucks-stranded-assam-bengal-border-petroleum-coke/ | 2026-10-23T06:01:58+00:00 | 17 Sep – 23 Oct | Srirampur | "The vehicles have reportedly been held around Srirampur and Bakshirhat since September 17." |

The text-fragment links (`#:~:text=start,end`) are in each `expected.json`.

Other real Kini passages that were read but did not qualify for the run above (useful for test case 1 fixtures):
- chinimandi.com, published 2026-11-04T07:06:35Z: "Kolhapur: The indefinite highway blockade launched by the Swabhimani Shetkari Sanghatana at Kini Toll Plaza, ... continued for a second day on Wednesday". This qualified in an earlier run (window 4–5 Nov); its page names Maharashtra in the title.
- Times of India (Kolhapur city pages) "farmers-highway-blockade-enters-second-day..." and "shetti-led-blockade-reduce-traffic...": rejected `region_not_named`. The article never names Maharashtra, and we hold no district table for route places, so the place test cannot confirm which Satara or Kolhapur it means. A `place_regions` gazetteer on the request (place → state, district) would let these qualify.

## Rejections worth knowing (they show the rules working)

| Scenario | URL | Reason |
| --- | --- | --- |
| Pune→Mumbai | outlookindia.com (the same Kini article) | `place_not_on_route`. "Pune" appears only in "Pune-Bengaluru highway" and "from Pune towards Kolhapur" (D-10, case 3) |
| Kini | thefederal.com ".../maharashtra-farmers-end-nh-48-blockade..." ; TOI "...shetti-calls-off-pune-bengaluru-highway-blockade..." | `called_off` |
| Kini | indianexpress.com | `blocked` (CloudFront error; human_mode retry also blocked; both fetches on the ledger) |
| 27–29 Oct | deccanchronicle.com ".../ubt-sena-plans-rasta-roko-agitation..." | `not_happened` (a plan, in Maharashtra, not on these dates) |
| Odisha | aninews.in, theprint.in "bjd-keeps-away...", deccanchronicle.com | `announced` (pre-bandh reports) |
| Odisha | thehindu.com "normal-life-hit-in-odisha..." | `no_effect`: the article names Cuttack only in "the streets of Bhubaneswar, Cuttack"; it states no road stoppage at a route place |
| Srirampur | indiashippingnews.com "over-300-trucks-reportedly-stranded..." | `place_not_on_route` (names the Assam–WB border, not a route place) |

## Findings for other lanes (DECISIONS F-1, F-2)

1. **Google redirect resolution is failing live (F-2 regression).** Most `google.com/goto` links now come back
   `200` with a JavaScript interstitial (meta refresh to the same goto link), not a `302`. With the full Chrome
   User-Agent in `pdr/broker/http.py` (`WebHttp`), every lookup in the first run got the interstitial. With
   `User-Agent: Mozilla/5.0` (what `crustdata_test/resolve_test.py` used) the first few resolved, then Google
   served the interstitial to that one too. Roughly 15–25 of 30 links per scenario stayed unresolved
   (`unresolved_redirect`). Evidence still came from the direct publisher links in the results (about 20%).
   This is a1's module; the live runs here used a short-UA web transport registered in the run script.
   Failed lookups are not cached, so a later run retries them.
2. **Query ladder (F-1).** With 3 searches, the first event word now goes to every route place before any
   second word, one slot is kept for the state rung, and the regional rung runs only if a slot is left.
   Regional results (Odia, Hindi) were all dropped by the English snippet filter, and they cannot pass the English place test without regional place names.
   Place queries of two words returned 0–10 results ("Satara blockade" 10, "Karad blockade" 6,
   "Lonavala blockade" 0, "Cuttack bandh" 8, "Srirampur trucks stranded" 1). Zero-result searches cost 0.
3. **Wording the deterministic reader learned from these pages:** occurrence verbs ("continued for a second day",
   "began"), detention wording ("have been held", "detained") as an effect on goods movement, "since September 17"
   running to the publication date, and "at several places, including A, B, C" as a locative list.
4. **No contradiction found live for Odisha.** The police statement that the Bhubaneswar–Cuttack highways were unaffected
   was not in any page we could fetch. Its source (OrissaPOST) came back only as unresolved goto links.
   Test case 8 needs a fixture page for it. The contradiction rule is covered by a unit test
   (`test_unaffected_sentence_gives_contradiction`).
