# REFERENCE: facts for the build

Companion to `PRD.md`. The PRD says what to build; this file holds the facts it relies on. Everything here was gathered from public sources on 22 Nov 2026 or tested against the Crustdata API that day. Use it for fixtures, extraction prompts, search queries and the README's source list. Do not hard-code any contract value from here into the engine: contract values come only from each client's confirmed terms (PRD Section 6, "No default rules").

Contents: 1 Crustdata API notes · 2 Data inventory · 3 Clause library · 4 False-deduction catalogue · 5 Real disruption events and official sources · 6 Legal, cost and data-protection facts

## 1 Crustdata API notes (tested 22 Nov 2026)

Docs: docs.crustdata.com. Verify request and response shapes against the docs at kickoff; the shapes below are what our test script saw.

**Common.** Base `https://api.crustdata.com`. Headers: `authorization: Bearer $CRUSTDATA_API_KEY`, `x-api-version: 2025-11-01`, `content-type: application/json`. Default rate limit 10 requests a minute (we slept 7 s between calls). About $0.30 per credit (from the team's balance; confirm). The key lives in `.env` (git-ignored); never log it or print it.

| Endpoint | Body | Cost | Returns | Pitfalls found |
| --- | --- | --- | --- | --- |
| `POST /web/search/live` | `{"query": "Kini toll blockade", "sources": ["news"], "start_date": <unix s>, "end_date": <unix s>, "page": 1}` | 1 credit per page | `results[]` with `title`, `url`, `snippet`; **no per-result date** | `location: "IN"` returns 0 for news: never send it. Queries over ~3 words return 0. Generic English words return foreign news. ~80% of `url`s are google.com redirect links |
| `POST /web/enrich/live` (fetch) | `{"urls": [up to 10]}` | 1 credit per URL, **even when blocked** | list of `{url, success, title, content, timestamp}`; `timestamp` is retrieval time | 11 of 17 fetches blocked in one run. Publication date must be parsed from `content` |
| `POST /company/identify` | company name | 0.2 credits per match; no match free | matched companies | Did not separate TCI Freight from TCI Group: send partial-confidence matches to a person |
| `POST /company/enrich` | company id | 2 credits | firmographics | Not needed for verdicts |
| Watchers | — | — | — | Not used; the event watch runs our own daily searches instead |

**Publication date patterns** (in order), applied to fetched `content`:

```python
PUB_PATTERNS = [
    r'property=["\']article:published_time["\'][^>]*content=["\']([^"\']+)',
    r'content=["\']([^"\']+)["\'][^>]*property=["\']article:published_time',
    r'"datePublished"\s*:\s*"([^"]+)"',
    r'name=["\'](?:pubdate|publishdate|date)["\'][^>]*content=["\']([^"\']+)',
]
```

**Redirect links.** Before fetching, turn a google.com redirect into the article URL by reading its `url` (or `q`) query parameter; if neither is present, resolve it with a plain HTTP HEAD from our own client. Cite the resolved URL. Fetching the redirect through Crustdata got blocked after a few tries.

**Blocked fetch check.** Treat a page as blocked if `success` is false, or `content` is short or lacks the query's key words (e.g. a consent or captcha page). Ledger the credit anyway and move to the next result.

**Query patterns that worked**

| Purpose | Query | Window | Result |
| --- | --- | --- | --- |
| Find a past event | "Kini toll blockade" style: 2–3 words + place | in-transit window ± allowance | Found all 6 test events without the location filter |
| Negative date test | "Odisha bandh trucks" | 10–11 Oct 2026 | Nothing new: the 8 Oct bandh isn't stretched |
| Regional language | `ଓଡ଼ିଶା ବନ୍ଦ` (Odia); Hindi equivalents | 7–11 Oct 2026 | 10 Odia local reports; Hindi reports from Amar Ujala and ETV Bharat dated 8 Oct |
| Event watch | "bandh call" | last 14 days | 10 Indian calls for bandhs still to come (Assam 36-hour, East Siang 24-hour, 5 districts on 18 Nov); one Bangladesh item |
| Event watch | "chakka jam" | last 14 days | 4 Indian items |
| Event watch (bad) | "truckers strike", "transporters strike", "transport strike announced" | last 14 days | Mostly foreign (Chile, US, Italy, UK, Canada) |
| Bad | "announced bandh or strike trucks India" + `location: "IN"` | last 14 days | 0 |

Every watch hit still needs filtering: an Indian place on an active lane, a future date, and an effect on road transport.

**Cost per contested deduction:** about 6–8 credits (1–2 searches, 3–5 fetches including blocked ones), roughly Rs 160–210. Event watch: about 1 credit per query; 10 states × 2 queries a day ≈ 600 credits a month (≈ $180).

## 2 Data inventory and minimum field set

From the discovery pack. Formats, holders and reliability are assumptions except where linked.

| Document | Key fields | Usual format (assumption) | Who holds it | Reliability (assumption) |
| --- | --- | --- | --- | --- |
| Agreement, rate card, amendments | Parties, lanes, rates, penalty schedule, caps, force majeure, notice, dispute route, effective dates | Signed PDF or scan; GeM contract PDF | Both | High, but amendments often sit in emails |
| Vehicle indent or placement request | Indent ID, vehicle type, count, site, required placement date and time, issue time | Client portal, email, WhatsApp | Client issues; 3PL receives | Medium: issue time is often not captured |
| Goods forwarding note | Consignor, consignee, goods, declared value | Paper or ERP; Form 7 under the Carriage by Road Rules, 2011 ([gist](https://taxguru.in/corporate-law/carriage-road-rules-2011-a-gist.html)) | Consignor; 3PL copy | Medium |
| Consignment note or lorry receipt (LR) | LR number and date, vehicle, origin, destination, packages, weight, freight | Paper book or TMS print; goods receipt in Form 8 under the same Rules | 3PL; copies to consignor and consignee | High for identity; date often handwritten |
| E-way bill | EWB number, generation time, invoice number, GSTINs, value, distance, vehicle number (Part B) and each vehicle update, validity | Government portal; PDF print. Validity starts at first Part B entry, 1 day per 200 km for regular cargo ([Tally](https://tallysolutions.com/gst/frequently-asked-questions-about-the-e-way-bill-answered/)) | Generator (consignor or 3PL); all parties can view | High: system timestamps |
| Client invoice for the goods | Invoice number, date, items, quantity | ERP PDF | Client | High |
| Gate-in and gate-out logs (origin and destination) | Vehicle number, in time, out time, purpose | Security register (paper) or yard system export | Client or consignee | High if electronic; medium if paper |
| Weighbridge slip | Vehicle, gross, tare, timestamp | Printed slip; plant system | Site operator | High |
| GPS or trip log | Pings (time, lat-long), halts, route, device status | Vendor dashboard; CSV export | 3PL or its GPS vendor; some clients mandate their own device | Medium to high; gaps and clock drift occur |
| Driver reports | Reporting call, SMS or WhatsApp with photo | Phone | Driver, 3PL operations | Low to medium |
| Toll (FASTag) transactions | Plaza, time, vehicle | Issuer bank statement or app | 3PL (vehicle owner) | High for location and time; worth testing |
| Proof of delivery (POD) | Receiver signature and stamp, date and time, shortages | Signed LR copy; photo; e-POD app | 3PL; consignee | Medium: time often missing or handwritten |
| 3PL freight invoice | Invoice number, trips or LRs billed, amounts | ERP or TMS PDF | 3PL | High |
| Client payment advice or remittance | Payment reference, date, invoices paid, deductions with reason text | Email PDF, Excel, bank or portal statement | Client sends; 3PL finance | High for amounts; reason text often vague |
| Debit or deduction note | Note number, date, linked invoice or LR, penalty reason, amount, calculation | Email PDF or letter | Client | Medium: calculation often absent |
| Emails and notices | Delay intimation, force-majeure notice, disputes, client replies | Email; registered post | Both | High as dated proof of notice |
| Detention or seizure papers | Authority, place, date and time held and released, reason | Official order (GST, police, RTO) | Driver and 3PL | High |

### Minimum field set

**Per deduction:** client legal name; payment advice or note number and date; line ID; amount; reason as stated; linked invoice, LR or trip IDs; period covered; date the 3PL received it (for time limits).

**Per trip:** trip or LR number; vehicle number; origin and destination sites; lane; indent issue time; required placement time; reporting time at origin (gate-in); loading complete and gate-out; e-way bill number, generation time and Part B history; delivery due time; arrival at destination (gate-in); unloading complete or POD time; trip freight and quantity; GPS track reference; holds (memo references); notices sent (time, channel, addressee).

**Per agreement:** parties and GSTINs; sites and lanes; effective date and each amendment with its date; penalty schedule (event, rate, unit, base); caps (amount, base, period); force-majeure events covered and excluded; who decides force majeure; notice deadline, method and addressee; client-caused delay rule; day-count rule (working days or hours); definitions of placement, reporting and delivery; time limit for claims and disputes; deduction route; dispute route; document precedence and the version of any linked general terms (such as GeM GTC 1.29).

## 3 Clause library

Eleven public documents were read: GeM's general terms, four GeM service-level terms, two warehousing and food-grain model tenders, an Odisha food-grain tender, two BPCL transport tenders, and the IOCL terms quoted in the TCI judgment. All were accessed 22 Nov 2026. The FCI road transport Model Tender Form could not be opened (the fci.gov.in link now returns a web page, not the PDF), so it is listed under Open questions.

### Documents

| ID | Title | Issuer | Date or version | Link |
| --- | --- | --- | --- | --- |
| D1 | General Terms and Conditions on GeM 4.0 | GeM SPV | Version 1.29, 25 Mar 2026 | [copy hosted by IISER Bhopal](https://www.iiserbpr.ac.in/uploads/file_upload/GeM%20Latest%20Guidelines%20Mar%202026.pdf) |
| D2 | SLA: Goods Transport Service (per truck per trip) | GeM service catalogue | Undated | [bidsla/50709794608561](https://bidplus.gem.gov.in/bidding/bid/bidsla/50709794608561) |
| D3 | SLA: Vehicle hiring | GeM service catalogue | Undated | [bidsla/48052915144441](https://bidplus.gem.gov.in/bidding/bid/bidsla/48052915144441) |
| D4 | STC: Handling and Transport on Lumpsum Basis | GeM service catalogue | Undated | [bidsla/51360141226801](https://bidplus.gem.gov.in/bidding/bid/bidsla/51360141226801) |
| D5 | STC: International logistics (freight, customs) | GeM service catalogue | Undated | [bidsla/54729072368100](https://bidplus.gem.gov.in/bidding/bid/bidsla/54729072368100) |
| D6 | Model tender: handling rail/road cargo at rail-side warehouses | Central Warehousing Corporation | Circular 219, 5 Apr 2023 | [cewacor.nic.in](https://cewacor.nic.in/Docs/Circulars/Commercial/modeltenderRoad_borne_cargo_at_Rail_side_Warehouse.pdf) |
| D7 | Ad hoc tender: wheat and rice transport, FCI godowns to ration shops | Delhi State Civil Supplies Corp. | No. F.15(220)/11/PDS HQ/2082, 11 Apr 2012 | [dscsc.delhi.gov.in](https://dscsc.delhi.gov.in/sites/default/files/dscsc/tender/sealed_quotations12_4_12.pdf) |
| D8 | Tender: inter-district road transport of custom-milled rice | Odisha State Civil Supplies Corp. | No. 6678/P-II/25/2025/PDS, 4 Apr 2026 | [foododisha.in](https://www.foododisha.in/Download/selection-appointment-transport-contractors-cmr-bargarh.pdf) |
| D9 | Tender: road transport of bulk petroleum by tank lorry, Gaigaon | BPCL | BPC/WR/MH-GAIGAON/POL/BULK/2016 | [bharatpetroleum.in](https://www.bharatpetroleum.in/pdf/gaigaon%20tender%202016rev.pdf) |
| D10 | Tender: road transport of packed bitumen, Muzaffarpur | BPCL | BPC/I&C/ER/BIT-PKD/MUZ/01/2019-22, 17 Apr 2019 | [bharatpetroleum.in](https://www.bharatpetroleum.in/pdf/REVISED-TENDER---Packed-Bit---Muzaffarpur---16-04-19---Final-104dfb.pdf) |
| D11 | IOCL polymer road transport ex Panipat, 2013-14 (terms as quoted in a judgment; secondary) | IOCL, via Delhi commercial court | Judgment of 25 May 2026 | [TCI Freight v IOCL](https://www.livelaw.in/pdf_upload/2026/06/09/judgement-25052026tci-freight-v-indian-oil-corporation-limited-1-678724.pdf) |

D4's page shows only the preamble. It says the penalty terms sit in the service-specific STC chosen at bid creation, so each GeM bid's own attachments must be collected. Precedence on GeM is bid terms (ATC), then service terms (STC), then general terms (GTC).

### Combined clause table

Page numbers are PDF pages, or the document's own page footer where it has one. Excerpts are verbatim; the rest is paraphrase.

| Doc | Clause, page | Clause type | Penalised event | Rate and unit | Cap | Events covered or excluded | Notice period | Excerpt |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| D1 | 15(iii), p.34 | Penalty; deduction method | Late delivery of goods or services | 0.5% of delayed-quantity value per week or part week | 5% of contract value; 10% if delay exceeds 25% of the period | Not if force majeure applies | — | "@ 0.5% of the contract value of delayed quantity per week" |
| D1 | 15(i), p.34 | Client-caused delay | — | Delivery date re-fixed without penalty | — | Delay caused by buyer or consignee | — | "delay attributable to the Buyer / Consignee" |
| D1 | 15(iv), p.35 | Force majeure; notice | — | — | Over 90 days: either side may terminate | Covers war, epidemics, civil commotion, fire, floods, strikes, lockouts, act of God. Excludes negligence and predictable or seasonal rain | 10 days, with supporting documents | "within10 days from the date of occurrence thereof" |
| D1 | 16, pp.35-36 | Dispute resolution | — | — | — | 30 days amicable, then a committee for 30 days, then DoE arbitration and mediation guidelines (OM of 3 Jun 2024). For bid contracts, the route the buyer chose | Written notice starts the 30 days | "endeavor to settle such disputes amicably" |
| D2 | 8 item 1 | Penalty; cap | Delay of a vehicle | 1% of trip cost per vehicle per day | 15% of trip cost per vehicle; 10% of contract value in total | None listed | — | "subject to the maximum of 15% of the trip cost for each vehicle" |
| D2 | 10.1 | Force majeure; notice | — | — | — | No events listed; contract may be amended | Notify cause, evidence and effect; no deadline | "cause, evidence and nature of such effect" |
| D2 | 7.1, 9.3 | Time limit; deduction method | Logbook rejection | Penalties settled before payment | — | Provider may contest a rejected logbook within the buyer's timelines | Per buyer timelines | "will be settled before making the payments" |
| D3 | 7 rows 1-5 | Penalty | Vehicle not deployed or arrives 30+ minutes late; breakdown | First: warning; then 5% and 8% of daily hire cost for late arrival; third-party hire cost plus 8-15% for non-deployment | Termination at 10% of contract value (9.3) | Escalates by instance | — | — |
| D3 | 6.2 | Deduction method | — | Deducted when the buyer generates the service delivery certificate (SDAC) | — | — | — | "All deductions (as applicable) will be accounted/deducted during SDAC generation" |
| D3 | 4.2 | Client-caused delay | Schedule change | — | — | Buyer must give 24 hours' notice of schedule changes | 24 hours (buyer) | — |
| D5 | Section 6 | Penalty | Delay in loading or unloading at buyer's premises | Rs 2,000 per day per consignment | No overall cap stated | — | — | — |
| D5 | 4.1(ix) | Client-caused delay | — | Buyer bears fines and demurrage from its late documents | — | Delay caused by buyer's documents | — | "Buyer will bear the same" |
| D6 | 13, p.7 | Penalty; cap | Delay in providing services | Rs 2,000 per day, or less as the Regional Manager sets | 15% of estimated contract value | — | — | "Liquidated Damages from the Service Provider @ Rs.2000/- per day" |
| D6 | 15.3.6 III, p.31 | Penalty | Default in labour, scales or services | Rs 2,000 per day or actual loss, whichever is higher | None; CWC decision final | — | — | — |
| D6 | Sched. II Pt III item 14, p.74 | Client-caused delay | Demurrage, wharfage from delays | Recovered from provider | — | Not if delay is beyond provider's control | — | "unless, the delay is for reasons beyond the Service Provider's control" |
| D6 | 20.1-20.2, pp.41-42 | Force majeure; notice | — | — | — | Covers war, riots, floods and natural disasters (not ordinary weather), fire, unexpected government acts. Excludes strikes by provider's or subcontractors' staff | 48 hours | — |
| D6 | 15.3.1, p.30 | Deduction method | Costs, demurrage, wharfage | Set off against any CWC contract | None | — | — | — |
| D6 | 29.2, pp.52-53 | Arbitration | — | — | — | Sole arbitrator up to Rs 50 crore; Delhi seat; provider keeps working | — | — |
| D7 | 20(a), p.6 | Penalty; deduction method | Failure to lift the scheduled quantity | Rs 10 per quintal per day; Rs 20 after target date; plus Rs 100 per quintal after validity date | None | — | — | "a penalty of Rs.10/- per qtl. per day will be imposed" |
| D7 | 20(d), p.7 | Penalty (late paperwork); notice | Delivery receipts (WCM) not handed in next working day | Rs 10 per quintal per day, minimum Rs 50 per receipt per day | 5 working days | — | Written explanation within 24 hours | — |
| D7 | 10(a), p.3 | Regulatory hold; notice | Delivery not within 3 hours of leaving godown | — | — | Seizure or detention by a government agency, or other causes beyond control, must be reported and an alternative arranged with written permission | Report to godown in-charge | — |
| D7 | 11(b)-(c), p.4 | Force majeure; notice | — | — | — | Covers fire, epidemic, act of God, civil disturbances, riots, earthquake, government orders. Excludes truck shortage, labour strikes, lockouts. CMD decides | "Immediately" | "labour strikes, lock- outs shall not be included in Force Majeure" |
| D7 | 20(k), p.8 | Penalty waiver | — | CMD may waive penalties | — | Involuntary breach or act of God | — | — |
| D7 | 35, p.10 | Arbitration | — | — | — | Sole arbitrator is the client's CMD or nominee; costs shared; Delhi courts | — | — |
| D8 | X(c), p.19 | Penalty; cap | Delay in transport | Rs 100 per day per quintal, or as the MD decides | 50% of contract value | — | — | "a sum of Rs.100 (Rupees one hundred) per day per quintal" |
| D8 | XVIII, p.22 | Force majeure | — | — | — | Delays beyond control, MD is final judge. Fire, theft and contractor's workers' strikes excluded. Bandh and flood not named | None stated | — |
| D8 | X(b), XIII, pp.18-20 | Deduction method | — | Deducted from any sum due, then security; set-off | — | — | — | "The total sum claimed shall be deducted from any sum due" |
| D8 | XVII(b), p.22 | Time limit | Contractor's claim bills | — | — | — | Within 2 months of contract expiry | — |
| D8 | XXII(a), p.26 | Arbitration | — | — | — | Sole arbitrator appointed by both sides; Bhubaneswar | — | — |
| D9 | NIT C.13, p.8 | Penalty | Lorries not placed after award letter | Rs 1 lakh per lorry from security deposit | Security deposit amount | Full non-placement: deposit forfeited, 5-year holiday listing | — | — |
| D9 | Agreement 4(l), p.31 | Penalty | Lorry not available on a day | Difference in freight for a replacement lorry | None | — | — | — |
| D9 | Agreement (f), p.32 | Client-caused delay | Unloading delayed at destination | No detention paid to carrier | — | — | — | "no detention charges etc. are payable" |
| D9 | 14, p.35 | Force majeure; notice | — | — | — | Floods, earthquake, acts of God, riots. Strikes and bandhs not named | 24 hours | — |
| D9 | Annex I 5.5, p.45 | Notice requirement | Truck not reported after normal transit time | Malpractice action under clause 8 | — | Applies even with a valid reason if delay not reported | Inform loading and receiving points | "not informing the delay as above shall be construed as a malpractice" |
| D9 | Annex I 8.2.2.2, p.48 | Penalty | Unauthorised stoppage, diversion, irregular reporting | Truck suspended 3 months, then blacklisted | — | — | — | — |
| D9 | 16, p.35 | Arbitration | — | — | — | Sole arbitrator is BPCL's Director (Marketing) or nominee | — | — |
| D10 | 8(d), p.31 | Penalty; deduction method | Late delivery of packages | Rs 100 per day of delay, recovered from bills | None stated | Waived if carrier gives a satisfactory explanation | — | "penalty on the carrier @ Rs. 100/- per day of delay" |
| D10 | 14, p.32 | Force majeure; notice | — | — | — | Floods, earthquakes, acts of God, riots | 24 hours | — |
| D11 | Special terms 14.1 | Penalty | Non-placement of vehicles | Alternative arrangement at transporter's cost; rates not quoted in the judgment | — | — | — | — |
| D11 | 8 and 5.5 | Force majeure; notice | — | — | Consult if over 30 days | Riots, civil commotion, natural disasters, impossibility of transport, strikes over 10 continuous days | Fax or email, then registered post | — |
| D11 | GTC 5 | Time limit | Claims | 3-year limitation referred to | — | — | — | — |

### What recurs, and what to extract every time

- **Rates vary in unit, not just size.** They are set per week as % of value (D1), per vehicle-day as % of trip cost (D2), per day flat (D6, D10), per quintal-day (D7, D8), or per instance (D3, D9). The service must store the unit and base, not only a number.
- **Caps are common but not universal.** They range from 5% (D1) to 50% (D8) of contract value; D7, D9 and D10 state none.
- **Force majeure lists diverge on the events that matter.** Strikes are covered by D1 and D11 (D11 only after 10 days), excluded by D6, D7 and D8, and not named by D9 and D10. Bandhs are named by none; they usually fall under "civil commotion" (D1, D11) or "civil disturbances" (D7). Assumption: whether a bandh counts will often be arguable, so verdicts should cite the exact wording.
- **Notice windows are short.** 24 hours (D9, D10), 48 hours (D6), 10 days (D1), "immediately" (D7). TCI lost partly for want of proper notice (D11).
- **Client-caused delay is explicit only sometimes** (D1, D5, D6). D9 says the client pays no detention for slow unloading.
- **Deduction is from running bills, then security, often across contracts** (D6, D8). The client often decides alone (D6, D7, D8).
- **Arbitrators named by the client** appear in D7, D9 and D10. Assumption: recent Supreme Court rulings against one-sided arbitrator appointment may weaken these clauses; a lawyer should confirm.

The service must always extract: penalised event; rate, unit and base; cap and its base; force-majeure events covered and excluded; who decides force majeure; notice method and deadline; time limit for claims; client-caused delay rule; deduction route (bills, security, other contracts); dispute route; precedence of documents; effective dates and amendments.

### GeM bids: where the terms actually sit

| Bid | Buyer | Service | What the bid itself says | What it leaves to other documents |
| --- | --- | --- | --- | --- |
| GEM/2024/B/5608340, 14 Nov 2024 | Balmer Lawrie (PSU) | Container trailers, Nhava Sheva to Turbhe, 6 months | Detention Rs 2,000 per trailer per day after 48 hours free; 3% performance security; no arbitration clause | Penalties and FM: GeM service terms and GTC, service terms prevailing |
| GEM/2025/B/5827905, 16 Jan 2025 | FCI Regional Office, Bhopal | 20,000 t food grains Shajapur (MP) to Bhawani Mandi (Rajasthan), 2 years, est. Rs 91 lakh | "As per NIT and MTF"; MTF prevails over GeM GTC | All penalty terms: the buyer SLA attachment (download by hand) |

FCI terms known from Tripura HC judgments (2026): clause X makes the contractor bear FCI's costs from its negligence (railway demurrage recovered this way); clause XVIII(vi) allows no compensation for trucks detained at godowns.

### Private-client and other rates (for fixtures)

- Hindustan Zinc finished-goods transport 2023-25: Rs 2,000 a day for late reporting against transit time; risk-purchase cost if vehicles aren't placed; one-third of freight for unapproved transshipment; no cap. Excuses delays from breakdown, agency detention, natural calamity or force majeure if reported within the transit period with proof attached to the freight bill. Pays detention Rs 2,000 a day at loading beyond 24 hours, Rs 2,000–5,000 a day at unloading.
- BHEL All India Transport Contract 2025-27: separate penalties for non-placement, late delivery, transshipment, missing GPS, overloading; recovered from running bills.
- Oil India bowser hire: late mobilisation at 1.5× daily fixed charge, capped at 7.5% of contract value.
- Oil-company tank trucks (ITDG v5.0, part of IOCL/BPCL/HPCL agreements): no money penalty per day and no FM clause; late or unreported delays are malpractice, punished by suspension (7 days upward; 1 month, 3 months, then blacklisting) and Rs 1 lakh / 3 lakh per blacklisted truck. A delay not reported to loading and receiving points is malpractice even with a valid reason (cl. 5.5). Pitch-only use case.

## 4 False-deduction catalogue

Twenty-one ways a deduction can be wrong fall into five groups. Arithmetic and client-record cases are usually decisive; force majeure is the weakest unless route-specific evidence and timely notice exist. The "How decisive" column is an assumption (our judgment), not a sourced fact. D-numbers refer to the contract library above.

| # | Reason | Example | Clause it depends on | Evidence that proves it | Where it lives | How decisive (assumption) | Indian instance |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **Force majeure** |  |  |  |  |  |  |  |
| 1 | Strike or bandh | Truck held in Cuttack on 8 Oct 2026 bandh day | FM event list and exclusions; notice window (D1 15(iv), D6 20, D7 11, D11 8) | Dated report naming the district and day; government or police statement; GPS showing the truck halted there; copy of the notice sent in time | Public; 3PL (GPS, notice) | Medium. Fails without route match and notice | [TCI Freight v IOCL](https://www.livelaw.in/pdf_upload/2026/06/09/judgement-25052026tci-freight-v-indian-oil-corporation-limited-1-678724.pdf): newspaper reports treated as hearsay; no proper notice; claim lost (25 May 2026) |
| 2 | Flood or landslide | National highway shut by landslide for two days | FM "floods" or "natural calamity"; D1 excludes predictable or seasonal rain | IMD warning; official closure notice for the road; GPS halt | Public; 3PL | Medium to high if an official closure names the road | Assumption: no case found yet; collect at kickoff |
| 3 | Road closure or diversion ordered by authorities | Traffic police close a stretch for an event | FM "government orders" (D7 11) or "unanticipated acts of Government" (D6 20.2) | Traffic police or district order naming road and hours | Public | High if the order names road and time | Delhi barred all goods vehicles 7-10 Sep 2023 for G20 ([Business Standard](https://www.business-standard.com/india-news/g20-summit-delhi-govt-issues-notification-on-traffic-restrictions-123090600070_1.html)). Some tenders shift route risk to the carrier ([Keonjhar paddy tender](https://kendujhar.odisha.gov.in/sites/default/files/2024-01/2023091481.pdf)) |
| 4 | Curfew or prohibitory order | District magistrate bars assembly or movement | FM "civil commotion" or "riots" | DM order with date and area | Public (district site, gazette) | High if order covers the route | Assumption: archive check in Section 4 |
| **Client-caused** |  |  |  |  |  |  |  |
| 5 | Late loading | Truck reported 08:00, loaded 17:00 | Buyer-caused delay (D1 15(i)); carve-out in D6 Sched. II item 14 | Client's gate-in time vs loading-complete time; loading slip | Client and 3PL | High: the client's own record | Assumption: common; confirm in interviews |
| 6 | No unloading slot or consignee delay | Consignee holds truck two days | Buyer-caused delay; D9 pays no detention, but delay should not be penalised | Consignee gate-in time; POD time; reporting call or SMS | Client, consignee, 3PL | High if gate log exists | Haryana transporters report loaded trucks waiting days at godowns, then rejected ([Tribune, 11 Mar 2025](https://www.tribuneindia.com/news/haryana/transporters-reluctant-to-join-wheat-procurement-amid-policy-dispute/amp)) |
| 7 | Client's documents late | Invoice or e-way bill issued hours after truck placed | Buyer to issue documents in reasonable time (D2 6.1); buyer bears costs of its late documents (D5 4.1(ix)) | E-way bill generation time; email timestamps | E-way bill portal; email; client | High | Assumption |
| 8 | Wrong or short-notice indent | Indent at 22:00 for 06:00 placement | Notice of schedule change (D3 4.2: 24 h; D7 9(b): one day) | Indent timestamp vs required notice | Client portal or email | High | Assumption |
| **Calculation** |  |  |  |  |  |  |  |
| 9 | Over the cap | Penalty exceeds 15% of trip cost | Cap clause (D1 15(iii), D2 8, D6 13, D8 X(c)) | Contract cap; sum of deductions for the period or trip | Contract; payment advices | High (arithmetic) | Assumption |
| 10 | Wrong base amount | % applied to whole invoice, not delayed trip or quantity | Base named in clause (D1 "delayed quantity", D2 "trip cost") | Invoice lines; trip value | Invoice; contract | High | Assumption |
| 11 | Duplicate | Same LR penalised on two payment advices | Any (no double recovery) | Same trip ID, LR or vehicle-date on two advices | Payment advices | High | Assumption |
| 12 | Old rate after amendment | Pre-amendment rate applied | Amendment and its effective date | Amendment letter; trip date | Contract file; email | High | Assumption |
| 13 | Reason not in contract | "Driver behaviour" penalty where schedule lists none | Penalty schedule | Schedule shows no such event | Contract | High ("no contractual basis") | Assumption |
| 14 | No loss shown | Flat LD deducted, client suffered no loss | Section 74, Contract Act; case law | Client's inability to show loss or a genuine pre-estimate | Client; legal | Medium (needs arbitration) | [IOCL v Standard Casting](https://delhihighcourt.nic.in/app/showFileJudgment/68013112025FAOC832021_154121.pdf), Delhi HC, 13 Nov 2025: Rs 62.5 lakh LD deduction failed; IOCL showed no loss |
| 15 | Explanation or waiver ignored | Carrier explained delay; penalty still taken | Penalty "unless satisfactory explanation" (D10 8(d)); waiver for involuntary breach (D7 20(k)) | Explanation letter and its date | 3PL | Medium | Assumption |
| **Measurement** |  |  |  |  |  |  |  |
| 16 | Sundays or holidays counted | Delay counted over a gazetted holiday | Day-count rule (D7 9(a) uses working days) | Contract rule; holiday list | Contract; public | High where contract says working days | Assumption |
| 17 | City no-entry hours | Truck could not enter Delhi until night | Transit-time definition; government orders | Traffic police restriction notification | Public | Medium to high | Delhi Traffic Police notification of 21 Jun 2024 sets goods-vehicle restriction timings ([PDF](https://traffic.delhipolice.gov.in/sites/default/files/uploads/2024/01-07-2024.pdf)) |
| 18 | Clock or GPS error | GPS gap makes truck look late | Which record governs (contract often silent) | Raw GPS pings vs gate logs vs e-way bill Part B updates; device fault report | 3PL; GPS vendor; client | Medium | D9 Annex I 6.2 requires reporting device failure within 30 minutes. Assumption: no public case found |
| 19 | Wrong start point | Placement counted from inspection pass, not gate arrival | Definition of "placement" or "reporting" | Gate-in log; reporting SMS | Client; 3PL | Medium to high | Assumption |
| **Regulatory holds** |  |  |  |  |  |  |  |
| 20 | Check-post, RTO or police detention | Truck held at border check | Detention by government agency (D7 10(a)) | Detention memo; release order; notice to client | 3PL (driver copy) | High for the fact; fault still needs checking | 300+ trucks reported held at the Assam-West Bengal border for days ([India Shipping News, 23 Oct 2026](https://indiashippingnews.com/logistics/over-300-trucks-reportedly-stranded-at-assam-wb-border-aploa-seek-centres-intervention/)) |
| 21 | E-way bill or GST detention | Goods detained for a typo in vehicle number | Fault: whose document was wrong (consignor's error is client-caused) | GST detention order and release; e-way bill history | 3PL; GST and e-way bill portals | High for the fact; fault decides the verdict | Delhi HC (Jan 2025) held Section 129 should not punish minor breaches ([LiveLaw](https://www.livelaw.in/amp/high-court/delhi-high-court/s129-cgst-act-doesnt-prescribe-statutory-penalty-doesnt-override-s126-shouldnt-be-invoked-for-minor-breaches-delhi-high-court-281322)); Allahabad HC quashed a Rs 59 lakh penalty over a vehicle-number typo ([TaxGuru](https://taxguru.in/goods-and-service-tax/minor-typo-error-e-way-bill-allahabad-hc-quashes-rs-59-lakh-gst-penalty.html)) |

One more pattern from the cases: a force-majeure claim also fails if notice was never given. In [B.S. Enterprises v IOCL](https://www.calcuttahighcourt.gov.in/Show-Judgment-File/2021~mat_586_e.pdf) (Calcutta HC, 25 Nov 2021), a COVID plea failed partly because no notice under the clause was shown. The service should check notice before it checks the disruption.

## 5 Real disruption events and official sources

Use these for test fixtures (all after 1 Sep 2026). Confirm passage and publication date by fetching before any case is submitted.

| # | Event | Date | Place and routes | Effect on trucks | Source 1 | Source 2 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | Odisha 12-hour statewide bandh (INDIA bloc) | 8 Oct 2026, 6 am to 6 pm | Bhubaneswar, Cuttack, Bargarh, Bhadrak, Balasore, Rayagada, Kendrapara, Balangir, Berhampur, Sambalpur; roads and junctions blocked | Buses, trucks, taxis stopped in several districts. Conflict: police said national highways in Bhubaneswar-Cuttack were unaffected | [OrissaPOST, 8 Oct 2026](https://www.orissapost.com/12-hour-bandh-disrupts-traffic-daily-life-across-odisha/) | [Business News Today, 9 Oct 2026](https://business-news-today.com/odisha-bandh-disrupts-transport-and-businesses-over-mining-law-and-political-demands/); also [Hans India](https://www.thehansindia.com/news/national/12-hour-bandh-by-india-bloc-disrupts-normal-life-across-odisha-1130216) |
| 2 | Farmers' protest at Kognoli toll plaza (Karnataka) | From morning of 13 Oct 2026 | NH-48 Kolhapur to Belagavi and Bengaluru; heavy vehicles diverted via Karad, Islampur, Miraj, Gokak | Maharashtra Highway Police told motorists to avoid Mumbai-Bengaluru via NH-48 | [Punekar News, 14 Oct 2026](https://www.punekarnews.in/?p=246988) | [OneIndia](https://www.oneindia.com/bengaluru/farmers-protest-at-kognoli-toll-plaza-disrupts-mumbai-bengaluru-highway-traffic-8234677.html) |
| 3 | Sugarcane farmers' blockade at Kini toll plaza | About 3 Nov 2026 onward; third day on 5 Nov | NH-48, about 120 km from Nagthane (Satara) to Sangli Phata (Kolhapur) | Over 1,500 trucks stranded, per the state transporters' association | [Outlook, 6 Nov 2026](https://www.outlookindia.com/national/maharashtra-farmers-protest-blocks-nh-48-strands-1500-trucks) | [NewsBytes](https://www.newsbytesapp.com/news/india/sugarcane-farmers-block-pune-bengaluru-highway-in-maharashtra-demanding-better-prices/tldr) |
| 4 | Trucks held at Assam-West Bengal border | Since 17 Sep 2026; still held on 23 Oct | Srirampur and Bakshirhat check points; raw petroleum coke from Assam refineries bound for Visakhapatnam, Haldia, Durgapur, Barauni, Paradip | 300+ trucks held; no written order reported | [Logistics Insider, 23 Oct 2026](https://www.logisticsinsider.in/?p=54555) | [India Shipping News, 23 Oct 2026](https://indiashippingnews.com/logistics/over-300-trucks-reportedly-stranded-at-assam-wb-border-aploa-seek-centres-intervention/) |
| 5 | Mundra empty-container yard strike, then transporters' standstill | Yards shut 28 Aug; trailer standstill from midnight 2 Sep; resumed 10 Sep 2026 | Mundra port, Kutch (Gujarat) | Container pickups halted; about 5,000 containers a day held up | [DeshGujarat, 1 Sep 2026](https://deshgujarat.com/2026/09/01/mundra-port-strike-disrupts-exim-movement-5000-containers-held-up-daily/) | [Container News](https://container-news.com/mundra-port-operations-resume-after-13-day-empty-container-yard-strike/); [Tradlinx](https://blogs.tradlinx.com/mundra-port-faces-wider-container-disruption-after-empty-yard-strike/) |
| 6 | CGST checks halt coal trucks; state stop on coal movement | Late Sep to 10 Oct 2026 | National highway in Dibrugarh (Assam); Assam and Arunachal coal movement | 150+ coal trucks blocked | [Free Press Journal, 2 Oct 2026](https://www.freepressjournal.in/business/cgst-checks-halt-over-150-coal-trucks-in-assams-dibrugarh-4042-lakh-recovered-from-36-vehicles) | Only one source found: open question |

These make good hard cases. Event 1 has conflicting accounts about highways (test case 8). Event 5 started before 1 September. Event 4 is a regulatory hold, not force majeure, and its cause is still unstated. Events 2 and 3 are on the same highway three weeks apart (lookalike).

**Notes for test cases.** Event 1 is the SOURCES\_CONFLICT case (PRD case 8). Event 3 (Kini) is the clean force-majeure case (PRD case 1); confirm its start and end dates from the sources. Event 4 has no written order, so a deduction resting on it is cannot verify without a detention memo (PRD case 7). Events 2 and 3 are on NH-48 three weeks apart, and NH-48 also runs Pune–Mumbai (lookalikes, PRD cases 2 and 3).

### Official sources with dated archives

| Source | Dated, linkable archive? | What is usable | Limits |
| --- | --- | --- | --- |
| IMD press releases | Yes. Dated PDFs listed by date ([list](https://internal.imd.gov.in/pages/press_release_mausam.php); e.g. [15 Oct 2026](https://mausam.imd.gov.in/Forecast/marquee_data/Press%20Release%2015-10-2026.pdf)) | Rain warnings and recorded rainfall by subdivision and state | Subdivision level, not district or road. District warnings page is a live map (assumption: not archived) |
| NHAI and MoRTH | No public closure archive found. MoRTH's circular archive covers policy, not closures ([archive](https://morth.gov.in/print/circular_archive)) | NHAI closures surface through police advisories or press | Open question: does NHAI's Rajmargyatra app keep history? |
| State and city traffic police | Partly. Delhi Traffic Police posts dated notifications and advisory PDFs ([example](https://traffic.delhipolice.gov.in/sites/default/files/uploads/2024/01-07-2024.pdf)). Maharashtra Highway Police's NH-48 advisory reached us only via press | Road-level closures, diversions, no-entry hours | Coverage varies by state; many advisories are posted only on social media (assumption) |
| District magistrate orders | Yes. District sites list Section 163 BNSS orders with start and end dates ([Darjeeling](https://darjeeling.gov.in/notice/order-under-section-163-of-bnss-2023-section-144-cr-p-c-1973-order-no-1-jmarms-dated-15-03-2026/), [Ri-Bhoi](https://ribhoi.gov.in/?p=32895)); Puducherry publishes them in its gazette ([example](https://styandptg.py.gov.in/2026/APRIL/EXTRAORDINARYPART-I/077-PART-I%20dated%2006-04-2026.pdf)) | Curfews and prohibitory orders by area and hours | Bandhs are called by parties, not declared by government. No official order was found for the Odisha bandh |

Why this matters: in TCI v IOCL, the court treated newspaper reports as hearsay without evidence from their authors. News will find the event; an official order or the 3PL's own GPS and notice records must carry the dispute.

## 6 Legal, cost and data-protection facts

**Cases**
- TCI Freight v IOCL (Delhi commercial court, 25 May 2026): Rs 62.8 lakh deducted over nine months in 2013-14 for non-placement, late delivery and late paperwork. TCI lost: newspaper reports were treated as hearsay without evidence from their authors, and proper force-majeure notice wasn't shown. The reason the service checks notice first and needs its own records.
- IOCL v Standard Casting (Delhi HC, 13 Nov 2025): Rs 62.5 lakh liquidated-damages deduction set aside because IOCL showed no loss (s.74 Contract Act); remanded to a new arbitrator. Basis for the "no real loss" ground, which always goes to legal.
- B.S. Enterprises v IOCL (Calcutta HC, 25 Nov 2021): COVID force-majeure plea failed partly for want of notice.
- IRCON v Kanchanjunga (Delhi HC, 29 Sep 2026): contract barred claims for bandh days; contractor still won 248 days because IRCON had admitted them and waived the bar by conduct. Hence the CLIENT\_ADMISSION\_CHECK flag.
- Delhi HC, 24 Jul 2025: arbitral majority held labour strikes not covered by that contract's clause.
- Dhanrajamal Gobindram v Shamji Kalidas (SC, 1961): strikes are included in "force majeure"; Energy Watchdog (SC): with an FM clause, relief needs the wording, a causal link, and notice and mitigation.
- No ruling settles whether a bandh is "civil commotion"; one NH contract lists "political bandh, transporters agitations, merchant agitations, riots, civil commotions" separately.
- CAG treats a penalty not levied as a favour to the contractor (Report 12 of 2024; Report 11 of 2018: Rs 17.39 crore "undue favour" at Eastern Coalfields), so public clients deduct by default.

**Legal costs** (for the escalation hint)

| Item | Cost |
| --- | --- |
| Legal notice | Rs 1,500–7,500 typical; over Rs 25,000 in complex corporate matters |
| Model arbitrator fee (Fourth Schedule), Rs 5 lakh in dispute | Rs 45,000 (sole arbitrator +25%: Rs 56,250) |
| Rs 10 lakh | Rs 62,500 (Rs 78,125) |
| Rs 50 lakh | Rs 1,87,500 (Rs 2,34,375) |

The schedule is a model, not mandatory (SC, 2022). Lawyers' fees come on top. Several PSU contracts name the client's own officer as sole arbitrator (D7, D9, D10); a lawyer should confirm how far recent Supreme Court rulings limit that.

**DPDP Act 2023 and Rules**
- Rules notified 13–14 Nov 2025; phased over 18 months; notice, consent, rights, security, breach reporting, retention and processor duties apply from May 2027. Until then the IT Act 2000 and its 2011 rules govern location data.
- Driver KYC, licence, vehicle details and real-time GPS are personal data. The 3PL is the data fiduciary; the service is its processor (Act s. 8(2)). Ground: consent or a listed legitimate use (some employment purposes). Drivers employed by subcontracted truck owners may fall outside the employment ground (assumption; lawyer to confirm).
- Processor duty: reasonable security safeguards; breaches reported to each affected person.

**Market facts for the finance view** (pitch, not engine): Haryana's wheat-lifting late penalty went from Rs 500 to Rs 5,000 a day, cut to Rs 1,000 after protests (2025-26). Share of invoices carrying deductions, dispute and win rates: no public source; label as assumptions.
