"""Baies de servo (spec servo §4) : poche ouverte sur une peau, prolongée par deux rampes en envergure.

Repère orienté : Z envergure, X corde, Y épaisseur (extrados +Y). La boîte du corps `SERVO…` est l'espace utile.
La poche est un prisme le long de X sur [x0, x1] dont le profil (z, y) est un trapèze : le fond de la boîte de z0
à z1, puis deux rampes qui remontent du fond vers l'extérieur, inclinées de `max_overhang_deg` par rapport à Z
(surplomb max de la matière, aux deux bouts : l'intérieur est creux, la paroi du bas surplombe aussi).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from build123d import Face, GeomType, Solid, Vector, Wire, extrude
from shapely import affinity
from shapely.geometry import Polygon, box

from aerorib.config import Config
from aerorib.midsurface import MidSurface

ALIGN_TOL_DEG = 2.0  # écart max des faces de la boîte aux axes orientés
OUTSIDE = 5.0  # mm : la poche dépasse la boîte englobante de la pièce de cette marge (côté ouverture)
_SAMPLES = 9  # échantillons par direction (x, z) pour situer la boîte par rapport aux peaux
_FAR = 1e4


class ServoError(ValueError):
    """Baie de servo non prise en charge (message en français)."""


@dataclass(frozen=True)
class ServoBay:
    name: str
    side: int  # +1 : ouverte sur l'extrados (+Y) ; -1 : sur l'intrados
    x0: float  # boîte orientée (espace utile)
    x1: float
    y_floor: float  # fond de la baie (face de la boîte côté opposé à l'ouverture)
    y_box: float  # face de la boîte côté ouverture (hors de la peau)
    z0: float
    z1: float
    depth: float  # écart max fond ↔ peau d'ouverture sur l'étendue en corde, aux deux bouts
    ramp: float  # Δ : longueur d'une rampe en envergure (depth / tan(max_overhang))
    run: float  # dz/dy des rampes = 1 / tan(max_overhang)
    y_out: float  # la poche s'étend jusqu'à cette ordonnée, au-delà de la peau
    zmin: float  # étendue de la pièce en envergure
    zmax: float

    @property
    def side_name(self) -> str:
        return "extrados" if self.side > 0 else "intrados"

    @property
    def opening(self) -> tuple[float, float]:
        """Étendue en envergure de l'ouverture dans la peau (rampes comprises), limitée à la pièce."""
        return max(self.z0 - self.ramp, self.zmin), min(self.z1 + self.ramp, self.zmax)


def _aligned_box(name: str, solid: Solid) -> tuple[float, float, float, float, float, float]:
    faces = solid.faces()
    if len(faces) != 6 or any(f.geom_type != GeomType.PLANE for f in faces):
        raise ServoError(f"{name} : la baie doit être une boîte (6 faces planes), {len(faces)} face(s) trouvée(s)")
    cos_tol = math.cos(math.radians(ALIGN_TOL_DEG))
    for f in faces:
        n = f.normal_at()
        if max(abs(n.X), abs(n.Y), abs(n.Z)) < cos_tol:
            raise ServoError(f"{name} : baie non alignée sur la corde / l'envergure (pris en charge plus tard)")
    bb = solid.bounding_box(optimal=True)
    return bb.min.X, bb.max.X, bb.min.Y, bb.max.Y, bb.min.Z, bb.max.Z


def _skins(mid: MidSurface, x, z) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    ym, th = mid.at(np.asarray(x, dtype=float), np.asarray(z, dtype=float))
    return ym + th / 2, ym - th / 2, th


