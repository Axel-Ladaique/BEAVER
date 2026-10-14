"""Trous à relier à la peau : longerons (corps SPAR) et passages existants (addendum P1 §4)."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

import numpy as np
from build123d import Align, Box, Cylinder, Face, GeomType, Location, Solid, Vector, Wire, extrude
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.TopAbs import TopAbs_REVERSED
from shapely.geometry import Polygon

from aerorib.classify import detect_spar
from aerorib.config import Config
from aerorib.midsurface import MidSurface


class BoreError(ValueError):
    """Trou non pris en charge (message en français)."""


@dataclass(frozen=True)
class Bore:
    name: str
    x: float  # axe à mi-hauteur, repère d'impression
    y: float
    radius: float  # rayon du trou (longeron : Ø/2 + jeu)
    sleeve: float
    strip: float
    side: int
    cut_hole: bool
    z0: float  # étendue le long de Z (repère d'impression)
    z1: float
    slit: float  # largeur de la fente vers la peau
    clearance: float  # marge en Z aux extrémités intérieures (longeron : jeu ; passage : 0)
    anchor_gap: float = 0.0  # > 0 : attache côté opposé, arrêtée à cette distance de la peau (0 : pas d'attache)
    cable: bool = False  # gaine de câble : trou + fente, manchon minimal, sans attache


OPEN_END = 1.0  # extrémité à moins de 1 mm du bord de la pièce : le trou débouche
OPEN_REACH = 10.0  # prolongement au-delà du bord pour un trou débouchant


def bore_extent(b: Bore, zmin: float, zmax: float) -> tuple[float, float]:
    """Étendue en Z du trou et de sa fente : débouchant près d'un bord, sinon arrêté à ± jeu."""
    lo = zmin - OPEN_REACH if b.z0 < zmin + OPEN_END else b.z0 - b.clearance
    hi = zmax + OPEN_REACH if b.z1 > zmax - OPEN_END else b.z1 + b.clearance
    return lo, hi


def _side(slit_side: str) -> int:
    return 1 if slit_side == "top" else -1


def _tilt_deg(dz: float) -> float:
    return math.degrees(math.acos(min(1.0, abs(dz))))


def spar_bores(
    spars: list[tuple[str, Solid]], cfg: Config, cables: frozenset[str] | set[str] = frozenset()
) -> list[Bore]:
    """`cables` : noms des longerons qui sont des gaines de câble (réglages `spars.cable`)."""
    limit = math.cos(math.radians(cfg.passages.max_tilt_deg))
    out = []
    for name, solid in spars:
        info = detect_spar(solid)
        if info is None:
            raise BoreError(f"{name} : aucun cylindre détecté")
        if abs(info.axis_dir[2]) < limit:
            raise BoreError(f"{name} : longeron non parallèle à l'axe d'impression : SBAR transversale (features.tubes)")
        is_cable = name in cables
        s = cfg.spar_settings(name, cable=is_cable)
        tilt = _tilt_deg(info.axis_dir[2])
        drift = info.length * math.tan(math.radians(tilt))
        if drift > s.clearance / 2:
            raise BoreError(
                f"{name} : longeron incliné de {tilt:.1f}° par rapport à l'axe d'impression "
                f"(dérive {drift:.1f} mm > jeu) — ambigu : trop incliné pour un trou vertical, trop peu "
                f"(≤ passages.max_tilt_deg = {cfg.passages.max_tilt_deg:g}°) pour une SBAR transversale ; "
                "l'aligner sur l'envergure ou l'incliner franchement"
            )
        start = info.axis_point
        z_end = start[2] + info.length * info.axis_dir[2]
        mid = [start[i] + info.axis_dir[i] * info.length / 2 for i in range(3)]
        out.append(
            Bore(
                name, mid[0], mid[1], info.diameter / 2 + s.clearance, s.sleeve, s.strip, _side(s.slit_side),
                True, min(start[2], z_end), max(start[2], z_end), s.slit_width, s.clearance,
                s.anchor_gap if s.anchor else 0.0, is_cable,
            )
        )
    return out


