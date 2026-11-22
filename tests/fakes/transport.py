"""FakeTransport: offline provider responses from cassettes, with injectable faults.

Cassette entries (JSON files at the top level of cassette_dir; a file may hold one entry,
a list of entries, or {"entries": [...]}):
  exact:  {"cache_key": "<64 hex>", "status": 200, "headers": {...}, "body": ...}
  loose:  {"endpoint": "/web/search/live", "query_contains": "Kini", "response": {...}}
          other filters: "provider", "url" / "url_contains" (fetch or web), "names_contain",
          "request_contains": {subset of the request}. "response_file": "raw/s3.json" loads the body
          from a file (relative to the cassette file, then cassette_dir, then tests/cassettes/).
  For "/web/enrich/live", an entry whose response is a single item dict is used per URL, so a
  batch fetch is assembled from per-URL entries.
Faults (optionally scoped "name@<endpoint substring>"): block_first, http_429_once, http_500_once,
timeout_once, undated, slow:<seconds>.
"""
from __future__ import annotations

import copy
import json
import re
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

from pdr.broker.transport import Response, TransportTimeout, cache_key as make_cache_key

CASSETTES_ROOT = Path(__file__).resolve().parent.parent / "cassettes"
SEARCH, FETCH, IDENTIFY = "/web/search/live", "/web/enrich/live", "/company/identify"

BLOCK_PAGE = ("<html><head><title>Google</title></head><body>Verifying your request. "
              "You'll be able to continue in a few seconds. Don't refresh this page.</body></html>")
_DATE_RES = [
    r'"datePublished"\s*:\s*"[^"]*"', r'"dateModified"\s*:\s*"[^"]*"', r'"uploadDate"\s*:\s*"[^"]*"',
    r'<meta[^>]+(?:published_time|modified_time|pubdate|publish-date|date)[^>]*>',
    r'<time[^>]*>', r'datetime="[^"]*"',
]


class CassetteMiss(LookupError):
    """No recorded response for this request (strict mode)."""


