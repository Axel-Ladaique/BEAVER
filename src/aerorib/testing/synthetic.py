"""Pièces synthétiques pour les tests : aile NACA bonne et variantes cassées."""

from __future__ import annotations

import math
from typing import Callable, Literal

import numpy as np
from build123d import (
    Align,
    Box,
    BuildLine,
    BuildPart,
    BuildSketch,
    Compound,
    Cylinder,
    Line,
    Location,
    Part,
    Solid,
    Spline,
    Vector,
    extrude,
    make_face,
)

Camber = Callable[[float], float]


def naca4(code: str = "4412", chord: float = 150.0, n: int = 80) -> tuple[list[tuple[float, float]], Camber]:
    m, p, t = int(code[0]) / 100, int(code[1]) / 10, int(code[2:]) / 100
    beta = np.linspace(0.0, math.pi, n)
    x = (1 - np.cos(beta)) / 2
    yt = 5 * t * (0.2969 * np.sqrt(x) - 0.1260 * x - 0.3516 * x**2 + 0.2843 * x**3 - 0.1036 * x**4)
    if m > 0:
        yc = np.where(x < p, m / p**2 * (2 * p * x - x**2), m / (1 - p) ** 2 * ((1 - 2 * p) + 2 * p * x - x**2))
        dyc = np.where(x < p, 2 * m / p**2 * (p - x), 2 * m / (1 - p) ** 2 * (p - x))
    else:
        yc = np.zeros_like(x)
        dyc = np.zeros_like(x)
    th = np.arctan(dyc)
    xu, yu = x - yt * np.sin(th), yc + yt * np.cos(th)
    xl, yl = x + yt * np.sin(th), yc - yt * np.cos(th)
    pts = list(zip(xu[::-1], yu[::-1])) + list(zip(xl[1:], yl[1:]))
    pts = [(float(a) * chord, float(b) * chord) for a, b in pts]

    def camber(xx: float) -> float:
        return float(np.interp(xx / chord, x, yc)) * chord

    return pts, camber


def naca_wing(code: str = "4412", chord: float = 150.0, span: float = 100.0) -> tuple[Part, Camber]:
    pts, camber = naca4(code, chord)
    mid = len(pts) // 2
    with BuildPart() as bp:
        with BuildSketch():
            with BuildLine():
                Spline(*pts[: mid + 1])
                Spline(*pts[mid:])
                Line(pts[-1], pts[0])
            make_face()
        extrude(amount=span)
    return bp.part, camber


def naca_face(code: str = "4412", chord: float = 150.0):
    """Face plane z = 0 du profil NACA (bord d'attaque en x = 0)."""
    pts, _ = naca4(code, chord)
    mid = len(pts) // 2
    with BuildSketch() as sk:
        with BuildLine():
            Spline(*pts[: mid + 1])
            Spline(*pts[mid:])
            Line(pts[-1], pts[0])
        make_face()
    return sk.sketch.faces()[0]


def swept_wing(
    chord: float = 60.0, span: float = 200.0, sweep_deg: float = 0.0, dihedral_deg: float = 0.0, code: str = "0012",
) -> Solid:
    """Aile prismatique en flèche et/ou en dièdre, repère de construction (envergure Z, corde X, épaisseur Y) :
    section constante, décalée de z·tan(flèche) en X et z·tan(dièdre) en Y ; emplanture en z = 0."""
    d = Vector(span * math.tan(math.radians(sweep_deg)), span * math.tan(math.radians(dihedral_deg)), span)
    return Solid.extrude(naca_face(code, chord), d)


def _slit(x: float, y_from: float, up: bool, width: float, span: float) -> Box:
    """Plaque verticale d'épaisseur `width` partant de y_from vers le haut (up) ou le bas."""
    align_y = Align.MIN if up else Align.MAX
    return Box(width, 60, span * 2, align=(Align.CENTER, align_y, Align.CENTER)).moved(
        Location((x, y_from, span / 2))
    )


def ribbed_wing(
    rib_x: tuple[float, ...] = (20, 70, 90, 110, 130),
    spar_x: float = 45.0,
    spar_d: float = 8.0,
    slit: float = 0.15,  # > 1,1 × min_slit_width (0,10) des presets
    gap: float = 0.6,
    chord: float = 150.0,
    span: float = 100.0,
) -> Part:
    wing, camber = naca_wing(chord=chord, span=span)
    tools = []
    for x in rib_x:
        yc = camber(x)
        tools.append(_slit(x, yc + gap / 2, True, slit, span))
        tools.append(_slit(x, yc - gap / 2, False, slit, span))
    ys = camber(spar_x)
    tools.append(Cylinder(spar_d / 2, span * 2).moved(Location((spar_x, ys, span / 2))))
    tools.append(_slit(spar_x, ys, True, slit, span))
    return wing - Compound(tools)


