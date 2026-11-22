"""Real HTTP transports (httpx): "crustdata" (POST JSON, bearer key) and "web" (plain GET/HEAD, free)."""
from __future__ import annotations

import html
import os
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

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


_REFRESH = re.compile(r'content="(\d+);\s*url=([^"]+)"', re.I)
MAX_INTERSTITIAL_WAIT = 90.0  # seconds; fresh google.com/goto links show a timed "Verifying" page first


class WebHttp:
    """request = {"method": "GET"|"HEAD", "url": ..., "follow_redirects": false}.

    Freshly minted google.com/goto links answer 200 with a meta-refresh interstitial and cookies; after
    the stated delay the refresh URL (with those cookies) gives the 302. Older links give the 302 at once.
    """

    def __init__(self, sleep=time.sleep, max_wait: float = MAX_INTERSTITIAL_WAIT, transport=None):
        self.client = httpx.Client(headers={"User-Agent": UA}, transport=transport)
        self.sleep = sleep
        self.max_wait = max_wait

    def _get(self, method: str, url: str, follow: bool, timeout: float) -> httpx.Response:
        return _wrap(lambda: self.client.request(method, url, timeout=timeout, follow_redirects=follow))

    def send(self, endpoint: str, request: dict, *, cache_key: str = "", timeout: float = 20) -> Response:
        method = (request.get("method") or endpoint or "GET").upper()
        follow = bool(request.get("follow_redirects", False))
        url = request["url"]
        r = self._get(method, url, follow, timeout)
        host = urlparse(url).netloc.lower()
        m = _REFRESH.search(r.text[:4000]) if r.status_code == 200 and "google." in host else None
        if m and float(m.group(1)) <= self.max_wait:
            self.sleep(float(m.group(1)) + 1)
            r = self._get(method, urljoin(url, html.unescape(m.group(2))), follow, timeout)
            if r.status_code >= 400:  # refresh target refused: the original link should now redirect
                r = self._get(method, url, follow, timeout)
        keep = {k: v for k, v in r.headers.items() if k.lower() in ("location", "content-type", "last-modified")}
        body = "" if method == "HEAD" or 300 <= r.status_code < 400 else r.text
        return Response(r.status_code, keep, body)


_WEB: WebHttp | None = None


def real_transport(provider: str, settings: Any = None):
    if provider == "crustdata":
        cfg = settings.providers.crustdata if settings is not None else None
        return CrustdataHttp(getattr(cfg, "base_url", "https://api.crustdata.com"),
                             getattr(cfg, "api_version", "2025-11-01"))
    if provider == "web":
        global _WEB
        if _WEB is None:
            _WEB = WebHttp()  # one client per process, so Google's cookies are reused
        return _WEB
    raise TransportError(f"no transport registered for provider {provider!r}")
