"""Enchaînement P1 : orientation → surface médiane → outils → découpe → validation."""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import NamedTuple

from build123d import Cylinder, Location, Solid
from OCP.Standard import Standard_Failure
from OCP.BRepTools import BRepTools
from shapely.geometry import MultiPoint, Point, Polygon

from aerorib.classify import Model
from aerorib.bambu import BambuError, read_template
from aerorib.config import AUTO_TEMPLATE, Config, ConfigError, resolve_template
from aerorib.features.bores import (
    Bore,
    BoreError,
    Passage,
    bore_cut_tools,
    bore_extent,
    extend_to_existing_bores,
    fill_radius,
    fill_tools,
    find_passages,
    passage_bores,
    passages_to_fill,
    spar_bores,
)
from aerorib.features.chord_cut import bay_obstacles, bore_obstacles, cut_lines, cut_segment, plan_layout
from aerorib.features.ribs import diamond_plates, emptied_by_walls, plate_lines
from aerorib.features.servo import ServoBay, ServoError, hole_meets_pocket, pocket_solid, reinforce_solids, reinforce_warnings, servo_bays
from aerorib.layout import Placement, fit_on_bed, format_rotation
from aerorib.midsurface import MidSurface
from aerorib.orient import Orientation, orient
from aerorib.segment import SegmentError, split_part
from aerorib.slicing import section_sampler, slice_layers, to_mesh
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
    bays: tuple[ServoBay, ...] = ()  # baies de servo
    placements: tuple[Placement, ...] = ()  # rotation sur le plateau de chaque tronçon (même ordre que segments)
    notices: tuple[str, ...] = ()  # décisions automatiques de découpe (informations, pas des avertissements)
    names: tuple[str, ...] = ()  # nom de chaque morceau (T1, T2… ; A, B / T1-A… avec une coupe dans la corde)
    spans: tuple[tuple[float, float], ...] = ()  # étendue en Z de chaque morceau (mm depuis l'emplanture)
    piece_reports: tuple[Report, ...] = ()  # validation de chaque morceau (coupe dans la corde seulement)

    @property
    def z_heights(self) -> list[float]:
        """Hauteurs des tronçons en Z (emplanture → saumon), un par tronçon même coupé dans la corde."""
        if not self.spans:
            return [s.bounding_box().max.Z - s.bounding_box().min.Z for s in self.segments]
        return [b - a for a, b in dict.fromkeys(self.spans)]


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


def rib_options(cfg: Config, bb, i: int, bays: Sequence[ServoBay] = ()) -> dict:
    """Paramètres de `diamond_plates` / `diamond_profiles` pour le niveau i : un seul jeu pour la génération et
    l'aperçu, qui doivent produire exactement les mêmes plaques (`bays` : baies de servo à contourner)."""
    layer = cfg.rib_layers[i]
    return dict(
        angle_deg=cfg.rib_angle(layer), gap=cfg.split.gap, slit=cfg.slit_width, min_depth=cfg.split.min_rib_depth,
        line_width=cfg.preset.line_width, max_mouth=cfg.validation.max_mouth_width, exclude=deeper_lines(cfg, bb, i),
        bays=tuple(bays), pocket_strip=cfg.servo_strip(),
    )


def wall_warnings(bb, mid, bores, cfg: Config, counts: Sequence[int], bays: Sequence[ServoBay] = ()) -> list[str]:
    """Avertissements des niveaux de nervures vidés par l'arrêt avant les parois raides (`counts` : plaques par
    niveau). Calcul refait seulement pour un niveau sans plaque : rare, donc sans coût en temps normal."""
    out = []
    for i, (layer, n) in enumerate(zip(cfg.rib_layers, counts)):
        if n == 0 and emptied_by_walls(bb, mid, list(bores), layer, **rib_options(cfg, bb, i, bays)):
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


def hole_openings(bores: Sequence[Bore], bays: Sequence[ServoBay], bb) -> list[Solid]:
    """Parties des trous qui débouchent dans une poche : en ces couches, le bord du trou fait partie du contour
    extérieur voulu. Limitées en envergure à la rencontre trou–poche (± `HOLE_TOL`) : ailleurs, une percée
    du trou dans la peau reste signalée (« peau entamée »)."""
    out = []
    for b in bores:
        if not b.cut_hole:
            continue
        lo, hi = bore_extent(b, bb.min.Z, bb.max.Z)
        for bay in bays:
            meet = hole_meets_pocket(bay, b.x, b.y, b.radius, lo, hi)
            if meet is not None and meet[1] > meet[0]:
                za, zb = meet
                out.append(Cylinder(b.radius, zb - za).moved(Location((b.x, b.y, (za + zb) / 2))))
    return out