class FakeTransport:
    def __init__(self, cassette_dir: Any = None, faults: list[str] | None = None, *,
                 strict: bool = True, provider: str | None = None, entries: list[dict] | None = None):
        dirs = cassette_dir if isinstance(cassette_dir, (list, tuple)) else ([cassette_dir] if cassette_dir else [])
        self.cassette_dirs = [str(Path(d)) for d in dirs]
        self.faults = list(faults or [])
        self.strict = strict
        self.provider = provider
        self.calls: list[dict] = []
        self._used: set[str] = set()
        self._extra: list[dict] = []
        self.by_key: dict[str, dict] = {}
        self.loose: list[dict] = []
        for d in self.cassette_dirs:
            self.load_dir(d)
        for e in entries or []:
            self.add(e)

    # ------------------------------------------------------------------ loading
    def load_dir(self, d: str | Path) -> None:
        p = Path(d)
        if not p.is_dir():
            return
        for f in sorted(p.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            items = data.get("entries") if isinstance(data, dict) and "entries" in data else data
            for e in items if isinstance(items, list) else [items]:
                if isinstance(e, dict):
                    self._index(e, f.parent)

    def _index(self, e: dict, base: Path | None = None) -> None:
        e = dict(e)
        if "response_file" in e:
            e["_base"] = str(base) if base else None
        if e.get("cache_key"):
            self.by_key[e["cache_key"]] = e
        elif "endpoint" in e or "url" in e or "url_contains" in e:
            self.loose.append(e)

    def add(self, entry: dict) -> "FakeTransport":
        """Add one cassette entry in memory (kept in spec() so a detached child sees it too)."""
        self._extra.append(entry)
        self._index(entry, CASSETTES_ROOT)
        return self

    def add_redirects(self, mapping: dict[str, str], status: int = 302) -> "FakeTransport":
        """goto link -> Location, for provider "web" (redirect.resolve)."""
        for src, loc in mapping.items():
            self.add({"provider": "web", "url": src, "status": status, "headers": {"Location": loc}, "response": ""})
        return self

    def add_resolved_file(self, path: str | Path = CASSETTES_ROOT / "raw" / "resolved.json") -> "FakeTransport":
        """Load the recorded goto -> publisher table (crustdata_test raw/resolved.json format)."""
        rows = json.loads(Path(path).read_text(encoding="utf-8"))
        return self.add_redirects({r["url"]: r["resolved"] for r in rows
                                   if r.get("kind") == "goto" and r.get("resolved")})

    def spec(self) -> dict:
        """How a detached child process rebuilds this transport."""
        return {"factory": "tests.fakes.transport:FakeTransport",
                "kwargs": {"cassette_dir": self.cassette_dirs, "faults": self.faults, "strict": self.strict,
                           "provider": self.provider, "entries": self._extra}}

    # ------------------------------------------------------------------ faults
    def _fault(self, name: str, endpoint: str) -> str | None:
        for f in self.faults:
            base, _, scope = f.partition("@")
            if (base == name or base.startswith(name + ":")) and (not scope or scope in endpoint):
                return f
        return None

    def _once(self, name: str, endpoint: str) -> bool:
        f = self._fault(name, endpoint)
        if f and f not in self._used:
            self._used.add(f)
            return True
        return False

    # ------------------------------------------------------------------ send
    def send(self, endpoint: str, request: dict, *, cache_key: str | None = None, timeout: float = 60) -> Response:
        key = cache_key or make_cache_key(self.provider or "crustdata", endpoint, request)
        self.calls.append({"endpoint": endpoint, "request": copy.deepcopy(request), "cache_key": key})
        slow = self._fault("slow", endpoint)
        if slow:
            time.sleep(float(slow.split("@")[0].split(":", 1)[1]))
        if self._once("timeout_once", endpoint):
            raise TransportTimeout("fake timeout")
        if self._once("http_429_once", endpoint):
            return Response(429, {"retry-after": "0"}, {"error": "rate limited (fake)"})
        if self._once("http_500_once", endpoint):
            return Response(500, {}, {"error": "server error (fake)"})
        resp = self._lookup(endpoint, request, key)
        if endpoint == FETCH and isinstance(resp.body, list):
            if self._once("block_first", endpoint):
                resp.body = [dict(it, success=True, title="Google", content=BLOCK_PAGE, http_status="200")
                             for it in resp.body]
            if self._fault("undated", endpoint):
                resp.body = [dict(it, content=_strip_dates(it.get("content") or "")) for it in resp.body]
        if not any(k.lower() == "x-credits-used" for k in resp.headers):
            resp.headers["x-credits-used"] = str(_default_credits(endpoint, request, resp))
        return resp

    def _lookup(self, endpoint: str, request: dict, key: str) -> Response:
        if key in self.by_key:
            return self._response(self.by_key[key])
        if endpoint == FETCH and request.get("urls"):
            items = []
            for u in request["urls"]:
                e = next((e for e in self.loose if self._matches(e, endpoint, {"urls": [u]}, per_url=True)), None)
                if e is None:
                    break
                body = self._response(e).body
                items.append(dict(body, url=body.get("url") or u))
            else:
                return Response(200, {}, items)
        for e in self.loose:
            if self._matches(e, endpoint, request):
                return self._response(e)
        if self.strict:
            raise CassetteMiss(f"no cassette for {endpoint} {json.dumps(request, default=str)[:200]} key={key}")
        return Response(404, {}, {"error": "no cassette (fake)"})

    def _matches(self, e: dict, endpoint: str, request: dict, per_url: bool = False) -> bool:
        if e.get("provider") and self.provider and e["provider"] != self.provider:
            return False
        if e.get("endpoint") and e["endpoint"] != endpoint:
            return False
        body_is_item = isinstance(e.get("response"), dict) or e.get("_item")
        if endpoint == FETCH and per_url != bool(body_is_item and ("url" in e or "url_contains" in e)):
            return False
        q = str(request.get("query", ""))
        if "query_contains" in e and e["query_contains"].lower() not in q.lower():
            return False
        urls = [str(u) for u in request.get("urls", [])] + ([str(request["url"])] if request.get("url") else [])
        if "url" in e and e["url"] not in urls:
            return False
        if "url_contains" in e and not any(e["url_contains"] in u for u in urls):
            return False
        if "names_contain" in e and not any(e["names_contain"].lower() in str(n).lower()
                                            for n in request.get("names", [])):
            return False
        for k, v in (e.get("request_contains") or {}).items():
            if request.get(k) != v:
                return False
        return True

    def _response(self, e: dict) -> Response:
        if "response_file" in e:
            body = _load_file(e["response_file"], e.get("_base"), self.cassette_dirs)
        else:
            body = e["response"] if "response" in e else e.get("body")
        return Response(int(e.get("status", 200)), dict(e.get("headers") or {}), copy.deepcopy(body))


def _load_file(name: str, base: str | None, dirs: list[str]) -> Any:
    for d in [base, *dirs, str(CASSETTES_ROOT)]:
        if d and (Path(d) / name).is_file():
            return json.loads((Path(d) / name).read_text(encoding="utf-8"))
    raise CassetteMiss(f"response_file not found: {name}")


def _strip_dates(html: str) -> str:
    for pat in _DATE_RES:
        html = re.sub(pat, "", html, flags=re.I)
    return html


def _default_credits(endpoint: str, request: dict, resp: Response) -> Decimal | int:
    if resp.status >= 400:
        return 0
    body = resp.body
    if endpoint == SEARCH:
        return 1 if isinstance(body, dict) and body.get("results") else 0
    if endpoint == FETCH:
        return len(request.get("urls") or [])
    if endpoint == IDENTIFY and isinstance(body, list):
        return Decimal("0.2") * sum(1 for it in body if isinstance(it, dict) and it.get("matches"))
    return 0


def save_cassette(directory: str | Path, provider: str, endpoint: str, request: dict, response: Response,
                  name: str | None = None) -> Path:
    """Write an exact (cache_key) cassette entry; returns the file path."""
    key = make_cache_key(provider, endpoint, request)
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{name or key}.json"
    path.write_text(json.dumps({"cache_key": key, "provider": provider, "endpoint": endpoint, "request": request,
                                "status": response.status, "headers": response.headers, "body": response.body},
                               ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return path


__all__ = ["FakeTransport", "CassetteMiss", "save_cassette", "BLOCK_PAGE", "CASSETTES_ROOT"]
