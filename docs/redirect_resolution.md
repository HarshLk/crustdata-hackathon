# Resolving google.com/goto news links (22 Nov 2026, agent a3)

Crustdata news search (`/web/search/live`, `sources: ["news"]`) returns most result URLs as
`https://www.google.com/goto?url=<opaque token>&gsessionid=...`. This note records what we measured and
the resolution ladder we shipped (`pdr/evidence/redirect.py`). Raw responses are in `tests/cassettes/redirect/`
(probe scripts read the key from `.env`; nothing here bypasses Google's check: no spoofed browser, cookie replay,
captcha work or proxies).

## 1. What Google's check page contains (offline)

Sources: 14 recorded pages with the title "Google". 8 came from Crustdata fetching goto links on the
morning of 22 Nov (`tests/cassettes/raw/fetch0.json`, `fetch1.json`). 6 came from Crustdata fetching the three
case-1 links today, with and without human_mode (`redirect/case1_fetch*.json`). a2's live cassettes kept 51
of our own 200 answers, but only the status, not the body. a1's `raw/live_kini` holds 9 resolves, all 302.

| Property | Count of 14 |
| --- | --- |
| `<meta http-equiv="refresh" content="N;url=/goto?url=...">`, N = 7 to 29 s | 14 |
| Refresh target is another goto link (a token different from the one requested) | 14 |
| One inline script: `delayMillis` = 4,044 to 26,976 ms, then it enables a "Continue" button that goes to the same goto link | 14 |
| Text: "Verifying your request. You'll be able to continue in a few seconds. Don't refresh this page." | 14 |
| Any absolute URL (`http://` or `https://`) anywhere in the body | 0 |
| `href=`, `data-*` attributes, `<form>`, captcha | 0 |

The page is a timed wait whose only way forward is another Google link. **The destination is not in the
body**, so there is no "extract from the interstitial" rung.

## 2. Crustdata fetch of goto links (live)

We fetched 15 goto links through `/web/enrich/live`: 12 from the Kini and Odisha searches, in two human_mode
batches of 6, and the 3 case-1 links. 4 of them were also fetched without human_mode. The publisher URL was
checked against the `Location` header (`redirect/truth.json`).

| Batch | Links | human_mode | Seconds | Credits | Article content (page check) | Response `url` | Canonical / og:url in page | Canonical = Location | Publication date |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Kini (links about 1 min old) | 6 | yes | 16.4 | 6 | 4 (2 CloudFront 403, indianexpress) | goto link, 6 of 6 | 4 | 4 of 4 | 4 of 4 |
| Odisha (about 4 min old) | 6 | yes | 70.7 | 6 | 5 (1 short page) | goto link, 6 of 6 | 6 | 6 of 6 | 4 of 5 |
| Mixed, same links | 4 | no | 9.5 | 4 | 2 (1 short, 1 Facebook reel) | goto link, 4 of 4 | 4 | 3 same, 1 same host | 2 of 2 |
| Case-1 links (about 1.5 min old) | 3 | yes | 4.0 | 3 | **0: Google check page** | goto link | 0 | n/a | n/a |
| Case-1 links, again | 3 | no | 4.1 | 3 | **0: Google check page** | goto link | 0 | n/a | n/a |

- Crustdata never reports the final URL: `url` is the requested goto link (19 of 19 items). The docs list no
  redirect or final-URL option for fetch (`urls`, `human_mode` only) and none for search (`query`, `location`,
  `sources`, `site`, `start_date`, `end_date`, `human_mode`, `page`). A news search with `human_mode: true` still
  gave 7 goto links out of 9.
- When the article comes back, the page's own `<link rel="canonical">` / `og:url` names the publisher URL. It
  matched the 302 Location in 13 of 13 cases (one at host level: a Facebook video/reel URL).
- When Google gates a link, it gates Crustdata's fetcher too, with or without human_mode, and every one of those
  fetches is billed. In the probe the plain `Location` lookup also worked: 12 of 12 at 2–3 min link age.

## 3. `sources: ["web"]` search (live)

| Query, window (IST) | news: results / goto / direct | web: results / goto / direct / social | web + `location: IN` | news + web |
| --- | --- | --- | --- | --- |
| Kini blockade, 3–7 Nov | 9 / 8 / 1 | 1 / 0 / 1 / 1 (a social post) | 1 / 0 / 1 / 1 | 10 (all tagged news) / 8 / 2 |
| Odisha bandh, 7–9 Oct | 10 / 9 / 1 | 6 / 4 / 2 / 0 | 6 / 4 / 2 / 1 | 14 / 11 / 3 |
| Srirampur trucks, 15–25 Oct | **0** | 4 / 2 / 2 / 2 | 5 / 3 / 2 / 2 | 5 (all web) / 3 / 2 |

- Web results are also goto links (50–60%), mix in social posts, and overlap the news results by title in
  **0** cases (web alone, with or without `IN`). Web search cannot supply the direct URL for a news result.
