"""Interface en ligne de commande AeroRib."""

from __future__ import annotations

from pathlib import Path
from typing import NoReturn, Optional

import typer

from aerorib import __version__
from aerorib.classify import ClassifyError, classify
from aerorib.config import Config, ConfigError, load_config
from aerorib.io_step import StepReadError, read_step

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


def _fail(message: str) -> NoReturn:
    typer.echo(f"erreur : {message}")
    raise typer.Exit(1)


def _load(cfg_path: Optional[Path]) -> Config:
    try:
        return load_config(cfg_path)
    except (ConfigError, OSError) as exc:
        _fail(str(exc))


def _fmt_vec(v) -> str:
    return "(" + ", ".join(f"{c:g}" for c in v) + ")"


@app.command()
def inspect(
    file: Path = typer.Argument(..., help="Fichier STEP à analyser."),
    config: Optional[Path] = typer.Option(None, "-c", "--config", help="Fichier de configuration YAML."),
) -> None:
    """Affiche les corps trouvés et le rôle attribué à chacun."""
    cfg = _load(config)
    try:
        model = classify(read_step(file), cfg)
    except (FileNotFoundError, StepReadError, ClassifyError) as exc:
        _fail(str(exc))
    typer.echo(f"{file.name} : {len(model.bodies)} corps")
    for cb in model.bodies:
        role = cb.role or "—"
        how = f"   ({cb.matched_by})" if cb.matched_by else ""
        typer.echo(f"  {role:<6}  {cb.body.display}{how}")
        if cb.spar is not None:
            s = cb.spar
            typer.echo(
                f"      Ø {s.diameter:.1f} mm · axe {_fmt_vec(s.axis_dir)} · longueur {s.length:.1f} mm"
            )
