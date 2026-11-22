"""Specs for every PRD section 11 test case. Hand arithmetic lives in each spec's NOTES text.

All dates are after 1 Sep 2026. Clients are stand-ins (fixtures/clients/). Driver names and phones
never appear in a Trip (driver_ref only); the canary appears once, in case 10's ops note.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from tests.cases._build import deduction, obs, ts

LANES = yaml.safe_load((Path(__file__).resolve().parents[2] / "fixtures" / "lanes.yaml").read_text(encoding="utf-8"))["lanes"]
CANARY = "CANARY-7Q4X-DRIVER-PHONE-9000000001"  # must never appear in any outbound payload


def route(lane: str) -> tuple[list[str], list[str]]:
    l = LANES[lane]
    return [f"{p['name']}, {p['district']}, {p['state']}" for p in l["places"]], list(l["route_states"])


def trip(trip_id: str, client_id: str, lane: str, origin: str, destination: str, **kw) -> dict:
    places, states = route(lane)
    t = {"trip_id": trip_id, "client_id": client_id, "lr_no": trip_id, "origin": origin,
         "destination": destination, "lane": lane, "route_places": places, "route_states": states,
         "source_ref": {"file": "trips.json", "field": trip_id}}
    t.update(kw)
    return t


SPECS: list[dict] = []

# =============================================================================== case 6
G_NAME = "Golf Demo Public Sector Buyer"
c6_trip = trip("LR-G-0612", "CLG", "PUNE-KOLHAPUR", "Pune", "Kolhapur",
               vehicle_no="MH12ZZ0612", invoice_no="INV-G-2026-0612", freight="30000.00",
               driver_ref="DRV-0612",
               gate_out_at=[obs("2026-09-09 20:00", "client_gate_log", "gate_log.csv", 1, "gate_out_at")],
               delivery_due_at=ts("2026-09-10 18:00"),
               arrival_at=[obs("2026-09-28 18:00", "consignee_gate_log", "gate_log.csv", 2, "arrival_at")])
c6_ded = deduction("CLG", G_NAME, "GPA/2026/0612", "1", "LR-G-0612", "late_delivery",
                   deduction_date="2026-10-05", received_date="2026-10-07", amount="54000.00",
                   reason_text="Late delivery penalty 1% per vehicle per day x 18 days on invoice value Rs 3,00,000",
                   invoice_ref="INV-G-2026-0612",
                   client_calc={"rate": "1", "unit": "pct_per_vehicle_day", "base_amount": "300000.00",
                                "units_counted": "18", "from_ts": ts("2026-09-10 18:00"),
                                "to_ts": ts("2026-09-28 18:00")},
                   source_ref={"file": "deductions.csv", "row": 1})
C6_KEY = c6_ded["dispute_key"]
C6_NOTES = """
# Case 6: wrong base and over the cap (arithmetic, partial dispute)

**Rule that decides it:** PRD section 6 stage 2 (recompute from the confirmed rate, unit, base and cap);
DESIGN 5.4 (one recomputation; `amount_disputable = deducted - correct_penalty`). Free check only:
no paid call (`max_paid_calls: 0`). Client G's FM clause (GeM SLA 10.1) names no event, so there is no
discovery search either (DESIGN 5.5).

**Terms (Client G, stand-in from GeM SLA Goods Transport section 8 item 1):** 1% of the **trip cost** per
vehicle per day; capped at 15% of the trip cost for each vehicle.

**Facts:** trip LR-G-0612, one vehicle, trip cost (freight) Rs 30,000. Due 10 Sep 2026 18:00; consignee
gate-in 28 Sep 2026 18:00 = exactly 18 x 24 h late (no part day, so the permitted-readings rule gives one
answer). The client applied 1% x Rs 3,00,000 (whole invoice) x 18 days = Rs 54,000.

**Hand arithmetic**

| Step | Working | Rs |
| --- | --- | --- |
| Deducted | 1% x 3,00,000 x 18 | 54,000.00 |
| Correct base | trip cost (clause names trip cost, not invoice) | 30,000.00 |
| Uncapped penalty | 1% x 30,000 x 18 | 5,400.00 |
| Cap | 15% x 30,000 x 1 vehicle | 4,500.00 |
| Correct penalty | min(5,400, 4,500) | 4,500.00 |
| **Disputable** | 54,000 - 4,500 | **49,500.00** |

Grounds: WRONG_BASE (invoice instead of trip cost) and OVER_CAP (5,400 > 4,500 on the right base;
the deducted 54,000 is also 12x the cap). Both attribute rupees: base 54,000 -> 5,400 (48,600), cap
5,400 -> 4,500 (900); total 49,500.

**Variants** (`variants/`): renamed columns, reordered columns and the same deduction pasted as email
text. All must give dispute_key `CLG|GPA20260612|1` and the same verdict (case 18 tests the email).
"""
c6_csv_renamed = [
    ["Customer", "Payment Advice", "Item", "Debit Date", "Recd On", "Penalty Amt", "Remarks", "Consignment (LR)",
     "Bill No"],
    [G_NAME, "GPA/2026/0612", "1", "05-10-2026", "07-10-2026", "Rs 54,000",
     c6_ded["reason_text"], "LR-G-0612", "INV-G-2026-0612"],
]
c6_csv_reordered = [
    ["amount", "reason", "lr_no", "line_no", "advice_no", "client", "deduction_date", "invoice_no", "received_date"],
    ["54000.00", c6_ded["reason_text"], "LR-G-0612", "1", "GPA/2026/0612", G_NAME, "2026-10-05",
     "INV-G-2026-0612", "2026-10-07"],
]
C6_EMAIL = f"""From: {G_NAME}
Subject: Deduction against your bill INV-G-2026-0612

Dear Sir,

Please note the following deduction made in our payment run.

Payment Advice No: GPA/2026/0612   Line: 1
Date: 05-10-2026
LR No: LR-G-0612
Invoice: INV-G-2026-0612
Amount: Rs 54,000
Reason: {c6_ded["reason_text"]}

Regards,
Accounts Payable
"""
SPECS.append({
    "slug": "case06_wrong_base_over_cap", "prd": "6", "title": "1% per vehicle-day applied to the whole invoice; over the cap",
    "fixture_clients": ["g"], "trips": [c6_trip], "deductions": [c6_ded],
    "expected": {C6_KEY: {"outcome": "disputable", "grounds": ["WRONG_BASE", "OVER_CAP"],
                          "amount_disputable": "49500.00", "max_paid_calls": 0}},
    "notes": C6_NOTES,
    "variants": {"renamed_columns.csv": c6_csv_renamed, "reordered_columns.csv": c6_csv_reordered,
                 "pasted_email.txt": C6_EMAIL},
})
