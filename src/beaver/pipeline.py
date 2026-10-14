"""Enchaînement P1 : orientation → surface médiane → outils → découpe → validation."""

from __future__ import annotations

import functools
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import NamedTuple

from build123d import Cylinder, Location, Solid
from OCP.Standard import Standard_Failure
from OCP.BRepTools import BRepTools
from shapely.errors import ShapelyError
from shapely.geometry import MultiPoint, Point, Polygon

from beaver.classify import Model
from beaver.bambu import BambuError, read_template
from beaver.config import AUTO_TEMPLATE, Config, ConfigError, resolve_template
from beaver.features.bores import (
    Bore,
    BoreError,
    Passage,
    bore_cut_tools,
    bore_extent,
    bore_layer,
    extend_to_existing_bores,
    fill_radius,
    fill_tools,
    find_passages,
    passage_bores,
    passages_to_fill,
    spar_bores,
)
from beaver.features.chord_cut import LayoutPlan, bay_obstacles, bore_obstacles, cut_lines, cut_segment, plan_layout
from beaver.features.ribs import CutGuides, HollowSpec, diamond_plates, emptied_by_walls, plate_lines
from beaver.features.tubes import Region, Tube, band, beyond, is_transversal, spar_tubes, tube_warnings
from beaver.features.servo import (
    ServoBay, ServoError, hole_meets_pocket, pocket_profile, pocket_solid, reinforce_solids, reinforce_warnings, servo_bays,
)
from beaver.layout import Placement, fit_on_bed, format_rotation
from beaver.midsurface import MidSurface
from beaver.orient import Orientation, orient
from beaver.segment import CutFailure, SegmentError, cut_chain, split_part
from beaver.slicing import mesh_sections, section_sampler, slice_layers, to_mesh
from beaver.validate import MAX_FAILED_LAYERS, Issue, Report, validate


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
    tubes: tuple[Tube, ...] = ()  # SBAR transversales / inclinées

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


_Z_OVERLAP = 1e-3  # mm : un longeron qui ne recouvre pas l'étendue en Z de la peau d'au moins cela n'y est pas


def spars_inside(skin: Solid, spars: list[tuple[str, Solid]], tol: float) -> list[tuple[str, Solid]]:
    """Longerons (et gaines) logés dans la peau : axe dans le contour de la section à mi-hauteur, et étendue en Z
    qui recouvre celle de la peau (plusieurs corps d'aile : un longeron va à chaque corps qu'il traverse)."""
    bb = skin.bounding_box()
    (mid,) = slice_layers(skin, [(bb.min.Z + bb.max.Z) / 2], tol)
    envelope = [Polygon(p.exterior) for p in mid.polygons]
    kept = []
    for name, solid in spars:
        sb = solid.bounding_box()
        if min(sb.max.Z, bb.max.Z) - max(sb.min.Z, bb.min.Z) <= _Z_OVERLAP:
            continue
        c = sb.center()
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


def rib_options(
    cfg: Config, bb, i: int, bays: Sequence[ServoBay] = (), anchor=None, tubes: Sequence[Tube] = (),
    cuts: CutGuides | None = None,
) -> dict:
    """Paramètres de `diamond_plates` / `diamond_profiles` pour le niveau i : un seul jeu pour la génération et
    l'aperçu, qui doivent produire exactement les mêmes plaques (`bays` : baies de servo à contourner ; `anchor` :
    boîte de calage du motif, la boîte globale des peaux quand le modèle en a plusieurs, voir `plate_lines` ; `cuts` :
    coupes du plan de découpe, `cut_guides`, autour desquelles les évidements s'arrêtent)."""
    layer = cfg.rib_layers[i]
    return dict(
        angle_deg=cfg.rib_angle(layer), gap=cfg.split.gap, slit=cfg.slit_width, min_depth=cfg.split.min_rib_depth,
        line_width=cfg.preset.line_width, max_mouth=cfg.validation.max_mouth_width, exclude=deeper_lines(cfg, bb, i, anchor),
        bays=tuple(bays), pocket_strip=cfg.servo_strip(), anchor=anchor, tubes=tuple(tubes), hollow=hollow_spec(cfg, i),
        cuts=cuts,
    )


def hollow_spec(cfg: Config, i: int) -> HollowSpec | None:
    """Évidements du niveau i (réglage global surchargé par la couche) ; None : désactivés (plaques pleines)."""
    h = cfg.layer_hollow(cfg.rib_layers[i])
    if h.mode == "off":
        return None
    return HollowSpec(
        h.mode, h.bridge_width, h.pitch, h.keep_ratio, h.keep_min, cfg.preset.max_overhang_deg, h.ramp_factor
    )


def hollow_on(cfg: Config) -> bool:
    """Au moins une couche de nervures évidée."""
    return any(hollow_spec(cfg, i) is not None for i in range(len(cfg.rib_layers)))


