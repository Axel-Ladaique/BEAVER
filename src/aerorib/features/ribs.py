"""Nervures en losange construites en 2D dans le plan de chaque plaque (addendum P1 §3)."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from build123d import Face, Solid, Vector, Wire, extrude
from shapely import affinity
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import unary_union

from aerorib.config import RibLayer
from aerorib.features.bores import Bore, bore_extent
from aerorib.features.servo import ServoBay, pocket_zones_2d
from aerorib.midsurface import MidSurface

_FAR = 1e4
_DEPTH_STEP = 0.5  # mm, pas d'échantillonnage de l'épaisseur le long d'une plaque
# mm : écart max entre le bord intérieur d'une plaque peu profonde (depth en mm) et la peau décalée. Le bord
# est échantillonné finement puis simplifié (Douglas-Peucker) : peu de sommets là où la peau est peu courbée,
# beaucoup au bord d'attaque. À 16 sommets réguliers, la corde creusait jusqu'à ~0,9 mm de trop au bord
# d'attaque ; à un sommet tous les 5 mm, la coupe coûtait ×2,7.
_PARTIAL_EDGE_TOL = 0.05
_MIN_BAND_TOL = 0.005  # mm : plancher de band_tol (gap = largeur de ligne) — sinon un sommet par échantillon
# sommets ajoutés au bord intérieur d'un tronçon pour suivre la bande (coût des booléens) ; au-delà, la tolérance
# est relâchée (doublée) jusqu'à tenir sous ce plafond
_MAX_BAND_VERTICES = 64
_SAME_LINE = 1e-6  # mm : deux plaques de même famille et même c sont confondues
# mm : écart minimal entre deux sommets du bord intérieur d'une plaque. Deux sommets plus proches (sommet de la
# grille régulière tombé à côté d'un sommet de bande) ne donnent qu'une arête microscopique, que le booléen
# reporte dans la pièce et que le maillage rend invalide.
_MIN_VERTEX_GAP = 0.05
# Bouts de la pièce (plans z = zmin et z = zmax). Le motif losange y fait se croiser deux plaques de familles
# opposées (toujours en zmin, en zmax dès que l'envergure est un multiple de la demi-période) : leurs bandes de
# séparation y coupent le plan de bout presque au même endroit, d'où des arêtes de 10 nm à 50 µm sur la face de
# bout. On fait coïncider exactement ces traces : sur une fenêtre ±h autour du bout, le bord intérieur n'a que
# trois sommets (bout − h, bout, bout + h), lus exactement sur la surface médiane, qui ne dépend plus de z au-delà
# de ses rangées extrêmes (`MidSurface.at` borne z) ; fenêtre = cette part de la marge entre bout et rangée.
_END_CLAMP_SHARE = 0.8


# Parois raides (bords d'attaque / de fuite épais, profil « boîte arrondie »). En tranche, une fente est une bande
# de largeur w = slit / cos(angle) en x qui traverse la peau ; là où la peau a une pente y'(x), la bouche ouverte
# mesure ≈ w·√(1 + y'²) ≤ max_mouth (validation), soit |y'| ≤ √((max_mouth/w)² − 1). La marge _SLOPE_SAFETY porte
# sur cette pente seulement (lue sur la surface médiane interpolée, grille `split.grid`, plus douce que les parois
# réelles), jamais sur le budget de bouche à plat : une fente admise par la config coupe toujours une peau plate.
_SLOPE_SAFETY = 0.75
_SLOPE_STEP = 0.1  # mm, pas en x de lecture de la pente de peau dans la fenêtre autour de la fente
# Marge en x entre une fente et la paroi raide la plus proche, en largeurs de ligne (+ la fente elle-même) :
# la matière entre la fente et la paroi doit rester plus large qu'une ligne (contrôle « matière trop fine »).
_WALL_MARGIN_LINES = 2


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


def wall_limits(*, slit: float, angle_deg: float, line_width: float, max_mouth: float) -> tuple[float, float]:
    """(pente max |dy/dx| de peau qu'une fente peut traverser, marge en x entre une fente et une paroi plus raide).

    Critère de bouche : w·√(1 + y'²) ≤ max_mouth, w = slit / cos(angle) étant la largeur de la fente vue dans une
    couche ; pente max = _SLOPE_SAFETY·√((max_mouth/w)² − 1). ValueError si w ≥ max_mouth : même une peau plate
    serait entamée (refusé plus tôt par la validation de la config).
    """
    w = slit / math.cos(math.radians(angle_deg))
    if w >= max_mouth:
        raise ValueError(f"fente vue {w:.3f} mm en tranche ≥ ouverture max {max_mouth} mm : aucune nervure possible")
    return _SLOPE_SAFETY * math.sqrt((max_mouth / w) ** 2 - 1.0), _WALL_MARGIN_LINES * line_width + slit


def _row_edges(mid: MidSurface) -> tuple[np.ndarray, np.ndarray]:
    """Abscisses du bord d'attaque et du bord de fuite de chaque rangée z de la grille (dernier nœud sans
    matière avant / après la section ; bornes de la grille pour une rangée vide)."""
    le, te = np.full(len(mid.zs), mid.xs[0]), np.full(len(mid.zs), mid.xs[-1])
    for j in range(len(mid.zs)):
        full = np.flatnonzero(mid.thick[:, j] > 0)
        if len(full):
            le[j], te[j] = mid.xs[max(full[0] - 1, 0)], mid.xs[min(full[-1] + 1, len(mid.xs) - 1)]
    return le, te


def _steep_on_row(mid: MidSurface, x: np.ndarray, j: np.ndarray, max_slope: float, margin: float) -> np.ndarray:
    """Pente de peau (haute ou basse) > max_slope dans [x − margin, x + margin], lue sur la rangée j de la grille."""
    half = max(margin, _SLOPE_STEP / 2)
    n = math.ceil(2 * half / _SLOPE_STEP) + 1
    offs = np.linspace(-half, half, n)
    step = offs[1] - offs[0]
    ym, th = mid.at((x[:, None] + offs).ravel(), np.repeat(mid.zs[j], n))
    ym, th = np.reshape(ym, (len(x), n)), np.reshape(th, (len(x), n))
    slope = np.maximum(np.abs(np.diff(ym + th / 2, axis=1)), np.abs(np.diff(ym - th / 2, axis=1))) / step
    return (~(slope <= max_slope)).any(axis=1)  # NaN (surface incomplète) : raide, par prudence


def steep_samples(mid: MidSurface, x, z, *, max_slope: float, margin: float) -> np.ndarray:
    """Échantillons (x, z) d'une plaque trop près d'une paroi raide : dans la couche z, la peau haute ou basse
    (ym ± th/2) dépasse la pente `max_slope` quelque part dans [x − margin, x + margin].

    Pente lue dans la couche (à z fixé), là où la fente coupe la peau — pas le long de la plaque, qui mélangerait
    la variation en envergure (flèche, effilement), sans effet sur la bouche. Elle n'est pas lue sur
    l'interpolation bilinéaire en z, qui, sur un bord en flèche, moyenne deux sections décalées et adoucit la paroi
    (une paroi verticale y devient une rampe) : elle est lue sur les deux rangées de la grille qui encadrent z,
    à la même position relative le long de la corde ξ = (x − x_BA(z)) / (x_BF(z) − x_BA(z)), bords d'attaque et
    de fuite interpolés linéairement en z. Une paroi raide sur l'une ou l'autre rangée exclut l'échantillon :
    c'est conservateur si l'émoussé varie en envergure (la rangée la plus raide masque tout l'intervalle).
    Une rangée sans matière n'a ni bords ni pente : l'autre rangée est alors lue en x direct. Hors de la grille
    (z < zs[0] ou > zs[-1]), les deux rangées extrêmes.
    """
    x, z = np.asarray(x, dtype=float), np.asarray(z, dtype=float)
    if len(mid.zs) < 2:
        return _steep_on_row(mid, x, np.zeros(len(x), dtype=int), max_slope, margin)
    le, te = _row_edges(mid)
    empty = ~(mid.thick > 0).any(axis=0)
    j1 = np.clip(np.searchsorted(mid.zs, z), 1, len(mid.zs) - 1)
    j0 = j1 - 1
    t = np.clip((z - mid.zs[j0]) / (mid.zs[j1] - mid.zs[j0]), 0.0, 1.0)
    le_z, te_z = le[j0] + (le[j1] - le[j0]) * t, te[j0] + (te[j1] - te[j0]) * t
    xi = (x - le_z) / np.maximum(te_z - le_z, 1e-9)
    direct = empty[j0] | empty[j1]
    out = np.zeros(len(x), dtype=bool)
    for j in (j0, j1):
        out |= _steep_on_row(mid, np.where(direct, x, le[j] + xi * (te[j] - le[j])), j, max_slope, margin)
    return out


def _deep_runs(ss: np.ndarray, deep: np.ndarray) -> list[tuple[float, float]]:
    """Intervalles de s où la pièce est assez épaisse ; bornes au milieu entre deux échantillons."""
    runs = []
    idx = np.flatnonzero(np.diff(np.concatenate([[0], deep.astype(int), [0]])))
    for a, b in zip(idx[::2], idx[1::2]):  # échantillons a … b−1 profonds
        lo = ss[0] if a == 0 else (ss[a - 1] + ss[a]) / 2
        hi = ss[-1] if b == len(ss) else (ss[b - 1] + ss[b]) / 2
        runs.append((float(lo), float(hi)))
    return runs


def _partial_edge_vertices(s_run, y_run, free, grid_edge) -> np.ndarray:
    """Abscisses des sommets du bord d'une plaque peu profonde sur un tronçon profond.

    Parties ramenées sur la bande de séparation : mêmes sommets qu'une plaque full (`grid_edge` : bornes du
    tronçon + grille régulière) — la bande reste construite comme celle du réseau principal. Parties libres
    (à plus de _PARTIAL_EDGE_TOL au-dessus de la bande) : échantillons fins simplifiés à _PARTIAL_EDGE_TOL près,
    donc jamais sous la bande.
    """
    keep = [grid_edge[0], grid_edge[-1]]
    inside_free = np.zeros(len(grid_edge), dtype=bool)
    idx = np.flatnonzero(np.diff(np.concatenate([[0], free.astype(int), [0]])))
    for a, b in zip(idx[::2], idx[1::2]):  # échantillons libres a … b−1
        seg = list(zip(s_run[a:b], y_run[a:b]))
        keep += [p[0] for p in (LineString(seg).simplify(_PARTIAL_EDGE_TOL).coords if len(seg) > 1 else seg)]
        inside_free |= (grid_edge > s_run[a]) & (grid_edge < s_run[b - 1])
    keep += list(grid_edge[~inside_free])
    return np.unique(np.asarray(keep, dtype=float))


def _merge_close(s, keep=(), min_gap: float = _MIN_VERTEX_GAP) -> np.ndarray:
    """Abscisses `s` (∪ `keep`) triées, sans sommet à moins de `min_gap` d'un autre : les sommets de `keep` (bornes,
    sommets de bande) restent tous ; un autre sommet trop proche d'un sommet gardé est retiré."""
    keep = np.unique(np.asarray(keep, dtype=float))
    out = list(keep)
    last = -np.inf
    for v in np.unique(np.asarray(s, dtype=float)):
        if v - last < min_gap or (len(keep) and np.min(np.abs(keep - v)) < min_gap):
            continue
        out.append(v)
        last = v
    return np.unique(np.asarray(out, dtype=float))