class Generated(NamedTuple):
    result: Solid
    bores: tuple[Bore, ...]
    plates: int
    filled: tuple[Passage, ...]
    slivers: int = 0  # éclats négligeables retirés après la découpe
    warnings: tuple[str, ...] = ()  # niveaux de nervures vidés par l'arrêt avant les parois raides
    bays: tuple[ServoBay, ...] = ()  # baies de servo
    # ouvertures voulues de la peau (poches des baies, trous qui débouchent dans une poche) : retirées de
    # l'original pour la validation
    openings: tuple[Solid, ...] = ()


def generate_skin(
    skin: Solid, spars: list[tuple[str, Solid]], cfg: Config, cables: frozenset[str] = frozenset(),
    servos: Sequence[tuple[str, Solid]] = (),
) -> Generated:
    tol = cfg.validation.tessellation_tol
    bb = skin.bounding_box()
    nx, nz = cfg.split.grid
    mid = MidSurface.from_skin(skin, nx, nz, tol)
    inside = spars_inside(skin, spars, tol)
    try:
        found = find_passages(skin, cfg.passages.max_tilt_deg)  # une seule recherche des alésages
        bores = extend_to_existing_bores(spar_bores(inside, cfg, cables), skin, cfg, found)
        to_fill = passages_to_fill(skin, cfg, exclude=bores, found=found)
        bores += passage_bores(skin, cfg, exclude=bores, found=found)
        bays = servo_bays(servos, skin, mid, cfg, bores)
    except (BoreError, ServoError) as exc:
        raise PipelineError(str(exc)) from exc
    skin = fill_skin(skin, to_fill, tol)
    plates, counts = [], []
    for i, layer in enumerate(cfg.rib_layers):
        layer_plates = diamond_plates(bb, mid, bores, layer, **rib_options(cfg, bb, i, bays))
        plates += layer_plates
        counts.append(len(layer_plates))
    warns = wall_warnings(bb, mid, bores, cfg, counts, bays) + reinforce_warnings(bays)
    # gaines et longerons qui entrent dans une baie : simplement découpés par la poche
    # poches et fentes de renfort : ouvertures voulues de la peau
    pockets = [t for b in bays for t in (pocket_solid(b), *reinforce_solids(b))]
    result = skin.cut(*plates, *bore_cut_tools(bores, bb, mid), *pockets)
    part, slivers = main_solid(result)
    for name, spar in inside:
        blocked = spar_obstruction(spar, part)
        if blocked > MAX_SPAR_OBSTRUCTION:
            raise PipelineError(f"{name} : le longeron traverse la matière ({blocked:.0f} mm³) — vérifier son axe")
    openings = (*pockets, *hole_openings(bores, bays, bb))
    return Generated(
        part, tuple(bores), len(plates), tuple(f for _, f in to_fill), slivers, tuple(warns), tuple(bays), openings,
    )




# erreurs de géométrie possibles (build123d / OpenCascade) : traduites en PipelineError
GEOMETRY_ERRORS = (ValueError, RuntimeError, Standard_Failure)


def _print_volume(cfg: Config) -> tuple[tuple[float, float, float], str | None]:
    """((largeur, profondeur, hauteur), avertissement) : gabarit Bambu s'il existe, sinon imprimante."""
    try:
        path = resolve_template(cfg)
        t = read_template(path)
        model = str(t.settings.get("printer_model") or "")
        warn = None
        if model and not model.lower().endswith(cfg.printer):
            warn = (f"gabarit {path.name} ({model}) ≠ imprimante choisie {cfg.printer}\u00a0: "
                    "le volume et le projet suivent le gabarit")
        return (t.bed[0], t.bed[1], t.height), warn
    except BambuError as exc:
        warn = f"gabarit Bambu illisible ({exc}) — volume de l'imprimante {cfg.printer} utilisé"
    except ConfigError as exc:
        warn = (
            f"{exc} — volume de l'imprimante {cfg.printer} utilisé" if cfg.bambu.template not in (None, AUTO_TEMPLATE) else None
        )
    return tuple(cfg.printer_spec.volume), warn


