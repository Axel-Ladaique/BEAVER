"""Trous à relier à la peau : longerons (corps SPAR) et passages existants (addendum P1 §4)."""

from __future__ import annotations

import math
from dataclasses import dataclass

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
    x: float
    y: float
    radius: float
    sleeve: float
    strip: float
    side: int
    cut_hole: bool


def _side(slit_side: str) -> int:
    return 1 if slit_side == "top" else -1


def spar_bores(spars: list[tuple[str, Solid]], cfg: Config) -> list[Bore]:
    limit = math.cos(math.radians(cfg.passages.max_tilt_deg))
    out = []
    for name, solid in spars:
        info = detect_spar(solid)
        if info is None or abs(info.axis_dir[2]) < limit:
            raise BoreError(f"{name} : longeron non parallèle à l'axe d'impression (pris en charge en P5)")
        s = cfg.spar_settings(name)
        out.append(
            Bore(
                name,
                info.axis_point[0],
                info.axis_point[1],
                info.diameter / 2 + s.clearance,
                s.sleeve,
                s.strip,
                _side(s.slit_side),
                True,
            )
        )
    return out


def find_passages(skin: Solid, max_tilt_deg: float) -> list[tuple[float, float, float]]:
    limit = math.cos(math.radians(max_tilt_deg))
    found: list[tuple[float, float, float]] = []
    for face in skin.faces():
        if face.geom_type != GeomType.CYLINDER or face.wrapped.Orientation() != TopAbs_REVERSED:
            continue
        cyl = BRepAdaptor_Surface(face.wrapped).Cylinder()
        d = cyl.Axis().Direction()
        if abs(d.Z()) < limit:
            continue
        p = cyl.Axis().Location()
        key = (round(p.X(), 6), round(p.Y(), 6), round(cyl.Radius(), 6))
        if key not in found:
            found.append(key)
    return found


def passage_bores(skin: Solid, cfg: Config, exclude: list[Bore]) -> list[Bore]:
    p = cfg.passages
    if p.mode == "off":
        return []
    out = []
    for x, y, r in find_passages(skin, p.max_tilt_deg):
        if any(math.hypot(x - b.x, y - b.y) < 0.5 for b in exclude):
            continue
        out.append(Bore(f"passage {len(out) + 1}", x, y, r, p.sleeve, p.strip, _side(p.slit_side), False))
    return out


def bore_cut_tools(bores: list[Bore], bb, slit_width: float) -> list[Solid]:
    height = (bb.max.Z - bb.min.Z) + 20
    zc = (bb.max.Z + bb.min.Z) / 2
    reach = (bb.max.Y - bb.min.Y) + 20
    tools = []
    for b in bores:
        if b.cut_hole:
            tools.append(Cylinder(b.radius, height).moved(Location((b.x, b.y, zc))))
        align_y = Align.MIN if b.side > 0 else Align.MAX
        tools.append(
            Box(slit_width, reach, height, align=(Align.CENTER, align_y, Align.CENTER)).moved(
                Location((b.x, b.y, zc))
            )
        )
    return tools
