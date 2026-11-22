"""A person confirms terms (value beside verbatim clause and page) and marks a client's document set complete."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from pdr.models import Agreement, Term, canonical_json

LOW_CONFIDENCE = 0.5  # below this, --all skips the term: it must be confirmed one by one


def unconfirmed(store: Any, client_id: str) -> list[Term]:
    return sorted(store.find(Term, client_id=client_id, status="unconfirmed"), key=lambda t: (t.agreement_id, t.page, t.term_id))


def render(t: Term) -> str:
    """Value beside the verbatim clause and its page, for the confirm screen."""
    flag = "  [LOW CONFIDENCE: confirm individually]" if t.extraction_confidence < LOW_CONFIDENCE else ""
    return (f"{t.term_id}  ({t.term_type}, {t.status}, confidence {t.extraction_confidence:.1f}){flag}\n"
            f"  value : {canonical_json(t.params)}\n"
            f"  clause: {t.clause_ref}, page {t.page}, effective from {t.effective_from}\n"
            f"  text  : \"{t.verbatim.strip()[:400]}\"")


def confirm(store: Any, client_id: str, who: str, *, term_ids: Optional[list[str]] = None, all_: bool = False,
            reject_ids: Optional[list[str]] = None) -> tuple[list[Term], list[Term]]:
    """Confirms the named terms (or all high-confidence ones), rejects reject_ids. Returns (changed, skipped)."""
    now = datetime.now(timezone.utc)
    changed, skipped = [], []
    targets = unconfirmed(store, client_id) if all_ else [store.get(Term, i) for i in (term_ids or [])]
    for t in targets:
        if t is None or t.client_id != client_id:
            continue
        if all_ and t.extraction_confidence < LOW_CONFIDENCE:
            skipped.append(t)
            continue
        t2 = t.model_copy(update={"status": "confirmed", "confirmed_by": who, "confirmed_at": now})
        store.put(t2)
        store.trail_append("term_confirmed", who, t.term_id, {"params": t.params, "page": t.page, "clause_ref": t.clause_ref})
        changed.append(t2)
    for i in reject_ids or []:
        t = store.get(Term, i)
        if t is not None and t.client_id == client_id:
            t2 = t.model_copy(update={"status": "rejected", "confirmed_by": who, "confirmed_at": now})
            store.put(t2)
            store.trail_append("term_rejected", who, t.term_id, {"page": t.page})
            changed.append(t2)
    return changed, skipped


def complete_set(store: Any, client_id: str, who: str) -> tuple[list[Agreement], list[Agreement]]:
    """A person states the uploaded set is the full set in force. Agreements with missing documents stay 'missing'."""
    now = datetime.now(timezone.utc)
    done, still_missing = [], []
    for a in store.find(Agreement, client_id=client_id):
        if a.missing_documents:
            still_missing.append(a)
            continue
        a2 = a.model_copy(update={"completeness": "complete", "confirmed_by": who, "confirmed_at": now})
        store.put(a2)
        done.append(a2)
    store.trail_append("complete_set", who, client_id, {"complete": [a.agreement_id for a in done],
                                                         "missing": {a.agreement_id: a.missing_documents for a in still_missing}})
    return done, still_missing
