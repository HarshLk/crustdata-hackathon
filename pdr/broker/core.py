"""paid_call: guard, cache, budget, intent, throttle, execute with retries, cache+ledger in one transaction."""
from __future__ import annotations

import json
import os
import random
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from urllib.parse import urljoin, urlparse

from pdr import guard
from pdr.broker import throttle
from pdr.broker.db import connect, db_path, is_memory, tx
from pdr.broker.transport import Response, TransportError, TransportOffline, TransportTimeout, cache_key, header
from pdr.contracts import BudgetReached, CallResult, RunContext
from pdr.crashpoints import crashpoint
from pdr.models import LedgerEntry, canonical_json
from pdr.settings import Settings

SEARCH, FETCH, IDENTIFY = "/web/search/live", "/web/enrich/live", "/company/identify"
GUARD_DEST = {("crustdata", SEARCH): "crustdata_search", ("crustdata", FETCH): "crustdata_fetch",
              ("crustdata", IDENTIFY): "crustdata_identify"}
# fallback when the response has no x-credits-used header (overridable: providers.crustdata.prices)
DEFAULT_PRICES = {SEARCH: {"per_call": "1"}, FETCH: {"per_url": "1"}, IDENTIFY: {"per_name": "0.2"}}
SLEEP = time.sleep  # backoff sleep; tests patch it
_budget_lock = threading.Lock()
_reserved: dict[str, Decimal] = {}  # run_id -> worst-case credits of calls in flight in this process
_inflight: set[str] = set()  # cache keys being executed by a thread of this process


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- provider config

def provider_cfg(settings: Settings, provider: str) -> dict:
    """rate_per_minute, retry_limit, backoff_seconds, timeout_seconds, prices for one provider."""
    sect = getattr(settings.providers, provider, None)
    d = sect.model_dump(mode="json") if hasattr(sect, "model_dump") else dict(sect or {})
    if provider == "web":
        d = {"rate_per_minute": 0, "retry_limit": 1, "backoff_seconds": [1], "timeout_seconds": 20, **d}
    d.setdefault("rate_per_minute", 0)
    d.setdefault("retry_limit", 2)
    d.setdefault("backoff_seconds", [2, 5, 12])
    d.setdefault("timeout_seconds", 60)
    d.setdefault("prices", DEFAULT_PRICES if provider == "crustdata" else {})
    return d


def price_credits(cfg: dict, endpoint: str, request: dict, resp: Response | None) -> Decimal:
    """Credits from the x-credits-used header, else the settings price table."""
    h = header(resp.headers, "x-credits-used") if resp is not None else None
    if h not in (None, ""):
        try:
            return Decimal(str(h))
        except Exception:
            pass
    if resp is None or resp.status >= 400:
        return Decimal("0")
    p = (cfg.get("prices") or {}).get(endpoint) or {}
    if endpoint == SEARCH and isinstance(resp.body, dict) and not resp.body.get("results"):
        return Decimal("0")  # empty searches are free (F-5)
    total = Decimal(str(p.get("per_call", 0)))
    total += Decimal(str(p.get("per_url", 0))) * len(request.get("urls") or [])
    total += Decimal(str(p.get("per_name", 0))) * len(request.get("names") or [])
    return total


def _google_host(url: str) -> bool:
    h = urlparse(url or "").netloc.lower()
    return h == "google.com" or h.endswith(".google.com") or h.startswith("google.") or ".google." in h


def cacheable(provider: str, request: dict, resp: Response) -> bool:
    """Redirect probes cache only a redirect to a non-Google host: a 200 interstitial or a /sorry/ redirect is
    a failure and is retried later. (A fetched page the caller finds useless is dropped with forget().)"""
    if provider == "web" and not request.get("follow_redirects", False):
        loc = header(resp.headers or {}, "location") or ""
        return 300 <= resp.status < 400 and bool(loc) and not _google_host(urljoin(str(request.get("url") or ""), loc))
    return resp.status < 400


