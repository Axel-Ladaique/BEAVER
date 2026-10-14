"""Évidements des nervures : profil 2D d'une plaque (ponts, profondeur gardée, rampes au surplomb max en z)."""

import math

import numpy as np
import pytest
from build123d import Box, Location
from shapely.geometry import LineString, box

from beaver.config import RibLayer, load_config
from beaver.features.ribs import (
    CutGuides,
    HollowEdge,
    HollowSpec,
    bridge_positions,
    diamond_profiles,
    plate_lines,
    plate_profiles_2d,
)
from beaver.midsurface import MidSurface
from beaver.pipeline import rib_crossings, rib_options

SS = np.linspace(-5, 100, 211)  # pas de 0,5 mm
YM = np.zeros_like(SS)
TH = np.full_like(SS, 20.0)
KW = dict(gap=0.6, min_depth=1.0, ytop=15.0, ybot=-15.0, edge_vertices=16, band_tol=0.045)
D1 = math.cos(math.radians(45))
K = math.tan(math.radians(45)) * D1  # avance max du fond de fente par mm de s (surplomb 45°, plaque à 45°)
CLEAR = 0.21  # demi-largeur de ligne : la rampe s'arrête à cette hauteur au-dessus de la bande


def hollow(bridges=(30.0, 60.0), **kw):
    return HollowEdge(bridges=tuple(bridges), width=4.0, keep_ratio=0.3, keep_min=2.0, slope=K, clearance=CLEAR, **kw)


def tip(poly, s, top=True):
    """Fond de fente (bord intérieur) de la plaque à l'abscisse s ; None hors plaque."""
    cut = poly.intersection(LineString([(s, -100), (s, 100)]))
    if cut.is_empty:
        return None
    return cut.bounds[1] if top else cut.bounds[3]


def side(profs, top):
    polys = [p for t, p in profs if t is top]
    assert len(polys) == 1
    return polys[0]


@pytest.mark.parametrize("top", [True, False])
def test_bridges_full_depth_elsewhere_kept_depth(top):
    poly = side(plate_profiles_2d(SS, YM, TH, [], **KW, hollow=hollow()), top)
    sign = 1 if top else -1
    assert poly.is_valid and not poly.interiors
    for s in (28.05, 30.0, 31.95, 58.05, 60.0, 61.95):  # ponts [s ± 2]
        assert tip(poly, s, top) == pytest.approx(sign * 0.3, abs=0.05), s
    # profondeur gardée : max(keep_min, 0,3 × demi-épaisseur 10) = 3 mm sous la peau (y = ±10)
    for s in (32.3, 40.0, 45.0, 48.0, 62.3, 80.0):  # rampe avant le pont de 60 : à partir de s ≈ 48,8
        assert tip(poly, s, top) == pytest.approx(sign * 7.0, abs=0.06), s


@pytest.mark.parametrize("top", [True, False])
def test_ramp_below_bridge_respects_overhang_in_print_z(top):
    """En montant (s croissant ⇔ z croissant, z = zmin + s·d1), le fond de fente n'avance vers la bande que de
    tan(surplomb) par mm de z, sauf la marche finale de `clearance` au pied du pont (contre la bande : « marche, pas
    rampe ») ; il peut reculer d'un coup (marche) au-dessus du pont."""
    poly = side(plate_profiles_2d(SS, YM, TH, [], **KW, hollow=hollow()), top)
    sign = 1 if top else -1
    ss = np.arange(0.0, 95.0, 0.05)
    tips = np.array([sign * tip(poly, s, top) for s in ss])  # distance à la surface médiane, > 0
    advance = -np.diff(tips) / (np.diff(ss) * D1)  # avance vers la bande par mm de z d'impression
    feet = np.isclose(ss[:-1], 27.95) | np.isclose(ss[:-1], 57.95)  # pieds des ponts (28, 58) entre deux échantillons
    assert advance[~feet].max() <= math.tan(math.radians(45)) + 1e-6
    assert (-np.diff(tips))[feet] == pytest.approx([CLEAR + 0.05 * K] * 2, abs=0.01)  # marche + 0,05 mm de rampe
    # la rampe existe bien : sous le pied du pont (s = 28), la plaque descend progressivement depuis la profondeur
    # gardée jusqu'à CLEAR au-dessus de la bande
    ramp = (7.0 - 0.3 - CLEAR) / K
    assert tip(poly, 28.0 - ramp + 0.5, top) * sign == pytest.approx(7.0 - 0.5 * K, abs=0.06)
    assert tip(poly, 27.9, top) * sign == pytest.approx(0.3 + CLEAR + 0.1 * K, abs=0.03)
    # marche verticale au-dessus du pont : bande en 31,95, profondeur gardée en 32,05
    assert tip(poly, 32.05, top) * sign == pytest.approx(7.0, abs=0.06)


