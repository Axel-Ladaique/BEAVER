"""Aperçu rapide, sans booléen OpenCascade, de la pièce générée (interface graphique).

Mêmes étapes et même configuration que `pipeline.generate_skin` jusqu'aux profils 2D des plaques ; la coupe
à une hauteur z est ensuite calculée en 2D (shapely) : tranche de la peau − empreintes des plaques − trous,
fentes et attaches. C'est une **approximation** de la couche réelle (maillage de la peau d'origine, pas
du résultat), à afficher comme telle.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import OrderedDict
from collections.abc import Sequence
from concurrent.futures import Future
from dataclasses import dataclass, field, replace
from itertools import pairwise
from pathlib import Path

import numpy as np
import trimesh
from build123d import Compound, Solid
from shapely import affinity
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import unary_union

from aerorib.classify import ClassifyError, classify
from aerorib.config import Config
from aerorib.features.bores import (
    Bore,
    BoreError,
    Passage,
    anchor_profiles_2d,
    bore_extent,
    extend_to_existing_bores,
    fill_radius,
    find_passages,
    passage_bores,
    passages_to_fill,
    spar_bores,
)
from aerorib.features.ribs import diamond_profiles, plate_solid
from aerorib.features.tubes import Tube
from aerorib.features.servo import ServoBay, ServoError, pocket_section, reinforce_section, reinforce_warnings, servo_bays
from aerorib.io_step import StepReadError, read_step
from aerorib.midsurface import MidSurface
from aerorib.orient import Orientation, orient
from aerorib.pipeline import (
    GEOMETRY_ERRORS,
    PipelineError,
    check_fill,
    check_spars_assigned,
    rib_options,
    servos_for,
    skin_tubes,
    skins_anchor,
    split_spars,
    spars_inside,
    _print_volume,
    usable_height,
    wall_warnings,
)
from aerorib.features.chord_cut import bay_obstacles, bore_obstacles, plan_layout
from aerorib.segment import SegmentError
from aerorib.slicing import Layer, layer_heights, mesh_sections, to_mesh
from aerorib.validate import Issue, check_connectivity, check_material_width, check_narrow_slits, slicer_closing

SECONDS_PER_PLATE = 0.3  # s : coût estimé d'une plaque (recalé : aile Fusion v2, 84 plaques, 29 s réelles)
MAX_MESH_PLATES = 600  # au-delà, pas de maillage 3D des plaques (trop lourd pour le navigateur)
SAMPLED_LAYERS = 20  # couches échantillonnées pour le trajet et la masse
CACHE_SIZE = 4
_FAR = 1e4
_MIN_AREA = 1e-4  # mm² : fragment de coupe négligeable (bruit numérique)
_PLATES_TOL = 0.05  # mm : maillage des plaques pour la vue 3D (plaques planes : précision sans effet)


class PreviewError(ValueError):
    """Aperçu impossible (message en français)."""


def _round(v: float) -> float:
    return round(float(v), 4) + 0.0  # + 0.0 : jamais « -0 » à l'écran


def _ring(coords) -> list[list[float]]:
    return [[_round(x), _round(y)] for x, y in coords]


def _poly_dict(p: Polygon) -> dict:
    return {"exterieur": _ring(p.exterior.coords), "trous": [_ring(r.coords) for r in p.interiors]}


def _parts(geom) -> list[Polygon]:
    if geom.is_empty:
        return []
    return [g for g in getattr(geom, "geoms", [geom]) if isinstance(g, Polygon) and g.area > _MIN_AREA]


# --- préparation (lente, mise en cache) ---------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class Prepared:
    """Pièce orientée et tout ce qui ne dépend pas des nervures (même calcul que `generate_skin`).

    Partagée par le cache entre appels (et threads) : **lecture seule**. Ses champs sont immuables, sauf le
    maillage, dont trimesh remplit un cache interne paresseux : tout accès passe par `mesh_lock`.
    """

    name: str
    skin: Solid  # peau orientée, avant rebouchage
    spars: tuple[tuple[str, Solid], ...]  # longerons orientés (tous)
    orientation: Orientation
    bb: object  # BoundBox de la peau (non optimale, comme generate_skin)
    # étendue Z exacte (boîte optimale) : `_segment` mesure le résultat, mais la découpe ne change pas l'étendue en Z
    zrange: tuple[float, float]
    mid: MidSurface
    bores: tuple[Bore, ...]
    filled: tuple[tuple[str, Passage], ...]  # passages rebouchés
    anchors: tuple[tuple[Polygon, ...], ...]  # par trou (même indice que `bores`) : profils (z, y) de son attache
    mesh: trimesh.Trimesh  # maillage de la peau orientée
    prepare_s: float  # durée de la préparation (s)
    key: tuple[str, str] = ("", "")
    bays: tuple[ServoBay, ...] = ()  # baies de servo (même calcul que generate_skin)
    mesh_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    anchor: object = None  # boîte de calage du motif de nervures (plusieurs corps d'aile), voir `skins_anchor`
    # plusieurs corps d'aile : une préparation par corps (même ordre que la génération) ; cette préparation-ci
    # en est l'ensemble (boîte globale, trous, baies et maillage réunis), et les vues sont calculées corps par corps
    parts: tuple["Prepared", ...] = ()
    tube_spars: tuple[tuple[str, Solid], ...] = ()  # SBAR transversales logées dans cette peau (orientées)
    cables: frozenset[str] = frozenset()
    # SBAR transversales par réglages (leurs fentes dépendent du plan de découpe) : calculées à la demande, sous
    # `mesh_lock`
    memo: dict = field(default_factory=dict, repr=False)

    @property
    def bounds(self) -> tuple[float, float, float, float, float, float]:
        b = self.bb
        return (b.min.X, b.min.Y, b.min.Z, b.max.X, b.max.Y, b.max.Z)

    def holes(self, z: float | None = None) -> list[dict]:
        """Trous présents (à la hauteur z, ou sur toute la pièce)."""
        if self.parts:
            return [h for part in self.parts if _at(part, z) for h in part.holes(z)]
        zmin, zmax = self.bb.min.Z, self.bb.max.Z
        out = []
        for b in self.bores:
            lo, hi = bore_extent(b, zmin, zmax)
            if z is None or lo <= z <= hi:
                kind = "gaine" if b.cable else "longeron" if b.cut_hole else "passage relié"
                out.append({"nom": b.name, "type": kind, "x": _round(b.x), "y": _round(b.y), "r": _round(b.radius)})
        for name, f in self.filled:
            if z is None or f.z0 <= z <= f.z1:
                out.append({"nom": name, "type": "passage rebouché", "x": _round(f.x), "y": _round(f.y), "r": _round(f.r)})
        return out

    def to_dict(self) -> dict:
        out = {
            "corps": self.name,
            "orientation": self.orientation.source,
            "boite": [_round(v) for v in self.bounds],
            "trous": self.holes(),
            "preparation_s": round(self.prepare_s, 2),
        }
        if self.parts:
            out["morceaux"] = [{"nom": p.name, "boite": [_round(v) for v in p.bounds]} for p in self.parts]
        return out


def _at(part: Prepared, z: float | None) -> bool:
    """Corps présent à la hauteur z (tous si z est None)."""
    return z is None or part.zrange[0] <= z <= part.zrange[1]


_PREP_FIELDS = ("naming", "roles", "orientation", "split", "spars", "passages", "servo")


def prepare_key(cfg: Config) -> str:
    """JSON canonique des réglages qui influencent la préparation."""
    data = cfg.model_dump(mode="json", include=set(_PREP_FIELDS))
    data["split"] = {"grid": data["split"]["grid"]}
    data["tessellation_tol"] = cfg.validation.tessellation_tol
    # matière : manchon des gaines (largeur de ligne), rampes des baies de servo (surplomb max), calage des ponts
    # de renfort sur les couches (hauteur de couche) ; épaisseur des nervures : peau d'ouverture `y_skin` des baies
    data["preset"] = {
        "line_width": cfg.preset.line_width,
        "max_overhang_deg": cfg.preset.max_overhang_deg,
        "layer_height": cfg.preset.layer_height,
    }
    data["slit_width"] = cfg.slit_width
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


_cache: OrderedDict[tuple[str, str], Prepared] = OrderedDict()
_pending: dict[tuple[str, str], Future] = {}  # préparations en cours : les appelants suivants les attendent
_hashes: OrderedDict[tuple[str, int, int], str] = OrderedDict()
_lock = threading.Lock()


def clear_cache() -> None:
    with _lock:
        _cache.clear()
        _hashes.clear()


def _file_sha1(path: Path) -> str:
    try:
        st = path.stat()
    except OSError as exc:
        raise PreviewError(f"fichier introuvable ou illisible : {path}") from exc
    stamp = (str(path.resolve()), st.st_mtime_ns, st.st_size)
    with _lock:
        if stamp in _hashes:
            _hashes.move_to_end(stamp)
            return _hashes[stamp]
    h = hashlib.sha1()
    try:
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
    except OSError as exc:
        raise PreviewError(f"fichier illisible : {path} ({exc.strerror or exc})") from exc
    digest = h.hexdigest()
    with _lock:
        _hashes[stamp] = digest
        while len(_hashes) > CACHE_SIZE:
            _hashes.popitem(last=False)
    return digest


def cached(step_path: str | Path, cfg: Config) -> Prepared | None:
    """Préparation déjà en cache pour (fichier, réglages), sans rien calculer ni bloquer sur un calcul en cours."""
    key = (_file_sha1(Path(step_path)), prepare_key(cfg))
    with _lock:
        prep = _cache.get(key)
        if prep is not None:
            _cache.move_to_end(key)
        return prep


def prepare_status(step_path: str | Path, cfg: Config) -> tuple[Prepared, bool]:
    """(préparation, servie_par_le_cache). Servie par le cache aussi quand l'appel a attendu une préparation
    identique déjà en cours (un seul calcul par clé) ; un échec n'est pas mis en cache (l'appel suivant réessaie)."""
    path = Path(step_path)
    key = (_file_sha1(path), prepare_key(cfg))
    with _lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key], True
        fut = _pending.get(key)
        owner = fut is None
        if owner:
            fut = _pending[key] = Future()
    if not owner:
        return fut.result(), True
    try:
        prep = _prepare(path, cfg, key)
    except BaseException as exc:
        with _lock:
            _pending.pop(key, None)
        fut.set_exception(exc)
        raise
    with _lock:
        _cache[key] = prep
        _cache.move_to_end(key)
        while len(_cache) > CACHE_SIZE:
            _cache.popitem(last=False)
        _pending.pop(key, None)
    fut.set_result(prep)
    return prep, False


def prepare(step_path: str | Path, cfg: Config) -> Prepared:
    """Orientation, peau orientée, surface médiane, trous, maillage ; en cache LRU (fichier, réglages)."""
    return prepare_status(step_path, cfg)[0]


def _prepare(path: Path, cfg: Config, key: tuple[str, str]) -> Prepared:
    t0 = time.perf_counter()
    try:
        model = classify(read_step(path), cfg)
    except (StepReadError, ClassifyError) as exc:
        raise PreviewError(str(exc)) from exc
    skins = model.of("skin")
    try:
        o = orient(model, cfg)
    except GEOMETRY_ERRORS as exc:
        raise PreviewError(f"orientation impossible : {exc}") from exc
    spars = tuple((b.body.name, b.body.solid.moved(o.location)) for b in model.of("spar"))
    servos = [(b.body.name, b.body.solid.moved(o.location)) for b in model.of("servo")]
    cables = frozenset(c.body.name for c in model.cables)
    oriented = [s.body.solid.moved(o.location) for s in skins]
    several = len(skins) > 1
    anchor = skins_anchor(oriented)  # mêmes plans de nervures que `run_model`
    if several:
        for name, solid in servos:
            if not any(servos_for(skin, [(name, solid)], True) for skin in oriented):
                raise PreviewError(f"{name} : la boîte ne traverse aucun corps d'aile (elle est hors de la pièce)")
        try:
            check_spars_assigned(
                [(s.body.name, skin) for s, skin in zip(skins, oriented)], spars, cfg.validation.tessellation_tol, cfg
            )
        except PipelineError as exc:
            raise PreviewError(str(exc)) from exc
    parts = [
        _prepare_skin(s.body.name, skin, spars, servos_for(skin, servos, several), cables, o, cfg, key, anchor, t0)
        for s, skin in zip(skins, oriented)
    ]
    if not several:
        return replace(parts[0], prepare_s=time.perf_counter() - t0)
    lo, hi = min(p.zrange[0] for p in parts), max(p.zrange[1] for p in parts)
    return Prepared(
        " + ".join(p.name for p in parts), Compound([p.skin for p in parts]), spars, o, anchor, (lo, hi), parts[0].mid,
        tuple(b for p in parts for b in p.bores), tuple(f for p in parts for f in p.filled),
        tuple(a for p in parts for a in p.anchors), trimesh.util.concatenate([p.mesh for p in parts]),
        time.perf_counter() - t0, key, bays=tuple(b for p in parts for b in p.bays), anchor=anchor, parts=tuple(parts),
    )


def _prepare_skin(
    name: str, skin: Solid, spars, servos, cables, o: Orientation, cfg: Config, key, anchor, t0: float
) -> Prepared:
    """Préparation d'une peau orientée : mêmes étapes que `generate_skin`, sans les booléens."""
    tol = cfg.validation.tessellation_tol
    try:
        bb = skin.bounding_box()
        nx, nz = cfg.split.grid
        mid = MidSurface.from_skin(skin, nx, nz, tol)
        vertical, transversal = split_spars(list(spars), cfg)
        inside = spars_inside(skin, vertical, tol)
        found = find_passages(skin, cfg.passages.max_tilt_deg)
        bores = extend_to_existing_bores(spar_bores(inside, cfg, cables), skin, cfg, found)
        to_fill = passages_to_fill(skin, cfg, exclude=bores, found=found)
        bores += passage_bores(skin, cfg, exclude=bores, found=found)
        check_fill(skin, to_fill, tol)
        bays = tuple(servo_bays(servos, skin, mid, cfg, bores))
        anchors = tuple(tuple(anchor_profiles_2d(b, mid, bb)) if b.anchor_gap > 0 else () for b in bores)
        mesh = to_mesh(skin, tol)
        obb = skin.bounding_box(optimal=True)
    except (PipelineError, BoreError, ServoError) as exc:
        raise PreviewError(f"{name} : {exc}") from exc
    except GEOMETRY_ERRORS as exc:
        raise PreviewError(f"{name} : préparation impossible ({type(exc).__name__} : {exc})") from exc
    return Prepared(
        name, skin, tuple(spars), o, bb, (obb.min.Z, obb.max.Z), mid, tuple(bores), tuple(to_fill), anchors, mesh,
        time.perf_counter() - t0, key, bays=bays, anchor=anchor, tube_spars=tuple(transversal), cables=cables,
    )


