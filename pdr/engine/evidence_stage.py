"""Stage 6 (evidence_searched): economic gate, one search per evidence group, corroboration, causation (DESIGN 5.6, 8.4)."""
from __future__ import annotations

import logging
import re
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Optional

from pdr.codes import CheckStatus, EventType, Ground, RateUnit, Reason, ReasonCode, Stage, TsSource
from pdr.contracts import BudgetReached, CheckContext, EvidenceRequest, EvidenceResult
from pdr.engine.checks_notice import assess, evidence_plan, keep, latest_term, record_evidence, term_evidence
from pdr.engine.gate import trip_date
from pdr.engine.registry import check
from pdr.models import CheckResult, Correction, Evidence, norm_key

log = logging.getLogger("pdr.engine")
IST = timezone(timedelta(hours=5, minutes=30))
CORROBORATING = {TsSource.gps, TsSource.fastag, TsSource.driver_report}

# per-run memo so one search serves every deduction of an evidence group (D-22)
_RUN_RESULTS: dict[tuple[str, str], EvidenceResult] = {}
GROUP_TOTALS: dict[tuple[str, str], Decimal] = {}
_SPAN = {ReasonCode.late_placement: ("placement_required", "reporting"),
         ReasonCode.late_reporting: ("placement_required", "reporting"),
         ReasonCode.non_placement: ("placement_required", "reporting")}


def _r(status, **kw) -> CheckResult:
    return CheckResult(check_id="evidence_search", stage=Stage.evidence_searched, status=status, **kw)


# ----------------------------------------------------------------------------- times

def _value(trip, event: str, settings) -> Optional[datetime]:
    try:
        from pdr.engine import timeline
        return timeline.resolve(trip, settings).value(event)
    except Exception:  # noqa: BLE001 - simple fallback while the resolver is missing
        fixed = {"delivery_due": trip.delivery_due_at, "placement_required": trip.placement_required_at}
        if event in fixed:
            return fixed[event]
        obs = getattr(trip, f"{event}_at", None) or []
        return min((o.value for o in obs), default=None)


def in_transit_window(trip, settings) -> Optional[tuple[datetime, datetime]]:
    """(dispatch, arrival) by the timeline resolver; None when either is unknown."""
    start = _value(trip, "gate_out", settings) or _value(trip, "reporting", settings)
    end = _value(trip, "arrival", settings) or _value(trip, "unloaded", settings)
    if start is None or end is None or end < start:
        return None
    return start, end


def delay_span(c: CheckContext) -> Optional[tuple[datetime, datetime]]:
    """(due, actual) for the penalised event."""
    frm, to = _SPAN.get(c.deduction.reason_code, ("delivery_due", "arrival"))
    due, actual = _value(c.trip, frm, c.ctx.settings), _value(c.trip, to, c.ctx.settings)
    return (due, actual) if due and actual else None


def reset_run_memo() -> None:
    """Forget per-run search results and group totals (start of each run; the broker cache still holds them)."""
    _RUN_RESULTS.clear()
    GROUP_TOTALS.clear()


def build_request(c: CheckContext, plan: dict) -> Optional[EvidenceRequest]:
    """EvidenceRequest from route places, the in-transit window and covered event types; None without a window."""
    win = in_transit_window(c.trip, c.ctx.settings)
    if win is None:
        return None
    return EvidenceRequest(route_places=list(c.trip.route_places), route_states=list(c.trip.route_states),
                           window=win, event_types=list(plan["event_types"]), mode=plan["mode"])


def get_evidence(req: EvidenceRequest, ctx) -> EvidenceResult:
    """find_evidence once per (run, evidence group); BudgetReached becomes status 'budget'."""
    k = (ctx.run_id, req.key())
    if k in _RUN_RESULTS:
        return _RUN_RESULTS[k]
    import pdr.evidence as ev
    try:
        res = ev.find_evidence(req, ctx)
    except BudgetReached:
        res = EvidenceResult(status="budget")
    _RUN_RESULTS[k] = res
    return res


# ----------------------------------------------------------------------------- corroboration

def _n(s) -> str:
    return norm_key(s)


def place_match(record_place: Optional[str], affected: list[str], route_places: list[str]) -> bool:
    """A record's place matches an affected place (either names the other, or both name one route place)."""
    rp = _n(record_place)
    if not rp:
        return False
    for p in affected:
        ap = _n(p)
        if ap and (ap in rp or rp in ap):
            return True
        for r in route_places:
            rr = _n(r)
            if rr and len(rr) >= 3 and rr in rp and rr in ap:
                return True
    return False


