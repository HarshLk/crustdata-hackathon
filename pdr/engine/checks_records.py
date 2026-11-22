"""Stage 4 (records_checked): delay at all, start point, client-caused intervals, regulatory hold."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from pdr.codes import CheckStatus as CS, Ground, Reason, ReasonCode, Stage, TsSource as S
from pdr.contracts import CheckContext
from pdr.engine import penalty as P
from pdr.engine import timeline as TL
from pdr.engine.calendar import IST
from pdr.engine.checks_terms import keep, res, tdate, term_cits
from pdr.engine.registry import check
from pdr.models import Evidence, Hold, NoticeRecord, Trip, sha256_hex

ST = Stage.records_checked
CLIENT_SIDE = {S.client_gate_log, S.weighbridge, S.consignee_gate_log, S.pod_signed}


def _fmt(dt: Optional[datetime]) -> str:
    return dt.astimezone(IST).strftime("%d %b %Y %H:%M IST") if dt else "?"


def timeline_of(c: CheckContext) -> Optional[TL.ResolvedTimeline]:
    """Resolve once per check context."""
    if c.trip is None:
        return None
    cached = getattr(c, "_b2_timeline", None)
    if cached is None or cached.trip is not c.trip:
        cached = TL.resolve(c.trip, c.ctx.settings)
        try:
            c._b2_timeline = cached
        except Exception:  # noqa: BLE001
            pass
    return cached


def span_events(c: CheckContext) -> tuple[Optional[str], Optional[str], list]:
    """(start event, end event, definition terms used) for the deduction's penalised event."""
    frm, to, defn = P.DEFAULT_SPAN.get(c.deduction.reason_code, (None, None, None))
    d = tdate(c)
    rules = P.rules_for(c.terms, c.deduction.reason_code, d)
    if rules:
        p = rules[0].typed_params()
        frm = TL.norm_event(p.measured_from) or frm
        to = TL.norm_event(p.measured_to) or to
    defs = [t for t in c.terms if t.term_type == "definition" and P.in_force(t, d) and t.typed_params().name == defn]
    if defs and TL.norm_event(defs[0].typed_params().event):
        to = TL.norm_event(defs[0].typed_params().event)
    return frm, to, defs


def conflict_result(c: CheckContext, check_id: str, tl: TL.ResolvedTimeline, event: str) -> "CheckResult":
    return res(check_id, CS.INCOMPLETE, ST, reason=Reason.RECORDS_CONFLICT, missing=tl.conflict_text(event),
               cits=keep(c, tl.evidence(event)), deps=[event], note="records disagree beyond the allowance; not a pick")


def notice_evidence(trip: Trip, n: NoticeRecord) -> Evidence:
    eid = "rec-" + sha256_hex(f"{trip.trip_id}|notice|{n.sent_at.isoformat()}|{n.ref.file}|{n.ref.row}")[:16]
    return Evidence(evidence_id=eid, kind="internal_record", client_id=trip.client_id, record_type="notice",
                    record_id=trip.trip_id, field="sent_at", value=f"{n.channel} to {n.addressee}: {n.subject or ''}",
                    timestamp=n.sent_at, ref=n.ref, link=f"#ev-{eid}")


def hold_evidence(trip: Trip, h: Hold) -> Evidence:
    eid = "rec-" + sha256_hex(f"{trip.trip_id}|hold|{h.held_from.isoformat()}|{h.authority}")[:16]
    return Evidence(evidence_id=eid, kind="internal_record", client_id=trip.client_id, record_type="detention_memo",
                    record_id=trip.trip_id, field="holds",
                    value=f"{h.authority} at {h.place}: {_fmt(h.held_from)} to {_fmt(h.released_at)}",
                    timestamp=h.held_from, ref=h.memo_ref, link=f"#ev-{eid}")


# ----------------------------------------------------------------------------- delay at all

@check(stage=ST, needs_events=["delivery_due", "arrival", "unloaded", "placement_required", "reporting"],
       settings=["records"])