def cut_guides(plan: LayoutPlan | None, z_root: float) -> CutGuides | None:
    """Coupes d'un plan de découpe en z absolus (`z_root` : bas de la pièce, boîte optimale) : plans z = cte et plans
    dans la corde x = x0 + pente·z limités à leur tronçon (`ribs.CutGuides`)."""
    if plan is None:
        return None
    lines = []
    for sp in plan.segments:
        for p in sp.planes:
            m = (p.xb - p.xa) / (p.zb - p.za)
            lines.append((p.xa - (z_root + p.za) * m, m, z_root + sp.z0, z_root + sp.z1))
    return CutGuides(tuple(z_root + c for c in plan.cuts), tuple(lines))


def wall_warnings(
    bb, mid, bores, cfg: Config, counts: Sequence[int], bays: Sequence[ServoBay] = (), anchor=None,
    tubes: Sequence[Tube] = (),
) -> list[str]:
    """Avertissements des niveaux de nervures vidés par l'arrêt avant les parois raides (`counts` : plaques par
    niveau). Calcul refait seulement pour un niveau sans plaque : rare, donc sans coût en temps normal."""
    out = []
    for i, (layer, n) in enumerate(zip(cfg.rib_layers, counts)):
        if n == 0 and emptied_by_walls(bb, mid, list(bores), layer, **rib_options(cfg, bb, i, bays, anchor, tubes)):
            out.append(
                f"niveau de nervures « {layer.name} » : aucune plaque — toutes écartées des parois trop raides pour "
                f"la fente (slit_width {cfg.slit_width} mm, angle {cfg.rib_angle(layer)}°, "
                f"validation.max_mouth_width {cfg.validation.max_mouth_width} mm) ; réduire slit_width ou augmenter "
                "validation.max_mouth_width"
            )
    return out


def deeper_lines(cfg: Config, bb, i: int, anchor=None) -> list[tuple[int, float]]:
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
        for line in plate_lines(bb, other, cfg.rib_angle(other), anchor)
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
    tubes: tuple[Tube, ...] = ()  # SBAR transversales / inclinées
    plan: LayoutPlan | None = None  # plan de découpe déjà calculé (SBAR transversales : coupes dans la corde)


def split_spars(
    spars: Sequence[tuple[str, Solid]], cfg: Config
) -> tuple[list[tuple[str, Solid]], list[tuple[str, Solid]]]:
    """(longerons parallèles à Z, à la tolérance `passages.max_tilt_deg` près ; longerons transversaux/inclinés)."""
    vertical, transversal = [], []
    for name, solid in spars:
        (transversal if is_transversal(solid, cfg) else vertical).append((name, solid))
    return vertical, transversal


def chord_regions(plan: LayoutPlan | None, z_root: float) -> list[Region]:
    """Morceaux de la coupe dans la corde (repère de la pièce, z absolu), nommés comme les morceaux (`plan.names`) ;
    un seul sans coupe dans la corde (les coupes en Z ne relient aucune couche au contour : sans effet)."""
    if plan is None or not plan.chord_cut:
        return [Region()]
    names = plan.names()
    out, k = [], 0
    last = len(plan.segments) - 1
    for i, sp in enumerate(plan.segments):
        zb = band(None if i == 0 else z_root + sp.z0, None if i == last else z_root + sp.z1)
        lines = []
        for p in sp.planes:  # x = xa + (z − z_root − za)·pente
            slope = (p.xb - p.xa) / (p.zb - p.za)
            lines.append((p.xa - (z_root + p.za) * slope, slope))
        order = sp.order()
        for j in range(sp.k):
            hs = list(zb)
            if j > 0:
                hs.append(beyond(*lines[j - 1], 1))
            if j < sp.k - 1:
                hs.append(beyond(*lines[j], -1))
            out.append(Region(tuple(hs), names[k + order.index(j)]))
        k += sp.k
    return out


def critical_planes(bores: Sequence[Bore], bays: Sequence[ServoBay], filled: Sequence[Passage], zmin: float,
                    zmax: float) -> list[float]:
    """Plans z = cte (absolus) où la génération pose des faces perpendiculaires à Z : bouts des trous et de leurs
    fentes (± jeu, `bore_extent`) et bouts du longeron, rebouchages, baies de servo (boîte, rampes, ponts de la fente
    de renfort, rencontre trou–poche). Une coupe en Z posée sur l'un d'eux fait échouer le booléen d'OpenCascade sans
    erreur (résultat vide) : le planificateur s'en écarte (`segment.CUT_CLEARANCE`). Les SBAR transversales n'en
    ajoutent pas : leurs bouts sont perpendiculaires à leur axe, pas à Z (et leurs fentes suivent le plan de coupe)."""
    zs: set[float] = set()
    for b in bores:
        lo, hi = bore_extent(b, zmin, zmax)
        zs |= {b.z0, b.z1, lo, hi}
        for bay in bays:
            if b.cut_hole and (meet := hole_meets_pocket(bay, b.x, b.y, b.radius, lo, hi)) is not None:
                zs |= set(meet)
    for f in filled:
        zs |= {f.z0, f.z1}
    for bay in bays:
        lo, _, hi, _ = pocket_profile(bay).bounds
        zs |= {bay.z0, bay.z1, bay.z0 - bay.ramp, bay.z1 + bay.ramp, lo, hi}
        zs |= {z for g in bay.rf_gaps for z in g[:2]}
        zs |= {z for r in bay.rings for z in r[3:]}
    return sorted(z for z in zs if math.isfinite(z) and zmin < z < zmax)


