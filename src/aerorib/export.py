"""Écriture des résultats : STEP, STL, rapport HTML."""

from __future__ import annotations

import re
from pathlib import Path

from build123d import export_stl
from OCP.BRepTools import BRepTools

from aerorib.bambu import BambuError, Template, write_project
from aerorib.layout import format_rotations
from aerorib.io_step import NamedBody, write_step
from aerorib.pipeline import RunResult
from aerorib.report import format_bays, format_bores, segment_heights, write_html
from aerorib.slicing import to_mesh


def safe_name(name: str) -> str:
    return re.sub(r"[^\w.-]", "_", name)


def _pieces(b) -> list[tuple[str, object]]:
    """(nom, solide) de chaque tronçon ; le corps seul s'il n'y en a qu'un."""
    segs = tuple(b.segments) or (b.result,)
    if len(segs) == 1:
        return [(b.name, segs[0])]
    return [(f"{b.name}_t{i}", s) for i, s in enumerate(segs, 1)]


def drop_mesh(solid) -> None:
    """Retire la triangulation laissée sur la forme par le maillage (STL, 3MF). Sur une pièce à très petites
    arêtes, le polygone-sur-triangulation peut ne plus coller à l'arête et BRepCheck déclare alors la forme
    invalide : la pièce rendue à l'appelant doit avoir la même validité qu'avant l'export."""
    BRepTools.Clean_s(solid.wrapped)


def export_results(
    run: RunResult, out_dir: Path, stem: str, formats: list[str], template: Template | None = None
) -> list[Path]:
    if "3mf" in formats and template is None:
        raise BambuError("export 3MF demandé sans gabarit Bambu (bambu.template)")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    single = len(run.bodies) == 1
    written: list[Path] = []
    pieces = {b.name: _pieces(b) for b in run.bodies}
    try:
        _write_all(run, out_dir, stem, formats, template, single, pieces, written)
    finally:
        for b in run.bodies:
            for _, solid in pieces[b.name]:
                drop_mesh(solid)
    return written


def _write_all(run, out_dir, stem, formats, template, single, pieces, written) -> None:
    if "step" in formats:
        written.append(
            write_step(out_dir / f"{stem}.step", [NamedBody((n,), s) for b in run.bodies for n, s in pieces[b.name]])
        )
    for b in run.bodies:
        base = stem if single else f"{stem}-{safe_name(b.name)}"
        if "stl" in formats:
            multi = len(pieces[b.name]) > 1
            for k, (_, solid) in enumerate(pieces[b.name], 1):
                path = out_dir / (f"{base}-t{k}.stl" if multi else f"{base}.stl")
                export_stl(solid, str(path), tolerance=0.02, angular_tolerance=0.3)
                written.append(path)
        details = [format_bores(b.bores, b.filled)] + ([format_bays(b.bays, segment_heights(b.segments))] if b.bays else [])
        if rot := format_rotations(b.placements):
            details.append("plateau : " + " ; ".join(rot))
        written.append(write_html(b.report, out_dir / f"{base}-rapport.html", title=b.name, details=details))
    if "3mf" in formats:
        for b in run.bodies:
            if len(b.placements) != len(pieces[b.name]):
                raise ValueError(
                    f"{b.name} : {len(b.placements)} placement(s) sur le plateau pour {len(pieces[b.name])} morceau(x)"
                )
        objs = [
            (n, to_mesh(s, 0.02), b.placements[k].angle_deg)
            for b in run.bodies
            for k, (n, s) in enumerate(pieces[b.name])
        ]
        written.append(write_project(template, objs, out_dir / f"{stem}.3mf"))
