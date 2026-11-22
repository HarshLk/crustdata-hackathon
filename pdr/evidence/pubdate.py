"""Publication date from a fetched page's raw HTML (REFERENCE section 1 patterns, plus uploadDate and <time>)."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

IST = timezone(timedelta(hours=5, minutes=30))

PUB_PATTERNS = [
    r'property=["\']article:published_time["\'][^>]*content=["\']([^"\']+)',
    r'content=["\']([^"\']+)["\'][^>]*property=["\']article:published_time',
    r'"datePublished"\s*:\s*"([^"]+)"',
    r'name=["\'](?:pubdate|publishdate|date)["\'][^>]*content=["\']([^"\']+)',
    r'itemprop=["\']datePublished["\'][^>]*(?:content|datetime)=["\']([^"\']+)',
    r'"uploadDate"\s*:\s*"([^"]+)"',
    r'<time[^>]*\bdatetime=["\']([^"\']+)',
]
_COMPILED = [re.compile(p, re.IGNORECASE) for p in PUB_PATTERNS]


def parse_datetime(s: str) -> datetime | None:
    """ISO 8601 or RFC 2822 to an aware datetime; offset-less input is read as IST."""
    s = (s or "").strip()
    if not s:
        return None
    dt = None
    iso = s.replace("Z", "+00:00") if s.endswith("Z") else s
    iso = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", iso)  # +0530 -> +05:30
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        m = re.match(r"(\d{4}-\d{2}-\d{2})(?:[T ](\d{2}:\d{2}(?::\d{2})?))?", s)
        if m:
            try:
                dt = datetime.fromisoformat(m.group(1) + ("T" + m.group(2) if m.group(2) else ""))
            except ValueError:
                dt = None
        if dt is None:
            try:
                dt = parsedate_to_datetime(s)
            except (TypeError, ValueError, IndexError):
                return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    return dt


def published_at(html: str, *, not_after: datetime | None = None) -> datetime | None:
    """First pattern (in order) that yields a parseable, not-in-the-future date; None when undated."""
    if not html:
        return None
    for rx in _COMPILED:
        for m in rx.finditer(html):
            dt = parse_datetime(m.group(1))
            if dt is None:
                continue
            if not_after is not None and dt > not_after + timedelta(days=1):
                continue
            return dt
    return None


__all__ = ["published_at", "parse_datetime", "PUB_PATTERNS", "IST"]
