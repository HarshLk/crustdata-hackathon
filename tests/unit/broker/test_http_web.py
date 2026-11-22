"""WebHttp: Google's timed interstitial is waited out only when configured (offline, httpx MockTransport)."""
import httpx

from pdr.broker.http import WebHttp

GOTO = "https://www.google.com/goto?url=CAES1&gsessionid=x"
PAGE = '<html><head><title>Google</title><meta content="41;url=/goto?url=CAES1" http-equiv="refresh"></head></html>'


def test_interstitial_then_302():
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(str(req.url))
        if "gsessionid" in str(req.url):
            return httpx.Response(200, text=PAGE, headers={"set-cookie": "NID=1"})
        assert req.headers.get("cookie") == "NID=1"
        return httpx.Response(302, headers={"Location": "https://news.example.in/a"})

    slept = []
    w = WebHttp(sleep=slept.append, max_wait=90, transport=httpx.MockTransport(handler))
    r = w.send("GET", {"method": "GET", "url": GOTO, "follow_redirects": False})
    assert r.status == 302 and r.headers["location"] == "https://news.example.in/a" and r.body == ""
    assert slept == [42.0] and seen[1] == "https://www.google.com/goto?url=CAES1"


def test_old_link_redirects_at_once():
    w = WebHttp(sleep=lambda s: (_ for _ in ()).throw(AssertionError("no wait")),
                transport=httpx.MockTransport(lambda r: httpx.Response(302, headers={"Location": "https://p.in/x"})))
    assert w.send("GET", {"method": "GET", "url": GOTO}).status == 302


def test_default_returns_interstitial_without_waiting():
    """Default (providers.web.interstitial_wait_seconds = 0): the 200 page comes back at once, one request."""
    seen = []

    def handler(req):
        seen.append(str(req.url))
        return httpx.Response(200, text=PAGE)

    w = WebHttp(sleep=lambda s: (_ for _ in ()).throw(AssertionError("no wait")), transport=httpx.MockTransport(handler))
    r = w.send("GET", {"method": "GET", "url": GOTO, "follow_redirects": False})
    assert r.status == 200 and len(seen) == 1


def test_wait_longer_than_cap_is_not_sat_out():
    w = WebHttp(sleep=lambda s: (_ for _ in ()).throw(AssertionError("no wait")), max_wait=8,
                transport=httpx.MockTransport(lambda r: httpx.Response(200, text=PAGE)))
    assert w.send("GET", {"method": "GET", "url": GOTO}).status == 200


def test_settings_cap_reaches_the_real_transport(monkeypatch):
    from pdr.broker import http
    from pdr.settings import load_settings
    from tests.unit.evidence.conftest import ROOT
    monkeypatch.delenv("PDR_OFFLINE", raising=False)
    monkeypatch.setattr(http, "_WEB", None)
    s = load_settings(ROOT / "settings.yaml")
    assert s.providers.web.interstitial_wait_seconds == 0 and s.providers.web.timeout_seconds <= 8
    assert http.real_transport("web", s).max_wait == 0
    monkeypatch.setattr(http, "_WEB", None)