_TUBE_MEMO = 4  # réglages gardés par préparation


def tubes(prep: Prepared, cfg: Config) -> tuple[Tube, ...]:
    """SBAR transversales d'une peau, exactement celles de `generate_skin` (même maillage, même plan de découpe)."""
    if not prep.tube_spars:
        return ()
    key = cfg.model_dump_json()
    with prep.mesh_lock:
        if key in prep.memo:
            return prep.memo[key]
        try:
            held, _ = skin_tubes(prep.skin, list(prep.tube_spars), cfg, prep.cables, prep.bores, prep.bays, prep.mesh)
        except BoreError as exc:
            raise PreviewError(f"{prep.name} : {exc}") from exc
        out = tuple(t for t, _ in held)
        prep.memo[key] = out
        while len(prep.memo) > _TUBE_MEMO:
            prep.memo.pop(next(iter(prep.memo)))
        return out


# --- plaques 2D ---------------------------------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class Plate2D:
    """Plaque dans son plan : x = c + s·d0, z = zmin + s·d1 ; `polygon` en (s, y)."""

    layer: int
    layer_name: str
    family: int  # ±1 : les deux réseaux du losange
    c: float
    d0: float
    d1: float
    zmin: float
    top: bool
    depth: str | float  # "full" ou mm
    slit: float
    polygon: Polygon
    body: int = 0  # indice du corps d'aile (plusieurs peaux), dans l'ordre de `Prepared.parts`

    def to_dict(self) -> dict:
        return {
            "corps": self.body,
            "couche": self.layer,
            "nom": self.layer_name,
            "famille": self.family,
            "c": _round(self.c),
            "d0": self.d0,
            "d1": self.d1,
            "zmin": _round(self.zmin),
            "haut": self.top,
            "profondeur": self.depth,
            "polygone": _poly_dict(self.polygon),
        }


