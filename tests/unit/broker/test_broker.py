"""paid_call: cache, retries, budget, throttle, ledger."""
from decimal import Decimal

import pytest

from pdr.broker import ledger, paid_call
from pdr.broker.exec import paid_calls_parallel
from pdr.contracts import BudgetReached

SEARCH = "/web/search/live"
REQ = {"query": "Kini blockade", "sources": ["news"], "start_date": 1, "end_date": 2, "page": 1}


def _results(n=3):
    return {"results": [{"title": f"t{i}", "url": f"https://x/{i}", "snippet": "s"} for i in range(n)]}


def test_second_identical_call_is_cache_hit_at_zero_cost(ctx, fake):
    fake.add({"endpoint": SEARCH, "query_contains": "Kini", "response": _results(), "headers": {"x-credits-used": "1"}})
    r1 = paid_call("crustdata", SEARCH, REQ, ctx=ctx, step="search", worst_case_credits=Decimal(1))
    r2 = paid_call("crustdata", SEARCH, REQ, ctx=ctx, step="search", worst_case_credits=Decimal(1))
    assert r1.ok and not r1.cache_hit and r1.credits == Decimal(1)
    assert r2.ok and r2.cache_hit and r2.credits == 0 and r2.body == r1.body
    assert len(fake.calls) == 1
    rows = ledger.rows(ctx.store, run_id="run1")
    assert [(e.cache_hit, e.units) for e in rows] == [(False, Decimal(1)), (True, Decimal(0))]
    assert rows[0].cost_inr == Decimal(1) * Decimal("0.30") * Decimal("88.0")
    assert ctx.spent_credits == Decimal(1)
    t = ledger.totals(ctx.store, by="client")
    assert t["ACME"]["paid_calls"] == 1 and t["ACME"]["cache_hits"] == 1


def test_429_then_200_gives_two_ledger_rows(ctx, fake, instant_time):
    fake.faults.append("http_429_once")
    fake.add({"endpoint": SEARCH, "response": _results()})
    r = paid_call("crustdata", SEARCH, REQ, ctx=ctx, step="search", worst_case_credits=Decimal(1))
    assert r.ok and r.status == 200
    rows = ledger.rows(ctx.store)
    assert [(e.attempt_no, e.outcome) for e in rows] == [(1, "retry"), (2, "ok")]
    assert any(isinstance(s, tuple) and s[0] == "backoff" for s in instant_time["sleeps"])


def test_timeouts_exhausted_returns_not_ok(ctx, fake):
    ctx.settings.providers.crustdata.retry_limit = 0
    fake.faults.append("timeout_once")
    r = paid_call("crustdata", SEARCH, REQ, ctx=ctx, step="search", worst_case_credits=Decimal(1))
    assert not r.ok and r.error
    assert [e.outcome for e in ledger.rows(ctx.store)] == ["failed"]


def test_budget_below_worst_case_raises_and_makes_no_call(tmp_path, fake):
    from tests.unit.broker.conftest import make_ctx
    ctx = make_ctx(tmp_path / "b.db", run_cost_credits=Decimal(2))
    fake.add({"endpoint": SEARCH, "response": _results()})
    with pytest.raises(BudgetReached):
        paid_call("crustdata", "/web/enrich/live", {"urls": ["https://a.in/1", "https://b.in/2", "https://c.in/3"]}, ctx=ctx, step="fetch",
                  worst_case_credits=Decimal(3))
    assert fake.calls == [] and ledger.rows(ctx.store) == []
    ctx2 = make_ctx(tmp_path / "c.db", per_deduction_credits=Decimal(1))
    with pytest.raises(BudgetReached):
        paid_call("crustdata", SEARCH, REQ, ctx=ctx2, step="search", worst_case_credits=Decimal(2))
    assert fake.calls == []


def test_deadline_raises(ctx, fake):
    from datetime import datetime, timezone
    ctx.deadline = datetime(2000, 1, 1, tzinfo=timezone.utc)
    with pytest.raises(BudgetReached):
        paid_call("crustdata", SEARCH, REQ, ctx=ctx, step="search", worst_case_credits=Decimal(1))


def test_throttle_delays_the_11th_call_in_a_minute(ctx, fake, instant_time):
    fake.add({"endpoint": SEARCH, "response": _results()})
    ctx.settings.budget.run_cost_credits = Decimal(100)
    ctx.dispute_key = None
    for i in range(10):
        paid_call("crustdata", SEARCH, dict(REQ, query=f"q{i}"), ctx=ctx, step="search", worst_case_credits=1)
    assert [s for s in instant_time["sleeps"] if not isinstance(s, tuple)] == []
    paid_call("crustdata", SEARCH, dict(REQ, query="q10"), ctx=ctx, step="search", worst_case_credits=1)
    waits = [s for s in instant_time["sleeps"] if not isinstance(s, tuple)]
    assert len(waits) == 1 and 59 <= waits[0] <= 60.01


def test_cost_falls_back_to_price_table_without_header(ctx, fake):
    fake.add({"endpoint": "/web/enrich/live", "url_contains": "x.in", "response": [{"url": "https://x.in/1"}, {"url": "https://x.in/2"}],
              "headers": {"x-credits-used": ""}})
    r = paid_call("crustdata", "/web/enrich/live", {"urls": ["https://x.in/1", "https://x.in/2"]}, ctx=ctx, step="fetch",
                  worst_case_credits=2)
    assert r.credits == Decimal(2)


def test_guard_violation_blocks_before_anything(ctx, fake, monkeypatch):
    from pdr import guard
    from pdr.contracts import GuardViolation

    def deny(dest, payload, client_id=None):
        raise GuardViolation(dest)

    monkeypatch.setattr(guard, "check", deny)
    with pytest.raises(GuardViolation):
        paid_call("crustdata", SEARCH, REQ, ctx=ctx, step="search", worst_case_credits=1)
    assert fake.calls == []


def test_parallel_bounded_and_same_key_once(ctx, fake):
    fake.faults.append("slow:0.2")
    fake.add({"endpoint": SEARCH, "response": _results()})
    ctx.dispute_key = None
    specs = [{"provider": "crustdata", "endpoint": SEARCH, "request": dict(REQ, query=q), "step": "search",
              "worst_case_credits": 1} for q in ["a", "b", "a", "c"]]
    res = paid_calls_parallel(specs, ctx)
    assert all(r.ok for r in res)
    assert sorted(c["request"]["query"] for c in fake.calls) == ["a", "b", "c"]
    assert sum(r.cache_hit for r in res) == 1


def test_memory_store_works(fake):
    from datetime import datetime, timedelta, timezone

    from pdr.contracts import RunContext
    from pdr.store_mem import MemStore
    from tests.unit.broker.conftest import ROOT
    from pdr.settings import load_settings
    ctx = RunContext("r", None, load_settings(ROOT / "settings.yaml"), MemStore(),
                     datetime.now(timezone.utc) + timedelta(hours=1))
    fake.add({"endpoint": SEARCH, "response": _results()})
    assert paid_call("crustdata", SEARCH, REQ, ctx=ctx, step="s", worst_case_credits=1).ok
    assert paid_call("crustdata", SEARCH, REQ, ctx=ctx, step="s", worst_case_credits=1).cache_hit
    other = RunContext("r", None, ctx.settings, MemStore(), ctx.deadline)
    assert not paid_call("crustdata", SEARCH, REQ, ctx=other, step="s", worst_case_credits=1).cache_hit