def delay_at_all(c: CheckContext) -> "CheckResult":
    """Was there a delay against the agreement's own definition of delivery (or placement)?"""
    frm, to, defs = span_events(c)
    tl = timeline_of(c)
    if frm is None or to is None:
        return res("delay_at_all", CS.NOT_APPLICABLE, ST, note="not a delay penalty")
    if tl is None:
        return res("delay_at_all", CS.INCOMPLETE, ST, reason=Reason.TRIP_NOT_FOUND, missing="trip record")
    if to in tl.conflicted:
        return conflict_result(c, "delay_at_all", tl, to)
    due, done = tl.value(frm), tl.value(to)
    if due is None:
        return res("delay_at_all", CS.INCOMPLETE, ST, reason=Reason.EVIDENCE_PARTIAL, deps=[frm],
                   missing=f"agreed {frm.replace('_', ' ')} time for the trip")
    if done is None:
        return res("delay_at_all", CS.INCOMPLETE, ST, reason=Reason.EVIDENCE_PARTIAL, deps=[to],
                   missing=f"record of {to} (e.g. consignee gate log, signed POD or GPS)")
    if done > due:
        return res("delay_at_all", CS.NO_GROUND, ST, deps=[frm, to],
                   note=f"{to} {_fmt(done)} is after {frm.replace('_', ' ')} {_fmt(due)}")
    cits = keep(c, tl.evidence(frm) + tl.evidence(to)) + term_cits(c, defs)
    return res("delay_at_all", CS.GROUND, ST, ground=Ground.NO_DELAY, cits=cits, deps=[frm, to] + [t.term_id for t in defs],
               corr=P.correction("void", ground="NO_DELAY", citations=cits,
                                 label=f"No delay: {to} {_fmt(done)} on or before {frm.replace('_', ' ')} {_fmt(due)}"),
               note="records show no delay against the agreement's definition")


# ----------------------------------------------------------------------------- start point

@check(stage=ST, needs_events=["delivery_due", "arrival", "placement_required", "reporting"], settings=["records"])
def start_point(c: CheckContext) -> list:
    """Client's counted start or end differs from the clause's defined event (e.g. inspection pass vs gate-in)."""
    cc = c.deduction.client_calc
    frm, to, defs = span_events(c)
    tl = timeline_of(c)
    if cc is None or (cc.from_ts is None and cc.to_ts is None) or frm is None or tl is None:
        return [res("start_point", CS.NOT_APPLICABLE, ST, note="client stated no timestamps to test")]
    tol = timedelta(minutes=float(c.ctx.settings.records.conflict_allowance_minutes))
    out = []
    for which, client_ts, ev in (("from", cc.from_ts, frm), ("to", cc.to_ts, to)):
        cid = "start_point" if which == "from" else "end_point"
        if client_ts is None:
            continue
        if ev in tl.conflicted:
            out.append(conflict_result(c, cid, tl, ev))
            continue
        rec = tl.value(ev)
        if rec is None:
            out.append(res(cid, CS.NO_GROUND, ST, deps=[ev], note=f"no record of {ev} to test the client's time against"))
            continue
        wrong = (client_ts < rec - tol) if which == "from" else (client_ts > rec + tol)
        if not wrong:
            out.append(res(cid, CS.NO_GROUND, ST, deps=[ev], note=f"client's {which} time matches the {ev} record"))
            continue
        cits = keep(c, tl.evidence(ev)) + term_cits(c, defs)
        out.append(res(cid, CS.GROUND, ST, ground=Ground.WRONG_START_POINT, cits=cits,
                       deps=[ev] + [t.term_id for t in defs],
                       corr=P.correction("timestamp", which=which, at=rec, citations=cits,
                                         label=f"Delay counted {which} {_fmt(client_ts)}; the clause's event ({ev}) "
                                               f"is {_fmt(rec)}"),
                       note=f"client counted {which} {_fmt(client_ts)}; {ev} per records {_fmt(rec)}"))
    return out or [res("start_point", CS.NOT_APPLICABLE, ST, note="nothing to test")]


# ----------------------------------------------------------------------------- client-caused

