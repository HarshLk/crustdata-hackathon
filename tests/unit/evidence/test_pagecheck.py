"""A5: page check (success:true never trusted), article text, snapshot, publication date."""
from __future__ import annotations

from datetime import datetime

from pdr.evidence.pagecheck import check_page, snapshot
from pdr.evidence.pubdate import IST, parse_datetime, published_at

from .conftest import raw_item


def test_google_block_page_detected_despite_success_true(settings):
    it = raw_item("fetch0.json", "goto?url=CAESogEB")
    assert it["success"] is True and it["title"] == "Google"
    p = check_page(it, settings)
    assert p.blocked and p.reason == "block_phrase"


def test_cloudfront_error_and_cloudflare_check_blocked(settings):
    assert check_page(raw_item("fetch0.json", "indianexpress.com"), settings).blocked
    assert check_page(raw_item("fetch_domains_0.json", "utkalpost.com"), settings).blocked


def test_empty_and_short_pages_blocked(settings):
    assert check_page({"url": "u", "success": True, "title": "x", "content": ""}, settings).reason == "empty"
    short = {"url": "u", "success": True, "title": "x", "content": "<p>" + "word " * 50 + "</p>"}
    assert check_page(short, settings).reason == "short_page"


def test_real_article_yields_date_and_snapshot(settings, ctx):
    p = check_page(raw_item("fetch_domains_1.json", "outlookindia.com"), settings)
    assert not p.blocked and len(p.text) > 1500
    assert p.published_at == datetime(2026, 11, 6, 12, 38, 46, tzinfo=IST)
    assert "Nagthane in Satara district" in p.text
    h = snapshot(p, ctx.store)
    snap = ctx.store.snapshot_get(h)
    assert snap["text"] == p.text and snap["url"] == p.url and p.content_hash == h


def test_snapshot_writes_html_next_to_file_store(settings, tmp_path):
    class S:
        path = str(tmp_path / "pdr.db")
        rows = {}

        def snapshot_get(self, h):
            return self.rows.get(h)

        def snapshot_put(self, h, url, text, html_path):
            self.rows[h] = {"url": url, "text": text, "html_path": html_path}
    s = S()
    p = check_page(raw_item("fetch_domains_1.json", "outlookindia.com"), settings)
    h = snapshot(p, s)
    assert (tmp_path / "snapshots" / f"{h}.html").exists()


def test_human_mode_page_recovered(settings):
    p = check_page(raw_item("fetch_human_retry.json", "orissapost.com"), settings)
    assert not p.blocked and p.published_at is not None


def test_date_patterns():
    assert published_at('<script>{"uploadDate": "2026-10-08T10:00:00+05:30"}</script>') == \
        datetime(2026, 10, 8, 10, tzinfo=IST)
    assert published_at('<time class="x" datetime="2026-10-08">8 Oct</time>') == datetime(2026, 10, 8, tzinfo=IST)
    assert published_at('<meta name="pubdate" content="2026-10-08T04:18:16Z">').utcoffset().total_seconds() == 0
    assert published_at("<p>no date here</p>") is None
    assert parse_datetime("2026-10-08T09:41:00+0530").utcoffset().total_seconds() == 19800
    assert parse_datetime("nonsense") is None


def test_undated_real_page(settings):
    p = check_page(raw_item("fetch_domains_0.json", "infra.economictimes"), settings)
    assert not p.blocked and p.published_at is None
