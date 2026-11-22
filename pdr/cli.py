"""pdr command line (DESIGN 12). Lane C owns this file; other lanes' commands are mounted below."""
from __future__ import annotations

import importlib
import sys
import traceback
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

import typer

from pdr import __version__

app = typer.Typer(help="Penalty-deduction recovery service", no_args_is_help=True, add_completion=False)

NO_START_CHECK = {"doctor", "version", None}
_state: dict[str, Any] = {}


def _err(msg: str) -> None:
    typer.echo(msg, err=True)


def settings_and_store():
    """Validated settings (refusing to start per D-19) and the SQLite store, cached for this process."""
    if "store" not in _state:
        from pdr.settings import SettingsError, load_for_start, record_version
        from pdr.store import open_store
        try:
            settings = load_for_start(_state.get("settings_path"))
        except SettingsError as e:
            _err(str(e))
            raise typer.Exit(2)
        store = open_store()
        record_version(store, settings)
        _state.update(settings=settings, store=store)
    return _state["settings"], _state["store"]


def command_ctx(time_limit: float | None = None, record: bool = False):
    """A RunContext for a command; `record` writes a runs row (pdr run)."""
    settings, store = settings_and_store()
    if record:
        from pdr.runctx import new_run
        return new_run(store, settings, time_limit=time_limit)
    from datetime import datetime, timedelta, timezone
    from pdr.contracts import RunContext
    secs = time_limit if time_limit is not None else settings.budget.run_time_seconds
    return RunContext(run_id="cli", dispute_key=None, settings=settings, store=store,
                      deadline=datetime.now(timezone.utc) + timedelta(seconds=float(secs)))


def reconcile(ctx) -> None:
    """Every command begins by reconciling `sending` actions (pdr.send, lane C2), when present."""
    try:
        send = importlib.import_module("pdr.send")
    except ImportError:
        return
    try:
        res = send.reconcile(ctx)
        moved = {k: v for k, v in (res or {}).items() if v}
        if moved:
            _err(f"reconcile: {moved}")
    except Exception as e:  # noqa: BLE001 - reconcile problems are reported, never fatal
        _err(f"reconcile failed: {type(e).__name__}: {e}")


@app.callback()
def _main(ctx: typer.Context,
          settings: Optional[Path] = typer.Option(None, "--settings", help="settings.yaml path (default $PDR_SETTINGS or ./settings.yaml)")) -> None:
    if settings:
        _state["settings_path"] = str(settings)
    if ctx.invoked_subcommand in NO_START_CHECK:
        return
    rctx = command_ctx()
    _cleanup(Path(rctx.settings.output.case_file.dir))  # covers `pdr report` on the default folder
    reconcile(rctx)


def _cleanup(out: Path) -> None:
    """Remove temp files a killed output write left behind."""
    from pdr.outputs.atomic import cleanup_stale
    n = cleanup_stale(out)
    if n:
        _err(f"removed {n} stale temp file(s) from {out}")


@app.command()
def version() -> None:
    """Print the package version."""
    typer.echo(__version__)


@app.command()
def doctor(online: bool = typer.Option(False, "--online", help="also check the provider is reachable (TCP only)")) -> None:
    """Preflight: python, dependencies, settings, data dir, keys (names only), provider reachability."""
    from pdr.doctor import failed, run_checks
    checks = run_checks(_state.get("settings_path"), online=online)
    for c in checks:
        typer.echo(f"{c.status.upper():<5} {c.name:<24} {c.detail}")
    if failed(checks):
        typer.echo("doctor: FAILED (fix the lines marked FAIL)")
        raise typer.Exit(1)
    typer.echo("doctor: ok")


@app.command()
def init() -> None:
    """Create the store, validate settings, print the settings version."""
    settings, store = settings_and_store()
    typer.echo(f"store: {store.path}")
    typer.echo(f"settings version: {settings.version}")


# ----------------------------------------------------------------------------- run

def _step(label: str, fn, *args, missing: str = "", **kw):
    """Run one wiring step; a missing lane or an error degrades with a clear message instead of a crash."""
    try:
        return True, fn(*args, **kw)
    except ImportError as e:
        _err(f"[{label}] not available: {missing or e} ({e})")
    except typer.Exit:
        raise
    except Exception as e:  # noqa: BLE001
        _err(f"[{label}] failed: {type(e).__name__}: {e}")
        _err("".join(traceback.format_exception(e)[-3:]).rstrip())
    return False, None


def _load(path: Path, ctx):
    try:
        from pdr.adapters.reader import load_request
    except ImportError as e:
        if not (Path(path) / "case.yaml").exists():
            raise
        return _load_case_folder(path, ctx, e)
    return load_request(path, ctx)


