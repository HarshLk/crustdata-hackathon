"""CLI for the decision log (add-on E1): `pdr log <dispute_key>`. Mounted by pdr/cli.py."""
from __future__ import annotations

from typing import Optional

import typer

app = typer.Typer(help="Show how each verdict was decided")


@app.command("log")
def log_cmd(dispute_key: str, verdict_id: Optional[str] = typer.Option(None, "--verdict", help="one verdict version only")) -> None:
    """Print the decision log of a deduction, every verdict version in order."""
    from pdr.cli_actions import _ctx
    from pdr.engine.decision_log import line
    store = _ctx().store
    entries = store.decision_log(dispute_key, verdict_id)
    if not entries:
        typer.echo(f"no decision log for {dispute_key}")
        raise typer.Exit(1)
    current = None
    for e in entries:
        if e["verdict_id"] != current:
            current = e["verdict_id"]
            typer.echo(f"v{e['version']} verdict {current} (run {e.get('run_id')}, settings {e['rule']['settings_version']})")
        typer.echo("  " + line(e))
        for c in e.get("evidence_considered", []):
            typer.echo(f"      {c}")
        for n in e.get("numbers", []):
            typer.echo(f"      = {n}")