def event_interval(res: EvidenceResult) -> Optional[tuple[datetime, datetime]]:
    if res.event_interval:
        return res.event_interval
    froms = [e.event_from for e in res.supporting if e.event_from]
    tos = [e.event_to or e.event_from for e in res.supporting if e.event_from]
    if not froms:
        return None
    return datetime.combine(min(froms), time.min, IST), datetime.combine(max(tos), time.max, IST)


_SEG = r"([A-Za-z][\w .()'-]{1,60}?)"
_STRETCH_RES = [
    re.compile(r"\bbetween\s+" + _SEG + r"\s+and\s+" + _SEG + r"(?=[,.;:]|\s+(?:on|in|along|of|stretch|section)\b|$)", re.I),
    re.compile(r"\bfrom\s+" + _SEG + r"\s+to\s+" + _SEG + r"(?=[,.;:]|\s+(?:on|in|along|of|stretch|section)\b|$)", re.I),
    re.compile(_SEG + r"\s*(?:\bto\b|-|\u2013|\u2014)\s*" + _SEG + r"\s+(?:stretch|section)\b", re.I),
]


def _route_index(text: str | None, route_places: list[str], prefer: str = "only") -> int | None:
    """Index of the route place a text names: 'last'/'first' by position in the text, 'only' = unique match."""
    low = (text or "").lower()
    hits = []
    for i, r in enumerate(route_places):
        if len(_n(r)) < 3:
            continue
        for m in re.finditer(r"\b" + re.escape(r.lower()) + r"\b", low):
            hits.append((m.start(), m.end(), i))
    if not hits:
        return None
    if prefer == "last":
        return max(hits, key=lambda h: (h[1], h[1] - h[0]))[2]
    if prefer == "first":
        return min(hits, key=lambda h: (h[0], -(h[1] - h[0])))[2]
    ids = {h[2] for h in hits}
    return ids.pop() if len(ids) == 1 else None


def stretches(text: str | None, route_places: list[str]) -> list[tuple[int, int]]:
    """(i, j) index pairs of from-to stretches the text names whose both ends are on the ordered route."""
    out = []
    for rx in _STRETCH_RES:
        for m in rx.finditer(text or ""):
            a, b = _route_index(m.group(1), route_places, "last"), _route_index(m.group(2), route_places, "first")
            if a is not None and b is not None and a != b:
                out.append((min(a, b), max(a, b)))
    return out


def in_stretch(record_place: str | None, texts: list[str], route_places: list[str]) -> bool:
    """A record's place is a route place lying between the two ends of a stretch the evidence names."""
    k = _route_index(record_place, route_places)
    if k is None:
        return False
    return any(i <= k <= j for t in texts for (i, j) in stretches(t, route_places))


def _evidence_texts(c: CheckContext, res: EvidenceResult) -> list[str]:
    texts = []
    for e in res.supporting:
        if e.passage:
            texts.append(e.passage)
        try:
            snap = c.ctx.store.snapshot_get(e.content_hash) if e.content_hash else None
            if snap and snap.get("text"):
                texts.append(snap["text"])
        except Exception:  # noqa: BLE001 - snapshot is optional context for stretch names
            pass
    return texts


def corroborate(c: CheckContext, res: EvidenceResult, iv) -> list[tuple[datetime, datetime, Evidence]]:
    """Own records placing the truck in an affected place (or inside a named stretch) during the event."""
    affected = [e.place_named for e in res.supporting if e.place_named]
    texts = _evidence_texts(c, res)
    out = []
    trip = c.trip

    def at_place(place):
        return place_match(place, affected, trip.route_places) or in_stretch(place, texts, trip.route_places)
    for h in trip.halts:
        if h.source in CORROBORATING and at_place(h.place):
            s, e = max(h.start, iv[0]), min(h.end, iv[1])
            if s <= e:
                out.append((s, e, record_evidence(trip, h.source.value, "halts",
                                                  f"{h.start.isoformat()}/{h.end.isoformat()} at {h.place}", h.start, h.ref)))
    for a in trip.assertions:
        if a.kind == "driver_report" and a.at and iv[0] <= a.at <= iv[1] and at_place(a.place):
            out.append((a.at, a.at, record_evidence(trip, a.kind, "assertions", a.text, a.at, a.ref)))
    return out