- Combined `["news","web"]` calls returned 4 news items whose URL was direct where the news-only call had given
  a goto link for the same title (2 Kini TOI, 2 Odisha). So whether Google wraps a link changes from call to call.
  Asking again costs a credit and is not reliable, so it is not a rung.

## 4. Direct links only

- Today's three searches: 1 of 9, 1 of 10 and 0 of 0 news results were publisher links.
- a2's recorded scenarios, replayed with `redirect_strategies: [direct]`: Kini (Pune→Kolhapur) 6 of 25 direct,
  **found** (Outlook); Odisha 5 of 28 direct, **found** (India Today); Srirampur 5 of 9 direct, **found**
  (Logistics Insider). The same supporting URLs come back with `[direct, location]`, because in those runs nearly
  every lookup got the check page.
- Live case-1 runs today: the direct links were indianexpress (blocked), commodity-board (`out_of_window`),
  India Today (`called_off`) and TOI (`region_not_named`). Direct links alone did not qualify a source.

## Ladder shipped (`evidence.redirect_strategies: [direct, location, crustdata_fetch]`)

1. **direct**: a publisher link is used as is.
2. **location**: our plain GET, redirects off. One attempt, 8 s timeout (`providers.web`). The wait-out for the
   timed check page is off (`interstitial_wait_seconds: 0`). Free. Only a redirect to a non-Google host is
   cached, so a 200 check page or a `/sorry/` redirect is asked again on the next run.
3. **crustdata_fetch**: Crustdata fetches the goto link (`redirect_fetch_human_mode: true`). The cited URL is the
   page's canonical/og:url, and the fetched page is used as is (no second fetch). With
   `redirect_fetch_probe_first: true` one link is fetched first. If Google's check page comes back, the rest of
   the evidence group's goto links are not paid for. Batches that hold a check page are removed from the cache
   (`broker.forget`); the ledger keeps the charge.
4. Otherwise `unresolved_redirect` goes in `EvidenceResult.rejected` and in `SearchLogLine.redirects`, and the
   scout moves on. Evidence never carries a google.com URL (also filtered at the end of `find_evidence`).

Why this order: the free lookup is instant and works whenever the link is not gated. The Crustdata fetch costs
nothing extra when it works, because it is the article fetch anyway. It fails in the same moments as the
free lookup, so the probe-first step caps the waste at 1 credit per evidence group.

## Live case-1 runs (CLI, fresh `PDR_DATA_DIR`, `PDR_LIVE=1`, no fake, case cassette not registered)

| Run | Code | Wall | Credits | Searches | Links: direct / location / crustdata_fetch / unresolved | Verdict |
| --- | --- | --- | --- | --- | --- | --- |
| live1 #1 (fresh) | before probe-first | 27.1 s | 6 | 2 (fetch room ran out) | 2 / 0 / 0 / 3 (3 paid goto fetches, all check pages) | cannot_verify, NO_EVIDENCE_FOUND |
| live1 #2 (same dir, about 6 min later) | same | 16.4 s | 6 | 2, both cache hits | 2 / 3 / 0 / 0 (the aged links now 302) | cannot_verify, NO_EVIDENCE_FOUND |
| live2 (fresh, shipped code) | probe-first | 40.2 s | 7 | 3 | 3 / 0 / 0 / 12 (1 paid probe, check page) | cannot_verify, NO_EVIDENCE_FOUND |

The second run is **not** all cache hits, by design. Failed lookups are not cached, so they are asked again (free).
Newly resolved links are new paid fetches, and a fetch batch with a different set of URLs is a new cache key.

Why the deterministic proposer did not qualify a source in these runs, by part of the four-part test:

| Source | Part that failed | Rejection |
| --- | --- | --- |
| TOI Kolhapur pages (×2) | place | `region_not_named` |
| India Today, The Federal | event | `called_off` |
| commodity-board.com | date | `out_of_window` |
| indianexpress | none (not read) | blocked, even with human_mode |

The Outlook article that qualified in a2's runs did not come up as a direct link today.

Link age matters. The case-1 goto links got the check page at about 15 s, 1.5 min and 2 min after the search,
and a 302 at about 5.5 min (`redirect/case1_plain*.json`). Links that are minutes old resolved 12 of 12 and 15 of 15.

## Still unreliable / question for Crustdata

- Fresh goto links are gated for both us and Crustdata's fetcher for the first few minutes. A live run that resolves
  seconds after searching often cites only direct links (about 10–25% of news results).
- A paid goto fetch that hits the check page is still billed (1 credit; capped at 1 per evidence group by the probe).
- Ask Crustdata: *"For `/web/search/live` with `sources: [\"news\"]`, can results return the publisher's original
  article URL instead of `https://www.google.com/goto?url=...` (or add a field with it)? If not, can
  `/web/enrich/live` follow the redirect, return the final URL in `url`, and not charge when the page is Google's
  'Verifying your request' check?"*