def plates_2d(prep: Prepared, cfg: Config) -> list[Plate2D]:
    """Profils 2D de toutes les plaques, exactement ceux de `generate_skin` (mêmes zones, mêmes exclusions, même
    calage du motif) ; plusieurs corps d'aile : plaques de chaque corps, `body` = indice du corps."""
    if prep.parts:
        return [replace(p, body=k) for k, part in enumerate(prep.parts) for p in plates_2d(part, cfg)]
    bb = prep.bb
    out = []
    for i, layer in enumerate(cfg.rib_layers):
        opts = rib_options(cfg, bb, i, prep.bays, prep.anchor, tubes(prep, cfg))
        for p in diamond_profiles(bb, prep.mid, list(prep.bores), layer, **opts):
            out.append(
                Plate2D(i, layer.name, p.family, p.c, p.d0, p.d1, bb.min.Z, p.top, layer.depth, cfg.slit_width, p.polygon)
            )
    return out


def plate_footprint(p: Plate2D, z: float) -> Polygon | None:
    """Coupe exacte de la plaque solide (± slit/2 autour de son plan) par le plan horizontal z, en (x, y).

    Un point à la distance t du plan a s = s0 + t·d0/d1 et x = x0 + t/d1 : l'empreinte est le profil
    restreint à |s − s0| ≤ slit·|d0|/(2·d1), puis x = x0 + (s − s0)/d0.
    """
    s0 = (z - p.zmin) / p.d1
    h = p.slit * abs(p.d0) / (2 * p.d1)
    clip = p.polygon.intersection(box(s0 - h, -_FAR, s0 + h, _FAR))
    if clip.is_empty or clip.area <= 0:
        return None
    x0 = p.c + s0 * p.d0
    geom = affinity.affine_transform(clip, [1 / p.d0, 0, 0, 1, x0 - s0 / p.d0, 0])
    parts = _parts(geom)
    return unary_union(parts) if parts else None


