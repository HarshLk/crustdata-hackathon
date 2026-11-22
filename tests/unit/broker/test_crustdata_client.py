"""Crustdata client and redirect resolution against recorded responses."""
import json
from datetime import datetime, timedelta, timezone

from pdr.broker import ledger
from pdr.evidence import crustdata, redirect
from tests.fakes.transport import CASSETTES_ROOT

IST = timezone(timedelta(hours=5, minutes=30))
RAW = CASSETTES_ROOT / "raw"


def test_search_never_sends_location_and_uses_unix_seconds(ctx, fake):
    fake.add({"endpoint": "/web/search/live", "query_contains": "Kini", "response_file": "raw/s3.json"})
    fake.add({"endpoint": "/web/search/live", "query_contains": "Odisha", "response_file": "raw/s1neg.json"})
    res = crustdata.search("NH-48 Kini blockade", datetime(2026, 11, 3), datetime(2026, 11, 7, 23, 59, 59, tzinfo=IST),
                           ctx)
    assert len(res) == 10 and {"title", "url", "snippet"} <= set(res[0])
    assert crustdata.search("Odisha bandh", datetime(2026, 10, 10), datetime(2026, 10, 11), ctx) == []
    assert ctx.last_call_failed is False
    for c in fake.calls:
        assert "location" not in c["request"]
        assert c["request"]["sources"] == ["news"]
    assert fake.calls[0]["request"]["start_date"] == int(datetime(2026, 11, 3, tzinfo=IST).timestamp())
    assert [e.units for e in ledger.rows(ctx.store)] == [1, 0]  # empty search is free


def test_search_failure_sets_flag(ctx, fake):
    ctx.settings.providers.crustdata.retry_limit = 0
    fake.faults.append("http_500_once")
    assert crustdata.search("Kini blockade", datetime(2026, 11, 3), datetime(2026, 11, 7), ctx) == []
    assert ctx.last_call_failed is True


def test_fetch_batches_of_ten_and_human_mode(ctx, fake):
    items = json.loads((RAW / "fetch2_plain.json").read_text())
    for it in items:
        fake.add({"endpoint": "/web/enrich/live", "url": it["url"], "response": it})
    urls = [it["url"] for it in items]
    for i in range(9):  # make 12 URLs -> 2 batches
        u = f"https://example.in/a{i}"
        urls.append(u)
        fake.add({"endpoint": "/web/enrich/live", "url": u, "response": {"url": u, "success": True,
                                                                          "content": "x", "title": "t"}})
    ctx.settings.budget.per_deduction_credits = 50
    out = crustdata.fetch(urls, ctx, human_mode=True)
    assert [o["url"] for o in out] == urls
    assert [len(c["request"]["urls"]) for c in fake.calls] == [10, 2]
    assert all(c["request"]["human_mode"] is True for c in fake.calls)
    assert out[0]["content"].startswith("<") and isinstance(out[0]["success"], bool)
    assert sum(r.credits for r in ctx.last_calls) == 12


def test_identify(ctx, fake):
    fake.add({"endpoint": "/company/identify", "names_contain": "Indian Oil", "response_file": "raw/identify.json"})
    out = crustdata.identify(["Indian Oil Corporation", "TCI Freight"], ctx)
    assert out[0]["matches"][0]["confidence_score"] == 1.0


def test_saved_goto_links_resolve_against_recorded_responses(ctx, fake):
    fake.add_resolved_file(RAW / "resolved.json")
    rows = [r for r in json.loads((RAW / "resolved.json").read_text()) if r["kind"] == "goto"][:5]
    for r in rows:
        assert redirect.resolve(r["url"], ctx) == r["resolved"]
    assert redirect.resolve("https://theprint.in/x", ctx) == "https://theprint.in/x"
    assert len(fake.calls) == 5
    assert redirect.resolve(rows[0]["url"], ctx) == rows[0]["resolved"]  # cached, free
    assert len(fake.calls) == 5
    assert all(e.units == 0 for e in ledger.rows(ctx.store))


def test_goto_back_to_google_is_unresolved(ctx, fake):
    fake.add_redirects({"https://www.google.com/goto?url=abc": "https://www.google.com/sorry/index"})
    assert redirect.resolve("https://www.google.com/goto?url=abc", ctx) is None
