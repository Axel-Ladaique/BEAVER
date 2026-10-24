"""Coupe plane dans la corde (spec découpe §4) : planification sans booléen, puis découpe des tronçons.

Un tronçon (en Z) dont une section ne tient pas sur le plateau, même tournée, est coupé en k morceaux par des plans
qui contiennent l'axe Y (épaisseur). Plans parallèles d'abord, verticaux de préférence (face de coupe sans surplomb,
morceaux non cisaillés), sinon inclinés comme le bord d'attaque, le bord de fuite ou le milieu de la corde : le moins
de morceaux, puis la plus faible inclinaison, puis des morceaux équilibrés ; chaque plan traverse toute la hauteur du
tronçon. Repli : plans en éventail aux points i/k de la corde aux deux bouts du tronçon. Un plan qui traverserait un
longeron ou une gaine (trou + manchon + bande) ou une baie de servo (poche + renfort + bande) est décalé
parallèlement, en corde, vers la position libre la plus proche."""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

import numpy as np
from build123d import Face, Pos, Shape, Solid, Vector, Wire, extrude
from shapely.geometry import MultiPoint, Polygon, box

from beaver.layout import Placement, fit_on_bed
from beaver.segment import (
    MIN_SEGMENT, CutFailure, PiecePlan, SegmentError, Sections, _extent, _samples, clear_of, plan_pieces,
)

MAX_PIECES = 6  # morceaux en corde au plus par tronçon
MIN_PIECE = 10.0  # mm : largeur minimale d'un morceau en corde, à chaque bout du tronçon
_OFF = 0.05  # mm : un plan décalé passe à cette distance au-delà du bord d'un obstacle
_FAR = 1e5
_PAD = 10.0  # mm : les outils de découpe dépassent la pièce de cette marge
SLIVER_VOLUME = 0.5  # mm³ : en dessous, un solide séparé est un éclat du booléen (cf. pipeline.MAX_SLIVER_VOLUME)
_LE_SHARE = 0.05  # part de la corde où l'épaisseur est lue pour situer le bord d'attaque (bord épais)


@dataclass(frozen=True)
class Obstacle:
    """Zone qu'un plan de coupe ne doit pas traverser (vue de dessus, repère de la pièce)."""

    name: str
    x0: float  # étendue en corde
    x1: float
    z0: float  # étendue en envergure (mm depuis l'emplanture)
    z1: float


@dataclass(frozen=True)
class ChordPlane:
    """Plan de coupe x = xa + (z − za)·(xb − xa)/(zb − za) (contient l'axe Y), z depuis l'emplanture."""

    za: float
    zb: float
    xa: float
    xb: float
    shift: float = 0.0  # mm : décalage en corde par rapport au plan nominal (i/k de la corde)
    avoided: tuple[str, ...] = ()  # obstacles qui ont imposé le décalage

    def x_at(self, z: float) -> float:
        return self.xa + (z - self.za) * (self.xb - self.xa) / (self.zb - self.za)

    @property
    def inclination_deg(self) -> float:
        """Inclinaison par rapport à Z (0 : plan vertical)."""
        return math.degrees(math.atan2(abs(self.xb - self.xa), self.zb - self.za))

    def shifted(self, s: float, avoided: tuple[str, ...] = ()) -> "ChordPlane":
        return ChordPlane(self.za, self.zb, self.xa + s, self.xb + s, self.shift + s, avoided)


@dataclass(frozen=True)
class SegmentPlan:
    """Un tronçon en Z [z0, z1] (mm depuis l'emplanture), coupé ou non dans la corde."""

    z0: float
    z1: float
    placements: tuple[Placement, ...]  # un par morceau, x croissant
    planes: tuple[ChordPlane, ...] = ()  # x croissant ; vide : pas de coupe dans la corde
    le_at_min: bool = True  # bord d'attaque du côté des x petits (nommage A, B… depuis le bord d'attaque)
    size: tuple[float, float] | None = None  # largeur × épaisseur de la section la plus encombrante (coupe en corde)

    @property
    def k(self) -> int:
        return len(self.planes) + 1

    def order(self) -> list[int]:
        """Indices des morceaux (x croissant) dans l'ordre du bord d'attaque au bord de fuite."""
        idx = list(range(self.k))
        return idx if self.le_at_min else idx[::-1]


