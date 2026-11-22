"""`pdr alerts [scan|send|list]` (add-on E4)."""
from __future__ import annotations

import typer

app = typer.Typer()
alerts_app = typer.Typer(help="Alert outbox: scan the store, send queued alerts, list them.")
app.add_typer(alerts_app, name="alerts")


def _open():
    from pdr.settings import load_for_start
    from pdr.store import open_store
    return load_for_start(), open_store()


@alerts_app.command("scan")
def scan_cmd() -> None:
    """Queue alerts for new events (read-only over the store)."""
    from pdr.alerts import events
    settings, store = _open()
    keys = events.scan(store, settings)
    typer.echo(f"queued {len(keys)} alert(s)")


@alerts_app.command("send")
def send_cmd() -> None:
    """Reconcile, then deliver queued alerts once each."""
    from pdr.alerts import outbox
    settings, store = _open()
    res = outbox.safe_send(store, settings)
    typer.echo({k: (len(v) if isinstance(v, list) else v) for k, v in res.items() if k != "reconcile"})


@alerts_app.command("list")
def list_cmd(status: str = typer.Option("", help="queued|sending|sent|unconfirmed|failed")) -> None:
    """List alerts in the outbox."""
    from pdr.alerts import outbox
    settings, store = _open()
    for r in outbox.rows(store, status or None):
        typer.echo(f"{r['status']:12} {r['alert_key']}  attempts={r['attempts']}")
