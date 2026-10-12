"""Baies de servo (spec servo §4) : poche ouverte sur une peau, prolongée par deux rampes en envergure.

Repère orienté : Z envergure, X corde, Y épaisseur (extrados +Y). La boîte du corps `SERVO…` est l'espace utile.
La poche est un prisme le long de X sur [x0, x1] dont le profil (z, y) est un trapèze : le fond de la boîte de z0
à z1, puis deux rampes qui remontent du fond vers l'extérieur, inclinées de `max_overhang_deg` par rapport à Z
(surplomb max de la matière, aux deux bouts : l'intérieur est creux, la paroi du bas surplombe aussi).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, replace

import numpy as np
from build123d import Align, Box, Face, GeomType, Location, Solid, Vector, Wire, extrude
from shapely import affinity
from shapely.geometry import LineString, Point, Polygon, box

from aerorib.config import Config
from aerorib.features.bores import Bore, bore_extent
from aerorib.midsurface import MidSurface

ALIGN_TOL_DEG = 2.0  # écart max des faces de la boîte aux axes orientés
OUTSIDE = 5.0  # mm : la poche dépasse la boîte englobante de la pièce de cette marge (côté ouverture)
_SAMPLES = 9  # échantillons par direction (x, z) pour situer la boîte par rapport aux peaux
SKIN_MARGIN = 0.5  # mm : la zone sans nervure s'arrête à cette distance au-delà de la peau d'ouverture
HOLE_TOL = 1.0  # mm : marge en envergure de la partie d'un trou retirée de la référence de validation
# Renfort : la bande de matière entre la poche et la fente (paroi doublée) serait un îlot si la fente en U était
# continue d'un bord de l'ouverture à l'autre. Dans chaque couche, la fente est interrompue par UN pont (un seul
# intervalle de x : deux branches en J, chacune ouverte sur la peau ; deux ponts laisseraient un tronçon de fente
# fermé). Le pont est choisi par tranche de z (`rf_gaps`) :
# - couches de rampe : autour du point haut de la peau d'ouverture dans la tranche — là où la rampe sort de la
#   peau, la bande ne subsiste qu'autour de ce point (peau unimodale le long de la corde, cas d'un profil) ;
# - tranche où un trou rencontre la poche ou la fente, ou étendue d'un trou dont la fente vers la peau d'ouverture
#   traverse la paroi doublée : pont autour du trou / de sa fente (ils couperaient la bande), réuni au
#   pont de rampe dans les couches de rampe ; entre z0 et z1, le fond est sur toute la corde : le pont du trou suffit ;
# - ailleurs : pont minimal au point haut de la peau.
RING_LINES = 2.0  # largeurs de ligne de matière gardées sans nervure autour d'un trou qui débouche dans la poche
BRIDGE_MIN = 1.0  # mm : longueur minimale d'un pont (au moins 2 largeurs de ligne)
BRIDGE_STEP = 1.0  # mm : pas des tranches de z dans les couches de rampe
_FAR = 1e4


class ServoError(ValueError):
    """Baie de servo non prise en charge (message en français)."""


@dataclass(frozen=True)
class ServoBay:
    name: str
    side: int  # +1 : ouverte sur l'extrados (+Y) ; -1 : sur l'intrados
    x0: float  # boîte orientée (espace utile)
    x1: float
    y_floor: float  # fond de la baie (face de la boîte côté opposé à l'ouverture)
    y_box: float  # face de la boîte côté ouverture (hors de la peau)
    z0: float
    z1: float
    depth: float  # écart max fond ↔ peau d'ouverture sur l'étendue en corde, aux bouts et le long des rampes
    ramp: float  # Δ : longueur d'une rampe en envergure (depth / tan(max_overhang))
    run: float  # dz/dy des rampes = 1 / tan(max_overhang)
    y_out: float  # la poche s'étend jusqu'à cette ordonnée, au-delà de la peau
    zmin: float  # étendue de la pièce en envergure
    zmax: float
    # peau d'ouverture la plus extérieure autour de la poche (corde ± bande, rampes ± bande) : les zones sans
    # nervure s'arrêtent juste au-delà (plus loin, une plaque ne coupe que de l'air)
    y_skin: float
    rf_offset: float = 0.0  # renfort : épaisseur de la paroi doublée, paroi de poche → bord proche de la fente (0 : sans)
    rf_width: float = 0.0  # largeur de la fente de renfort
    rf_gaps: tuple[tuple[float, float, float, float], ...] = ()  # ponts : (z début, z fin, x début, x fin)
    rf_lost: tuple[tuple[float, float], ...] = ()  # tranches de z où un trou supprime une branche de la fente
    # trous qui débouchent dans la poche : (x, y, rayon de l'anneau sans nervure, z début, z fin). Là où le trou
    # rejoint la poche, il fait partie de l'extérieur et son manchon devient le seul passage de la matière autour
    # de lui : un manchon de gaine (≈ 1,2 ligne) moins la demi-épaisseur d'une plaque voisine serait trop fin.
    rings: tuple[tuple[float, float, float, float, float], ...] = ()

    @property
    def reach(self) -> float:
        """Distance de la paroi de poche au bord extérieur de la fente de renfort (0 sans renfort)."""
        return self.rf_offset + self.rf_width if self.rf_offset > 0 else 0.0

    @property
    def side_name(self) -> str:
        return "extrados" if self.side > 0 else "intrados"

    @property
    def opening(self) -> tuple[float, float]:
        """Étendue en envergure de l'ouverture dans la peau (rampes comprises), limitée à la pièce."""
        return max(self.z0 - self.ramp, self.zmin), min(self.z1 + self.ramp, self.zmax)


