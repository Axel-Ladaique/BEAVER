import math

import numpy as np
import pytest
from build123d import Box, Location
from shapely.geometry import LineString, Point

from aerorib.config import RibLayer
from aerorib.features.bores import Bore
from aerorib.features.ribs import (
    bore_zones_2d,
    diamond_plates,
    diamond_profiles,
    plate_profiles_2d,
    steep_samples,
    wall_limits,
)
from aerorib.midsurface import MidSurface

SS = np.linspace(-5, 100, 40)
YM = np.zeros_like(SS)
TH = np.full_like(SS, 20.0)
KW = dict(gap=0.6, min_depth=1.0, ytop=15.0, ybot=-15.0)
WALL = dict(line_width=0.42, max_mouth=0.5)  # largeur de ligne PLA Aero, bouche max par défaut


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
    plates = diamond_plates(bb, mid, [], RibLayer(spacing=25.0), angle_deg=45.0, gap=0.6, slit=0.1, min_depth=1.0, **WALL)
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


# --- doubles nervures : niveau peu profond (depth en mm) ---------------------------------------


def test_partial_depth_profiles_stay_near_the_skin():
    # épaisseur 20, profondeur 3 : haute de ym + 10 − 3 = 7 à ytop, basse de ybot à −7
    profs = plate_profiles_2d(SS, YM, TH, [], **KW, depth=3.0)
    (top,) = [p for t, p in profs if t]
    (bot,) = [p for t, p in profs if not t]
    assert top.bounds[1] == pytest.approx(7.0) and top.bounds[3] == pytest.approx(15.0)
    assert bot.bounds[1] == pytest.approx(-15.0) and bot.bounds[3] == pytest.approx(-7.0)


def test_partial_depth_follows_the_mid_surface_and_thickness():
    ym = 0.05 * SS  # surface médiane inclinée
    th = np.linspace(20.0, 12.0, len(SS))
    (top,) = [p for t, p in plate_profiles_2d(SS, ym, th, [], **KW, depth=3.0) if t]
    for s in (0.0, 40.0, 90.0):
        (seg,) = column(top, s)
        skin = float(np.interp(s, SS, ym + th / 2))
        assert seg.bounds[1] == pytest.approx(skin - 3.0, abs=0.05), s


def test_partial_depth_on_thin_section_falls_back_to_the_gap_band():
    # th/2 − 3 = 0 < gap/2 : la nervure va jusqu'à la bande de séparation, comme une plaque full
    th = np.full_like(SS, 6.0)
    shallow = plate_profiles_2d(SS, YM, th, [], **KW, depth=3.0)
    full = plate_profiles_2d(SS, YM, th, [], **KW)
    assert [(t, p.bounds) for t, p in shallow] == [(t, p.bounds) for t, p in full]
    (top,) = [p for t, p in shallow if t]
    assert top.bounds[1] == pytest.approx(0.3)


def test_partial_depth_keeps_pruning_and_bore_zones():
    th = np.where(SS < 50, 20.0, 2.0)  # partie trop mince : pas de coupe, comme en full
    (top,) = [p for t, p in plate_profiles_2d(SS, YM, th, [], **KW, depth=3.0) if t]
    assert all(seg.bounds[1] >= 15.0 - 1e-9 for seg in column(top, 80.0))
    d = math.sin(math.radians(45))
    b = Bore("S", 40.0 * d, 0.0, 4.0, 0.5, 2.0, 1, True, 0.0, 100.0, 0.1, 0.15)
    zones = bore_zones_2d(b, 0.0, d, d, 0.0, 100.0, slit=0.1)
    polys = [p for t, p in plate_profiles_2d(SS, YM, TH, zones, **KW, depth=3.0) if t]
    assert all(p.intersection(z).area < 1e-6 for p in polys for z in zones)  # bande sans nervure au-dessus du trou
    assert not any(p.contains(Point(40.0, 12.0)) for p in polys)


