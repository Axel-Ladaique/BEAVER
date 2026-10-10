"""Découpe de la pièce en tronçons perpendiculaires à l'envergure (plans z = cte)."""

from __future__ import annotations

import math

from build123d import Box, Pos, Solid, Shape


class SegmentError(ValueError):
    """Découpe en tronçons impossible ou invalide."""


def plan_cuts(height: float, max_height: float, cuts_at: list[float] | None = None) -> list[float]:
    """Hauteurs de coupe internes (vide si la pièce tient d'un seul tenant)."""
    if cuts_at is None:
        if height <= max_height:
            return []
        n = math.ceil(height / max_height)
        return [height * i / n for i in range(1, n)]
    cuts = sorted(float(c) for c in cuts_at)
    for c in cuts:
        if not 0 < c < height:
            raise SegmentError(f"coupe à {c:g} mm hors de la pièce (0 à {height:g} mm)")
    bounds = [0.0, *cuts, float(height)]
    for a, b in zip(bounds, bounds[1:]):
        if b - a > max_height + 1e-9:
            raise SegmentError(f"tronçon de {b - a:g} mm > hauteur utile {max_height:g} mm")
    return cuts


def split_part(solid: Shape, cuts: list[float]) -> list[Solid]:
    """Coupe par plans z = cte ; chaque tronçon est ramené à min Z = 0 (emplanture → saumon)."""
    bb = solid.bounding_box()
    pad = 10.0
    sx, sy = bb.size.X + 2 * pad, bb.size.Y + 2 * pad
    cx, cy = (bb.min.X + bb.max.X) / 2, (bb.min.Y + bb.max.Y) / 2
    bounds = [bb.min.Z - pad, *sorted(cuts), bb.max.Z + pad]
    out: list[Solid] = []
    for i, (z0, z1) in enumerate(zip(bounds, bounds[1:])):
        box = Pos(cx, cy, (z0 + z1) / 2) * Box(sx, sy, z1 - z0)
        piece = solid & box
        solids = list(piece.solids())
        if len(solids) != 1:
            raise SegmentError(f"tronçon {i + 1} : {len(solids)} solides obtenus au lieu de 1")
        s = solids[0]
        if not s.is_valid:
            raise SegmentError(f"tronçon {i + 1} : solide invalide")
        zmin = s.bounding_box().min.Z
        out.append(Pos(0, 0, -zmin) * s)
    return out