def _load_case_folder(path: Path, ctx, why: Exception):
    """Fallback while the request reader is absent: a test-case folder loads through tests.caselib."""
    from tests.caselib import load_case  # only importable from the repository
    from pdr.models import Deduction
    from pdr.settings import with_overrides
    _err(f"[read] request reader (lane D) unavailable ({why}); loading the test-case folder with tests.caselib")
    keys, _expected, overrides = load_case(path, ctx.store)
    if overrides:
        ctx.settings = with_overrides(ctx.settings, overrides)
    return [d for d in (ctx.store.get(Deduction, k) for k in keys) if d is not None]


def _engine_run(keys, ctx):
    from pdr import engine
    if not hasattr(engine, "run"):
        raise ImportError("pdr.engine.run")
    return engine.run(keys, ctx)


def _draft_all(verdicts, ctx) -> int:
    from pdr.actions import draft_for_verdict
    n = 0
    for v in verdicts:
        try:
            if draft_for_verdict(v, ctx) is not None:
                n += 1
        except Exception as e:  # noqa: BLE001 - one bad draft never stops the others
            _err(f"[actions] draft for {v.dispute_key} failed: {type(e).__name__}: {e}")
    return n


def _report(store, out, settings):
    from pdr.outputs.report import render
    return render(store, out, settings)


def verdict_rows(store, settings, keys: list[str] | None = None) -> list[dict]:
    from pdr.outputs.derived import run_cost_inr
    from pdr.outputs.k5 import current_verdicts
    rows = []
    for v in current_verdicts(store):
        if keys is not None and v.dispute_key not in keys:
            continue
        why = ",".join(str(getattr(g, "value", g)) for g in v.grounds) or str(getattr(v.primary_reason, "value", v.primary_reason) or "")
        rows.append({"dispute_key": v.dispute_key, "outcome": str(getattr(v.outcome, "value", v.outcome)),
                     "grounds/reason": why, "amount_disputable": str(v.amount_disputable),
                     "cost_inr": run_cost_inr(v, [], settings, store)})
    return rows


@app.command()
def run(in_: Path = typer.Option(..., "--in", help="request: a folder or file of deductions"),
        out: Optional[Path] = typer.Option(None, "--out", help="output directory (default settings.output.case_file.dir)"),
        time_limit: Optional[float] = typer.Option(None, "--time-limit", help="seconds for this run")) -> None:
    """Read the request, run both passes, write K5 output, draft actions, render the case file."""
    from pdr.economics import table
    from pdr.log import log_event, set_console
    from pdr.outputs.k5 import current_verdicts, write_all
    from pdr.runctx import finish_run

    settings, store = settings_and_store()
    out = out or Path(settings.output.case_file.dir)
    _cleanup(out)
    ctx = command_ctx(time_limit, record=True)
    set_console(True)
    store.on_stage = lambda key, stage: log_event("stage", str(getattr(stage, "value", stage)), "saved", dispute_key=key)
    typer.echo(f"run {ctx.run_id}  settings {settings.version}  in {in_}  out {out}")

    ok, deds = _step("read", _load, in_, ctx, missing="pdr.adapters.reader (lane D) is not on main")
    if ok:
        keys = [d.dispute_key for d in deds or []]
    else:
        keys = [d.dispute_key for d in store.unfinished()]
        _err(f"[read] continuing with the {len(keys)} undecided deductions already in the store")
    log_event("run", "read", "ok" if ok else "degraded", n=len(keys))

    ok, _ = _step("engine", _engine_run, keys, ctx, missing="pdr.engine.run (lane B) is not on main")
    current = {v.dispute_key: v for v in current_verdicts(store)}
    verdicts = [current[k] for k in keys if k in current]
    _step("k5", write_all, store, out, settings)
    ok, n = _step("actions", _draft_all, verdicts, ctx, missing="pdr.actions (lane C2) is not on main")
    if ok:
        typer.echo(f"actions drafted: {n}")
    _step("report", _report, store, out, settings, missing="pdr.outputs.report (lane C2) is not on main")

    rows = verdict_rows(store, settings, keys)
    undecided = [k for k in keys if k not in current]
    finish_run(ctx, {"deductions": len(keys), "verdicts": len(rows), "undecided": len(undecided)})
    set_console(False)
    typer.echo(table(rows, ["dispute_key", "outcome", "grounds/reason", "amount_disputable", "cost_inr"]))
    typer.echo(f"{len(rows)}/{len(keys)} deductions have a verdict; output {out / 'verdicts.jsonl'}")
    if undecided or not keys:
        for k in undecided:
            _err(f"no verdict: {k}")
        raise typer.Exit(1)


