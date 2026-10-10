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


def bore_zones_2d(b: Bore, c: float, d0: float, d1: float, zmin: float, zmax: float) -> list[Polygon]:
    """Zones sans nervure d'un trou dans le repère (s, y) de la plaque (x = c + s·d0, z = zmin + s·d1)."""
    r = b.radius + b.sleeve
    s0 = (b.x - c) / d0
    ellipse = affinity.scale(Point(s0, b.y).buffer(1.0, 32), r / abs(d0), r)
    half = (b.strip / 2) / abs(d0)
    band = box(s0 - half, min(b.y, b.y + _FAR * b.side), s0 + half, max(b.y, b.y + _FAR * b.side))
    # limitées à l'étendue du trou le long de Z (même marge que les outils de coupe)
    lo, hi = bore_extent(b, zmin, zmax)
    extent = box((lo - zmin) / d1, -_FAR, (hi - zmin) / d1, _FAR)
    return [z for z in (ellipse.intersection(extent), band.intersection(extent)) if not z.is_empty]


def plate_profiles_2d(ss, ym, th, zones, *, gap, min_depth, ytop, ybot) -> list[tuple[bool, Polygon]]:
    deep = th > gap + 2 * min_depth
    if not deep.any():
        return []
    out = []
    for top in (True, False):
        far = ytop if top else ybot
        edge = np.where(deep, ym + (gap / 2 if top else -gap / 2), far)
        poly = Polygon(list(zip(ss, edge)) + [(ss[-1], far), (ss[0], far)]).buffer(0)
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
    ss = np.linspace(-5, length, layer.samples)
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
            zones = [z for b in bores for z in bore_zones_2d(b, c, d0, d1, zmin, zmax)]
            for _, poly in plate_profiles_2d(ss, ym, th, zones, gap=gap, min_depth=min_depth, ytop=ytop, ybot=ybot):
                plates.append(plate_solid(poly, c, d0, d1, zmin, slit))
    return plates
