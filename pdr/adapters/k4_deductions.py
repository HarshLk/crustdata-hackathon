"""K4: deductions from CSV / XLSX / JSON / PDF text / pasted email text -> Deduction."""
from __future__ import annotations

import csv
import io
import json
import logging
import re
from datetime import date
from pathlib import Path
from typing import Any, Optional

from pdr.codes import Flag
from pdr.models import Client, Deduction, SourceRef, Trip, make_dispute_key, make_event_fp, norm_key, sha256_hex

from .k1_clients import resolve_client
from .reason_map import has_instruction, map_reason, parse_client_calc
from .schema_map import mapping_for, money_str, parse_amount, parse_date, split_refs

log = logging.getLogger("pdr.adapters.k4")
TABULAR = {".csv", ".tsv", ".xlsx", ".xlsm", ".json"}


# --------------------------------------------------------------------------- readers

def read_table(p: Path) -> tuple[list[str], list[dict], Optional[str]]:
    """CSV / TSV / XLSX -> (headers, rows, sheet name)."""
    suf = p.suffix.lower()
    if suf in (".xlsx", ".xlsm"):
        from openpyxl import load_workbook
        wb = load_workbook(p, read_only=True, data_only=True)
        ws = wb.worksheets[0]
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
        rows = [r for r in rows if any(c not in (None, "") for c in r)]
        if not rows:
            return [], [], ws.title
        headers = [str(h).strip() if h is not None else f"col{i}" for i, h in enumerate(rows[0])]
        return headers, [dict(zip(headers, r)) for r in rows[1:]], ws.title
    text = p.read_text(encoding="utf-8-sig")
    dialect = "excel-tab" if suf == ".tsv" or ("\t" in text.splitlines()[0] if text else False) else "excel"
    rd = csv.DictReader(io.StringIO(text), dialect=dialect)
    headers = [h.strip() for h in (rd.fieldnames or [])]
    rows = [{(k or "").strip(): (v.strip() if isinstance(v, str) else v) for k, v in r.items()} for r in rd]
    return headers, rows, None


def pdf_text(p: Path) -> list[str]:
    """Page texts of a PDF (empty list if unreadable)."""
    try:
        from pypdf import PdfReader
        return [pg.extract_text() or "" for pg in PdfReader(str(p)).pages]
    except Exception as e:  # noqa: BLE001
        log.warning("pdf unreadable %s: %s", p.name, type(e).__name__)
        return []


# --------------------------------------------------------------------------- building a deduction

def _clients(ctx: Any) -> list[Client]:
    store = getattr(ctx, "store", None)
    return store.find(Client) if store is not None else []


def _trip_id_for(ref: str, ctx: Any, client_id: str) -> Optional[str]:
    """Exact normalised match of a reference to one stored trip (else None)."""
    store = getattr(ctx, "store", None)
    if store is None or not ref:
        return None
    n = norm_key(ref)
    hits = {t.trip_id for t in store.find(Trip, client_id=client_id)
            if n in {norm_key(t.trip_id), norm_key(t.lr_no), norm_key(t.invoice_no), norm_key(t.eway_bill_no)}}
    return hits.pop() if len(hits) == 1 else None


