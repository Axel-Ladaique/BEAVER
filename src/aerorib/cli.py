"""Interface en ligne de commande AeroRib."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import NoReturn, Optional

import typer

from aerorib import __version__
from aerorib.classify import ClassifyError, cable_marker, classify
from aerorib.bambu import BambuError, open_in_bambu, read_template, vase_warning
from aerorib.config import AUTO_TEMPLATE, Config, embedded_templates, ConfigError, load_config, resolve_template
from aerorib.material_profiles import ProfileError, copy_profile, find_profiles, load_profile
from aerorib.export import export_results
from aerorib.layout import format_rotations
from aerorib.io_step import StepReadError, read_step
from aerorib.orient import orient
from aerorib.pipeline import PipelineError, run_model
from aerorib.report import format_bays, format_bores, format_text, write_html
from aerorib.validate import validate

app = typer.Typer(no_args_is_help=True, add_completion=False, pretty_exceptions_show_locals=False)


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
        # une gaine de câble est un longeron marqué : affichée « gaine »
        role = "gaine" if cb.is_cable else (cb.role or "—")
        how = cb.matched_by
        if cb.is_cable:
            how += f", marqueur de gaine '{cable_marker(cb.body, cfg)}'"
        how = f"   ({how})" if how else ""
        typer.echo(f"  {role:<6}  {cb.body.display}{how}")
        if cb.body.note:
            typer.echo(f"      ⚠ nom ambigu, {cb.body.note}")
        if cb.spar is not None:
            s = cb.spar
            typer.echo(
                f"      Ø {s.diameter:.1f} mm · axe {_fmt_vec(s.axis_dir)} · longueur {s.length:.1f} mm"
            )
        if cb.role == "servo":
            bb = cb.body.solid.bounding_box()
            typer.echo(f"      boîte {bb.size.X:.1f} × {bb.size.Y:.1f} × {bb.size.Z:.1f} mm")


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
    orient_flag: bool = typer.Option(
        False, "--orient", help="Oriente la pièce (axe du longeron) avant de vérifier."
    ),
) -> None:
    """Vérifie qu'une pièce est imprimable en vase mode."""
    cfg = _load(config)
    model = _classify(file, cfg)
    skins = model.of("skin")
    loc = None
    if orient_flag:
        try:
            loc = orient(model, cfg).location
        except ValueError as exc:
            _fail(str(exc))

    def placed(solid):
        return solid if loc is None else solid.moved(loc)

    originals = {}
    if original is not None:
        orig_skins = _skins(original, cfg)
        by_name = {s.body.name: placed(s.body.solid) for s in orig_skins}
        for s in skins:
            if s.body.name in by_name:
                originals[s.body.name] = by_name[s.body.name]
            elif len(skins) == 1 and len(orig_skins) == 1:
                originals[s.body.name] = placed(orig_skins[0].body.solid)
            else:
                _fail(f"pas de peau d'origine pour {s.body.name}")
    all_ok = True
    for s in skins:
        report = validate(placed(s.body.solid), cfg, original=originals.get(s.body.name))
        all_ok &= report.ok
        typer.echo(format_text(report, title=s.body.display))
        if html is not None:
            target = html if len(skins) == 1 else html.with_name(f"{html.stem}-{s.body.name}{html.suffix}")
            write_html(report, target, title=s.body.display)
            typer.echo(f"  rapport : {target}")
    if not all_ok:
        raise typer.Exit(1)


@app.command()
def profils(
    copier: Optional[str] = typer.Option(
        None, "--copier", help="Copie le profil embarqué indiqué dans ./profiles/ pour l'éditer."
    ),
) -> None:
    """Liste les profils matière (paramètres géométriques stockés) ou en copie un pour édition."""
    if copier is not None:
        try:
            target = copy_profile(copier, Path.cwd() / "profiles")
        except (ProfileError, OSError) as exc:
            _fail(str(exc))
        typer.echo(f"profil copié : {target}")
        typer.echo("modifiez-le : il prend le pas sur le profil embarqué (dans ce dossier de travail).")
        return
    try:
        infos = list(find_profiles().values())
    except ProfileError as exc:
        _fail(str(exc))
    for info in infos:
        try:
            data, _files = load_profile(info.name)
            template = (data.get("bambu") or {}).get("template", AUTO_TEMPLATE)
        except ProfileError as exc:
            typer.echo(f"  {info.name:<12} {info.origin:<11} ⚠ {exc}")
            continue
        label = "aucun"
        if template == AUTO_TEMPLATE:
            found = embedded_templates(info.name)
            label = "automatique : " + ", ".join(f.name for f in found) if found else "aucun"
        elif template:
            label = Path(template).name + ("" if Path(template).is_file() else " (introuvable)")
        typer.echo(f"  {info.name:<12} {info.origin:<11} gabarit Bambu : {label}   ({info.path})")


