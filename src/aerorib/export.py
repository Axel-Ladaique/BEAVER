"""Écriture des résultats : STEP, STL, rapport HTML."""

from __future__ import annotations

import re
from pathlib import Path

from build123d import export_stl

from aerorib.bambu import BambuError, Template, write_project
from aerorib.io_step import NamedBody, write_step
from aerorib.pipeline import RunResult
from aerorib.report import write_html
from aerorib.slicing import to_mesh


def safe_name(name: str) -> str:
    return re.sub(r"[^\w.-]", "_", name)


def _pieces(b) -> list[tuple[str, object]]:
    """(nom, solide) de chaque tronçon ; le corps seul s'il n'y en a qu'un."""
    segs = tuple(b.segments) or (b.result,)
    if len(segs) == 1:
        return [(b.name, segs[0])]
    return [(f"{b.name}_t{i}", s) for i, s in enumerate(segs, 1)]


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
        written.append(write_html(b.report, out_dir / f"{base}-rapport.html", title=b.name))
    if "3mf" in formats:
        objs = [(n, to_mesh(s, 0.02)) for b in run.bodies for n, s in pieces[b.name]]
        written.append(write_project(template, objs, out_dir / f"{stem}.3mf"))
    return written
