"""Subprocess for the kill tests: one slow paid call, which the test kills mid-flight."""
import sys
from decimal import Decimal

from pdr import broker
from tests.fakes.transport import FakeTransport
from tests.unit.broker.conftest import make_ctx

SEARCH = "/web/search/live"
REQ = {"query": "Kini blockade", "sources": ["news"], "start_date": 1, "end_date": 2, "page": 1}
RESPONSE = {"endpoint": SEARCH, "response": {"results": [{"title": "t", "url": "https://x.in/1", "snippet": "s"}]},
            "headers": {"x-credits-used": "1"}}

if __name__ == "__main__":
    db, mode, slow = sys.argv[1], sys.argv[2], sys.argv[3]
    broker.register_transport("crustdata", FakeTransport(faults=[f"slow:{slow}"], entries=[RESPONSE]))
    ctx = make_ctx(db)
    ctx.settings.providers.paidcall_mode = mode
    r = broker.paid_call("crustdata", SEARCH, REQ, ctx=ctx, step="search", worst_case_credits=Decimal(1))
    print("DONE", r.ok, r.cache_hit)