_TOL = 1e-4  # tolérance de regroupement des faces d'un même alésage (mm)
_FULL_SPAN = 2 * math.pi * 0.99  # un alésage complet : somme des arcs ≥ 99 % de 2π


@dataclass(frozen=True, order=True)
class Passage:
    x: float  # axe à mi-hauteur de l'étendue
    y: float
    r: float
    z0: float
    z1: float
    tilt_deg: float
    r_max: float = 0.0  # plus grand rayon (lamage) ; r = le plus petit


def find_passages(skin: Solid, max_tilt_deg: float) -> list[Passage]:
    limit = math.cos(math.radians(max_tilt_deg))
    groups: list[dict] = []
    for face in skin.faces():
        if face.geom_type != GeomType.CYLINDER or face.wrapped.Orientation() != TopAbs_REVERSED:
            continue
        ad = BRepAdaptor_Surface(face.wrapped)
        cyl = ad.Cylinder()
        d = cyl.Axis().Direction()
        if abs(d.Z()) < limit:
            continue
        p = cyl.Axis().Location()
        # trace de l'axe dans le plan z = 0 : indépendante du point d'origine choisi par OCC
        dx, dy = d.X() / d.Z(), d.Y() / d.Z()
        x, y, r = p.X() - dx * p.Z(), p.Y() - dy * p.Z(), cyl.Radius()
        span = ad.LastUParameter() - ad.FirstUParameter()
        fb = face.bounding_box()
        for g in groups:
            if abs(g["x"] - x) < _TOL and abs(g["y"] - y) < _TOL and abs(g["r"] - r) < _TOL:
                g["span"] += span
                g["z0"], g["z1"] = min(g["z0"], fb.min.Z), max(g["z1"], fb.max.Z)
                break
        else:
            groups.append(dict(x=x, y=y, r=r, span=span, z0=fb.min.Z, z1=fb.max.Z, dx=dx, dy=dy, dz=d.Z()))
    # alésages complets seulement (un arc de congé ou de poche n'est pas un passage)
    full = [g for g in groups if g["span"] >= _FULL_SPAN]
    # alésages concentriques (lamage) : une seule entrée, le plus petit rayon, étendue cumulée
    merged: list[dict] = []
    for g in full:
        for m in merged:
            if abs(m["x"] - g["x"]) < _TOL and abs(m["y"] - g["y"]) < _TOL:
                m["r"], m["rmax"] = min(m["r"], g["r"]), max(m["rmax"], g["r"])
                m["z0"], m["z1"] = min(m["z0"], g["z0"]), max(m["z1"], g["z1"])
                break
        else:
            merged.append(dict(g, rmax=g["r"]))
    out = []
    for m in merged:
        zc = (m["z0"] + m["z1"]) / 2
        out.append(
            Passage(
                round(m["x"] + m["dx"] * zc, 6), round(m["y"] + m["dy"] * zc, 6), round(m["r"], 6),
                m["z0"], m["z1"], round(_tilt_deg(m["dz"]), 6), round(m["rmax"], 6),
            )
        )
    return out


def free_passages(
    skin: Solid, cfg: Config, exclude: list[Bore], found: list[Passage] | None = None
) -> list[tuple[str, Passage]]:
    """Alésages de la peau qui ne logent pas un longeron (`exclude`), nommés « passage N ».

    `found` : résultat de `find_passages` déjà calculé (sinon recherché ici)."""
    if found is None:
        found = find_passages(skin, cfg.passages.max_tilt_deg)
    out: list[tuple[str, Passage]] = []
    for f in found:
        if any(_linked(b, f) for b in exclude if b.cut_hole):
            continue  # alésage du logement d'un longeron : déjà traité avec lui
        out.append((f"passage {len(out) + 1}", f))
    return out


