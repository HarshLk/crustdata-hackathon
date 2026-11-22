"""pdr command line (lane C owns; W0 ships only `version`)."""
from __future__ import annotations

import typer

from pdr import __version__

app = typer.Typer(help="Penalty-deduction recovery service", no_args_is_help=True)


@app.command()
def version() -> None:
    """Print the package version."""
    typer.echo(__version__)


@app.callback()
def _main() -> None:
    pass


if __name__ == "__main__":
    app()
