"""Écriture des résultats : STEP, STL, rapport HTML."""

from __future__ import annotations

import re
from pathlib import Path

from build123d import export_stl

from aerorib.io_step import NamedBody, write_step
from aerorib.pipeline import RunResult
from aerorib.report import write_html


def safe_name(name: str) -> str:
    return re.sub(r"[^\w.-]", "_", name)


def export_results(run: RunResult, out_dir: Path, stem: str, formats: list[str]) -> list[Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    single = len(run.bodies) == 1
    written: list[Path] = []
    if "step" in formats:
        written.append(write_step(out_dir / f"{stem}.step", [NamedBody((b.name,), b.result) for b in run.bodies]))
    for b in run.bodies:
        base = stem if single else f"{stem}-{safe_name(b.name)}"
        if "stl" in formats:
            path = out_dir / f"{base}.stl"
            export_stl(b.result, str(path), tolerance=0.02, angular_tolerance=0.3)
            written.append(path)
        written.append(write_html(b.report, out_dir / f"{base}-rapport.html", title=b.name))
    return written
