"""SBAR transversales et inclinées (spec découpe §6) : trou en goutte d'eau le long d'un axe quelconque.

Un longeron dont l'axe s'écarte de Z de plus que `passages.max_tilt_deg` n'est pas un trou vertical (`bores.py`) :
c'est un « tube ». Son trou est un prisme convexe le long de l'axe, de section en goutte d'eau (pointe vers +Z dans
le plan qui contient l'axe et Z, flancs au surplomb max de la matière) : le plafond du trou est imprimable.

Vase mode : dans chaque couche, la section du trou doit rejoindre le contour sans couper la couche en deux. La
décision est prise couche par couche (échantillons en z), sur l'enveloppe de la peau (contours extérieurs remplis)
limitée au morceau de la coupe dans la corde (`Region`) — une face de coupe, la face d'un autre corps d'aile ou la
peau sont des bords ; l'emplanture, le saumon et les coupes en Z (faces horizontales) ne relient aucune couche.
Options par couche : pas de fente (le trou débouche franchement), ou une fente de largeur `slit_width` du trou vers
l'extérieur, dans l'une des familles :

- « le long de l'axe » (±u, u = axe projeté sur l'horizontale) : plan vertical qui contient l'axe ;
- « en travers » (±n, n = horizontale ⊥ u) : plan qui contient l'axe et n si l'axe est à moins du surplomb max de
  Z (parois inclinées de l'angle de l'axe, imprimables) ; sinon (axe presque horizontal, ce plan serait presque
  horizontal) plan vertical ⊥ u, au milieu du trou.

Une option est admise dans une couche si le trou (+ fente) rejoint l'extérieur par une bouche plus large que
`min_slit_width`, si la couche reste d'un seul tenant (coupe gonflée d'une demi-largeur de ligne : une paroi plus
fine qu'une ligne compte comme coupée) et si la bouche d'une fente sur la peau reste ≤ `validation.max_mouth_width`.
Choix : le moins de changements d'option sur la hauteur, puis le moins de couches avec fente, puis les fentes les
plus courtes. Aucune option admise dans une couche : `TubeError` (impossible en vase mode, message précis).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np
import trimesh
from build123d import Align, Box, Face, Location, Plane, Solid, Vector, Wire, extrude
from shapely.errors import ShapelyError
from shapely.geometry import LineString, MultiPoint, Point, Polygon
from shapely.ops import unary_union

from aerorib.classify import detect_spar
from aerorib.config import Config
from aerorib.features.bores import BoreError

OPEN_END = 1.0  # mm : extrémité à moins de cela de l'extérieur, le long de l'axe → le trou débouche
OPEN_REACH = 10.0  # mm : prolongement de l'axe au-delà d'une extrémité débouchante
FACETS = 64  # facettes de la goutte d'eau (polygone circonscrit au cercle)
_FAR = 1e4
# couches de l'analyse : pas = hauteur du trou / _MAX_SAMPLES, borné à [_MIN_STEP, _MAX_STEP] mm (au-delà de 100 mm de
# hauteur, plus de _MAX_SAMPLES échantillons : 2 par mm)
_MIN_STEP, _MAX_STEP, _MAX_SAMPLES = 0.05, 0.5, 200
Z = np.array([0.0, 0.0, 1.0])


class TubeError(BoreError):
    """SBAR transversale impossible (message en français)."""


# erreurs de calcul géométrique (shapely, trimesh, numpy) traduites en TubeError
GEOMETRY_ERRORS = (ShapelyError, ValueError, IndexError, ZeroDivisionError, FloatingPointError)


def _unit(v) -> np.ndarray:
    v = np.asarray(v, dtype=float)
    return v / np.linalg.norm(v)


def tilt_deg(axis) -> float:
    """Angle (°) entre l'axe et Z."""
    return math.degrees(math.acos(min(1.0, abs(float(_unit(axis)[2])))))


def is_transversal(solid: Solid, cfg: Config) -> bool:
    """Longeron transversal ou incliné : axe à plus de `passages.max_tilt_deg` de Z (sinon trou vertical)."""
    info = detect_spar(solid)
    return info is not None and tilt_deg(info.axis_dir) > cfg.passages.max_tilt_deg