def rib_crossings(cfg: Config, origin: float, zmin: float, zmax: float) -> list[float]:
    """Plans z = cte où les deux familles de plaques d'un même niveau de nervures se croisent (`plate_lines` : plaques
    x = c ± tan(angle)·(z − origine), c au pas `spacing` ; croisements en origine + j·spacing / (2·tan(angle))). Leurs
    arêtes de croisement (parallèles à Y) sont dans ces plans : une coupe en Z posée dessus fait échouer le booléen
    (aile ×3, coupe à 420 mm). `origin` : bas de la boîte de calage du motif (`skins_anchor`, sinon la peau).

    Évidements : marches verticales au pied et au-dessus de chaque pont (arêtes parallèles à Y à z = pont ±
    bridge_width·d1/2),
    pont aux croisements ou tous les `pitch` le long de la plaque à partir de `origin` (`ribs.bridge_positions`)."""
    out = []
    for i, layer in enumerate(cfg.rib_layers):
        ang = math.radians(cfg.rib_angle(layer))
        t = math.tan(ang)
        if t <= 0 or layer.spacing <= 0:
            continue
        step = layer.spacing / (2 * t)
        j0, j1 = math.ceil((zmin - origin) / step - 1e-9), math.floor((zmax - origin) / step + 1e-9)
        if j1 - j0 > 100_000:
            continue
        out += [origin + j * step for j in range(j0, j1 + 1)]
        hollow = hollow_spec(cfg, i)
        if hollow is None:
            continue
        lift = hollow.bridge_width * math.cos(ang) / 2
        if hollow.mode == "pas":
            step = hollow.pitch * math.cos(ang)
        for off in (-lift, lift):  # marche au pied du pont (fin de rampe) et au-dessus
            j0, j1 = math.ceil((zmin - off - origin) / step - 1e-9), math.floor((zmax - off - origin) / step + 1e-9)
            if j1 - j0 <= 100_000:
                out += [origin + j * step + off for j in range(j0, j1 + 1)]
    return out


def rib_origin_of(skin, anchor=None) -> float:
    """Bas de la boîte de calage du motif de nervures (`plate_lines` : `anchor`, sinon la boîte de la peau)."""
    return (anchor if anchor is not None else skin.bounding_box()).min.Z


def layout_for(
    height: float, z0: float, sample, cfg: Config, bores, bays, filled: Sequence[Passage] = (),
    rib_origin: float | None = None,
) -> LayoutPlan:
    """Plan de découpe (coupes en Z puis dans la corde) d'une pièce d'étendue [z0, z0 + height] ; `sample` : zs absolus
    → points des sections. Même calcul en génération (`segment_part`, SBAR transversales) et dans l'aperçu ; les
    coupes en Z restent écartées des plans critiques (`critical_planes`, `rib_crossings` ; `rib_origin` : bas de la
    boîte de calage du motif de nervures, `rib_origin_of` ; None : pièce sans nervures)."""
    seg = cfg.segment
    (W, D, H), _, _ = _print_volume(cfg)
    max_h = seg.max_height if seg.max_height is not None else H - seg.margin
    obstacles = [*bore_obstacles(bores, z0, z0 + height), *bay_obstacles(bays, z0, cfg.servo_strip())]
    zmax = z0 + height
    ribs = [] if rib_origin is None else rib_crossings(cfg, rib_origin, z0, zmax)
    critical = [z - z0 for z in (*critical_planes(bores, bays, filled, z0, zmax), *ribs) if z0 < z < zmax]
    return plan_layout(
        height, max_h, lambda zs: sample([z0 + z for z in zs]), (W, D), seg.margin, seg.cuts_at or None,
        chord=seg.chord, max_overhang_deg=cfg.preset.max_overhang_deg, obstacles=obstacles, critical=critical,
    )


def cut_plan(
    skin: Solid, cfg: Config, bores, bays, mesh=None, filled: Sequence[Passage] = (), rib_origin: float | None = None,
) -> LayoutPlan | None:
    """Plan de découpe d'une peau orientée (avant rebouchage), calculé avant les nervures : il ne dépend que des
    sections de la peau, des trous, baies, rebouchages et du calage du motif. Le même plan sert aux SBAR
    transversales, aux évidements (zones pleines aux coupes) et à la découpe (`Generated.plan`) ; l'aperçu fait le
    même calcul sur le même maillage. None : découpe désactivée, ou plan impossible (SegmentError, signalée par la
    découpe)."""
    if cfg.segment.mode == "off":
        return None
    mesh = to_mesh(skin, cfg.validation.tessellation_tol) if mesh is None else mesh
    obb = skin.bounding_box(optimal=True)
    try:
        return layout_for(obb.max.Z - obb.min.Z, obb.min.Z, mesh_sections(mesh), cfg, bores, bays, filled, rib_origin)
    except SegmentError:
        return None