def has_own_records(trip) -> bool:
    return any(h.source in CORROBORATING for h in trip.halts) or any(
        a.kind == "driver_report" and a.at for a in trip.assertions)


def _search_note(res: EvidenceResult) -> str:
    lines = [f"'{s.query}' -> {s.n_results} results" + (" (cache)" if s.cache_hit else "") for s in res.search_log]
    rej = [f"{r.url} rejected: {r.reason}" for r in res.rejected]
    return "search log: " + ("; ".join(lines) or "no queries") + (". " + "; ".join(rej) if rej else "")


# ----------------------------------------------------------------------------- the check

@check(stage=Stage.evidence_searched, needs_terms=["force_majeure", "notice", "penalty_rule"],
       needs_events=["gate_out", "arrival", "delivery_due"],
       settings=["evidence", "economics", "causation", "budget"])
def evidence_search(c: CheckContext):
    """Search public sources for the asserted or discoverable disruption and test corroboration and causation."""
    plan = evidence_plan(c, c.prior)
    if plan is None:
        return _r(CheckStatus.NOT_APPLICABLE, note="no evidence search needed")
    mode, referral = plan["mode"], plan.get("referral", False)
    settings = c.ctx.settings
    a = assess(c)
    fm_cits = keep(c, [term_evidence(a.fm_term, c.agreement)]) if a.fm_term else []
    if not c.trip.route_places:
        return _r(CheckStatus.INCOMPLETE, reason=Reason.EVIDENCE_PARTIAL, citations=fm_cits,
                  missing_item=f"route places for lane {c.trip.lane}", note="no route places; evidence cannot qualify")
    req = build_request(c, plan)
    if req is None:
        return _r(CheckStatus.INCOMPLETE, reason=Reason.EVIDENCE_PARTIAL, citations=fm_cits,
                  missing_item=f"dispatch and arrival times for trip {c.trip.trip_id} (in-transit window)")
    if not req.event_types:
        if mode == "discovery":
            return _r(CheckStatus.NOT_APPLICABLE, note="no searchable event type")
        return _r(CheckStatus.INCOMPLETE, reason=Reason.NO_EVIDENCE_FOUND, citations=fm_cits,
                  missing_item="official advisory or report of the asserted event (no search vocabulary in settings)")
    gps_item = f"GPS extract for vehicle {c.trip.vehicle_no} during {req.window[0].date()} to {req.window[1].date()}"
    if mode == "asserted" and not settings.evidence.search_when_corroboration_missing and not has_own_records(c.trip):
        return _r(CheckStatus.INCOMPLETE, reason=Reason.EVIDENCE_PARTIAL, citations=fm_cits, missing_item=gps_item,
                  note="no own record could corroborate; search skipped by setting")
    # economic gate (D-28)
    ratio = Decimal(str(settings.economics.search_gate_ratio or 0))
    if ratio > 0:
        try:
            from pdr.evidence import estimate_credits
            est = Decimal(str(estimate_credits(req, c.ctx)))
        except Exception:  # noqa: BLE001
            est = Decimal(str(settings.economics.estimated_search_credits))
        cost_inr = est * Decimal(str(settings.budget.credit_price_usd)) * Decimal(str(settings.budget.usd_inr))
        at_stake = GROUP_TOTALS.get((c.ctx.run_id, req.key()), Decimal(c.deduction.amount))
        if cost_inr > 0 and at_stake < ratio * cost_inr:
            return _r(CheckStatus.INCOMPLETE, reason=Reason.SEARCH_NOT_ECONOMICAL, citations=fm_cits,
                      missing_item=f"search not economical: Rs {at_stake} recoverable vs estimated Rs {cost_inr.quantize(Decimal('0.01'))} "
                                   f"({est} credits); a person can force it")
    res = get_evidence(req, c.ctx)
    log_note = _search_note(res)
    sup = keep(c, res.supporting)
    con = keep(c, res.contradicting)
    if res.status == "budget":
        return _r(CheckStatus.INCOMPLETE, reason=Reason.BUDGET_REACHED, citations=fm_cits, note=log_note,
                  missing_item="search not completed within the run's cost or time limit")
    if res.status == "provider_failed":
        return _r(CheckStatus.INCOMPLETE, reason=Reason.PROVIDER_FAILED, citations=fm_cits, note=log_note,
                  missing_item="search provider failed after retries; run again later")
    if res.status == "no_route_places":
        return _r(CheckStatus.INCOMPLETE, reason=Reason.EVIDENCE_PARTIAL, citations=fm_cits, note=log_note,
                  missing_item=f"route places for lane {c.trip.lane}")
    if res.supporting and res.contradicting:
        return _r(CheckStatus.INCOMPLETE, reason=Reason.SOURCES_CONFLICT, citations=fm_cits + sup + con, note=log_note,
                  missing_item="qualifying sources disagree; both shown side by side for a person")
    if mode == "discovery":
        if res.status == "found" and res.supporting:
            return _r(CheckStatus.INCOMPLETE, reason=Reason.EVIDENCE_PARTIAL, citations=fm_cits + sup, note=log_note,
                      missing_item="the 3PL's own account of the reported event: notice record and a GPS or driver "
                                   "record placing the truck there (nobody at the 3PL asserted it)")
        return _r(CheckStatus.NO_GROUND, citations=fm_cits, note="no disruption asserted; no qualifying source. " + log_note)
    claim_cits = keep(c, [cl.evidence for cl in a.claims])
    if res.status == "undated_only":
        return _r(CheckStatus.INCOMPLETE, reason=Reason.SOURCE_UNDATED, citations=fm_cits + claim_cits, note=log_note,
                  missing_item="report found but its publication date cannot be read from the page")
    if res.status != "found" or not res.supporting:
        return _r(CheckStatus.INCOMPLETE, reason=Reason.NO_EVIDENCE_FOUND, citations=fm_cits + claim_cits, note=log_note,
                  missing_item="police or district advisory, or a dated report, for the disruption the 3PL reported")
    if len(res.supporting) < int(settings.evidence.min_qualifying_sources or 1):
        return _r(CheckStatus.INCOMPLETE, reason=Reason.EVIDENCE_PARTIAL, citations=fm_cits + sup, note=log_note,
                  missing_item=f"{settings.evidence.min_qualifying_sources} qualifying sources required; "
                               f"{len(res.supporting)} found")
    iv = event_interval(res)
    hits = corroborate(c, res, iv) if iv else []
    if not hits:
        return _r(CheckStatus.INCOMPLETE, reason=Reason.EVIDENCE_PARTIAL, citations=fm_cits + sup + claim_cits,
                  note=log_note + ". News alone never carries a dispute", missing_item=gps_item)
    if referral:
        return _r(CheckStatus.REFER, reason=Reason.FM_WORDING_ARGUABLE, citations=fm_cits + sup, note=log_note,
                  missing_item="evidence found for an event the clause covers only through general words; a person decides")
    span = delay_span(c)
    if span is None:
        return _r(CheckStatus.INCOMPLETE, reason=Reason.EVIDENCE_PARTIAL, citations=fm_cits + sup, note=log_note,
                  missing_item="due and actual times of the penalised event")
    delay = span[1] - span[0]
    start = min(h[0] for h in hits)
    end = max(h[1] for h in hits) + timedelta(hours=float(settings.causation.allowance_hours or 0))
    if delay.total_seconds() <= 0:
        return _r(CheckStatus.NOT_APPLICABLE, note="no delay to excuse")
    end = min(end, start + delay)
    excused = end - start
    rec_cits = keep(c, [h[2] for h in hits])
    d = trip_date(c.trip, c.deduction)
    rule = latest_term(c.terms, "penalty_rule", d, lambda t: t.params.get("event") == c.deduction.reason_code.value)
    cits = fm_cits + sup + rec_cits
    if rule is not None and rule.params.get("unit") == RateUnit.per_instance.value and delay > excused:
        return _r(CheckStatus.INCOMPLETE, reason=Reason.CAUSATION_PARTIAL, citations=cits, note=log_note,
                  missing_item=f"delay of {delay} is longer than the excused {excused}; per-instance penalty cannot be split")
    label = f"force-majeure halt {start.isoformat()} to {end.isoformat()} (incl. {settings.causation.allowance_hours}h allowance)"
    corr = Correction(kind="excused_interval", order=60, value={
        "start": start.isoformat(), "end": end.isoformat(), "label": label, "ground": Ground.FORCE_MAJEURE.value,
        "citations": [ct.model_dump(mode="json") for ct in cits]})
    return _r(CheckStatus.GROUND, ground=Ground.FORCE_MAJEURE, citations=cits, correction=corr,
              depends_on=[t.term_id for t in (a.fm_term, a.notice_term) if t] + ["arrival", "delivery_due"],
              note=f"{label}; corroborated by own records; " + log_note)