def _aligned_box(name: str, solid: Solid) -> tuple[float, float, float, float, float, float]:
    faces = solid.faces()
    if len(faces) != 6 or any(f.geom_type != GeomType.PLANE for f in faces):
        raise ServoError(f"{name} : la baie doit être une boîte (6 faces planes), {len(faces)} face(s) trouvée(s)")
    cos_tol = math.cos(math.radians(ALIGN_TOL_DEG))
    for f in faces:
        n = f.normal_at()
        if max(abs(n.X), abs(n.Y), abs(n.Z)) < cos_tol:
            raise ServoError(f"{name} : baie non alignée sur la corde / l'envergure (pris en charge plus tard)")
    bb = solid.bounding_box(optimal=True)
    return bb.min.X, bb.max.X, bb.min.Y, bb.max.Y, bb.min.Z, bb.max.Z


def _skins(mid: MidSurface, x, z) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    ym, th = mid.at(np.asarray(x, dtype=float), np.asarray(z, dtype=float))
    return ym + th / 2, ym - th / 2, th


def servo_bay(name: str, solid: Solid, bb, mid: MidSurface, cfg: Config, bores: Sequence[Bore] = ()) -> ServoBay:
    x0, x1, y0, y1, z0, z1 = _aligned_box(name, solid)
    xs, zs = np.meshgrid(np.linspace(x0, x1, _SAMPLES), np.linspace(z0, z1, _SAMPLES))
    top, bot, th = _skins(mid, xs.ravel(), zs.ravel())
    inside = th > 0
    top, bot = top[inside], bot[inside]
    if not inside.any():
        raise ServoError(f"{name} : la boîte ne traverse aucune peau (elle est hors de la pièce)")
    cross_top = bool(np.any((y1 > top) & (y0 < top)))
    cross_bot = bool(np.any((y0 < bot) & (y1 > bot)))
    if cross_top and cross_bot:
        raise ServoError(f"{name} : la boîte traverse l'extrados et l'intrados (une baie s'ouvre d'un seul côté)")
    if not (cross_top or cross_bot):
        raise ServoError(
            f"{name} : la boîte ne traverse aucune peau (elle doit dépasser de l'extrados ou de l'intrados)"
        )
    side = 1 if cross_top else -1
    floor, outer = (y0, y1) if side > 0 else (y1, y0)
    gap = float(np.min(floor - bot)) if side > 0 else float(np.min(top - floor))
    need = cfg.servo.min_floor_gap
    rf_offset = cfg.reinforce_offset() if cfg.servo.reinforce else 0.0
    rf_width = cfg.servo.slit_width if cfg.servo.reinforce else 0.0
    reach = rf_offset + rf_width if rf_offset > 0 else 0.0
    other = "l'intrados" if side > 0 else "l'extrados"
    if gap < need:
        raise ServoError(
            f"{name} : le fond de la baie laisse {gap:.2f} mm avant {other} (minimum servo.min_floor_gap = {need:g} mm)"
        )
    if gap < need + reach:
        raise ServoError(
            f"{name} : le fond de la baie laisse {gap:.2f} mm avant {other} ; avec le renfort (paroi + fente = "
            f"{reach:.2f} mm sous le fond), il faut servo.min_floor_gap + {reach:.2f} = {need + reach:.2f} mm "
            "(désactiver servo.reinforce, ou réduire servo.reinforce_offset)"
        )
    run = 1.0 / math.tan(math.radians(cfg.preset.max_overhang_deg))
    zmin, zmax = bb.min.Z, bb.max.Z

    def outer_skin(xa: float, xb: float, spans: list[tuple[float, float]]) -> float | None:
        """Peau d'ouverture la plus extérieure (side·y max) sur [xa, xb] × les intervalles de z."""
        xs_ = np.linspace(xa, xb, 4 * _SAMPLES)
        best = None
        for za, zb in spans:
            za, zb = max(za, zmin), min(zb, zmax)
            if zb < za:
                continue
            gx, gz = np.meshgrid(xs_, np.linspace(za, zb, _SAMPLES))
            t, b_, th_ = _skins(mid, gx.ravel(), gz.ravel())
            vals = side * (t if side > 0 else b_)[th_ > 0]
            if len(vals):
                best = float(vals.max()) if best is None else max(best, float(vals.max()))
        return None if best is None else side * best

    # profondeur : écart max fond ↔ peau d'ouverture sur la corde de la boîte, aux deux bouts puis (une itération)
    # le long des rampes — prudent sur une aile effilée, où la peau est plus haute d'un côté
    ends = outer_skin(x0, x1, [(z0, z0), (z1, z1)])
    depth = max(side * (ends - floor), 0.0) if ends is not None else 0.0
    along = outer_skin(x0, x1, [(z0 - depth * run, z0), (z1, z1 + depth * run)])
    if along is not None:
        depth = max(depth, side * (along - floor))
    ramp = depth * run
    grow = cfg.servo.strip + cfg.slit_width / 2 + reach
    around = outer_skin(x0 - grow, x1 + grow, [(z0 - ramp - grow, z1 + ramp + grow)])
    y_skin = around if around is not None else floor + side * depth
    y_out = bb.max.Y + OUTSIDE if side > 0 else bb.min.Y - OUTSIDE
    bay = ServoBay(
        name, side, x0, x1, floor, outer, z0, z1, depth, ramp, run, y_out, zmin, zmax, y_skin, rf_offset, rf_width
    )
    rings = []
    for b in bores:
        if not b.cut_hole:
            continue
        lo, hi = bore_extent(b, zmin, zmax)
        ring = b.radius + RING_LINES * cfg.preset.line_width
        # trou qui rejoint la poche ou la fente de renfort : extérieur dans ces couches
        meet = hole_meets_pocket(bay, b.x, b.y, b.radius, lo, hi, tol=ring, grow=bay.reach)
        if meet is not None:
            rings.append((b.x, b.y, ring, *meet))
    bay = replace(bay, rings=tuple(rings))
    if rf_offset > 0:
        bay = _bridges(bay, bores, mid, max(BRIDGE_MIN, 2 * cfg.preset.line_width), cfg.preset.layer_height)
    return bay


