"""Schema mapper (D-26): propose column -> field mappings for unseen headers, validate by parsing real values.

Also holds the value parsers every adapter uses (amounts, dates, timestamps, references).
"""
from __future__ import annotations

import difflib
import re
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from pydantic import BaseModel

from pdr.models import SchemaMap, sha256_hex

IST = timezone(timedelta(hours=5, minutes=30))

# --------------------------------------------------------------------------- value parsers

_AMT_CLEAN = re.compile(r"(?i)(?:rs\.?|inr|₹|/-|\s)")
_NUM_RE = re.compile(r"^-?\d+(?:\.\d+)?$")


def parse_amount(v: Any) -> Optional[Decimal]:
    """'Rs 12,000.50', '₹12000', 12000 -> Decimal; anything else -> None."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, Decimal):
        return v
    if isinstance(v, (int, float)):
        return Decimal(str(v))
    s = _AMT_CLEAN.sub("", str(v)).strip()
    if re.fullmatch(r"-?\d{1,3}(?:,\d{2,3})+(?:\.\d+)?", s):
        s = s.replace(",", "")
    if not _NUM_RE.match(s):
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def money_str(d: Decimal) -> str:
    """Canonical 2-dp string so '12,000' and '12000.00' hash alike."""
    return str(d.quantize(Decimal("0.01")))


_DATE_FORMATS = ["%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y", "%d %b %Y", "%d %B %Y", "%d-%b-%Y",
                 "%d-%b-%y", "%d/%m/%y", "%b %d, %Y", "%B %d, %Y", "%d %b, %Y", "%Y/%m/%d"]
_TIME_FORMATS = ["%H:%M:%S", "%H:%M", "%I:%M %p", "%I:%M%p", "%H%M"]


def parse_date(v: Any) -> Optional[date]:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", str(v).strip())
    s = s.replace("Sept", "Sep")
    for f in _DATE_FORMATS:
        try:
            return datetime.strptime(s, f).date()
        except ValueError:
            pass
    m = re.match(r"^(\d{4}-\d{2}-\d{2})[T ]", s)
    if m:
        return parse_date(m.group(1))
    return None


def parse_dt(v: Any) -> Optional[tuple[datetime, bool]]:
    """Timestamp -> (aware datetime, tz_assumed). Offset-less input is read as IST."""
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return (v, False) if v.tzinfo else (v.replace(tzinfo=IST), True)
    s = str(v).strip()
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return (d, False) if d.tzinfo else (d.replace(tzinfo=IST), True)
    except ValueError:
        pass
    m = re.match(r"^(.*?)[ T,]+(\d{1,2}[:.]?\d{2}(?::\d{2})?\s*(?:[AaPp][Mm])?)\s*(?:IST|hrs|hours)?$", s)
    if m:
        d = parse_date(m.group(1).strip())
        if d:
            ts = m.group(2).replace(".", ":").upper().strip()
            for f in _TIME_FORMATS:
                try:
                    t = datetime.strptime(ts, f).time()
                    return datetime.combine(d, t, IST), True
                except ValueError:
                    pass
    d = parse_date(s)
    if d:
        return datetime.combine(d, time(0, 0), IST), True
    return None


def looks_like_ref(v: Any) -> bool:
    """An LR / invoice / e-way bill reference: has a digit, short, at most 3 tokens."""
    s = str(v or "").strip()
    return bool(s) and 2 <= len(s) <= 40 and bool(re.search(r"\d", s)) and len(s.split()) <= 3


def norm_header(h: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(h or "").lower())


# --------------------------------------------------------------------------- synonyms

K4_SYNONYMS: dict[str, list[str]] = {
    "client": ["client", "clientname", "customer", "customername", "party", "partyname", "buyer", "consignor", "from", "company"],
    "advice_no": ["adviceno", "advice", "paymentadvice", "paymentadviceno", "advicenumber", "debitnote", "debitnoteno",
                  "dnno", "remittanceno", "paymentref", "paymentreference", "voucherno", "documentno", "docno"],
    "line_no": ["lineno", "line", "lineid", "linenumber", "item", "itemno", "sno", "srno", "slno", "serialno"],
    "client_deduction_no": ["deductionno", "deductionid", "deductionnumber", "dedno", "deductionref", "penaltyno", "claimno"],
    "deduction_date": ["deductiondate", "date", "advicedate", "notedate", "debitnotedate", "documentdate", "dndate", "paymentdate", "debitdate", "deductedon", "penaltydate"],
    "received_date": ["receiveddate", "datereceived", "receivedon", "receiptdate", "recdon", "recddate", "recd", "dateofreceipt"],
    "amount": ["amount", "amt", "deductionamount", "deductionamt", "dedamt", "penalty", "penaltyrs", "penaltyamount",
               "penaltyamt", "amountrs", "amountinr", "deducted", "deductedamount", "debitamount", "ldamount", "value"],
    "reason": ["reason", "reasontext", "remarks", "remark", "narration", "description", "deductionreason", "particulars",
               "penaltyreason", "comments", "details"],
    "lr_no": ["lrno", "lr", "lrnumber", "lorryreceipt", "lorryreceiptno", "grno", "gr", "grnumber", "cnno", "consignmentnote",
              "consignmentno", "consignmentlr", "consignment", "docketno", "tripid", "tripno", "trip", "lrnos", "tripref", "triprefs", "shipmentno", "bilty", "biltyno"],
    "invoice_no": ["invoiceno", "invoice", "invno", "billno", "freightinvoice", "invoicenumber", "billnumber"],
    "period_from": ["periodfrom", "fromdate", "periodstart"],
    "period_to": ["periodto", "todate", "periodend"],
    "calc_rate": ["calcrate", "rate", "penaltyrate"],
    "calc_unit": ["calcunit", "rateunit", "unit"],
    "calc_base_amount": ["calcbaseamount", "baseamount", "base", "tripcost", "basevalue"],
    "calc_units": ["calcunits", "days", "delaydays", "noofdays", "unitscounted", "daysdelayed"],
    "calc_from": ["calcfrom"],
    "calc_to": ["calcto"],
}

K3_SYNONYMS: dict[str, list[str]] = {
    "trip_id": ["tripid", "tripno", "trip", "shipmentid", "shipmentno"],
    "client": ["client", "clientname", "customer", "clientid"],
    "lr_no": ["lrno", "lr", "lorryreceipt", "lorryreceiptno", "grno", "gr", "cnno", "docketno", "bilty"],
    "invoice_no": ["invoiceno", "invoice", "invno", "billno"],
    "eway_bill_no": ["ewaybillno", "ewaybill", "ewb", "ewbno", "eway"],
    "origin": ["origin", "from", "source", "loadingpoint", "fromlocation"],
    "destination": ["destination", "to", "dest", "unloadingpoint", "tolocation", "consignee"],
    "lane": ["lane", "route", "laneid", "routeid"],
    "vehicle_no": ["vehicleno", "vehicle", "truckno", "truck", "lorryno", "regno", "vehiclenumber"],
    "indent_issued_at": ["indentissuedat", "indenttime", "indentdate", "indentissued"],
    "placement_required_at": ["placementrequiredat", "placementdue", "requiredplacement", "placementrequired"],
    "reporting_at": ["reportingat", "reportingtime", "reported", "gatein", "gateintime", "reportedat"],
    "gate_out_at": ["gateoutat", "gateout", "gateouttime", "dispatchtime", "dispatchedat"],
    "delivery_due_at": ["deliverydueat", "duedate", "deliverydue", "edd", "expecteddelivery"],
    "arrival_at": ["arrivalat", "arrival", "arrivaltime", "arrivedat", "destgatein", "reachedat"],
    "unloaded_at": ["unloadedat", "unloaded", "unloadingtime", "podtime", "deliveredat", "podat"],
    "freight": ["freight", "freightamount", "tripcost", "freightrs"],
    "quantity": ["quantity", "qty", "weight", "tonnage"],
    "quantity_unit": ["quantityunit", "qtyunit", "uom"],
    "delayed_quantity_value": ["delayedquantityvalue", "delayedvalue"],
    "route_places": ["routeplaces", "via", "routevia"],
    "route_states": ["routestates", "states"],
    "driver_name": ["drivername", "driver"],
    "driver_phone": ["driverphone", "drivermobile", "drivercontact", "mobile", "phone", "driverno"],
    "driver_licence": ["driverlicence", "driverlicense", "dlno", "licence", "license", "licenceno", "licenseno"],
    "ops_note": ["opsnote", "opsnotes", "remarks", "notes", "note"],
    "driver_report": ["driverreport", "driverremarks"],
}

SYNONYMS = {"k3": K3_SYNONYMS, "k4": K4_SYNONYMS}
REQUIRED = {"k4": ["amount", "deduction_date"], "k3": ["vehicle_no"]}
# Fields the engine needs but whose absence does not invalidate the mapping: rows get input_problems.
WANTED = {"k4": ["client", "lr_no", "reason"], "k3": ["trip_id", "origin", "destination"]}
AMOUNT_FIELDS = {"amount", "calc_base_amount", "freight", "delayed_quantity_value", "calc_rate", "quantity", "calc_units"}
DATE_FIELDS = {"deduction_date", "received_date", "period_from", "period_to"}
DT_FIELDS = {"indent_issued_at", "placement_required_at", "reporting_at", "gate_out_at", "delivery_due_at",
             "arrival_at", "unloaded_at", "calc_from", "calc_to"}
REF_FIELDS = {"lr_no", "invoice_no", "eway_bill_no", "advice_no", "client_deduction_no"}


def header_hash(headers: list[str], source_kind: str) -> str:
    return sha256_hex(source_kind + "|" + "|".join(sorted(norm_header(h) for h in headers)))[:16]


def _deterministic(headers: list[str], source_kind: str, preset: dict[str, str] | None = None) -> dict[str, str]:
    syn = SYNONYMS[source_kind]
    out: dict[str, str] = {}
    used: set[str] = set()
    preset_n = {norm_header(k): v for k, v in (preset or {}).items()}
    for h in headers:  # 1. settings field_map
        f = preset_n.get(norm_header(h))
        if f and f not in used:
            out[h], _ = f, used.add(f)
    for h in headers:  # 2. exact synonym (field name itself counts)
        if h in out:
            continue
        nh = norm_header(h)
        for f, words in syn.items():
            if f not in used and (nh == norm_header(f) or nh in words):
                out[h] = f
                used.add(f)
                break
    for h in headers:  # 3. fuzzy: close match or a synonym with a unit suffix ("penaltyrs", "amtinr")
        if h in out:
            continue
        nh = re.sub(r"(rs|inr|no|number|s)$", "", norm_header(h)) or norm_header(h)
        best, score = None, 0.0
        for f, words in syn.items():
            if f in used:
                continue
            for w in words + [norm_header(f)]:
                r = difflib.SequenceMatcher(None, nh, w).ratio()
                if len(w) >= 4 and nh.startswith(w) and len(nh) - len(w) <= 4:
                    r = max(r, 0.9)
                if r > score:
                    best, score = f, r
        if best and score >= 0.85:
            out[h] = best
            used.add(best)
    tokens = TOKEN_RULES.get(source_kind, [])
    for h in headers:  # 4. word rules on the raw header ("Debit Date" -> deduction_date, "Amt (INR)" -> amount)
        if h in out:
            continue
        for rx, f in tokens:
            if f not in used and re.search(rx, str(h), re.I):
                out[h] = f
                used.add(f)
                break
    return out


TOKEN_RULES: dict[str, list[tuple[str, str]]] = {
    "k4": [(r"\b(recd|received|receipt)\b", "received_date"), (r"\bdate\b|\bdt\b", "deduction_date"),
           (r"\b(amt|amount|penalty|deduct\w*)\b", "amount"), (r"\b(lr|lorry|gr|consignment|docket|trip)\b", "lr_no"),
           (r"\b(invoice|bill|inv)\b", "invoice_no"), (r"\b(advice|debit\s*note|dn)\b", "advice_no"),
           (r"\b(reason|remark|narration|description)\w*", "reason"), (r"\b(client|customer|party|buyer)\b", "client")],
    "k3": [(r"\b(truck|vehicle|lorry)\b", "vehicle_no"), (r"\b(lr|gr|consignment)\b", "lr_no"),
           (r"\barriv\w*", "arrival_at"), (r"\bgate\s*out|dispatch", "gate_out_at"), (r"\breport\w*", "reporting_at")],
}


class _LlmMapping(BaseModel):
    mapping: dict[str, str] = {}


def propose_schema_map(headers: list[str], sample_rows: list[dict], source_kind: str, ctx: Any) -> SchemaMap | None:
    """Synonym/fuzzy proposer first, then llm_json("propose_schema_map") for what is left, if available."""
    preset = None
    settings = getattr(ctx, "settings", None)
    if settings is not None:
        try:
            preset = getattr(settings.adapters, source_kind).field_map
        except AttributeError:
            preset = None
    mapping = _deterministic(list(headers), source_kind, preset)
    missing = [f for f in REQUIRED[source_kind] + WANTED[source_kind] if f not in mapping.values()]
    if missing and ctx is not None:
        try:
            from pdr.llm import llm_json
            src = "headers: " + " | ".join(map(str, headers)) + "\nfields: " + ", ".join(SYNONYMS[source_kind])
            src += "\nsample: " + "; ".join(str(r) for r in sample_rows[:3])
            got = llm_json("propose_schema_map", src, _LlmMapping, ctx)  # type: ignore[arg-type]
        except Exception:
            got = None
        if got is not None:
            for col, f in getattr(got, "mapping", {}).items():
                if col in headers and col not in mapping and f in SYNONYMS[source_kind] and f not in mapping.values():
                    mapping[col] = f
    if not mapping:
        return None
    return SchemaMap(header_hash=header_hash(list(headers), source_kind), source_kind=source_kind, mapping=mapping)


def _parses(field: str, v: Any) -> bool:
    if field in AMOUNT_FIELDS:
        return parse_amount(v) is not None
    if field in DATE_FIELDS:
        return parse_date(v) is not None
    if field in DT_FIELDS:
        return parse_dt(v) is not None
    if field in REF_FIELDS or field == "lr_no":
        return all(looks_like_ref(p) for p in split_refs(v)) if field == "lr_no" else looks_like_ref(v)
    return True


def split_refs(v: Any) -> list[str]:
    return [p.strip() for p in re.split(r"[;,|\n]|\s+and\s+", str(v or "")) if p.strip()]


def validate_schema_map(m: SchemaMap, rows: list[dict]) -> list[str]:
    """Problems with the mapping, found by parsing the real values; empty list = valid."""
    problems: list[str] = []
    fields = list(m.mapping.values())
    for f in REQUIRED.get(m.source_kind, []):
        if f not in fields:
            problems.append(f"required field {f} not mapped")
    dup = {f for f in fields if fields.count(f) > 1}
    problems += [f"field {f} mapped from several columns" for f in sorted(dup)]
    for col, f in m.mapping.items():
        vals = [r.get(col) for r in rows[:50] if r.get(col) not in (None, "")]
        if not vals:
            continue
        ok = sum(1 for v in vals if _parses(f, v))
        if ok == 0 or ok / len(vals) < 0.5:
            problems.append(f"column {col!r} does not parse as {f} ({ok}/{len(vals)})")
    return problems


def mapping_for(headers: list[str], rows: list[dict], source_kind: str, ctx: Any) -> tuple[dict[str, str], list[str]]:
    """Stored map by header hash if any, else propose + validate + store. Returns (column->field, problems)."""
    store = getattr(ctx, "store", None)
    hh = header_hash(headers, source_kind)
    if store is not None:
        known = store.get(SchemaMap, hh)
        if known is not None and known.validated:
            return dict(known.mapping), []
    m = propose_schema_map(headers, rows[:5], source_kind, ctx)
    if m is None:
        return {}, [f"no column could be mapped for {source_kind}"]
    problems = validate_schema_map(m, rows)
    if problems:
        # drop columns that failed to parse, keep the rest (rows then carry input_problems)
        bad = {p.split("'")[1] for p in problems if p.startswith("column ")}
        m = m.model_copy(update={"mapping": {c: f for c, f in m.mapping.items() if c not in bad}})
        problems = validate_schema_map(m, rows) or problems
    else:
        m = m.model_copy(update={"validated": True})
        if store is not None:
            store.put(m)
    return dict(m.mapping), problems
