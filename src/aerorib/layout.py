"""Placement d'un morceau sur le plateau : rotation autour de Z pour tenir dans le volume d'impression."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from shapely.geometry import MultiPoint, Polygon

_SWEEP_STEP = 0.5  # pas du balayage fin (degrés)
_EPS = 1e-9


@dataclass(frozen=True)
class Placement:
    angle_deg: float  # rotation (anti-horaire, autour de Z) appliquée au morceau, dans ]-90, 90]
    fits: bool  # le morceau tourné de angle_deg tient dans le plateau moins les marges
    slack: float  # mm libres sur l'axe le plus serré (négatif si ça ne rentre pas)
    size: tuple[float, float]  # largeur × profondeur de l'emprise une fois tourné


def _hull_points(footprint) -> np.ndarray:
    if isinstance(footprint, np.ndarray):
        geom = MultiPoint([tuple(p) for p in footprint[:, :2]])
    else:
        geom = footprint
    hull = geom.convex_hull
    if isinstance(hull, Polygon):
        return np.asarray(hull.exterior.coords)[:-1]
    return np.asarray(hull.coords)


def _extent(pts: np.ndarray, angles_deg: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Largeur et profondeur de l'emprise après rotation de chacun des angles."""
    a = np.radians(angles_deg)[:, None]
    c, s = np.cos(a), np.sin(a)
    x = c * pts[None, :, 0] - s * pts[None, :, 1]
    y = s * pts[None, :, 0] + c * pts[None, :, 1]
    return x.max(1) - x.min(1), y.max(1) - y.min(1)


def _norm(angle: float) -> float:
    """Angle ramené dans ]-90, 90] (l'emprise est identique à 180° près)."""
    a = (angle + 90.0) % 180.0 - 90.0
    return 90.0 if a <= -90.0 + _EPS else a


def fit_on_bed(footprint, bed: tuple[float, float], margin: float = 0.0) -> Placement:
    """Angle de rotation autour de Z qui fait tenir l'emprise (polygone XY, enveloppe convexe prise) sur le plateau.

    0° si ça tient tel quel ; sinon l'angle de plus grande marge (calipers sur les arêtes de l'enveloppe + balayage
    fin). `fits` est faux si aucun angle ne convient : l'angle renvoyé est alors le moins mauvais."""
    pts = _hull_points(footprint)
    aw, ad = bed[0] - 2 * margin, bed[1] - 2 * margin
    w0, d0 = (float(v[0]) for v in _extent(pts, np.array([0.0])))
    if w0 <= aw + _EPS and d0 <= ad + _EPS:
        return Placement(0.0, True, min(aw - w0, ad - d0), (w0, d0))
    cands = [np.arange(0.0, 180.0, _SWEEP_STEP)]
    if len(pts) >= 2:
        e = np.roll(pts, -1, axis=0) - pts
        ang = np.degrees(np.arctan2(e[:, 1], e[:, 0]))
        cands += [(-ang) % 180.0, (90.0 - ang) % 180.0]
    angles = np.unique(np.round(np.concatenate(cands), 9))
    w, d = _extent(pts, angles)
    slack = np.minimum(aw - w, ad - d)
    best = slack.max()
    # à marge égale (à 1 µm), l'angle le plus petit en valeur absolue
    ok = np.flatnonzero(slack >= best - 1e-6)
    i = min(ok, key=lambda j: abs(_norm(float(angles[j]))))
    return Placement(_norm(float(angles[i])), bool(best >= -_EPS), float(slack[i]), (float(w[i]), float(d[i])))


def rotation_matrix(angle_deg: float) -> np.ndarray:
    """Matrice 3×3 (colonnes) de la rotation anti-horaire de `angle_deg` autour de Z."""
    c, s = math.cos(math.radians(angle_deg)), math.sin(math.radians(angle_deg))
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])


def format_rotation(pl: Placement) -> str:
    """« tourné de 38° pour tenir sur le plateau » (angle arrondi au degré)."""
    return f"tourné de {abs(pl.angle_deg):.0f}° pour tenir sur le plateau"


def format_rotations(placements, names: list[str] | None = None) -> list[str]:
    """Une ligne par tronçon tourné : « T1 : tourné de 38° pour tenir sur le plateau »."""
    out = []
    for i, pl in enumerate(placements):
        if pl.angle_deg:
            out.append(f"{names[i] if names else f'T{i + 1}'} : {format_rotation(pl)}")
    return out