def broken_wing(kind: Literal["hole", "island", "narrow", "skin", "hole_narrow"]) -> Part:
    wing, camber = naca_wing()
    if kind == "hole":
        return wing - Cylinder(4, 200).moved(Location((45, camber(45), 50)))
    if kind == "island":
        return wing - Box(0.1, 80, 200).moved(Location((100, 0, 50)))
    if kind == "narrow":
        return wing - _slit(100, camber(100) + 0.3, True, 0.05, 100)
    if kind == "skin":
        return wing - _slit(100, camber(100) + 0.3, True, 3.0, 100)
    if kind == "hole_narrow":
        hole = Cylinder(4, 200).moved(Location((45, camber(45), 50)))
        return wing - hole - _slit(45, camber(45), True, 0.05, 100)
    raise ValueError(f"variante inconnue : {kind}")


def wing_x_bodies(
    chord: float = 100.0,
    span: float = 80.0,
    spar_x: float = 30.0,
    spar_d: float = 8.0,
    passage_x: float = 70.0,
    passage_d: float = 2.4,
) -> list["NamedBody"]:
    from aerorib.io_step import NamedBody

    wing, camber = naca_wing(chord=chord, span=span)
    wing = wing - Cylinder(spar_d / 2, span * 2).moved(Location((spar_x, camber(spar_x), span / 2)))
    wing = wing - Cylinder(passage_d / 2, span * 2).moved(Location((passage_x, camber(passage_x), span / 2)))
    spar = Cylinder(spar_d / 2, span).moved(Location((spar_x, camber(spar_x), span / 2)))
    to_x = Location((0, 0, 0), (0, 90, 0))
    return [
        NamedBody(("Aile_test",), wing.moved(to_x).solids()[0]),
        NamedBody(("SBAR_8",), spar.moved(to_x).solids()[0]),
    ]


ServoBox = tuple[float, float, float, float, float, float]  # x0, x1, y0, y1, z0, z1 (repère de construction)
# boîtes servo par défaut sur l'aile NACA 4412 (corde 100) : extrados à y ≈ 9,9, intrados à y ≈ -2,1 vers x = 30
SERVO_BOXES: dict[str, ServoBox] = {
    "extrados": (22.0, 42.0, 3.0, 16.0, 40.0, 75.0),
    "intrados": (22.0, 42.0, -8.0, 1.5, 40.0, 75.0),
}


