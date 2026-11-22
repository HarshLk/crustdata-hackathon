"""PDR_OFFLINE=1: no real network request, ever; live tests are opt-in."""
from decimal import Decimal

import httpx
import pytest

from pdr import broker
from pdr.broker import ledger


def test_offline_no_fake_makes_no_http_request(ctx, monkeypatch):
    monkeypatch.setenv("PDR_OFFLINE", "1")
    monkeypatch.delitem(broker.TRANSPORTS, "crustdata", raising=False)
    monkeypatch.delitem(broker.TRANSPORTS, "web", raising=False)

    def boom(*a, **k):
        raise AssertionError("network request attempted")

    for name in ("post", "get", "request"):
        monkeypatch.setattr(httpx, name, boom)
    monkeypatch.setattr(httpx.Client, "request", boom)
    r = broker.paid_call("crustdata", "/web/search/live", {"query": "Kini blockade", "sources": ["news"]},
                         ctx=ctx, step="search", worst_case_credits=Decimal(1))
    assert not r.ok and "PDR_OFFLINE" in r.error
    w = broker.paid_call("web", "GET", {"method": "GET", "url": "https://www.google.com/goto?url=x"},
                         ctx=ctx, step="resolve", worst_case_credits=Decimal(0))
    assert not w.ok
    assert [e.outcome for e in ledger.rows(ctx.store)] == ["failed", "failed"]  # no retries


def test_offline_blocks_real_transport_registered_from_pdr(ctx, monkeypatch):
    monkeypatch.setenv("PDR_OFFLINE", "1")
    from pdr.broker.http import CrustdataHttp
    monkeypatch.setitem(broker.TRANSPORTS, "crustdata", CrustdataHttp("https://api.invalid", "v"))
    monkeypatch.setattr(httpx, "post", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network")))
    r = broker.paid_call("crustdata", "/web/search/live", {"query": "Kini"}, ctx=ctx, step="s",
                         worst_case_credits=Decimal(1))
    assert not r.ok


def test_session_is_offline():
    import os
    assert os.environ.get("PDR_OFFLINE") == "1" or os.environ.get("PDR_LIVE") == "1"


@pytest.mark.live
def test_live_marker_is_skipped_by_default():
    import os
    assert os.environ.get("PDR_LIVE") == "1"