def teardrop(radius: float, tilt: float, overhang: float, facets: int = FACETS) -> np.ndarray:
    """Section du trou dans le plan ⊥ axe, coordonnées (t, s) : t = projection de +Z (pointe), s = latéral.

    Polygone circonscrit au cercle de rayon `radius` (le longeron y entre toujours). Une facette de normale sortante
    (du trou vers la matière) d'angle φ par rapport à t a un surplomb asin(cos φ · sin(tilt)) ; il est ≤ `overhang`
    pour |φ| ≥ φ0 = 90° − β, sin β = sin(overhang) / sin(tilt). Les facettes ont leurs normales de φ0 à 360° − φ0 ;
    les deux flancs se coupent à la pointe, en t = radius / sin β. Si sin β ≥ 1 (axe à moins du surplomb max de Z),
    pas de pointe : polygone circonscrit régulier."""
    sin_b = math.sin(math.radians(overhang)) / max(math.sin(math.radians(tilt)), 1e-12)
    if sin_b >= 1 - 1e-9:
        ang = (np.arange(facets) + 0.5) * 2 * math.pi / facets
        r = radius / math.cos(math.pi / facets)
        return np.column_stack([r * np.cos(ang), r * np.sin(ang)])
    phi0 = math.pi / 2 - math.asin(sin_b)
    span = 2 * math.pi - 2 * phi0
    n = max(2, math.ceil(span / (2 * math.pi / facets)))
    normals = phi0 + np.arange(n + 1) * span / n  # φ0 … 2π − φ0
    mids = (normals[:-1] + normals[1:]) / 2
    r = radius / math.cos(span / n / 2)
    tip = radius / math.cos(phi0)
    return np.asarray([(tip, 0.0)] + [(r * math.cos(a), r * math.sin(a)) for a in mids])


HalfSpace = tuple[tuple[float, float, float], float]  # (c, k) : c·p + k ≥ 0


@dataclass(frozen=True, eq=False)
class Prism:
    """Prisme convexe : polygone plan convexe `ring0` (M × 3) translaté en `ring1`, limité aux demi-espaces horizontaux
    `clips` (bouts fermés d'un tube incliné : fond et plafond horizontaux)."""

    ring0: np.ndarray
    ring1: np.ndarray
    clips: tuple[HalfSpace, ...] = ()

    def zrange(self) -> tuple[float, float]:
        z = self.vertices[:, 2]
        lo, hi = float(z.min()), float(z.max())
        for c, k in self.clips:
            if c[2] > 0:
                lo = max(lo, -k / c[2])
            elif c[2] < 0:
                hi = min(hi, -k / c[2])
        return lo, hi

    @property
    def vertices(self) -> np.ndarray:
        return np.vstack([self.ring0, self.ring1])

    def section(self, origin, ex, ey) -> Polygon | None:
        """Intersection avec le plan (origin, ex, ey), coordonnées ((p − origin)·ex, (p − origin)·ey) : enveloppe
        convexe des intersections des arêtes avec le plan (exacte pour un polyèdre convexe)."""
        origin, ex, ey = (np.asarray(a, dtype=float) for a in (origin, ex, ey))
        pts = self.vertices
        m = len(self.ring0)
        d = (pts - origin) @ np.cross(ex, ey)
        i = np.arange(m)
        a = np.concatenate([i, i + m, i])
        b = np.concatenate([(i + 1) % m, (i + 1) % m + m, i + m])
        cross = d[a] * d[b] < 0
        t = (d[a[cross]] / (d[a[cross]] - d[b[cross]]))[:, None]
        hits = np.vstack([pts[np.abs(d) < 1e-9], pts[a[cross]] + t * (pts[b[cross]] - pts[a[cross]])])
        if len(hits) < 3:
            return None
        rel = hits - origin
        hull = MultiPoint(np.column_stack([rel @ ex, rel @ ey])).convex_hull
        if not isinstance(hull, Polygon) or hull.area <= 1e-9:
            return None
        if not self.clips:
            return hull
        pts = np.asarray(hull.exterior.coords)[:-1]
        for c, k in self.clips:
            c = np.asarray(c)
            pts = _clip(pts, float(c @ ex), float(c @ ey), float(c @ origin + k))
            if len(pts) < 3:
                return None
        poly = Polygon(pts)
        return poly if poly.area > 1e-9 else None

    def layer(self, z: float) -> Polygon | None:
        return self.section((0.0, 0.0, z), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0))

    def solid(self) -> Solid:
        face = Face(Wire.make_polygon([Vector(*map(float, p)) for p in self.ring0], close=True))
        v = self.ring1[0] - self.ring0[0]
        out = extrude(face, amount=float(np.linalg.norm(v)), dir=Vector(*map(float, _unit(v))))
        if not self.clips:
            return out
        lo, hi = self.zrange()
        pts = self.vertices
        (x0, y0), (x1, y1) = pts[:, :2].min(axis=0) - 1, pts[:, :2].max(axis=0) + 1
        keep = Box(x1 - x0, y1 - y0, hi - lo, align=(Align.MIN, Align.MIN, Align.MIN)).moved(Location((x0, y0, lo)))
        out = (out & keep).solids()[0]
        size = 2 * float(np.linalg.norm(pts.max(axis=0) - pts.min(axis=0))) + 10
        center = (pts.max(axis=0) + pts.min(axis=0)) / 2
        for c, k in self.clips:  # demi-espaces non horizontaux (face verticale d'un bout fermé)
            c = np.asarray(c, dtype=float)
            if abs(c[2]) > 1e-12:
                continue
            n = c / np.linalg.norm(c)
            q = center - n * ((center @ c + k) / np.linalg.norm(c))  # projection du centre sur le plan c·p + k = 0
            half = Box(size, size, size, align=(Align.CENTER, Align.CENTER, Align.MIN))  # côté c·p + k ≥ 0
            out = (out & half.moved(Plane(origin=tuple(map(float, q)), z_dir=tuple(map(float, n))).location)).solids()[0]
        return out