def _shrink_off(runs, windows) -> list[tuple[float, float]]:
    """Tronçons dont aucune borne ne tombe dans un intervalle ouvert de `windows` : la borne est ramenée hors de
    l'intervalle, côté intérieur du tronçon (le tronçon raccourcit, jamais ne s'allonge)."""
    out = []
    for lo, hi in runs:
        for w0, w1 in windows:
            if w0 < lo < w1:
                lo = w1
            if w0 < hi < w1:
                hi = w0
        if hi > lo:
            out.append((lo, hi))
    return out


def _end_vertices(s_edge: np.ndarray, lo: float, hi: float, ends, min_gap: float = _MIN_VERTEX_GAP) -> np.ndarray:
    """Sommets du bord sur [lo, hi] : dans chaque fenêtre de bout (a ± h, élargie de `min_gap`), seulement
    a − h, a et a + h — mêmes sommets, en x, pour les deux familles qui se croisent au bout."""
    s = np.asarray(s_edge, dtype=float)
    for a, h in ends:
        inside = (s > a - h - min_gap) & (s < a + h + min_gap) & (s != lo) & (s != hi)
        anchors = np.array([a - h, a, a + h])
        s = np.concatenate([s[~inside], anchors[(anchors > lo) & (anchors < hi)]])
    return np.unique(s)