def test_diamond_plates_partial_depth_on_box():
    part = Box(100, 20, 60)
    bb = part.bounding_box()
    mid = MidSurface(
        xs=np.linspace(bb.min.X, bb.max.X, 11), zs=np.linspace(bb.min.Z, bb.max.Z, 5),
        ymid=np.zeros((11, 5)), thick=np.full((11, 5), 20.0),
    )
    layer = RibLayer(name="peau", spacing=10.0, depth=3.0, phase=5.0)
    plates = diamond_plates(bb, mid, [], layer, angle_deg=45.0, gap=0.6, slit=0.1, min_depth=1.0, **WALL)
    assert plates and all(p.is_valid for p in plates)
    for p in plates:  # chaque plaque reste dans les 3 mm sous la peau (y = ±10)
        pb = p.bounding_box()
        assert pb.min.Y >= 7.0 - 1e-6 or pb.max.Y <= -7.0 + 1e-6


def _naca_half_thickness(x, chord=200.0, t=0.12):
    xi = np.clip(x / chord, 0.0, 1.0)
    return 5 * t * chord * (0.2969 * np.sqrt(xi) - 0.1260 * xi - 0.3516 * xi**2 + 0.2843 * xi**3 - 0.1036 * xi**4)


def test_partial_depth_edge_follows_a_curved_skin():
    # plaque à 45° sur une corde NACA 4412-like de 200 mm : l'épaisseur varie fortement près du bord d'attaque.
    # Le bord intérieur (moins de sommets que d'échantillons) ne doit pas creuser plus de depth + 0,15.
    d0 = math.sin(math.radians(45))
    ss = np.linspace(-5, 300, 611)
    half = _naca_half_thickness(ss * d0)
    th = 2 * half
    depth = 3.0
    (top,) = [p for t, p in plate_profiles_2d(ss, np.zeros_like(ss), th, [], **KW, edge_vertices=16, depth=depth) if t]
    free = half - depth > KW["gap"] / 2 + 1e-9  # régions non ramenées sur la bande de séparation
    worst = 0.0
    for s in np.linspace(0, 280, 1121):
        i = np.searchsorted(ss, s)
        if not free[max(i - 12, 0): i + 12].all():  # à ±6 mm d'une zone ramenée : non contrôlé (corde plus courte)
            continue
        (seg,) = column(top, s)
        worst = max(worst, float(np.interp(s, ss, half)) - seg.bounds[1])
    assert depth - 0.01 < worst <= depth + 0.15


def test_partial_depth_clamped_band_keeps_the_gap():
    # Là où la nervure de peau est ramenée sur la bande de séparation, les plaques haute et basse doivent
    # garder entre elles exactement `gap` (mêmes sommets en haut et en bas, comme une plaque full) : simplifiées
    # séparément, la bande s'amincissait et, au bord d'attaque de l'aile réelle, laissait un col trop fin.
    d0 = math.sin(math.radians(45))
    ss = np.linspace(-5, 300, 611)
    th = 2 * _naca_half_thickness(ss * d0)
    ym = 0.02 * (ss * d0 - 100) ** 2 / 100 - 2  # surface médiane courbée
    profs = plate_profiles_2d(ss, ym, th, [], **dict(KW, ytop=30.0, ybot=-30.0), edge_vertices=16, depth=3.0)
    tops = [p for t, p in profs if t]
    bots = [p for t, p in profs if not t]
    clamped = (th / 2 - 3.0 <= KW["gap"] / 2) & (th > KW["gap"] + 2 * KW["min_depth"])
    checked = 0
    for s in np.linspace(0, 280, 561):
        i = np.searchsorted(ss, s)
        if not clamped[max(i - 2, 0): i + 2].all():
            continue
        (a,) = [seg for p in tops for seg in column(p, s)]
        (b,) = [seg for p in bots for seg in column(p, s)]
        assert a.bounds[1] - b.bounds[3] >= KW["gap"] - 1e-6, s  # plus large à une transition, jamais plus fine
        checked += 1
    assert checked > 10


# --- parois raides : bords d'attaque / de fuite épais (aile à profil « boîte arrondie ») -------------


def test_wall_limits_follow_the_mouth_criterion():
    # fente vue en tranche : w = slit / cos(angle) ; bouche ≈ w·√(1 + y'²) ≤ marge · max_mouth
    max_slope, margin = wall_limits(slit=0.1, angle_deg=45.0, **WALL)
    w = 0.1 / math.cos(math.radians(45))
    assert w * math.sqrt(1 + max_slope**2) < 0.5  # avec marge de sécurité
    assert w * math.sqrt(1 + max_slope**2) > 0.25  # pas absurdement sévère (bord d'attaque NACA conservé)
    assert margin == pytest.approx(2 * 0.42 + 0.1)
    # bouche plus large tolérée → pente plus forte permise ; fente plus large → moins
    assert wall_limits(slit=0.1, angle_deg=45.0, line_width=0.42, max_mouth=1.0)[0] > max_slope
    assert wall_limits(slit=0.2, angle_deg=45.0, **WALL)[0] < max_slope