@dataclass(frozen=True)
class LayoutPlan:
    z: PiecePlan  # coupes en Z du planificateur d'empreinte (avant raffinement pour l'inclinaison)
    segments: tuple[SegmentPlan, ...]
    extra_z: bool = False  # tronçons en Z ajoutés pour réduire l'inclinaison d'une coupe dans la corde
    cuts: tuple[float, ...] = field(default=())  # coupes en Z finales (mm depuis l'emplanture)

    @property
    def chord_cut(self) -> bool:
        return any(s.planes for s in self.segments)

    def names(self) -> list[str]:
        """Noms des morceaux, tronçon par tronçon (emplanture → saumon), puis du bord d'attaque au bord de fuite :
        T1, T2 (Z) ; A, B (corde seule) ; T1-A, T1-B… ; un seul morceau : T1."""
        out = []
        multi = len(self.segments) > 1
        for i, seg in enumerate(self.segments, 1):
            if not seg.planes:
                out.append(f"T{i}")
                continue
            for j in range(seg.k):
                letter = chr(ord("A") + j)
                out.append(f"T{i}-{letter}" if multi else letter)
        return out


class _TooSteep(Exception):
    def __init__(self, angle: float, z0: float, z1: float):
        super().__init__(angle, z0, z1)
        self.angle, self.z0, self.z1 = angle, z0, z1


def _hull(pts: np.ndarray) -> Polygon | None:
    if len(pts) < 3:
        return None
    h = MultiPoint(np.ascontiguousarray(np.asarray(pts)[:, :2], dtype=float)).convex_hull
    return h if isinstance(h, Polygon) and h.area > 0 else None


class _Rings:
    """Anneaux (ouverts) de sections convexes mis bout à bout, pour les couper tous d'un coup à une bande par section."""

    def __init__(self, zs: Sequence[float], rings: Sequence[np.ndarray]):
        self.z = np.asarray(zs, dtype=float)
        self.pts = np.concatenate(rings)
        self.sec = np.repeat(np.arange(len(rings)), [len(r) for r in rings])
        starts = np.cumsum([0] + [len(r) for r in rings[:-1]])
        nxt = np.arange(len(self.pts)) + 1
        nxt[starts + np.array([len(r) for r in rings]) - 1] = starts  # dernier sommet d'un anneau → son premier
        self.nxt = nxt

    def clip(self, xl: np.ndarray, xr: np.ndarray) -> np.ndarray:
        """Sommets de chaque (section ∩ bande xl ≤ x ≤ xr de sa section) — ceux de `h.intersection(box(xl, …, xr, …))`
        à l'arrondi près, sans construire de géométrie (le plan de découpe en essaie des milliers) : sommets dans la
        bande et passages des arêtes sur x = xl et x = xr (polygones convexes), dans le désordre (seule leur
        enveloppe convexe sert). Sections où xr ≤ xl : ignorées."""
        ok = (xr > xl)[self.sec]
        x = self.pts[:, 0]
        lo, hi = xl[self.sec], xr[self.sec]
        out = [self.pts[ok & (x >= lo) & (x <= hi)]]
        a, b = self.pts, self.pts[self.nxt]
        for line in (lo, hi):
            cross = ok & ((a[:, 0] - line) * (b[:, 0] - line) < 0)
            if cross.any():
                ac, bc, lc = a[cross], b[cross], line[cross]
                on = ac + ((lc - ac[:, 0]) / (bc[:, 0] - ac[:, 0]))[:, None] * (bc - ac)
                on[:, 0] = lc
                out.append(on)
        return np.concatenate(out)


def _le_at_min(hull: Polygon) -> bool:
    """Le bord d'attaque (bord épais) est-il du côté des x petits ? Épaisseur lue à 5 % de la corde de chaque bout."""
    x0, _, x1, _ = hull.bounds
    d = _LE_SHARE * (x1 - x0)

    def thick(a: float, b: float) -> float:
        g = hull.intersection(box(a, -_FAR, b, _FAR))
        return 0.0 if g.is_empty else g.bounds[3] - g.bounds[1]

    return thick(x0, x0 + d) >= thick(x1 - d, x1)