# --- coupe à une hauteur ------------------------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class Footprint:
    layer: int
    family: int
    top: bool
    polygon: Polygon


@dataclass(frozen=True, eq=False)
class SectionView:
    """Couche approximée à la hauteur z (aperçu, pas la tranche réelle)."""

    z: float
    polygons: list[Polygon]
    skin: list[Polygon]  # tranche de la peau (rebouchée) avant découpe
    footprints: list[Footprint]
    defects: list[Issue]
    band: list[tuple[float, float]]  # surface médiane (x, y)
    band_width: float
    holes: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "z": self.z,
            "approximation": True,
            "polygones": [_poly_dict(p) for p in self.polygons],
            "peau": [_poly_dict(p) for p in self.skin],
            "empreintes": [
                {"couche": f.layer, "famille": f.family, "haut": f.top, "polygone": _poly_dict(f.polygon)}
                for f in self.footprints
            ],
            "defauts": [
                {
                    "check": i.check,
                    "gravite": i.severity,
                    "message": i.message,
                    "bounds": None if i.bounds is None else [_round(v) for v in i.bounds],
                }
                for i in self.defects
            ],
            "bande": [[_round(x), _round(y)] for x, y in self.band],
            "largeur_bande": self.band_width,
            "trous": self.holes,
        }