def usable_height(cfg: Config) -> float:
    """Hauteur max d'un tronçon (mm) : segment.max_height, sinon hauteur d'impression − marge."""
    seg = cfg.segment
    if seg.max_height is not None:
        return seg.max_height
    (_, _, H), _ = _print_volume(cfg)
    return H - seg.margin


class Segmented(NamedTuple):
    pieces: tuple[Solid, ...]  # morceaux imprimables, posés à z = 0
    warnings: tuple[str, ...]
    notices: tuple[str, ...] = ()  # décisions automatiques de découpe (informations)
    names: tuple[str, ...] = ()  # T1, T2… ; A, B / T1-A, T1-B… avec une coupe dans la corde
    spans: tuple[tuple[float, float], ...] = ()  # étendue en Z de chaque morceau (mm depuis l'emplanture)
    # coupe dans la corde : peau d'origine coupée de la même façon et zone de peau tolérée (faces de coupe), par
    # morceau, pour la validation morceau par morceau
    checks: tuple[tuple[Solid, Callable], ...] = ()
    chord: bool = False  # au moins une coupe dans la corde


CUT_FACE_TOL = 0.05  # mm : demi-largeur de la bande autour d'une face de coupe (la face d'origine y est)


def slit_gap(cfg: Config) -> float:
    """Largeur en couche d'une fente de nervure (fente / cos(angle)) + tolérance de maillage : écart admis entre la
    face de coupe et le bord du morceau, là où une fente longe la face et y débouche."""
    w = max(cfg.slit_width / math.cos(math.radians(cfg.rib_angle(layer))) for layer in cfg.rib_layers)
    return w + cfg.validation.tessellation_tol


def _face_exempt(band: Callable, gap: float) -> Callable:
    return lambda z: (band(z), gap)


def _segment(
    body_name: str, result: Solid, cfg: Config, progress: Callable[[str], None], skin: Solid | None = None
) -> tuple[tuple[Solid, ...], tuple[str, ...]]:
    """(morceaux, avertissements) de `segment_part`."""
    seg = segment_part(body_name, result, cfg, progress, skin)
    return seg.pieces, seg.warnings


def segment_part(
    body_name: str, result: Solid, cfg: Config, progress: Callable[[str], None], skin: Solid | None = None,
    original: Solid | None = None, bores: Sequence[Bore] = (), bays: Sequence[ServoBay] = (),
) -> Segmented:
    """Découpe en morceaux : tronçons en Z selon la hauteur et l'empreinte sur le plateau, puis coupe dans la corde
    des tronçons qui ne tiennent pas (`plan_layout`). L'empreinte est lue sur les sections de `skin` (peau d'origine,
    même contour extérieur que `result` ; `result` par défaut). `original` (peau de référence de la validation) est
    coupée comme `result` quand il y a une coupe dans la corde ; `bores`, `bays` : obstacles des plans de coupe."""
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
        return Segmented((result,), tuple(warnings), (), ("T1",), ((0.0, bb.max.Z - bb.min.Z),))
    for w_ in warnings:
        progress(f"⚠ {w_}")
    z0 = bb.min.Z
    height = bb.max.Z - z0
    sample = section_sampler(skin if skin is not None else result, cfg.validation.tessellation_tol)
    obstacles = [*bore_obstacles(bores, z0, bb.max.Z), *bay_obstacles(bays, z0, cfg.servo_strip())]
    printer = cfg.printer.upper()
    try:
        plan = plan_layout(
            height, max_h, lambda zs: sample([z0 + z for z in zs]), (W, D), seg.margin, seg.cuts_at or None,
            chord=seg.chord, max_overhang_deg=cfg.preset.max_overhang_deg, obstacles=obstacles,
        )
        cuts = plan.cuts
        names = plan.names()
        spans = tuple((s.z0, s.z1) for s in plan.segments for _ in range(s.k))
        checks: list[tuple[Solid, Callable]] = []
        gap = slit_gap(cfg)
        if not plan.chord_cut:
            pieces = split_part(result, [z0 + c for c in cuts]) if cuts else [result]
        else:
            pieces = []
            last = len(plan.segments) - 1
            for i, sp in enumerate(plan.segments):
                label = f"{body_name} : T{i + 1}"
                order = sp.order()
                cut = cut_segment(result, sp, z0, i == 0, i == last, label)
                pieces += [cut[j] for j in order]
                if original is not None:
                    ref = cut_segment(original, sp, z0, i == 0, i == last, f"{label} (référence)")
                    checks += [(ref[j], _face_exempt(cut_lines(sp, j, CUT_FACE_TOL), gap)) for j in order]
    except SegmentError as exc:
        raise PipelineError(f"{body_name} : {exc}") from exc
    notices = []
    n_z = len(plan.z.cuts) + 1
    if plan.z.reason:
        notices.append(f"découpe automatique : {n_z} tronçons en Z ({plan.z.reason})")
    if plan.extra_z:
        notices.append(
            f"découpe automatique : {len(plan.segments)} tronçons en Z pour limiter l'inclinaison de la coupe dans "
            f"la corde (surplomb max {cfg.preset.max_overhang_deg:g}°)"
        )
    need = plan.z.chord
    for i, sp in enumerate(plan.segments, 1):
        if not sp.planes:
            continue
        where = f"T{i} : " if len(plan.segments) > 1 else ""
        if need is not None and need.z0 <= sp.z1 and need.z1 >= sp.z0 and sp.size is not None:
            why = f"section {sp.size[0]:.0f} × {sp.size[1]:.0f} mm > plateau {printer} même en diagonale"
        else:
            why = f"tronçon trop grand pour le plateau {printer} même tourné"
        line = f"découpe automatique : {where}coupe dans la corde en {sp.k} morceaux ({why})"
        for p in sp.planes:
            if p.shift:
                toward = "le bord de fuite" if (p.shift > 0) == sp.le_at_min else "le bord d'attaque"
                line += (f" ; plan décalé de {abs(p.shift):.1f} mm vers {toward} pour éviter "
                         + ", ".join(p.avoided or ("un obstacle",)))
        notices.append(line)
    n_seg = len(plan.segments)
    if n_seg > 1:
        progress(f"{body_name} : découpe en {n_seg} tronçons")
    for line in notices:
        progress(f"{body_name} : {line}")
    return Segmented(
        tuple(pieces), tuple(warnings), tuple(notices), tuple(names), spans, tuple(checks), plan.chord_cut
    )


