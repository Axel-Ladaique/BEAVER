import math

import numpy as np
import pytest
from build123d import Box
from shapely.geometry import LineString, Point

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
    b = Bore("S", 40.0 * math.sin(math.radians(45)), 0.0, 4.0, 0.5, 2.0, 1, True, 0.0, 100.0, 0.1, 0.15)
    d = math.sin(math.radians(45))
    zones = bore_zones_2d(b, 0.0, d, d, 0.0, 100.0, slit=0.1)
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


def test_bore_zones_limited_to_bore_extent():
    # trou de z = 0 (débouchant) à z = 30 dans une pièce de 100 mm ; plaque à 45° : z = s·cos45
    d = math.sin(math.radians(45))
    b = Bore("S", 40.0 * d, 0.0, 4.0, 0.5, 2.0, 1, True, 0.0, 30.0, 0.1, 0.15)
    zones = bore_zones_2d(b, 0.0, d, d, 0.0, 100.0, slit=0.1)
    s_hi = (30.0 + 0.15) / d + 0.05 / d  # marge du jeu, plus la demi-épaisseur de plaque
    assert max(z.bounds[2] for z in zones) == pytest.approx(s_hi)
    above = bore_zones_2d(b, 0.0 - 40 * d, d, d, 0.0, 100.0, slit=0.1)  # plaque qui croise l'axe à z ≈ 56
    assert above == []


def test_bore_zone_covers_the_plate_thickness():
    # la zone (polygone) doit contenir l'ellipse exacte du manchon élargie de la demi-épaisseur de plaque
    d0, d1 = math.sin(math.radians(45)), math.cos(math.radians(45))
    b = Bore("S", 20.0, 1.0, 1.2, 0.7, 1.0, 1, False, 0.0, 100.0, 0.15, 0.0)
    ellipse, _ = bore_zones_2d(b, 0.0, d0, d1, 0.0, 100.0, slit=0.1)
    s0, r = 20.0 / d0, 1.2 + 0.7 + 0.05
    for t in np.linspace(0, 2 * math.pi, 721):
        pt = Point(s0 + r / d0 * math.cos(t), 1.0 + r * math.sin(t))
        assert ellipse.covers(pt), t


def test_depth_transition_is_a_step_not_a_ramp():
    # Une rampe entre un échantillon profond et un échantillon trop mince donne, en tranche, des
    # pointes de fente obliques qui pincent la bande de séparation : la transition doit être une marche.
    ss = np.linspace(-5, 100, 421)  # pas de 0,25 mm pour situer la transition
    th = np.where(ss < 50.1, 20.0, 2.0)
    (top,) = [p for t, p in plate_profiles_2d(ss, np.zeros_like(ss), th, [], **KW, edge_vertices=16) if t]
    assert top.bounds[0] == pytest.approx(-5.0) and top.bounds[2] == pytest.approx(50.1, abs=0.2)
    for s in (0.0, 25.0, 49.8):
        (seg,) = column(top, s)
        assert seg.bounds[1] == pytest.approx(0.3)
    assert len(top.exterior.coords) <= 16 + 4  # le bord reste échantillonné grossièrement (coût booléen)


@pytest.mark.parametrize("anchor_gap, below", [(0.0, False), (1.0, True)])
def test_strip_on_both_sides_when_anchored(anchor_gap, below):
    # plaque à 45°, trou en s0 = 40
    d = math.sin(math.radians(45))
    b = Bore("S", 40.0 * d, 0.0, 4.0, 0.5, 2.0, 1, True, 0.0, 100.0, 0.15, 0.15, anchor_gap)
    zones = bore_zones_2d(b, 0.0, d, d, 0.0, 100.0, slit=0.1)
    # bande sans nervure au-dessus (côté fente) toujours ; en dessous (côté attache) seulement si ancré
    assert any(z.contains(Point(40.0, 10.0)) for z in zones)
    assert any(z.contains(Point(40.0, -10.0)) for z in zones) is below
