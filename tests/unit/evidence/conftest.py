"""Shared fixtures for the evidence lane tests."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from pdr.contracts import RunContext
from pdr.settings import load_settings
from pdr.store_mem import MemStore

ROOT = Path(__file__).resolve().parents[3]
CASSETTES = ROOT / "tests" / "cassettes"
PAGES = CASSETTES / "pages"
IST = timezone(timedelta(hours=5, minutes=30))


@pytest.fixture
def settings():
    return load_settings(ROOT / "settings.yaml")


@pytest.fixture
def ctx(settings):
    return RunContext(run_id="t", dispute_key=None, settings=settings, store=MemStore(),
                      deadline=datetime.now(timezone.utc) + timedelta(hours=1))

import json

RAW = CASSETTES / "raw"
FILLER = "".join(
    f"<p>Paragraph {i}: the district administration said meeting number {i} reviewed arrangements with "
    f"representatives of group {i}, and officials listed {i * 3} essential services, including hospitals, "
    f"that remained exempt under order {i * 7}.</p>" for i in range(1, 14))


def raw_item(fname: str, url_part: str) -> dict:
    """One saved Crustdata fetch item (url, success, title, content, timestamp) from tests/cassettes/raw."""
    for it in json.loads((RAW / fname).read_text(encoding="utf-8")):
        if url_part in (it.get("url") or "") or url_part in (it.get("title") or ""):
            return it
    raise KeyError(url_part)


def make_item(url: str, title: str, body_paras: list[str], published: str | None) -> dict:
    """A synthetic fetched article (long enough to pass the page check)."""
    meta = f'<meta property="article:published_time" content="{published}">' if published else ""
    body = "".join(f"<p>{p}</p>" for p in body_paras)
    html = (f"<html><head><title>{title}</title>{meta}</head><body><article><h1>{title}</h1>{body}{FILLER}"
            f"</article></body></html>")
    return {"url": url, "success": True, "title": title, "content": html, "timestamp": 1795339868}


class FakeClient:
    """Stands in for pdr.evidence.crustdata: search by query substring, fetch by URL, ledger every call."""

    def __init__(self, searches: dict[str, list[dict]], pages: dict[str, dict], human_pages: dict[str, dict] | None = None):
        self.searches, self.pages, self.human_pages = searches, pages, human_pages or {}
        self.ledger: list[tuple] = []

    def search(self, query, start, end, ctx, *, step="search"):
        self.ledger.append(("search", query))
        for k, v in self.searches.items():
            if k.lower() in query.lower():
                return v
        return []

    def fetch(self, urls, ctx, *, human_mode=False, step="fetch"):
        src = self.human_pages if human_mode else self.pages
        out = []
        for u in urls:
            self.ledger.append(("fetch_human" if human_mode else "fetch", u))
            out.append(dict(src.get(u) or {"url": u, "success": False, "title": "", "content": ""}, url=u))
        return out


@pytest.fixture
def use_client(monkeypatch):
    def _install(client):
        from pdr.evidence import scout
        monkeypatch.setattr(scout, "_crust", lambda: client)
        monkeypatch.setattr(scout, "_resolve", lambda url, ctx: url)
        return client
    return _install