def _in_windows(s: float, windows) -> bool:
    return any(a - h - 1e-9 <= s <= a + h + 1e-9 for a, h in windows)


def _simplify_ring(coords, tol: float, windows) -> list:
    """Anneau simplifié à `tol` près, sommets des fenêtres de bout intacts (simplification par morceaux entre eux)."""
    pts = list(coords)[:-1]
    fixed = [i for i, p in enumerate(pts) if _in_windows(p[0], windows)]
    if not fixed:
        return list(LineString(list(coords)).simplify(tol).coords)
    pts = pts[fixed[0]:] + pts[:fixed[0]]
    fixed = [i - fixed[0] for i in fixed] + [len(pts)]
    pts.append(pts[0])
    out = []
    for i0, i1 in zip(fixed, fixed[1:]):
        chain = pts[i0:i1 + 1]
        out += list(LineString(chain).simplify(tol).coords)[:-1] if len(chain) > 2 else chain[:-1]
    return out + [out[0]]


def _simplify_keeping(poly: Polygon, tol: float, windows) -> Polygon:
    """`poly.simplify(tol)` sans toucher aux sommets des fenêtres de bout (a ± h) ; simplification ordinaire si le
    résultat par morceaux n'est pas valide."""
    if not windows:
        return poly.simplify(tol)
    out = Polygon(_simplify_ring(poly.exterior.coords, tol, windows),
                  [_simplify_ring(r.coords, tol, windows) for r in poly.interiors])
    return out if out.is_valid and not out.is_empty else poly.simplify(tol)


