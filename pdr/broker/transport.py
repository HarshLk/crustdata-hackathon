"""Transport protocol shared by the real HTTP transports and tests' FakeTransport."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from pdr.models import canonical_json, sha256_hex


@dataclass
class Response:
    """One provider response, stored raw in call_cache before any parsing."""
    status: int
    headers: dict = field(default_factory=dict)
    body: Any = None


class TransportTimeout(Exception):
    """The provider did not answer in time (retried by the broker)."""


class TransportError(Exception):
    """Connection-level failure (retried by the broker)."""


class TransportOffline(TransportError):
    """PDR_OFFLINE=1: no network request is made; the call fails at once without retries."""


class Transport(Protocol):
    def send(self, endpoint: str, request: dict, *, cache_key: str, timeout: float) -> Response: ...


def cache_key(provider: str, endpoint: str, request: dict) -> str:
    """sha256(provider | endpoint | canonical request)."""
    return sha256_hex(f"{provider}|{endpoint}|{canonical_json(request)}")


def header(headers: dict, name: str) -> Any:
    """Case-insensitive header lookup."""
    low = name.lower()
    for k, v in (headers or {}).items():
        if str(k).lower() == low:
            return v
    return None
