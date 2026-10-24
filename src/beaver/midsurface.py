"""Surface médiane y_mid(x, z) d'une pièce orientée (addendum P1 §2)."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property

import numpy as np
import shapely
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


# Échantillons fins près des bords exacts de chaque rangée (`MidSurface.edges`) : premier à _EDGE_FIRST mm du bord,
# puis pas croissant de _EDGE_GROWTH jusqu'à _EDGE_REACH pas de grille. La grille régulière (101 nœuds sur la corde
# de la boîte, 12 mm sur une aile de 1,2 m) ne voit pas un nez émoussé : entre le bord (épaisseur nulle) et le premier
# nœud, la peau devient une rampe droite, moins raide que la paroi réelle — des fentes passaient alors à 1 mm du nez,
# le longeaient et entamaient la peau (`ribs.steep_samples`).
_EDGE_FIRST = 0.05
_EDGE_GROWTH = 1.2
_EDGE_REACH = 2.0


def edge_offsets(step: float) -> np.ndarray:
    """Distances au bord des échantillons fins (pas de grille `step`)."""
    n = max(int(np.ceil(np.log(_EDGE_REACH * step / _EDGE_FIRST) / np.log(_EDGE_GROWTH))), 0) + 1
    return _EDGE_FIRST * _EDGE_GROWTH ** np.arange(n)


def edge_samples(env, le: float, te: float, step: float, y_lo: float, y_hi: float):
    """(x, y médian, épaisseur) aux abscisses fines près du bord d'attaque et du bord de fuite d'une section `env`
    (x croissant, dans ]le, te[, sans recouvrement entre les deux bords)."""
    d = edge_offsets(step)
    d = d[d < (te - le) / 2]
    xs = np.unique(np.concatenate([le + d, te - d]))
    if not len(xs):
        return xs, xs, xs
    cut = shapely.intersection(env, shapely.linestrings([[(x, y_lo), (x, y_hi)] for x in xs]))
    b = shapely.bounds(cut)
    ok = ~shapely.is_empty(cut)
    th = np.where(ok, b[:, 3] - b[:, 1], 0.0)
    ym = np.where(ok, (b[:, 1] + b[:, 3]) / 2, np.nan)
    return xs[ok], ym[ok], th[ok]


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
    # par rangée : (x, y médian, épaisseur) fins près des bords exacts (`edge_samples`), ou None : grille seule
    edges: tuple | None = None

    @classmethod
    def from_skin(cls, skin: Solid, nx: int = 101, nz: int = 22, tol: float = 0.02) -> "MidSurface":
        bb = skin.bounding_box()
        xs = np.linspace(bb.min.X, bb.max.X, nx)
        zs = np.linspace(bb.min.Z + 0.3, bb.max.Z - 0.3, nz)
        ymid = np.full((nx, nz), np.nan)
        thick = np.zeros((nx, nz))
        le, te = np.full(nz, np.nan), np.full(nz, np.nan)
        empty = np.empty(0)
        edges = [(empty, empty, empty)] * nz
        for j, layer in enumerate(slice_layers(skin, zs, tol)):
            if not layer.polygons:
                continue
            env = unary_union([Polygon(p.exterior) for p in layer.polygons])
            ymid[:, j], thick[:, j] = mid_line(env, xs, bb.min.Y - 5, bb.max.Y + 5)
            if env.geom_type == "Polygon":  # plusieurs corps dans la rangée (aileron…) : lecture à x fixe
                le[j], te[j] = env.bounds[0], env.bounds[2]
                step = xs[1] - xs[0] if nx > 1 else te[j] - le[j]
                edges[j] = edge_samples(env, le[j], te[j], step, bb.min.Y - 5, bb.max.Y + 5)
        for j in range(nz):
            ok = ~np.isnan(ymid[:, j])
            ymid[:, j] = np.interp(xs, xs[ok], ymid[ok, j]) if ok.any() else 0.0
        return cls(xs, zs, ymid, thick, le, te, tuple(edges))

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
                xr, yr, tr = self._rows[k]
                ym[m] += w[m] * np.interp(xj[m], xr, yr)
                th[m] += w[m] * np.interp(xj[m], xr, tr, left=0.0, right=0.0)
        return ym, np.where(inside, th, 0.0)

    @cached_property
    def _rows(self) -> list[tuple[np.ndarray, np.ndarray, np.ndarray]]:
        """(x, y médian, épaisseur) de chaque rangée, lus par interpolation linéaire en x : nœuds de la grille dans la
        section, échantillons fins près des bords (`edges`) et bords exacts (épaisseur nulle) — sans les bords,
        l'interpolation entre le dernier nœud vide et le premier plein mettrait de l'épaisseur devant le nez quand le
        bord tombe entre deux nœuds (flèche, effilement) ; sans les échantillons fins, un nez émoussé deviendrait une
        rampe d'un pas de grille. Rangée sans bords connus : grille seule."""
        out = []
        for k in range(len(self.zs)):
            le, te = self.le[k], self.te[k]
            if np.isnan(le) or np.isnan(te):
                out.append((self.xs, self.ymid[:, k], self.thick[:, k]))
                continue
            ex, ey, et = self.edges[k] if self.edges is not None else (np.empty(0),) * 3
            # nœuds de la grille au-delà des échantillons fins de chaque bord
            half = (le + te) / 2
            lo = ex[ex < half].max(initial=le)
            hi = ex[ex > half].min(initial=te)
            keep = (self.xs > lo) & (self.xs < hi)
            xa = np.concatenate([[le], ex, self.xs[keep], [te]])
            ya = np.concatenate([[np.nan], ey, self.ymid[keep, k], [np.nan]])
            ta = np.concatenate([[0.0], et, self.thick[keep, k], [0.0]])
            order = np.argsort(xa, kind="stable")
            xa, ya, ta = xa[order], ya[order], ta[order]
            ok = ~np.isnan(ya)  # y médian aux bords exacts : celui du point voisin (lu dans la section)
            ya = np.interp(xa, xa[ok], ya[ok]) if ok.any() else np.interp(xa, self.xs, self.ymid[:, k])
            out.append((xa, ya, ta))
        return out
