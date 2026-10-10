"""Orientation simple (addendum P1 §1) : axe de construction, emplanture en z = 0, corde X, extrados +Y."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from build123d import Location, Plane, Solid

from aerorib.classify import Model
from aerorib.config import Config, OrientationAxis
from aerorib.midsurface import camber_offset
from aerorib.slicing import slice_layers, to_mesh


@dataclass(frozen=True)
class Orientation:
    location: Location
    axis: tuple[float, float, float]
    source: str


def choose_axis(model: Model, cfg: Config) -> tuple[np.ndarray, str]:
    if isinstance(cfg.orientation, OrientationAxis):
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


def _needs_flip(skin_moved: Solid, inset: float, tol: float) -> bool:
    (root,) = slice_layers(skin_moved, [inset], tol)
    return bool(root.polygons) and camber_offset(max(root.polygons, key=lambda p: p.area)) < 0


def orientation_location(skin: Solid, axis: np.ndarray, tol: float = 0.02) -> Location:
    axis = np.asarray(axis, dtype=float)
    axis = axis / np.linalg.norm(axis)
    mesh = to_mesh(skin, tol)
    proj = mesh.vertices @ axis
    lo, hi = float(proj.min()), float(proj.max())
    inset = min(0.5, (hi - lo) / 4)
    a_lo, v_lo = _section_area(mesh, axis, lo + inset)
    a_hi, v_hi = _section_area(mesh, axis, hi - inset)
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
    chord /= np.linalg.norm(chord)
    origin = centre + axis * root_t
    loc = Location(Plane(origin=tuple(origin), x_dir=tuple(chord), z_dir=tuple(z_dir))).inverse()
    if _needs_flip(skin.moved(loc), inset, tol):
        # Convention : une rotation propre ne peut pas inverser Y seul ; la corde reste sur X
        # mais le BA passe en +X quand la pièce a dû être tournée pour mettre l'extrados en +Y.
        loc = Location((0, 0, 0), (0, 0, 180)) * loc
    return loc


def orient(model: Model, cfg: Config) -> Orientation:
    skins = model.of("skin")
    if not skins:
        raise ValueError("aucune peau à orienter")
    axis, source = choose_axis(model, cfg)
    skin = skins[0].body.solid
    loc = orientation_location(skin, axis, cfg.validation.tessellation_tol)
    return Orientation(loc, tuple(float(c) for c in axis), source)
