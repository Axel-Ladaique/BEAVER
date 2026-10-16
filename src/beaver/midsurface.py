"""Surface médiane y_mid(x, z) d'une pièce orientée (addendum P1 §2)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from build123d import Solid
from scipy.interpolate import RegularGridInterpolator
from shapely.geometry import LineString, Polygon
from shapely.ops import unary_union

from beaver.slicing import slice_layers


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
    # bords d'attaque et de fuite exacts de chaque rangée z (NaN : rangée sans matière) ; None : interpolation à x
    # fixe entre deux rangées
    le: np.ndarray | None = None
    te: np.ndarray | None = None

    @classmethod
    def from_skin(cls, skin: Solid, nx: int = 101, nz: int = 22, tol: float = 0.02) -> "MidSurface":
        bb = skin.bounding_box()
        xs = np.linspace(bb.min.X, bb.max.X, nx)
        zs = np.linspace(bb.min.Z + 0.3, bb.max.Z - 0.3, nz)
        ymid = np.full((nx, nz), np.nan)
        thick = np.zeros((nx, nz))
        le, te = np.full(nz, np.nan), np.full(nz, np.nan)
        for j, layer in enumerate(slice_layers(skin, zs, tol)):
            if not layer.polygons:
                continue
            env = unary_union([Polygon(p.exterior) for p in layer.polygons])
            ymid[:, j], thick[:, j] = mid_line(env, xs, bb.min.Y - 5, bb.max.Y + 5)
            if env.geom_type == "Polygon":  # plusieurs corps dans la rangée (aileron…) : lecture à x fixe
                le[j], te[j] = env.bounds[0], env.bounds[2]
        for j in range(nz):
            ok = ~np.isnan(ymid[:, j])
            ymid[:, j] = np.interp(xs, xs[ok], ymid[ok, j]) if ok.any() else 0.0
        return cls(xs, zs, ymid, thick, le, te)

    def at(self, x: np.ndarray, z: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """(y médian, épaisseur) en (x, z). Entre deux rangées z de la grille, chaque rangée est lue à la même position
        relative le long de la corde ξ = (x − x_BA(z)) / (x_BF(z) − x_BA(z)), bords interpolés linéairement en z —
        pas à x fixe : sur un bord en flèche ou effilé, x fixe mélangerait deux sections décalées (un point juste
        devant le nez y aurait encore de l'épaisseur). Corde constante et sans flèche : identique à x fixe. Une
        rangée sans matière (ou sans bords connus) est lue à x fixe."""
        x = np.asarray(x, dtype=float)
        z = np.asarray(z, dtype=float)
        inside = (x >= self.xs[0]) & (x <= self.xs[-1])
        if self.le is None or self.te is None or len(self.zs) < 2:
            pts = np.column_stack([np.clip(x, self.xs[0], self.xs[-1]), np.clip(z, self.zs[0], self.zs[-1])])
            ym = RegularGridInterpolator((self.xs, self.zs), self.ymid)(pts)
            th = RegularGridInterpolator((self.xs, self.zs), self.thick)(pts)
            return ym, np.where(inside, th, 0.0)
        zc = np.clip(z, self.zs[0], self.zs[-1])
        j1 = np.clip(np.searchsorted(self.zs, zc), 1, len(self.zs) - 1)
        j0 = j1 - 1
        t = (zc - self.zs[j0]) / (self.zs[j1] - self.zs[j0])
        le0, le1, te0, te1 = self.le[j0], self.le[j1], self.te[j0], self.te[j1]
        known = ~(np.isnan(le0) | np.isnan(le1) | np.isnan(te0) | np.isnan(te1))
        le_z = np.where(known, le0 + (le1 - le0) * t, 0.0)
        te_z = np.where(known, te0 + (te1 - te0) * t, 1.0)
        xi = (x - le_z) / np.maximum(te_z - le_z, 1e-9)
        ym, th = np.zeros(len(x)), np.zeros(len(x))
        for j, w in ((j0, 1.0 - t), (j1, t)):
            le_j, te_j = self.le[j], self.te[j]
            xj = np.where(known, le_j + xi * (te_j - le_j), x)
            xj = np.clip(xj, self.xs[0], self.xs[-1])
            for k in np.unique(j):
                m = j == k
                ym[m] += w[m] * np.interp(xj[m], self.xs, self.ymid[:, k])
                th[m] += w[m] * self._row_thickness(k, xj[m])
        return ym, np.where(inside, th, 0.0)

    def _row_thickness(self, k: int, x: np.ndarray) -> np.ndarray:
        """Épaisseur de la rangée k en x : nœuds de la grille dans la section, plus les bords exacts (épaisseur nulle) —
        sinon l'interpolation entre le dernier nœud vide et le premier plein mettrait de l'épaisseur devant le nez
        quand le bord tombe entre deux nœuds (flèche, effilement)."""
        le, te = self.le[k], self.te[k]
        if np.isnan(le) or np.isnan(te):
            return np.interp(x, self.xs, self.thick[:, k])
        keep = (self.xs > le) & (self.xs < te)
        xa = np.concatenate([[le], self.xs[keep], [te]])
        ta = np.concatenate([[0.0], self.thick[keep, k], [0.0]])
        return np.interp(x, xa, ta, left=0.0, right=0.0)