def _forbidden(plane: ChordPlane, a: float, b: float, obstacles: Sequence[Obstacle]) -> list[tuple[float, float, str]]:
    """Décalages (s_min, s_max, nom), intervalles ouverts, pour lesquels le plan traverse un obstacle sur [a, b]."""
    out = []
    for o in obstacles:
        lo, hi = max(a, o.z0), min(b, o.z1)
        if hi <= lo:
            continue
        xs = (plane.x_at(lo), plane.x_at(hi))
        out.append((o.x0 - max(xs), o.x1 - min(xs), o.name))
    return out


def _chord_segment(
    a: float, b: float, height: float, get: Callable[[list[float]], list[np.ndarray]], bed, margin: float,
    max_overhang: float, obstacles: Sequence[Obstacle],
) -> SegmentPlan:
    zs = _samples(a, b, height)
    sections = [(z, _hull(p)) for z, p in zip(zs, get(zs)) if len(p)]
    sections = [(z, h) for z, h in sections if h is not None]
    if len(sections) < 2:
        raise SegmentError(f"tronçon z = {a:.0f} à {b:.0f} mm : sections introuvables pour la coupe dans la corde")
    (za, ha), (zb, hb) = sections[0], sections[-1]
    lo_a, _, hi_a, _ = ha.bounds
    lo_b, _, hi_b, _ = hb.bounds
    worst = max(sections, key=lambda t: t[1].bounds[2] - t[1].bounds[0])[1]
    size = _extent(np.asarray(worst.exterior.coords))
    le_min = _le_at_min(worst)

    rings = _Rings([z for z, _ in sections], [np.asarray(h.exterior.coords)[:-1, :2] for _, h in sections])

    def footprint(left: ChordPlane | None, right: ChordPlane | None) -> Placement | None:
        far = np.full(len(rings.z), _FAR)
        xl = -far if left is None else left.xa + (rings.z - left.za) * (left.xb - left.xa) / (left.zb - left.za)
        xr = far if right is None else right.xa + (rings.z - right.za) * (right.xb - right.xa) / (right.zb - right.za)
        pts = rings.clip(xl, xr)
        if not len(pts):
            return None
        return fit_on_bed(pts, bed, margin)

    def inside(p: ChordPlane, prev: ChordPlane | None) -> bool:
        if not (lo_a + MIN_PIECE <= p.xa <= hi_a - MIN_PIECE and lo_b + MIN_PIECE <= p.xb <= hi_b - MIN_PIECE):
            return False
        return prev is None or (p.xa - prev.xa >= MIN_PIECE and p.xb - prev.xb >= MIN_PIECE)

    def make(planes: list[ChordPlane], places: list[Placement]) -> SegmentPlan:
        return SegmentPlan(a, b, tuple(places), tuple(planes), le_min, size)

    # plans parallèles (verticaux d'abord) : le moins de morceaux, puis la plus faible inclinaison
    best = None
    for sigma in _slopes(sections, za, zb, max_overhang):
        got = _parallel(sigma, sections, za, zb, footprint, obstacles, a, b)
        if got is not None and (best is None or len(got[0]) < len(best[0])):
            best = got
            if len(got[0]) == 1:
                break
    if best is not None:
        return make(*best)

    # repli : plans en éventail, aux points i/k de la corde aux deux bouts du tronçon
    steep: float | None = None
    blocked: set[str] = set()
    for k in range(2, MAX_PIECES + 1):
        nominal = [
            ChordPlane(za, zb, lo_a + i / k * (hi_a - lo_a), lo_b + i / k * (hi_b - lo_b)) for i in range(1, k)
        ]
        angle = max(p.inclination_deg for p in nominal)
        if angle > max_overhang + 1e-9:
            steep = angle if steep is None else min(steep, angle)
            continue
        bounds = [None, *nominal, None]
        nominal_fit = [footprint(l, r) for l, r in zip(bounds, bounds[1:])]
        if not all(pl is not None and pl.fits for pl in nominal_fit):
            continue
        chosen: list[ChordPlane] = []
        places: list[Placement] = []
        for i, p in enumerate(nominal):
            forb = _forbidden(p, a, b, obstacles)
            cands = {0.0}
            for lo, hi, _ in forb:
                cands |= {lo - _OFF, hi + _OFF}
            free = sorted((s for s in cands if not any(lo < s < hi for lo, hi, _ in forb)), key=lambda s: (abs(s), s))
            on_nominal = tuple(n for lo, hi, n in forb if lo < 0 < hi)
            prev = chosen[-1] if chosen else None
            for s in free:
                q = p.shifted(s, on_nominal if s else ())
                if not inside(q, prev):
                    continue
                left = footprint(prev, q)
                if left is None or not left.fits:
                    continue
                right = footprint(q, None) if i == len(nominal) - 1 else None
                if right is not None and not right.fits:
                    continue
                chosen.append(q)
                places.append(left)
                if right is not None:
                    places.append(right)
                break
            else:
                blocked |= {n for _, _, n in forb}
                break
        if len(chosen) == len(nominal):
            return make(chosen, places)
    if blocked:
        raise SegmentError(
            f"coupe dans la corde impossible (z = {a:.0f} à {b:.0f} mm) : aucune position libre pour le plan de "
            f"coupe, obstacles : {', '.join(sorted(blocked))} (longerons/gaines avec manchon et bande, baies de "
            "servo) — déplacer un obstacle ou choisir une imprimante plus grande"
        )
    if steep is not None:
        raise _TooSteep(steep, a, b)
    raise SegmentError(
        f"coupe dans la corde impossible (z = {a:.0f} à {b:.0f} mm) : la section {size[0]:.0f} × {size[1]:.0f} mm "
        f"ne tient pas sur le plateau même en {MAX_PIECES} morceaux"
    )