def _band_vertices(ss, ym, lo: float, hi: float, s_edge: np.ndarray, tol: float) -> np.ndarray:
    """`s_edge` complété des sommets de ym simplifiée à `tol` près sur [lo, hi] (bornes interpolées) ; un sommet
    de `s_edge` à moins de `_MIN_VERTEX_GAP` d'un sommet de bande est retiré (pas de micro-arête)."""
    inner = (ss > lo) & (ss < hi)
    s_run = np.concatenate([[lo], ss[inner], [hi]])
    y_run = np.interp(s_run, ss, ym)
    line = LineString(list(zip(s_run, y_run)))
    kept = [p[0] for p in line.simplify(tol).coords]
    while len(kept) > _MAX_BAND_VERTICES:  # plafond : tolérance relâchée plutôt qu'un bord trop coûteux
        tol *= 2
        kept = [p[0] for p in line.simplify(tol).coords]
    return _merge_close(s_edge, keep=kept)


def plate_profiles_2d(
    ss, ym, th, zones, *, gap, min_depth, ytop, ybot, edge_vertices: int | None = None,
    depth: float | None = None, steep: np.ndarray | None = None, band_tol: float | None = None,
    ends: Sequence[tuple[float, float]] = (),
) -> list[tuple[bool, Polygon]]:
    """Profils 2D des plaques haute et basse.

    ss/ym/th : échantillons fins le long de la plaque (pour situer les transitions d'épaisseur) ;
    le bord intérieur n'utilise que `edge_vertices` sommets (coût des booléens) ; pour un niveau peu
    profond (`depth`), il est simplifié à `_PARTIAL_EDGE_TOL` près au lieu d'être échantillonné régulièrement.
    Une transition profonde / trop mince est une marche verticale, jamais une rampe : en tranche, une
    rampe donne des pointes de fente obliques qui pincent la bande de séparation.

    `depth` (mm, None = full) : la plaque ne descend qu'à `depth` sous la peau (y_peau = ym ± th/2),
    sans jamais dépasser la bande de séparation. Profondeur mesurée selon Y (épaisseur), pas selon la
    normale à la peau : écart de l'ordre de depth·(1 − cos(pente de la peau)), négligeable sur un profil d'aile.

    `steep` (booléens par échantillon, voir `steep_samples`) : échantillons trop près d'une paroi raide, exclus
    comme les trop minces (même marche verticale) — la fente y raserait la paroi au lieu de la traverser.

    `band_tol` (mm) : la bande de séparation (ym ± gap/2) est suivie à `band_tol` près — sommets réguliers
    complétés de ceux de ym simplifiée (Douglas-Peucker), mêmes sommets en haut et en bas. Sans cela, là où ym est
    coudée (bord épais dissymétrique), les cordes des deux familles s'écartent différemment de ym et, au croisement
    de deux plaques, leurs bandes se décalent jusqu'à pincer la matière qui les relie.

    `ends` : fenêtres de bout (a, h) — abscisse s du plan de bout de la pièce et demi-largeur. Les abscisses
    a − h, a, a + h doivent être dans `ss` (valeurs exactes). Aucune borne de tronçon dans une fenêtre, et seulement
    ces trois sommets dedans (voir `_END_CLAMP_SHARE`), conservés par la simplification finale.
    """
    ss, ym, th = (np.asarray(a, dtype=float) for a in (ss, ym, th))
    deep = th > gap + 2 * min_depth
    if steep is not None:
        deep &= ~np.asarray(steep, dtype=bool)
    g = _MIN_VERTEX_GAP
    runs = _shrink_off(_deep_runs(ss, deep), [(a - h - g, a + h + g) for a, h in ends])
    if not runs:
        return []
    simplify = depth is not None and edge_vertices is not None
    coarse = ss if edge_vertices is None else np.linspace(ss[0], ss[-1], edge_vertices)
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
            if band_tol is not None and edge_vertices is not None:
                s_edge = _band_vertices(ss, ym, lo, hi, s_edge, band_tol)
            # bord d'une plaque peu profonde lu sur les seuls échantillons profonds du tronçon : aux bornes (entre
            # deux échantillons), pas de mélange avec l'épaisseur d'un échantillon trop mince (marche, pas de rampe)
            run = np.ones(len(ss), dtype=bool) if depth is None else deep & (ss >= lo) & (ss <= hi)
            if simplify:
                free = th[run] / 2 - depth > gap / 2 + _PARTIAL_EDGE_TOL
                s_edge = _merge_close(_partial_edge_vertices(ss[run], inner[run], free, s_edge), keep=[lo, hi])
            if ends:
                s_edge = _end_vertices(s_edge, lo, hi, ends)
            y_edge = np.interp(s_edge, ss[run], inner[run])
            pieces.append(Polygon(list(zip(s_edge, y_edge)) + [(hi, far), (lo, far)]).buffer(0))
        poly = unary_union(pieces)
        if zones:
            shift = -_FAR if top else _FAR
            # ombre côté surface médiane : un fragment coincé entre la zone et la bande ne toucherait pas la peau
            shadow = unary_union([z.union(affinity.translate(z, yoff=shift)).convex_hull for z in zones])
            poly = poly.difference(shadow)
        for part in getattr(poly, "geoms", [poly]):
            if isinstance(part, Polygon) and not part.is_empty and part.area > 1e-3:
                out.append((top, _simplify_keeping(part, 0.01, ends)))
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


