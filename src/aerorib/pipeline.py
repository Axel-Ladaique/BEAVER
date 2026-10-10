"""Enchaînement P1 : orientation → surface médiane → outils → découpe → validation."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from build123d import Solid
from shapely.geometry import Point, Polygon

from aerorib.classify import Model
from aerorib.config import Config
from aerorib.features.bores import (
    Bore,
    BoreError,
    bore_cut_tools,
    extend_to_existing_bores,
    passage_bores,
    spar_bores,
)
from aerorib.features.ribs import diamond_plates
from aerorib.midsurface import MidSurface
from aerorib.orient import Orientation, orient
from aerorib.slicing import slice_layers
from aerorib.validate import Report, validate


class PipelineError(ValueError):
    """Échec de génération (message en français)."""


@dataclass(frozen=True)
class BodyResult:
    name: str
    original: Solid
    result: Solid
    report: Report
    bores: tuple[Bore, ...]
    plates: int


@dataclass(frozen=True)
class RunResult:
    orientation: Orientation
    bodies: tuple[BodyResult, ...]

    @property
    def ok(self) -> bool:
        return all(b.report.ok for b in self.bodies)


def _spars_inside(skin: Solid, spars: list[tuple[str, Solid]], tol: float) -> list[tuple[str, Solid]]:
    bb = skin.bounding_box()
    (mid,) = slice_layers(skin, [(bb.min.Z + bb.max.Z) / 2], tol)
    envelope = [Polygon(p.exterior) for p in mid.polygons]
    kept = []
    for name, solid in spars:
        c = solid.bounding_box().center()
        if any(e.contains(Point(c.X, c.Y)) for e in envelope):
            kept.append((name, solid))
    return kept


MAX_SPAR_OBSTRUCTION = 1.0  # mm³ : au-delà, le longeron ne rentre pas dans la pièce générée


def spar_obstruction(spar: Solid, result: Solid) -> float:
    """Volume (mm³) de matière de la pièce générée occupé par le longeron (0 si le longeron rentre)."""
    common = spar.intersect(result)
    return 0.0 if common is None else sum(s.volume for s in common.solids())


def generate_skin(skin: Solid, spars: list[tuple[str, Solid]], cfg: Config) -> tuple[Solid, tuple[Bore, ...], int]:
    tol = cfg.validation.tessellation_tol
    bb = skin.bounding_box()
    nx, nz = cfg.split.grid
    mid = MidSurface.from_skin(skin, nx, nz, tol)
    inside = _spars_inside(skin, spars, tol)
    try:
        bores = extend_to_existing_bores(spar_bores(inside, cfg), skin, cfg)
        bores += passage_bores(skin, cfg, exclude=bores)
    except BoreError as exc:
        raise PipelineError(str(exc)) from exc
    plates = []
    for layer in cfg.rib_layers:
        plates += diamond_plates(
            bb, mid, bores, layer, angle_deg=cfg.rib_angle(layer), gap=cfg.split.gap,
            slit=cfg.slit_width, min_depth=cfg.split.min_rib_depth,
        )
    result = skin.cut(*plates, *bore_cut_tools(bores, bb))
    solids = result.solids()
    if not result.is_valid or len(solids) != 1:
        raise PipelineError(f"découpe invalide ({len(solids)} solide(s), valide={result.is_valid})")
    for name, spar in inside:
        blocked = spar_obstruction(spar, solids[0])
        if blocked > MAX_SPAR_OBSTRUCTION:
            raise PipelineError(f"{name} : le longeron traverse la matière ({blocked:.0f} mm³) — vérifier son axe")
    return solids[0], tuple(bores), len(plates)


def run_model(model: Model, cfg: Config, progress: Callable[[str], None] = lambda m: None) -> RunResult:
    progress("orientation…")
    o = orient(model, cfg)
    progress(f"orientation : {o.source}")
    spars = [(s.body.name, s.body.solid.moved(o.location)) for s in model.of("spar")]
    bodies = []
    for s in model.of("skin"):
        skin = s.body.solid.moved(o.location)
        progress(f"{s.body.name} : surface médiane, trous et nervures…")
        result, bores, n = generate_skin(skin, spars, cfg)
        progress(f"{s.body.name} : {n} nervures, {len(bores)} trou(s) relié(s) — validation…")
        report = validate(result, cfg, original=skin)
        bodies.append(BodyResult(s.body.name, skin, result, report, bores, n))
    return RunResult(o, tuple(bodies))