_BISECT = 12  # itérations de dichotomie (position d'un plan, largeur maximale des morceaux)


def _slopes(sections, za: float, zb: float, max_overhang: float) -> list[float]:
    """Pentes dx/dz candidates des plans parallèles, de la plus faible à la plus forte : verticale, puis celles du
    bord d'attaque, du bord de fuite et du milieu de la corde (flèche), si l'inclinaison reste ≤ surplomb."""
    (_, ha), (_, hb) = sections[0], sections[-1]
    dz = zb - za
    lo_a, _, hi_a, _ = ha.bounds
    lo_b, _, hi_b, _ = hb.bounds
    cands = [0.0, (lo_b - lo_a) / dz, (hi_b - hi_a) / dz, ((lo_b + hi_b) - (lo_a + hi_a)) / (2 * dz)]
    limit = math.tan(math.radians(max_overhang)) + 1e-9
    out: list[float] = []
    for c in sorted(cands, key=abs):
        if abs(c) <= limit and not any(abs(c - o) < 1e-6 for o in out):
            out.append(c)
    return out


def _parallel(
    sigma: float, sections, za: float, zb: float, footprint, obstacles: Sequence[Obstacle], a: float, b: float,
) -> tuple[list[ChordPlane], list[Placement]] | None:
    """Plans parallèles de pente `sigma` (x = u + sigma·(z − za)). Positions nominales, sans obstacle : le moins de
    morceaux qui tiennent sur le plateau, puis la plus petite largeur maximale (morceaux équilibrés), placés du bord
    x petit vers x grand. Chaque plan est ensuite décalé vers la position libre la plus proche hors des obstacles, si
    les morceaux tiennent encore ; sinon les plans sont replacés au plus loin, obstacles compris. Chaque plan
    traverse toutes les sections du tronçon à MIN_PIECE au moins de leurs bords. None : pas de solution à
    MAX_PIECES morceaux au plus."""
    dz = zb - za
    u_lo = max(h.bounds[0] - sigma * (z - za) for z, h in sections) + MIN_PIECE
    u_hi = min(h.bounds[2] - sigma * (z - za) for z, h in sections) - MIN_PIECE
    if u_hi < u_lo:
        return None
    l_u = min(h.bounds[0] - sigma * (z - za) for z, h in sections)
    r_u = max(h.bounds[2] - sigma * (z - za) for z, h in sections)
    span_u = r_u - l_u

    def plane(u: float, shift: float = 0.0, avoided: tuple[str, ...] = ()) -> ChordPlane:
        return ChordPlane(za, zb, u, u + sigma * dz, shift, avoided)

    memo: dict[tuple, Placement | None] = {}

    def fit(lu: float | None, ru: float | None) -> Placement | None:
        key = (lu, ru)
        if key not in memo:
            pl = footprint(None if lu is None else plane(lu), None if ru is None else plane(ru))
            memo[key] = pl if pl is not None and pl.fits else None
        return memo[key]

    def bounds(prev: float | None, cap: float) -> tuple[float, float]:
        if prev is None:
            return u_lo, min(u_hi, l_u + cap)
        return max(u_lo, prev + MIN_PIECE), min(u_hi, prev + cap)

    def farthest(prev: float | None, lo: float, hi: float) -> float | None:
        """Plus grande position dans [lo, hi] où le morceau à gauche tient (dichotomie)."""
        if hi < lo or fit(prev, lo) is None:
            return None
        if fit(prev, hi) is not None:
            return hi
        good, bad = lo, hi
        for _ in range(_BISECT):
            mid = 0.5 * (good + bad)
            good, bad = (mid, bad) if fit(prev, mid) is not None else (good, mid)
        return good

    def nominal(cap: float) -> list[float] | None:
        """Plans au plus loin, morceaux d'au plus `cap` de large, sans obstacle."""
        us: list[float] = []
        prev: float | None = None
        while not (us and r_u - prev <= cap + 1e-9 and fit(prev, None) is not None):
            if len(us) + 1 >= MAX_PIECES:
                return None
            u = farthest(prev, *bounds(prev, cap))
            if u is None:
                return None
            us.append(u)
            prev = u
        return us

    def avoid(targets: list[float] | None) -> tuple[list[ChordPlane], list[Placement]] | None:
        """Plans aux positions `targets` décalées hors des obstacles ; None : au plus loin, obstacles compris."""
        planes: list[ChordPlane] = []
        places: list[Placement] = []
        prev: float | None = None
        while True:
            if planes and (targets is None or len(planes) == len(targets)):
                last = fit(prev, None)
                if last is not None:
                    return planes, places + [last]
                if targets is not None:
                    return None
            if len(planes) + 1 >= MAX_PIECES:
                return None
            lo, hi = bounds(prev, span_u)
            target = targets[len(planes)] if targets is not None else farthest(prev, lo, hi)
            if target is None:
                return None
            forb = _forbidden(plane(target), a, b, obstacles)
            cands = {0.0}
            for f_lo, f_hi, _ in forb:
                cands |= {f_lo - _OFF, f_hi + _OFF}
            free = sorted(
                (s for s in cands if not any(f_lo < s < f_hi for f_lo, f_hi, _ in forb)), key=lambda s: (abs(s), s)
            )
            on_target = tuple(n for f_lo, f_hi, n in forb if f_lo < 0 < f_hi)
            for s in free:
                u = target + s
                if not lo <= u <= hi:
                    continue
                left = fit(prev, u)
                if left is None:
                    continue
                planes.append(plane(u, s, on_target if s else ()))
                places.append(left)
                prev = u
                break
            else:
                return None

    us = nominal(span_u)
    if us is None:
        return None
    good, bad = span_u, 0.0
    for _ in range(_BISECT):  # largeur maximale la plus petite au même nombre de morceaux
        mid = 0.5 * (good + bad)
        got = nominal(mid)
        if got is not None and len(got) <= len(us):
            us, good = got, mid
        else:
            bad = mid
    return avoid(us) or avoid(None)


