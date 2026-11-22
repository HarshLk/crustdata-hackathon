"""Small inline world for lane-B engine tests: one client, one agreement, one Pune-Kolhapur trip."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from pdr.contracts import RunContext
from pdr.models import (
    Agreement, Assertion, Client, Deduction, Halt, NoticeRecord, Observation, SourceRef, Term, Trip,
    make_dispute_key, make_event_fp,
)
from pdr.settings import load_settings, settings_from_dict
from pdr.store_mem import MemStore

ROOT = Path(__file__).resolve().parents[3]
IST = timezone(timedelta(hours=5, minutes=30))


def ist(d, h=0, m=0):
    return datetime(2026, 11, d, h, m, tzinfo=IST)


def settings(**overrides):
    s = load_settings(ROOT / "settings.yaml")
    if not overrides:
        return s
    data = s.model_dump(mode="json")
    for dotted, v in overrides.items():
        cur = data
        parts = dotted.split(".")
        for p in parts[:-1]:
            cur = cur[p]
        cur[parts[-1]] = v
    return settings_from_dict(data)


_RUN = [0]


def ctx(store=None, run_id=None, **overrides):
    if run_id is None:
        _RUN[0] += 1
        run_id = f"r-test-{_RUN[0]}"
    return RunContext(run_id=run_id, dispute_key=None, settings=settings(**overrides), store=store or MemStore(),
                      deadline=datetime.now(timezone.utc) + timedelta(hours=1))


def term(tid, ttype, params, page=7, clause="14.1", verbatim="...", status="confirmed", agreement="A1", client="ACME"):
    return Term(term_id=tid, agreement_id=agreement, client_id=client, term_type=ttype, params=params, page=page,
                clause_ref=clause, verbatim=verbatim, effective_from=date(2026, 4, 1), status=status)


def penalty_term(client="ACME", agreement="A1", tid="T-PEN"):
    return term(tid, "penalty_rule", {"event": "late_delivery", "rate": "2000", "unit": "flat_per_day", "base": "none",
                                      "part_unit_counts_as_full": True, "day_count": "calendar"},
                clause="12.1", verbatim="Rs 2000 per day of delay", client=client, agreement=agreement)


def fm_named(client="ACME", agreement="A1", tid="T-FM"):
    return term(tid, "force_majeure", {"events_named": ["blockade", "agitation"]},
                verbatim="road blockades and agitations", client=client, agreement=agreement)


def fm_excludes(client="ACME", agreement="A1"):
    return term("T-FM", "force_majeure", {"events_named": ["flood"], "events_excluded": ["strike", "agitation", "blockade"]},
                verbatim="strikes and agitations are excluded", client=client, agreement=agreement)


def fm_general(client="ACME", agreement="A1"):
    return term("T-FM", "force_majeure", {"general_words": ["civil commotion"]}, verbatim="civil commotion",
                client=client, agreement=agreement)


def notice_term(hours=240, client="ACME", agreement="A1"):
    return term("T-NOT", "notice", {"deadline_hours": hours, "applies_to": "force_majeure"}, clause="14.2",
                verbatim=f"notice within {hours} hours", client=client, agreement=agreement)


REF = SourceRef(file="gps_2026-11.csv", row=412)


def trip(tid="TRIP-1", lr="LR-1001", client="ACME", halt=True, notice=True, assertion=True, places=None):
    return Trip(
        trip_id=tid, client_id=client, lr_no=lr, origin="Pune", destination="Kolhapur", lane="PUN-KOP",
        route_places=places if places is not None else ["Pune", "Satara", "Kini", "Kolhapur"],
        route_states=["Maharashtra"], vehicle_no="MH12AB1234",
        gate_out_at=[Observation(value=ist(4, 8), source="client_gate_log", ref=SourceRef(file="gate.csv", row=1))],
        delivery_due_at=ist(5, 18),
        arrival_at=[Observation(value=ist(6, 12), source="consignee_gate_log", ref=SourceRef(file="gate.csv", row=2))],
        freight=Decimal("25000"),
        halts=[Halt(start=ist(5, 6, 10), end=ist(5, 21, 40), place="Kini toll plaza", source="gps", ref=REF)] if halt else [],
        notices=[NoticeRecord(sent_at=ist(5, 10), channel="email", addressee="client logistics",
                              ref=SourceRef(file="mail.eml"))] if notice else [],
        assertions=[Assertion(kind="ops_note", event_type="blockade", text="Truck stuck at Kini toll blockade",
                              at=ist(5, 7), place="Kini toll plaza", ref=SourceRef(file="ops.csv", row=3))] if assertion else [],
    )


def deduction(advice="PA-1", line=1, client="ACME", name="Acme Foods Pvt Ltd", refs=("LR-1001",), amount="2000",
              reason="late delivery", code="late_delivery"):
    return Deduction(deduction_id=f"D-{advice}-{line}", dispute_key=make_dispute_key(client, advice, line),
                     event_fp=make_event_fp(client, refs[0] if refs else "", code), client_id=client,
                     client_name_raw=name, advice_no=advice, line_no=str(line), deduction_date=date(2026, 11, 10),
                     amount=Decimal(amount), reason_text=reason, reason_code=code, trip_refs=list(refs))


def world(store=None, terms=None, trips=None, deds=None, clients=None, agreements=None):
    """Put a world into a store; returns (store, dispute_keys)."""
    store = store or MemStore()
    for c in clients or [Client(client_id="ACME", legal_name="Acme Foods Pvt Ltd", aliases=["ACME FOODS"])]:
        store.put(c)
    for a in agreements or [Agreement(agreement_id="A1", client_id="ACME", doc_type="master", title="Transport Agreement",
                                      file_ref="ta.pdf", effective_from=date(2026, 4, 1), is_stand_in=True)]:
        store.put(a)
    for t in terms if terms is not None else [penalty_term(), fm_named(), notice_term()]:
        store.put(t)
    kini_snapshot(store)
    for t in trips if trips is not None else [trip()]:
        store.put(t)
    keys = []
    for d in deds if deds is not None else [deduction()]:
        store.put(d)
        keys.append(d.dispute_key)
    return store, keys


def stub_free_checks():
    """Register no-op stage 3/4 checks when lane B's real ones are not loaded; returns ids to remove."""
    from pdr.codes import CheckStatus, Stage
    from pdr.engine import registry
    from pdr.models import CheckResult
    import sys
    import types
    added = offline()
    for stage, mod in ((Stage.terms_checked, "pdr.engine.checks_terms"), (Stage.records_checked, "pdr.engine.checks_records")):
        if mod in sys.modules and registry.checks_for(stage):
            continue
        if mod not in sys.modules:
            sys.modules[mod] = types.ModuleType(mod)
            added.append("module:" + mod)
        cid = f"stub_{stage.value}"

        def fn(c, _s=stage, _id=cid):
            return CheckResult(check_id=_id, stage=_s, status=CheckStatus.NOT_APPLICABLE, note="test stub")
        registry.check(stage=stage, check_id=cid)(fn)
        added.append(cid)
    return added