def build_deduction(f: dict, ref: SourceRef, ctx: Any, raw_text: str = "", default_client: Optional[str] = None,
                    problems_out: Optional[list[str]] = None) -> Optional[Deduction]:
    """Canonical field dict -> Deduction, or None (reason appended to problems_out) if amount/date unusable."""
    problems: list[str] = list(f.pop("_problems", []))
    where = f"{ref.file}" + (f" row {ref.row}" if ref.row else "")
    amount = parse_amount(f.get("amount"))
    if amount is None:
        (problems_out if problems_out is not None else []).append(
            f"{where}: amount {'missing' if f.get('amount') in (None, '') else 'unparseable'}; row skipped")
        return None
    ddate = parse_date(f.get("deduction_date"))
    if ddate is None:
        (problems_out if problems_out is not None else []).append(f"{where}: deduction_date unusable; row skipped")
        return None
    clients = _clients(ctx)
    raw_client = str(f.get("client") or "").strip()
    client_id, cands = resolve_client(raw_client, clients)
    if not raw_client and default_client:
        client_id, raw_client = default_client, ""
    if client_id is None:
        client_id = norm_key(raw_client) or "UNKNOWN"
        problems.append(f"client_unmatched:{raw_client or '(none)'}" + (f" candidates={','.join(cands)}" if cands else ""))
    reason_text = str(f.get("reason") or "").strip()
    reason_code = map_reason(reason_text, ctx)
    refs = split_refs(f.get("lr_no"))
    if not refs:
        problems.append("missing_field:lr_no")
    advice_no = str(f.get("advice_no") or "").strip() or None
    line_no = str(f.get("line_no") or "").strip() or None
    if line_no and re.fullmatch(r"\d+\.0+", line_no):
        line_no = line_no.split(".")[0]
    ded_no = str(f.get("client_deduction_no") or "").strip() or None
    fallback = {"date": ddate.isoformat(), "amount": money_str(amount), "refs": "|".join(sorted(norm_key(r) for r in refs)),
                "reason": reason_code.value}
    dispute_key = make_dispute_key(client_id, advice_no, line_no, ded_no, fallback)
    trip_key = (_trip_id_for(refs[0], ctx, client_id) or refs[0]) if refs else dispute_key
    flags = [Flag.INSTRUCTION_IN_INPUT] if has_instruction(raw_text or " ".join(str(v) for v in f.values())) else []
    pf, pt = parse_date(f.get("period_from")), parse_date(f.get("period_to"))
    rd = parse_date(f.get("received_date"))
    return Deduction(
        deduction_id="DED-" + sha256_hex(dispute_key)[:12], dispute_key=dispute_key,
        event_fp=make_event_fp(client_id, trip_key, reason_code), client_id=client_id,
        client_name_raw=raw_client, advice_no=advice_no, line_no=line_no, client_deduction_no=ded_no,
        deduction_date=ddate, received_date=rd, amount=amount, reason_text=reason_text, reason_code=reason_code,
        trip_refs=refs, invoice_ref=str(f.get("invoice_no") or "").strip() or None,
        period=(pf, pt) if pf and pt else None, client_calc=parse_client_calc(reason_text, f),
        flags=flags, input_problems=problems, source_ref=ref,
    )


# --------------------------------------------------------------------------- tabular

def rows_to_deductions(headers: list[str], rows: list[dict], fname: str, sheet: Optional[str], ctx: Any,
                       problems_out: list[str], default_client: Optional[str] = None) -> list[Deduction]:
    mapping, map_problems = mapping_for(headers, rows, "k4", ctx)
    problems_out += [f"{fname}: {p}" for p in map_problems]
    mapped = set(mapping.values())
    unmapped_required = [f"missing_field:{x}" for x in ("lr_no", "client", "reason") if x not in mapped]
    if "client" not in mapped and default_client:
        unmapped_required.remove("missing_field:client")
    out = []
    for i, r in enumerate(rows, start=2):
        f: dict[str, Any] = {fld: r.get(col) for col, fld in mapping.items()}
        f["_problems"] = [p for p in unmapped_required if p != "missing_field:lr_no"]
        raw = " ".join(str(v) for v in r.values() if v is not None)
        d = build_deduction(f, SourceRef(file=fname, sheet=sheet, row=i), ctx, raw, default_client, problems_out)
        if d is not None:
            out.append(d)
    return out