# ----------------------------------------------------------------------------- views

@app.command()
def status(dispute_key: Optional[str] = typer.Argument(None)) -> None:
    """Queue and stage table, or one deduction's detail."""
    from pdr.economics import table
    from pdr.models import Deduction
    from pdr.outputs.k5 import current_verdicts, render_line, evidence_for
    settings, store = settings_and_store()
    current = {v.dispute_key: v for v in current_verdicts(store)}
    if dispute_key:
        d = store.get(Deduction, dispute_key)
        if d is None:
            _err(f"no deduction {dispute_key}")
            raise typer.Exit(1)
        typer.echo(f"{d.dispute_key}  client {d.client_id}  amount {d.amount}  stage {d.stage.value}")
        rows = store.query("SELECT stage, inputs_hash, saved_at FROM stage_results WHERE dispute_key=? ORDER BY saved_at",
                           (dispute_key,))
        typer.echo(table(rows))
        v = current.get(dispute_key)
        if v:
            import json
            typer.echo(json.dumps(render_line(v, evidence_for(store, v), settings, store), indent=2, default=str))
        return
    rows = []
    for d in store.find(Deduction):
        v = current.get(d.dispute_key)
        rows.append({"dispute_key": d.dispute_key, "stage": d.stage.value, "amount": str(d.amount),
                     "outcome": str(getattr(v.outcome, "value", v.outcome)) if v else "-",
                     "disputable": str(v.amount_disputable) if v else "-"})
    typer.echo(table(rows))


@app.command()
def ledger(by: str = typer.Option("deduction", "--by", help="deduction | run | client")) -> None:
    """Measured paid-call cost per deduction, run or client."""
    from pdr.economics import ledger_view, table
    settings, store = settings_and_store()
    try:
        typer.echo(table(ledger_view(store, settings, by)))
    except ValueError as e:
        _err(str(e))
        raise typer.Exit(2)


@app.command()
def economics() -> None:
    """Cost per deduction by verdict type, break-even fee rate, fee and margin (assumptions labelled)."""
    from pdr.economics import format_report, report
    settings, store = settings_and_store()
    typer.echo(format_report(report(store, settings)))


@app.command()
def recheck(dispute_key: str, live: bool = typer.Option(False, "--live", help="also run the refuter")) -> None:
    """Re-verify the citations of a deduction's current verdict (the auditor can only downgrade)."""
    from pdr.outputs.k5 import current_verdicts, evidence_for
    ctx = command_ctx()
    v = next((x for x in current_verdicts(ctx.store) if x.dispute_key == dispute_key), None)
    if v is None:
        _err(f"no current verdict for {dispute_key}")
        raise typer.Exit(1)
    try:
        from pdr.engine.audit import audit
    except ImportError as e:
        _err(f"[recheck] auditor not available: pdr.engine.audit (lane B) is not on main ({e})")
        raise typer.Exit(1)
    findings = audit(v, evidence_for(ctx.store, v), ctx, with_refuter=live)
    for f in findings:
        typer.echo(f"{'PASS' if f.passed else 'FAIL'}  {f.citation_evidence_id:<20} {f.rule}  {f.detail}")
    if any(not f.passed for f in findings):
        typer.echo(f"recheck: {dispute_key} would be downgraded; run `pdr run` again to re-decide")
        raise typer.Exit(1)
    typer.echo(f"recheck: {len(findings)} findings, all passed")


# ----------------------------------------------------------------------------- other lanes

def mount(module_name: str) -> bool:
    """Flatten another lane's typer sub-app (`app = typer.Typer()`) into the top level."""
    try:
        mod = importlib.import_module(module_name)
    except ImportError:
        return False
    sub = getattr(mod, "app", None)
    if sub is None:
        return False
    existing = {c.name or (c.callback.__name__ if c.callback else None) for c in app.registered_commands}
    for cmd in sub.registered_commands:
        name = cmd.name or (cmd.callback.__name__.replace("_", "-") if cmd.callback else None)
        if name not in existing:
            app.registered_commands.append(cmd)
    for grp in sub.registered_groups:
        app.registered_groups.append(grp)
    return True


for _m in ("pdr.cli_actions", "pdr.cli_intake", "pdr.cli_alerts"):
    try:
        mount(_m)
    except Exception as _e:  # noqa: BLE001 - a broken sub-app never takes the CLI down
        print(f"[cli] could not mount {_m}: {type(_e).__name__}: {_e}", file=sys.stderr)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
