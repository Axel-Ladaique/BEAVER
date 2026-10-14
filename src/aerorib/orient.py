"""Orientation simple (addendum P1 §1) : axe de construction, emplanture en z = 0, corde X, extrados +Y."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from build123d import Compound, Location, Plane, Shape, Solid
from shapely.geometry import Polygon
from shapely.ops import unary_union

from aerorib.classify import Model
from aerorib.config import Config, OrientationAxis
from aerorib.midsurface import camber_offset
from aerorib.slicing import slice_layers, to_mesh


@dataclass(frozen=True)
class Orientation:
    location: Location
    axis: tuple[float, float, float]
    source: str
    warnings: tuple[str, ...] = ()  # ex. sections des corps d'aile non réunies à l'emplanture


def choose_axis(model: Model, cfg: Config) -> tuple[np.ndarray, str]:
    if isinstance(cfg.orientation, OrientationAxis) and cfg.orientation.axis is not None:
        v = np.asarray(cfg.orientation.axis, dtype=float)
        return v / np.linalg.norm(v), "axe forcé"
    spars = model.of("spar")
    if spars:
        return np.asarray(spars[0].spar.axis_dir, dtype=float), f"longeron {spars[0].body.name}"
    boxes = [s.body.solid.bounding_box() for s in model.of("skin")]
    lo = np.min([[b.min.X, b.min.Y, b.min.Z] for b in boxes], axis=0)
    hi = np.max([[b.max.X, b.max.Y, b.max.Z] for b in boxes], axis=0)
    axis = np.zeros(3)
    axis[int(np.argmax(hi - lo))] = 1.0
    return axis, "plus grande dimension"


def _section_area(mesh, axis: np.ndarray, t: float) -> tuple[float, np.ndarray | None]:
    section = mesh.section(plane_origin=axis * t, plane_normal=axis)
    if section is None:
        return 0.0, None
    planar, _ = section.to_2D()
    return float(sum(p.area for p in planar.polygons_full)), np.asarray(section.vertices)


_JOIN = 0.05  # mm : jeu comblé entre les sections de deux corps d'aile jointifs (maillages non conformes)


def _root_polygons(skin_moved: Shape, inset: float, tol: float, merge: bool = False) -> list[Polygon]:
    """Section d'emplanture (z = inset). `merge` (plusieurs corps d'aile) : sections jointives réunies, à `_JOIN`
    près (un jeu plus large entre deux corps laisse deux sections)."""
    (root,) = slice_layers(skin_moved, [inset], tol)
    polys = list(root.polygons)
    if merge and len(polys) > 1:
        joined = unary_union([Polygon(p.exterior).buffer(_JOIN) for p in polys]).buffer(-_JOIN)
        polys = [g for g in getattr(joined, "geoms", [joined]) if isinstance(g, Polygon) and not g.is_empty]
    return polys


def _needs_flip(skin_moved: Shape, inset: float, tol: float, merge: bool = False) -> bool:
    """Extrados en −Y à l'emplanture. `merge` (plusieurs corps d'aile) : les sections jointives des corps sont
    d'abord réunies — un corps coupé dans la corde n'a pas, seul, la cambrure du profil ; si elles ne se réunissent
    pas (jeu entre les corps), la plus grande décide (voir l'avertissement de `orient`)."""
    polys = _root_polygons(skin_moved, inset, tol, merge)
    return bool(polys) and camber_offset(max(polys, key=lambda p: p.area)) < 0


CABLE_TOUCH_TOL = 0.5  # mm : une gaine à moins de cette distance d'un bout de l'envergure « touche » ce bout


def _span_range(solid: Solid, axis: np.ndarray) -> tuple[float, float]:
    proj = np.array([[v.X, v.Y, v.Z] for v in solid.vertices()], dtype=float) @ axis
    return float(proj.min()), float(proj.max())


def choose_root(model: Model, cfg: Config, axis: np.ndarray) -> tuple[bool | None, str]:
    """(`root_is_lo` imposé ou None = règle des sections, complément de `source`)."""
    mode = cfg.orientation.root if isinstance(cfg.orientation, OrientationAxis) else "auto"
    if mode in ("debut", "fin"):
        # debut = coordonnée min. du repère d'origine le long de l'axe, quel que soit son sens (axe imposé négatif,
        # sens arbitraire du cylindre détecté) : sens fixé par la composante dominante de l'axe
        a = np.asarray(axis, dtype=float)
        positive = bool(a[int(np.argmax(np.abs(a)))] > 0)
        return (mode == "debut") == positive, f"emplanture imposée ({mode})"
    cables = model.cables
    if not cables:
        return None, ""
    axis = np.asarray(axis, dtype=float) / np.linalg.norm(axis)
    ranges = [_span_range(s.body.solid, axis) for s in model.of("skin")]  # envergure de l'ensemble des peaux
    lo, hi = min(r[0] for r in ranges), max(r[1] for r in ranges)
    at_lo: list[str] = []
    at_hi: list[str] = []
    for c in cables:
        c_lo, c_hi = _span_range(c.body.solid, axis)
        if c_lo <= lo + CABLE_TOUCH_TOL:
            at_lo.append(c.body.name)
        if c_hi >= hi - CABLE_TOUCH_TOL:
            at_hi.append(c.body.name)
    if bool(at_lo) == bool(at_hi):  # aucune gaine à un bout, ou aux deux : règle actuelle
        return None, ""
    return bool(at_lo), f"emplanture côté gaine {(at_lo or at_hi)[0]}"


def orientation_location(
    skin: Shape, axis: np.ndarray, tol: float = 0.02, root_is_lo: bool | None = None, merge: bool = False
) -> Location:
    """Repère orienté de `skin` (une peau, ou un composé de plusieurs corps d'aile avec `merge`)."""
    axis = np.asarray(axis, dtype=float)
    axis = axis / np.linalg.norm(axis)
    mesh = to_mesh(skin, tol)
    proj = mesh.vertices @ axis
    lo, hi = float(proj.min()), float(proj.max())
    inset = min(0.5, (hi - lo) / 4)
    a_lo, v_lo = _section_area(mesh, axis, lo + inset)
    a_hi, v_hi = _section_area(mesh, axis, hi - inset)
    if root_is_lo is None:
        root_is_lo = a_lo >= a_hi * 0.99
    z_dir = axis if root_is_lo else -axis
    root_t = lo if root_is_lo else hi
    verts = v_lo if root_is_lo else v_hi
    if verts is None:
        raise ValueError("section d'emplanture introuvable")
    flat = verts - np.outer(verts @ axis, axis)
    centre = flat.mean(axis=0)
    te = flat[np.argmax(np.linalg.norm(flat - centre, axis=1))]
    le = flat[np.argmax(np.linalg.norm(flat - te, axis=1))]
    chord = te - le
    chord = chord - chord.dot(z_dir) * z_dir
    norm = float(np.linalg.norm(chord))
    if norm < 1e-9:
        raise ValueError("corde de longueur nulle : section d'emplanture dégénérée (vérifier l'axe d'orientation)")
    chord /= norm
    origin = centre + axis * root_t
    loc = Location(Plane(origin=tuple(origin), x_dir=tuple(chord), z_dir=tuple(z_dir))).inverse()
    if _needs_flip(skin.moved(loc), inset, tol, merge):
        # Convention : une rotation propre ne peut pas inverser Y seul ; la corde reste sur X
        # mais le BA passe en +X quand la pièce a dû être tournée pour mettre l'extrados en +Y.
        loc = Location((0, 0, 0), (0, 0, 180)) * loc
    return loc


def orient(model: Model, cfg: Config) -> Orientation:
    skins = model.of("skin")
    if not skins:
        raise ValueError("aucune peau à orienter")
    axis, source = choose_axis(model, cfg)
    # plusieurs corps d'aile (découpés dans la CAO) : un seul repère pour l'ensemble, calculé sur le composé des
    # peaux — emplanture = bout de l'envergure totale, corde et extrados lus sur la section réunie des corps
    multi = len(skins) > 1
    skin = Compound([s.body.solid for s in skins]) if multi else skins[0].body.solid
    root_is_lo, why = choose_root(model, cfg, axis)
    loc = orientation_location(skin, axis, cfg.validation.tessellation_tol, root_is_lo, merge=multi)
    if why:
        source = f"{source} ; {why}"
    warnings: tuple[str, ...] = ()
    if multi:
        moved = skin.moved(loc)
        bb = moved.bounding_box()
        polys = _root_polygons(moved, min(0.5, (bb.max.Z - bb.min.Z) / 4), cfg.validation.tessellation_tol, True)
        if len(polys) > 1:
            warnings = (
                f"orientation : les sections des corps d'aile ne se rejoignent pas à l'emplanture ({len(polys)} "
                f"contours séparés, jeu > {2 * _JOIN:g} mm entre les corps ?) — extrados choisi sur le plus grand "
                "corps, vérifier l'orientation et le jeu entre les corps dans la CAO",
            )
    return Orientation(loc, tuple(float(c) for c in axis), source, warnings)