def forget(ctx: RunContext, provider: str, endpoint: str, request: dict) -> bool:
    """Drop one cached response the caller found to be a failure (the ledger rows stay); True if one was cached."""
    key = cache_key(provider, endpoint, request)
    conn = connect(ctx.store)
    with tx(conn) as c:
        n = c.execute("DELETE FROM call_cache WHERE cache_key=?", (key,)).rowcount
    return bool(n)


def is_empty(endpoint: str, body: Any) -> bool:
    if endpoint == SEARCH:
        return not (isinstance(body, dict) and body.get("results"))
    if endpoint in (FETCH, IDENTIFY):
        return not body
    return False


# --------------------------------------------------------------------------- ledger rows

def write_ledger(conn: sqlite3.Connection, *, settings: Settings, run_id: str, dispute_key: str | None,
                 key: str, step: str, provider: str, endpoint: str, credits: Decimal, cache_hit: bool,
                 attempt_no: int, outcome: str, summary: str, started_at: datetime,
                 ended_at: datetime | None = None) -> LedgerEntry:
    price = Decimal(str(settings.budget.credit_price_usd))
    cost_usd = credits * price
    entry = LedgerEntry(
        entry_id=uuid.uuid4().hex, run_id=run_id, dispute_key=dispute_key, cache_key=key, step=step,
        provider=provider, endpoint=endpoint, units=credits, unit_kind="credits", unit_price_usd=price,
        cost_usd=cost_usd, cost_inr=cost_usd * Decimal(str(settings.budget.usd_inr)), cache_hit=cache_hit,
        attempt_no=attempt_no, outcome=outcome, request_summary=summary[:300], started_at=started_at,
        ended_at=ended_at, client_id=(dispute_key or "").split("|")[0] or None)
    conn.execute("INSERT INTO ledger(entry_id, run_id, dispute_key, cache_key, body, cost_inr, cache_hit, started_at)"
                 " VALUES (?,?,?,?,?,?,?,?)",
                 (entry.entry_id, run_id, dispute_key, key, entry.model_dump_json(), str(entry.cost_inr),
                  int(cache_hit), started_at.isoformat()))
    return entry


def summarize(endpoint: str, request: dict) -> str:
    if "query" in request:
        return f"{endpoint} q={request['query']!r}"
    if "urls" in request:
        return f"{endpoint} urls={len(request['urls'])}" + (" human_mode" if request.get("human_mode") else "")
    if "url" in request:
        return f"{request.get('method', 'GET')} {request['url'][:200]}"
    return f"{endpoint} {canonical_json(request)[:200]}"


def _spent(conn: sqlite3.Connection, run_id: str, dispute_key: str | None) -> tuple[Decimal, Decimal]:
    run = ded = Decimal("0")
    for body, dk in conn.execute("SELECT body, dispute_key FROM ledger WHERE run_id=? AND cache_hit=0", (run_id,)):
        u = Decimal(str(json.loads(body).get("units", "0")))
        run += u
        if dispute_key and dk == dispute_key:
            ded += u
    return run, ded


# --------------------------------------------------------------------------- transports

def get_transport(provider: str, settings: Settings | None = None):
    from pdr.broker import TRANSPORTS
    from pdr.broker.http import OfflineTransport, offline, real_transport
    t = TRANSPORTS.get(provider)
    if t is not None:
        # PDR_OFFLINE=1 also stops real transports registered from pdr/ (e.g. llm.AnthropicTransport)
        if offline() and type(t).__module__.startswith("pdr.") and not isinstance(t, OfflineTransport):
            return OfflineTransport(provider)
        return t
    return real_transport(provider, settings)


def transport_specs() -> dict:
    """Registered transports that can be rebuilt in a child process (FakeTransport.spec())."""
    from pdr.broker import TRANSPORTS
    return {name: t.spec() for name, t in TRANSPORTS.items() if hasattr(t, "spec")}


# --------------------------------------------------------------------------- execute (inline or in the child)

