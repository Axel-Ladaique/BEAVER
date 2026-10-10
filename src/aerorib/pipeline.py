"""Enchaînement P1 : orientation → surface médiane → outils → découpe → validation."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import NamedTuple

from build123d import Solid
from OCP.Standard import Standard_Failure
from shapely.geometry import Point, Polygon

from aerorib.classify import Model
from aerorib.config import Config
from aerorib.features.bores import (
    Bore,
    BoreError,
    Passage,
    bore_cut_tools,
    extend_to_existing_bores,
    fill_radius,
    fill_tools,
    passage_bores,
    passages_to_fill,
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
    filled: tuple[Passage, ...] = ()  # alésages rebouchés (passages: {mode: fill})


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


_FILL_WALL = 1e-3  # mm : matière minimale laissée entre le rebouchage et la surface extérieure


def fill_skin(skin: Solid, passages: list[tuple[str, Passage]], tol: float) -> Solid:
    """Rebouche les passages : fusion d'un cylindre par passage, sans toucher la surface extérieure."""
    if not passages:
        return skin
    zs, owners = [], []
    for i, (_, f) in enumerate(passages):
        for t in (0.05, 0.5, 0.95):  # trois hauteurs dans l'étendue (pas sur les faces d'extrémité)
            zs.append(f.z0 + t * (f.z1 - f.z0))
            owners.append(i)
    layers = slice_layers(skin, zs, tol)
    for i, layer in zip(owners, layers):
        name, f = passages[i]
        p = Point(f.x, f.y)
        rings = [poly.exterior for poly in layer.polygons if Polygon(poly.exterior).contains(p)]
        dist = min(r.distance(p) for r in rings) if rings else 0.0  # centre → surface extérieure
        if dist - fill_radius(f) < _FILL_WALL:
            raise PipelineError(
                f"{name} (x={f.x:.1f}, y={f.y:.1f}, Ø{2 * f.r:.1f}) : alésage à {max(dist - f.r, 0.0):.2f} mm "
                "de la surface — rebouchage impossible sans déformer la peau "
                "(corriger le modèle, ou passages: {mode: link} / {mode: off})"
            )
    filled = skin.fuse(*fill_tools([f for _, f in passages])).clean()
    solids = filled.solids()
    if not filled.is_valid or len(solids) != 1:
        raise PipelineError(f"rebouchage invalide ({len(solids)} solide(s), valide={filled.is_valid})")
    return solids[0]


class Generated(NamedTuple):
    result: Solid
    bores: tuple[Bore, ...]
    plates: int
    filled: tuple[Passage, ...]


def generate_skin(skin: Solid, spars: list[tuple[str, Solid]], cfg: Config) -> Generated:
    tol = cfg.validation.tessellation_tol
    bb = skin.bounding_box()
    nx, nz = cfg.split.grid
    mid = MidSurface.from_skin(skin, nx, nz, tol)
    inside = _spars_inside(skin, spars, tol)
    try:
        bores = extend_to_existing_bores(spar_bores(inside, cfg), skin, cfg)
        to_fill = passages_to_fill(skin, cfg, exclude=bores)
        bores += passage_bores(skin, cfg, exclude=bores)
    except BoreError as exc:
        raise PipelineError(str(exc)) from exc
    skin = fill_skin(skin, to_fill, tol)
    plates = []
    for layer in cfg.rib_layers:
        plates += diamond_plates(
            bb, mid, bores, layer, angle_deg=cfg.rib_angle(layer), gap=cfg.split.gap,
            slit=cfg.slit_width, min_depth=cfg.split.min_rib_depth,
        )
    result = skin.cut(*plates, *bore_cut_tools(bores, bb, mid))
    solids = result.solids()
    if not result.is_valid or len(solids) != 1:
        raise PipelineError(f"découpe invalide ({len(solids)} solide(s), valide={result.is_valid})")
    for name, spar in inside:
        blocked = spar_obstruction(spar, solids[0])
        if blocked > MAX_SPAR_OBSTRUCTION:
            raise PipelineError(f"{name} : le longeron traverse la matière ({blocked:.0f} mm³) — vérifier son axe")
    return Generated(solids[0], tuple(bores), len(plates), tuple(f for _, f in to_fill))


# erreurs de géométrie possibles (build123d / OpenCascade) : traduites en PipelineError
_GEOMETRY_ERRORS = (ValueError, RuntimeError, Standard_Failure)


def run_model(model: Model, cfg: Config, progress: Callable[[str], None] = lambda m: None) -> RunResult:
    skins = model.of("skin")
    if len(skins) > 1:
        names = ", ".join(s.body.name for s in skins)
        raise PipelineError(
            f"plusieurs peaux ({names}) : en P1, traiter une pièce à la fois "
            "(exporter chaque aile dans son propre STEP)"
        )
    progress("orientation…")
    try:
        o = orient(model, cfg)
    except _GEOMETRY_ERRORS as exc:
        raise PipelineError(f"orientation impossible : {exc}") from exc
    progress(f"orientation : {o.source}")
    spars = [(s.body.name, s.body.solid.moved(o.location)) for s in model.of("spar")]
    bodies = []
    for s in skins:
        skin = s.body.solid.moved(o.location)
        progress(f"{s.body.name} : surface médiane, trous et nervures…")
        try:
            result, bores, n, filled = generate_skin(skin, spars, cfg)
        except PipelineError:
            raise
        except _GEOMETRY_ERRORS as exc:
            raise PipelineError(f"{s.body.name} : génération impossible ({type(exc).__name__} : {exc})") from exc
        progress(
            f"{s.body.name} : {n} nervures, {len(bores)} trou(s) relié(s), {len(filled)} rebouché(s) — validation…"
        )
        # l'original reste la peau avant rebouchage : seuls ses contours extérieurs sont comparés
        report = validate(result, cfg, original=skin)
        bodies.append(BodyResult(s.body.name, skin, result, report, bores, n, filled))
    return RunResult(o, tuple(bodies))
