# Input format (request bundle)

Owner: d1 (adapters). Read by `pdr.adapters.reader.load_request(path_or_text, ctx)`.
A request is **a folder** (the bundle), **a single file** (treated as the deductions file), or
**a string** of pasted text (an email; parsed as free-text deductions).

Kickoff shapes are handled by the schema mapper (unknown column names are mapped to the
fields below, validated by parsing real values, and stored by header hash) or by
`settings.adapters.k3/k4.field_map` (`{"their column": "our field"}`).

```
bundle/
  request.txt            optional  one sentence of instruction (classified; never obeyed beyond the type)
  clients.yaml|json      optional  K1 clients
  lanes.yaml             optional  K1 lanes with route places (district, state)
  agreements/            optional  K2: PDFs / .txt / .md, agreements.yaml (metadata), terms.yaml (CONFIRMED terms)
  terms.yaml             optional  same as agreements/terms.yaml (either location is read)
  trips.csv|json         optional  K3 trips
  gate_log.csv           optional  K3 timestamps from gate registers, weighbridge, POD, e-way bill, FASTag
  gps.csv                optional  K3 GPS pings (halts are derived)
  notices.csv            optional  K3 notices sent by the 3PL
  notes.csv              optional  K3 ops notes / driver reports / memos (assertions)
  holds.csv              optional  K3 government holds
  deductions.csv|xlsx|json|txt|pdf   K4 (required for check_deductions)
```

Load order: K1 -> K2 confirmed terms -> K3 -> K4. Everything is put in `ctx.store`.
Logical-model JSON loads directly (`trips.json` as `list[Trip]`, `deductions.json` as
`list[Deduction]`, `clients.json` as `list[Client]`).

All timestamps: ISO 8601 or `DD-MM-YYYY HH:MM` / `DD/MM/YYYY HH:MM`. Offset-less values are read
as IST (+05:30) and the observation carries `tz_assumed: true`. Dates: `YYYY-MM-DD`,
`DD-MM-YYYY`, `DD/MM/YYYY`, `DD Mon YYYY`. Amounts: `12000`, `12,000.50`, `Rs 12,000`, `₹12,000`, `INR 12000`.

## K1 `clients.yaml`

```yaml
clients:
  - client_id: ACME
    legal_name: Acme Foods Private Limited
    aliases: [Acme Foods Pvt Ltd]        # person-confirmed aliases only
    sites: [Pune DC, Nagpur DC]
    lanes: [PUN-NAG]
    approvers: {kam: R. Shah, finance: S. Iyer}
```
A top-level list (without `clients:`) is accepted too.

## K1 `lanes.yaml`

```yaml
lanes:
  PUN-NAG:
    origin: Pune
    destination: Nagpur
    places:                              # in route order
      - {name: Pune, district: Pune, state: Maharashtra, state_local: महाराष्ट्र, lat: 18.52, lon: 73.85, radius_km: 15}
      - {name: Aurangabad, district: Aurangabad, state: Maharashtra}
      - {name: Nagpur, district: Nagpur, state: Maharashtra}
```
Trips on that lane without `route_places` get `"Place, District, State"` strings and the distinct
states in route order. No lane entry = no route geography (never invented).

## K2 `agreements/`

- Documents: `*.pdf`, `*.txt`, `*.md` (page breaks in text files: a form-feed `\f` or a line `--- page N ---`).
- `agreements.yaml`: list of `Agreement` objects (logical model).
- `terms.yaml`: list of **already confirmed** `Term` objects (logical model; `status: confirmed`), or
  `{agreements: [...], terms: [...]}`. Unconfirmed terms come only from `pdr intake`.

## K3 `trips.csv`

