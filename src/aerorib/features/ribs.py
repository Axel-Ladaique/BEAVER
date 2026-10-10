"""Nervures en losange construites en 2D dans le plan de chaque plaque (addendum P1 §3)."""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np
from build123d import Face, Solid, Vector, Wire, extrude
from shapely import affinity
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import unary_union

from aerorib.config import RibLayer
from aerorib.features.bores import Bore, bore_extent
from aerorib.midsurface import MidSurface

_FAR = 1e4
_DEPTH_STEP = 0.5  # mm, pas d'échantillonnage de l'épaisseur le long d'une plaque
# mm : écart max entre le bord intérieur d'une plaque peu profonde (depth en mm) et la peau décalée. Le bord
# est échantillonné finement puis simplifié (Douglas-Peucker) : peu de sommets là où la peau est peu courbée,
# beaucoup au bord d'attaque. À 16 sommets réguliers, la corde creusait jusqu'à ~0,9 mm de trop au bord
# d'attaque ; à un sommet tous les 5 mm, la coupe coûtait ×2,7.
_PARTIAL_EDGE_TOL = 0.05
_SAME_LINE = 1e-6  # mm : deux plaques de même famille et même c sont confondues


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
    # côté fente seulement ; des deux côtés si le longeron a une attache côté opposé
    y_lo, y_hi = (b.y - _FAR, b.y + _FAR) if b.anchor_gap > 0 else sorted((b.y, b.y + _FAR * b.side))
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
    ss, ym, th, zones, *, gap, min_depth, ytop, ybot, edge_vertices: int | None = None,
    depth: float | None = None,
) -> list[tuple[bool, Polygon]]:
    """Profils 2D des plaques haute et basse.

    ss/ym/th : échantillons fins le long de la plaque (pour situer les transitions d'épaisseur) ;
    le bord intérieur n'utilise que `edge_vertices` sommets (coût des booléens) ; pour un niveau peu
    profond (`depth`), il est simplifié à `_PARTIAL_EDGE_TOL` près au lieu d'être échantillonné régulièrement.
    Une transition profonde / trop mince est une marche verticale, jamais une rampe : en tranche, une
    rampe donne des pointes de fente obliques qui pincent la bande de séparation.

    `depth` (mm, None = full) : la plaque ne descend qu'à `depth` sous la peau (y_peau = ym ± th/2),
    sans jamais dépasser la bande de séparation. Profondeur mesurée selon Y (épaisseur), pas selon la
    normale à la peau : écart en 1/cos(pente de la peau), négligeable sur un profil d'aile.
    """
    ss, ym, th = (np.asarray(a, dtype=float) for a in (ss, ym, th))
    deep = th > gap + 2 * min_depth
    runs = _deep_runs(ss, deep)
    if not runs:
        return []
    simplify = depth is not None and edge_vertices is not None
    coarse = ss if edge_vertices is None or simplify else np.linspace(ss[0], ss[-1], edge_vertices)
    out = []
    for top in (True, False):
        far = ytop if top else ybot
        if top:
            inner = ym + (gap / 2 if depth is None else np.maximum(gap / 2, th / 2 - depth))
        else:
            inner = ym - (gap / 2 if depth is None else np.maximum(gap / 2, th / 2 - depth))
        pieces = []
        for lo, hi in runs:
            s_edge = np.concatenate([[lo], coarse[(coarse > lo) & (coarse < hi)], [hi]])
            if depth is None:
                y_edge = np.interp(s_edge, ss, inner)
            else:
                # bord lu sur les seuls échantillons profonds du tronçon : aux bornes (entre deux échantillons),
                # pas de mélange avec l'épaisseur d'un échantillon trop mince (marche, pas de rampe)
                run = deep & (ss >= lo) & (ss <= hi)
                y_edge = np.interp(s_edge, ss[run], inner[run])
                if simplify and len(s_edge) > 2:
                    edge = LineString(list(zip(s_edge, y_edge))).simplify(_PARTIAL_EDGE_TOL)
                    s_edge, y_edge = (np.asarray(a) for a in zip(*edge.coords))
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


def _plate_length(bb, angle_deg: float) -> float:
    return (bb.max.Z - bb.min.Z) / math.cos(math.radians(angle_deg)) + 10


def plate_lines(bb, layer: RibLayer, angle_deg: float) -> list[tuple[int, float]]:
    """Plans du motif losange : (famille ±1, abscisse c de la plaque en z = zmin)."""
    ang = math.radians(angle_deg)
    reach = _plate_length(bb, angle_deg) * math.sin(ang)
    origin = bb.min.X + layer.phase
    out = []
    for sign in (1, -1):
        lo = bb.min.X - (reach if sign > 0 else 0) - layer.spacing
        hi = bb.max.X + (reach if sign < 0 else 0) + layer.spacing
        for k in range(math.floor((lo - origin) / layer.spacing), math.ceil((hi - origin) / layer.spacing) + 1):
            out.append((sign, origin + k * layer.spacing))
    return out


def diamond_plates(
    bb, mid: MidSurface, bores: list[Bore], layer: RibLayer, *, angle_deg: float, gap: float, slit: float,
    min_depth: float, exclude: Sequence[tuple[int, float]] = (),
) -> list[Solid]:
    """Plaques d'un niveau ; `exclude` : plans (famille, c) déjà coupés par un niveau plus profond."""
    ang = math.radians(angle_deg)
    zmin, zmax = bb.min.Z, bb.max.Z
    ytop, ybot = bb.max.Y + 5, bb.min.Y - 5
    length = _plate_length(bb, angle_deg)
    # échantillonnage fin (≈ 0,5 mm) pour situer les transitions d'épaisseur ; bord à `samples` sommets
    ss = np.linspace(-5, length, max(layer.samples, math.ceil((length + 5) / _DEPTH_STEP) + 1))
    plates: list[Solid] = []
    for sign, c in plate_lines(bb, layer, angle_deg):
        if any(es == sign and abs(ec - c) < _SAME_LINE for es, ec in exclude):
            continue
        d0, d1 = sign * math.sin(ang), math.cos(ang)
        ym, th = mid.at(c + ss * d0, zmin + ss * d1)
        zones = [z for b in bores for z in bore_zones_2d(b, c, d0, d1, zmin, zmax, slit=slit)]
        for _, poly in plate_profiles_2d(
            ss, ym, th, zones, gap=gap, min_depth=min_depth, ytop=ytop, ybot=ybot, edge_vertices=layer.samples,
            depth=None if layer.depth == "full" else layer.depth,
        ):
            plates.append(plate_solid(poly, c, d0, d1, zmin, slit))
    return plates