def _bridges(
    bay: ServoBay, bores: Sequence[Bore], mid: MidSurface, length: float, layer_height: float | None = None
) -> ServoBay:
    """Ponts de la fente de renfort par tranche de z (voir l'en-tête du module), bornés à [x0 − reach, x1 + reach] ;
    tranches où un trou supprime une branche entière (`rf_lost`, signalées par `reinforce_warnings`).

    `layer_height` : bornes des tranches calées sur zmin + k·hauteur de couche, à mi-chemin entre deux couches
    (`layer_heights`) : aucune couche ne tombe sur une borne, l'aperçu et la génération prennent la même tranche.
    """
    side, x0, x1, reach = bay.side, bay.x0, bay.x1, bay.reach

    def snap(z: float) -> float:
        if not layer_height:
            return z
        return bay.zmin + round((z - bay.zmin) / layer_height) * layer_height
    xs_ = np.linspace(x0, x1, 8 * _SAMPLES)

    def high_x(z: float) -> float:
        t, b_, th_ = _skins(mid, xs_, np.full(len(xs_), min(max(z, bay.zmin), bay.zmax)))
        sk = np.where(th_ > 0, side * (t if side > 0 else b_), -np.inf)
        x = float(xs_[int(np.argmax(sk))])
        return min(max(x, x0 + length / 2), x1 - length / 2)

    # étendue en z où la fente coupe la peau : rampe décalée de reach (≤ reach·√(1 + run²) en z), jusqu'à la peau
    ext = abs(bay.y_skin - bay.y_floor) * bay.run + 2 * reach * math.sqrt(1 + bay.run**2) + 1.0
    lo_all, hi_all = max(bay.z0 - ext, bay.zmin - 1.0), min(bay.z1 + ext, bay.zmax + 1.0)
    holes = []  # (z début, z fin, x début, x fin)
    for b in bores:
        if not b.cut_hole:
            continue
        lo, hi = bore_extent(b, bay.zmin, bay.zmax)
        meet = hole_meets_pocket(bay, b.x, b.y, b.radius, lo, hi, tol=reach + length, grow=reach)
        if meet is not None:
            holes.append((snap(meet[0]), snap(meet[1]), b.x - b.radius - length, b.x + b.radius + length))
        # fente du trou vers la peau d'ouverture : elle traverse la paroi doublée (fond, ou rampe dans les couches
        # de rampe) sur toute l'étendue du trou (même effet qu'un trou)
        if b.side == side and x0 - reach < b.x < x1 + reach:
            holes.append((snap(lo), snap(hi), b.x - b.slit / 2 - length, b.x + b.slit / 2 + length))
    cuts = {lo_all, hi_all}
    if lo_all < bay.z0:
        cuts |= set(np.arange(bay.z0, lo_all, -BRIDGE_STEP).tolist())
    if bay.z1 < hi_all:
        cuts |= set(np.arange(bay.z1, hi_all, BRIDGE_STEP).tolist())
    cuts |= {min(max(z, lo_all), hi_all) for h in holes for z in h[:2]}
    zs = sorted({snap(z) for z in cuts})
    bands: list[list[float]] = []
    lost: list[list[float]] = []
    for za, zb in zip(zs[:-1], zs[1:]):
        if zb - za < 1e-6:
            continue
        middle = bay.z0 <= (za + zb) / 2 <= bay.z1
        if middle:
            hx = high_x((za + zb) / 2)
            base = (hx - length / 2, hx + length / 2)
        else:
            ha, hb = sorted((high_x(za), high_x(zb)))
            base = (ha - length / 2, hb + length / 2)
        near = [(ga, gb) for ha_, hb_, ga, gb in holes if ha_ < zb and hb_ > za]
        if near:
            parts = near if middle else [base, *near]
            gap = (min(a for a, _ in parts), max(b for _, b in parts))
        else:
            gap = base
        gap = (max(gap[0], x0 - reach), min(gap[1], x1 + reach))
        if gap[0] < x0 - bay.rf_offset or gap[1] > x1 + bay.rf_offset:  # fente latérale coupée : branche perdue
            if lost and abs(lost[-1][1] - za) < 1e-9:
                lost[-1][1] = zb
            else:
                lost.append([za, zb])
        if bands and abs(bands[-1][1] - za) < 1e-9 and bands[-1][2:] == list(gap):
            bands[-1][1] = zb
        else:
            bands.append([za, zb, *gap])
    return replace(
        bay, rf_gaps=tuple(tuple(b) for b in bands), rf_lost=tuple(tuple(z) for z in lost)
    )


