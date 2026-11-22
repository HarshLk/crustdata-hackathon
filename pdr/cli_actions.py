"""CLI for actions (lane C2): approve, reject, override, send, actions, report, trail, pack. Mounted by pdr/cli.py."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer

app = typer.Typer(help="Approve and send actions; render the case file")


def _ctx():
    try:
        from pdr.cli import command_ctx
        return command_ctx()
    except ImportError:  # standalone use
        from datetime import datetime, timedelta, timezone
        from pdr.contracts import RunContext
        from pdr.settings import load_settings
        from pdr.store import open_store
        s = load_settings()
        return RunContext(run_id="cli", dispute_key=None, settings=s, store=open_store(),
                          deadline=datetime.now(timezone.utc) + timedelta(seconds=s.budget.run_time_seconds))


def _decide(fn_name: str, action_id: str, role: str, name: str, comment: Optional[str]) -> None:
    from pdr import approval
    try:
        a = getattr(approval, fn_name)(_ctx(), action_id, role, name, comment)
    except approval.ApprovalError as exc:
        typer.echo(f"refused: {exc}", err=True)
        raise typer.Exit(1)
    typer.echo(f"{a.action_id}: {a.status.value if hasattr(a.status, 'value') else a.status}")


@app.command()
def approve(action_id: str, role: str = typer.Option(..., "--role"), name: str = typer.Option(..., "--name"),
            comment: Optional[str] = typer.Option(None, "--comment")) -> None:
    """Approve this exact action version under a role."""
    _decide("approve", action_id, role, name, comment)


@app.command()
def reject(action_id: str, role: str = typer.Option(..., "--role"), name: str = typer.Option(..., "--name"),
           comment: Optional[str] = typer.Option(None, "--comment")) -> None:
    """Reject an action; it will never be sent."""
    _decide("reject", action_id, role, name, comment)


@app.command()
def override(action_id: str, role: str = typer.Option(..., "--role"), name: str = typer.Option(..., "--name"),
             comment: str = typer.Option(..., "--comment", help="written reason (required)")) -> None:
    """Release an OUT_OF_WINDOW hold with a written reason."""
    _decide("override", action_id, role, name, comment)


@app.command()
def send() -> None:
    """Reconcile `sending`, re-evaluate roles, re-audit and send approved actions exactly once."""
    from pdr import send as S
    res = S.run(_ctx())
    typer.echo(json.dumps({k: v for k, v in res.items() if v}, indent=2) if any(res.values()) else "nothing to send")


@app.command("actions")
def list_actions(status: Optional[str] = typer.Option(None, "--status")) -> None:
    """List actions with status and missing roles."""
    from pdr import approval
    from pdr.models import Action
    store = _ctx().store
    for a in sorted(store.find(Action), key=lambda a: a.action_id):
        if status and str(a.status) != status:
            continue
        miss = approval.missing_roles(store, a)
        typer.echo(f"{a.action_id}  {a.kind}  {a.status}  INR {a.amount_claimed}  waiting for: {', '.join(miss) or '-'}")


def _out(out: Optional[Path], settings) -> Path:
    return out or Path(settings.output.case_file.dir)


@app.command()
def report(out: Optional[Path] = typer.Option(None, "--out", help="output folder (default settings.output.case_file.dir)")) -> None:
    """Render the static case file (index.html, case pages, money.html)."""
    from pdr.outputs.report import render
    ctx = _ctx()
    paths = render(ctx.store, _out(out, ctx.settings), ctx.settings)
    typer.echo(f"wrote {len(paths)} pages; open {_out(out, ctx.settings) / 'index.html'}")


@app.command()
def trail(verify: bool = typer.Option(False, "--verify", help="check the hash chain")) -> None:
    """Show the audit trail, or verify its hash chain."""
    store = _ctx().store
    if verify:
        ok = store.trail_verify()
        typer.echo("trail OK: hash chain intact" if ok else "trail BROKEN: a row was edited or removed")
        raise typer.Exit(0 if ok else 1)
    rows = store.trail() if hasattr(store, "trail") else []
    for r in rows:
        typer.echo(f"{r.get('seq')}  {r.get('at')}  {r.get('event')}  {r.get('actor')}  {r.get('subject_id')}")


@app.command()
def pack(dispute_key: str, out: Optional[Path] = typer.Option(None, "--out")) -> None:
    """Print the case page path for a deduction (renders the case file first)."""
    from pdr.outputs.report import case_page, render
    ctx = _ctx()
    d = _out(out, ctx.settings)
    render(ctx.store, d, ctx.settings)
    p = d / case_page(dispute_key)
    if not p.exists():
        typer.echo(f"no case page for {dispute_key}", err=True)
        raise typer.Exit(1)
    typer.echo(str(p.resolve()))
