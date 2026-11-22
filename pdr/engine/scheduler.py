"""Two-pass run (DESIGN 5.3, D-22): free stages for all, then one search per evidence group."""
from __future__ import annotations

import logging
from decimal import Decimal

from pdr.codes import Stage
from pdr.contracts import RunContext
from pdr.engine import evidence_stage, pipeline
from pdr.models import Verdict

log = logging.getLogger("pdr.engine")


def _safe(fn, key, *a, **kw):
    """One deduction's failure never stops the run: it gets an INTERNAL_ERROR verdict instead."""
    try:
        return fn(key, *a, **kw)
    except Exception as err:  # noqa: BLE001
        log.exception("deduction %s failed outside the check barrier: %r", key, err)
        return None


def run(dispute_keys: list[str], ctx: RunContext) -> list[Verdict]:
    """Pass 1 settles what free checks can; pass 2 groups the rest by EvidenceRequest.key()."""
    keys = list(dict.fromkeys(dispute_keys))  # a resubmitted key is processed once
    verdicts: dict[str, Verdict] = {}
    pending: list[str] = []
    for key in keys:
        v = _safe(pipeline.process, key, ctx, upto=Stage.notice_checked)
        if v is not None:
            verdicts[key] = v
        else:
            pending.append(key)
    groups: dict[str, list[str]] = {}
    for key in pending:
        gk = _group_key(key, ctx)
        groups.setdefault(gk or f"solo:{key}", []).append(key)
    for gk, members in groups.items():
        total = Decimal("0")
        for k in members:
            st = pipeline.load_state(k, ctx)
            total += Decimal(st.deduction.amount) if st else Decimal("0")
        evidence_stage.GROUP_TOTALS[(ctx.run_id, gk)] = total
        for k in members:
            v = _safe(pipeline.process, k, ctx)
            if v is not None:
                verdicts[k] = v
    for key in keys:  # always answer: anything still undecided gets BUDGET_REACHED or INTERNAL_ERROR
        if key not in verdicts:
            v = _safe(pipeline.budget_verdict, key, ctx)
            if v is not None:
                verdicts[key] = v
    return [verdicts[k] for k in keys if k in verdicts]


def _group_key(key: str, ctx: RunContext) -> str | None:
    """The evidence group of a pending deduction (stages 1-5 are memo hits here)."""
    try:
        st = pipeline.load_state(key, ctx)
        if st is None or pipeline.advance(st, ctx, Stage.notice_checked) != "pending":
            return None
        from pdr.engine.checks_notice import evidence_plan
        plan = evidence_plan(st.c, st.prior)
        req = evidence_stage.build_request(st.c, plan) if plan else None
        return req.key() if req else None
    except Exception:  # noqa: BLE001
        log.exception("grouping failed for %s", key)
        return None
