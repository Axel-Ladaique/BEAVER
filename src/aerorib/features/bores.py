"""Trous à relier à la peau : longerons (corps SPAR) et passages existants (addendum P1 §4)."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

from build123d import Align, Box, Cylinder, GeomType, Location, Solid
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.TopAbs import TopAbs_REVERSED

from aerorib.classify import detect_spar
from aerorib.config import Config


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


def spar_bores(spars: list[tuple[str, Solid]], cfg: Config) -> list[Bore]:
    limit = math.cos(math.radians(cfg.passages.max_tilt_deg))
    out = []
    for name, solid in spars:
        info = detect_spar(solid)
        if info is None:
            raise BoreError(f"{name} : aucun cylindre détecté")
        if abs(info.axis_dir[2]) < limit:
            raise BoreError(f"{name} : longeron non parallèle à l'axe d'impression (pris en charge en P5)")
        s = cfg.spar_settings(name)
        tilt = _tilt_deg(info.axis_dir[2])
        drift = info.length * math.tan(math.radians(tilt))
        if drift > s.clearance / 2:
            raise BoreError(
                f"{name} : longeron incliné de {tilt:.1f}° par rapport à l'axe d'impression "
                f"(dérive {drift:.1f} mm > jeu) — non pris en charge en P1"
            )
        start = info.axis_point
        z_end = start[2] + info.length * info.axis_dir[2]
        mid = [start[i] + info.axis_dir[i] * info.length / 2 for i in range(3)]
        out.append(
            Bore(
                name, mid[0], mid[1], info.diameter / 2 + s.clearance, s.sleeve, s.strip, _side(s.slit_side),
                True, min(start[2], z_end), max(start[2], z_end), s.slit_width, s.clearance,
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
                m["r"] = min(m["r"], g["r"])
                m["z0"], m["z1"] = min(m["z0"], g["z0"]), max(m["z1"], g["z1"])
                break
        else:
            merged.append(dict(g))
    out = []
    for m in merged:
        zc = (m["z0"] + m["z1"]) / 2
        out.append(
            Passage(
                round(m["x"] + m["dx"] * zc, 6), round(m["y"] + m["dy"] * zc, 6), round(m["r"], 6),
                m["z0"], m["z1"], round(_tilt_deg(m["dz"]), 6),
            )
        )
    return out


def passage_bores(skin: Solid, cfg: Config, exclude: list[Bore]) -> list[Bore]:
    p = cfg.passages
    if p.mode == "off":
        return []
    out = []
    for f in find_passages(skin, p.max_tilt_deg):
        if any(math.hypot(f.x - b.x, f.y - b.y) < 0.5 for b in exclude):
            continue
        name = f"passage {len(out) + 1}"
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


def extend_to_existing_bores(spars: list[Bore], skin: Solid, cfg: Config) -> list[Bore]:
    """Un longeron logé dans un alésage existant de la peau : fente sur toute l'étendue de l'alésage."""
    found = find_passages(skin, cfg.passages.max_tilt_deg)
    out = []
    for b in spars:
        same = [f for f in found if math.hypot(f.x - b.x, f.y - b.y) < 0.5]
        z0 = min([b.z0, *(f.z0 for f in same)])
        z1 = max([b.z1, *(f.z1 for f in same)])
        out.append(replace(b, z0=z0, z1=z1))
    return out


def bore_cut_tools(bores: list[Bore], bb) -> list[Solid]:
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
    return tools