def skin_tubes(
    skin: Solid, transversal, cfg: Config, cables, bores, bays, mesh=None, filled: Sequence[Passage] = (),
    rib_origin: float | None = None,
) -> tuple[list[tuple[Tube, Solid]], LayoutPlan | None]:
    """SBAR transversales d'une peau orientée (avant rebouchage) et plan de découpe qui fixe leurs fentes.
    Plan impossible (SegmentError) : None — l'erreur est signalée par la découpe."""
    if not transversal:
        return [], None
    tol = cfg.validation.tessellation_tol
    mesh = to_mesh(skin, tol) if mesh is None else mesh
    obb = skin.bounding_box(optimal=True)
    plan = cut_plan(skin, cfg, bores, bays, mesh, filled, rib_origin)
    bb = skin.bounding_box()
    others = lambda z: [g for b in bores for g in bore_layer(b, z, bb)]  # noqa: E731
    return spar_tubes(transversal, cfg, mesh, bb, cables, chord_regions(plan, obb.min.Z), others), plan


def generate_skin(
    skin: Solid, spars: list[tuple[str, Solid]], cfg: Config, cables: frozenset[str] = frozenset(),
    servos: Sequence[tuple[str, Solid]] = (), anchor=None,
) -> Generated:
    """Nervures, trous et baies d'une peau orientée. `anchor` : boîte de calage du motif de nervures (boîte globale
    des peaux d'un modèle à plusieurs corps d'aile ; None = boîte de la peau)."""
    tol = cfg.validation.tessellation_tol
    bb = skin.bounding_box()
    nx, nz = cfg.split.grid
    mid = MidSurface.from_skin(skin, nx, nz, tol)
    vertical, transversal = split_spars(spars, cfg)
    inside = spars_inside(skin, vertical, tol)
    try:
        found = find_passages(skin, cfg.passages.max_tilt_deg)  # une seule recherche des alésages
        bores = extend_to_existing_bores(spar_bores(inside, cfg, cables), skin, cfg, found)
        to_fill = passages_to_fill(skin, cfg, exclude=bores, found=found)
        bores += passage_bores(skin, cfg, exclude=bores, found=found)
        bays = servo_bays(servos, skin, mid, cfg, bores)
        held, plan = skin_tubes(
            skin, transversal, cfg, cables, bores, bays, filled=[f for _, f in to_fill],
            rib_origin=rib_origin_of(skin, anchor),
        )
    except (BoreError, ServoError) as exc:
        raise PipelineError(str(exc)) from exc
    tubes = [t for t, _ in held]
    guides = None
    if hollow_on(cfg):  # évidements : nervures pleines autour des coupes, plan calculé avant (le même sert à la découpe)
        if plan is None:
            plan = cut_plan(skin, cfg, bores, bays, filled=[f for _, f in to_fill], rib_origin=rib_origin_of(skin, anchor))
        guides = cut_guides(plan, skin.bounding_box(optimal=True).min.Z)
    skin = fill_skin(skin, to_fill, tol)
    plates, counts = [], []
    for i, layer in enumerate(cfg.rib_layers):
        layer_plates = diamond_plates(bb, mid, bores, layer, **rib_options(cfg, bb, i, bays, anchor, tubes, guides))
        plates += layer_plates
        counts.append(len(layer_plates))
    warns = [*wall_warnings(bb, mid, bores, cfg, counts, bays, anchor, tubes), *reinforce_warnings(bays),
             *tube_warnings(tubes, cfg, bays)]
    # gaines et longerons qui entrent dans une baie : simplement découpés par la poche
    # poches et fentes de renfort : ouvertures voulues de la peau
    pockets = [t for b in bays for t in (pocket_solid(b), *reinforce_solids(b))]
    tube_tools = [tool for t in tubes for tool in t.tools()]
    result = skin.cut(*plates, *bore_cut_tools(bores, bb, mid), *tube_tools, *pockets)
    part, slivers = main_solid(result)
    for name, spar in [*inside, *((t.name, solid) for t, solid in held)]:
        blocked = spar_obstruction(spar, part)
        if blocked > MAX_SPAR_OBSTRUCTION:
            raise PipelineError(f"{name} : le longeron traverse la matière ({blocked:.0f} mm³) — vérifier son axe")
    # trous des SBAR transversales entre leurs bouts réels, bouches comprises (`Tube.opening`) : ouvertures voulues
    # de la peau. Pas le prolongement d'outil au-delà (une entaille par lui reste « peau entamée »), et le trou ne
    # touche la peau qu'à ses bouches (sinon TubeError, `tubes._check_wall`)
    openings = (*pockets, *hole_openings(bores, bays, bb), *(t.opening_solid() for t in tubes))
    return Generated(
        part, tuple(bores), len(plates), tuple(f for _, f in to_fill), slivers, tuple(warns), tuple(bays), openings,
        tuple(tubes), plan,
    )




# erreurs de géométrie possibles (build123d / OpenCascade) : traduites en PipelineError
GEOMETRY_ERRORS = (ValueError, RuntimeError, Standard_Failure)


