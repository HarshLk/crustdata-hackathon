"""Vocabulary shared by every lane (INTERFACES.md section 1).

Every enum is a ``str`` Enum whose values equal the names written in INTERFACES.md,
so ``Outcome("valid") is Outcome.valid`` and ``Ground("WRONG_RATE") is Ground.WRONG_RATE``.
"""
from __future__ import annotations

from enum import Enum


class _StrEnum(str, Enum):
    def __str__(self) -> str:  # print the value, not "Class.member"
        return self.value


class Outcome(_StrEnum):
    valid = "valid"
    disputable = "disputable"
    cannot_verify = "cannot_verify"


class Ground(_StrEnum):
    NO_CONTRACTUAL_BASIS = "NO_CONTRACTUAL_BASIS"
    WRONG_RATE = "WRONG_RATE"
    WRONG_BASE = "WRONG_BASE"
    OVER_CAP = "OVER_CAP"
    DUPLICATE = "DUPLICATE"
    WRONG_DAY_COUNT = "WRONG_DAY_COUNT"
    NO_DELAY = "NO_DELAY"
    WRONG_START_POINT = "WRONG_START_POINT"
    CLIENT_CAUSED = "CLIENT_CAUSED"
    REGULATORY_HOLD = "REGULATORY_HOLD"
    FORCE_MAJEURE = "FORCE_MAJEURE"


class Reason(_StrEnum):
    TERMS_MISSING = "TERMS_MISSING"
    TERMS_UNCONFIRMED = "TERMS_UNCONFIRMED"
    TRIP_NOT_FOUND = "TRIP_NOT_FOUND"
    AMBIGUOUS_MATCH = "AMBIGUOUS_MATCH"
    RECORDS_CONFLICT = "RECORDS_CONFLICT"
    SOURCES_CONFLICT = "SOURCES_CONFLICT"
    EVIDENCE_PARTIAL = "EVIDENCE_PARTIAL"
    FM_WORDING_ARGUABLE = "FM_WORDING_ARGUABLE"
    NO_FM_CLAUSE = "NO_FM_CLAUSE"
    EXPLANATION_IGNORED = "EXPLANATION_IGNORED"
    SOURCE_UNDATED = "SOURCE_UNDATED"
    NOTICE_EVIDENCE_MISSING = "NOTICE_EVIDENCE_MISSING"
    CAUSATION_PARTIAL = "CAUSATION_PARTIAL"
    NO_EVIDENCE_FOUND = "NO_EVIDENCE_FOUND"
    BUDGET_REACHED = "BUDGET_REACHED"
    PROVIDER_FAILED = "PROVIDER_FAILED"
    LEGAL_JUDGMENT = "LEGAL_JUDGMENT"
    SEARCH_NOT_ECONOMICAL = "SEARCH_NOT_ECONOMICAL"  # D-28
    INTERNAL_ERROR = "INTERNAL_ERROR"  # D-25


class Flag(_StrEnum):
    OUT_OF_WINDOW = "OUT_OF_WINDOW"
    INSTRUCTION_IN_INPUT = "INSTRUCTION_IN_INPUT"
    CLIENT_ADMISSION_CHECK = "CLIENT_ADMISSION_CHECK"
    STAND_IN_TERMS = "STAND_IN_TERMS"
    RESUBMISSION = "RESUBMISSION"


class CheckStatus(_StrEnum):
    GROUND = "GROUND"
    NO_GROUND = "NO_GROUND"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    INCOMPLETE = "INCOMPLETE"
    REFER = "REFER"


class Stage(_StrEnum):
    received = "received"
    matched = "matched"
    terms_checked = "terms_checked"
    records_checked = "records_checked"
    notice_checked = "notice_checked"
    evidence_searched = "evidence_searched"
    verdict = "verdict"


class ActionStatus(_StrEnum):
    drafted = "drafted"
    awaiting_approval = "awaiting_approval"
    approved = "approved"
    sending = "sending"
    sent = "sent"
    held = "held"
    rejected = "rejected"


class ActionKind(_StrEnum):
    dispute_letter = "dispute_letter"
    document_request = "document_request"
    fm_notice = "fm_notice"
    payment_test = "payment_test"