def execute(conn: sqlite3.Connection, *, settings: Settings, provider: str, endpoint: str, request: dict,
            key: str, run_id: str, dispute_key: str | None, step: str, transport=None) -> tuple[CallResult, Decimal]:
    """Throttle, send with retries (one ledger row per attempt), then cache + ledger in one transaction."""
    cfg = provider_cfg(settings, provider)
    transport = transport or get_transport(provider, settings)
    retries = int(cfg["retry_limit"])
    backoff = list(cfg["backoff_seconds"]) or [1]
    summary = summarize(endpoint, request)
    spent = Decimal("0")
    last_entry, last_status, last_err = "", None, ""
    for attempt in range(1, retries + 2):
        throttle.acquire(conn, provider, int(cfg["rate_per_minute"] or 0))
        started = now_utc()
        resp: Response | None = None
        offline = False
        try:
            resp = transport.send(endpoint, request, cache_key=key, timeout=float(cfg["timeout_seconds"]))
        except (TransportTimeout, TransportError, TimeoutError, ConnectionError, OSError) as e:
            offline = isinstance(e, TransportOffline)
            last_err, last_status = f"{type(e).__name__}: {e}"[:200], None
        retryable = resp is None or resp.status == 429 or resp.status >= 500
        final = not retryable or offline or attempt == retries + 1
        credits = price_credits(cfg, endpoint, request, resp) if resp is not None else Decimal("0")
        spent += credits
        if not retryable:
            ok = resp.status < 400
            store = ok and cacheable(provider, request, resp)
            crashpoint("call.after_response_before_cache")
            with tx(conn) as c:
                if store:
                    c.execute("INSERT OR REPLACE INTO call_cache(cache_key, status, headers, body, stored_at)"
                              " VALUES (?,?,?,?,?)", (key, resp.status, json.dumps(dict(resp.headers), default=str),
                                                     json.dumps(resp.body, default=str), now_utc().isoformat()))
                outcome = ("empty" if is_empty(endpoint, resp.body) else "ok") if ok else "failed"
                e = write_ledger(c, settings=settings, run_id=run_id, dispute_key=dispute_key, key=key, step=step,
                                 provider=provider, endpoint=endpoint, credits=credits, cache_hit=False,
                                 attempt_no=attempt, outcome=outcome, summary=summary, started_at=started,
                                 ended_at=now_utc())
                c.execute("UPDATE call_intents SET status=? WHERE cache_key=?", ("done" if ok else "failed", key))
            crashpoint("call.after_cache")
            return CallResult(ok=ok, status=resp.status, body=resp.body, cache_hit=False, credits=spent,
                              ledger_entry_id=e.entry_id, error=None if ok else f"http {resp.status}",
                              headers=dict(resp.headers)), spent
        if resp is not None:
            last_status, last_err = resp.status, f"http {resp.status}"
        with tx(conn) as c:
            e = write_ledger(c, settings=settings, run_id=run_id, dispute_key=dispute_key, key=key, step=step,
                             provider=provider, endpoint=endpoint, credits=credits, cache_hit=False,
                             attempt_no=attempt, outcome="failed" if final else "retry",
                             summary=f"{summary} [{last_err}]", started_at=started, ended_at=now_utc())
            if final:
                c.execute("UPDATE call_intents SET status='failed' WHERE cache_key=?", (key,))
        last_entry = e.entry_id
        if final:
            break
        if not final:
            wait = float(backoff[min(attempt - 1, len(backoff) - 1)])
            ra = header(resp.headers, "retry-after") if resp is not None else None
            if ra not in (None, ""):
                try:
                    wait = max(wait, float(ra))
                except ValueError:
                    pass
            SLEEP(wait * (1 + random.random() * 0.2))
    return CallResult(ok=False, status=last_status, body=None, cache_hit=False, credits=spent,
                      ledger_entry_id=last_entry, error=last_err or "failed"), spent


# --------------------------------------------------------------------------- paid_call