class PrintVolume(NamedTuple):
    size: tuple[float, float, float]  # largeur, profondeur, hauteur (mm)
    warning: str | None  # gabarit ≠ imprimante choisie, gabarit illisible…
    label: str  # imprimante du volume : celle du gabarit (« P1S »), sinon celle de la config


@functools.lru_cache(maxsize=8)
def _template_at(path: str, mtime_ns: int, size: int):
    return read_template(Path(path))


def _cached_template(path: Path):
    """Gabarit relu seulement s'il a changé (chemin, date, taille) : `_print_volume` est appelé plusieurs fois par
    corps."""
    try:
        st = path.stat()
    except OSError:
        return read_template(path)  # BambuError en français
    return _template_at(str(path), st.st_mtime_ns, st.st_size)


def _print_volume(cfg: Config) -> PrintVolume:
    """(largeur, profondeur, hauteur), avertissement, libellé de l'imprimante : gabarit Bambu s'il existe (libellé
    tiré de son `printer_model`), sinon imprimante de la config."""
    try:
        path = resolve_template(cfg)
        t = _cached_template(path)
        model = str(t.settings.get("printer_model") or "")
        warn = None
        if model and not model.lower().endswith(cfg.printer):
            warn = (f"gabarit {path.name} ({model}) ≠ imprimante choisie {cfg.printer}\u00a0: "
                    "le volume et le projet suivent le gabarit")
        label = model.split()[-1].upper() if model.split() else cfg.printer.upper()
        return PrintVolume((t.bed[0], t.bed[1], t.height), warn, label)
    except BambuError as exc:
        warn = f"gabarit Bambu illisible ({exc}) — volume de l'imprimante {cfg.printer} utilisé"
    except ConfigError as exc:
        warn = (
            f"{exc} — volume de l'imprimante {cfg.printer} utilisé" if cfg.bambu.template not in (None, AUTO_TEMPLATE) else None
        )
    return PrintVolume(tuple(cfg.printer_spec.volume), warn, cfg.printer.upper())


def volume_warnings(cfg: Config, size: tuple[float, float, float] | None = None) -> list[str]:
    """Avertissements « volume d'impression », mêmes textes en génération (`segment_part`) et dans l'aperçu
    (`preview._layout`) : gabarit ≠ imprimante ou illisible, segment.max_height au-delà de la hauteur utile, et en
    `segment.mode: off` pièce de dimensions `size` (largeur, profondeur, hauteur) trop grande."""
    seg = cfg.segment
    (W, D, H), fallback, _ = _print_volume(cfg)
    out = [fallback] if fallback else []
    usable = H - seg.margin
    if seg.max_height is not None and seg.max_height > usable + 1e-9:
        out.append(
            f"segment.max_height ({seg.max_height:g} mm) dépasse la hauteur utile "
            f"({usable:g} mm = {H:g} − marge {seg.margin:g})"
        )
    if seg.mode == "off" and size is not None:
        w, d, h = size
        max_h = seg.max_height if seg.max_height is not None else usable
        if h > max_h + 1e-9 or w > W - 2 * seg.margin or d > D - 2 * seg.margin:
            out.append(
                f"la pièce ({h:.0f} mm / {w:.0f}×{d:.0f} mm) dépasse le volume d'impression "
                "— découpe désactivée (segment.mode: off)"
            )
    return out


def usable_height(cfg: Config) -> float:
    """Hauteur max d'un tronçon (mm) : segment.max_height, sinon hauteur d'impression − marge."""
    seg = cfg.segment
    if seg.max_height is not None:
        return seg.max_height
    (_, _, H), _, _ = _print_volume(cfg)
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


def layout_notices(plan: LayoutPlan, cfg: Config, bed: tuple[float, float], label: str | None = None) -> list[str]:
    """Décisions automatiques de découpe (informations pour l'utilisateur), mêmes textes en génération et dans
    l'aperçu ; `bed` : plateau du gabarit (largeur, profondeur) ; `label` : son imprimante (`_print_volume`)."""
    notices = []
    n_z = len(plan.z.cuts) + 1
    if plan.z.reason:
        notices.append(f"découpe automatique : {n_z} tronçons en Z ({plan.z.reason})")
    if plan.extra_z:
        notices.append(
            f"découpe automatique : {len(plan.segments)} tronçons en Z pour limiter l'inclinaison de la coupe dans "
            f"la corde (surplomb max {cfg.preset.max_overhang_deg:g}°)"
        )
    for a, b in plan.z.moved:
        notices.append(
            f"découpe : coupe imposée à z = {a:g} mm déplacée à {b:.2f} mm (trop près d'une face de trou, de baie ou "
            f"de rebouchage : le booléen y échoue)"
        )
    label = label if label is not None else _print_volume(cfg).label
    plateau = f"plateau {label} ({bed[0]:.0f} × {bed[1]:.0f} mm)"
    need = plan.z.chord
    for i, sp in enumerate(plan.segments, 1):
        if not sp.planes:
            continue
        where = f"T{i} : " if len(plan.segments) > 1 else ""
        if need is not None and need.z0 <= sp.z1 and need.z1 >= sp.z0 and sp.size is not None:
            why = f"section {sp.size[0]:.0f} × {sp.size[1]:.0f} mm > {plateau} même en diagonale"
        else:
            why = f"tronçon trop grand pour le {plateau} même tourné"
        line = f"découpe automatique : {where}coupe dans la corde en {sp.k} morceaux ({why})"
        for p in sp.planes:
            if p.shift:
                toward = "le bord de fuite" if (p.shift > 0) == sp.le_at_min else "le bord d'attaque"
                line += (f" ; plan décalé de {abs(p.shift):.1f} mm vers {toward} pour éviter "
                         + ", ".join(p.avoided or ("un obstacle",)))
        notices.append(line)
    return notices