def passage_bores(
    skin: Solid, cfg: Config, exclude: list[Bore], found: list[Passage] | None = None
) -> list[Bore]:
    """Mode `link` : chaque passage est relié à la peau par une fente (sans attache)."""
    p = cfg.passages
    if p.mode != "link":
        return []
    out = []
    for name, f in free_passages(skin, cfg, exclude, found):
        drift = (f.z1 - f.z0) * math.tan(math.radians(f.tilt_deg))
        if drift > p.sleeve / 2:
            raise BoreError(
                f"{name} (x={f.x:.1f}, y={f.y:.1f}) : passage incliné de {f.tilt_deg:.1f}° par rapport à l'axe "
                f"d'impression (dérive {drift:.1f} mm > manchon) — non pris en charge en P1"
            )
        out.append(
            Bore(name, f.x, f.y, f.r, p.sleeve, p.strip, _side(p.slit_side), False, f.z0, f.z1, p.slit_width, 0.0)
        )
    return out


FILL_MARGIN = 0.02  # mm : le cylindre de rebouchage déborde de l'alésage pour une fusion franche


def fill_radius(f: Passage) -> float:
    return max(f.r, f.r_max) + FILL_MARGIN


def passages_to_fill(
    skin: Solid, cfg: Config, exclude: list[Bore], found: list[Passage] | None = None
) -> list[tuple[str, Passage]]:
    """Mode `fill` : passages à reboucher (cylindre vertical ; un passage incliné est refusé)."""
    if cfg.passages.mode != "fill":
        return []
    out = []
    for name, f in free_passages(skin, cfg, exclude, found):
        drift = (f.z1 - f.z0) * math.tan(math.radians(f.tilt_deg))
        if drift / 2 > FILL_MARGIN:
            raise BoreError(
                f"{name} (x={f.x:.1f}, y={f.y:.1f}) : passage incliné de {f.tilt_deg:.1f}° par rapport à l'axe "
                f"d'impression (dérive {drift:.2f} mm) — rebouchage impossible en P1 "
                "(corriger le modèle, ou passages: {mode: link} / {mode: off})"
            )
        out.append((name, f))
    return out


def fill_tools(passages: list[Passage]) -> list[Solid]:
    """Cylindres de rebouchage : rayon r + FILL_MARGIN, exactement sur [z0, z1] (pas au-delà de la pièce)."""
    return [
        Cylinder(fill_radius(f), f.z1 - f.z0).moved(Location((f.x, f.y, (f.z0 + f.z1) / 2))) for f in passages
    ]


_COAXIAL = 0.5  # mm : alésage sur l'axe d'un longeron
_EPS = 1e-3


def _linked(spar: Bore, f: Passage) -> bool:
    """Alésage existant prolongeant le logement du longeron : coaxial, au moins aussi large que le
    longeron, et touchant son étendue (± jeu). Un alésage plus petit ou séparé reste un passage."""
    return (
        math.hypot(f.x - spar.x, f.y - spar.y) < _COAXIAL
        and f.r >= spar.radius - spar.clearance - _EPS
        and f.z0 <= spar.z1 + spar.clearance + _EPS
        and f.z1 >= spar.z0 - spar.clearance - _EPS
    )


def extend_to_existing_bores(
    spars: list[Bore], skin: Solid, cfg: Config, found: list[Passage] | None = None
) -> list[Bore]:
    """Un longeron logé dans un alésage existant plus long : trou et fente sur toute l'union contiguë."""
    if found is None:
        found = find_passages(skin, cfg.passages.max_tilt_deg)
    out = []
    for b in spars:
        while True:  # de proche en proche : un alésage relié peut en toucher un autre
            linked = [f for f in found if _linked(b, f)]
            z0 = min([b.z0, *(f.z0 for f in linked)])
            z1 = max([b.z1, *(f.z1 for f in linked)])
            if (z0, z1) == (b.z0, b.z1):
                break
            b = replace(b, z0=z0, z1=z1)
        out.append(b)
    return out


ANCHOR_STEP = 2.0  # mm, pas max d'échantillonnage de la peau opposée le long de Z
ANCHOR_MIN = 0.2  # mm : attache plus courte (hors du trou) → omise à cette hauteur


