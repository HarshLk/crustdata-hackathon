"""Replays the recorded live smoke (search "Kini blockade" 3-7 Nov 2026 IST, resolve, fetch) offline."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from pdr import broker
from pdr.broker import ledger
from pdr.evidence import crustdata, redirect
from tests.fakes.transport import CASSETTES_ROOT, FakeTransport

IST = timezone(timedelta(hours=5, minutes=30))
LIVE = CASSETTES_ROOT / "raw" / "live_kini"


def test_live_kini_replay(ctx, monkeypatch):
    t = FakeTransport(LIVE)
    monkeypatch.setitem(broker.TRANSPORTS, "crustdata", t)
    monkeypatch.setitem(broker.TRANSPORTS, "web", t)
    ctx.dispute_key = None
    res = crustdata.search("Kini blockade", datetime(2026, 11, 3, tzinfo=IST),
                           datetime(2026, 11, 7, 23, 59, 59, tzinfo=IST), ctx)
    assert len(res) == 9 and any("Kini" in r["title"] for r in res)
    urls = [redirect.resolve(r["url"], ctx) for r in res]
    assert all(u and "google.com" not in u for u in urls)
    items = crustdata.fetch([u for u in urls if u][:3], ctx)
    assert len(items) == 3 and "Kini" in items[1]["title"]
    paid = [e for e in ledger.rows(ctx.store) if e.provider == "crustdata"]
    assert [e.units for e in paid] == [Decimal(1), Decimal(3)]  # read from the recorded x-credits-used header
