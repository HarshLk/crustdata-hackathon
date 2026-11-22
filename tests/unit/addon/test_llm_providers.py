"""E2: provider-agnostic model adapter (anthropic | openai_compatible), cache keys, doctor, offline."""
from __future__ import annotations

import json

import httpx
import pytest

from pdr import broker, doctor, llm
from pdr.broker import ledger
from pdr.broker.transport import cache_key
from pdr.llm_openai import OpenAICompatibleTransport
from tests.unit.addon.test_llm_costs import LlmFake
from tests.unit.broker.conftest import make_ctx

SECRET = "sk-test-DO-NOT-PRINT-123"
RECORDED = {"id": "chatcmpl-1", "object": "chat.completion", "model": "gpt-x",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant",
                                     "content": '{"reason_code": "late_delivery", "quote": "late"}'}}],
            "usage": {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150}}


@pytest.fixture
def octx(tmp_path):
    c = make_ctx(tmp_path / "pdr.db")
    cfg = c.settings.providers.llm
    cfg.provider, cfg.base_url, cfg.api_key_env = "openai_compatible", "https://llm.example/v1", "PDR_TEST_LLM_KEY"
    cfg.model_parse = cfg.model_extract = "gpt-x"
    return c


def _mock(seen, status=200, body=RECORDED):
    def handler(req: httpx.Request):
        seen.append(req)
        return httpx.Response(status, json=body)
    return httpx.MockTransport(handler)


def test_openai_transport_request_and_recorded_response(octx, monkeypatch):
    monkeypatch.setenv("PDR_TEST_LLM_KEY", SECRET)
    seen: list = []
    monkeypatch.setitem(broker.TRANSPORTS, "llm",
                        OpenAICompatibleTransport("https://llm.example/v1", "PDR_TEST_LLM_KEY", _mock(seen)))
    monkeypatch.setattr("pdr.broker.core.get_transport", lambda p, s=None: broker.TRANSPORTS[p])  # bypass offline swap
    out = llm.llm_json("map_reason", "late delivery", llm.ReasonMap, octx)
    assert out is not None and out.reason_code.value == "late_delivery"
    [req] = seen
    assert str(req.url) == "https://llm.example/v1/chat/completions"
    assert req.headers["authorization"] == f"Bearer {SECRET}"
    body = json.loads(req.content)
    assert body["model"] == "gpt-x" and body["temperature"] == 0
    assert body["response_format"] == {"type": "json_object"}
    assert body["messages"][0]["role"] == "system" and "JSON Schema" in body["messages"][0]["content"]
    assert body["messages"][1]["role"] == "user"
    [row] = ledger.rows(octx.store)
    assert (row.tokens_in, row.tokens_out, row.outcome) == (120, 30, "ok")
    assert SECRET not in row.model_dump_json()


def test_wrong_key_returns_none_and_failed_row(octx, monkeypatch):
    monkeypatch.setenv("PDR_TEST_LLM_KEY", "wrong")
    seen: list = []
    monkeypatch.setitem(broker.TRANSPORTS, "llm", OpenAICompatibleTransport(
        "https://llm.example/v1", "PDR_TEST_LLM_KEY", _mock(seen, 401, {"error": {"message": "bad key"}})))
    monkeypatch.setattr("pdr.broker.core.get_transport", lambda p, s=None: broker.TRANSPORTS[p])
    assert llm.llm_json("map_reason", "late delivery", llm.ReasonMap, octx) is None
    [row] = ledger.rows(octx.store)
    assert row.outcome == "failed" and row.cost_usd == 0


def test_missing_key_returns_none_and_failed_row_without_request(octx, monkeypatch):
    monkeypatch.delenv("PDR_TEST_LLM_KEY", raising=False)
    monkeypatch.delitem(broker.TRANSPORTS, "llm", raising=False)
    monkeypatch.setattr(httpx.Client, "send", lambda *a, **k: pytest.fail("network request made"))
    monkeypatch.setattr("pdr.broker.core.get_transport", lambda p, s=None: broker.TRANSPORTS[p])
    assert llm.llm_json("map_reason", "late delivery", llm.ReasonMap, octx) is None
    assert isinstance(broker.TRANSPORTS["llm"], OpenAICompatibleTransport)
    [row] = ledger.rows(octx.store)
    assert row.outcome == "failed"


def test_cache_key_differs_between_models_and_providers(octx, tmp_path):
    actx = make_ctx(tmp_path / "a.db")
    r1 = llm.build_request("map_reason", "x", llm.ReasonMap, actx)
    actx.settings.providers.llm.model_parse = "claude-other"
    r2 = llm.build_request("map_reason", "x", llm.ReasonMap, actx)
    r3 = llm.build_request("map_reason", "x", llm.ReasonMap, octx)
    keys = {cache_key("llm", llm.endpoint_for(actx), r1), cache_key("llm", llm.endpoint_for(actx), r2),
            cache_key("llm", llm.endpoint_for(octx), r3)}
    assert len(keys) == 3
    assert llm.endpoint_for(actx) != llm.endpoint_for(octx)


def test_doctor_names_provider_never_prints_key(octx, monkeypatch, capsys):
    monkeypatch.setenv("PDR_TEST_LLM_KEY", SECRET)
    checks = doctor.llm_checks(octx.settings)
    text = "\n".join(f"{c.name} {c.status} {c.detail}" for c in checks)
    assert "openai_compatible" in text and "gpt-x" in text and "PDR_TEST_LLM_KEY" in text
    assert "llm prices warn" in text
    assert SECRET not in text
    monkeypatch.delenv("PDR_TEST_LLM_KEY")
    assert any(c.name == "llm key PDR_TEST_LLM_KEY" and c.status == "warn" for c in doctor.llm_checks(octx.settings))


def test_doctor_default_anthropic(tmp_path):
    checks = doctor.llm_checks(make_ctx(tmp_path / "a.db").settings)
    assert checks[0].detail.startswith("anthropic;") and checks[1].name == "llm key ANTHROPIC_API_KEY"


@pytest.mark.parametrize("provider", ["anthropic", "openai_compatible"])
def test_offline_neither_transport_makes_a_request(octx, monkeypatch, provider):
    monkeypatch.setenv("PDR_OFFLINE", "1")
    monkeypatch.setenv("PDR_TEST_LLM_KEY", SECRET)
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    monkeypatch.setattr(httpx.Client, "send", lambda *a, **k: pytest.fail("network request made"))
    monkeypatch.setattr(llm.AnthropicTransport, "_sdk", lambda self: pytest.fail("anthropic sdk used"))
    monkeypatch.delitem(broker.TRANSPORTS, "llm", raising=False)
    octx.settings.providers.llm.provider = provider
    assert llm.llm_json("map_reason", "late delivery", llm.ReasonMap, octx) is None
    rows = ledger.rows(octx.store)
    assert rows and rows[-1].outcome == "failed"


def test_fake_llm_still_works_with_openai_shape(octx, monkeypatch):
    monkeypatch.setitem(broker.TRANSPORTS, "llm", LlmFake(RECORDED))
    assert llm.llm_json("map_reason", "late", llm.ReasonMap, octx) is not None
