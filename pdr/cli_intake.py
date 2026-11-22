"""CLI: pdr intake / confirm / complete-set / export-terms / load-terms (mounted by pdr/cli.py)."""
from __future__ import annotations

import getpass
from datetime import date
from pathlib import Path
from typing import List, Optional

import typer

app = typer.Typer(help="Agreement intake")


def _ctx():
    from pdr.cli import command_ctx
    return command_ctx()


def _who(who: Optional[str]) -> str:
    return who or getpass.getuser()


@app.command()
def intake(client: str, files: List[Path], effective_from: Optional[str] = typer.Option(None, "--effective-from"),
           doc_type: str = typer.Option("agreement", "--doc-type"), who: Optional[str] = typer.Option(None, "--who")) -> None:
    """Store agreement files for a client and propose terms (unconfirmed) with page and verbatim text."""
    from pdr.intake.confirm import render
    from pdr.intake.extract import run_intake
    ctx = _ctx()
    eff = date.fromisoformat(effective_from) if effective_from else None
    for ag, res in run_intake(client, [str(f) for f in files], ctx, who=_who(who), effective_from=eff, doc_type=doc_type):
        typer.echo(f"{ag.agreement_id}: {len(res.terms)} proposed, {len(res.rejected)} rejected (verbatim not on page), "
                   f"effective_from {ag.effective_from}")
        for t in res.terms:
            typer.echo(render(t))
        for m in res.missing_documents:
            typer.echo(f"MISSING DOCUMENT: {m}  (request drafted)")
    typer.echo(f"next: pdr confirm {client} --all   (or --term <id>)")


@app.command()
def confirm(client: str, all_: bool = typer.Option(False, "--all"), term: List[str] = typer.Option([], "--term"),
            reject: List[str] = typer.Option([], "--reject"), who: Optional[str] = typer.Option(None, "--who"),
            show: bool = typer.Option(False, "--show", help="only list unconfirmed terms")) -> None:
    """Confirm (or reject) proposed terms; shows value beside the verbatim clause and page."""
    from pdr.intake.confirm import confirm as do_confirm, render, unconfirmed
    store = _ctx().store
    if show or not (all_ or term or reject):
        for t in unconfirmed(store, client):
            typer.echo(render(t))
        return
    changed, skipped = do_confirm(store, client, _who(who), term_ids=list(term), all_=all_, reject_ids=list(reject))
    for t in changed:
        typer.echo(f"{t.status}: " + render(t))
    for t in skipped:
        typer.echo(f"SKIPPED (low confidence, use --term {t.term_id}): {t.verbatim[:100]}")


@app.command("complete-set")
def complete_set(client: str, who: Optional[str] = typer.Option(None, "--who")) -> None:
    """State that the uploaded documents are the full set in force for the client."""
    from pdr.intake.confirm import complete_set as do_complete
    done, missing = do_complete(_ctx().store, client, _who(who))
    typer.echo(f"complete: {[a.agreement_id for a in done]}")
    for a in missing:
        typer.echo(f"still missing for {a.agreement_id}: {a.missing_documents}")


@app.command("export-terms")
def export_terms(client: str, out: Path) -> None:
    """Write the client's agreements and terms to a terms.yaml."""
    from pdr.adapters.k2_agreements import save_terms_file
    from pdr.models import Agreement, Term
    store = _ctx().store
    save_terms_file(out, store.find(Agreement, client_id=client), store.find(Term, client_id=client))
    typer.echo(f"wrote {out}")


@app.command("load-terms")
def load_terms(path: Path) -> None:
    """Load agreements and terms from a terms.yaml into the store."""
    from pdr.adapters.k2_agreements import load_terms_file
    store = _ctx().store
    ags, terms = load_terms_file(path)
    for x in [*ags, *terms]:
        store.put(x)
    typer.echo(f"loaded {len(ags)} agreements, {len(terms)} terms")