def body_notice(n_pieces: int, cfg: Config) -> str:
    """Corps d'aile (plusieurs corps dans le STEP) encore découpé pour tenir dans le volume d'impression."""
    return (
        f"corps d'aile déjà découpé dans la CAO, encore découpé en {n_pieces} morceaux pour tenir dans le volume "
        f"d'impression ({_print_volume(cfg).label})"
    )


def segment_part(
    body_name: str, result: Solid, cfg: Config, progress: Callable[[str], None], skin: Solid | None = None,
    original: Solid | None = None, bores: Sequence[Bore] = (), bays: Sequence[ServoBay] = (),
    plan: LayoutPlan | None = None, filled: Sequence[Passage] = (), rib_origin: float | None = None,
) -> Segmented:
    """Découpe en morceaux : tronçons en Z selon la hauteur et l'empreinte sur le plateau, puis coupe dans la corde
    des tronçons qui ne tiennent pas (`plan_layout`). L'empreinte est lue sur les sections de `skin` (peau d'origine,
    même contour extérieur que `result` ; `result` par défaut). `original` (peau de référence de la validation) est
    coupée comme `result` quand il y a une coupe dans la corde ; `bores`, `bays` : obstacles des plans de coupe ;
    `bores`, `bays`, `filled` : plans critiques dont les coupes en Z s'écartent (`critical_planes`). Une coupe dont le
    booléen échoue malgré tout est décalée de quelques centièmes à dixièmes de mm (`segment.cut_chain`), pour
    `result` comme pour `original`, et signalée."""
    seg = cfg.segment
    (W, D, _), _, printer = _print_volume(cfg)
    bb = result.bounding_box(optimal=True)
    size = (bb.max.X - bb.min.X, bb.max.Y - bb.min.Y, bb.max.Z - bb.min.Z)
    warnings = [f"{body_name} : {w}" for w in volume_warnings(cfg, size)]
    if seg.mode == "off":
        for w_ in warnings:
            progress(f"⚠ {w_}")
        return Segmented((result,), tuple(warnings), (), ("T1",), ((0.0, bb.max.Z - bb.min.Z),))
    for w_ in warnings:
        progress(f"⚠ {w_}")
    z0 = bb.min.Z
    height = bb.max.Z - z0
    try:
        if plan is None:  # plan déjà calculé par generate_skin quand il y a des SBAR transversales
            sample = section_sampler(skin if skin is not None else result, cfg.validation.tessellation_tol)
            plan = layout_for(height, z0, sample, cfg, bores, bays, filled, rib_origin)
        cuts = plan.cuts
        names = plan.names()
        checks: list[tuple[Solid, Callable]] = []
        gap = slit_gap(cfg)
        retried: list[str] = []
        actual: dict[float, float] = {}  # coupe en Z planifiée (absolue) → coupe retenue après repli

        def moved(a: float, b: float) -> None:
            actual[a] = b
            retried.append(
                f"découpe : coupe en Z déplacée de z = {a - z0:.2f} à {b - z0:.2f} mm depuis l'emplanture (booléen en "
                "échec sur des faces coïncidentes)"
            )

        where = lambda z: f"z = {z - z0:.2f} mm depuis l'emplanture"  # noqa: E731
        shifted: dict[int, float] = {}  # tronçon → décalage des plans de coupe dans la corde (repli)
        last = len(plan.segments) - 1

        def actual_seg(i: int, lo: float | None, hi: float | None, shift: float):
            sp = plan.segments[i]
            return replace(
                sp, z0=sp.z0 if lo is None else lo - z0, z1=sp.z1 if hi is None else hi - z0,
                planes=tuple(p.shifted(shift, p.avoided) for p in sp.planes) if shift else sp.planes,
            )

        if not plan.chord_cut:
            pieces = split_part(result, [z0 + c for c in cuts], moved) if cuts else [result]
            at = [actual.get(z0 + c, z0 + c) for c in cuts]
            segs = [actual_seg(i, lo, hi, 0.0) for i, (lo, hi) in enumerate(zip([None, *at], [*at, None]))]
        else:
            def make(i: int, lo: float | None, hi: float | None, shift: float):
                shift = shift or shifted.get(i, 0.0)  # morceau recoupé (coupe du haut déplacée) : même décalage
                sp = actual_seg(i, lo, hi, shift)
                label = f"{body_name} : T{i + 1}"
                cut = cut_segment(result, sp, z0, i == 0, i == last, label)
                ref = cut_segment(original, sp, z0, i == 0, i == last, f"{label} (référence)") if original is not None else None
                if shift:
                    shifted[i] = shift
                return sp, cut, ref

            done = cut_chain([z0 + c for c in cuts], make, moved, shifts=True, where=where)
            for i, d in sorted(shifted.items()):
                retried.append(
                    f"découpe : T{i + 1} : plans de coupe dans la corde décalés de {d:+.2f} mm (booléen en échec sur "
                    "des faces coïncidentes)"
                )
            pieces = []
            segs = []
            for sp, cut, ref in done:
                order = sp.order()
                segs.append(sp)
                pieces += [cut[j] for j in order]
                if ref is not None:
                    checks += [(ref[j], _face_exempt(cut_lines(sp, j, CUT_FACE_TOL), gap)) for j in order]
        spans = tuple((s.z0, s.z1) for s in segs for _ in range(s.k))
    except SegmentError as exc:
        raise PipelineError(f"{body_name} : {exc}") from exc
    notices = [*layout_notices(plan, cfg, (W, D), printer), *retried]
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
    # au plus MAX_FAILED_LAYERS couches dessinées, comme pour une pièce validée d'un seul tenant (les plus basses)
    failed = {z: failed[z] for z in sorted(failed)[:MAX_FAILED_LAYERS]}
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
    """Rotation de chaque tronçon sur le plateau + erreurs (morceau trop grand même tourné). `segment.mode: off` :
    pas d'erreur (l'avertissement « dépasse le volume d'impression » de `segment_part` le dit déjà) ; sinon le plan
    de découpe aurait dû faire tenir le morceau : erreur de validation (`oversize_issue`), pièce non imprimable."""
    (W, D, _), _, printer = _print_volume(cfg)
    margin = cfg.segment.margin
    placements, errors = [], []
    for i, piece in enumerate(pieces, 1):
        pb = piece.bounding_box(optimal=True)
        w, d = pb.max.X - pb.min.X, pb.max.Y - pb.min.Y
        if w <= W - 2 * margin and d <= D - 2 * margin:  # tient droit : pas besoin du maillage
            placements.append(Placement(0.0, True, min(W - 2 * margin - w, D - 2 * margin - d), (w, d)))
            continue
        pl = fit_on_bed(_footprint(piece), (W, D), margin)
        if pl.fits:
            progress(f"{body_name} : {names[i - 1] if names else f'T{i}'} : {format_rotation(pl)}")
        elif cfg.segment.mode != "off":
            errors.append(
                f"{body_name} : {f'le morceau {names[i - 1]}' if names else f'le tronçon {i}'} "
                f"({pl.size[0]:.0f}×{pl.size[1]:.0f} mm au mieux, même tourné) ne tient pas sur le plateau {printer} "
                f"({W:.0f}×{D:.0f} mm, marge {margin:g} mm) malgré le plan de découpe — non imprimable : imposer "
                "des coupes (segment.cuts_at) ou choisir une imprimante plus grande"
            )
        if not pl.fits:  # posé sans rotation : marge et emprise sont celles de 0°
            pl = Placement(0.0, False, min(W - 2 * margin - w, D - 2 * margin - d), (w, d))
        placements.append(pl)
    for e in errors:
        progress(f"✘ {e}")
    return tuple(placements), tuple(errors)


