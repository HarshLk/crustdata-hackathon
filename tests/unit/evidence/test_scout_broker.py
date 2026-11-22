"""A11: fault cases through the real broker and a1's FakeTransport (19 budget, 22 redirect + blocked first fetch, 23 undated)."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from pdr import broker
from pdr.broker import ledger
from pdr.contracts import EvidenceRequest, RunContext
from pdr.evidence import find_evidence
from pdr.store_mem import MemStore
from tests.fakes.transport import FakeTransport

from .conftest import IST, raw_item

GOTO = "https://www.google.com/goto?url=CAEStestKini&gsessionid=x"
BLOCKED = "https://indianexpress.com/article/cities/pune/kini-blockade-test"
OUTLOOK = "https://www.outlookindia.com/national/maharashtra-farmers-protest-blocks-nh-48-strands-1500-trucks"
W = (datetime(2026, 11, 4, 8, tzinfo=IST), datetime(2026, 11, 5, 20, tzinfo=IST))
REQ = EvidenceRequest(route_places=["Pune", "Satara", "Kini", "Kolhapur"], route_states=["Maharashtra"], window=W,
                      event_types=["blockade"])


def _search(results):
    return {"endpoint": "/web/search/live", "query_contains": "Satara", "response": {"results": results}}


def _hit(url):
    return {"title": "Kini toll blockade strands trucks in Satara", "url": url, "snippet": "Satara district blockade"}


@pytest.fixture
def setup(settings, monkeypatch):
    settings.providers.paidcall_mode = "inline"
    store = MemStore()
    ctx = RunContext(run_id="r", dispute_key=None, settings=settings, store=store,
                     deadline=datetime.now(timezone.utc) + timedelta(hours=1))

    def install(entries, faults=()):
        crust = FakeTransport(entries=entries, faults=list(faults), provider="crustdata", strict=False)
        web = FakeTransport(provider="web", strict=False).add_redirects({GOTO: BLOCKED})
        monkeypatch.setitem(broker.TRANSPORTS, "crustdata", crust)
        monkeypatch.setitem(broker.TRANSPORTS, "web", web)
        monkeypatch.delitem(broker.TRANSPORTS, "llm", raising=False)
        return crust
    return ctx, install


def _pages():
    outlook = dict(raw_item("fetch_domains_1.json", "outlookindia.com"), url=OUTLOOK)
    block = dict(raw_item("fetch0.json", "goto?url=CAESogEB"), url=BLOCKED)
    return outlook, block


def test_case22_redirect_resolved_and_blocked_first_fetch_ledgered(setup, monkeypatch):
    ctx, install = setup
    outlook, block = _pages()
    install([_search([_hit(GOTO), _hit(OUTLOOK)]),
             {"endpoint": "/web/enrich/live", "url": BLOCKED, "response": block},
             {"endpoint": "/web/enrich/live", "url": OUTLOOK, "response": outlook}])
    r = find_evidence(REQ, ctx)
    assert r.status == "found" and r.supporting[0].url == OUTLOOK
    assert r.supporting[0].ledger_entry_id
    assert any(x.url == BLOCKED and x.reason == "blocked" and x.ledger_entry_id for x in r.rejected)
    rows = ledger.rows(ctx.store, run_id="r")
    fetch_rows = [e for e in rows if e.endpoint == "/web/enrich/live"]
    assert {e.step for e in fetch_rows} >= {"fetch", "fetch_human"}  # blocked page paid twice, both on the ledger
    assert sum(e.units for e in fetch_rows) >= Decimal("3")
    assert not any("google.com" in (e.request_summary or "") and e.endpoint == "/web/enrich/live" for e in rows)
    assert r.search_log[0].ledger_entry_id and r.search_log[0].credits == Decimal("1")


def test_case23_undated(setup):
    ctx, install = setup
    outlook, _ = _pages()
    undated = dict(outlook, content=re.sub(r"20\d\d-\d\d-\d\dT[\d:.+]+", "", outlook["content"]))
    install([_search([_hit(OUTLOOK)]), {"endpoint": "/web/enrich/live", "url": OUTLOOK, "response": undated}])
    assert find_evidence(REQ, ctx).status == "undated_only"


def test_case19_budget(setup):
    ctx, install = setup
    crust = install([_search([_hit(OUTLOOK)])])
    ctx.spent_credits = Decimal(ctx.settings.budget.run_cost_credits)
    r = find_evidence(REQ, ctx)
    assert r.status == "budget" and crust.calls == []