def anchor_profiles_2d(b: Bore, mid: MidSurface, bb) -> list[Polygon]:
    """Profils (z, y) de l'attache côté opposé à la fente, dans le plan x = b.x : du centre du trou jusqu'à
    `anchor_gap` de la peau opposée (lue dans la surface médiane), sur l'étendue en Z du trou.

    Aux hauteurs où la peau opposée est à moins de `radius + anchor_gap + ANCHOR_MIN` du centre, pas
    d'attache (un profil par portion continue).
    """
    lo, hi = bore_extent(b, bb.min.Z, bb.max.Z)
    zs = np.linspace(lo, hi, max(2, math.ceil((hi - lo) / ANCHOR_STEP) + 1))
    ym, th = mid.at(np.full_like(zs, b.x), zs)
    skin = ym - b.side * th / 2  # peau opposée à la fente
    room = (th > 0) & (b.side * (b.y - skin) >= b.radius + b.anchor_gap + ANCHOR_MIN)
    y_end = skin + b.side * b.anchor_gap
    out = []
    idx = np.flatnonzero(np.diff(np.concatenate([[0], room.astype(int), [0]])))
    for i, j in zip(idx[::2], idx[1::2]):  # échantillons i … j−1 avec de la place
        if j - i < 2:
            continue
        pts = [(zs[i], b.y), (zs[j - 1], b.y)] + [(zs[k], y_end[k]) for k in range(j - 1, i - 1, -1)]
        out.append(Polygon(pts).simplify(0.01))
    return out


def anchor_tools(b: Bore, mid: MidSurface, bb) -> list[Solid]:
    """Attache côté opposé à la fente : fente borgne dans le plan x = b.x (voir `anchor_profiles_2d`),
    construite en 2D (z, y) puis extrudée de ± slit/2 en X."""
    faces = []
    for poly in anchor_profiles_2d(b, mid, bb):
        ring = [Vector(b.x, y, z) for z, y in list(poly.exterior.coords)[:-1]]
        faces.append(Face(Wire.make_polygon(ring, close=True)))
    return [extrude(f, amount=b.slit / 2, dir=Vector(1, 0, 0), both=True) for f in faces]


def bore_layer(b: Bore, z: float, bb) -> list[Polygon]:
    """Trou et fente du trou `b` dans la couche z (sans l'attache), repère XY : sections des outils de coupe."""
    from shapely.geometry import Point, box

    lo, hi = bore_extent(b, bb.min.Z, bb.max.Z)
    if not lo <= z <= hi:
        return []
    reach = (bb.max.Y - bb.min.Y) + 20
    out = [Point(b.x, b.y).buffer(b.radius, quad_segs=32)] if b.cut_hole else []
    y0, y1 = (b.y, b.y + reach) if b.side > 0 else (b.y - reach, b.y)
    return [*out, box(b.x - b.slit / 2, y0, b.x + b.slit / 2, y1)]


def bore_cut_tools(bores: list[Bore], bb, mid: MidSurface | None = None) -> list[Solid]:
    """Outils de coupe des trous : trou (longeron), fente vers la peau, attache côté opposé (si anchor_gap)."""
    reach = (bb.max.Y - bb.min.Y) + 20
    tools = []
    for b in bores:
        lo, hi = bore_extent(b, bb.min.Z, bb.max.Z)
        height, zc = hi - lo, (hi + lo) / 2
        if b.cut_hole:
            tools.append(Cylinder(b.radius, height).moved(Location((b.x, b.y, zc))))
        align_y = Align.MIN if b.side > 0 else Align.MAX
        tools.append(
            Box(b.slit, reach, height, align=(Align.CENTER, align_y, Align.CENTER)).moved(Location((b.x, b.y, zc)))
        )
        if b.anchor_gap > 0:
            if mid is None:
                raise ValueError(f"{b.name} : attache impossible sans surface médiane")
            tools += anchor_tools(b, mid, bb)
    return tools