def test_off_is_unchanged():
    assert plate_profiles_2d(SS, YM, TH, [], **KW) == plate_profiles_2d(SS, YM, TH, [], **KW, hollow=None)


def test_no_hollow_where_saving_is_small():
    th = np.where(SS < 50, 20.0, 4.0)  # 4 mm : bord évidé à 2 mm sous la peau ⇒ y = 0 < bande, gain nul
    poly = side(plate_profiles_2d(SS, YM, th, [], **KW, hollow=hollow(bridges=())), True)
    assert tip(poly, 25.0) == pytest.approx(7.0, abs=0.06)
    for s in (60.0, 80.0):
        assert tip(poly, s) == pytest.approx(0.3, abs=0.05)


def test_partial_depth_layer_hollowed_only_below_kept_depth():
    shallow = plate_profiles_2d(SS, YM, TH, [], **KW, depth=3.0)
    assert plate_profiles_2d(SS, YM, TH, [], **KW, depth=3.0, hollow=hollow()) == shallow  # 3 mm = profondeur gardée
    deep = side(plate_profiles_2d(SS, YM, TH, [], **KW, depth=6.0, hollow=hollow()), True)
    assert tip(deep, 30.0) == pytest.approx(4.0, abs=0.06) and tip(deep, 45.0) == pytest.approx(7.0, abs=0.06)


def test_full_around_zones_and_ends():
    zone = box(44.0, -2.0, 46.0, 2.0)
    profs = plate_profiles_2d(SS, YM, TH, [zone], **KW, hollow=hollow(bridges=()))
    top = side(profs, True)
    # à une largeur de pont de la zone : pleine hauteur ; plus loin : évidé
    for s in (40.5, 49.5):
        assert tip(top, s) == pytest.approx(0.3, abs=0.05), s
    assert tip(top, 30.0) == pytest.approx(7.0, abs=0.06)
    ends = [(0.0, 1.0), (90.0, 1.0)]
    ss = np.unique(np.concatenate([SS, [a + k * h for a, h in ends for k in (-1, 0, 1)]]))
    th = np.full_like(ss, 20.0)
    top = side(plate_profiles_2d(ss, np.zeros_like(ss), th, [], **KW, ends=ends, hollow=hollow(bridges=())), True)
    for s in (0.0, 3.0, 4.9, 85.1, 87.0, 89.9):  # fenêtre de bout ± (h + largeur de pont)
        assert tip(top, s) == pytest.approx(0.3, abs=0.05), s
    assert tip(top, 45.0) == pytest.approx(7.0, abs=0.06)


def test_no_micro_edges_on_band():
    poly = side(plate_profiles_2d(SS, YM, TH, [], **KW, hollow=hollow()), True)
    pts = np.asarray(poly.exterior.coords)
    seg = np.hypot(*np.diff(pts, axis=0).T)
    assert seg.min() >= 0.05 - 1e-9


SPEC = dict(bridge_width=4.0, pitch=30.0, keep_ratio=0.3, keep_min=2.0, max_overhang_deg=45.0)


def test_bridges_at_crossings_with_the_other_family():
    class BB:  # boîte englobante minimale
        class min:
            X, Y, Z = 0.0, -10.0, 5.0

        class max:
            X, Y, Z = 100.0, 10.0, 85.0

    layer = RibLayer(spacing=20.0, phase=3.0)
    lines = plate_lines(BB, layer, 45.0)
    d0 = d1 = math.sin(math.radians(45))
    spec = HollowSpec(mode="croisements", **SPEC)
    for sign, c in lines[:5]:
        s_b = bridge_positions(spec, sign, c, sign * d0, lines, s_ref=0.0, s_lo=-5.0, s_hi=130.0)
        assert len(s_b) >= 4
        assert np.allclose(np.diff(s_b), 20.0 / (2 * d0))  # pas des croisements le long de la plaque
        for s in s_b:  # une plaque de l'autre famille passe par ce point (x, z), plan médian
            x = c + s * sign * d0
            assert min(abs(x - (co - s * sign * d0)) for so, co in lines if so == -sign) < 1e-9