# --- morceaux de la coupe dans la corde --------------------------------------------------------------------------


def _clip(pts: np.ndarray, a: float, b: float, c: float) -> np.ndarray:
    """Polygone convexe (N × 2) limité au demi-plan a·x + b·y + c ≥ 0 (Sutherland–Hodgman)."""
    out = []
    n = len(pts)
    for i in range(n):
        p, q = pts[i], pts[(i + 1) % n]
        fp, fq = a * p[0] + b * p[1] + c, a * q[0] + b * q[1] + c
        if fp >= 0:
            out.append(p)
        if fp * fq < 0:
            out.append(p + (q - p) * fp / (fp - fq))
    return np.asarray(out).reshape(-1, 2)


@dataclass(frozen=True)
class Region:
    """Morceau d'une coupe dans la corde : intersection de demi-espaces (repère de la pièce, z absolu)."""

    halfspaces: tuple[HalfSpace, ...] = ()
    label: str = ""

    def in_plane(self, origin, ex, ey, reach: float) -> Polygon | None:
        """Le morceau dans le plan (origin, ex, ey), limité à ±reach."""
        origin, ex, ey = (np.asarray(v, dtype=float) for v in (origin, ex, ey))
        pts = np.array([(-reach, -reach), (reach, -reach), (reach, reach), (-reach, reach)])
        for c, k in self.halfspaces:
            c = np.asarray(c)
            pts = _clip(pts, float(c @ ex), float(c @ ey), float(c @ origin + k))
            if len(pts) < 3:
                return None
        poly = Polygon(pts)
        return poly if poly.is_valid and poly.area > 1e-9 else None

    def layer(self, z: float, reach: float) -> Polygon | None:
        return self.in_plane((0.0, 0.0, z), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), reach)


def band(z_lo: float | None, z_hi: float | None) -> tuple[HalfSpace, ...]:
    out = []
    if z_lo is not None:
        out.append(((0.0, 0.0, 1.0), -z_lo))
    if z_hi is not None:
        out.append(((0.0, 0.0, -1.0), z_hi))
    return tuple(out)


def beyond(x0: float, slope: float, side: int) -> HalfSpace:
    """Côté `side` (+1 : x ≥, −1 : x ≤) du plan x = x0 + slope·z (contient l'axe Y)."""
    return ((float(side), 0.0, -side * slope), -side * x0)