def servo_wing_bodies(
    side: Literal["extrados", "intrados"] = "extrados",
    box: ServoBox | None = None,
    tilt_deg: float = 0.0,
    chord: float = 100.0,
    span: float = 120.0,
    cable: bool = False,
) -> list["NamedBody"]:
    """Aile droite (envergure Z > corde : orientée sans longeron) + corps `SERVO_1` (boîte), dans le repère de
    construction (corde X, épaisseur Y, envergure Z). `tilt_deg` : boîte tournée autour de Y (baie non alignée).
    `cable` : gaine `SBAR_CABLE_SERVO` (Ø6) de l'emplanture (z = 0) jusque dans la boîte, dépassant de son fond."""
    from aerorib.io_step import NamedBody

    wing, _ = naca_wing(chord=chord, span=span)
    x0, x1, y0, y1, z0, z1 = box if box is not None else SERVO_BOXES[side]
    servo = Box(x1 - x0, y1 - y0, z1 - z0).moved(
        Location(((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2), (0, tilt_deg, 0))
    )
    bodies = [NamedBody(("Aile_servo",), wing.solids()[0]), NamedBody(("SERVO_1",), servo.solids()[0])]
    if cable:
        zc = (z0 + z1) / 2
        tube = Cylinder(3.0, zc).moved(Location(((x0 + x1) / 2, y0 - 1.0, zc / 2)))
        bodies.append(NamedBody(("SBAR_CABLE_SERVO",), tube.solids()[0]))
    return bodies


def wide_wing_bodies(
    chord: float = 400.0,
    span: float = 120.0,
    sweep_deg: float = 0.0,
    spar_x: float | None = 200.0,
    spar_d: float = 8.0,
    servo: ServoBox | None = None,
    code: str = "4412",
) -> list["NamedBody"]:
    """Aile à grande corde (coupe dans la corde sur P1S), repère de construction (corde X, épaisseur Y, envergure Z) :
    profil NACA extrudé avec flèche `sweep_deg` ; longeron `SBAR_8` parallèle à Z en x = spar_x (à l'emplanture,
    sur la ligne de cambrure), sur toute l'envergure ; `servo` : boîte `SERVO_1` (x0, x1, y0, y1, z0, z1)."""
    from aerorib.io_step import NamedBody

    pts, camber = naca4(code, chord)
    wing = swept_wing(chord=chord, span=span, sweep_deg=sweep_deg, code=code)
    bodies = []
    if spar_x is not None:
        loc = Location((spar_x, camber(spar_x), span / 2))
        wing = wing - Cylinder(spar_d / 2, span * 2).moved(loc)
        bodies.append(NamedBody(("SBAR_8",), Cylinder(spar_d / 2, span).moved(loc).solids()[0]))
    bodies.insert(0, NamedBody(("Aile_large",), wing.solids()[0]))
    if servo is not None:
        x0, x1, y0, y1, z0, z1 = servo
        box = Box(x1 - x0, y1 - y0, z1 - z0).moved(Location(((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2)))
        bodies.append(NamedBody(("SERVO_1",), box.solids()[0]))
    return bodies


def split_wing_bodies(
    split: Literal["z", "chord"] = "z",
    at: float | None = None,
    chord: float = 100.0,
    span: float = 80.0,
    spar_x: float | None = 30.0,
    spar_d: float = 8.0,
    code: str = "4412",
) -> list["NamedBody"]:
    """Aile droite découpée « dans la CAO » en deux corps `WING_1`, `WING_2`, repère de construction (corde X,
    épaisseur Y, envergure Z, emplanture z = 0) : en Z (`split="z"`, plan z = at, défaut mi-envergure) ou dans la
    corde (`split="chord"`, plan x = at, défaut mi-corde). Longeron `SBAR_8` parallèle à Z en x = spar_x, sur la
    ligne de cambrure, sur toute l'envergure (traverse la jonction d'une découpe en Z)."""
    from aerorib.io_step import NamedBody

    pts, camber = naca4(code, chord)
    wing, _ = naca_wing(code, chord, span)
    bodies = []
    if spar_x is not None:
        loc = Location((spar_x, camber(spar_x), span / 2))
        wing = wing - Cylinder(spar_d / 2, span * 2).moved(loc)
        bodies.append(NamedBody(("SBAR_8",), Cylinder(spar_d / 2, span).moved(loc).solids()[0]))
    big = 10 * (chord + span)
    if split == "z":
        cut = span / 2 if at is None else at
        lo = Box(big, big, cut, align=(Align.CENTER, Align.CENTER, Align.MIN))
        hi = Box(big, big, span - cut, align=(Align.CENTER, Align.CENTER, Align.MIN)).moved(Location((0, 0, cut)))
    else:
        cut = chord / 2 if at is None else at
        lo = Box(big, big, 2 * big, align=(Align.MAX, Align.CENTER, Align.CENTER)).moved(Location((cut, 0, 0)))
        hi = Box(big, big, 2 * big, align=(Align.MIN, Align.CENTER, Align.CENTER)).moved(Location((cut, 0, 0)))
    one, two = (wing & lo).solids()[0], (wing & hi).solids()[0]
    return [NamedBody(("WING_1",), one), NamedBody(("WING_2",), two), *bodies]


def rod(p0: tuple[float, float, float], p1: tuple[float, float, float], d: float) -> Solid:
    """Cylindre de diamètre d de p0 à p1 (axe quelconque)."""
    a, b = np.asarray(p0, dtype=float), np.asarray(p1, dtype=float)
    v = b - a
    length = float(np.linalg.norm(v))
    face = Cylinder(d / 2, length, align=(Align.CENTER, Align.CENTER, Align.MIN))
    z = np.array([0.0, 0.0, 1.0])
    axis = np.cross(z, v / length)
    ang = math.degrees(math.atan2(np.linalg.norm(axis), float(np.dot(z, v / length))))
    if np.linalg.norm(axis) < 1e-12:
        axis = np.array([1.0, 0.0, 0.0])
    from build123d import Axis

    return face.rotate(Axis((0, 0, 0), tuple(axis / np.linalg.norm(axis))), ang).moved(Location(tuple(a))).solids()[0]


def cross_wing_bodies(
    p0: tuple[float, float, float],
    p1: tuple[float, float, float],
    d: float = 4.0,
    chord: float = 100.0,
    span: float = 80.0,
    spar_x: float | None = 25.0,
    spar_d: float = 6.0,
    code: str = "4412",
    wide: bool = False,
    name: str = "SBAR_T",
) -> list["NamedBody"]:
    """Aile droite (repère de construction : corde X, épaisseur Y, envergure Z, emplanture z = 0) traversée par une
    SBAR transversale ou inclinée `name` (Ø d, de p0 à p1, retirée de la peau comme dans la CAO) ; longeron principal
    `SBAR_8` parallèle à Z en x = spar_x sur la ligne de cambrure (oriente la pièce : le plus long). `wide` : aile
    à grande corde de `wide_wing_bodies` (coupe dans la corde sur P1S)."""
    from aerorib.io_step import NamedBody

    pts, camber = naca4(code, chord)
    wing = swept_wing(chord=chord, span=span, code=code) if wide else naca_wing(code, chord, span)[0]
    bodies = []
    if spar_x is not None:
        loc = Location((spar_x, camber(spar_x), span / 2))
        wing = wing - Cylinder(spar_d / 2, span * 2).moved(loc)
        bodies.append(NamedBody(("SBAR_8",), Cylinder(spar_d / 2, span).moved(loc).solids()[0]))
    tube = rod(p0, p1, d)
    wing = wing - tube
    bodies.append(NamedBody((name,), tube))
    return [NamedBody(("Aile_T",), wing.solids()[0]), *bodies]
