"""Découpe de la pièce en tronçons perpendiculaires à l'envergure (plans z = cte)."""

from __future__ import annotations

import math

from build123d import Box, Pos, Solid, Shape


class SegmentError(ValueError):
    """Découpe en tronçons impossible ou invalide."""


MIN_SEGMENT = 10.0  # mm : longueur minimale d'un tronçon


def plan_cuts(height: float, max_height: float, cuts_at: list[float] | None = None) -> list[float]:
    """Hauteurs de coupe internes (vide si la pièce tient d'un seul tenant)."""
    if not (math.isfinite(height) and math.isfinite(max_height)) or height <= 0 or max_height <= 0:
        raise SegmentError(f"hauteur de pièce ({height}) ou hauteur utile ({max_height}) invalide")
    if cuts_at is None:
        if height <= max_height:
            return []
        n = math.ceil(height / max_height - 1e-9)
        cuts = [height * i / n for i in range(1, n)]
    else:
        cuts = sorted(float(c) for c in cuts_at)
        for c in cuts:
            if not math.isfinite(c) or not 0 < c < height:
                raise SegmentError(f"coupe à {c:g} mm hors de la pièce (0 à {height:g} mm)")
        dups = sorted({c for a, c in zip(cuts, cuts[1:]) if a == c})
        if dups:
            raise SegmentError(f"coupes en double : {', '.join(f'{c:g}' for c in dups)}")
    bounds = [0.0, *cuts, float(height)]
    for a, b in zip(bounds, bounds[1:]):
        if b - a > max_height + 1e-9:
            raise SegmentError(f"tronçon de {b - a:g} mm > hauteur utile {max_height:g} mm")
        if b - a < MIN_SEGMENT - 1e-9:
            raise SegmentError(f"tronçon de {b - a:g} mm trop court (minimum {MIN_SEGMENT:g} mm)")
    return cuts


def split_part(solid: Shape, cuts: list[float]) -> list[Solid]:
    """Coupe par plans z = cte ; chaque tronçon est ramené à min Z = 0 (emplanture → saumon)."""
    bb = solid.bounding_box(optimal=True)
    cuts = sorted(float(c) for c in cuts)
    for c in cuts:
        if not math.isfinite(c) or not bb.min.Z < c < bb.max.Z:
            raise SegmentError(f"coupe à z = {c:g} mm hors de la pièce ({bb.min.Z:g} à {bb.max.Z:g} mm)")
    if any(a == b for a, b in zip(cuts, cuts[1:])):
        raise SegmentError("coupes en double")
    pad = 10.0
    sx, sy = bb.size.X + 2 * pad, bb.size.Y + 2 * pad
    cx, cy = (bb.min.X + bb.max.X) / 2, (bb.min.Y + bb.max.Y) / 2
    bounds = [bb.min.Z - pad, *cuts, bb.max.Z + pad]
    out: list[Solid] = []
    for i, (z0, z1) in enumerate(zip(bounds, bounds[1:])):
        try:
            box = Pos(cx, cy, (z0 + z1) / 2) * Box(sx, sy, z1 - z0)
            solids = list((solid & box).solids())
        except Exception as exc:  # erreurs OCC (Standard_Failure, etc.)
            raise SegmentError(f"découpe impossible à z = {z1 if i < len(cuts) else z0:g} mm ({exc})") from exc
        if len(solids) != 1:
            raise SegmentError(f"tronçon {i + 1} : {len(solids)} solides obtenus au lieu de 1")
        s = solids[0]
        if not s.is_valid:
            raise SegmentError(f"tronçon {i + 1} : solide invalide")
        zmin = s.bounding_box(optimal=True).min.Z
        out.append(Pos(0, 0, -zmin) * s)
    return out
