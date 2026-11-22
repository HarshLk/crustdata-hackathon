"""Regenerate the case folders from the specs in tests/cases/_specs.py (run: python -m tests.cases._build).

The generated files are committed; edit the spec, not the generated file, then re-run.
"""
from __future__ import annotations

import csv
import io
import json
import shutil
from pathlib import Path

import yaml

from pdr.models import make_dispute_key, make_event_fp

HERE = Path(__file__).parent
REPO = HERE.parent.parent
FIX = REPO / "fixtures" / "clients"


def ts(s: str) -> str:
    """'2026-11-05 18:00' -> ISO 8601 in IST."""
    d, t = s.split(" ")
    return f"{d}T{t}:00+05:30"


def obs(when: str, source: str, file: str, row: int, field: str) -> dict:
    return {"value": ts(when), "source": source, "ref": {"file": file, "row": row, "field": field}}


def fixture_client(name: str) -> dict:
    with open(FIX / f"client_{name}.yaml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def deduction(client_id: str, client_name_raw: str, advice_no: str, line_no: str, trip_id: str,
              reason_code: str, **kw) -> dict:
    row = {"client_id": client_id, "client_name_raw": client_name_raw, "advice_no": advice_no,
           "line_no": line_no, "reason_code": reason_code, "trip_refs": [trip_id], **kw}
    row["dispute_key"] = make_dispute_key(client_id, advice_no, line_no)
    row["event_fp"] = make_event_fp(client_id, trip_id, reason_code)
    row["deduction_id"] = row["dispute_key"]
    return row


# d1's K4 columns (docs/input_format.md), so the same folder also runs through the real adapters.
CSV_COLS = [("client", "client_name_raw"), ("advice_no", "advice_no"), ("line_no", "line_no"),
            ("client_deduction_no", "client_deduction_no"),
            ("deduction_date", "deduction_date"), ("received_date", "received_date"), ("amount", "amount"),
            ("reason", "reason_text"), ("lr_no", None), ("invoice_no", "invoice_ref"),
            ("calc_rate", "rate"), ("calc_unit", "unit"), ("calc_base_amount", "base_amount"),
            ("calc_units", "units_counted"), ("calc_from", "from_ts"), ("calc_to", "to_ts")]


def csv_rows(deds: list[dict], cols=CSV_COLS) -> list[list[str]]:
    out = [[c for c, _ in cols]]
    for d in deds:
        calc = d.get("client_calc") or {}
        line = []
        for _, f in cols:
            if f is None:
                line.append(d["trip_refs"][0])
            elif f in calc:
                line.append(str(calc[f]))
            else:
                line.append(str(d.get(f, "") or ""))
        out.append(line)
    return out


def write_csv(path: Path, rows: list[list[str]]) -> None:
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows(rows)
    path.write_text(buf.getvalue(), encoding="utf-8")


def write_case(spec: dict) -> Path:
    d = HERE / spec["slug"]
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    fixtures = [fixture_client(n) for n in spec.get("fixture_clients", [])] + spec.get("inline_clients", [])
    clients = [f["client"] for f in fixtures]
    agreements = [a for f in fixtures for a in f.get("agreements", [])]
    terms = [t for f in fixtures for t in f.get("terms", [])]
    for patch in spec.get("agreement_patches", []):
        for a in agreements:
            if a["agreement_id"] == patch["agreement_id"]:
                a.update(patch["set"])
    meta = {"id": spec["slug"], "prd_case": spec["prd"], "title": spec["title"],
            "clients": [c["client_id"] for c in clients], "kind": spec.get("kind", "free_check"),
            "settings_overrides": spec.get("settings_overrides", {})}
    for k in ("faults", "kill", "adapter_only", "notes_for_runner", "k4_file"):
        if k in spec:
            meta[k] = spec[k]
    dump = lambda obj: yaml.safe_dump(obj, sort_keys=False, allow_unicode=True, width=120)
    (d / "case.yaml").write_text(dump(meta), encoding="utf-8")
    (d / "clients.yaml").write_text(
        "# Stand-in clients (not real companies). Built from fixtures/clients/.\n" + dump({"clients": clients}),
        encoding="utf-8")
    (d / "terms.yaml").write_text(
        "# Confirmed stand-in terms built from real public clauses (REFERENCE.md section 3). is_stand_in: true.\n"
        + dump({"agreements": agreements, "terms": terms}), encoding="utf-8")
    (d / "trips.json").write_text(json.dumps(spec["trips"], indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (d / "deductions.json").write_text(json.dumps(spec["deductions"], indent=2, ensure_ascii=False) + "\n",
                                       encoding="utf-8")
    if not spec.get("k4_file"):  # the K4 input as the service would receive it
        write_csv(d / "deductions.csv", csv_rows(spec["deductions"]))
    exp = {}
    for key, e in spec["expected"].items():
        full = {"outcome": None, "grounds": [], "primary_reason": None, "amount_disputable": "0",
                "flags": [], "missing_item_contains": None, "max_paid_calls": 0}
        full.update(e)
        exp[key] = full
    (d / "expected.json").write_text(json.dumps(exp, indent=2) + "\n", encoding="utf-8")
    (d / "NOTES.md").write_text(spec["notes"].strip() + "\n", encoding="utf-8")
    cas = d / "cassette"
    cas.mkdir()
    for name, body in spec.get("cassette", {}).items():
        (cas / name).write_text(json.dumps(body, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if not spec.get("cassette"):
        (cas / ".gitkeep").write_text("", encoding="utf-8")
    for name, body in spec.get("variants", {}).items():
        (d / "variants").mkdir(exist_ok=True)
        if isinstance(body, list):
            write_csv(d / "variants" / name, body)
        else:
            (d / "variants" / name).write_text(body, encoding="utf-8")
    for name, body in spec.get("extra_files", {}).items():
        (d / name).write_text(body, encoding="utf-8")
    return d


def main() -> None:
    from tests.cases._specs import SPECS
    for s in SPECS:
        print("wrote", write_case(s).relative_to(REPO))


if __name__ == "__main__":
    main()