def plate_lines(bb, layer: RibLayer, angle_deg: float, anchor=None) -> list[tuple[int, float]]:
    """Plans du motif losange : (famille ±1, abscisse c de la plaque en z = zmin).

    `anchor` (boîte englobante, None = `bb`) : boîte sur laquelle le motif est calé (x = anchor.min.X + phase en
    z = anchor.min.Z) ; avec la boîte globale de plusieurs corps d'aile, les plaques de deux corps voisins sont
    dans les mêmes plans et les nervures se prolongent d'un corps à l'autre. Les plans restent exprimés dans le
    repère de `bb` (c en z = bb.min.Z) et couvrent `bb`."""
    ang = math.radians(angle_deg)
    reach = _plate_length(bb, angle_deg) * math.sin(ang)
    ref = bb if anchor is None else anchor
    out = []
    for sign in (1, -1):
        # plan d'origine du motif, suivi jusqu'à z = bb.min.Z (décalage nul sans ancre)
        origin = ref.min.X + layer.phase + sign * math.tan(ang) * (bb.min.Z - ref.min.Z)
        lo = bb.min.X - (reach if sign > 0 else 0) - layer.spacing
        hi = bb.max.X + (reach if sign < 0 else 0) + layer.spacing
        for k in range(math.floor((lo - origin) / layer.spacing), math.ceil((hi - origin) / layer.spacing) + 1):
            out.append((sign, origin + k * layer.spacing))
    return out


