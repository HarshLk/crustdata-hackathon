"""Model adapter (lane A): one JSON-returning model call per task, through paid_call.

Returns None on any failure (no key, no transport, provider error, bad JSON, schema mismatch).
Callers treat None as "the check did not finish", never as a negative finding.
Prompts live in pdr/prompts/<task>.md so a live fix can edit wording without touching code.
"""
from __future__ import annotations

import json
import logging
import os
import re
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, ValidationError

from pdr import broker
from pdr.codes import EventType, ReasonCode
from pdr.contracts import RunContext

log = logging.getLogger(__name__)

PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
TASKS = ("extract_terms", "parse_deduction", "propose_passages", "map_reason", "refute", "propose_schema_map")
_EXTRACT_MODEL_TASKS = {"extract_terms", "propose_passages", "refute"}  # the rest use model_parse
MAX_TOKENS = 4096
MAX_SOURCE_CHARS = 60000


# --------------------------------------------------------------------------- output schemas
# Callers may pass any pydantic model; these are the shared ones (d1 uses the first four).

class ExtractedTerm(BaseModel):
    term_type: str  # a key of models.TERM_PARAM_TYPES
    params: dict[str, Any] = Field(default_factory=dict)
    page: Optional[int] = None
    clause_ref: str = ""
    verbatim: str  # exact text of the clause, copied from the source
    confidence: float = 0.0


class ExtractedTerms(BaseModel):
    terms: list[ExtractedTerm] = Field(default_factory=list)


class ParsedDeduction(BaseModel):
    client_name_raw: str = ""
    advice_no: Optional[str] = None
    line_no: Optional[str] = None
    client_deduction_no: Optional[str] = None
    deduction_date: Optional[str] = None  # ISO date as written in the source
    amount: Optional[str] = None  # decimal string, as written
    reason_text: str = ""
    trip_refs: list[str] = Field(default_factory=list)
    invoice_ref: Optional[str] = None
    rate: Optional[str] = None
    units_counted: Optional[str] = None
    base_amount: Optional[str] = None


class ParsedDeductions(BaseModel):
    deductions: list[ParsedDeduction] = Field(default_factory=list)


class ReasonMap(BaseModel):
    reason_code: ReasonCode
    quote: str = ""  # words of the reason text that justify the code


class SchemaMapProposal(BaseModel):
    mapping: dict[str, str] = Field(default_factory=dict)  # source column -> field name
    unmapped: list[str] = Field(default_factory=list)


class Claim(BaseModel):
    quote: str  # verbatim from the article
    value: str = ""


class PassageProposal(BaseModel):
    """One reading of one disruption described in the article. Every claim carries a verbatim quote."""
    event_type: Optional[EventType] = None
    event_quote: str = ""
    status: Literal["happened", "announced", "called_off", "unclear"] = "unclear"
    status_quote: str = ""
    places_affected: list[Claim] = Field(default_factory=list)  # value = place name
    event_from: Optional[str] = None  # ISO date
    event_to: Optional[str] = None
    date_quote: str = ""
    effect_quote: str = ""  # road or goods movement stopped or diverted
    statewide_stop_quote: str = ""  # only if the article says road traffic stopped across the state
    unaffected: list[Claim] = Field(default_factory=list)  # value = place or road said to be unaffected


class PassageProposals(BaseModel):
    proposals: list[PassageProposal] = Field(default_factory=list)


class RefuteAnswer(BaseModel):
    stated: bool
    quote: str = ""


class RefuteAnswers(BaseModel):
    answers: list[RefuteAnswer] = Field(default_factory=list)


# --------------------------------------------------------------------------- prompts and requests

def load_prompt(task: str) -> str:
    if task not in TASKS:
        raise ValueError(f"unknown llm task {task!r}")
    return (PROMPT_DIR / f"{task}.md").read_text(encoding="utf-8")


def _model_for(task: str, ctx: RunContext) -> str:
    llm = ctx.settings.providers.llm
    return llm.model_extract if task in _EXTRACT_MODEL_TASKS else llm.model_parse