def oversize_issue(message: str, z: float) -> Issue:
    """Morceau hors plateau malgré le plan de découpe : erreur de validation (pièce non imprimable)."""
    return Issue("volume", "error", z, message)


def _with_issues(report: Report, extra: Sequence[Issue]) -> Report:
    return replace(report, issues=(*report.issues, *extra)) if extra else report


def skins_anchor(skins: Sequence[Solid]):
    """Boîte de calage du motif de nervures : None pour une seule peau (boîte de la peau, comme avant), boîte
    englobante de toutes les peaux orientées sinon — même calcul en génération et dans l'aperçu."""
    if len(skins) < 2:
        return None
    box = skins[0].bounding_box()
    for skin in skins[1:]:
        box = box.add(skin.bounding_box())
    return box


def _overlaps(a, b) -> bool:
    return all(
        min(getattr(a.max, k), getattr(b.max, k)) > max(getattr(a.min, k), getattr(b.min, k)) for k in ("X", "Y", "Z")
    )


def servos_for(skin: Solid, servos: Sequence[tuple[str, Solid]], several: bool) -> list[tuple[str, Solid]]:
    """Baies de servo d'une peau : toutes s'il n'y a qu'une peau (une boîte hors de la pièce est refusée par
    `servo_bays`), sinon celles dont la boîte recoupe la boîte de la peau."""
    if not several:
        return list(servos)
    bb = skin.bounding_box()
    return [(name, solid) for name, solid in servos if _overlaps(solid.bounding_box(), bb)]


