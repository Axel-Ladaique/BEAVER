"""Surface médiane y_mid(x, z) d'une pièce orientée (addendum P1 §2)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from build123d import Solid
from scipy.interpolate import RegularGridInterpolator
from shapely.geometry import LineString, Polygon
from shapely.ops import unary_union

from aerorib.slicing import slice_layers


def mid_line(envelope, xs: np.ndarray, y_lo: float, y_hi: float) -> tuple[np.ndarray, np.ndarray]:
    ymid = np.full(len(xs), np.nan)
    thick = np.zeros(len(xs))
    for i, x in enumerate(xs):
        cut = envelope.intersection(LineString([(x, y_lo), (x, y_hi)]))
        if cut.is_empty:
            continue
        y0, y1 = cut.bounds[1], cut.bounds[3]
        ymid[i] = (y0 + y1) / 2
        thick[i] = y1 - y0
    return ymid, thick


def camber_offset(section: Polygon, n: int = 41) -> float:
    env = Polygon(section.exterior)
    coords = np.asarray(env.exterior.coords)
    le, te = coords[np.argmin(coords[:, 0])], coords[np.argmax(coords[:, 0])]
    xs = np.linspace(le[0], te[0], n + 2)[1:-1]
    x0, y0, x1, y1 = env.bounds
    ymid, _ = mid_line(env, xs, y0 - 1, y1 + 1)
    chord_y = le[1] + (te[1] - le[1]) * (xs - le[0]) / (te[0] - le[0])
    return float(np.nanmean(ymid - chord_y))


@dataclass(frozen=True, eq=False)
class MidSurface:
    xs: np.ndarray
    zs: np.ndarray
    ymid: np.ndarray  # (nx, nz)
    thick: np.ndarray  # (nx, nz)

    @classmethod
    def from_skin(cls, skin: Solid, nx: int = 101, nz: int = 22, tol: float = 0.02) -> "MidSurface":
        bb = skin.bounding_box()
        xs = np.linspace(bb.min.X, bb.max.X, nx)
        zs = np.linspace(bb.min.Z + 0.3, bb.max.Z - 0.3, nz)
        ymid = np.full((nx, nz), np.nan)
        thick = np.zeros((nx, nz))
        for j, layer in enumerate(slice_layers(skin, zs, tol)):
            if not layer.polygons:
                continue
            env = unary_union([Polygon(p.exterior) for p in layer.polygons])
            ymid[:, j], thick[:, j] = mid_line(env, xs, bb.min.Y - 5, bb.max.Y + 5)
        for j in range(nz):
            ok = ~np.isnan(ymid[:, j])
            ymid[:, j] = np.interp(xs, xs[ok], ymid[ok, j]) if ok.any() else 0.0
        return cls(xs, zs, ymid, thick)

    def at(self, x: np.ndarray, z: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        x = np.asarray(x, dtype=float)
        z = np.asarray(z, dtype=float)
        pts = np.column_stack([np.clip(x, self.xs[0], self.xs[-1]), np.clip(z, self.zs[0], self.zs[-1])])
        ym = RegularGridInterpolator((self.xs, self.zs), self.ymid)(pts)
        th = RegularGridInterpolator((self.xs, self.zs), self.thick)(pts)
        inside = (x >= self.xs[0]) & (x <= self.xs[-1])
        return ym, np.where(inside, th, 0.0)
