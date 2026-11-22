"""Hand-built verdicts for lane C2 tests: FORCE_MAJEURE, WRONG_BASE+OVER_CAP, SOURCES_CONFLICT."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from pdr.codes import Flag, Ground, Outcome, Reason, ReasonCode, TsSource
from pdr.contracts import RunContext
from pdr.models import (Agreement, Assertion, Citation, Client, Deduction, Evidence, Halt, NoticeRecord, Observation,
                        RecomputeLine, SourceRef, Term, Trip, Verdict, make_dispute_key, make_event_fp)
from pdr.settings import load_settings, settings_from_dict
from pdr.store_mem import MemStore

IST = timezone(timedelta(hours=5, minutes=30))
ROOT = Path(__file__).resolve().parents[3]
CANARY_NAME = "Zorawar Canarysingh"
CANARY_PHONE = "+91 98765 43210"
FM_CLAUSE = "Neither party shall be liable for delay caused by road blockades and agitations, bandhs or floods."
PASSAGE = "Over 1,500 trucks were stranded on a 120 km stretch of NH-48 near Kini toll plaza on November 5."
CAP_CLAUSE = "The penalty shall not exceed 15% of the trip cost per vehicle."
RATE_CLAUSE = "Penalty of 1% of trip cost per vehicle per day of delay."


def settings(tmp_path: Path | None = None, **approvals):
    s = load_settings(ROOT / "settings.yaml")
    data = s.model_dump(mode="json")
    data["approvals"].update(approvals)
    if tmp_path is not None:
        data["adapters"]["k7"]["path"] = str(tmp_path / "outbox")
    return settings_from_dict(data)


def ctx(store=None, tmp_path=None, **approvals) -> RunContext:
    return RunContext(run_id="r-test", dispute_key=None, settings=settings(tmp_path, **approvals),
                      store=store if store is not None else MemStore(),
                      deadline=datetime.now(timezone.utc) + timedelta(hours=1))


def _ts(day: int, h: int, m: int = 0) -> datetime:
    return datetime(2026, 11, day, h, m, tzinfo=IST)


def base(store, *, client_id="ACME", advice="PA-2026-0412", line="3", amount="2000.00", reason_text="Late delivery 1 day",
         stand_in=True, trip_id="TRIP-0091"):
    store.put(Client(client_id=client_id, legal_name="Acme Cement Limited", approvers={"kam": "Asha Rao"}))
    store.put(Agreement(agreement_id=f"{client_id}-TA", client_id=client_id, doc_type="transport_agreement",
                        title="Transport Agreement", file_ref="agreements/acme_ta.pdf",
                        effective_from=date(2026, 4, 1), is_stand_in=stand_in))
    trip = Trip(trip_id=trip_id, client_id=client_id, origin="Pune", destination="Kolhapur", lane="PUN-KOP",
                route_places=["Pune", "Satara", "Kini"], vehicle_no="MH12AB1234", driver_ref="d-1a2b3c4d5e6f",
                gate_out_at=[Observation(value=_ts(4, 8), source=TsSource.client_gate_log, ref=SourceRef(file="gate.csv", row=7))],
                delivery_due_at=_ts(5, 18),
                arrival_at=[Observation(value=_ts(6, 11), source=TsSource.pod_signed, ref=SourceRef(file="pod.csv", row=3)),
                            Observation(value=_ts(6, 10, 40), source=TsSource.gps, ref=SourceRef(file="gps.csv", row=420))],
                freight=Decimal("40000"),
                halts=[Halt(start=_ts(5, 6, 10), end=_ts(5, 21, 40), place="Kini toll plaza", source=TsSource.gps,
                            ref=SourceRef(file="gps_2026-11.csv", row=412)),
                       Halt(start=datetime(2026, 10, 1, 9, tzinfo=IST), end=datetime(2026, 10, 1, 10, tzinfo=IST),
                            place="Far away depot", source=TsSource.gps, ref=SourceRef(file="gps_2026-10.csv", row=5))],
                notices=[NoticeRecord(sent_at=_ts(5, 9), channel="email", addressee="logistics@acme.example",
                                      ref=SourceRef(file="mail.mbox", row=1))],
                assertions=[Assertion(kind="driver_report", text=f"{CANARY_NAME} ({CANARY_PHONE}) says road blocked at Kini",
                                      at=_ts(5, 7), place="Kini", ref=SourceRef(file="ops.csv", row=9)),
                            Assertion(kind="ops_note", text=f"Called driver on {CANARY_PHONE}; blockade confirmed",
                                      at=_ts(5, 8), place="Kini", ref=SourceRef(file="ops.csv", row=10))])
    store.put(trip)
    key = make_dispute_key(client_id, advice, line)
    ded = Deduction(deduction_id=f"D-{advice}-{line}", dispute_key=key, event_fp=make_event_fp(client_id, trip_id, "late_delivery"),
                    client_id=client_id, client_name_raw="ACME CEMENT LTD", advice_no=advice, line_no=line,
                    deduction_date=date(2026, 11, 15), amount=Decimal(amount), reason_text=reason_text,
                    reason_code=ReasonCode.late_delivery, trip_refs=[trip_id])
    store.put(ded)
    return ded


def term(store, tid, ttype, params, clause, verbatim, page=7, client_id="ACME"):
    t = Term(term_id=tid, agreement_id=f"{client_id}-TA", client_id=client_id, term_type=ttype, params=params, page=page,
             clause_ref=clause, verbatim=verbatim, effective_from=date(2026, 4, 1), status="confirmed")
    store.put(t)
    return t


def fm_verdict(store, *, amount="2000.00", flags=(), reason_text="Late delivery 1 day", advice="PA-2026-0412"):
    """FORCE_MAJEURE: one external source, one contract term, one GPS record."""
    ded = base(store, amount=amount, reason_text=reason_text, advice=advice)
    t = term(store, "T-FM", "force_majeure", {"events_named": ["blockade", "agitation"]}, "14.1", FM_CLAUSE)
    store.put(Evidence(evidence_id="E-GPS", kind="internal_record", record_type="gps_halt", record_id="TRIP-0091#halt1",
                       field="start/end", value="2026-11-05T06:10+05:30/2026-11-05T21:40+05:30", timestamp=_ts(5, 6, 10),
                       ref=SourceRef(file="gps_2026-11.csv", row=412), link="#rec-E-GPS", client_id="ACME"))
    store.put(Evidence(evidence_id="E-NEWS", kind="external_news", url="https://www.outlookindia.com/kini-blockade",
                       passage=PASSAGE, published_at=datetime(2026, 11, 6, 9, 0, tzinfo=IST),
                       event_from=date(2026, 11, 4), event_to=date(2026, 11, 6), place_named="Kini toll plaza",
                       retrieved_at=datetime(2026, 11, 22, 9, 41, tzinfo=IST), retrieved_via="api", fetch_status="ok",
                       content_hash="abc123snap", stance="supports",
                       link="https://www.outlookindia.com/kini-blockade#:~:text=Over%201%2C500%20trucks,November%205", audit="passed"))
    v = Verdict(verdict_id=f"V-{ded.dispute_key}-1", deduction_id=ded.deduction_id, dispute_key=ded.dispute_key,
                client_id="ACME", outcome=Outcome.disputable, grounds=[Ground.FORCE_MAJEURE],
                flags=[Flag.STAND_IN_TERMS, *flags], amount_deducted=ded.amount, recomputed_penalty=Decimal("0.00"),
                amount_disputable=ded.amount,
                recomputation=[RecomputeLine(step="excused", description="Delay excused by force majeure (clause 14.1)",
                                             value=Decimal("0.00"))],
                terms_applied=[t.term_id], evidence_ids=["E-GPS", "E-NEWS"],
                explanation="A blockade on NH-48 near Kini covered by clause 14.1 explains the one-day delay.",
                what_would_change=["A report that the blockade ended before 5 Nov"], checks_run=["fm_wording", "notice", "evidence"],
                settings_version="test")
    store.put(v)
    return v


def cap_verdict(store, *, amount="9000.00"):
    """WRONG_BASE + OVER_CAP on a 40,000 trip: rate on trip cost 1%/day x 2 days = 800, cap 6,000."""
    ded = base(store, amount=amount, advice="PA-2026-0500", line="1")
    t1 = term(store, "T-RATE", "penalty_rule", {"event": "late_delivery", "rate": "1", "unit": "pct_per_vehicle_day", "base": "trip_cost"},
              "9.2", RATE_CLAUSE, page=4)
    t2 = term(store, "T-CAP", "cap", {"value": "15", "kind": "pct", "base": "trip_cost"}, "9.3", CAP_CLAUSE, page=4)
    lines = [RecomputeLine(step="base", description="Trip cost per clause 9.2 (not the invoice value)", value=Decimal("40000.00")),
             RecomputeLine(step="rate", description="1% per vehicle-day x 2 days", value=Decimal("800.00")),
             RecomputeLine(step="cap", description="Cap 15% of trip cost per clause 9.3", value=Decimal("6000.00")),
             RecomputeLine(step="result", description="Penalty due (lower of rate result and cap)", value=Decimal("800.00"))]
    v = Verdict(verdict_id=f"V-{ded.dispute_key}-1", deduction_id=ded.deduction_id, dispute_key=ded.dispute_key, client_id="ACME",
                outcome=Outcome.disputable, grounds=[Ground.WRONG_BASE, Ground.OVER_CAP], amount_deducted=ded.amount,
                recomputed_penalty=Decimal("800.00"), amount_disputable=ded.amount - Decimal("800.00"), recomputation=lines,
                terms_applied=[t1.term_id, t2.term_id], explanation="Rate applied to invoice value instead of trip cost.",
                checks_run=["wrong_base", "over_cap"], settings_version="test")
    store.put(v)
    return v


def conflict_verdict(store):
    """SOURCES_CONFLICT: one report supports, one contradicts."""
    ded = base(store, advice="PA-2026-0600", line="2")
    t = term(store, "T-FM", "force_majeure", {"events_named": ["bandh"]}, "14.1", FM_CLAUSE)
    store.put(Evidence(evidence_id="E-OP", kind="external_news", url="https://www.orissapost.com/bandh", passage="Trucks were stopped in several districts.",
                       published_at=datetime(2026, 10, 8, 20, tzinfo=IST), retrieved_at=datetime(2026, 11, 22, 10, tzinfo=IST),
                       stance="supports", content_hash="snapop", link="https://www.orissapost.com/bandh#:~:text=Trucks%20were%20stopped"))
    store.put(Evidence(evidence_id="E-POL", kind="external_official", url="https://police.example.gov.in/statement",
                       passage="The Bhubaneswar-Cuttack national highways were unaffected.",
                       published_at=datetime(2026, 10, 8, 18, tzinfo=IST), retrieved_at=datetime(2026, 11, 22, 10, 5, tzinfo=IST),
                       stance="contradicts", content_hash="snappol", link="https://police.example.gov.in/statement#:~:text=unaffected"))
    v = Verdict(verdict_id=f"V-{ded.dispute_key}-1", deduction_id=ded.deduction_id, dispute_key=ded.dispute_key, client_id="ACME",
                outcome=Outcome.cannot_verify, primary_reason=Reason.SOURCES_CONFLICT, reasons=[Reason.SOURCES_CONFLICT],
                amount_deducted=ded.amount, terms_applied=[t.term_id], evidence_ids=["E-OP", "E-POL"],
                missing_items=["a person to weigh the police statement against the report"],
                explanation="Reports conflict on whether the highway was affected.", settings_version="test")
    store.put(v)
    return v