def _steep_message(e: _TooSteep, max_overhang: float) -> str:
    return (
        f"coupe dans la corde inclinée de {e.angle:.0f}° par rapport à la verticale (tronçon z = {e.z0:.0f} à "
        f"{e.z1:.0f} mm, flèche ou effilement) > surplomb max {max_overhang:g}° de la matière : raccourcir les "
        "tronçons en Z (segment.cuts_at, segment.max_height) ou choisir une imprimante plus grande"
    )


def plan_layout(
    height: float, max_height: float, sections: Sections, bed: tuple[float, float], margin: float = 0.0,
    cuts_at: list[float] | None = None, chord: str = "auto", max_overhang_deg: float = 45.0,
    obstacles: Sequence[Obstacle] = (), off_label: str = "segment.chord: off", critical: Sequence[float] = (),
) -> LayoutPlan:
    """Plan de découpe complet, sans booléen : coupes en Z (`plan_pieces`), puis coupe dans la corde de chaque
    tronçon qui ne tient pas sur le plateau (k minimal, plans i/k de la corde, inclinaison ≤ surplomb, décalés hors
    des obstacles). Si les plans sont trop inclinés, le tronçon est d'abord redécoupé en Z (sauf coupes imposées),
    puis SegmentError. `chord = "off"` : SegmentError si un tronçon ne tient pas. `critical` : plans z = cte (mm depuis
    l'emplanture) dont toute coupe en Z reste écartée (`segment.plan_pieces`, CUT_CLEARANCE)."""
    cache: dict[float, np.ndarray] = {}

    def get(zs: list[float]) -> list[np.ndarray]:
        missing = sorted({z for z in zs if z not in cache})
        if missing:
            cache.update(zip(missing, sections(missing)))
        return [cache[z] for z in zs]

    crit = sorted(float(c) for c in critical if 0.0 < c < height)
    zplan = plan_pieces(height, max_height, get, bed, margin, cuts_at, crit)
    bounds = [0.0, *zplan.cuts, float(height)]

    def plain(a: float, b: float) -> Placement:
        pts = [p for p in get(_samples(a, b, height)) if len(p)]
        return fit_on_bed(np.concatenate(pts), bed, margin) if pts else Placement(0.0, True, 0.0, (0.0, 0.0))

    segs: list[SegmentPlan] = []
    extra = False
    for (a, b), pl in zip(zip(bounds, bounds[1:]), zplan.placements):
        if pl.fits:
            segs.append(SegmentPlan(a, b, (pl,)))
            continue
        if chord == "off":
            w, d = pl.size
            raise SegmentError(
                f"le tronçon z = {a:.0f} à {b:.0f} mm ({w:.0f} × {d:.0f} mm au mieux) ne tient pas sur le plateau "
                f"({bed[0] - 2 * margin:.0f} × {bed[1] - 2 * margin:.0f} mm utiles) même tourné, et la coupe dans "
                f"la corde est désactivée ({off_label}) — activer segment.chord: auto ou choisir une imprimante "
                "plus grande"
            )
        try:
            segs.append(_chord_segment(a, b, height, get, bed, margin, max_overhang_deg, obstacles))
            continue
        except _TooSteep as exc:
            if cuts_at is not None:
                raise SegmentError(_steep_message(exc, max_overhang_deg)) from None
            first = exc
        m, done = 2, None
        while (b - a) / m >= MIN_SEGMENT - 1e-9 and done is None:
            sub = [a + (b - a) * i / m for i in range(m + 1)]
            for i in range(1, m):  # coupes ajoutées, elles aussi écartées des plans critiques
                z = clear_of(sub[i], crit, sub[i - 1] + MIN_SEGMENT - 1e-9, sub[i + 1] - MIN_SEGMENT + 1e-9)
                sub[i] = sub[i] if z is None else z
            try:
                part = []
                for sa, sb in zip(sub, sub[1:]):
                    p = plain(sa, sb)
                    part.append(
                        SegmentPlan(sa, sb, (p,)) if p.fits
                        else _chord_segment(sa, sb, height, get, bed, margin, max_overhang_deg, obstacles)
                    )
                done = part
            except _TooSteep:
                m += 1
        if done is None:
            raise SegmentError(_steep_message(first, max_overhang_deg))
        segs += done
        extra = True
    cuts = tuple(s.z0 for s in segs[1:])
    return LayoutPlan(zplan, tuple(segs), extra, cuts)


