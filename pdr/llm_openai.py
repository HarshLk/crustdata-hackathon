"""OpenAI-compatible chat-completions transport (httpx) and request shape for providers.llm.provider=openai_compatible."""
from __future__ import annotations

import os
from typing import Any

import httpx

from pdr.broker.transport import Response, TransportError, TransportTimeout

ENDPOINT = "/chat/completions"
DEFAULT_KEY_ENV = {"anthropic": "ANTHROPIC_API_KEY", "openai_compatible": "OPENAI_API_KEY"}


def key_env(llm_cfg: Any) -> str:
    """Name of the env var holding the model key (never its value)."""
    name = (getattr(llm_cfg, "api_key_env", "") or "").strip()
    return name or DEFAULT_KEY_ENV.get(getattr(llm_cfg, "provider", "anthropic"), "ANTHROPIC_API_KEY")


def to_chat_request(anthropic_request: dict) -> dict:
    """Messages API body -> chat-completions body (system first, JSON output)."""
    msgs = [{"role": "system", "content": anthropic_request.get("system", "")}]
    msgs += [dict(m) for m in anthropic_request.get("messages", [])]
    return {"model": anthropic_request["model"], "max_tokens": anthropic_request.get("max_tokens"),
            "temperature": anthropic_request.get("temperature", 0), "messages": msgs,
            "response_format": {"type": "json_object"}}


def response_text(body: Any) -> str | None:
    """choices[0].message.content, or None when the body is not chat-completions shaped."""
    if isinstance(body, dict) and isinstance(body.get("choices"), list) and body["choices"]:
        msg = body["choices"][0].get("message") or {}
        c = msg.get("content")
        return c if isinstance(c, str) else None
    return None


class OpenAICompatibleTransport:
    """POST {base_url}/chat/completions with a bearer key from the named env var (broker Transport protocol)."""

    def __init__(self, base_url: str, api_key_env: str, http_transport: Any = None):
        self.base_url = (base_url or "").rstrip("/")
        self.api_key_env = api_key_env
        self._http_transport = http_transport  # tests: httpx.MockTransport

    def send(self, endpoint: str, request: dict, *, cache_key: str | None = None, timeout: float = 60.0) -> Response:
        key = os.environ.get(self.api_key_env, "").strip()
        if not key:  # no request leaves the machine; not retryable
            return Response(401, {}, {"error": f"{self.api_key_env} not set"})
        if not self.base_url:
            return Response(400, {}, {"error": "providers.llm.base_url not set"})
        headers = {"Authorization": f"Bearer {key}", "content-type": "application/json"}
        try:
            with httpx.Client(transport=self._http_transport, timeout=timeout) as c:
                r = c.post(self.base_url + (endpoint or ENDPOINT), json=request, headers=headers)
        except httpx.TimeoutException as e:
            raise TransportTimeout(type(e).__name__) from None
        except httpx.HTTPError as e:
            raise TransportError(type(e).__name__) from None
        try:
            body: Any = r.json()
        except ValueError:
            body = {"error": r.text[:2000]}
        if r.status_code >= 400:
            body = {"error": (body.get("error") if isinstance(body, dict) else None) or f"http {r.status_code}"}
        keep = {k: v for k, v in r.headers.items() if k.lower() in ("retry-after", "content-type")}
        return Response(r.status_code, keep, body)


__all__ = ["OpenAICompatibleTransport", "to_chat_request", "response_text", "key_env", "ENDPOINT", "DEFAULT_KEY_ENV"]