def _skin_slices(prep: Prepared, zs: list[float]) -> list[list[Polygon]]:
    with prep.mesh_lock:
        sections = prep.mesh.section_multiplane(plane_origin=[0, 0, 0], plane_normal=[0, 0, 1], heights=zs)
    return [list(s.polygons_full) if s is not None else [] for s in sections]


def _bore_tools(prep: Prepared, z: float, tubes: Sequence[Tube] = ()) -> list[Polygon]:
    """Trous, fentes, attaches et poches de servo à la hauteur z (outils de coupe coupés par le plan z)."""
    bb = prep.bb
    reach = (bb.max.Y - bb.min.Y) + 20
    tools: list[Polygon] = []
    for i, b in enumerate(prep.bores):
        lo, hi = bore_extent(b, bb.min.Z, bb.max.Z)
        if not lo <= z <= hi:
            continue
        if b.cut_hole:
            tools.append(Point(b.x, b.y).buffer(b.radius, quad_segs=32))
        y0, y1 = (b.y, b.y + reach) if b.side > 0 else (b.y - reach, b.y)
        tools.append(box(b.x - b.slit / 2, y0, b.x + b.slit / 2, y1))
        for prof in prep.anchors[i]:
            cut = prof.intersection(LineString([(z, -_FAR), (z, _FAR)]))
            if not cut.is_empty:
                tools.append(box(b.x - b.slit / 2, cut.bounds[1], b.x + b.slit / 2, cut.bounds[3]))
    for t in tubes:
        tools += t.section(z)
    for bay in prep.bays:
        pocket = pocket_section(bay, z)
        if pocket is not None:
            tools.append(pocket)
        tools += reinforce_section(bay, z)
    return tools