# --- obstacles -------------------------------------------------------------------------------------------------


def bore_obstacles(bores, zmin: float, zmax: float) -> list[Obstacle]:
    """Longerons, gaines et passages reliés parallèles à Z : trou + manchon + bande, sur leur étendue réelle."""
    from beaver.features.bores import bore_extent

    out = []
    for b in bores:
        lo, hi = bore_extent(b, zmin, zmax)
        lo, hi = max(lo, zmin), min(hi, zmax)
        r = b.radius + b.sleeve + b.strip
        out.append(Obstacle(b.name, b.x - r, b.x + r, lo - zmin, hi - zmin))
    return out


def bay_obstacles(bays, zmin: float, strip: float) -> list[Obstacle]:
    """Baies de servo : poche (rampes comprises) + fente de renfort + bande."""
    from beaver.features.servo import pocket_profile

    out = []
    for bay in bays:
        d = bay.reach + strip
        lo, _, hi, _ = pocket_profile(bay).bounds
        out.append(Obstacle(bay.name, bay.x0 - d, bay.x1 + d, lo - d - zmin, hi + d - zmin))
    return out


# --- géométrie --------------------------------------------------------------------------------------------------


def _prism(left, right, z_lo: float, z_hi: float, bb) -> Solid:
    """Région entre deux plans (fonctions z → x, None : au-delà de la pièce), z_lo ≤ z ≤ z_hi, toute l'épaisseur."""
    x_min, x_max = bb.min.X - _PAD, bb.max.X + _PAD
    y0, depth = bb.min.Y - _PAD, bb.max.Y - bb.min.Y + 2 * _PAD
    xl = (lambda z: x_min) if left is None else left
    xr = (lambda z: x_max) if right is None else right
    ring = [Vector(xl(z_lo), y0, z_lo), Vector(xr(z_lo), y0, z_lo), Vector(xr(z_hi), y0, z_hi), Vector(xl(z_hi), y0, z_hi)]
    return extrude(Face(Wire.make_polygon(ring, close=True)), amount=depth, dir=Vector(0, 1, 0))


