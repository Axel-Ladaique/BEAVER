import math

import numpy as np
import pytest
from build123d import Box
from shapely.geometry import LineString

from aerorib.config import RibLayer
from aerorib.features.bores import Bore
from aerorib.features.ribs import bore_zones_2d, diamond_plates, plate_profiles_2d
from aerorib.midsurface import MidSurface

SS = np.linspace(-5, 100, 40)
YM = np.zeros_like(SS)
TH = np.full_like(SS, 20.0)
KW = dict(gap=0.6, min_depth=1.0, ytop=15.0, ybot=-15.0)


def column(poly, s):
    cut = poly.intersection(LineString([(s, -100), (s, 100)]))
    return [] if cut.is_empty else list(getattr(cut, "geoms", [cut]))


def test_profiles_leave_the_gap_band():
    profs = plate_profiles_2d(SS, YM, TH, [], **KW)
    tops = [p for top, p in profs if top]
    bots = [p for top, p in profs if not top]
    assert len(tops) == 1 and len(bots) == 1
    assert tops[0].bounds[1] == pytest.approx(0.3) and tops[0].bounds[3] == pytest.approx(15.0)
    assert bots[0].bounds[3] == pytest.approx(-0.3) and bots[0].bounds[1] == pytest.approx(-15.0)


def test_shallow_region_not_cut():
    th = np.where(SS < 50, 20.0, 2.0)  # 2.0 <= 0.6 + 2·1.0 → pas de coupe
    (top,) = [p for t, p in plate_profiles_2d(SS, YM, th, [], **KW) if t]
    for s in (60.0, 80.0):
        assert all(seg.bounds[1] >= 15.0 - 1e-9 for seg in column(top, s))


@pytest.mark.parametrize("top_side", [True, False])
def test_every_column_reaches_the_skin_around_a_bore(top_side):
    b = Bore("S", 40.0 * math.sin(math.radians(45)), 0.0, 4.0, 0.5, 2.0, 1, True)
    zones = bore_zones_2d(b, 0.0, math.sin(math.radians(45)))
    polys = [p for t, p in plate_profiles_2d(SS, YM, TH, zones, **KW) if t is top_side]
    far = 15.0 if top_side else -15.0
    for s in np.linspace(0, 99, 200):
        segs = [seg for p in polys for seg in column(p, s)]
        assert len(segs) <= 1, s
        if segs:
            assert segs[0].bounds[3 if top_side else 1] == pytest.approx(far)


def test_diamond_plates_on_box():
    part = Box(100, 20, 60)
    bb = part.bounding_box()
    mid = MidSurface(
        xs=np.linspace(bb.min.X, bb.max.X, 11), zs=np.linspace(bb.min.Z, bb.max.Z, 5),
        ymid=np.zeros((11, 5)), thick=np.full((11, 5), 20.0),
    )
    plates = diamond_plates(bb, mid, [], RibLayer(spacing=25.0), angle_deg=45.0, gap=0.6, slit=0.1, min_depth=1.0)
    assert plates and all(p.is_valid for p in plates)
    cut = part.cut(*plates)
    assert cut.is_valid and len(cut.solids()) == 1  # la bande de séparation garde la pièce d'un seul tenant