def merge_reports(reports: Sequence[Report], names: Sequence[str], offsets: Sequence[float], height: float) -> Report:
    """Rapport d'ensemble des morceaux validés séparément : défauts préfixés du nom du morceau, z ramenés au repère
    de la pièce entière (`offsets` : bas de chaque morceau) ; trajet et masse additionnés ; couches : celles de la
    pièce (une fois par tronçon en Z), le détail par morceau restant dans chaque rapport."""
    issues, failed = [], {}
    for r, name, dz in zip(reports, names, offsets):
        issues += [replace(i, z=i.z + dz, message=f"{name} : {i.message}") for i in r.issues]
        for z, polys in r.failed_layers.items():
            failed[z + dz] = (*failed.get(z + dz, ()), *polys)
    stats = {
        # couches de la pièce : celles d'un morceau par tronçon en Z (les morceaux d'un tronçon partagent ses couches)
        "layers": float(sum({dz: r.stats["layers"] for r, dz in zip(reports, offsets)}.values())),
        "height_mm": float(height),
        "path_length_m": sum(r.stats["path_length_m"] for r in reports),
        "mass_g": sum(r.stats["mass_g"] for r in reports),
    }
    return Report(tuple(issues), stats, failed)


def _footprint(piece: Solid) -> Polygon:
    """Emprise XY d'un tronçon : enveloppe convexe des sommets de son maillage (tolérance fine, triangulation retirée)."""
    mesh = to_mesh(piece, 0.05, 0.5)
    BRepTools.Clean_s(piece.wrapped)
    return MultiPoint([tuple(v) for v in mesh.vertices[:, :2]]).convex_hull