def test_bridges_at_regular_pitch_anchored_on_global_box():
    spec = HollowSpec(mode="pas", **SPEC)
    s_b = bridge_positions(spec, 1, 0.0, 0.7, [], s_ref=-12.0, s_lo=-5.0, s_hi=100.0)
    assert np.allclose(s_b, [-12.0 + 30.0 * k for k in range(1, 4)])  # -12 : hors de la plaque


def _box_mid():
    skin = Box(100, 20, 80).moved(Location((50, 0, 40)))
    return skin.bounding_box(), MidSurface.from_skin(skin, 41, 9, 0.05)


OPTS = dict(angle_deg=45.0, gap=0.6, slit=0.1, min_depth=1.0, line_width=0.42, max_mouth=0.5)


def test_diamond_profiles_off_and_none_identical():
    bb, mid = _box_mid()
    ref = diamond_profiles(bb, mid, [], RibLayer(spacing=20.0), **OPTS)
    assert diamond_profiles(bb, mid, [], RibLayer(spacing=20.0), **OPTS, hollow=None) == ref
    cfg = load_config(overrides={"rib_layers": [{"spacing": 20}]})
    assert rib_options(cfg, bb, 0)["hollow"] is None


def test_diamond_profiles_hollow_bridges_meet_at_crossings():
    bb, mid = _box_mid()
    spec = HollowSpec(mode="croisements", **SPEC)
    # espacement 40 : croisements tous les 28,3 mm le long de la plaque ; pont 4 mm + rampe ≈ 9,5 mm (6,7 mm à 0,707)
    profs = diamond_profiles(bb, mid, [], RibLayer(spacing=40.0), **OPTS, hollow=spec)
    full = diamond_profiles(bb, mid, [], RibLayer(spacing=40.0), **OPTS)
    assert len(profs) == len(full)
    inside = box(-1e3, -10.0, 1e3, 10.0)  # matière retirée dans l'aile (les plaques dépassent de la peau)
    area = lambda ps: sum(p.polygon.intersection(inside).area for p in ps)  # noqa: E731
    assert area(profs) < 0.75 * area(full)
    assert all(p.polygon.is_valid and not p.polygon.interiors for p in profs)


def test_rib_options_carry_layer_hollow():
    cfg = load_config(overrides={
        "ribs_hollow": {"mode": "pas", "pitch": 25},
        "rib_layers": [{"spacing": 20}, {"name": "peau", "depth": 3, "hollow": {"mode": "off"}}],
    })
    bb, _ = _box_mid()
    h = rib_options(cfg, bb, 0)["hollow"]
    assert h == HollowSpec(mode="pas", bridge_width=4.0, pitch=25.0, keep_ratio=0.3, keep_min=2.0,
                           max_overhang_deg=cfg.preset.max_overhang_deg, ramp_factor=1.0)
    assert rib_options(cfg, bb, 1)["hollow"] is None


@pytest.mark.parametrize("mode, period", [("croisements", 15.0), ("pas", 30.0 * math.cos(math.radians(45)))])
def test_bridge_steps_are_critical_planes_for_z_cuts(mode, period):
    off = load_config(overrides={"rib_layers": [{"spacing": 30}]})
    on = load_config(overrides={"rib_layers": [{"spacing": 30}], "ribs_hollow": {"mode": mode}})
    base = rib_crossings(off, 0.0, 0.0, 100.0)
    planes = rib_crossings(on, 0.0, 0.0, 100.0)
    lift = 4.0 * math.cos(math.radians(45)) / 2  # marches au pied et au-dessus du pont : pont ∓ w·d1/2
    assert set(base) <= set(planes)
    for k in (1, 2, 3):
        for off_ in (-lift, lift):
            assert any(abs(p - (k * period + off_)) < 1e-9 for p in planes), (k, off_)