# --- tube ------------------------------------------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class Tube:
    name: str
    diameter: float  # Ø du longeron
    radius: float  # rayon du trou (Ø/2 + jeu)
    sleeve: float
    strip: float
    slit: float  # largeur de la fente vers l'extérieur
    tilt: float  # angle (°) entre l'axe et Z
    cable: bool
    start: tuple[float, float, float]  # axe du trou (extrémités prolongées : jeu, ou OPEN_REACH si débouchant)
    end: tuple[float, float, float]
    open_start: bool
    open_end: bool
    hole: Prism
    zone: Prism  # trou + manchon + demi-épaisseur de plaque : sans nervure
    slits: tuple[Prism, ...] = ()
    slit_zones: tuple[Prism, ...] = ()  # fentes + bande (strip) + demi-épaisseur de plaque : sans nervure

    @property
    def axis(self) -> np.ndarray:
        """Direction de l'axe (z croissant)."""
        return _unit(np.subtract(self.end, self.start))

    @property
    def debouching(self) -> bool:
        """Chaque couche du trou rejoint le contour sans fente."""
        return not self.slits

    def tools(self) -> list[Solid]:
        return [self.hole.solid(), *(s.solid() for s in self.slits)]

    def section(self, z: float) -> list[Polygon]:
        """Trou et fentes dans la couche z (repère XY) : mêmes prismes que les outils de coupe."""
        return [g for g in (p.layer(z) for p in (self.hole, *self.slits)) if g is not None]

    def zones_2d(self, c: float, d0: float, d1: float, zmin: float) -> list[Polygon]:
        """Zones sans nervure dans le repère (s, y) d'une plaque (x = c + s·d0, z = zmin + s·d1)."""
        out = []
        for p in (self.zone, *self.slit_zones):
            g = p.section((c, 0.0, zmin), (d0, 0.0, d1), (0.0, 1.0, 0.0))
            if g is not None:
                out.append(g)
        return out


def _prism(start, end, profile: np.ndarray, e_t, e_s, clips: tuple[HalfSpace, ...] = ()) -> Prism:
    ring0 = np.asarray(start) + profile[:, :1] * e_t + profile[:, 1:] * e_s
    return Prism(ring0, ring0 + (np.asarray(end) - np.asarray(start)), clips)


@dataclass(frozen=True)
class _Family:
    """Fente dans le plan (origin, ex, ey), vers ±ex, épaisseur selon `normal`."""

    name: str
    origin: np.ndarray
    ex: np.ndarray
    ey: np.ndarray

    @property
    def normal(self) -> np.ndarray:
        return _unit(np.cross(self.ex, self.ey))

    def slab(self, poly: Polygon, half: float) -> Prism:
        coords = np.asarray(poly.exterior.coords)[:-1]
        pts = self.origin + coords[:, :1] * self.ex + coords[:, 1:] * self.ey - self.normal * half
        return Prism(pts, pts + 2 * half * self.normal)

    def shadow(self, hole: Prism, region: Region, side: int, reach: float) -> Polygon | None:
        """Le trou dans le plan, prolongé vers side·ex jusqu'à `reach`, limité au morceau `region`."""
        f = hole.section(self.origin, self.ex, self.ey)
        q = region.in_plane(self.origin, self.ex, self.ey, reach)
        if f is None or q is None:
            return None
        f = f.intersection(q)
        if f.is_empty or f.area <= 1e-9:
            return None
        pts = np.asarray(f.convex_hull.exterior.coords)
        hull = MultiPoint(np.vstack([pts, pts + (side * reach, 0.0)])).convex_hull.intersection(q)
        return hull if isinstance(hull, Polygon) and hull.area > 1e-9 else None


@dataclass(frozen=True, eq=False)
class _Option:
    """Fente vers `side` (±1 selon l'axe `ex` de chaque famille), en morceaux (famille, z_bas, z_haut ; None : ouvert)."""

    pieces: tuple[tuple[_Family, float | None, float | None], ...]
    side: int

    def shadows(self, hole: Prism, region: Region, reach: float, lo: float | None = None, hi: float | None = None):
        out = []
        for fam, a, b in self.pieces:
            za = lo if a is None else (a if lo is None else max(a, lo))
            zb = hi if b is None else (b if hi is None else min(b, hi))
            if za is not None and zb is not None and zb <= za:
                continue
            sh = fam.shadow(hole, Region((*region.halfspaces, *band(za, zb))), self.side, reach)
            if sh is not None:
                out.append((fam, sh))
        return out

    def slabs(self, hole: Prism, region: Region, reach: float, half: float) -> list[Prism]:
        return [fam.slab(sh, half) for fam, sh in self.shadows(hole, region, reach)]


def _envelopes(mesh: trimesh.Trimesh, zs: Sequence[float], close: float) -> list:
    """Enveloppe de la peau dans chaque couche z : contours extérieurs remplis, fermés de `close` (le trou du
    longeron dans la CAO, débouchant ou non, n'est pas un bord de la pièce)."""
    zs = [float(z) for z in zs]
    if not zs:
        return []
    cuts = mesh.section_multiplane(plane_origin=[0, 0, 0], plane_normal=[0, 0, 1], heights=zs)
    out = []
    for c in cuts:
        polys = [Polygon(p.exterior) for p in c.polygons_full] if c is not None else []
        env = unary_union(polys) if polys else Polygon()
        out.append(env.buffer(close).buffer(-close) if not env.is_empty else env)
    return out


