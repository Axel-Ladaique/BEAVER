"""Interface en ligne de commande AeroRib."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import NoReturn, Optional

import typer

from aerorib import __version__
from aerorib.classify import ClassifyError, classify
from aerorib.config import Config, ConfigError, load_config
from aerorib.io_step import StepReadError, read_step
from aerorib.report import format_text, write_html
from aerorib.validate import validate

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
    # symboles ✔ ✘ ≈ Ø ⚠ absents de cp1252 : sortie redirigée sous Windows
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _fail(message: str) -> NoReturn:
    typer.echo(f"erreur : {message}")
    raise typer.Exit(1)


def _load(cfg_path: Optional[Path]) -> Config:
    try:
        return load_config(cfg_path)
    except ConfigError as exc:
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
    model = _classify(file, cfg)
    typer.echo(f"{file.name} : {len(model.bodies)} corps")
    for cb in model.bodies:
        role = cb.role or "—"
        how = f"   ({cb.matched_by})" if cb.matched_by else ""
        typer.echo(f"  {role:<6}  {cb.body.display}{how}")
        if cb.body.note:
            typer.echo(f"      ⚠ nom ambigu, {cb.body.note}")
        if cb.spar is not None:
            s = cb.spar
            typer.echo(
                f"      Ø {s.diameter:.1f} mm · axe {_fmt_vec(s.axis_dir)} · longueur {s.length:.1f} mm"
            )


def _classify(file: Path, cfg: Config):
    try:
        return classify(read_step(file), cfg)
    except (FileNotFoundError, StepReadError, ClassifyError) as exc:
        _fail(str(exc))
    except OSError as exc:
        _fail(f"lecture impossible : {file} ({exc.strerror or exc})")


def _skins(file: Path, cfg: Config):
    return _classify(file, cfg).of("skin")


@app.command()
def check(
    file: Path = typer.Argument(..., help="Pièce à vérifier (STEP)."),
    config: Optional[Path] = typer.Option(None, "-c", "--config", help="Fichier de configuration YAML."),
    original: Optional[Path] = typer.Option(None, "--original", help="STEP d'origine, pour vérifier la peau."),
    html: Optional[Path] = typer.Option(None, "--html", help="Écrit un rapport HTML."),
) -> None:
    """Vérifie qu'une pièce est imprimable en vase mode."""
    cfg = _load(config)
    skins = _skins(file, cfg)
    originals = {}
    if original is not None:
        orig_skins = _skins(original, cfg)
        by_name = {s.body.name: s.body.solid for s in orig_skins}
        for s in skins:
            if s.body.name in by_name:
                originals[s.body.name] = by_name[s.body.name]
            elif len(skins) == 1 and len(orig_skins) == 1:
                originals[s.body.name] = orig_skins[0].body.solid
            else:
                _fail(f"pas de peau d'origine pour {s.body.name}")
    all_ok = True
    for s in skins:
        report = validate(s.body.solid, cfg, original=originals.get(s.body.name))
        all_ok &= report.ok
        typer.echo(format_text(report, title=s.body.display))
        if html is not None:
            target = html if len(skins) == 1 else html.with_name(f"{html.stem}-{s.body.name}{html.suffix}")
            write_html(report, target, title=s.body.display)
            typer.echo(f"  rapport : {target}")
    if not all_ok:
        raise typer.Exit(1)