@dataclass(frozen=True)
class PlateProfile:
    """Profil 2D d'une plaque dans son plan (x = c + s·d0, z = zmin + s·d1) ; `polygon` en (s, y)."""

    family: int  # ±1
    c: float
    d0: float
    d1: float
    top: bool  # côté extrados (+Y) de la bande de séparation
    polygon: Polygon


def _end_windows(mid: MidSurface, zmin: float, zmax: float, d1: float) -> list[tuple[float, float]]:
    """Fenêtres de bout (a, h) en abscisse s d'une plaque (z = zmin + s·d1) : plans z = zmin et z = zmax, demi-largeur
    h telle que la fenêtre reste dans la zone où la surface médiane ne dépend plus de z (au-delà des rangées
    extrêmes de la grille). Aucune fenêtre si la grille touche le bout."""
    dz = _END_CLAMP_SHARE * min(mid.zs[0] - zmin, zmax - mid.zs[-1])
    if dz <= _MIN_VERTEX_GAP:
        return []
    return [(0.0, dz / d1), ((zmax - zmin) / d1, dz / d1)]


def diamond_profiles(
    bb, mid: MidSurface, bores: list[Bore], layer: RibLayer, *, angle_deg: float, gap: float, slit: float,
    min_depth: float, line_width: float, max_mouth: float, exclude: Sequence[tuple[int, float]] = (),
    walls: bool = True, bays: Sequence[ServoBay] = (), pocket_strip: float = 0.0, anchor=None,
) -> list[PlateProfile]:
    """Profils 2D des plaques d'un niveau, sans construire les solides (aperçu rapide).

    `exclude` : plans (famille, c) déjà coupés par un niveau plus profond. `line_width`, `max_mouth` : largeur
    de ligne et bouche max de la validation, d'où l'arrêt des plaques avant les parois raides (`wall_limits`) ;
    `walls=False` : sans cet arrêt (pour vérifier qu'il ne vide pas un niveau, voir `emptied_by_walls`).
    `bays` : baies de servo, zones interdites (poche + `pocket_strip`, ponts, anneaux des trous qui y débouchent)
    avec la même ombre que les trous, limitées à la peau d'ouverture le long de la plaque.
    `anchor` : boîte de calage du motif (voir `plate_lines`).
    """
    max_slope, margin = wall_limits(slit=slit, angle_deg=angle_deg, line_width=line_width, max_mouth=max_mouth)
    # deux bandes qui se croisent, chacune à band_tol de ym ± gap/2, se recouvrent d'au moins gap − 2·band_tol :
    # la moitié de l'excès de gap sur une ligne, l'autre moitié en réserve
    band_tol = max((gap - line_width) / 4, _MIN_BAND_TOL)
    ang = math.radians(angle_deg)
    zmin, zmax = bb.min.Z, bb.max.Z
    ytop, ybot = bb.max.Y + 5, bb.min.Y - 5
    length = _plate_length(bb, angle_deg)
    # échantillonnage fin (≈ 0,5 mm) pour situer les transitions d'épaisseur ; bord à `samples` sommets
    ss = np.linspace(-5, length, max(layer.samples, math.ceil((length + 5) / _DEPTH_STEP) + 1))
    ends = _end_windows(mid, zmin, zmax, math.cos(ang))
    ss = np.unique(np.concatenate([ss, [a + k * h for a, h in ends for k in (-1, 0, 1)]]))
    out: list[PlateProfile] = []
    for sign, c in plate_lines(bb, layer, angle_deg, anchor):
        if any(es == sign and abs(ec - c) < _SAME_LINE for es, ec in exclude):
            continue
        d0, d1 = sign * math.sin(ang), math.cos(ang)
        x, z = c + ss * d0, zmin + ss * d1
        ym, th = mid.at(x, z)
        steep = np.zeros(len(ss), dtype=bool)
        cand = th > gap + 2 * min_depth  # seuls les échantillons assez épais peuvent recevoir une fente
        if walls and cand.any():
            steep[cand] = steep_samples(mid, x[cand], z[cand], max_slope=max_slope, margin=margin)
        zones = [z for b in bores for z in bore_zones_2d(b, c, d0, d1, zmin, zmax, slit=slit)]
        for bay in bays:
            open_skin = np.where(th > 0, ym + bay.side * th / 2, np.nan)  # peau d'ouverture le long de la plaque
            zones += pocket_zones_2d(bay, c, d0, d1, zmin, zmax, strip=pocket_strip, slit=slit, skin=(ss, open_skin))
        for top, poly in plate_profiles_2d(
            ss, ym, th, zones, gap=gap, min_depth=min_depth, ytop=ytop, ybot=ybot, edge_vertices=layer.samples,
            depth=None if layer.depth == "full" else layer.depth, steep=steep, band_tol=band_tol, ends=ends,
        ):
            out.append(PlateProfile(sign, c, d0, d1, top, poly))
    return out


