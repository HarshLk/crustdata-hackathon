"""google.com/goto resolution ladder (docs/redirect_resolution.md) from recorded responses (22 Nov 2026)."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from pdr import broker
from pdr.broker import ledger
from pdr.contracts import EvidenceRequest, RunContext
from pdr.evidence import find_evidence, redirect
from tests.fakes.transport import FakeTransport

from .conftest import CASSETTES, make_item

RED = CASSETTES / "redirect"
IST = timezone(timedelta(hours=5, minutes=30))
FETCH, SEARCH = "/web/enrich/live", "/web/search/live"


def _load(name):
    return json.loads((RED / f"{name}.json").read_text(encoding="utf-8"))


def _interstitial() -> dict:
    """Google's "Verifying your request" page as Crustdata fetched it (crustdata_test raw/fetch0.json)."""
    items = json.loads((CASSETTES / "raw" / "fetch0.json").read_text(encoding="utf-8"))
    return next(it for it in items if it.get("title") == "Google")


FETCHED = _load("fetchHM_kini")  # six goto links fetched with human_mode: 4 articles, 2 CloudFront blocks
NEWS = _load("newsHM_kini")
TRUTH = {r["goto"]: r["location"] for r in _load("truth")}
GOTO_ARTICLE = FETCHED["request"]["urls"][0]  # chinimandi "continue highway blockade at Kini"
GOTO_BLOCKED = FETCHED["request"]["urls"][2]  # indianexpress, 403 from CloudFront


@pytest.fixture
def fake(settings, monkeypatch, tmp_path):
    settings.providers.paidcall_mode = "inline"
    settings.budget.run_cost_credits = 300
    t = FakeTransport(strict=False)
    monkeypatch.setitem(broker.TRANSPORTS, "crustdata", t)
    monkeypatch.setitem(broker.TRANSPORTS, "web", t)
    monkeypatch.delitem(broker.TRANSPORTS, "llm", raising=False)
    return t


@pytest.fixture
def fctx(settings, tmp_path):
    from pdr.store import SqliteStore
    store = SqliteStore(tmp_path / "state.db")
    return RunContext(run_id="t", dispute_key=None, settings=settings, store=store,
                      deadline=datetime.now(timezone.utc) + timedelta(hours=1))


def _cached_rows(ctx) -> int:
    path = getattr(ctx.store, "path", ":memory:")
    if path == ":memory:":
        pytest.skip("needs a file store to inspect the cache")
    return sqlite3.connect(path).execute("SELECT count(*) FROM call_cache").fetchone()[0]


def _interstitial_web(fake, url):
    fake.add({"provider": "web", "url": url, "status": 200, "headers": {"content-type": "text/html"},
              "response": _interstitial()["content"]})


# ----------------------------------------------------------------------------- rung a: direct

def test_direct_publisher_link_used_as_is_without_a_call(fake, fctx):
    r = redirect.resolve_free("https://www.outlookindia.com/national/x-1", fctx)
    assert (r.url, r.rung) == ("https://www.outlookindia.com/national/x-1", "direct") and fake.calls == []


# ----------------------------------------------------------------------------- rung c: Location header

def test_location_rung_resolves_and_caches(fake, fctx):
    fake.add_redirects({GOTO_ARTICLE: TRUTH[GOTO_ARTICLE]})
    assert redirect.resolve(GOTO_ARTICLE, fctx) == TRUTH[GOTO_ARTICLE]
    assert redirect.resolve(GOTO_ARTICLE, fctx) == TRUTH[GOTO_ARTICLE]
    assert len(fake.calls) == 1  # second time from cache
    rows = ledger.rows(fctx.store)
    assert [r.step for r in rows] == ["resolve", "resolve"] and rows[1].cache_hit


def test_interstitial_200_is_not_resolved_and_not_cached(fake, fctx):
    _interstitial_web(fake, GOTO_ARTICLE)
    assert redirect.resolve(GOTO_ARTICLE, fctx) is None and fctx.last_call_failed
    assert redirect.resolve(GOTO_ARTICLE, fctx) is None
    assert len(fake.calls) == 2 and _cached_rows(fctx) == 0  # asked again, never cached


def test_redirect_to_google_sorry_page_is_a_failure_and_not_cached(fake, fctx):
    fake.add_redirects({GOTO_ARTICLE: "https://www.google.com/sorry/index?continue=x"})
    assert redirect.resolve(GOTO_ARTICLE, fctx) is None
    assert redirect.resolve(GOTO_ARTICLE, fctx) is None
    assert len(fake.calls) == 2 and _cached_rows(fctx) == 0


def test_location_rung_is_one_short_attempt(settings):
    from pdr.broker.core import provider_cfg
    cfg = provider_cfg(settings, "web")
    assert cfg["retry_limit"] == 0 and cfg["timeout_seconds"] <= 8
    assert settings.providers.web.interstitial_wait_seconds == 0


# ----------------------------------------------------------------------------- rung d: Crustdata fetch