def check_spars_assigned(
    skins: Sequence[tuple[str, Solid]], spars: Sequence[tuple[str, Solid]], tol: float, cfg: Config | None = None
) -> None:
    """Plusieurs corps d'aile : un longeron (ou une gaine) dont la boîte recoupe celle d'un corps doit être logé dans
    au moins un corps (`spars_inside`). Sinon (axe sur une jonction dans la corde, entre deux contours), il serait
    ignoré en silence et traverserait la matière : PipelineError en français."""
    for name, solid in spars:
        if cfg is not None and is_transversal(solid, cfg):
            continue  # SBAR transversale : attribuée à chaque corps que son axe traverse (`spar_tubes`)
        sb = solid.bounding_box()
        touched = [n for n, skin in skins if _overlaps(sb, skin.bounding_box())]
        if touched and not any(spars_inside(skin, [(name, solid)], tol) for _, skin in skins):
            where = " et ".join(touched)
            raise PipelineError(
                f"{name} : sur la jonction entre {where} (axe hors du contour de chaque corps) — déplacer le longeron "
                "ou la coupe dans la CAO"
            )


def run_model(model: Model, cfg: Config, progress: Callable[[str], None] = lambda m: None) -> RunResult:
    """Génération de toutes les peaux du modèle. Plusieurs peaux (corps d'aile découpés dans la CAO) : un morceau
    par corps (re-découpé s'il ne tient pas), orientation commune, motif de nervures calé sur la boîte globale
    (`skins_anchor`), longerons, gaines et baies attribués à chaque corps qu'ils traversent."""
    skins = model.of("skin")
    progress("orientation…")
    try:
        o = orient(model, cfg)
    except GEOMETRY_ERRORS as exc:
        raise PipelineError(f"orientation impossible : {exc}") from exc
    progress(f"orientation : {o.source}")
    spars = [(s.body.name, s.body.solid.moved(o.location)) for s in model.of("spar")]
    cables = frozenset(c.body.name for c in model.cables)
    servos = [(s.body.name, s.body.solid.moved(o.location)) for s in model.of("servo")]
    oriented = [s.body.solid.moved(o.location) for s in skins]
    several = len(skins) > 1
    anchor = skins_anchor(oriented)
    if several:
        progress(f"{len(skins)} corps d'aile ({', '.join(s.body.name for s in skins)}) : un morceau par corps, "
                 "nervures alignées d'un corps à l'autre")
        for name, solid in servos:
            if not any(servos_for(skin, [(name, solid)], True) for skin in oriented):
                raise PipelineError(f"{name} : la boîte ne traverse aucun corps d'aile (elle est hors de la pièce)")
        check_spars_assigned(
            [(s.body.name, skin) for s, skin in zip(skins, oriented)], spars, cfg.validation.tessellation_tol, cfg
        )
    for w in o.warnings:
        progress(f"⚠ {w}")
    bodies = []
    for s, skin in zip(skins, oriented):
        progress(f"{s.body.name} : surface médiane, trous et nervures…")
        try:
            gen = generate_skin(skin, spars, cfg, cables, servos_for(skin, servos, several), anchor=anchor)
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
            + (f"{len(gen.tubes)} SBAR transversale(s), " if gen.tubes else "")
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
            raise PipelineError(
                f"{s.body.name} : ouverture de la peau impossible (poche, trou de SBAR) ({type(exc).__name__} : {exc})"
            ) from exc
        try:
            seg = segment_part(
                s.body.name, result, cfg, progress, skin=skin, original=original, bores=bores, bays=gen.bays,
                plan=gen.plan, filled=gen.filled, rib_origin=rib_origin_of(skin, anchor),
            )
        except PipelineError:
            raise
        except (*GEOMETRY_ERRORS, ShapelyError) as exc:  # planificateur (shapely) ou découpe (OpenCascade)
            raise PipelineError(f"{s.body.name} : découpe impossible ({type(exc).__name__} : {exc})") from exc
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
        notices = seg.notices
        if several and len(seg.pieces) > 1:
            notices = (
                body_notice(len(seg.pieces), cfg),
                *notices,
            )
            progress(f"{s.body.name} : {notices[0]}")
        names = seg.names if seg.chord else ()
        placements, too_big = place_pieces(s.body.name, seg.pieces, cfg, progress, names)
        if too_big:  # segment.mode auto : le plan de découpe s'est trompé, pièce non imprimable
            bb = result.bounding_box(optimal=True)
            spans = seg.spans if len(seg.spans) == len(seg.pieces) else [(0.0, 0.0)] * len(seg.pieces)
            bad = [i for i, pl in enumerate(placements) if not pl.fits]
            report = _with_issues(report, [oversize_issue(m, bb.min.Z + spans[i][0]) for i, m in zip(bad, too_big)])
            if piece_reports:
                piece_reports = tuple(
                    _with_issues(r, [oversize_issue(m, 0.0) for i, m in zip(bad, too_big) if i == k])
                    for k, r in enumerate(piece_reports)
                )
        warns = (*(o.warnings if not bodies else ()), *gen.warnings, *warns)
        bodies.append(
            BodyResult(
                s.body.name, skin, result, report, bores, n, filled, warns, seg.pieces, gen.bays, placements,
                notices, seg.names, seg.spans, piece_reports, gen.tubes,
            )
        )
    return RunResult(o, tuple(bodies))