def _section(prep: Prepared, plates: list[Plate2D], cfg: Config, z: float, raw: list[Polygon]) -> SectionView:
    fills = [Point(f.x, f.y).buffer(fill_radius(f), quad_segs=32) for _, f in prep.filled if f.z0 <= z <= f.z1]
    skin = _parts(unary_union(raw + fills)) if fills else [p for p in raw if p.area > _MIN_AREA]
    skin_union = unary_union(skin)
    feet = []
    for p in plates:
        fp = plate_footprint(p, z)
        if fp is not None and fp.intersects(skin_union):
            feet.append(Footprint(p.layer, p.family, p.top, fp))
    held = tubes(prep, cfg)
    tools = [f.polygon for f in feet] + _bore_tools(prep, z, held)
    polys = _parts(skin_union.difference(unary_union(tools))) if tools else skin
    preset = cfg.preset
    closed = Layer(z, slicer_closing(polys, preset.min_slit_width))
    defects = check_connectivity(closed) + check_material_width(closed, preset.line_width)
    defects += check_narrow_slits(Layer(z, tuple(polys)), preset.min_slit_width)
    ym, th = prep.mid.at(prep.mid.xs, np.full_like(prep.mid.xs, z))
    band = [(float(x), float(y)) for x, y, t in zip(prep.mid.xs, ym, th) if t > 0]
    holes = prep.holes(z)
    for t in held:
        sec = t.hole.layer(z)
        if sec is not None:
            c = sec.centroid
            holes.append({"nom": t.name, "type": "SBAR transversale", "x": _round(c.x), "y": _round(c.y),
                          "r": _round(t.radius)})
    return SectionView(z, polys, skin, feet, defects, band, cfg.split.gap, holes)


def section(prep: Prepared, plates: list[Plate2D], cfg: Config, z: float) -> SectionView:
    """Couche approximée à z : peau − empreintes des plaques − trous/fentes/attaches, avec ses défauts.
    Plusieurs corps d'aile : réunion des couches de chaque corps présent à z (chacun avec ses plaques)."""
    if prep.parts:
        views = [
            section(part, [p for p in plates if p.body == k], cfg, z)
            for k, part in enumerate(prep.parts) if _at(part, float(z))
        ]
        return SectionView(
            float(z), [p for v in views for p in v.polygons], [p for v in views for p in v.skin],
            [f for v in views for f in v.footprints], [d for v in views for d in v.defects],
            sorted(pt for v in views for pt in v.band), cfg.split.gap, [h for v in views for h in v.holes],
        )
    (raw,) = _skin_slices(prep, [float(z)])
    return _section(prep, plates, cfg, float(z), raw)


# --- vue de dessus ------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class PlanSegment:
    x0: float
    z0: float
    x1: float
    z1: float
    layer: int
    family: int


@dataclass(frozen=True)
class PlanformView:
    segments: list[PlanSegment]
    wing: tuple[float, float, float, float]  # xmin, zmin, xmax, zmax
    cuts: list[float]  # altitudes absolues des coupes de tronçons
    warnings: list[str] = field(default_factory=list)
    # trous vus de dessus, repère orienté : {nom, type, x, r, manchon (rayon trou + manchon), z0, z1}
    bores: list[dict] = field(default_factory=list)
    # baies de servo vues de dessus : {nom, cote, x0, x1, z0, z1 (boîte utile), ouverture [z, z] (rampes
    # comprises), profondeur, renfort, renfort_partiel}
    bays: list[dict] = field(default_factory=list)
    # plusieurs corps d'aile : {nom, x0, z0, x1, z1} de chaque corps (vide pour une seule peau)
    bodies: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "corps": self.bodies,
            "segments": [
                [_round(s.x0), _round(s.z0), _round(s.x1), _round(s.z1), s.layer, s.family] for s in self.segments
            ],
            "aile": [_round(v) for v in self.wing],
            "coupes": [_round(c) for c in self.cuts],
            "avertissements": self.warnings,
            "trous": self.bores,
            "baies": self.bays,
        }


def _plan_bays(prep: Prepared) -> list[dict]:
    """Baies de servo vues de dessus : boîte utile et ouverture dans la peau (rampes comprises)."""
    out = []
    for b in prep.bays:
        lo, hi = b.opening
        out.append({
            "nom": b.name, "cote": b.side_name, "x0": _round(b.x0), "x1": _round(b.x1), "z0": _round(b.z0),
            "z1": _round(b.z1), "ouverture": [_round(lo), _round(hi)], "profondeur": _round(b.depth),
            "renfort": b.rf_offset > 0, "renfort_partiel": bool(b.rf_offset > 0 and b.rf_lost),
        })
    return out