def _parts(geom) -> list[Polygon]:
    return [g for g in getattr(geom, "geoms", [geom]) if isinstance(g, Polygon) and not g.is_empty]


@dataclass(frozen=True)
class _Rules:
    line_width: float
    min_slit: float
    max_mouth: float


def _admits(env: Polygon, hole: Polygon, slit: Polygon | None, rules: _Rules) -> float | None:
    """Longueur de fente dans la matière si l'option est admise dans la couche (0 sans fente), sinon None."""
    cut = hole if slit is None else hole.union(slit)
    # le trou (+ fente) rejoint l'extérieur par une bouche assez large pour le slicer
    if env.contains(cut.buffer(-rules.min_slit / 2 * 1.1)):
        return None
    # couche d'un seul tenant, une paroi plus fine qu'une ligne comptant comme coupée
    rest = [p for p in _parts(env.difference(cut.buffer(rules.line_width / 2))) if p.area > rules.line_width**2]
    if len(rest) > 1:
        return None
    if slit is None:
        return 0.0
    extra = slit.difference(hole)
    mouths = env.exterior.intersection(extra) if isinstance(env, Polygon) else env.boundary.intersection(extra)
    for m in getattr(mouths, "geoms", [mouths]):
        if m.length > rules.max_mouth:
            return None
    return env.intersection(extra).area + 1e-6


def _cause(env: Polygon, hole: Polygon, rules: "_Rules", axis_ends, z_root: float) -> str:
    """Suite du message d'échec d'une couche : la cause et quoi faire. `axis_ends` : (bout de l'axe, débouchant ?)."""
    rest = [p for p in _parts(env.difference(hole)) if p.area > rules.line_width**2]
    if len(rest) > 1:  # le trou seul coupe la couche : il débouche des deux côtés
        opened = [p for p, o in axis_ends if o]
        if len(opened) == 2:
            where = " ou ".join(f"le bout à x = {p[0]:.1f} mm, z = {p[2] - z_root:.1f} mm" for p in opened)
            return (" : le trou débouche aux deux bouts dans la même couche — impossible en vase mode. Fermer l'un "
                    f"des deux bouts dans la pièce (raccourcir la SBAR : {where} ; l'un des deux suffit), ou "
                    "l'incliner pour que ses bouches soient à des hauteurs différentes")
        return (" : le trou traverse la couche de part en part (il sort de la peau des deux côtés) — impossible en "
                "vase mode. Déplacer la SBAR vers le milieu de l'épaisseur ou réduire son diamètre")
    return (" : la paroi entre le trou et la peau est plus fine qu'une ligne, ou la bouche d'une fente dépasserait "
            "validation.max_mouth_width — impossible en vase mode. Éloigner la SBAR de la peau, réduire son diamètre "
            "ou la déplacer")


_IGNORED = {"anchor": "attache (anchor)", "anchor_gap": "écart de l'attache (anchor_gap)", "slit_side": "côté de la fente (slit_side)"}


def tube_warnings(tubes: Sequence["Tube"], cfg: Config, bays=()) -> list[str]:
    """Réglages sans effet sur une SBAR transversale (attache, côté de la fente : la fente est choisie couche par
    couche) et rencontres SBAR transversale ↔ baie de servo (non traitées, la validation reste l'arbitre)."""
    from shapely.geometry import box

    out = []
    for t in tubes:
        own = next((v for k, v in cfg.spars.items() if k.lower() == t.name.lower()), {})
        keys = [label for key, label in _IGNORED.items() if key in own]
        if keys:
            out.append(f"{t.name} (SBAR transversale) : {', '.join(keys)} sans effet — réglage ignoré (trou en goutte "
                       "d'eau, fente choisie couche par couche)")
        band = LineString([(t.start[0], t.start[2]), (t.end[0], t.end[2])]).buffer(t.radius + t.sleeve)
        for bay in bays:
            lo, hi = bay.opening
            if band.intersects(box(bay.x0, lo, bay.x1, hi)):
                out.append(f"{t.name} (SBAR transversale) traverse la baie de servo {bay.name} vue de dessus : leur "
                           "rencontre n'est pas traitée (la validation reste l'arbitre) — éloigner la SBAR de la baie")
    return out