@check(stage=ST, needs_terms=["buyer_delay"], needs_events=["reporting", "gate_out", "arrival", "unloaded", "indent"],
       settings=["records"])
def client_caused(c: CheckContext) -> list:
    """Loading beyond free hours, unloading wait, short-notice indent: only if the buyer bears them."""
    d = tdate(c)
    bds = [t for t in c.terms if t.term_type == "buyer_delay" and P.in_force(t, d) and t.typed_params().buyer_bears]
    if not bds:
        return [res("client_caused", CS.NOT_APPLICABLE, ST, note="no clause makes the buyer bear delays it causes")]
    tl = timeline_of(c)
    if tl is None:
        return [res("client_caused", CS.NOT_APPLICABLE, ST, note="no trip")]
    term = bds[0]
    bd = term.typed_params()
    frm, to, _ = span_events(c)
    rc = c.deduction.reason_code
    out = []
    if bd.free_loading_hours is not None and rc == ReasonCode.late_delivery:
        out.append(_wait(c, tl, term, "client_caused_loading", "reporting", "gate_out", bd.free_loading_hours,
                         "loading", {S.client_gate_log}, {S.client_gate_log, S.weighbridge}))
    if bd.free_unloading_hours is not None and to == "unloaded":
        out.append(_wait(c, tl, term, "client_caused_unloading", "arrival", "unloaded", bd.free_unloading_hours,
                         "unloading", {S.consignee_gate_log}, {S.pod_signed, S.consignee_gate_log}))
    if bd.min_indent_notice_hours is not None and rc in (ReasonCode.late_placement, ReasonCode.late_reporting):
        out.append(_indent(c, tl, term, bd.min_indent_notice_hours))
    return [r for r in out if r is not None] or [
        res("client_caused", CS.NO_GROUND, ST, deps=[term.term_id], note="no client-caused wait applies to this penalty")]


def _wait(c, tl, term, cid, a_ev, b_ev, free_h, what, a_ok, b_ok):
    for ev in (a_ev, b_ev):
        if ev in tl.conflicted:
            return conflict_result(c, cid, tl, ev)
    a, b = tl.chosen.get(a_ev), tl.chosen.get(b_ev)
    if a is None or b is None:
        return res(cid, CS.NO_GROUND, ST, deps=[term.term_id], note=f"no {a_ev}/{b_ev} records to measure {what}")
    free = timedelta(hours=float(free_h))
    took = b.value - a.value
    if took <= free:
        return res(cid, CS.NO_GROUND, ST, deps=[a_ev, b_ev, term.term_id], note=f"{what} took {took}, within {free}")
    if a.source not in a_ok or b.source not in b_ok:
        return res(cid, CS.INCOMPLETE, ST, reason=Reason.EVIDENCE_PARTIAL, deps=[a_ev, b_ev, term.term_id],
                   cits=keep(c, tl.evidence(a_ev) + tl.evidence(b_ev)),
                   missing=f"client or consignee record of {a_ev} and {b_ev} times (our records show {what} took {took})")
    start, end = a.value + free, b.value
    cits = term_cits(c, [term]) + keep(c, tl.evidence(a_ev) + tl.evidence(b_ev))
    return res(cid, CS.GROUND, ST, ground=Ground.CLIENT_CAUSED, cits=cits, deps=[a_ev, b_ev, term.term_id],
               corr=P.correction("excused_interval", start=start, end=end, citations=cits,
                                 label=f"{what} {took} against {free_h} h agreed ({_fmt(start)} to {_fmt(end)})"),
               note=f"{what} took {took}; agreed {free_h} h; buyer bears the excess {end - start}")


def _indent(c, tl, term, min_h):
    if "indent" in tl.conflicted:
        return conflict_result(c, "client_caused_indent", tl, "indent")
    ind, req = tl.value("indent"), tl.value("placement_required")
    if ind is None or req is None:
        return res("client_caused_indent", CS.NO_GROUND, ST, deps=[term.term_id], note="indent or placement time not held")
    need = ind + timedelta(hours=float(min_h))
    if req >= need:
        return res("client_caused_indent", CS.NO_GROUND, ST, deps=["indent", term.term_id], note="indent notice sufficient")
    cits = term_cits(c, [term]) + keep(c, tl.evidence("indent") + tl.evidence("placement_required"))
    return res("client_caused_indent", CS.GROUND, ST, ground=Ground.CLIENT_CAUSED, cits=cits, deps=["indent", term.term_id],
               corr=P.correction("excused_interval", start=req, end=need, citations=cits,
                                 label=f"indent given {req - ind} before placement; {min_h} h notice required"),
               note=f"short-notice indent: {req - ind} given, {min_h} h required")


