"""Enchaînement P1 : orientation → surface médiane → outils → découpe → validation."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import NamedTuple

from build123d import Solid
from OCP.Standard import Standard_Failure
from shapely.geometry import Point, Polygon

from aerorib.classify import Model
from aerorib.bambu import BambuError, read_template
from aerorib.config import Config, ConfigError, resolve_template
from aerorib.features.bores import (
    Bore,
    BoreError,
    Passage,
    bore_cut_tools,
    extend_to_existing_bores,
    fill_radius,
    fill_tools,
    find_passages,
    passage_bores,
    passages_to_fill,
    spar_bores,
)
from aerorib.features.ribs import diamond_plates, emptied_by_walls, plate_lines
from aerorib.midsurface import MidSurface
from aerorib.orient import Orientation, orient
from aerorib.segment import SegmentError, plan_cuts, split_part
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
    warnings: tuple[str, ...] = ()  # avertissements de volume d'impression / gabarit
    segments: tuple[Solid, ...] = ()  # tronçons imprimables, emplanture → saumon (un seul si pas de découpe)


@dataclass(frozen=True)
class RunResult:
    orientation: Orientation
    bodies: tuple[BodyResult, ...]

    @property
    def ok(self) -> bool:
        return all(b.report.ok for b in self.bodies)


def spars_inside(skin: Solid, spars: list[tuple[str, Solid]], tol: float) -> list[tuple[str, Solid]]:
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
# mm³ : en dessous, un solide séparé est un éclat du booléen (fins de plaques presque jointives), pas de la matière
MAX_SLIVER_VOLUME = 0.5


def main_solid(result) -> tuple[Solid, int]:
    """(pièce, nombre d'éclats retirés) ; refuse une découpe invalide ou en plusieurs vrais morceaux."""
    solids = result.solids()
    kept = [s for s in solids if s.volume > MAX_SLIVER_VOLUME]
    if not result.is_valid or len(kept) != 1:
        raise PipelineError(
            f"découpe invalide ({len(kept)} morceaux, valide={result.is_valid}) : "
            + ", ".join(f"{s.volume:.1f} mm³" for s in solids)
        )
    return kept[0], len(solids) - 1


def spar_obstruction(spar: Solid, result: Solid) -> float:
    """Volume (mm³) de matière de la pièce générée occupé par le longeron (0 si le longeron rentre)."""
    common = spar.intersect(result)
    return 0.0 if common is None else sum(s.volume for s in common.solids())


_FILL_WALL = 1e-3  # mm : matière minimale laissée entre le rebouchage et la surface extérieure


def check_fill(skin: Solid, passages: list[tuple[str, Passage]], tol: float) -> None:
    """Refuse un rebouchage qui dépasserait la surface extérieure (ou la laisserait trop mince)."""
    if not passages:
        return
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
        wall = dist - fill_radius(f)  # paroi restante autour du cylindre de rebouchage
        if wall < _FILL_WALL:
            tool = f"Ø{2 * fill_radius(f):.2f}"
            why = (
                f"le rebouchage ({tool}) dépasserait la surface de {-wall:.2f} mm" if wall <= 0
                else f"paroi de {wall:.2f} mm entre le rebouchage ({tool}) et la surface"
            )
            raise PipelineError(
                f"{name} (x={f.x:.1f}, y={f.y:.1f}, Ø{2 * f.r:.1f}) : {why} — rebouchage impossible sans déformer la peau "
                "(corriger le modèle, ou passages: {mode: link} / {mode: off})"
            )


def fill_skin(skin: Solid, passages: list[tuple[str, Passage]], tol: float) -> Solid:
    """Rebouche les passages : fusion d'un cylindre par passage, sans toucher la surface extérieure."""
    if not passages:
        return skin
    check_fill(skin, passages, tol)
    filled = skin.fuse(*fill_tools([f for _, f in passages])).clean()
    solids = filled.solids()
    if not filled.is_valid or len(solids) != 1:
        raise PipelineError(f"rebouchage invalide ({len(solids)} solide(s), valide={filled.is_valid})")
    return solids[0]


def rib_options(cfg: Config, bb, i: int) -> dict:
    """Paramètres de `diamond_plates` / `diamond_profiles` pour le niveau i : un seul jeu pour la génération et
    l'aperçu, qui doivent produire exactement les mêmes plaques."""
    layer = cfg.rib_layers[i]
    return dict(
        angle_deg=cfg.rib_angle(layer), gap=cfg.split.gap, slit=cfg.slit_width, min_depth=cfg.split.min_rib_depth,
        line_width=cfg.preset.line_width, max_mouth=cfg.validation.max_mouth_width, exclude=deeper_lines(cfg, bb, i),
    )


def wall_warnings(bb, mid, bores, cfg: Config, counts: Sequence[int]) -> list[str]:
    """Avertissements des niveaux de nervures vidés par l'arrêt avant les parois raides (`counts` : plaques par
    niveau). Calcul refait seulement pour un niveau sans plaque : rare, donc sans coût en temps normal."""
    out = []
    for i, (layer, n) in enumerate(zip(cfg.rib_layers, counts)):
        if n == 0 and emptied_by_walls(bb, mid, list(bores), layer, **rib_options(cfg, bb, i)):
            out.append(
                f"niveau de nervures « {layer.name} » : aucune plaque — toutes écartées des parois trop raides pour "
                f"la fente (slit_width {cfg.slit_width} mm, angle {cfg.rib_angle(layer)}°, "
                f"validation.max_mouth_width {cfg.validation.max_mouth_width} mm) ; réduire slit_width ou augmenter "
                "validation.max_mouth_width"
            )
    return out


def deeper_lines(cfg: Config, bb, i: int) -> list[tuple[int, float]]:
    """Plans (famille, c) des niveaux au moins aussi profonds que le niveau i peu profond, de même angle :
    une plaque peu profonde sur l'un d'eux y est contenue (booléen inutile, faces coplanaires)."""
    layer = cfg.rib_layers[i]
    if layer.depth == "full":
        return []

    def deeper(j: int) -> bool:
        other = cfg.rib_layers[j]
        if other.depth == "full":
            return True
        return other.depth > layer.depth or (other.depth == layer.depth and j < i)

    return [
        line
        for j, other in enumerate(cfg.rib_layers)
        if j != i and deeper(j) and cfg.rib_angle(other) == cfg.rib_angle(layer)
        for line in plate_lines(bb, other, cfg.rib_angle(other))
    ]


class Generated(NamedTuple):
    result: Solid
    bores: tuple[Bore, ...]
    plates: int
    filled: tuple[Passage, ...]
    slivers: int = 0  # éclats négligeables retirés après la découpe
    warnings: tuple[str, ...] = ()  # niveaux de nervures vidés par l'arrêt avant les parois raides


def generate_skin(skin: Solid, spars: list[tuple[str, Solid]], cfg: Config) -> Generated:
    tol = cfg.validation.tessellation_tol
    bb = skin.bounding_box()
    nx, nz = cfg.split.grid
    mid = MidSurface.from_skin(skin, nx, nz, tol)
    inside = spars_inside(skin, spars, tol)
    try:
        found = find_passages(skin, cfg.passages.max_tilt_deg)  # une seule recherche des alésages
        bores = extend_to_existing_bores(spar_bores(inside, cfg), skin, cfg, found)
        to_fill = passages_to_fill(skin, cfg, exclude=bores, found=found)
        bores += passage_bores(skin, cfg, exclude=bores, found=found)
    except BoreError as exc:
        raise PipelineError(str(exc)) from exc
    skin = fill_skin(skin, to_fill, tol)
    plates, counts = [], []
    for i, layer in enumerate(cfg.rib_layers):
        layer_plates = diamond_plates(bb, mid, bores, layer, **rib_options(cfg, bb, i))
        plates += layer_plates
        counts.append(len(layer_plates))
    warns = wall_warnings(bb, mid, bores, cfg, counts)
    result = skin.cut(*plates, *bore_cut_tools(bores, bb, mid))
    part, slivers = main_solid(result)
    for name, spar in inside:
        blocked = spar_obstruction(spar, part)
        if blocked > MAX_SPAR_OBSTRUCTION:
            raise PipelineError(f"{name} : le longeron traverse la matière ({blocked:.0f} mm³) — vérifier son axe")
    return Generated(part, tuple(bores), len(plates), tuple(f for _, f in to_fill), slivers, tuple(warns))




# erreurs de géométrie possibles (build123d / OpenCascade) : traduites en PipelineError
GEOMETRY_ERRORS = (ValueError, RuntimeError, Standard_Failure)


def _print_volume(cfg: Config) -> tuple[tuple[float, float, float], str | None]:
    """((largeur, profondeur, hauteur), avertissement) : gabarit Bambu s'il existe, sinon imprimante."""
    try:
        t = read_template(resolve_template(cfg))
        return (t.bed[0], t.bed[1], t.height), None
    except BambuError as exc:
        warn = f"gabarit Bambu illisible ({exc}) — volume de l'imprimante {cfg.printer} utilisé"
    except ConfigError as exc:
        warn = (
            f"{exc} — volume de l'imprimante {cfg.printer} utilisé" if cfg.bambu.template is not None else None
        )
    return tuple(cfg.printer_spec.volume), warn


def usable_height(cfg: Config) -> float:
    """Hauteur max d'un tronçon (mm) : segment.max_height, sinon hauteur d'impression − marge."""
    seg = cfg.segment
    if seg.max_height is not None:
        return seg.max_height
    (_, _, H), _ = _print_volume(cfg)
    return H - seg.margin


def _segment(
    body_name: str, result: Solid, cfg: Config, progress: Callable[[str], None]
) -> tuple[tuple[Solid, ...], tuple[str, ...]]:
    seg = cfg.segment
    (W, D, H), fallback = _print_volume(cfg)
    warnings = [f"{body_name} : {fallback}"] if fallback else []
    usable = H - seg.margin
    if seg.max_height is not None and seg.max_height > usable + 1e-9:
        warnings.append(
            f"{body_name} : segment.max_height ({seg.max_height:g} mm) dépasse la hauteur utile "
            f"({usable:g} mm = {H:g} − marge {seg.margin:g})"
        )
    max_h = seg.max_height if seg.max_height is not None else usable
    bb = result.bounding_box(optimal=True)
    if seg.mode == "off":
        w, d, h = bb.max.X - bb.min.X, bb.max.Y - bb.min.Y, bb.max.Z - bb.min.Z
        if h > max_h + 1e-9 or w > W - 2 * seg.margin or d > D - 2 * seg.margin:
            warnings.append(
                f"{body_name} : la pièce ({h:.0f} mm / {w:.0f}×{d:.0f} mm) dépasse le volume d'impression "
                "— découpe désactivée (segment.mode: off)"
            )
        for w_ in warnings:
            progress(f"⚠ {w_}")
        return (result,), tuple(warnings)
    for w_ in warnings:
        progress(f"⚠ {w_}")
    try:
        cuts = plan_cuts(bb.max.Z - bb.min.Z, max_h, seg.cuts_at or None)
        pieces = split_part(result, [bb.min.Z + c for c in cuts]) if cuts else [result]
    except SegmentError as exc:
        raise PipelineError(f"{body_name} : {exc}") from exc
    if cuts:
        progress(f"{body_name} : découpe en {len(pieces)} tronçons")
    for i, piece in enumerate(pieces, 1):
        pb = piece.bounding_box(optimal=True)
        w, d = pb.max.X - pb.min.X, pb.max.Y - pb.min.Y
        if w > W - 2 * seg.margin or d > D - 2 * seg.margin:
            raise PipelineError(
                f"{body_name} : le tronçon {i} fait {w:.0f}×{d:.0f} mm, plus grand que le plateau "
                f"({W:.0f}×{D:.0f} mm, marge {seg.margin:g} mm)"
            )
    return tuple(pieces), tuple(warnings)


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
    except GEOMETRY_ERRORS as exc:
        raise PipelineError(f"orientation impossible : {exc}") from exc
    progress(f"orientation : {o.source}")
    spars = [(s.body.name, s.body.solid.moved(o.location)) for s in model.of("spar")]
    bodies = []
    for s in skins:
        skin = s.body.solid.moved(o.location)
        progress(f"{s.body.name} : surface médiane, trous et nervures…")
        try:
            gen = generate_skin(skin, spars, cfg)
            result, bores, n, filled = gen.result, gen.bores, gen.plates, gen.filled
        except PipelineError:
            raise
        except GEOMETRY_ERRORS as exc:
            raise PipelineError(f"{s.body.name} : génération impossible ({type(exc).__name__} : {exc})") from exc
        spars_n = sum(b.cut_hole for b in bores)
        progress(
            f"{s.body.name} : {n} nervures, {spars_n} longeron(s), {len(bores) - spars_n} passage(s) relié(s), "
            f"{len(filled)} rebouché(s)"
            + (f", {gen.slivers} éclat(s) négligeable(s) retiré(s)" if gen.slivers else "")
            + " — validation…"
        )
        # l'original reste la peau avant rebouchage : seuls ses contours extérieurs sont comparés
        report = validate(result, cfg, original=skin)
        segments, warns = _segment(s.body.name, result, cfg, progress)
        if gen.slivers:
            warns = (*warns, f"{gen.slivers} éclat(s) négligeable(s) (< {MAX_SLIVER_VOLUME} mm³) retiré(s) à la découpe")
        warns = (*gen.warnings, *warns)
        bodies.append(BodyResult(s.body.name, skin, result, report, bores, n, filled, warns, segments))
    return RunResult(o, tuple(bodies))