def emptied_by_walls(bb, mid: MidSurface, bores: list[Bore], layer: RibLayer, **opts) -> bool:
    """Le niveau aurait des plaques sans l'arrêt avant les parois raides, mais n'en a aucune avec (garde-fou :
    une aile sans nervure passerait la validation sans un mot)."""
    return not diamond_profiles(bb, mid, bores, layer, **opts) and bool(
        diamond_profiles(bb, mid, bores, layer, **opts, walls=False)
    )


def diamond_plates(
    bb, mid: MidSurface, bores: list[Bore], layer: RibLayer, *, angle_deg: float, gap: float, slit: float,
    min_depth: float, line_width: float, max_mouth: float, exclude: Sequence[tuple[int, float]] = (),
    bays: Sequence[ServoBay] = (), pocket_strip: float = 0.0, anchor=None,
) -> list[Solid]:
    """Plaques d'un niveau ; `exclude` : plans (famille, c) déjà coupés par un niveau plus profond ; `anchor` :
    boîte de calage du motif (voir `plate_lines`)."""
    profiles = diamond_profiles(
        bb, mid, bores, layer, angle_deg=angle_deg, gap=gap, slit=slit, min_depth=min_depth,
        line_width=line_width, max_mouth=max_mouth, exclude=exclude, bays=bays, pocket_strip=pocket_strip,
        anchor=anchor,
    )
    return [plate_solid(p.polygon, p.c, p.d0, p.d1, bb.min.Z, slit) for p in profiles]