def _choose(valid: list[list[float | None]]) -> list[int]:
    """Option par échantillon : moins de changements, puis moins d'échantillons avec fente, puis fentes plus courtes
    (programmation dynamique ; option 0 = sans fente)."""
    n_opt = len(valid[0])
    inf = (math.inf, math.inf, math.inf)
    best = [inf if v is None else (0, int(o > 0), v) for o, v in enumerate(valid[0])]
    back: list[list[int]] = []
    for row in valid[1:]:
        new, arg = [], []
        for o, v in enumerate(row):
            if v is None:
                new.append(inf)
                arg.append(0)
                continue
            cands = [(best[p][0] + (p != o), best[p][1] + int(o > 0), best[p][2] + v, p) for p in range(n_opt)]
            c = min(cands)
            new.append(c[:3])
            arg.append(c[3])
        best, back = new, [*back, arg]
    o = min(range(n_opt), key=lambda k: best[k])
    if best[o][0] == math.inf:
        return []
    path = [o]
    for arg in reversed(back):
        o = arg[o]
        path.append(o)
    return path[::-1]


def spar_tubes(
    spars: Sequence[tuple[str, Solid]], cfg: Config, mesh: trimesh.Trimesh, bb,
    cables: frozenset[str] | set[str] = frozenset(), regions: Sequence[Region] = (Region(),),
    others: Callable[[float], list[Polygon]] | None = None,
) -> list[tuple[Tube, Solid]]:
    """Tubes des longerons transversaux qui traversent la peau maillée `mesh` (boîte `bb`), avec le solide du
    longeron. `regions` : morceaux de la coupe dans la corde (un seul : pas de coupe) ; `others` : z → autres
    découpes de la couche (trous et fentes des longerons parallèles à Z), retirées de l'enveloppe pour l'analyse.
    TubeError si une couche ne peut pas être reliée au contour (voir le module)."""
    reach = float(np.linalg.norm([bb.max.X - bb.min.X, bb.max.Y - bb.min.Y, bb.max.Z - bb.min.Z])) + 2 * OPEN_REACH + 20
    out = []
    for name, solid in spars:
        try:
            tube = _tube(name, solid, cfg, mesh, bb, cables, regions, others, reach)
        except TubeError:
            raise
        except GEOMETRY_ERRORS as exc:  # shapely, trimesh, numpy : jamais d'erreur brute pour l'utilisateur
            raise TubeError(f"{name} : SBAR transversale impossible ({type(exc).__name__} : {exc})") from exc
        if tube is not None:
            out.append((tube, solid))
    return out