def test_canonical_url_from_recorded_pages_matches_the_location_header():
    """On the 11 recorded pages that carry one, canonical/og:url equals the 302 Location (truth.json)."""
    n = 0
    for name in ("fetchHM_kini",):
        for it in _load(name)["body"]:
            c = redirect.canonical_url(it.get("content") or "")
            if c:
                n += 1
                assert c.split("?")[0].rstrip("/") == TRUTH[it["url"]].split("?")[0].rstrip("/")
    assert n == 4
    assert redirect.canonical_url(_interstitial()["content"]) is None  # the check page names no destination


def test_canonical_url_rejects_google_and_homepages():
    assert redirect.canonical_url('<link rel="canonical" href="https://www.google.com/goto?url=x">') is None
    assert redirect.canonical_url('<link rel="canonical" href="https://news.example.in/">') is None
    assert redirect.canonical_url('<meta content="https://p.in/a/b" property="og:url">') == "https://p.in/a/b"


def test_crustdata_fetch_rung_cites_canonical_and_keeps_the_page(fake, fctx):
    fctx.settings.evidence.redirect_fetch_probe_first = False
    for it in FETCHED["body"]:
        fake.add({"endpoint": FETCH, "url": it["url"], "response": it})
    out = redirect.resolve_via_fetch([GOTO_ARTICLE, GOTO_BLOCKED], fctx)
    ok, bad = out
    assert ok.rung == "crustdata_fetch" and ok.url == TRUTH[GOTO_ARTICLE].split("?")[0]
    assert ok.page.url == ok.url and not ok.page.blocked and ok.page.published_at is not None
    assert ok.page.human_mode
    assert bad.url is None and bad.rung == "unresolved_redirect" and bad.spent_fetch
    req = [c["request"] for c in fake.calls if c["endpoint"] == FETCH]
    assert req == [{"urls": [GOTO_ARTICLE, GOTO_BLOCKED], "human_mode": True}]  # one paid call for both
    assert [r.step for r in ledger.rows(fctx.store)] == ["resolve_fetch"]


def test_crustdata_fetch_of_interstitial_is_unresolved_and_forgotten(fake, fctx):
    item = dict(_interstitial(), url=GOTO_ARTICLE)
    fake.add({"endpoint": FETCH, "url": GOTO_ARTICLE, "response": item})
    (r,) = redirect.resolve_via_fetch([GOTO_ARTICLE], fctx)
    assert r.url is None and "Google interstitial" in r.detail and r.spent_fetch
    assert _cached_rows(fctx) == 0  # not kept as the answer
    redirect.resolve_via_fetch([GOTO_ARTICLE], fctx)
    assert len([c for c in fake.calls if c["endpoint"] == FETCH]) == 2  # a later run asks again
    rows = ledger.rows(fctx.store)
    assert [r.cache_hit for r in rows] == [False, False]


def test_probe_first_pays_for_no_more_links_when_google_gates(fake, fctx):
    gotos = FETCHED["request"]["urls"][:4]
    for u in gotos:
        fake.add({"endpoint": FETCH, "url": u, "response": dict(_interstitial(), url=u)})
    out = redirect.resolve_via_fetch(gotos, fctx)
    assert [r.url for r in out] == [None] * 4 and all(r.gated for r in out)
    assert [r.paid for r in out] == [True, False, False, False]
    assert [c["request"]["urls"] for c in fake.calls] == [gotos[:1]]  # one credit, not four


def test_probe_first_then_the_rest_in_one_batch(fake, fctx):
    gotos = FETCHED["request"]["urls"][:4]
    for it in FETCHED["body"]:
        fake.add({"endpoint": FETCH, "url": it["url"], "response": it})
    out = redirect.resolve_via_fetch(gotos, fctx)
    assert [c["request"]["urls"] for c in fake.calls] == [gotos[:1], gotos[1:]]
    assert sum(r.paid for r in out) == 4 and sum(r.url is not None for r in out) == 2


def test_reported_final_url_is_used_when_crustdata_gives_one(fake, fctx):
    it = dict(FETCHED["body"][0], url="https://www.chinimandi.com/some-final-article/")
    fake.add({"endpoint": FETCH, "url": GOTO_ARTICLE, "response": it})
    (r,) = redirect.resolve_via_fetch([GOTO_ARTICLE], fctx)
    assert r.url == "https://www.chinimandi.com/some-final-article/"


# ----------------------------------------------------------------------------- the scout

def _kini_request():
    return EvidenceRequest(route_places=["Pune", "Satara", "Karad", "Kini", "Kolhapur"], route_states=["Maharashtra"],
                           window=(datetime(2026, 11, 4, 8, tzinfo=IST), datetime(2026, 11, 5, 20, tzinfo=IST)),
                           event_types=["blockade"])


def _scout_fake(fake, *, fetch_items=True):
    fake.add({"endpoint": SEARCH, "query_contains": "blockade", "response": NEWS["body"],
              "headers": {"x-credits-used": "1"}})
    if fetch_items:
        for it in FETCHED["body"]:
            fake.add({"endpoint": FETCH, "url": it["url"], "response": it})
    for x in NEWS["body"]["results"]:
        if redirect.is_redirect_link(x["url"]):
            _interstitial_web(fake, x["url"])  # Google answers every plain lookup with the check page


