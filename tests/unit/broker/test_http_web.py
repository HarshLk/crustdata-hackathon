"""WebHttp: Google's timed interstitial on fresh goto links is waited out (offline, httpx MockTransport)."""
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
    w = WebHttp(sleep=slept.append, transport=httpx.MockTransport(handler))
    r = w.send("GET", {"method": "GET", "url": GOTO, "follow_redirects": False})
    assert r.status == 302 and r.headers["location"] == "https://news.example.in/a" and r.body == ""
    assert slept == [42.0] and seen[1] == "https://www.google.com/goto?url=CAES1"


def test_old_link_redirects_at_once():
    w = WebHttp(sleep=lambda s: (_ for _ in ()).throw(AssertionError("no wait")),
                transport=httpx.MockTransport(lambda r: httpx.Response(302, headers={"Location": "https://p.in/x"})))
    assert w.send("GET", {"method": "GET", "url": GOTO}).status == 302