def _plan_bores(prep: Prepared) -> list[dict]:
    """Longerons, passages reliés (étendue réelle `bore_extent`) et passages rebouchés, vus de dessus."""
    zmin, zmax = prep.bb.min.Z, prep.bb.max.Z
    out = []
    for b in prep.bores:
        lo, hi = bore_extent(b, zmin, zmax)
        lo, hi = max(lo, zmin), min(hi, zmax)  # débouchant : l'outil dépasse la pièce
        out.append({
            "nom": b.name, "type": "gaine" if b.cable else "longeron" if b.cut_hole else "passage relié", "x": _round(b.x),
            "r": _round(b.radius), "manchon": _round(b.radius + b.sleeve), "z0": _round(lo), "z1": _round(hi),
        })
    for name, f in prep.filled:
        out.append({
            "nom": name, "type": "passage rebouché", "x": _round(f.x), "r": _round(f.r), "manchon": _round(f.r),
            "z0": _round(max(f.z0, zmin)), "z1": _round(min(f.z1, zmax)),
        })
    return out


def _cuts(prep: Prepared, cfg: Config) -> tuple[list[float], list[str]]:
    """Coupes de tronçons (altitudes relatives à l'emplanture), comme la découpe du pipeline."""
    zlo, zhi = prep.zrange
    seg = cfg.segment
    if seg.mode == "off":
        return [], []
    (W, D, _), _ = _print_volume(cfg)
    sample = mesh_sections(prep.mesh)

    def sections(zs):
        with prep.mesh_lock:
            return sample([zlo + z for z in zs])

    try:
        obstacles = [*bore_obstacles(prep.bores, zlo, zhi), *bay_obstacles(prep.bays, zlo, cfg.servo_strip())]
        plan = plan_layout(
            zhi - zlo, usable_height(cfg), sections, (W, D), seg.margin, seg.cuts_at or None, chord=seg.chord,
            max_overhang_deg=cfg.preset.max_overhang_deg, obstacles=obstacles,
        )
        return list(plan.cuts), []
    except SegmentError as exc:
        return [], [f"découpe en tronçons impossible : {exc}"]


def planform(prep: Prepared, plates: list[Plate2D], cfg: Config | None = None) -> PlanformView:
    """Vue de dessus (x, z) : parties coupées de chaque plaque, contour de l'aile, trous, baies de servo, coupes de
    tronçons. Plusieurs corps d'aile : vues de chaque corps réunies (coupes et avertissements par corps), contour
    = boîte globale, `corps` = nom et étendue de chaque corps."""
    if prep.parts:
        views = [planform(part, [p for p in plates if p.body == k], cfg) for k, part in enumerate(prep.parts)]
        bb = prep.bb
        return PlanformView(
            [sg for v in views for sg in v.segments], (bb.min.X, prep.zrange[0], bb.max.X, prep.zrange[1]),
            [c for v in views for c in v.cuts],
            [f"{part.name} : {w}" for part, v in zip(prep.parts, views) for w in v.warnings],
            [b for v in views for b in v.bores], [b for v in views for b in v.bays],
            [{"nom": part.name, "x0": _round(v.wing[0]), "z0": _round(v.wing[1]), "x1": _round(v.wing[2]),
              "z1": _round(v.wing[3])} for part, v in zip(prep.parts, views)],
        )
    bb = prep.bb
    zmin, zmax = bb.min.Z, bb.max.Z
    segs = []
    for p in plates:
        lo, hi = p.polygon.bounds[0], p.polygon.bounds[2]
        lo, hi = max(lo, 0.0), min(hi, (zmax - zmin) / p.d1)
        if hi <= lo:
            continue
        segs.append(PlanSegment(p.c + lo * p.d0, zmin + lo * p.d1, p.c + hi * p.d0, zmin + hi * p.d1, p.layer, p.family))
    cuts, warns = _cuts(prep, cfg) if cfg is not None else ([], [])
    return PlanformView(segs, (bb.min.X, zmin, bb.max.X, zmax), [prep.zrange[0] + c for c in cuts], warns,
                        _plan_bores(prep), _plan_bays(prep))


# --- chiffres estimés ---------------------------------------------------------------------------------------