def test_scout_finds_kini_through_the_fetch_rung_and_never_cites_google(fake, fctx):
    _scout_fake(fake)
    res = find_evidence(_kini_request(), fctx)
    assert res.status == "found"
    urls = [e.url for e in res.supporting + res.contradicting]
    assert urls and not any(redirect.is_google(u) for u in urls)
    assert any("chinimandi.com" in u for u in urls)
    unresolved = [r for r in res.rejected if r.reason == "unresolved_redirect"]
    assert len(unresolved) == 2 and all("crustdata_fetch" in r.detail for r in unresolved)  # the 2 CloudFront blocks
    assert res.search_log[0].redirects == {"direct": 1, "crustdata_fetch": 2, "unresolved_redirect": 2}
    fetch_calls = [c["request"] for c in fake.calls if c["endpoint"] == FETCH]
    goto_fetched = [u for r in fetch_calls for u in r["urls"] if redirect.is_redirect_link(u)]
    assert len(goto_fetched) == 4 and len(set(goto_fetched)) == 4  # each goto paid once
    publisher_fetched = [u for r in fetch_calls for u in r["urls"] if not redirect.is_redirect_link(u)]
    assert not any("chinimandi" in u for u in publisher_fetched)  # the article was not fetched a second time


def test_unresolvable_goto_is_unresolved_and_scout_moves_on(fake, fctx):
    """With the paid rung switched off, goto links end as unresolved_redirect and the scout takes later results."""
    fctx.settings.evidence.redirect_strategies = ["direct", "location"]
    _scout_fake(fake, fetch_items=False)
    fake.add({"endpoint": FETCH, "url": "https://timesofindia.indiatimes.com/city/kolhapur/shetti-led",
              "url_contains": "timesofindia", "response": {"title": "x", "content": "", "success": True}})
    res = find_evidence(_kini_request(), fctx)
    rej = {r.url: r.reason for r in res.rejected}
    gotos = [x["url"] for x in NEWS["body"]["results"] if redirect.is_redirect_link(x["url"])]
    assert {rej.get(g) for g in gotos} <= {"unresolved_redirect", "snippet_no_place"}
    assert sum(rej.get(g) == "unresolved_redirect" for g in gotos) >= 4
    direct = [x["url"] for x in NEWS["body"]["results"] if not redirect.is_redirect_link(x["url"])]
    fetched = [u for c in fake.calls if c["endpoint"] == FETCH for u in c["request"]["urls"]]
    assert fetched and set(fetched) <= set(direct)  # only publisher links reached Crustdata fetch
    assert sum(l.redirects.get("unresolved_redirect", 0) for l in res.search_log) == \
        sum(rej.get(g) == "unresolved_redirect" for g in gotos)


def test_direct_only_ladder_makes_no_redirect_calls(fake, fctx):
    fctx.settings.evidence.redirect_strategies = ["direct"]
    _scout_fake(fake)
    find_evidence(_kini_request(), fctx)
    assert not [c for c in fake.calls if c["endpoint"] == "GET"]
    assert not [u for c in fake.calls if c["endpoint"] == FETCH for u in c["request"]["urls"]
                if redirect.is_redirect_link(u)]


def test_strategies_setting_order_and_unknown_names(settings):
    settings.evidence.redirect_strategies = ["crustdata_fetch", "bogus", "location", "location"]
    assert redirect.strategies(settings) == ["crustdata_fetch", "location"]


def test_gated_links_cost_one_credit_and_the_scout_goes_on_to_later_queries(fake, fctx):
    """Live case-1 run, 22 Nov: Google gated fresh goto links for both our lookup and Crustdata's fetcher."""
    fake.add({"endpoint": SEARCH, "query_contains": "blockade", "response": NEWS["body"],
              "headers": {"x-credits-used": "1"}})
    for x in NEWS["body"]["results"]:
        if redirect.is_redirect_link(x["url"]):
            _interstitial_web(fake, x["url"])
            fake.add({"endpoint": FETCH, "url": x["url"], "response": dict(_interstitial(), url=x["url"])})
        else:  # publisher links fetch fine (a long page that names nothing on the route)
            fake.add({"endpoint": FETCH, "url": x["url"], "response": make_item(x["url"], "Elsewhere", ["Nothing."], None)})
    res = find_evidence(_kini_request(), fctx)
    goto_fetches = [c["request"]["urls"] for c in fake.calls if c["endpoint"] == FETCH
                    and any(redirect.is_redirect_link(u) for u in c["request"]["urls"])]
    assert goto_fetches == [goto_fetches[0]] and len(goto_fetches[0]) == 1  # one paid probe in the whole group
    assert len(res.search_log) == fctx.settings.evidence.max_searches_per_deduction  # every query still ran
    assert not any(redirect.is_google(e.url) for e in res.supporting + res.contradicting)
    assert {r.reason for r in res.rejected if redirect.is_redirect_link(r.url)} == {"unresolved_redirect",
                                                                                      "snippet_no_place"}
