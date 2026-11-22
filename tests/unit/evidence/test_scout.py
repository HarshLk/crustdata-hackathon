"""A9/A11: find_evidence end to end over a fake client (statuses, limits, ledger of blocked fetches)."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pdr.contracts import EvidenceRequest
from pdr.evidence import estimate_credits, find_evidence, scout
from pdr.llm import Claim, PassageProposal, PassageProposals

from .conftest import IST, FakeClient, make_item, raw_item

KINI_W = (datetime(2026, 11, 4, 8, tzinfo=IST), datetime(2026, 11, 5, 20, tzinfo=IST))
OUTLOOK = "https://www.outlookindia.com/national/maharashtra-farmers-protest-blocks-nh-48-strands-1500-trucks"
BLOCKED = "https://indianexpress.com/article/cities/pune/kini-blockade"
HIT = {"title": "Maharashtra farmers' protest blocks NH-48", "url": OUTLOOK,
       "snippet": "blockade between Nagthane in Satara district and Sangli Phata in Kolhapur district"}


def _req(places=("Pune", "Satara", "Kini", "Kolhapur"), window=KINI_W):
    return EvidenceRequest(route_places=list(places), route_states=["Maharashtra"], window=window,
                           event_types=["blockade"])


def _pages():
    outlook = dict(raw_item("fetch_domains_1.json", "outlookindia.com"), url=OUTLOOK)
    block = dict(raw_item("fetch0.json", "goto?url=CAESogEB"), url=BLOCKED)
    return outlook, block


def test_found_for_pune_kolhapur(ctx, use_client):
    outlook, _ = _pages()
    c = use_client(FakeClient({"Satara blockade": [HIT]}, {OUTLOOK: outlook}))
    r = find_evidence(_req(), ctx)
    assert r.status == "found" and len(r.supporting) == 1
    ev = r.supporting[0]
    assert ev.url == OUTLOOK and ev.content_hash and ctx.store.snapshot_get(ev.content_hash)
    assert ev.link.startswith(OUTLOOK + "#:~:text=")
    assert r.search_log[0].query == "Satara blockade" and r.search_log[0].n_results == 1
    assert r.event_interval is not None
    assert not any(x == ("search", "Maharashtra blockade") for x in c.ledger)  # stopped at the first qualifying rung


def test_none_for_pune_mumbai(ctx, use_client):
    outlook, _ = _pages()
    hit = dict(HIT, snippet="Pune-Bengaluru highway blockade strands trucks")
    use_client(FakeClient({"blockade": [hit]}, {OUTLOOK: outlook}))
    r = find_evidence(_req(places=("Pune", "Lonavala", "Panvel", "Mumbai")), ctx)
    assert r.status == "none" and r.supporting == []
    assert any(x.reason == "place_not_on_route" for x in r.rejected)
    assert r.search_log  # cited by a "valid" verdict


def test_first_fetch_blocked_falls_through_both_ledgered(ctx, use_client):
    outlook, block = _pages()
    hits = [dict(HIT, url=BLOCKED, title="Kini blockade Satara"), HIT]
    c = use_client(FakeClient({"Satara blockade": hits}, {OUTLOOK: outlook, BLOCKED: block}, {BLOCKED: block}))
    r = find_evidence(_req(), ctx)
    assert r.status == "found"
    fetches = [x for x in c.ledger if x[0].startswith("fetch")]
    assert ("fetch", BLOCKED) in fetches and ("fetch", OUTLOOK) in fetches and ("fetch_human", BLOCKED) in fetches
    assert any(x.url == BLOCKED and x.reason == "blocked" for x in r.rejected)


def test_human_mode_retry_recovers(ctx, use_client):
    outlook, block = _pages()
    use_client(FakeClient({"Satara blockade": [HIT]}, {OUTLOOK: block}, {OUTLOOK: outlook}))
    assert find_evidence(_req(), ctx).status == "found"


def test_undated_page_gives_undated_only(ctx, use_client):
    outlook, _ = _pages()
    import re
    undated = dict(outlook, content=re.sub(r"20\d\d-\d\d-\d\dT[\d:.+]+", "", outlook["content"]))
    use_client(FakeClient({"blockade": [HIT]}, {OUTLOOK: undated}))
    r = find_evidence(_req(), ctx)
    assert r.status == "undated_only" and any(x.reason == "no_date" for x in r.rejected)


def test_budget_below_one_search_returns_budget_with_no_call(ctx, use_client):
    c = use_client(FakeClient({"blockade": [HIT]}, {}))
    ctx.spent_credits = Decimal(ctx.settings.budget.run_cost_credits) - Decimal("0.5")
    r = find_evidence(_req(), ctx)
    assert r.status == "budget" and c.ledger == []


def test_budget_reached_mid_run(ctx, use_client):
    from pdr.contracts import BudgetReached

    class Broke(FakeClient):
        def fetch(self, *a, **k):
            raise BudgetReached("short")
    use_client(Broke({"blockade": [HIT]}, {}))
    assert find_evidence(_req(), ctx).status == "budget"


def test_no_route_places(ctx, use_client):
    c = use_client(FakeClient({}, {}))
    assert find_evidence(_req(places=()), ctx).status == "no_route_places" and c.ledger == []


def test_provider_failed(ctx, use_client):
    class Down(FakeClient):
        def search(self, query, start, end, ctx, *, step="search"):
            self.ledger.append(("search", query))
            ctx.last_call_failed = True
            return []
    use_client(Down({}, {}))
    assert find_evidence(_req(), ctx).status == "provider_failed"


def test_zero_results_everywhere_is_none(ctx, use_client):
    c = use_client(FakeClient({}, {}))
    r = find_evidence(_req(window=(datetime(2026, 10, 27, tzinfo=IST), datetime(2026, 10, 29, tzinfo=IST))), ctx)
    assert r.status == "none" and len(r.search_log) == ctx.settings.evidence.max_searches_per_deduction
    assert not any(x[0] == "fetch" for x in c.ledger)


def test_snippet_prefilter_skips_fetch(ctx, use_client):
    c = use_client(FakeClient({"blockade": [dict(HIT, title="Blockade in Chile", snippet="Santiago truckers")]}, {}))
    r = find_evidence(_req(), ctx)
    assert r.status == "none" and not any(x[0] == "fetch" for x in c.ledger)
    assert any(x.reason == "snippet_no_place" for x in r.rejected)


def test_max_blocked_fetches_stops(ctx, use_client):
    _, block = _pages()
    hits = [dict(HIT, url=f"https://x.example/{i}") for i in range(10)]
    c = use_client(FakeClient({"blockade": hits}, {h["url"]: block for h in hits}, {h["url"]: block for h in hits}))
    r = find_evidence(_req(), ctx)
    n = len([x for x in c.ledger if x[0] == "fetch"])
    assert r.status == "none" and n <= ctx.settings.evidence.max_fetches_per_deduction


def test_model_proposal_used_when_available_and_still_tested(ctx, use_client, monkeypatch):
    outlook, _ = _pages()
    use_client(FakeClient({"Satara blockade": [HIT]}, {OUTLOOK: outlook}))
    fake = PassageProposals(proposals=[PassageProposal(
        event_type="blockade", status="happened",
        places_affected=[Claim(value="Kini", quote="Trucks were stranded at Kini toll plaza in Kolhapur district.")],
        event_from="2026-11-04", event_to="2026-11-05", effect_quote="Trucks were stranded.")])
    monkeypatch.setattr(scout, "llm_json", lambda *a, **k: fake)
    r = find_evidence(_req(), ctx)
    assert r.supporting == [] and any("quote_not_verbatim" in x.reason for x in r.rejected)


def test_estimate_credits_falls_back_to_setting(ctx):
    assert estimate_credits(_req(), ctx) == Decimal(str(ctx.settings.economics.estimated_search_credits))