def paidcall_mode(settings: Settings, store: Any, provider: str = "crustdata") -> str:
    """detached|inline; inline on Windows, for in-memory stores and for free "web" calls."""
    mode = os.environ.get("PDR_PAIDCALL_MODE") or getattr(settings.providers, "paidcall_mode", "inline")
    if os.name == "nt" or is_memory(store) or provider == "web":
        return "inline"
    return mode


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _cached(conn: sqlite3.Connection, key: str):
    return conn.execute("SELECT status, headers, body FROM call_cache WHERE cache_key=?", (key,)).fetchone()


def _cache_hit_result(conn, ctx: RunContext, key: str, provider: str, endpoint: str, request: dict, step: str,
                      row) -> CallResult:
    status, headers, body = row
    started = now_utc()
    with tx(conn) as c:
        e = write_ledger(c, settings=ctx.settings, run_id=ctx.run_id, dispute_key=ctx.dispute_key, key=key, step=step,
                         provider=provider, endpoint=endpoint, credits=Decimal("0"), cache_hit=True, attempt_no=1,
                         outcome="empty" if is_empty(endpoint, json.loads(body)) else "ok",
                         summary=summarize(endpoint, request), started_at=started, ended_at=started)
    return CallResult(ok=True, status=status, body=json.loads(body), cache_hit=True, credits=Decimal("0"),
                      ledger_entry_id=e.entry_id, headers=json.loads(headers or "{}"))


def _wait_for_running(conn, key: str, max_wait: float) -> None:
    """Another live process holds this intent: wait until it finishes, fails or dies."""
    t_end = time.time() + max_wait
    while time.time() < t_end:
        row = conn.execute("SELECT status FROM call_intents WHERE cache_key=?", (key,)).fetchone()
        if _cached(conn, key) or not row or not str(row[0]).startswith("running:"):
            return
        if not _pid_alive(int(str(row[0]).split(":")[1])):
            return
        time.sleep(0.05)


def _recover(conn, ctx: RunContext, key: str, provider: str, endpoint: str, request: dict, step: str,
             max_wait: float) -> None:
    """A pending/running intent from a process that died: wait for it, or ledger in_flight_at_crash."""
    row = conn.execute("SELECT status, request, created_at FROM call_intents WHERE cache_key=?", (key,)).fetchone()
    if not row or row[0] in ("done", "failed"):
        return
    status = str(row[0])
    if status.startswith("running:") and int(status.split(":")[1]) == os.getpid() and key in _inflight:
        t_end = time.time() + max_wait  # another thread of this process is on it
        while key in _inflight and time.time() < t_end:
            time.sleep(0.05)
        return
    if status.startswith("running:") and int(status.split(":")[1]) != os.getpid():
        _wait_for_running(conn, key, max_wait)
        if _cached(conn, key):
            return
        row = conn.execute("SELECT status, request, created_at FROM call_intents WHERE cache_key=?", (key,)).fetchone()
        if not row or row[0] in ("done", "failed"):
            return
    env = json.loads(row[1] or "{}")
    worst = Decimal(str(env.get("worst_case_credits", "0")))
    with tx(conn) as c:
        write_ledger(c, settings=ctx.settings, run_id=env.get("run_id") or ctx.run_id,
                     dispute_key=env.get("dispute_key", ctx.dispute_key), key=key, step=env.get("step", step),
                     provider=provider, endpoint=endpoint, credits=worst, cache_hit=False, attempt_no=1,
                     outcome="in_flight_at_crash",
                     summary=f"{summarize(endpoint, request)} [cost unknown, assume charged]",
                     started_at=datetime.fromisoformat(row[2]) if row[2] else now_utc(), ended_at=None)
        c.execute("UPDATE call_intents SET status='failed' WHERE cache_key=?", (key,))


def _max_wait(cfg: dict) -> float:
    return (float(cfg["timeout_seconds"]) + 5) * (int(cfg["retry_limit"]) + 1) + sum(map(float, cfg["backoff_seconds"])) * 1.2 + 120