def offline() -> list[str]:
    """Register a strict FakeTransport for every provider so no test can reach a live API."""
    try:
        from pdr.broker import TRANSPORTS, register_transport
        from tests.fakes.transport import FakeTransport
    except ImportError:
        return []
    out = []
    for name in ("crustdata", "llm", "web"):
        if name not in TRANSPORTS:
            register_transport(name, FakeTransport())
            out.append("transport:" + name)
    return out


def unregister(ids):
    import sys
    from pdr.engine import registry
    for i in ids:
        if i.startswith("transport:"):
            from pdr.broker import register_transport
            register_transport(i[10:], None)
        if i.startswith("module:"):
            sys.modules.pop(i[7:], None)
        registry._REGISTRY.pop(i, None)


class FakeFinder:
    """Stands in for pdr.evidence.find_evidence; counts calls."""

    def __init__(self, result_fn):
        self.calls = []
        self.result_fn = result_fn

    def __call__(self, req, ctx):
        self.calls.append(req)
        return self.result_fn(req)


KINI_PASSAGE = "Over 1,500 trucks stranded near Kini toll plaza as the blockade halted traffic on NH-48"
KINI_TEXT = "Kolhapur, 6 Nov. " + KINI_PASSAGE + ". Police cleared the highway by evening."


def kini_snapshot(store):
    """Store the snapshot the Kini evidence cites, so the citation audit can re-verify it."""
    from pdr.models import sha256_hex
    store.snapshot_put(sha256_hex(KINI_TEXT), "https://example.org/kini-blockade", KINI_TEXT, None)
    return store


def kini_evidence(eid="ext-kini-1", place="Kini toll plaza", stance="supports"):
    from datetime import date as _d
    from pdr.models import Evidence, sha256_hex
    return Evidence(evidence_id=eid, kind="external_news", url="https://example.org/kini-blockade",
                    passage=KINI_PASSAGE,
                    published_at=ist(6, 9), event_from=_d(2026, 11, 3), event_to=_d(2026, 11, 6), place_named=place,
                    retrieved_at=ist(22, 9), fetch_status="ok", content_hash=sha256_hex(KINI_TEXT), stance=stance,
                    link="https://example.org/kini-blockade#:~:text=Over")