class RequestType(_StrEnum):
    check_deductions = "check_deductions"
    intake_agreement = "intake_agreement"
    status = "status"
    unsupported = "unsupported"


class EventType(_StrEnum):
    """The vocabulary only; which ones a clause covers comes from its confirmed Term."""
    strike = "strike"
    bandh = "bandh"
    hartal = "hartal"
    chakka_jam = "chakka_jam"
    blockade = "blockade"
    agitation = "agitation"
    flood = "flood"
    landslide = "landslide"
    road_closure = "road_closure"
    curfew = "curfew"
    govt_detention = "govt_detention"
    riot = "riot"
    other = "other"


class ReasonCode(_StrEnum):
    """Mapped from Deduction.reason_text."""
    late_delivery = "late_delivery"
    late_placement = "late_placement"
    non_placement = "non_placement"
    late_reporting = "late_reporting"
    late_paperwork = "late_paperwork"
    detention_charge = "detention_charge"
    shortage = "shortage"
    damage = "damage"
    other = "other"


class RateUnit(_StrEnum):
    pct_per_week_or_part = "pct_per_week_or_part"
    pct_per_day = "pct_per_day"
    pct_per_vehicle_day = "pct_per_vehicle_day"
    flat_per_day = "flat_per_day"
    per_quintal_day = "per_quintal_day"
    per_instance = "per_instance"


class Base(_StrEnum):
    delayed_quantity_value = "delayed_quantity_value"
    trip_cost = "trip_cost"
    daily_hire = "daily_hire"
    contract_value = "contract_value"
    invoice_value = "invoice_value"
    none = "none"


class DayCount(_StrEnum):
    calendar = "calendar"
    working = "working"


class TsSource(_StrEnum):
    client_gate_log = "client_gate_log"
    consignee_gate_log = "consignee_gate_log"
    weighbridge = "weighbridge"
    pod_signed = "pod_signed"
    eway_bill = "eway_bill"
    gps = "gps"
    fastag = "fastag"
    driver_report = "driver_report"
    client_stated = "client_stated"
    ops_note = "ops_note"


# Stages in pipeline order. free pass = received..notice_checked; evidence pass = evidence_searched.
STAGE_ORDER: list[Stage] = [
    Stage.received,
    Stage.matched,
    Stage.terms_checked,
    Stage.records_checked,
    Stage.notice_checked,
    Stage.evidence_searched,
    Stage.verdict,
]
FREE_STAGES: list[Stage] = STAGE_ORDER[:5]

# Gate reasons (stage 2 "matched") come first, then the rest in INTERFACES.md order.
GATE_REASONS: list[Reason] = [Reason.AMBIGUOUS_MATCH, Reason.TRIP_NOT_FOUND]

# Primary reason of a cannot_verify verdict = the first reason in this list that is present.
REASON_PRECEDENCE: list[Reason] = [
    Reason.AMBIGUOUS_MATCH,
    Reason.TRIP_NOT_FOUND,
    Reason.TERMS_MISSING,
    Reason.TERMS_UNCONFIRMED,
    Reason.RECORDS_CONFLICT,
    Reason.NOTICE_EVIDENCE_MISSING,
    Reason.FM_WORDING_ARGUABLE,
    Reason.NO_FM_CLAUSE,
    Reason.SOURCES_CONFLICT,
    Reason.SOURCE_UNDATED,
    Reason.EVIDENCE_PARTIAL,
    Reason.CAUSATION_PARTIAL,
    Reason.NO_EVIDENCE_FOUND,
    Reason.EXPLANATION_IGNORED,
    Reason.LEGAL_JUDGMENT,
    Reason.SEARCH_NOT_ECONOMICAL,
    Reason.BUDGET_REACHED,
    Reason.PROVIDER_FAILED,
    Reason.INTERNAL_ERROR,
]
assert set(REASON_PRECEDENCE) == set(Reason), "REASON_PRECEDENCE must list every Reason"


def primary_reason(reasons) -> Reason | None:
    """First reason by REASON_PRECEDENCE, or None if the list is empty."""
    present = {Reason(r) for r in reasons}
    for r in REASON_PRECEDENCE:
        if r in present:
            return r
    return None
