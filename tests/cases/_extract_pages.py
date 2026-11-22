"""One-off: trim real saved Crustdata responses into small cassette pages (run: python -m tests.cases._extract_pages).

Source: harsh/crustdata_test/raw/ (real /web/search/live and /web/enrich/live responses of 22 Nov 2026).
Each page keeps the real URL, title, retrieval timestamp and publication meta tag, and the article text as
extracted by trafilatura (verbatim, nothing added), wrapped in minimal HTML. Output: tests/cases/_pages/*.json.
"""
from __future__ import annotations

import os
import html
import json
import re
from pathlib import Path

import trafilatura

RAW = Path(os.environ.get("PDR_RAW_DIR", "crustdata_test/raw"))  # folder of saved raw Crustdata responses
OUT = Path(__file__).parent / "_pages"
FETCH_FILES = ["fetch_domains_1.json", "fetch_domains_0.json", "fetch2_plain.json", "fetch0.json"]
PAGES = {  # name -> (url substring, search file, title substring for the search result)
    "outlook_kini": ("outlookindia.com/national/maharashtra-farmers-protest", "s3", "Strands 1,500 Trucks"),
    "toi_kini_nov3": ("shetti-led-blockade-reduce-traffic", "s3", "Shetti-led blockade"),
    "etinfra_kini": ("pune-bengaluru-national-highway-blocked-by-farme", "s3", "Pune-Bengaluru National Highway blocked"),
    "logisticsinsider_srirampur": ("logisticsinsider.in/trucks-stranded-assam-bengal", "s4", "300 Petroleum Coke Trucks Stuck at Assam"),
    "toi_srirampur_oct7": ("sos-to-pmo-after-orders-from-above", "s4", "SOS to PMO"),
    "theprint_odisha": ("theprint.in/india/normal-life-hit-in-odisha", "s1", "Normal life hit in Odisha"),
    "assamtribune_odisha": ("assamtribune.com/national/india-blocs-12-hour-bandh", "s1", "INDIA bloc’s 12-hour bandh disrupts"),
}
META = re.compile(r'<meta[^>]+(?:article:published_time|datePublished|pubdate)[^>]*>', re.I)
JSONLD = re.compile(r'"datePublished"\s*:\s*"([^"]+)"')


def _fetch_items() -> list[dict]:
    out = []
    for f in FETCH_FILES:
        out += json.loads((RAW / f).read_text(encoding="utf-8"))
    return out


def main() -> None:
    OUT.mkdir(exist_ok=True)
    items = _fetch_items()
    resolved = {r["url"]: r.get("resolved") for r in json.loads((RAW / "resolved.json").read_text(encoding="utf-8"))}
    for name, (frag, sfile, tfrag) in PAGES.items():
        it = next(i for i in items if frag in (i.get("url") or "") and len(i.get("content") or "") > 5000)
        raw = it["content"]
        text = trafilatura.extract(raw) or ""
        m = META.search(raw)
        meta = m.group(0) if m else ""
        if not meta and JSONLD.search(raw):
            meta = f'<meta property="article:published_time" content="{JSONLD.search(raw).group(1)}">'
        paras = "".join(f"<p>{html.escape(p)}</p>\n" for p in text.split("\n") if p.strip())
        content = (f"<html><head><title>{html.escape(it.get('title') or '')}</title>{meta}</head>"
                   f"<body><article>\n{paras}</article></body></html>")
        search = json.loads((RAW / f"{sfile}.json").read_text(encoding="utf-8"))
        result = next(r for r in search["results"] if tfrag in r["title"])
        real = resolved.get(result["url"]) or result["url"]
        page = {"url": real, "success": True, "title": it.get("title"), "content": content,
                "timestamp": it.get("timestamp"), "http_status": it.get("http_status", "200")}
        (OUT / f"{name}.json").write_text(json.dumps({"search_result": result, "resolved": real, "page": page,
                                                      "source_file": f"crustdata_test/raw/{sfile}.json + fetch"},
                                                     indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(name, real, len(text), meta[:90])


if __name__ == "__main__":
    main()