def _blunt_mid(sweep=0.0, nz=5, span=100.0):
    """Profil « boîte » : paroi quasi verticale de 20 mm entre x = x_BA et x_BA + 0,5 (x_BA = sweep·z), puis
    épaisseur constante ; grille fine en x (0,1 mm) comme une coupe exacte, `nz` rangées en z."""
    xs = np.linspace(-30.0, 110.0, 1401)
    zs = np.linspace(0.0, span, nz)
    le = sweep * zs
    th = 20.0 * np.clip((xs[:, None] - le) / 0.5, 0.0, 1.0)
    return MidSurface(xs, zs, np.zeros_like(th), th)


def test_plates_stop_before_a_steep_wall():
    d0 = d1 = math.sin(math.radians(45))
    ss = np.linspace(-5, 100, 211)
    x, z = ss * d0, ss * d1
    mid = _blunt_mid()
    ym, th = mid.at(x, z)
    max_slope, margin = wall_limits(slit=0.1, angle_deg=45.0, **WALL)
    steep = steep_samples(mid, x, z, max_slope=max_slope, margin=margin)
    # sans le critère, la plaque descend dans la paroi (x < 0,5) : en tranche, la fente rase la paroi
    (naive,) = [p for t, p in plate_profiles_2d(ss, ym, th, [], **KW) if t]
    assert naive.bounds[0] * d0 < 0.5
    for top in (True, False):
        (poly,) = [p for t, p in plate_profiles_2d(ss, ym, th, [], **KW, steep=steep) if t is top]
        # le bout de la plaque reste à au moins `margin` de la paroi (au pas d'échantillonnage près)
        assert poly.bounds[0] * d0 >= 0.5 + margin - 0.5 * d0
        assert poly.bounds[2] == pytest.approx(100.0)
        # marche, pas rampe : la première colonne va de la bande de séparation à la peau lointaine
        (seg,) = column(poly, poly.bounds[0] + 0.01)
        assert seg.bounds[1 if top else 3] == pytest.approx(0.3 if top else -0.3)


@pytest.mark.parametrize("sweep", [0.2, -0.2])
def test_steep_mask_follows_a_swept_wall_between_grid_rows(sweep):
    # bord en flèche, rangées tous les 25 mm : entre deux rangées, l'interpolation bilinéaire moyenne deux sections
    # décalées de 5 mm (deux demi-parois aux abscisses des rangées, rien à l'abscisse réelle) ; le masque doit
    # suivre la paroi réelle, en x = sweep·z
    mid = _blunt_mid(sweep=sweep, nz=5)
    max_slope, margin = wall_limits(slit=0.1, angle_deg=45.0, **WALL)
    for z in (5.0, 12.5, 20.0, 60.0, 87.5):
        wall = sweep * z
        x = np.arange(wall - 3.0, wall + 10.0, 0.05)
        steep = steep_samples(mid, x, np.full_like(x, z), max_slope=max_slope, margin=margin)
        assert steep[(x >= wall) & (x <= wall + 0.5 + margin - 0.1)].all(), z  # paroi et marge couvertes
        assert not steep[x > wall + 0.5 + margin + 0.2].any(), z  # pas d'excès loin de la paroi


def _naca_mid(chord=200.0, nx=101):
    """Surface médiane d'une aile NACA 4412 de 200 mm, échantillonnée comme `MidSurface.from_skin`."""
    xs = np.linspace(0.0, chord, nx)
    xi = xs / chord
    camber = np.where(xi < 0.4, 0.04 / 0.16 * (0.8 * xi - xi**2), 0.04 / 0.36 * (0.2 + 0.8 * xi - xi**2)) * chord
    th = 2 * _naca_half_thickness(xs, chord)
    th[[0, -1]] = 0.0  # coupe exactement au bord d'attaque / de fuite : épaisseur nulle
    zs = np.linspace(0.0, 200.0, 5)
    return MidSurface(xs, zs, np.repeat(camber[:, None], 5, axis=1), np.repeat(th[:, None], 5, axis=1))


