"""K6: the calling convention in front of `pdr run` (read request -> load -> engine), used by the CLI."""
from __future__ import annotations

from typing import Any

from pdr.codes import RequestType
from pdr.models import Agreement, Deduction, Term, Verdict

from .reader import describe, load_request_ex, read_request


def run_request(path_or_text: Any, ctx: Any, *, run_engine: bool = True) -> dict:
    """Returns {"request_type", "message", "dispute_keys", "summary", "verdicts"}; never guesses a job."""
    req = read_request(path_or_text, ctx)
    out: dict[str, Any] = {"request_type": req.request_type.value, "message": describe(req),
                           "dispute_keys": [], "summary": {}, "verdicts": []}
    if req.request_type == RequestType.check_deductions:
        deds, summary = load_request_ex(path_or_text, ctx)
        out["summary"] = summary
        out["dispute_keys"] = [d.dispute_key for d in deds]
        if run_engine and deds:
            try:
                from pdr import engine
                out["verdicts"] = engine.run(out["dispute_keys"], ctx)
            except (ImportError, AttributeError) as e:
                out["message"] += f" (engine unavailable: {type(e).__name__})"
    elif req.request_type == RequestType.status:
        s = ctx.store
        out["summary"] = {"deductions": len(s.find(Deduction)), "unfinished": len(s.unfinished()),
                          "verdicts": len([v for v in s.find(Verdict) if v.superseded_by is None]),
                          "agreements": len(s.find(Agreement)),
                          "terms_unconfirmed": len(s.find(Term, status="unconfirmed"))}
    elif req.request_type == RequestType.intake_agreement:
        out["message"] += " -> run: pdr intake <client> <files...>, then pdr confirm <client>"
    return out