def servo_bay(name: str, solid: Solid, bb, mid: MidSurface, cfg: Config) -> ServoBay:
    x0, x1, y0, y1, z0, z1 = _aligned_box(name, solid)
    xs, zs = np.meshgrid(np.linspace(x0, x1, _SAMPLES), np.linspace(z0, z1, _SAMPLES))
    top, bot, th = _skins(mid, xs.ravel(), zs.ravel())
    inside = th > 0
    top, bot = top[inside], bot[inside]
    if not inside.any():
        raise ServoError(f"{name} : la boîte ne traverse aucune peau (elle est hors de la pièce)")
    cross_top = bool(np.any((y1 > top) & (y0 < top)))
    cross_bot = bool(np.any((y0 < bot) & (y1 > bot)))
    if cross_top and cross_bot:
        raise ServoError(f"{name} : la boîte traverse l'extrados et l'intrados (une baie s'ouvre d'un seul côté)")
    if not (cross_top or cross_bot):
        raise ServoError(
            f"{name} : la boîte ne traverse aucune peau (elle doit dépasser de l'extrados ou de l'intrados)"
        )
    side = 1 if cross_top else -1
    floor, outer = (y0, y1) if side > 0 else (y1, y0)
    gap = float(np.min(floor - bot)) if side > 0 else float(np.min(top - floor))
    need = cfg.servo.min_floor_gap
    if gap < need:
        other = "l'intrados" if side > 0 else "l'extrados"
        raise ServoError(
            f"{name} : le fond de la baie laisse {gap:.2f} mm avant {other} (minimum servo.min_floor_gap = {need:g} mm)"
        )
    # profondeur : écart max fond ↔ peau d'ouverture sur l'étendue en corde, près de chaque bout
    xe = np.linspace(x0, x1, 4 * _SAMPLES)
    ze = np.concatenate([np.full(len(xe), z0), np.full(len(xe), z1)])
    top_e, bot_e, th_e = _skins(mid, np.concatenate([xe, xe]), ze)
    skin_e = (top_e if side > 0 else bot_e)[th_e > 0]
    depth = float(np.max(side * (skin_e - floor))) if len(skin_e) else 0.0
    depth = max(depth, 0.0)
    run = 1.0 / math.tan(math.radians(cfg.preset.max_overhang_deg))
    y_out = bb.max.Y + OUTSIDE if side > 0 else bb.min.Y - OUTSIDE
    return ServoBay(name, side, x0, x1, floor, outer, z0, z1, depth, depth * run, run, y_out, bb.min.Z, bb.max.Z)


def servo_bays(servos: Sequence[tuple[str, Solid]], skin: Solid, mid: MidSurface, cfg: Config) -> list[ServoBay]:
    """Baies des corps servo orientés (nom, solide) ; ServoError (français) si une boîte n'est pas prise en charge."""
    bb = skin.bounding_box()
    return [servo_bay(name, solid, bb, mid, cfg) for name, solid in servos]


def pocket_profile(bay: ServoBay) -> Polygon:
    """Profil (z, y) de la poche : fond de z0 à z1, rampes jusqu'à `y_out` (au-delà de la peau)."""
    h = abs(bay.y_out - bay.y_floor)
    reach = h * bay.run
    return Polygon(
        [(bay.z0, bay.y_floor), (bay.z1, bay.y_floor), (bay.z1 + reach, bay.y_out), (bay.z0 - reach, bay.y_out)]
    )


def pocket_solid(bay: ServoBay) -> Solid:
    """Outil de coupe de la poche : profil (z, y) extrudé le long de X sur [x0, x1]."""
    ring = [Vector(bay.x0, y, z) for z, y in list(pocket_profile(bay).exterior.coords)[:-1]]
    return extrude(Face(Wire.make_polygon(ring, close=True)), amount=bay.x1 - bay.x0, dir=Vector(1, 0, 0))


def pocket_section(bay: ServoBay, z: float) -> Polygon | None:
    """Coupe de la poche par le plan horizontal z, en (x, y) (aperçu)."""
    prof = pocket_profile(bay)
    lo, _, hi, _ = prof.bounds
    if not lo < z < hi:
        return None
    if z < bay.z0:
        y_ramp = bay.y_floor + bay.side * (bay.z0 - z) / bay.run
    elif z > bay.z1:
        y_ramp = bay.y_floor + bay.side * (z - bay.z1) / bay.run
    else:
        y_ramp = bay.y_floor
    ya, yb = sorted((y_ramp, bay.y_out))
    return box(bay.x0, ya, bay.x1, yb)


def pocket_zones_2d(
    bay: ServoBay, c: float, d0: float, d1: float, zmin: float, zmax: float, *, strip: float, slit: float
) -> list[Polygon]:
    """Zone sans nervure de la poche dans le repère (s, y) de la plaque (x = c + s·d0, z = zmin + s·d1).

    Poche agrandie de `strip` + slit/2 (demi-épaisseur de plaque) en x, z et y, puis coupée par le plan de la
    plaque : profil (z, y) ramené en s, limité aux s où la plaque traverse l'étendue en corde de la boîte.
    """
    grow = strip + slit / 2
    prof = pocket_profile(bay).buffer(grow, join_style="mitre")
    mapped = affinity.affine_transform(prof, [1 / d1, 0, 0, 1, -zmin / d1, 0])
    sa, sb = sorted(((bay.x0 - grow - c) / d0, (bay.x1 + grow - c) / d0))
    zone = mapped.intersection(box(sa, -_FAR, sb, _FAR))
    return [zone] if isinstance(zone, Polygon) and not zone.is_empty and zone.area > 0 else []
