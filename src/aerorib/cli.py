"""Interface en ligne de commande AeroRib."""

from __future__ import annotations

import typer

from aerorib import __version__

app = typer.Typer(no_args_is_help=True, add_completion=False)


def _print_version(value: bool) -> None:
    if value:
        typer.echo(f"aerorib {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False, "--version", callback=_print_version, is_eager=True, help="Affiche la version."
    ),
) -> None:
    """AeroRib : structure interne pour impression vase mode."""
