"""Explanations assembled from check notes and citations; no model text (B14)."""
from __future__ import annotations

from pdr.codes import CheckStatus, Flag, Outcome, Reason

NEXT = {
    Reason.AMBIGUOUS_MATCH: "to a person: confirm which client or trip this deduction belongs to",
    Reason.TRIP_NOT_FOUND: "to a person: find the trip the deduction refers to",
    Reason.TERMS_MISSING: "request the missing agreement document from the account manager",
    Reason.TERMS_UNCONFIRMED: "billing lead to confirm the extracted terms",
    Reason.RECORDS_CONFLICT: "to a person: decide between the conflicting records",
    Reason.NOTICE_EVIDENCE_MISSING: "ask the account manager for the notice email; no search run",
    Reason.FM_WORDING_ARGUABLE: "refer to a person with the exact wording",
    Reason.NO_FM_CLAUSE: "refer to a person: the agreement has no force-majeure clause",
    Reason.SOURCES_CONFLICT: "a person decides between the conflicting sources",
    Reason.SOURCE_UNDATED: "find a dated report or official advisory",
    Reason.EVIDENCE_PARTIAL: "to a person: obtain the named record (e.g. GPS extract or written order)",
    Reason.CAUSATION_PARTIAL: "to a person: the delay cannot be split under a per-event penalty",
    Reason.NO_EVIDENCE_FOUND: "request a police or district advisory for the reported disruption",
    Reason.EXPLANATION_IGNORED: "to the account manager: the client decides if the explanation was satisfactory",
    Reason.LEGAL_JUDGMENT: "to legal",
    Reason.SEARCH_NOT_ECONOMICAL: "search skipped as uneconomical; a person can force it",
    Reason.BUDGET_REACHED: "run again with more budget or time to finish",
    Reason.PROVIDER_FAILED: "run again when the provider is reachable",
    Reason.INTERNAL_ERROR: "to a person: a check failed; see the log",
}


def _next_action(v) -> str:
    if v.outcome == Outcome.disputable:
        return "hold for account manager" if Flag.OUT_OF_WINDOW in v.flags else "draft dispute; approval required"
    if v.outcome == Outcome.valid:
        return "accept and record"
    return NEXT.get(v.primary_reason, "to a person")


def _sentence(s: str) -> str:
    s = (s or "").strip()
    return s if not s or s.endswith(".") else s + "."


def explain(verdict, checks, st=None):
    """Fill explanation, next_action, what_would_change and checks_run where combine left them empty."""
    parts = []
    if verdict.outcome == Outcome.disputable:
        parts.append(f"Disputable for Rs {verdict.amount_disputable} of Rs {verdict.amount_deducted} on "
                     f"{', '.join(str(g) for g in verdict.grounds)}.")
    elif verdict.outcome == Outcome.valid:
        parts.append(f"Valid: every check finished and none found a ground against the Rs {verdict.amount_deducted} deduction.")
    else:
        parts.append(f"Cannot verify ({verdict.primary_reason}).")
    for r in checks:
        if r.status == CheckStatus.GROUND:
            parts.append(_sentence(f"Ground {r.ground}: {r.note}"))
        elif r.status in (CheckStatus.INCOMPLETE, CheckStatus.REFER):
            parts.append(_sentence(f"Open ({r.reason}): {r.missing_item or r.note}"))
    cited = []
    for r in checks:
        for ct in r.citations:
            if ct.label and ct.label not in cited:
                cited.append(ct.label)
    if cited:
        parts.append("Sources: " + "; ".join(cited[:8]) + ("; ..." if len(cited) > 8 else "") + ".")
    if Flag.STAND_IN_TERMS in verdict.flags:
        parts.append("Terms used are stand-in public terms, not the client's signed agreement.")
    if Flag.INSTRUCTION_IN_INPUT in verdict.flags:
        parts.append("The deduction text contained instruction-like text; it was treated as data and ignored.")
    wwc = list(verdict.what_would_change)
    if not wwc:
        if verdict.outcome == Outcome.cannot_verify:
            wwc = [m for m in verdict.missing_items] or [r.missing_item for r in checks if r.missing_item]
        elif verdict.outcome == Outcome.disputable:
            wwc = [f"a record contradicting: {ct.label}" for r in checks if r.status == CheckStatus.GROUND
                   for ct in r.citations if ct.kind != "contract_term"][:5] or ["a client record contradicting the cited facts"]
        else:
            wwc = ["a qualifying record or term showing a ground (e.g. a confirmed cap, a signed POD on time)"]
    missing = list(verdict.missing_items) or [r.missing_item for r in checks
                                              if r.missing_item and r.status in (CheckStatus.INCOMPLETE, CheckStatus.REFER)]
    return verdict.model_copy(update={
        "explanation": verdict.explanation or " ".join(parts),
        "next_action": verdict.next_action or _next_action(verdict),
        "what_would_change": wwc,
        "missing_items": missing,
        "checks_run": list(verdict.checks_run) or list(dict.fromkeys(f"{r.stage}:{r.check_id}:{r.status}" for r in checks)),
    })