def reinforce_warnings(bays: Sequence[ServoBay]) -> list[str]:
    """Avertissements français des baies dont le renfort a perdu une branche (trou au bord de la boîte)."""
    return [
        f"{b.name} : renfort incomplet — un trou supprime une branche de la fente de renfort sur z = "
        + ", ".join(f"{za:.1f}–{zb:.1f}" for za, zb in b.rf_lost)
        + " mm (paroi non doublée de ce côté)"
        for b in bays
        if b.rf_lost
    ]


def servo_bays(
    servos: Sequence[tuple[str, Solid]], skin: Solid, mid: MidSurface, cfg: Config, bores: Sequence[Bore] = ()
) -> list[ServoBay]:
    """Baies des corps servo orientés (nom, solide) ; ServoError (français) si une boîte n'est pas prise en charge.
    `bores` : trous de la pièce, pour placer les ponts de la fente de renfort."""
    bb = skin.bounding_box()
    return [servo_bay(name, solid, bb, mid, cfg, bores) for name, solid in servos]


def pocket_profile(bay: ServoBay) -> Polygon:
    """Profil (z, y) de la poche : fond de z0 à z1, rampes jusqu'à `y_out` (au-delà de la peau)."""
    h = abs(bay.y_out - bay.y_floor)
    reach = h * bay.run
    return Polygon(
        [(bay.z0, bay.y_floor), (bay.z1, bay.y_floor), (bay.z1 + reach, bay.y_out), (bay.z0 - reach, bay.y_out)]
    )


