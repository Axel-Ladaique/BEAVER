"""Nervures en losange construites en 2D dans le plan de chaque plaque (addendum P1 §3)."""

from __future__ import annotations

import math

import numpy as np
from build123d import Face, Solid, Vector, Wire, extrude
from shapely import affinity
from shapely.geometry import Point, Polygon, box
from shapely.ops import unary_union

from aerorib.config import RibLayer
from aerorib.features.bores import Bore, bore_extent
from aerorib.midsurface import MidSurface

_FAR = 1e4
_DEPTH_STEP = 0.5  # mm, pas d'échantillonnage de l'épaisseur le long d'une plaque


_ZONE_SEGMENTS = 64
# polygone circonscrit au cercle unité : aucun point du cercle n'est hors de la zone
_CIRCUMSCRIBED = 1 / math.cos(math.pi / _ZONE_SEGMENTS)


def bore_zones_2d(
    b: Bore, c: float, d0: float, d1: float, zmin: float, zmax: float, *, slit: float
) -> list[Polygon]:
    """Zones sans nervure d'un trou dans le repère (s, y) de la plaque (x = c + s·d0, z = zmin + s·d1).

    Agrandies de la demi-épaisseur de plaque (slit/2 en y, slit/(2·|d0|) en s) : c'est la plaque
    réelle, pas son plan médian, qui doit rester hors du manchon.
    """
    pad_s = slit / (2 * abs(d0))
    r = b.radius + b.sleeve + slit / 2
    s0 = (b.x - c) / d0
    unit = Point(s0, b.y).buffer(_CIRCUMSCRIBED, quad_segs=_ZONE_SEGMENTS // 4)
    ellipse = affinity.scale(unit, r / abs(d0), r, origin=(s0, b.y))
    half = (b.strip / 2) / abs(d0) + pad_s
    y_lo, y_hi = sorted((b.y, b.y + _FAR * b.side))
    band = box(s0 - half, y_lo, s0 + half, y_hi)
    # limitées à l'étendue du trou le long de Z (même marge que les outils de coupe)
    lo, hi = bore_extent(b, zmin, zmax)
    extent = box((lo - zmin) / d1 - pad_s, -_FAR, (hi - zmin) / d1 + pad_s, _FAR)
    return [z for z in (ellipse.intersection(extent), band.intersection(extent)) if not z.is_empty]


def _deep_runs(ss: np.ndarray, deep: np.ndarray) -> list[tuple[float, float]]:
    """Intervalles de s où la pièce est assez épaisse ; bornes au milieu entre deux échantillons."""
    runs = []
    idx = np.flatnonzero(np.diff(np.concatenate([[0], deep.astype(int), [0]])))
    for a, b in zip(idx[::2], idx[1::2]):  # échantillons a … b−1 profonds
        lo = ss[0] if a == 0 else (ss[a - 1] + ss[a]) / 2
        hi = ss[-1] if b == len(ss) else (ss[b - 1] + ss[b]) / 2
        runs.append((float(lo), float(hi)))
    return runs


def plate_profiles_2d(
    ss, ym, th, zones, *, gap, min_depth, ytop, ybot, edge_vertices: int | None = None
) -> list[tuple[bool, Polygon]]:
    """Profils 2D des plaques haute et basse.

    ss/ym/th : échantillons fins le long de la plaque (pour situer les transitions d'épaisseur) ;
    le bord le long de la surface médiane n'utilise que `edge_vertices` sommets (coût des booléens).
    Une transition profonde / trop mince est une marche verticale, jamais une rampe : en tranche, une
    rampe donne des pointes de fente obliques qui pincent la bande de séparation.
    """
    ss, ym, th = (np.asarray(a, dtype=float) for a in (ss, ym, th))
    deep = th > gap + 2 * min_depth
    runs = _deep_runs(ss, deep)
    if not runs:
        return []
    coarse = ss if edge_vertices is None else np.linspace(ss[0], ss[-1], edge_vertices)
    out = []
    for top in (True, False):
        far = ytop if top else ybot
        off = gap / 2 if top else -gap / 2
        pieces = []
        for lo, hi in runs:
            s_edge = np.concatenate([[lo], coarse[(coarse > lo) & (coarse < hi)], [hi]])
            y_edge = np.interp(s_edge, ss, ym) + off
            pieces.append(Polygon(list(zip(s_edge, y_edge)) + [(hi, far), (lo, far)]).buffer(0))
        poly = unary_union(pieces)
        if zones:
            shift = -_FAR if top else _FAR
            # ombre côté surface médiane : un fragment coincé entre la zone et la bande ne toucherait pas la peau
            shadow = unary_union([z.union(affinity.translate(z, yoff=shift)).convex_hull for z in zones])
            poly = poly.difference(shadow)
        for part in getattr(poly, "geoms", [poly]):
            if isinstance(part, Polygon) and not part.is_empty and part.area > 1e-3:
                out.append((top, part.simplify(0.01)))
    return out


def plate_solid(poly: Polygon, c: float, d0: float, d1: float, zmin: float, slit: float) -> Solid:
    def to3(s: float, y: float) -> Vector:
        return Vector(c + s * d0, y, zmin + s * d1)

    def ring(coords) -> Wire:
        return Wire.make_polygon([to3(*p) for p in list(coords)[:-1]], close=True)

    face = Face(ring(poly.exterior.coords), [ring(r.coords) for r in poly.interiors])
    return extrude(face, amount=slit / 2, dir=Vector(d1, 0, -d0), both=True)


def diamond_plates(
    bb, mid: MidSurface, bores: list[Bore], layer: RibLayer, *, angle_deg: float, gap: float, slit: float,
    min_depth: float,
) -> list[Solid]:
    ang = math.radians(angle_deg)
    zmin, zmax = bb.min.Z, bb.max.Z
    ytop, ybot = bb.max.Y + 5, bb.min.Y - 5
    length = (zmax - zmin) / math.cos(ang) + 10
    # échantillonnage fin (≈ 0,5 mm) pour situer les transitions d'épaisseur ; bord à `samples` sommets
    ss = np.linspace(-5, length, max(layer.samples, math.ceil((length + 5) / _DEPTH_STEP) + 1))
    plates: list[Solid] = []
    for sign in (1, -1):
        d0, d1 = sign * math.sin(ang), math.cos(ang)
        reach = length * abs(d0)
        lo = bb.min.X - (reach if sign > 0 else 0) - layer.spacing
        hi = bb.max.X + (reach if sign < 0 else 0) + layer.spacing
        origin = bb.min.X + layer.phase
        for k in range(math.floor((lo - origin) / layer.spacing), math.ceil((hi - origin) / layer.spacing) + 1):
            c = origin + k * layer.spacing
            ym, th = mid.at(c + ss * d0, zmin + ss * d1)
            zones = [z for b in bores for z in bore_zones_2d(b, c, d0, d1, zmin, zmax, slit=slit)]
            for _, poly in plate_profiles_2d(
                ss, ym, th, zones, gap=gap, min_depth=min_depth, ytop=ytop, ybot=ybot, edge_vertices=layer.samples
            ):
                plates.append(plate_solid(poly, c, d0, d1, zmin, slit))
    return plates