| column | meaning |
| --- | --- |
| trip_id | required (defaults to lr_no if absent) |
| client / client_id | client legal name or id |
| lr_no, invoice_no, eway_bill_no | references used for matching |
| origin, destination, lane, vehicle_no | |
| indent_issued_at, reporting_at, gate_out_at, arrival_at, unloaded_at | timestamps (observations) |
| `<ts field>_source` | optional TsSource for that column; default `ops_note` (the 3PL's own TMS) |
| placement_required_at, delivery_due_at | plain timestamps |
| freight, quantity, quantity_unit, delayed_quantity_value | numbers |
| route_places, route_states | `;`-separated, optional |
| driver_name, driver_phone, driver_licence | **go to vault.db only**; the trip gets `driver_ref` |
| ops_note, driver_report | free text -> `Assertion`s (pseudonymised) |

`trips.json` = `list[Trip]` in logical-model JSON (driver fields, if present as
`driver_name`/`driver_phone`/`driver_licence` keys, are split into the vault the same way).

## K3 side files

- `gate_log.csv`: `trip_id|lr_no|vehicle_no, event, at, source[, place]`; event in
  `indent_issued|reporting|gate_out|arrival|unloaded`; source is a TsSource value
  (`client_gate_log, consignee_gate_log, weighbridge, pod_signed, eway_bill, gps, fastag, driver_report, client_stated, ops_note`).
- `gps.csv`: `trip_id|vehicle_no, at, lat, lon[, place]`. A halt = consecutive pings within
  ~0.5 km for at least the threshold (`settings.adapters.k3.halt_minutes`, default 60) inside the trip window.
  The place is the `place` column or the lanes.yaml geofence containing the point.
- `notices.csv`: `trip_id|lr_no, sent_at, channel, addressee[, subject]`.
- `notes.csv`: `trip_id|lr_no, kind (ops_note|driver_report|memo|notice), text[, at, place, event_type]`.
- `holds.csv`: `trip_id|lr_no, authority, place, held_from[, released_at, reason, memo]`.

## K4 `deductions.csv|xlsx|json`

| column | meaning |
| --- | --- |
| client | client name as written by the client (required) |
| advice_no | payment advice / debit note number |
| line_no | line in that advice |
| client_deduction_no | client's own deduction number (used if no advice_no) |
| deduction_date | required |
| received_date | date the 3PL received it |
| amount | required |
| reason | reason text as stated (kept verbatim as data) |
| lr_no | trip refs (LR / trip id / e-way bill), `;`-separated for several |
| invoice_no | linked 3PL invoice |
| period_from, period_to | period covered |
| calc_rate, calc_unit, calc_base_amount, calc_units, calc_from, calc_to | the client's stated calculation |

`deductions.json`: `list[Deduction]` (logical model) or a list of row dicts with the columns above.

## K4 `deductions.txt` (pasted email) or `.pdf`

Labelled lines, any order, one deduction per block (blocks separated by a blank line followed
by a new `Advice`/`Debit Note` label, or by `---`):

```
From: Acme Foods Private Limited
Payment Advice No: PA/2026/0412   Line: 3
Date: 14-10-2026
LR No: LR-88121
Amount: Rs 12,000
Reason: Late delivery penalty 2 days @ 1% of trip cost
```
Labels recognised: client/from/customer, advice no / payment advice / debit note,
line / line no / item, deduction no, date / deduction date, received, LR / lorry receipt / GR /
trip, invoice, amount / deduction amount / penalty, reason / remarks / narration.
Every extracted value must occur verbatim in the text; a value that does not is dropped.

## Keys

`dispute_key` = `make_dispute_key(client_id, advice_no, line_no, client_deduction_no, fallback)` and
`event_fp` = `make_event_fp(client_id, trip_ref, reason_code)` from `pdr/models.py`, so the same
deduction as a sheet, as XLSX with renamed columns, and as pasted text gives the same key.
The client is matched strictly (exact normalised legal name or confirmed alias); an unmatched
name keeps `client_id = norm_key(name)` and an input problem `client_unmatched: <name>`.

## Input problems

A deduction whose required field could not be read is still created where possible and carries
`Deduction.input_problems` (e.g. `["missing_field:lr_no"]`, `["unparsed_amount:12,0O0"]`); the
engine returns cannot verify naming the field. A row with no usable amount or date is skipped and
reported in the load summary; the other rows load.
Instruction-like text inside any input ("approve all", "ignore previous", "mark as sent",
"do not dispute") adds flag `INSTRUCTION_IN_INPUT` and changes nothing else.