def _prism(profile: Polygon, xa: float, xb: float) -> Solid:
    ring = [Vector(xa, y, z) for z, y in list(profile.exterior.coords)[:-1]]
    return extrude(Face(Wire.make_polygon(ring, close=True)), amount=xb - xa, dir=Vector(1, 0, 0))


def pocket_solid(bay: ServoBay) -> Solid:
    """Outil de coupe de la poche : profil (z, y) extrudé le long de X sur [x0, x1]."""
    return _prism(pocket_profile(bay), bay.x0, bay.x1)


def _grown(bay: ServoBay, d: float) -> tuple[Polygon, float, float]:
    """Poche décalée de d vers l'intérieur de l'aile : profil (z, y) et étendue en x."""
    prof = pocket_profile(bay)
    return (prof.buffer(d, join_style="mitre") if d > 0 else prof), bay.x0 - d, bay.x1 + d


def reinforce_solids(bay: ServoBay) -> list[Solid]:
    """Fente de renfort : poche décalée de `rf_offset + w` moins poche décalée de `rf_offset` (U parallèle aux parois,
    au fond et aux rampes, ouvert vers l'extérieur ; paroi doublée d'épaisseur `rf_offset`), interrompue aux ponts
    `rf_gaps` (un par tranche de z). Vide sans renfort.
    """
    if bay.rf_offset <= 0:
        return []
    outer = _prism(*_grown(bay, bay.reach))
    inner = _prism(*_grown(bay, bay.rf_offset))
    _, ya, _, yb = _grown(bay, bay.reach)[0].bounds
    cuts = [
        Box(xb - xa, yb - ya + 2, zb - za, align=(Align.MIN, Align.MIN, Align.MIN)).moved(Location((xa, ya - 1, za)))
        for za, zb, xa, xb in bay.rf_gaps
    ]
    return list(outer.cut(inner, *cuts).solids())


def _y_range(prof: Polygon, z: float) -> tuple[float, float] | None:
    cut = prof.intersection(LineString([(z, -_FAR), (z, _FAR)]))
    if cut.is_empty or cut.length <= 0:
        return None
    return cut.bounds[1], cut.bounds[3]


def reinforce_section(bay: ServoBay, z: float) -> list[Polygon]:
    """Coupe de la fente de renfort par le plan horizontal z, en (x, y) (aperçu)."""
    if bay.rf_offset <= 0:
        return []
    prof, xa, xb = _grown(bay, bay.reach)
    ys = _y_range(prof, z)
    if ys is None:
        return []
    geom = box(xa, ys[0], xb, ys[1])
    prof, xa, xb = _grown(bay, bay.rf_offset)
    ys = _y_range(prof, z)
    if ys is not None:
        geom = geom.difference(box(xa, ys[0], xb, ys[1]))
    for za, zb, ga, gb in bay.rf_gaps:
        if za <= z <= zb:
            geom = geom.difference(box(ga, -_FAR, gb, _FAR))
            break
    return [g for g in getattr(geom, "geoms", [geom]) if isinstance(g, Polygon) and not g.is_empty]