def _tube(
    name: str, solid: Solid, cfg: Config, mesh: trimesh.Trimesh, bb, cables, regions: Sequence[Region],
    others: Callable[[float], list[Polygon]] | None, reach: float,
) -> Tube | None:
    """Tube d'un longeron transversal (None : hors de cette peau) ; voir `spar_tubes`."""
    rules = _Rules(cfg.preset.line_width, cfg.preset.min_slit_width, cfg.validation.max_mouth_width)
    overhang = cfg.preset.max_overhang_deg
    plate_pad = cfg.slit_width / 2
    zr = (bb.min.Z, bb.max.Z)
    info = detect_spar(solid)
    if info is None:
        raise TubeError(f"{name} : aucun cylindre détecté")
    d = _unit(info.axis_dir)
    a = np.asarray(info.axis_point, dtype=float)
    b = a + d * info.length
    if d[2] < 0:
        d, a, b = -d, b, a
    s = cfg.spar_settings(name, cable=name in cables)
    radius = info.diameter / 2 + s.clearance
    cache: dict[float, object] = {}

    def env_at(zs: list[float], close: float = radius + 1.0) -> list:
        miss = sorted({z for z in zs if z not in cache})
        if miss:
            cache.update(zip(miss, _envelopes(mesh, miss, close)))
        return [cache[z] for z in zs]

    def inside(pts: np.ndarray) -> np.ndarray:
        envs = env_at([float(p[2]) for p in pts])
        return np.array([
            zr[0] < p[2] < zr[1] and not e.is_empty and e.contains(Point(p[0], p[1])) for p, e in zip(pts, envs)
        ])

    on_axis = a + np.linspace(0, 1, max(2, min(400, math.ceil(info.length) + 1)))[:, None] * (b - a)
    if not inside(on_axis).any():
        return None  # longeron hors de ce corps d'aile
    tilt = tilt_deg(d)
    # axe à moins du surplomb max de Z : bouts fermés horizontaux (fond, plafond), sous / au-dessus du bout du
    # longeron + jeu — chaque couche du trou est alors une section complète, centrée sur l'axe (la fente « en
    # travers », dans le plan de l'axe, la rejoint toujours) ; sinon bouts perpendiculaires à l'axe
    flat = tilt <= overhang
    u = _unit(np.array([d[0], d[1], 0.0])) if not flat else None
    pad = s.sleeve + plate_pad
    ends, clips, zclips, ucaps = [], [], [], []
    for p, away, sign in ((a, -d, 1.0), (b, d, -1.0)):
        is_open = not inside(np.array([p, p + away * OPEN_END])).all()
        if is_open:
            ends.append((p + away * OPEN_REACH, True))
        elif flat:
            ends.append((p + away * (2 * radius + s.clearance + 1.0), False))
            zc = p[2] - sign * (info.diameter / 2 * math.sin(math.radians(tilt)) + s.clearance)
            clips.append(zc)
            zclips.append(sign)
        elif sign < 0 and 90.0 - tilt > overhang + 1e-9:
            # bout haut fermé : une face ⊥ axe surplomberait de 90° − θ > surplomb max ; face verticale (⊥ u)
            # au-delà du bout du longeron + jeu (tout le disque du bout y est), le prisme la dépasse
            u0 = float(p @ u) + info.diameter / 2 * math.cos(math.radians(tilt)) + s.clearance
            tip = (radius + pad) * 2 / math.sin(math.radians(overhang)) * math.sin(math.radians(tilt))
            ends.append((p + away * ((u0 - float(p @ u) + tip) / math.sin(math.radians(tilt)) + 1.0), False))
            ucaps.append(u0)
        else:
            ends.append((p + away * s.clearance, False))
    (a2, open_a), (b2, open_b) = ends
    e_t = _unit(Z - d[2] * d)
    e_s = np.cross(d, e_t)

    def caps(grow: float) -> tuple[HalfSpace, ...]:
        horizontal = tuple(((0.0, 0.0, sg), -sg * (zc - sg * grow)) for zc, sg in zip(clips, zclips))
        return (*horizontal, *(((-float(u[0]), -float(u[1]), 0.0), u0 + grow) for u0 in ucaps))

    hole = _prism(a2, b2, teardrop(radius, tilt, overhang), e_t, e_s, caps(0.0))
    zone = _prism(a2 - d * pad, b2 + d * pad, teardrop(radius + pad, tilt, overhang), e_t, e_s, caps(pad))
    u = _unit(np.array([d[0], d[1], 0.0]))
    n = np.cross(Z, u)
    hz0, hz1 = hole.zrange()
    z0, z1 = max(hz0, zr[0]), min(hz1, zr[1])
    step = min(_MAX_STEP, max(_MIN_STEP, (z1 - z0) / _MAX_SAMPLES))
    zs = [float(z) for z in np.arange(z0 + step / 2, z1, step)]
    envs = env_at(zs)
    slits: list[Prism] = []
    zones: list[Prism] = []
    for region in regions:
        along = _Family("le long de l'axe", a2, u, Z)
        if tilt <= overhang:  # plan de l'axe et de n : parois inclinées de l'angle de l'axe, imprimables
            across = [(_Family("en travers", a2, n, d), None, None)]
        else:  # axe presque horizontal : plans verticaux ⊥ u, en escalier le long du trou
            across = [
                (_Family("en travers", a2 + u * ts, n, Z), lo, hi)
                for ts, lo, hi in _stairs(hole, region, a2, u, radius, reach)
            ]
        families = (((along, None, None),), tuple(across))
        options = [_Option(pieces, side) for pieces in families if pieces for side in (1, -1)]
        full = [o.slabs(hole, region, reach, s.slit_width / 2) for o in options]
        rows, kept, seen = [], [], []
        for z, env in zip(zs, envs):
            clip = region.layer(z, reach)
            h = hole.layer(z)
            if clip is None or h is None or env.is_empty:
                continue
            if h.intersection(clip).area <= 1e-9:
                continue  # trou hors de ce morceau (le trou n'est pas limité au morceau : au-delà d'une face de
                # coupe, il est à l'extérieur du morceau, la bouche est sur la face)
            avail = env.intersection(clip)
            if others is not None:
                cuts = others(z)
                if cuts:
                    avail = avail.difference(unary_union(cuts))
            touched = [p for p in _parts(avail) if p.intersects(h)]
            if not touched:
                continue  # trou hors de la matière à cette hauteur
            e = max(touched, key=lambda p: p.intersection(h).area)
            row = [_admits(e, h, None, rules)]
            for slabs in full:
                g = [x for x in (p.layer(z) for p in slabs) if x is not None]
                row.append(None if not g else _admits(e, h, unary_union(g), rules))
            rows.append(row)
            kept.append(z)
            seen.append((e, h))
        if not rows:
            continue
        path = _choose(rows)
        if not path:
            i = next(i for i, r in enumerate(rows) if all(v is None for v in r))
            where = f" ({region.label})" if region.label else ""
            cause = _cause(*seen[i], rules, ((a, open_a), (b, open_b)), zr[0])
            raise TubeError(f"{name}{where} : à z = {kept[i] - zr[0]:.1f} mm de l'emplanture, aucune fente ne relie le "
                            f"trou à l'extérieur sans couper la couche en deux{cause}")
        for opt, lo, hi in _runs(path, kept):
            if opt == 0:
                continue
            for fam, sh in options[opt - 1].shadows(hole, region, reach, lo, hi):
                slits.append(fam.slab(sh, s.slit_width / 2))
                zones.append(fam.slab(sh.buffer(plate_pad, join_style="mitre"), s.strip / 2 + plate_pad))
    tube = Tube(
        name, info.diameter, radius, s.sleeve, s.strip, s.slit_width, tilt, name in cables,
        tuple(map(float, a2)), tuple(map(float, b2)), open_a, open_b, hole, zone, tuple(slits), tuple(zones),
    )
    return tube


