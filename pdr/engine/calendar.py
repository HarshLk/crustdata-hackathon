"""Day counting for penalties: calendar or working days (D-12). No holiday table in code."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Iterable

IST = timezone(timedelta(hours=5, minutes=30))
_DAY = Decimal(86400)


def to_ist(dt: datetime) -> datetime:
    """Express an aware datetime in IST (the calendar the contracts count in)."""
    return dt.astimezone(IST)


def is_working(d: date, holidays: Iterable[date] = ()) -> bool:
    """Sundays and listed holidays are non-working; nothing else is assumed."""
    return d.weekday() != 6 and d not in set(holidays)


def calendar_days(start: datetime, end: datetime) -> Decimal:
    """Elapsed days between two instants as an exact Decimal; 0 if end <= start."""
    if end <= start:
        return Decimal(0)
    return Decimal(int((end - start).total_seconds())) / _DAY


def working_days(start: datetime, end: datetime, holidays: Iterable[date] = ()) -> Decimal:
    """Elapsed time in days, counting only the parts that fall on IST working days."""
    if end <= start:
        return Decimal(0)
    hol = set(holidays)
    s, e = to_ist(start), to_ist(end)
    total = 0
    d = s.date()
    while d <= e.date():
        day_start = datetime.combine(d, time(0), IST)
        day_end = day_start + timedelta(days=1)
        lo, hi = max(s, day_start), min(e, day_end)
        if hi > lo and is_working(d, hol):
            total += int((hi - lo).total_seconds())
        d += timedelta(days=1)
    return Decimal(total) / _DAY


def weekdays_in(start: datetime, end: datetime) -> list[date]:
    """IST dates other than Sundays touched by the interval (the days a holiday list would decide)."""
    if end <= start:
        return []
    s, e = to_ist(start), to_ist(end)
    out, d = [], s.date()
    while d <= e.date():
        if d.weekday() != 6:
            out.append(d)
        d += timedelta(days=1)
    return out


def union_length(intervals: Iterable[tuple[datetime, datetime]]) -> timedelta:
    """Total length of the union of intervals; overlaps are counted once."""
    ivs = sorted((a, b) for a, b in intervals if b > a)
    total = timedelta(0)
    cur_s = cur_e = None
    for a, b in ivs:
        if cur_e is None or a > cur_e:
            if cur_e is not None:
                total += cur_e - cur_s
            cur_s, cur_e = a, b
        else:
            cur_e = max(cur_e, b)
    if cur_e is not None:
        total += cur_e - cur_s
    return total
