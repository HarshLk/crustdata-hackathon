"""Reader: bundle loads K1->K2->K3->K4 into the store; instruction classification; unsupported never guessed."""
from __future__ import annotations

import yaml

from pdr.adapters import reader
from pdr.adapters.k6_runner import run_request
from pdr.codes import RequestType
from pdr.models import Agreement, Client, Deduction, Term, Trip


def _bundle(tmp_path):
    (tmp_path / "clients.yaml").write_text(yaml.safe_dump({"clients": [
        {"client_id": "ACME", "legal_name": "Acme Foods Private Limited", "aliases": ["Acme Foods Pvt Ltd"]}]}))
    (tmp_path / "terms.yaml").write_text(yaml.safe_dump({
        "agreements": [{"agreement_id": "AG1", "client_id": "ACME", "doc_type": "sla", "title": "SLA",
                        "file_ref": "agreements/sla.txt", "effective_from": "2026-04-01"}],
        "terms": [{"term_id": "T-CAP", "agreement_id": "AG1", "client_id": "ACME", "term_type": "cap",
                   "params": {"value": "15", "kind": "pct", "base": "trip_cost"}, "page": 1, "clause_ref": "8",
                   "verbatim": "maximum of 15% of the trip cost", "effective_from": "2026-04-01", "status": "confirmed"}]}))
    (tmp_path / "trips.csv").write_text("trip_id,lr_no,origin,destination,lane,vehicle_no,freight\n"
                                        "T1,LR-1,Pune,Nagpur,PUN-NAG,MH12AB1,50000\n")
    (tmp_path / "deductions.csv").write_text("client,advice_no,line_no,deduction_date,amount,reason,lr_no\n"
                                             "Acme Foods Pvt Ltd,A1,1,2026-10-01,1000,late delivery,LR-1\n"
                                             "Acme Foods Pvt Ltd,A1,2,2026-10-01,oops,late delivery,LR-1\n")


def test_bundle_loads_everything(ctx, tmp_path):
    _bundle(tmp_path)
    deds, summary = reader.load_request_ex(tmp_path, ctx)
    s = ctx.store
    assert len(s.find(Client)) == 1 and len(s.find(Agreement)) == 1 and len(s.find(Term)) == 1
    assert len(s.find(Trip)) == 1 and [d.dispute_key for d in s.find(Deduction)] == ["ACME|A1|1"]
    assert deds[0].client_id == "ACME" and summary["problems"]
    again = reader.load_request(tmp_path, ctx)
    assert len(again) == 1 and len(s.find(Deduction)) == 1  # resubmission: nothing new


def test_read_request_types(ctx, tmp_path):
    _bundle(tmp_path)
    assert reader.read_request(tmp_path, ctx).request_type == RequestType.check_deductions
    r = reader.read_request("find me three carriers for Pune–Nagpur", ctx)
    assert r.request_type == RequestType.unsupported
    assert "checks penalty deductions" in reader.describe(r)
    assert reader.read_request("Please check the deductions in this advice", ctx).request_type == RequestType.check_deductions
    assert reader.read_request("Take in the new agreement for Acme", ctx).request_type == RequestType.intake_agreement
    assert reader.read_request("status of disputes", ctx).request_type == RequestType.status
    reader.load_request(tmp_path, ctx)
    r = reader.read_request("Check Acme Foods Private Limited deductions for October 2026", ctx)
    assert r.client_hint == "ACME" and str(r.period[1]) == "2026-10-31"
    (tmp_path / "request.txt").write_text("write me a poem about trucks")
    assert reader.read_request(tmp_path, ctx).request_type == RequestType.unsupported


def test_pasted_email_is_a_check_request(ctx):
    email = "Payment Advice No: PA1  Line: 1\nDate: 01-10-2026\nAmount: Rs 500\nReason: late delivery\nLR No: LR-9\n"
    assert reader.read_request(email, ctx).request_type == RequestType.check_deductions


def test_k6_unsupported_runs_nothing(ctx):
    out = run_request("find me three carriers for Pune–Nagpur", ctx)
    assert out["request_type"] == "unsupported" and out["dispute_keys"] == []