@pytest.mark.parametrize("sign", [1, -1])
def test_naca_leading_edge_is_unchanged(sign):
    # bord d'attaque arrondi mais mince : l'élagage par épaisseur suffit, le critère de pente ne retire rien
    mid = _naca_mid()
    d0, d1 = sign * math.sin(math.radians(45)), math.cos(math.radians(45))
    max_slope, margin = wall_limits(slit=0.1, angle_deg=45.0, **WALL)
    for c in (-150.0, -100.0, -30.0, 0.0, 30.0, 100.0, 150.0, 250.0, 330.0):
        ss = np.linspace(-5, 290, 591)
        x, z = c + ss * d0, ss * d1
        ym, th = mid.at(x, z)
        steep = steep_samples(mid, x, z, max_slope=max_slope, margin=margin)
        kw = dict(KW, ytop=30.0, ybot=-30.0, edge_vertices=16)
        before = plate_profiles_2d(ss, ym, th, [], **kw)
        after = plate_profiles_2d(ss, ym, th, [], **kw, steep=steep)
        assert [(t, p.bounds) for t, p in after] == [(t, p.bounds) for t, p in before], c


def test_diamond_profiles_keep_away_from_blunt_edges():
    # aile « boîte » : parois verticales aux deux bouts de la corde (th passe de 0 à 20 en un pas de grille)
    xs = np.linspace(-50.0, 50.0, 101)
    th = np.full(101, 20.0)
    th[[0, -1]] = 0.0
    mid = MidSurface(xs, np.linspace(0.0, 60.0, 5), np.zeros((101, 5)), np.repeat(th[:, None], 5, axis=1))
    bb = Box(100, 20, 60).moved(Location((0, 0, 30))).bounding_box()
    profs = diamond_profiles(bb, mid, [], RibLayer(spacing=10.0), angle_deg=45.0, gap=0.6, slit=0.1, min_depth=1.0,
                             **WALL)
    assert profs
    _, margin = wall_limits(slit=0.1, angle_deg=45.0, **WALL)
    for p in profs:
        xa, xb = sorted(p.c + s * p.d0 for s in (p.polygon.bounds[0], p.polygon.bounds[2]))
        # paroi entre x = ±49 et ±50 (premier pas de grille) : bout de plaque à `margin` au moins, au pas près
        assert xa >= -49.0 + margin - 0.5 and xb <= 49.0 - margin + 0.5, (p.family, p.c, xa, xb)


def test_full_edge_follows_a_kinked_mid_surface():
    # Surface médiane coudée près d'un bord épais (profil « boîte » dissymétrique) : à 16 sommets réguliers, la corde
    # s'écarte de ym de plusieurs dixièmes ; deux plaques qui se croisent (familles ±, sommets différents) décalent
    # alors leurs bandes de séparation et le col entre elles devient plus fin qu'une ligne. Avec `band_tol`, le bord
    # reste à band_tol de ym ± gap/2 : deux bandes qui se croisent se recouvrent d'au moins gap − 2·band_tol.
    ss = np.linspace(-5, 300, 611)
    ym = np.where(ss < 20, -4.0 - 5.0 * np.clip(ss, 0, 20) / 20, -9.0) + 0.002 * (ss - 150) ** 2 / 10
    th = np.full_like(ss, 30.0)
    tol = (0.6 - 0.42) / 4
    for top in (True, False):
        (poly,) = [p for t, p in plate_profiles_2d(ss, ym, th, [], **dict(KW, ytop=40.0, ybot=-40.0), edge_vertices=16,
                                                  band_tol=tol) if t is top]
        worst = 0.0
        for s in np.linspace(-4.9, 299.9, 1500):
            (seg,) = column(poly, s)
            edge = seg.bounds[1] if top else seg.bounds[3]
            worst = max(worst, abs(edge - (np.interp(s, ss, ym) + (0.3 if top else -0.3))))
        assert worst <= tol + 0.011  # + simplification finale du polygone (0,01)
        assert len(poly.exterior.coords) < 80  # toujours peu de sommets (coût booléen)
