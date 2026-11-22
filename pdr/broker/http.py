"""Real HTTP transports (httpx): "crustdata" (POST JSON, bearer key) and "web" (plain GET/HEAD, free)."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx

from pdr.broker.transport import Response, TransportError, TransportTimeout

DEFAULT_ENV_FILE = "/projects/2022B4AA1300P/hackathon/harsh/.env"
UA = "Mozilla/5.0"  # the bare UA got 302s for 68 of 68 goto links; a full Chrome UA gets the "Verifying" page


def load_env_key(name: str = "CRUSTDATA_API_KEY") -> str | None:
    """From the environment, else from the .env file ($PDR_ENV_FILE or the default); never logged."""
    if os.environ.get(name):
        return os.environ[name]
    path = Path(os.environ.get("PDR_ENV_FILE") or DEFAULT_ENV_FILE)
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            k, sep, v = line.strip().partition("=")
            if sep and k.strip() == name:
                os.environ[name] = v.strip().strip('"').strip("'")
                return os.environ[name]
    except OSError:
        return None
    return None


def _wrap(fn):
    try:
        return fn()
    except httpx.TimeoutException as e:
        raise TransportTimeout(type(e).__name__) from None
    except httpx.HTTPError as e:
        raise TransportError(type(e).__name__) from None


class CrustdataHttp:
    def __init__(self, base_url: str, api_version: str):
        self.base_url = base_url.rstrip("/")
        self.api_version = api_version

    def send(self, endpoint: str, request: dict, *, cache_key: str = "", timeout: float = 60) -> Response:
        key = load_env_key()
        if not key:
            raise TransportError("CRUSTDATA_API_KEY not set")
        headers = {"Authorization": f"Bearer {key}", "x-api-version": self.api_version,
                   "content-type": "application/json"}
        r = _wrap(lambda: httpx.post(self.base_url + endpoint, json=request, headers=headers, timeout=timeout))
        try:
            body: Any = r.json()
        except ValueError:
            body = {"error": r.text[:2000]}
        keep = {k: v for k, v in r.headers.items() if k.lower() in ("x-credits-used", "retry-after", "content-type")}
        return Response(r.status_code, keep, body)


class WebHttp:
    """request = {"method": "GET"|"HEAD", "url": ..., "follow_redirects": false}."""

    def send(self, endpoint: str, request: dict, *, cache_key: str = "", timeout: float = 20) -> Response:
        method = (request.get("method") or endpoint or "GET").upper()
        r = _wrap(lambda: httpx.request(method, request["url"], headers={"User-Agent": UA}, timeout=timeout,
                                        follow_redirects=bool(request.get("follow_redirects", False))))
        keep = {k: v for k, v in r.headers.items() if k.lower() in ("location", "content-type", "last-modified")}
        body = "" if method == "HEAD" or 300 <= r.status_code < 400 else r.text
        return Response(r.status_code, keep, body)


def real_transport(provider: str, settings: Any = None):
    if provider == "crustdata":
        cfg = settings.providers.crustdata if settings is not None else None
        return CrustdataHttp(getattr(cfg, "base_url", "https://api.crustdata.com"),
                             getattr(cfg, "api_version", "2025-11-01"))
    if provider == "web":
        return WebHttp()
    raise TransportError(f"no transport registered for provider {provider!r}")
