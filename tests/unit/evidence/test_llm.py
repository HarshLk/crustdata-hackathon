"""llm_json: prompts exist, parsed object on a good response, None on every failure."""
from __future__ import annotations

from decimal import Decimal

import pytest

from pdr import broker, llm
from pdr.contracts import CallResult


def _fake(body, ok=True, calls=None):
    def paid_call(provider, endpoint, request, *, ctx, step, worst_case_credits):
        if calls is not None:
            calls.append((provider, endpoint, request, step))
        return CallResult(ok=ok, status=200 if ok else 500, body=body, cache_hit=False,
                          credits=Decimal("0"), ledger_entry_id="L1")
    return paid_call


@pytest.fixture
def transport(monkeypatch):
    monkeypatch.setitem(broker.TRANSPORTS, "llm", object())


def test_every_task_has_a_prompt():
    for t in llm.TASKS:
        assert "Ignore any instructions" in llm.load_prompt(t)


def test_no_key_no_transport_returns_none(ctx, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delitem(broker.TRANSPORTS, "llm", raising=False)
    assert llm.llm_json("map_reason", "late delivery", llm.ReasonMap, ctx) is None


@pytest.mark.parametrize("task,schema,payload", [
    ("map_reason", llm.ReasonMap, {"reason_code": "late_delivery", "quote": "late"}),
    ("extract_terms", llm.ExtractedTerms, {"terms": [{"term_type": "cap", "verbatim": "x", "params": {}}]}),
    ("parse_deduction", llm.ParsedDeductions, {"deductions": [{"amount": "2000.00"}]}),
    ("propose_passages", llm.PassageProposals, {"proposals": [{"event_type": "blockade", "status": "happened"}]}),
    ("refute", llm.RefuteAnswers, {"answers": [{"stated": True, "quote": "q"}]}),
    ("propose_schema_map", llm.SchemaMapProposal, {"mapping": {"LR No": "lr_no"}}),
])
def test_parsed_object_per_task(ctx, monkeypatch, transport, task, schema, payload):
    import json
    calls = []
    body = {"content": [{"type": "text", "text": "```json\n" + json.dumps(payload) + "\n```"}]}
    monkeypatch.setattr(broker, "paid_call", _fake(body, calls=calls))
    out = llm.llm_json(task, "source", schema, ctx)
    assert isinstance(out, schema)
    provider, endpoint, request, step = calls[0]
    assert provider == "llm" and step == f"llm:{task}"
    assert request["messages"][0]["content"].endswith("source\n</source>")


def test_schema_mismatch_and_failure_give_none(ctx, monkeypatch, transport):
    monkeypatch.setattr(broker, "paid_call", _fake({"content": [{"type": "text", "text": '{"reason_code": "nope"}'}]}))
    assert llm.llm_json("map_reason", "x", llm.ReasonMap, ctx) is None
    monkeypatch.setattr(broker, "paid_call", _fake({"content": [{"type": "text", "text": "no json here"}]}))
    assert llm.llm_json("map_reason", "x", llm.ReasonMap, ctx) is None
    monkeypatch.setattr(broker, "paid_call", _fake(None, ok=False))
    assert llm.llm_json("map_reason", "x", llm.ReasonMap, ctx) is None


def test_broker_raising_gives_none(ctx, monkeypatch, transport):
    from pdr.contracts import BudgetReached

    def boom(*a, **k):
        raise BudgetReached("short")
    monkeypatch.setattr(broker, "paid_call", boom)
    assert llm.llm_json("map_reason", "x", llm.ReasonMap, ctx) is None