def estimates(prep: Prepared, plates: list[Plate2D], cfg: Config, *, cached: bool = False) -> dict:
    """Plaques (total et par couche, dans l'ordre de `rib_layers`), tronçons, trajet et masse (couches
    échantillonnées), durée estimée.

    `duree_s` = plaques × SECONDS_PER_PLATE + durée de préparation mesurée, sauf si `cached` (préparation
    servie par le cache, voir `prepare_status`) : elle n'est alors pas recomptée. `preparation_s` donne
    toujours la durée mesurée lors du calcul initial.

    Plusieurs corps d'aile : chiffres de chaque corps additionnés (chaque corps est imprimé à part : couches et
    tronçons de chacun), avertissements préfixés du nom du corps.
    """
    if prep.parts:
        per = [
            estimates(part, [p for p in plates if p.body == k], cfg, cached=True) for k, part in enumerate(prep.parts)
        ]
        n_plates = sum(e["plaques"] for e in per)
        troncons = None if any(e["troncons"] is None for e in per) else [t for e in per for t in e["troncons"]]
        return {
            "plaques": n_plates,
            "plaques_par_couche": [
                {**c, "plaques": sum(e["plaques_par_couche"][i]["plaques"] for e in per)}
                for i, c in enumerate(per[0]["plaques_par_couche"])
            ],
            "troncons": troncons,
            "couches": sum(e["couches"] for e in per),
            "trajet_m": sum(e["trajet_m"] for e in per),
            "masse_g": sum(e["masse_g"] for e in per),
            "duree_s": n_plates * SECONDS_PER_PLATE + (0.0 if cached else prep.prepare_s),
            "preparation_s": prep.prepare_s,
            "avertissements": [
                *prep.orientation.warnings,
                *(f"{part.name} : {w}" for part, e in zip(prep.parts, per) for w in e["avertissements"]),
            ],
        }
    preset = cfg.preset
    per_layer = [0] * len(cfg.rib_layers)
    for p in plates:
        per_layer[p.layer] += 1
    zlo, zhi = prep.zrange
    cuts, warns = _cuts(prep, cfg)
    bounds = [0.0, *cuts, zhi - zlo]
    troncons = [b - a for a, b in pairwise(bounds)] if not warns else None
    warns = [
        *wall_warnings(prep.bb, prep.mid, prep.bores, cfg, per_layer, prep.bays, prep.anchor, tubes(prep, cfg)),
        *reinforce_warnings(prep.bays), *warns,
    ]
    bb = prep.bb
    zs = layer_heights(bb.min.Z, bb.max.Z, preset.layer_height)
    n = len(zs)
    picked = sorted({round(i) for i in np.linspace(0, n - 1, min(SAMPLED_LAYERS, n))}) if n else []
    sample = [float(zs[i]) for i in picked]
    lengths = [
        sum(p.length for p in _section(prep, plates, cfg, z, raw).polygons)
        for z, raw in zip(sample, _skin_slices(prep, sample))
    ]
    path_mm = float(np.mean(lengths)) * n if lengths else 0.0
    mass_g = path_mm * preset.line_width * preset.layer_height * preset.density / 1000.0
    return {
        "plaques": len(plates),
        "plaques_par_couche": [
            {"indice": i, "nom": layer.name, "plaques": n} for i, (layer, n) in enumerate(zip(cfg.rib_layers, per_layer))
        ],
        "troncons": troncons,
        "couches": n,
        "trajet_m": path_mm / 1000.0,
        "masse_g": mass_g,
        "duree_s": len(plates) * SECONDS_PER_PLATE + (0.0 if cached else prep.prepare_s),
        "preparation_s": prep.prepare_s,
        "avertissements": warns,
    }


# --- maillages 3D -------------------------------------------------------------------------------------------


def _stl(mesh: trimesh.Trimesh) -> bytes:
    return mesh.export(file_type="stl")


def plates_mesh(prep: Prepared, plates: list[Plate2D]) -> bytes:
    """STL binaire des plaques solides (outils de coupe), pour la vue 3D."""
    if len(plates) > MAX_MESH_PLATES:
        raise PreviewError(
            f"{len(plates)} plaques : vue 3D limitée à {MAX_MESH_PLATES} plaques (augmenter l'espacement des nervures)"
        )
    if not plates:
        return _stl(trimesh.Trimesh())
    solids = [plate_solid(p.polygon, p.c, p.d0, p.d1, p.zmin, p.slit) for p in plates]
    return _stl(to_mesh(Compound(solids), _PLATES_TOL))


def skin_mesh(prep: Prepared) -> bytes:
    """STL binaire de la peau orientée (avant découpe)."""
    with prep.mesh_lock:
        return _stl(prep.mesh)