def _tips_along(prof, top=True):
    """Fonds de fente d'une plaque (distance à y = 0) tous les 0,05 mm de s."""
    s0, _, s1, _ = prof.polygon.bounds
    ss = np.arange(s0 + 0.1, s1 - 0.1, 0.05)
    t = [tip(prof.polygon, s, top) for s in ss]
    keep = [i for i, v in enumerate(t) if v is not None]
    return ss[keep], np.abs(np.array([t[i] for i in keep]))


@pytest.mark.parametrize("factor", [1.0, 0.5])
def test_diamond_ramp_slope_is_overhang_in_print_z(factor):
    """Pente des rampes de `diamond_profiles` : tan(surplomb) × ramp_factor par mm de z d'impression (d1 = cos 45°)."""
    bb, mid = _box_mid()
    spec = HollowSpec(mode="croisements", **SPEC, ramp_factor=factor)
    tops = [p for p in diamond_profiles(bb, mid, [], RibLayer(spacing=40.0), **OPTS, hollow=spec) if p.top]
    prof = max(tops, key=lambda p: p.polygon.bounds[2] - p.polygon.bounds[0])  # la plus longue : plusieurs ponts
    ss, tips = _tips_along(prof)
    adv = -np.diff(tips) / (np.diff(ss) * prof.d1)  # avance vers la bande par mm de z
    steps = -np.diff(tips) > 0.1  # marches au pied des ponts (clearance)
    assert adv[~steps].max() == pytest.approx(math.tan(math.radians(45)) * factor, rel=0.02)


def test_crossings_with_global_anchor_are_full_on_both_plates():
    bb, mid = _box_mid()

    class Anchor:  # boîte globale décalée (aile en plusieurs corps)
        class min:
            X, Y, Z = -7.3, -10.0, -23.0

        class max:
            X, Y, Z = 100.0, 10.0, 80.0

    layer = RibLayer(spacing=40.0, phase=2.5)
    spec = HollowSpec(mode="croisements", **SPEC)
    profs = [p for p in diamond_profiles(bb, mid, [], layer, **OPTS, hollow=spec, anchor=Anchor) if p.top]
    assert {p.family for p in profs} == {1, -1}
    nodes = 0
    for a in (p for p in profs if p.family == 1):
        for b in (p for p in profs if p.family == -1):
            s = (b.c - a.c) / (2 * a.d0)  # même s sur les deux plaques (même zmin)
            if not (8.0 < s < 100.0):  # loin des bouts (pleins de toute façon)
                continue
            for p in (a, b):
                t = tip(p.polygon, s)
                if t is not None:
                    nodes += 1
                    assert t == pytest.approx(0.3, abs=0.06)
            mid_s = s + 20.0 / (2 * a.d0) / 2  # entre deux croisements : évidé
            t = tip(a.polygon, mid_s)
            if t is not None and 8.0 < mid_s < 100.0:
                assert t > 3.0
    assert nodes >= 6


def test_cut_guides_windows():
    g = CutGuides(z=(40.0,), lines=((10.0, 0.0, 0.0, 30.0),))
    d = math.sin(math.radians(45))
    # plaque x = 0 + s·d, z = s·d : coupe en Z à s = 40/d ; plan x = 10 (z ≤ 30) à s = 10/d (z = 10, dans le tronçon)
    flat = lambda w: [v for iv in w for v in iv]  # noqa: E731
    assert flat(g.windows(0.0, d, d, 0.0, 4.0, -5.0, 150.0)) == pytest.approx([40 / d - 4, 40 / d + 4, 10 / d - 4, 10 / d + 4])
    # plan rencontré hors de son tronçon (z = 50 > 30 + 4) : pas de fenêtre
    assert flat(g.windows(-40.0, d, d, 0.0, 4.0, -5.0, 150.0)) == pytest.approx([40 / d - 4, 40 / d + 4])
    # plaque parallèle au plan de coupe (x = 10 + z) et proche : toute la plaque pleine
    par = CutGuides(lines=((10.0 + 1.0, 1.0, 0.0, 100.0),))
    assert par.windows(10.0, d, d, 0.0, 4.0, -5.0, 150.0) == ((-6.0, 151.0),)