def _from_json(p: Path, ctx: Any, problems_out: list[str], default_client: Optional[str]) -> list[Deduction]:
    data = json.loads(p.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("deductions", [data])
    if data and all(isinstance(x, dict) and "dispute_key" in x and "deduction_id" in x for x in data):
        return [Deduction.model_validate(x) for x in data]
    headers = sorted({k for x in data for k in x})
    return rows_to_deductions(headers, data, p.name, None, ctx, problems_out, default_client)


# --------------------------------------------------------------------------- free text

_LABELS: dict[str, str] = {
    "client": r"(?:client(?:\s+name)?|customer|from|party|buyer)",
    "advice_no": r"(?:payment\s+advice(?:\s+no\.?|\s+number)?|advice\s+(?:no\.?|number|ref)|debit\s+note(?:\s+no\.?|\s+number)?|advice)",
    "line_no": r"(?:line(?:\s+no\.?|\s+item)?|item(?:\s+no\.?)?|s\.?\s*no\.?)",
    "client_deduction_no": r"(?:deduction\s+(?:no\.?|number|ref|id))",
    "deduction_date": r"(?:deduction\s+date|advice\s+date|note\s+date|dated|date)",
    "received_date": r"(?:received(?:\s+on|\s+date)?)",
    "lr_no": r"(?:lr\s*(?:no\.?|number|nos\.?)?|lorry\s+receipt(?:\s+no\.?)?|gr\s*(?:no\.?)?|trip(?:\s+(?:id|no\.?))?|consignment\s+note(?:\s+no\.?)?)",
    "invoice_no": r"(?:invoice(?:\s+no\.?|\s+number)?|bill\s+no\.?)",
    "amount": r"(?:deduction\s+amount|amount\s+deducted|penalty\s+amount|amount(?:\s*\(rs\.?\))?|penalty|deducted)",
    "reason": r"(?:reason|remarks?|narration|particulars|description)",
}
# value runs to the end of the line, or up to two spaces / a tab followed by another label
_SEP = r"\s*(?:[:=\-–]|#)\s*"


def _label_re(field: str) -> re.Pattern:
    return re.compile(r"(?im)(?:^|\s{2,}|\t|\|\s*|,\s+)" + _LABELS[field] + _SEP + r"(?P<v>[^\n\t|]*?)(?=\s{2,}|\t|\||$)")


_LABEL_RES = {f: _label_re(f) for f in _LABELS}
_BLOCK_SPLIT = re.compile(r"(?m)^\s*-{3,}\s*$|\n\s*\n(?=\s*(?:payment\s+advice|advice|debit\s+note|deduction\s+no)\b)", re.I)


def _extract_block(block: str) -> dict:
    f: dict[str, Any] = {}
    for field, rx in _LABEL_RES.items():
        for m in rx.finditer(block):
            v = m.group("v").strip().strip(",;")
            if field == "advice_no" and re.match(r"(?i)^(?:no\.?|number)\s*[:\-]?\s*", v):
                v = re.sub(r"(?i)^(?:no\.?|number)\s*[:\-]?\s*", "", v)
            if field == "client":
                v = re.sub(r"<[^>]*>|\S+@\S+", "", v).strip().strip(",;")
            if not v or v not in block:  # verbatim rule
                continue
            if field in ("deduction_date", "received_date") and parse_date(v) is None:
                continue
            if field == "amount" and parse_amount(v) is None:
                mm = re.search(r"(?:rs\.?|inr|₹)?\s*[\d,]+(?:\.\d+)?", v, re.I)
                if not mm or parse_amount(mm.group(0)) is None:
                    continue
                v = mm.group(0).strip()
            if field not in f:
                f[field] = v
    return f


def text_to_deductions(text: str, fname: str, ctx: Any, problems_out: list[str],
                       default_client: Optional[str] = None, page: Optional[int] = None) -> list[Deduction]:
    """Pasted email / PDF text: labelled fields, one deduction per block; every value verbatim in the source."""
    blocks = [b for b in _BLOCK_SPLIT.split(text) if b and b.strip()]
    header_client = _extract_block(blocks[0]).get("client") if blocks else None
    out = []
    for i, b in enumerate(blocks, start=1):
        f = _extract_block(b)
        if not f.get("amount") and not f.get("advice_no"):
            continue
        if not f.get("client") and header_client:
            f["client"] = header_client
        if not f.get("deduction_date"):
            dm = re.search(r"\b\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b", b)
            if dm:
                f["deduction_date"] = dm.group(0)
        ref = SourceRef(file=fname, page=page, row=i if len(blocks) > 1 else None)
        d = build_deduction(f, ref, ctx, b, default_client, problems_out)
        if d is not None:
            out.append(d)
    if not out:
        problems_out.append(f"{fname}: no deduction found in text")
    return out


# --------------------------------------------------------------------------- entry points

def load_deductions_ex(path_or_text: Any, settings: Any, ctx: Any,
                       default_client: Optional[str] = None) -> tuple[list[Deduction], list[str]]:
    """Returns (deductions, load problems). Unusable rows are skipped and named; the others load."""
    problems: list[str] = []
    p = Path(path_or_text) if isinstance(path_or_text, Path) or (
        isinstance(path_or_text, str) and "\n" not in path_or_text and len(path_or_text) < 4096
        and Path(path_or_text).exists()) else None
    if p is None:
        return text_to_deductions(str(path_or_text), "pasted_text", ctx, problems, default_client), problems
    suf = p.suffix.lower()
    if suf == ".json":
        out = _from_json(p, ctx, problems, default_client)
    elif suf in TABULAR:
        headers, rows, sheet = read_table(p)
        out = rows_to_deductions(headers, rows, p.name, sheet, ctx, problems, default_client)
    elif suf == ".pdf":
        out = []
        for n, pg in enumerate(pdf_text(p), start=1):
            out += text_to_deductions(pg, p.name, ctx, [], default_client, page=n)
        if not out:
            problems.append(f"{p.name}: no deduction found in PDF text")
    else:
        out = text_to_deductions(p.read_text(encoding="utf-8", errors="replace"), p.name, ctx, problems, default_client)
    for msg in problems:
        log.warning("k4 load: %s", msg)
    return out, problems


def k4_load_deductions(path_or_text: Any, settings: Any, ctx: Any) -> list[Deduction]:
    return load_deductions_ex(path_or_text, settings, ctx)[0]