def _format_pieces(b, first_plate: int | None) -> str:
    """Morceaux d'une coupe dans la corde : nom, étendue en Z, validation."""
    items = [
        f"{n} (z = {a:.0f}–{z:.0f} mm{'' if r.ok else ', ✘'})"
        for n, (a, z), r in zip(b.names, b.spans, b.piece_reports)
    ]
    line = f"  morceaux : {len(b.segments)} — " + ", ".join(items)
    if first_plate is not None:
        line += f" → plateaux {first_plate} à {first_plate + len(b.segments) - 1}"
    return line


def _format_segments(segments, first_plate: int | None) -> str:
    z, spans = 0.0, []
    for s in segments:
        h = s.bounding_box().max.Z
        spans.append(f"{z:.1f}–{z + h:.1f}".replace(".0–", "–").removesuffix(".0"))
        z += h
    line = f"  tronçons : {len(segments)} (z = {', '.join(spans)} mm)"
    if first_plate is not None:
        line += f" → plateaux {first_plate} à {first_plate + len(segments) - 1}"
    return line


@app.command()
def run(
    file: Path = typer.Argument(..., help="Pièce CAO à traiter (STEP)."),
    config: Optional[Path] = typer.Option(None, "-c", "--config", help="Fichier de configuration YAML."),
    out: Optional[Path] = typer.Option(None, "--out", help="Dossier de sortie (défaut : output.dir de la config)."),
    three_mf: bool = typer.Option(False, "--3mf", help="Écrit aussi le projet Bambu Studio multi-plateaux (.3mf)."),
    open_: bool = typer.Option(False, "--open", help="Ouvre le projet dans Bambu Studio (implique --3mf)."),
) -> None:
    """Génère les nervures, vérifie et exporte la pièce imprimable en vase mode."""
    cfg = _load(config)
    open_ = open_ or cfg.bambu.open
    formats = list(cfg.output.formats)
    if (three_mf or open_) and "3mf" not in formats:
        formats.append("3mf")
    template = None
    if "3mf" in formats:
        try:
            template = read_template(resolve_template(cfg))
        except (ConfigError, BambuError) as exc:
            _fail(str(exc))
        if (warn := vase_warning(template)) is not None:
            typer.echo(f"  ⚠ {warn}")
    model = _classify(file, cfg)
    try:
        result = run_model(model, cfg, progress=lambda m: typer.echo(f"… {m}"))
        for b in result.bodies:
            typer.echo(format_text(b.report, title=b.name))
            typer.echo(format_bores(b.bores, b.filled, b.tubes))
            if b.bays:
                typer.echo(format_bays(b.bays, b.z_heights))
        plate = 1
        for b in result.bodies:
            for n in b.notices:
                typer.echo(f"  ℹ {n}")
            if b.piece_reports:
                typer.echo(_format_pieces(b, plate if template else None))
            elif len(b.segments) > 1:
                typer.echo(_format_segments(b.segments, plate if template else None))
            for line in format_rotations(b.placements, list(b.names) if b.piece_reports else None):
                typer.echo(f"  {line}")
            for w in b.warnings:
                typer.echo(f"  ⚠ {w}")
            plate += max(len(b.segments), 1)
        target = out or Path(cfg.output.dir)
        try:
            paths = export_results(result, target, file.stem, formats, template)
        except OSError as exc:
            _fail(f"écriture impossible : {exc.filename or target} ({exc.strerror or exc})")
        for path in paths:
            typer.echo(f"  fichier : {path}")
        if open_:
            project = next((p for p in paths if p.suffix == ".3mf"), None)
            if project is not None and not result.ok:
                typer.echo("  ⚠ projet non imprimable : non ouvert dans Bambu Studio (fichiers écrits pour inspection)")
            elif project is not None:
                try:
                    open_in_bambu(project)
                except BambuError as exc:
                    typer.echo(f"  ⚠ {exc}")
    except (PipelineError, BambuError) as exc:
        _fail(str(exc))
    except typer.Exit:
        raise
    except Exception as exc:  # dernier recours : jamais de trace Python pour l'utilisateur
        _fail(f"erreur interne : {type(exc).__name__}: {exc} (merci de transmettre le fichier STEP)")
    if not result.ok:
        raise typer.Exit(1)


@app.command("gui")
def gui(
    dossier: Path = typer.Option(Path("projets"), "--dossier", help="Dossier racine des projets."),
    port: int = typer.Option(0, "--port", help="Port d'écoute (0 = port libre)."),
    sans_navigateur: bool = typer.Option(False, "--sans-navigateur", help="N'ouvre pas le navigateur."),
) -> None:
    """Lance l'interface graphique locale dans le navigateur."""
    from aerorib.gui import server

    server.run_gui(dossier, port, not sans_navigateur)
