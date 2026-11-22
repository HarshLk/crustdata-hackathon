"""Broker side of redirect resolution: what is cached, forget(), and fetch-item matching (a3)."""
from __future__ import annotations

import sqlite3

from pdr import broker
from pdr.broker.core import cacheable
from pdr.broker.transport import Response
from pdr.evidence import crustdata

GOTO = "https://www.google.com/goto?url=CAES1&gsessionid=x"


def test_only_a_redirect_to_a_publisher_is_cacheable():
    req = {"method": "GET", "url": GOTO, "follow_redirects": False}
    assert cacheable("web", req, Response(302, {"location": "https://p.in/a"}, ""))
    assert not cacheable("web", req, Response(200, {}, "<title>Google</title>Verifying your request"))
    assert not cacheable("web", req, Response(302, {"location": "https://www.google.com/sorry/index"}, ""))
    assert not cacheable("web", req, Response(302, {"location": "/sorry/index?continue=x"}, ""))
    assert not cacheable("web", req, Response(302, {}, ""))


def test_forget_drops_the_cached_response_but_keeps_the_ledger(ctx, fake):
    fake.add({"endpoint": "/web/enrich/live", "url": "https://p.in/a", "response": {"title": "A", "content": "x"}})
    crustdata.fetch(["https://p.in/a"], ctx)
    db = sqlite3.connect(ctx.store.path)
    assert db.execute("SELECT count(*) FROM call_cache").fetchone()[0] == 1
    assert broker.forget(ctx, "crustdata", "/web/enrich/live", {"urls": ["https://p.in/a"]})
    assert db.execute("SELECT count(*) FROM call_cache").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM ledger").fetchone()[0] == 1
    crustdata.fetch(["https://p.in/a"], ctx)
    assert len(fake.calls) == 2


def test_fetch_items_matched_by_url_when_the_response_is_reordered(ctx, fake):
    a, b, c = "https://p.in/a", "https://p.in/b", "https://p.in/c"
    fake.add({"endpoint": "/web/enrich/live", "request_contains": {"urls": [a, b, c]}, "response": [
        {"url": c, "success": True, "title": "C", "content": "c"},
        {"url": None, "success": False, "title": None, "content": None},
        {"url": a, "success": True, "title": "A", "content": "a"}]})
    got = {it["url"]: it for it in crustdata.fetch([a, b, c], ctx)}
    assert got[a]["title"] == "A" and got[c]["title"] == "C"
    assert got[b]["success"] is False  # the null-url failure goes to the one unmatched request
    assert got[a]["batch_request"] == {"urls": [a, b, c]}