# ----------------------------------------------------------------------------- regulatory hold

@check(stage=ST, needs_terms=["hold_rule"])
def regulatory_hold(c: CheckContext) -> list:
    """Detention by an authority, proven by its memo, excused if the clause covers government detention."""
    trip = c.trip
    if trip is None or not trip.holds:
        return [res("regulatory_hold", CS.NOT_APPLICABLE, ST, note="no detention or seizure on record")]
    d = tdate(c)
    rules = [t for t in c.terms if t.term_type == "hold_rule" and P.in_force(t, d)]
    out = []
    for i, h in enumerate(trip.holds):
        cid = "regulatory_hold" if i == 0 else f"regulatory_hold_{i + 1}"
        if h.memo_ref is None:
            out.append(res(cid, CS.INCOMPLETE, ST, reason=Reason.EVIDENCE_PARTIAL,
                           missing=f"detention memo from {h.authority} at {h.place} ({_fmt(h.held_from)})"))
            continue
        if not rules or not rules[0].typed_params().covers_govt_detention:
            out.append(res(cid, CS.REFER, ST, reason=Reason.FM_WORDING_ARGUABLE, deps=[t.term_id for t in rules],
                           cits=term_cits(c, rules[:1]) + keep(c, [hold_evidence(trip, h)]),
                           missing=f"agreement does not say government detention is excused; a person decides "
                                   f"({h.authority} at {h.place})"))
            continue
        if h.released_at is None:
            out.append(res(cid, CS.INCOMPLETE, ST, reason=Reason.EVIDENCE_PARTIAL, deps=[rules[0].term_id],
                           missing=f"release time of the hold by {h.authority} at {h.place}"))
            continue
        cits = term_cits(c, rules[:1]) + keep(c, [hold_evidence(trip, h)])
        out.append(res(cid, CS.GROUND, ST, ground=Ground.REGULATORY_HOLD, cits=cits, deps=[rules[0].term_id],
                       corr=P.correction("excused_interval", start=h.held_from, end=h.released_at, citations=cits,
                                         label=f"held by {h.authority} at {h.place} ({_fmt(h.held_from)} to {_fmt(h.released_at)})"),
                       note=f"memo on file; clause covers government detention"))
    return out


# ----------------------------------------------------------------------------- marker for stage 5

@check(stage=ST, needs_terms=["penalty_rule", "cap"], settings=["records"])
def delay_remaining(c: CheckContext) -> "CheckResult":
    """Marker: does a delay penalty still stand after stages 3-4 (stage 5 runs only if so)?"""
    if c.deduction.reason_code not in P.DEFAULT_SPAN:
        return res("delay_remaining", CS.NOT_APPLICABLE, ST, note="unexplained_delay=no (not a delay penalty)")
    prior = [r for s in (Stage.terms_checked,) for r in c.prior.get(s, [])]
    mine = []
    for fn in (delay_at_all, start_point, client_caused, regulatory_hold):
        try:
            out = fn(c)
            mine.extend(out if isinstance(out, list) else [out])
        except Exception:  # noqa: BLE001 - marker only
            pass
    corr = [r.correction for r in prior + mine if r.status == CS.GROUND and r.correction is not None]
    try:
        val, _ = P.recompute(c.deduction, c.trip, c.terms, corr, agreement=c.agreement)
        remains = val > 0
    except Exception:  # noqa: BLE001 - unknown: keep checking
        remains = True
    return res("delay_remaining", CS.NOT_APPLICABLE, ST,
               note=f"unexplained_delay={'yes' if remains else 'no'}")