def cut_segment(
    shape: Shape, seg: SegmentPlan, z_root: float, first: bool, last: bool, label: str = "",
) -> list[Solid]:
    """Morceaux du tronçon `seg` (x croissant) découpés dans `shape` (repère de la pièce, emplanture en z_root),
    chacun ramené à z = 0 en bas du tronçon comme `split_part`. SegmentError si un morceau n'est pas un solide
    unique valide (éclats < SLIVER_VOLUME ignorés)."""
    bb = shape.bounding_box(optimal=True)
    z_lo = bb.min.Z - _PAD if first else z_root + seg.z0
    z_hi = bb.max.Z + _PAD if last else z_root + seg.z1
    planes = [None, *[(lambda z, p=p: p.x_at(z - z_root)) for p in seg.planes], None]
    out = []
    for j, (left, right) in enumerate(zip(planes, planes[1:])):
        name = f"{label} morceau {j + 1}".strip()
        try:
            common = shape & _prism(left, right, z_lo, z_hi, bb)
            solids = [] if common is None else list(common.solids())
        except Exception as exc:  # erreurs OCC
            raise CutFailure(f"{name} : booléen de la coupe en échec ({type(exc).__name__})") from exc
        kept = [s for s in solids if s.volume > SLIVER_VOLUME]
        if not kept:  # booléen vide : faces coïncidentes avec un plan de coupe (`segment.cut_chain` réessaie)
            raise CutFailure(f"{name} : intersection vide")
        if len(kept) != 1:
            raise SegmentError(f"{name} : {len(kept)} solides obtenus au lieu de 1 après la coupe dans la corde")
        if not kept[0].is_valid:
            raise SegmentError(f"{name} : solide invalide après la coupe dans la corde")
        out.append(Pos(0, 0, -(z_root + seg.z0)) * kept[0])
    return out


def cut_lines(seg: SegmentPlan, j: int, width: float) -> Callable[[float], Polygon | None]:
    """Bandes (vue en couche, repère du morceau j, z depuis le bas du tronçon) de largeur 2·width autour des faces
    coupées du morceau j : la face d'origine y est comparée au morceau avec une tolérance élargie à la largeur d'une
    fente de nervure (fentes qui longent la face et y débouchent), voir validate.check_skin."""
    sides = [p for p in (seg.planes[j - 1] if j > 0 else None, seg.planes[j] if j < len(seg.planes) else None) if p]

    def strips(z: float):
        geoms = [box(p.x_at(z + seg.z0) - width, -_FAR, p.x_at(z + seg.z0) + width, _FAR) for p in sides]
        if not geoms:
            return None
        g = geoms[0]
        for other in geoms[1:]:
            g = g.union(other)
        return g

    return strips
