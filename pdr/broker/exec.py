"""Detached executor (D-07): `python -m pdr.broker.exec <cache_key> <job.json>` runs one paid call in its own
session and writes cache + ledger itself, so killing the parent mid-request loses nothing.
Also: paid_calls_parallel, bounded by providers.paid_concurrency.
"""
from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
from typing import Any

from pdr.broker.db import connect, db_path, tx
from pdr.contracts import CallResult, RunContext

REPO_ROOT = Path(__file__).resolve().parents[2]


def _job_dir(path: str) -> Path:
    d = Path(path).resolve().parent / ".broker_jobs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _paid_rows_since(conn, key: str, since: str) -> list[dict]:
    rows = conn.execute("SELECT body FROM ledger WHERE cache_key=? AND cache_hit=0 AND started_at>=? "
                        "ORDER BY started_at", (key, since)).fetchall()
    return [json.loads(r[0]) for r in rows]


def run_detached(conn, ctx: RunContext, key: str, provider: str, endpoint: str, request: dict, step: str,
                 max_wait: float) -> tuple[CallResult, Decimal]:
    """Spawn the child, poll the cache; fall back to inline if the child dies before it starts."""
    from pdr.broker.core import _cached, execute, now_utc, write_ledger, summarize
    path = db_path(ctx.store)
    since = conn.execute("SELECT created_at FROM call_intents WHERE cache_key=?", (key,)).fetchone()[0]
    jobdir = _job_dir(path)
    from pdr.broker.core import transport_specs
    job = {"db": str(Path(path).resolve()), "settings": ctx.settings.model_dump(mode="json"),
           "transports": transport_specs()}
    jobfile = jobdir / f"{key}.json"
    jobfile.write_text(json.dumps(job, default=str), encoding="utf-8")
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(REPO_ROOT), os.getcwd(), env.get("PYTHONPATH", "")])
    with open(jobdir / f"{key}.log", "ab") as log:
        proc = subprocess.Popen([sys.executable, "-m", "pdr.broker.exec", key, str(jobfile)], cwd=str(REPO_ROOT),
                                env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    t_end = time.time() + max_wait
    while time.time() < t_end:
        row = _cached(conn, key)
        status = conn.execute("SELECT status FROM call_intents WHERE cache_key=?", (key,)).fetchone()
        status = status[0] if status else "failed"
        if row is not None or status in ("done", "failed"):
            proc.wait(timeout=30)
            rows = _paid_rows_since(conn, key, since)
            spent = sum((Decimal(str(r.get("units", "0"))) for r in rows), Decimal("0"))
            last = rows[-1]["entry_id"] if rows else ""
            if row is not None:
                return CallResult(ok=True, status=row[0], body=json.loads(row[2]), cache_hit=False, credits=spent,
                                  ledger_entry_id=last), spent
            err = rows[-1].get("request_summary", "failed") if rows else "failed"
            return CallResult(ok=False, status=None, body=None, cache_hit=False, credits=spent,
                              ledger_entry_id=last, error=err), spent
        if proc.poll() is not None:  # child gone without a result
            if str(status).startswith("running:"):
                with tx(conn) as c:
                    write_ledger(c, settings=ctx.settings, run_id=ctx.run_id, dispute_key=ctx.dispute_key, key=key,
                                 step=step, provider=provider, endpoint=endpoint,
                                 credits=Decimal("0"), cache_hit=False, attempt_no=1, outcome="in_flight_at_crash",
                                 summary=f"{summarize(endpoint, request)} [executor died; cost unknown]",
                                 started_at=now_utc())
            with tx(conn) as c:
                c.execute("UPDATE call_intents SET status=? WHERE cache_key=?", (f"running:{os.getpid()}", key))
            return execute(conn, settings=ctx.settings, provider=provider, endpoint=endpoint, request=request,
                           key=key, run_id=ctx.run_id, dispute_key=ctx.dispute_key, step=step)
        time.sleep(0.05)
    return CallResult(ok=False, status=None, body=None, cache_hit=False, credits=Decimal("0"), ledger_entry_id="",
                      error="detached executor timed out"), Decimal("0")


def _build_transport(spec: dict) -> Any:
    mod, _, name = spec["factory"].partition(":")
    return getattr(importlib.import_module(mod), name)(**spec.get("kwargs", {}))


def main(argv: list[str]) -> int:
    """Child entry point: claim the intent, execute, write cache + ledger."""
    from pdr.broker import register_transport
    from pdr.broker.core import execute
    from pdr.settings import settings_from_dict
    key, jobfile = argv[0], argv[1]
    job = json.loads(Path(jobfile).read_text(encoding="utf-8"))
    for name, spec in (job.get("transports") or {}).items():
        register_transport(name, _build_transport(spec))
    settings = settings_from_dict(job["settings"])
    conn = connect(job["db"])
    with tx(conn) as c:
        row = c.execute("SELECT provider, endpoint, request, status, run_id, dispute_key FROM call_intents "
                        "WHERE cache_key=?", (key,)).fetchone()
        if row is None or row[3] != "pending":
            return 0
        c.execute("UPDATE call_intents SET status=? WHERE cache_key=?", (f"running:{os.getpid()}", key))
    provider, endpoint, envelope, _, run_id, dispute_key = row
    env = json.loads(envelope)
    execute(conn, settings=settings, provider=provider, endpoint=endpoint, request=env["request"], key=key,
            run_id=run_id, dispute_key=dispute_key, step=env.get("step", "call"))
    try:
        Path(jobfile).unlink()
    except OSError:
        pass
    return 0


def paid_calls_parallel(specs: list[dict], ctx: RunContext, *, return_exceptions: bool = False) -> list[Any]:
    """Run paid calls concurrently (at most providers.paid_concurrency); results in input order.

    Each spec: {provider, endpoint, request, step, worst_case_credits}. Without return_exceptions the first
    exception (e.g. BudgetReached) is raised after all calls finish.
    """
    from pdr.broker import paid_call
    n = max(1, int(getattr(ctx.settings.providers, "paid_concurrency", 1) or 1))

    def one(s: dict):
        try:
            return paid_call(s["provider"], s["endpoint"], s["request"], ctx=ctx, step=s.get("step", "call"),
                             worst_case_credits=Decimal(str(s.get("worst_case_credits", 0))))
        except Exception as e:  # noqa: BLE001 - returned or re-raised below
            return e

    with ThreadPoolExecutor(max_workers=n) as pool:
        results = list(pool.map(one, specs))
    if not return_exceptions:
        for r in results:
            if isinstance(r, Exception):
                raise r
    return results


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