def place_pieces(
    body_name: str, pieces: tuple[Solid, ...], cfg: Config, progress: Callable[[str], None],
    names: Sequence[str] = (),
) -> tuple[tuple[Placement, ...], tuple[str, ...]]:
    """Rotation de chaque tronçon sur le plateau + avertissements (tronçon trop grand même tourné)."""
    (W, D, _), _ = _print_volume(cfg)
    margin = cfg.segment.margin
    placements, warnings = [], []
    for i, piece in enumerate(pieces, 1):
        pb = piece.bounding_box(optimal=True)
        w, d = pb.max.X - pb.min.X, pb.max.Y - pb.min.Y
        if w <= W - 2 * margin and d <= D - 2 * margin:  # tient droit : pas besoin du maillage
            placements.append(Placement(0.0, True, min(W - 2 * margin - w, D - 2 * margin - d), (w, d)))
            continue
        pl = fit_on_bed(_footprint(piece), (W, D), margin)
        if pl.fits:
            progress(f"{body_name} : {names[i - 1] if names else f'T{i}'} : {format_rotation(pl)}")
        elif cfg.segment.mode != "off":  # en mode off, l'avertissement de dépassement existe déjà
            warnings.append(
                f"{body_name} : {f'le morceau {names[i - 1]}' if names else f'le tronçon {i}'} ({pl.size[0]:.0f}×{pl.size[1]:.0f} mm au mieux, même tourné) "
                f"ne tient pas sur le plateau ({W:.0f}×{D:.0f} mm, marge {margin:g} mm) — "
                "à découper ou à réduire"
            )
        if not pl.fits:  # posé sans rotation : marge et emprise sont celles de 0°
            pl = Placement(0.0, False, min(W - 2 * margin - w, D - 2 * margin - d), (w, d))
        placements.append(pl)
    for w_ in warnings:
        progress(f"⚠ {w_}")
    return tuple(placements), tuple(warnings)


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
    cables = frozenset(c.body.name for c in model.cables)
    servos = [(s.body.name, s.body.solid.moved(o.location)) for s in model.of("servo")]
    bodies = []
    for s in skins:
        skin = s.body.solid.moved(o.location)
        progress(f"{s.body.name} : surface médiane, trous et nervures…")
        try:
            gen = generate_skin(skin, spars, cfg, cables, servos)
            result, bores, n, filled = gen.result, gen.bores, gen.plates, gen.filled
        except PipelineError:
            raise
        except GEOMETRY_ERRORS as exc:
            raise PipelineError(f"{s.body.name} : génération impossible ({type(exc).__name__} : {exc})") from exc
        cables_n = sum(b.cable for b in bores)
        spars_n = sum(b.cut_hole for b in bores) - cables_n
        progress(
            f"{s.body.name} : {n} nervures, {spars_n} longeron(s), "
            + (f"{cables_n} gaine(s), " if cables_n else "")
            + f"{len(bores) - spars_n - cables_n} passage(s) relié(s), "
            f"{len(filled)} rebouché(s)"
            + (f", {len(gen.bays)} baie(s) de servo" if gen.bays else "")
            + (f", {gen.slivers} éclat(s) négligeable(s) retiré(s)" if gen.slivers else "")
            + " — validation…"
        )
        # l'original reste la peau avant rebouchage : seuls ses contours extérieurs sont comparés ; les poches des
        # baies en sont retirées (ouverture voulue, pas une « peau entamée »)
        try:
            original = skin.cut(*gen.openings) if gen.openings else skin
        except GEOMETRY_ERRORS as exc:
            raise PipelineError(f"{s.body.name} : poche de servo impossible ({type(exc).__name__} : {exc})") from exc
        seg = segment_part(
            s.body.name, result, cfg, progress, skin=skin, original=original, bores=bores, bays=gen.bays
        )
        piece_reports: tuple[Report, ...] = ()
        if seg.checks:  # coupe dans la corde : chaque morceau validé avec la peau d'origine coupée de même
            piece_reports = tuple(
                validate(piece, cfg, original=ref, skin_exempt=exempt)
                for piece, (ref, exempt) in zip(seg.pieces, seg.checks)
            )
            bb = result.bounding_box(optimal=True)
            report = merge_reports(
                piece_reports, seg.names, [bb.min.Z + a for a, _ in seg.spans], bb.max.Z - bb.min.Z
            )
        else:
            report = validate(result, cfg, original=original)
        warns = seg.warnings
        if gen.slivers:
            warns = (*warns, f"{gen.slivers} éclat(s) négligeable(s) (< {MAX_SLIVER_VOLUME} mm³) retiré(s) à la découpe")
        names = seg.names if seg.chord else ()
        placements, place_warns = place_pieces(s.body.name, seg.pieces, cfg, progress, names)
        warns = (*gen.warnings, *warns, *place_warns)
        bodies.append(
            BodyResult(
                s.body.name, skin, result, report, bores, n, filled, warns, seg.pieces, gen.bays, placements,
                seg.notices, seg.names, seg.spans, piece_reports,
            )
        )
    return RunResult(o, tuple(bodies))