def _reserve(conn, ctx: RunContext, key: str, worst: Decimal) -> bool:
    """Budget and deadline check; reserves the worst case. False if another thread holds this key."""
    with _budget_lock:
        if key in _inflight:
            return False
        if ctx.past_deadline():
            raise BudgetReached("run deadline reached")
        run_spent, ded_spent = _spent(conn, ctx.run_id, ctx.dispute_key)
        reserved = _reserved.get(ctx.run_id, Decimal("0"))
        run_used = max(run_spent, ctx.spent_credits) + reserved
        if worst > 0 and run_used + worst > Decimal(str(ctx.settings.budget.run_cost_credits)):
            raise BudgetReached(f"run budget: used {run_used} + worst case {worst} > "
                                f"{ctx.settings.budget.run_cost_credits}")
        if worst > 0 and ctx.dispute_key and ded_spent + reserved + worst > Decimal(
                str(ctx.settings.budget.per_deduction_credits)):
            raise BudgetReached(f"per-deduction budget: used {ded_spent} + worst case {worst} > "
                                f"{ctx.settings.budget.per_deduction_credits}")
        _reserved[ctx.run_id] = reserved + worst
        _inflight.add(key)
        return True


def paid_call(provider: str, endpoint: str, request: dict, *, ctx: RunContext, step: str,
              worst_case_credits: Decimal) -> CallResult:
    """Raises BudgetReached, GuardViolation; returns ok=False after retries (-> PROVIDER_FAILED)."""
    guard.check(GUARD_DEST.get((provider, endpoint), "llm" if provider == "llm" else "web"), request)
    key = cache_key(provider, endpoint, request)
    conn = connect(ctx.store)
    cfg = provider_cfg(ctx.settings, provider)
    while True:  # the same request already running in another thread of this process: share its result
        with _budget_lock:
            busy = key in _inflight
        if not busy:
            break
        time.sleep(0.05)
    row = _cached(conn, key)
    if row is None:
        _recover(conn, ctx, key, provider, endpoint, request, step, _max_wait(cfg))
        row = _cached(conn, key)
    if row is not None:
        return _cache_hit_result(conn, ctx, key, provider, endpoint, request, step, row)

    worst = Decimal(str(worst_case_credits or 0))
    if not _reserve(conn, ctx, key, worst):  # lost a race with another thread: it will be a cache hit now
        return paid_call(provider, endpoint, request, ctx=ctx, step=step, worst_case_credits=worst_case_credits)
    try:
        mode = paidcall_mode(ctx.settings, ctx.store, provider)
        envelope = {"request": request, "step": step, "worst_case_credits": str(worst), "run_id": ctx.run_id,
                    "dispute_key": ctx.dispute_key}
        with tx(conn) as c:
            c.execute("INSERT OR REPLACE INTO call_intents(cache_key, provider, endpoint, request, status, run_id,"
                      " dispute_key, created_at) VALUES (?,?,?,?,?,?,?,?)",
                      (key, provider, endpoint, json.dumps(envelope, default=str),
                       f"running:{os.getpid()}" if mode == "inline" else "pending", ctx.run_id, ctx.dispute_key,
                       now_utc().isoformat()))
        crashpoint("call.after_intent")
        try:
            if mode == "detached":
                from pdr.broker.exec import run_detached
                result, spent = run_detached(conn, ctx, key, provider, endpoint, request, step, _max_wait(cfg))
            else:
                result, spent = execute(conn, settings=ctx.settings, provider=provider, endpoint=endpoint,
                                        request=request, key=key, run_id=ctx.run_id, dispute_key=ctx.dispute_key,
                                        step=step)
        except Exception:  # a bug or cassette miss, not a crash: nothing to recover on restart
            with tx(conn) as c:
                c.execute("UPDATE call_intents SET status='failed' WHERE cache_key=? AND status NOT IN ('done')",
                          (key,))
            raise
        with _budget_lock:
            ctx.spent_credits += spent
        return result
    finally:
        with _budget_lock:
            _reserved[ctx.run_id] = _reserved.get(ctx.run_id, Decimal("0")) - worst
            _inflight.discard(key)
