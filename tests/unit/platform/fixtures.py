"""Small store fixtures shared by platform tests."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from pdr.models import (
    Agreement, Client, Deduction, Evidence, LedgerEntry, Observation, SourceRef, Term, Trip, Verdict,
    make_dispute_key, make_event_fp,
)

IST = timezone(timedelta(hours=5, minutes=30))


def deduction(n: int, amount: str = "2000.00") -> Deduction:
    key = make_dispute_key("ACME", "PA/2026/0412", n)
    return Deduction(deduction_id=f"D{n}", dispute_key=key, event_fp=make_event_fp("ACME", f"T{n}", "late_delivery"),
                     client_id="ACME", client_name_raw="ACME FOODS", advice_no="PA/2026/0412", line_no=str(n),
                     deduction_date=date(2026, 11, 10), amount=Decimal(amount), reason_text="late delivery",
                     reason_code="late_delivery", trip_refs=[f"LR-{n}"])


def verdict(d: Deduction, outcome: str = "disputable", vid: str | None = None, **kw) -> Verdict:
    base = dict(verdict_id=vid or f"V-{d.deduction_id}", deduction_id=d.deduction_id, dispute_key=d.dispute_key,
                outcome=outcome, amount_deducted=d.amount, client_id=d.client_id, run_id="r1",
                settings_version="abc123")
    if outcome == "disputable":
        base.update(grounds=["OVER_CAP"], recomputed_penalty=Decimal("500.00"),
                    amount_disputable=d.amount - Decimal("500.00"), evidence_ids=["E-term", "E-rec"],
                    checks_run=["basis", "over_cap"])
    elif outcome == "cannot_verify":
        base.update(primary_reason="TERMS_MISSING", reasons=["TERMS_MISSING"], missing_items=["agreement"])
    base.update(kw)
    return Verdict(**base)


def fill(store, n: int = 2) -> list[Deduction]:
    store.put(Client(client_id="ACME", legal_name="Acme Foods Pvt Ltd"))
    store.put(Agreement(agreement_id="A1", client_id="ACME", doc_type="master", title="Transport Agreement",
                        file_ref="ta.pdf", effective_from=date(2026, 4, 1)))
    store.put(Term(term_id="T-cap", agreement_id="A1", client_id="ACME", term_type="cap",
                   params={"value": "10", "kind": "pct", "base": "trip_cost", "scope": "per_trip"}, page=7,
                   clause_ref="14.2", verbatim="capped at 10% of trip cost", effective_from=date(2026, 4, 1),
                   status="confirmed"))
    store.put(Evidence(evidence_id="E-term", kind="contract_term", term_id="T-cap", link="ta.pdf#page=7"))
    store.put(Evidence(evidence_id="E-rec", kind="internal_record", record_type="trip", record_id="T1",
                       field="freight", value=Decimal("5000.00"), ref=SourceRef(file="trips.csv", row=4),
                       link="trips.csv#row=4"))
    ds = [deduction(i + 1) for i in range(n)]
    for d in ds:
        store.put(d)
    return ds