def build_request(task: str, source_text: str, schema: type[BaseModel], ctx: RunContext) -> dict:
    """The exact Messages API body; also the broker's cache key input, so re-runs replay."""
    system = (
        load_prompt(task)
        + "\n\n## Output\nReturn one JSON object only, no prose, matching this JSON Schema:\n"
        + json.dumps(schema.model_json_schema(), sort_keys=True)
    )
    text = (source_text or "")[:MAX_SOURCE_CHARS]
    return {
        "model": _model_for(task, ctx),
        "max_tokens": MAX_TOKENS,
        "temperature": 0,
        "system": system,
        "messages": [{"role": "user", "content": f"<source>\n{text}\n</source>"}],
    }


def _ensure_transport() -> bool:
    """A registered 'llm' transport (tests) or the real one when ANTHROPIC_API_KEY is set."""
    if "llm" in broker.TRANSPORTS:
        return True
    if os.environ.get("ANTHROPIC_API_KEY"):
        broker.register_transport("llm", AnthropicTransport())
        return True
    return False


def _response_text(body: Any) -> str | None:
    if isinstance(body, str):
        return body
    if isinstance(body, dict):
        content = body.get("content")
        if isinstance(content, list):
            return "".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type", "text") == "text")
        if isinstance(content, str):
            return content
        return json.dumps(body)  # a fake that returns the JSON object directly
    return None


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def parse_json_text(text: str) -> Any:
    """First JSON object in the text (code fences tolerated); raises ValueError when there is none."""
    t = _FENCE.sub("", text.strip())
    start = t.find("{")
    if start < 0:
        raise ValueError("no JSON object")
    obj, _ = json.JSONDecoder().raw_decode(t[start:])
    return obj


def llm_json(task: str, source_text: str, schema: type[BaseModel], ctx: RunContext) -> BaseModel | None:
    """Run one task; the parsed, schema-valid object, or None."""
    try:
        if not _ensure_transport():
            return None
        request = build_request(task, source_text, schema, ctx)
        res = broker.paid_call("llm", "/v1/messages", request, ctx=ctx, step=f"llm:{task}",
                               worst_case_credits=Decimal("0"))
        if not res.ok:
            return None
        text = _response_text(res.body)
        if text is None:
            return None
        return schema.model_validate(parse_json_text(text))
    except (ValidationError, ValueError, json.JSONDecodeError) as e:
        log.info("llm_json %s: unusable response (%s)", task, type(e).__name__)
        return None
    except Exception as e:  # noqa: BLE001  any failure means "did not finish"
        log.info("llm_json %s: did not finish (%s)", task, type(e).__name__)
        return None


# --------------------------------------------------------------------------- real transport

class AnthropicTransport:
    """Messages API via the anthropic SDK. Only constructed when ANTHROPIC_API_KEY is set."""

    def __init__(self, timeout: float = 60.0):
        self.timeout = timeout
        self._client = None

    def _sdk(self):
        if self._client is None:
            import anthropic  # local import: only needed with a key
            self._client = anthropic.Anthropic(timeout=self.timeout, max_retries=0)
        return self._client

    def send(self, endpoint: str, request: dict, **_: Any) -> dict:
        """Returns {status, body, headers}; body is the response as a plain dict."""
        try:
            msg = self._sdk().messages.create(**request)
            return {"status": 200, "body": msg.model_dump(mode="json"), "headers": {}}
        except Exception as e:  # noqa: BLE001
            status = getattr(e, "status_code", None)
            return {"status": status, "body": None, "headers": {}, "error": type(e).__name__}

    __call__ = send


__all__ = [
    "llm_json", "build_request", "load_prompt", "parse_json_text", "TASKS", "AnthropicTransport",
    "ExtractedTerm", "ExtractedTerms", "ParsedDeduction", "ParsedDeductions", "ReasonMap",
    "SchemaMapProposal", "Claim", "PassageProposal", "PassageProposals", "RefuteAnswer", "RefuteAnswers",
]