def pocket_section(bay: ServoBay, z: float) -> Polygon | None:
    """Coupe de la poche par le plan horizontal z, en (x, y) (aperçu)."""
    prof = pocket_profile(bay)
    lo, _, hi, _ = prof.bounds
    if not lo < z < hi:
        return None
    if z < bay.z0:
        y_ramp = bay.y_floor + bay.side * (bay.z0 - z) / bay.run
    elif z > bay.z1:
        y_ramp = bay.y_floor + bay.side * (z - bay.z1) / bay.run
    else:
        y_ramp = bay.y_floor
    ya, yb = sorted((y_ramp, bay.y_out))
    return box(bay.x0, ya, bay.x1, yb)


def pocket_zones_2d(
    bay: ServoBay, c: float, d0: float, d1: float, zmin: float, zmax: float, *, strip: float, slit: float
) -> list[Polygon]:
    """Zone sans nervure de la poche dans le repère (s, y) de la plaque (x = c + s·d0, z = zmin + s·d1).

    Poche agrandie de `strip` + slit/2 (demi-épaisseur de plaque) en x, z et y, puis coupée par le plan de la
    plaque : profil (z, y) ramené en s, limité aux s où la plaque traverse l'étendue en corde de la boîte.
    """
    grow = strip + slit / 2 + bay.reach  # renfort compris
    # au-delà de la peau d'ouverture, une plaque ne coupe que de l'air : la zone s'arrête juste après la peau
    # (sinon, prolongée jusqu'à y_out, son ombre retirerait des nervures loin au-delà de l'ouverture)
    cap = bay.y_skin + bay.side * SKIN_MARGIN
    ya, yb = sorted((bay.y_floor - bay.side * _FAR, cap))
    prof = pocket_profile(bay).intersection(box(-_FAR, ya, _FAR, yb)).buffer(grow, join_style="mitre")
    mapped = affinity.affine_transform(prof, [1 / d1, 0, 0, 1, -zmin / d1, 0])
    sa, sb = sorted(((bay.x0 - grow - c) / d0, (bay.x1 + grow - c) / d0))
    zone = mapped.intersection(box(sa, -_FAR, sb, _FAR))
    out = [zone] if isinstance(zone, Polygon) and not zone.is_empty and zone.area > 0 else []
    for x, y, ring, za, zb in bay.rings:  # anneau autour des trous qui débouchent dans la poche (cf. `rings`)
        r = ring + slit / 2
        s0 = (x - c) / d0
        ell = affinity.scale(Point(s0, y).buffer(1.0, quad_segs=16), r / abs(d0), r, origin=(s0, y))
        part = ell.intersection(box((za - zmin) / d1 - slit / (2 * abs(d0)), -_FAR, (zb - zmin) / d1 + slit / (2 * abs(d0)), _FAR))
        if isinstance(part, Polygon) and not part.is_empty and part.area > 0:
            out.append(part)
    return out


def hole_meets_pocket(
    bay: ServoBay, x: float, y: float, r: float, lo: float, hi: float, tol: float = HOLE_TOL, grow: float = 0.0
) -> tuple[float, float] | None:
    """Étendue en envergure où un trou (axe (x, y), rayon r, de lo à hi en z) débouche dans la poche, ± tol,
    bornée au trou ; None s'il ne la rencontre pas.

    Point du cercle le plus avancé vers l'ouverture sur l'étendue en corde de la boîte, comparé au profil réel de la
    poche (fond et rampes) : la poche est convexe et s'évase vers l'extérieur. `grow` : poche décalée de grow
    (fente de renfort comprise).
    """
    prof, xa, xb = _grown(bay, grow)
    d = max(xa - x, x - xb, 0.0)
    if d >= r:
        return None
    y_t = y + bay.side * math.sqrt(r * r - d * d)
    cut = prof.intersection(LineString([(lo, y_t), (hi, y_t)]))
    if cut.is_empty or cut.length <= 0:
        return None
    za, _, zb, _ = cut.bounds
    return max(za - tol, lo), min(zb + tol, hi)
