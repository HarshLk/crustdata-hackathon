"""FakeTransport: cassette lookup and faults."""
import pytest

from pdr.broker.transport import Response, TransportTimeout, cache_key
from tests.fakes.transport import CASSETTES_ROOT, CassetteMiss, FakeTransport, save_cassette

SEARCH = "/web/search/live"
FETCH = "/web/enrich/live"


def test_exact_and_loose(tmp_path):
    req = {"query": "x", "sources": ["news"]}
    save_cassette(tmp_path, "crustdata", SEARCH, req, Response(200, {"x-credits-used": "1"}, {"results": [1]}))
    t = FakeTransport(tmp_path)
    assert t.send(SEARCH, req, cache_key=cache_key("crustdata", SEARCH, req)).body == {"results": [1]}
    t.add({"endpoint": SEARCH, "query_contains": "Kini", "response_file": "raw/s3.json"})
    r = t.send(SEARCH, {"query": "NH-48 Kini blockade"}, cache_key="k")
    assert len(r.body["results"]) == 10 and r.headers["x-credits-used"] == "1"
    with pytest.raises(CassetteMiss):
        t.send(SEARCH, {"query": "nothing"}, cache_key="k2")


def test_per_url_fetch_and_faults():
    t = FakeTransport(faults=["http_429_once", "timeout_once", "block_first", "undated"])
    t.add({"endpoint": FETCH, "url": "https://a/1", "response": {"title": "A", "content": '<time datetime="2026">x'}})
    t.add({"endpoint": FETCH, "url": "https://b/2", "response": {"title": "B", "content": "y"}})
    req = {"urls": ["https://a/1", "https://b/2"]}
    with pytest.raises(TransportTimeout):
        t.send(FETCH, req, cache_key="k")
    assert t.send(FETCH, req, cache_key="k").status == 429
    blocked = t.send(FETCH, req, cache_key="k")
    assert "Verifying your request" in blocked.body[0]["content"] and blocked.headers["x-credits-used"] == "2"
    ok = t.send(FETCH, req, cache_key="k")
    assert [i["title"] for i in ok.body] == ["A", "B"] and "datetime" not in ok.body[0]["content"]
    assert len(t.calls) == 4


def test_redirects_from_recorded_table():
    t = FakeTransport().add_resolved_file(CASSETTES_ROOT / "raw" / "resolved.json")
    goto = next(e for e in t.loose if "google.com/goto" in e["url"])
    r = t.send("GET", {"method": "GET", "url": goto["url"]}, cache_key="k")
    assert r.status == 302 and r.headers["Location"].startswith("http")
