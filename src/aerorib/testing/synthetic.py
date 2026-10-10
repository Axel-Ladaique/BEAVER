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
    Spline,
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
    slit: float = 0.1,
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