def _stairs(
    hole: Prism, region: Region, origin, u, radius: float, reach: float
) -> list[tuple[float, float | None, float | None]]:
    """Fente « en travers » d'un axe presque horizontal : plans verticaux ⊥ u en escalier, (t, z_bas, z_haut) — t le
    long de u depuis `origin` ; chaque marche reste à l'intérieur de la section du trou (marge min(R/2, ¼ de la
    section)) dans toutes ses couches ; bornes au milieu entre deux pas, la première et la dernière ouvertes (None)."""
    f = hole.section(origin, u, Z)
    q = region.in_plane(origin, u, Z, reach)
    if f is None or q is None:
        return []
    f = f.intersection(q)
    if f.is_empty or f.area <= 1e-9:
        return []
    z0, z1 = f.bounds[1], f.bounds[3]
    zs = np.append(np.arange(z0, z1, _MIN_STEP), z1)

    def interval(z: float) -> tuple[float, float] | None:
        cut = f.intersection(LineString([(-reach, z), (reach, z)]))
        return None if cut.is_empty else (cut.bounds[0], cut.bounds[2])

    steps: list[tuple[float, int, int]] = []
    i = 0
    while i < len(zs):
        iv = interval(zs[i])
        if iv is None:
            i += 1
            continue
        ts, j = (iv[0] + iv[1]) / 2, i
        while j + 1 < len(zs):
            nxt = interval(zs[j + 1])
            if nxt is None:
                break
            m = min(radius / 2, (nxt[1] - nxt[0]) / 4)
            if not nxt[0] + m <= ts <= nxt[1] - m:
                break
            j += 1
        steps.append((ts, i, j))
        i = j + 1
    oz = float(np.asarray(origin)[2])
    out = []
    for k, (ts, i, j) in enumerate(steps):
        lo = None if k == 0 else oz + (zs[steps[k - 1][2]] + zs[i]) / 2
        hi = None if k == len(steps) - 1 else oz + (zs[j] + zs[steps[k + 1][1]]) / 2
        out.append((float(ts), lo, hi))
    return out


def _runs(path: list[int], zs: list[float]) -> list[tuple[int, float | None, float | None]]:
    """Tranches de hauteur d'option constante : (option, z_bas, z_haut), bornes au milieu entre échantillons, la
    première et la dernière ouvertes (None)."""
    out = []
    start = 0
    for i in range(1, len(path) + 1):
        if i == len(path) or path[i] != path[start]:
            lo = None if start == 0 else (zs[start - 1] + zs[start]) / 2
            hi = None if i == len(path) else (zs[i - 1] + zs[i]) / 2
            out.append((path[start], lo, hi))
            start = i
    return out
