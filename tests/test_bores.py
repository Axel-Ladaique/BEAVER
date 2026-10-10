import pytest
from build123d import Align, Box, Cylinder, GeomType, Location, fillet

from aerorib.config import load_config
from aerorib.midsurface import MidSurface
import numpy as np

from aerorib.features.bores import (
    Bore,
    BoreError,
    anchor_tools,
    bore_cut_tools,
    bore_extent,
    extend_to_existing_bores,
    find_passages,
    passage_bores,
    spar_bores,
)

CFG = load_config()
LINK = load_config(overrides={"passages": {"mode": "link"}})


def bore(name="S", x=0.0, y=0.0, radius=7.15, *, sleeve=0.7, strip=2.0, side=1, cut_hole=True, z0=-20.0, z1=20.0,
         slit=0.15, clearance=0.15, anchor_gap=0.0):
    return Bore(name, x, y, radius, sleeve, strip, side, cut_hole, z0, z1, slit, clearance, anchor_gap)


def drilled_box():
    return Box(60, 30, 40) - Cylinder(1.2, 60).moved(Location((10, 3, 0))) - Cylinder(
        7.0, 60
    ).moved(Location((-15, 0, 0)))


def test_find_passages_concave_parallel_to_z_only():
    found = sorted(find_passages(drilled_box(), 5.0))
    assert [(round(p.x, 3), round(p.y, 3), round(p.r, 3)) for p in found] == [(-15.0, 0.0, 7.0), (10.0, 3.0, 1.2)]
    assert all(p.z0 == pytest.approx(-20.0) and p.z1 == pytest.approx(20.0) and p.tilt_deg == 0 for p in found)
    boss = Box(60, 30, 40) + Cylinder(3, 60).moved(Location((40, 0, 0)))
    assert find_passages(boss, 5.0) == []
    tilted = Box(60, 30, 40) - Cylinder(1, 100).moved(Location((0, 0, 0), (0, 90, 0)))
    assert find_passages(tilted, 5.0) == []


def test_spar_bore_from_oriented_spar():
    (b,) = spar_bores([("SBAR_14", Cylinder(7, 100).moved(Location((-15, 0, 0))))], CFG)
    assert (b.name, b.x, b.y, b.cut_hole, b.side) == ("SBAR_14", -15.0, 0.0, True, 1)
    assert b.radius == pytest.approx(7.15) and b.strip == 2.0 and b.sleeve == 0.8
    assert (b.z0, b.z1, b.clearance, b.slit) == (pytest.approx(-50.0), pytest.approx(50.0), 0.15, 0.15)


def test_spar_slit_side_override():
    cfg = load_config(overrides={"spars": {"SBAR_14": {"slit_side": "bottom"}}})
    (b,) = spar_bores([("SBAR_14", Cylinder(7, 100))], cfg)
    assert b.side == -1


def test_tilted_spar_rejected():
    with pytest.raises(BoreError, match="P5"):
        spar_bores([("SBAR_x", Cylinder(7, 100).moved(Location((0, 0, 0), (0, 90, 0))))], CFG)


def test_passages_exclude_spar_bore_and_mode_off():
    spar = bore("SBAR", -15.0, 0.0)
    ps = passage_bores(drilled_box(), LINK, exclude=[spar])
    assert [(p.name, p.x, p.y, p.radius, p.cut_hole, p.strip, p.clearance) for p in ps] == [
        ("passage 1", 10.0, 3.0, 1.2, False, 1.0, 0.0)
    ]
    assert ps[0].slit == 0.15
    assert (ps[0].z0, ps[0].z1) == (pytest.approx(-20.0), pytest.approx(20.0))
    assert passage_bores(drilled_box(), load_config(overrides={"passages": {"mode": "off"}}), []) == []
    assert passage_bores(drilled_box(), CFG, []) == []  # défaut fill : rebouchés, pas de fente
    assert all(p.anchor_gap == 0.0 for p in ps)  # pas d'attache pour un passage


def test_bore_cut_tools_counts():
    bb = Box(60, 30, 40).bounding_box()
    tools = bore_cut_tools(
        [bore("S"), bore("p", 10, 3, 1.2, strip=1, side=-1, cut_hole=False, slit=0.2, clearance=0.0)], bb
    )
    assert len(tools) == 3
    slit_down = tools[2].bounding_box()
    assert slit_down.max.Y == pytest.approx(3.0) and slit_down.min.Y < -15
    assert slit_down.max.X - slit_down.min.X == pytest.approx(0.2)
    # étendue sur toute la hauteur : ouverte de 10 mm au-delà de l'emplanture et du saumon
    assert all(t.bounding_box().min.Z == pytest.approx(-30) and t.bounding_box().max.Z == pytest.approx(30) for t in tools)


def test_bore_extent_stops_inside_the_part():
    # trou borgne : s'arrête à z0 − jeu / z1 + jeu ; extrémité à moins de 1 mm du bord : ouverte de 10 mm
    assert bore_extent(bore(z0=-5.0, z1=8.0), -20.0, 20.0) == (pytest.approx(-5.15), pytest.approx(8.15))
    assert bore_extent(bore(z0=-19.5, z1=19.2), -20.0, 20.0) == (pytest.approx(-30.0), pytest.approx(30.0))
    bb = Box(60, 30, 40).bounding_box()
    hole, slit = bore_cut_tools([bore(z0=-19.9, z1=5.0)], bb)
    for t in (hole, slit):
        assert t.bounding_box().min.Z == pytest.approx(-30.0) and t.bounding_box().max.Z == pytest.approx(5.15)


def test_spar_extent_from_spar_info():
    (b,) = spar_bores([("SBAR_half", Cylinder(7, 40).moved(Location((0, 0, -10))))], CFG)
    assert (b.z0, b.z1) == (pytest.approx(-30.0), pytest.approx(10.0))


def _tilted_spar(deg: float, length: float = 210.0):
    return Cylinder(7, length).moved(Location((0, 0, 0), (0, deg, 0)))


def test_slightly_tilted_spar_rejected():
    # 1° sur 210 mm = 3,7 mm de dérive, bien plus que le demi-jeu (0,075 mm)
    with pytest.raises(BoreError, match="incliné de 1.0°"):
        spar_bores([("SBAR_t", _tilted_spar(1.0))], CFG)
    with pytest.raises(BoreError, match="incliné de 3.0°"):
        spar_bores([("SBAR_t", _tilted_spar(3.0))], CFG)


def test_tiny_tilt_within_clearance_accepted_at_mid_height():
    # 0,01° sur 210 mm = 0,037 mm < 0,075 : accepté, centré à mi-hauteur
    (b,) = spar_bores([("SBAR_t", _tilted_spar(0.01).moved(Location((5, 0, 0))))], CFG)
    assert b.x == pytest.approx(5.0, abs=1e-6) and b.y == pytest.approx(0.0, abs=1e-6)


def test_tilted_passage_rejected():
    part = Box(60, 30, 40) - Cylinder(1.2, 80).moved(Location((10, 3, 0), (0, 2, 0)))
    with pytest.raises(BoreError, match="passage 1.*incliné de 2.0°"):
        passage_bores(part, LINK, exclude=[])


def inner_corner_fillet_box():
    # Encoche d'angle : l'arête verticale rentrante (10, 0) est arrondie -> arc partiel concave ∥ Z
    part = Box(60, 30, 40) - Box(20, 15, 40).moved(Location((20, 7.5, 0)))
    edges = [e for e in part.edges() if e.geom_type == GeomType.LINE and abs(e.center().X - 10) < 1e-6 and abs(e.center().Y) < 1e-6]
    assert len(edges) == 1
    return fillet(edges, 3)


def test_partial_arc_is_not_a_passage():
    assert find_passages(inner_corner_fillet_box(), 5.0) == []


def test_counterbored_hole_is_one_passage():
    part = Box(60, 30, 40) - Cylinder(1.2, 60).moved(Location((10, 3, 0))) - Cylinder(2.5, 5).moved(
        Location((10, 3, 17.5))
    )
    (p,) = find_passages(part, 5.0)
    assert (p.x, p.y, p.r) == (10.0, 3.0, 1.2)
    assert (p.z0, p.z1) == (pytest.approx(-20.0), pytest.approx(20.0))


def test_spar_without_cylinder_rejected():
    with pytest.raises(BoreError, match="aucun cylindre"):
        spar_bores([("SBAR_box", Box(10, 10, 10))], CFG)


def test_spar_in_longer_existing_bore_gets_full_extent():
    # longeron de z = -20 à 0 dans un alésage existant traversant (-20 à 20) : fente sur tout l'alésage
    spar = bore("SBAR", -15.0, 0.0, z0=-20.0, z1=0.0)
    (b,) = extend_to_existing_bores([spar], drilled_box(), CFG)
    assert (b.z0, b.z1) == (pytest.approx(-20.0), pytest.approx(20.0))
    (alone,) = extend_to_existing_bores([bore("SBAR", 0.0, -10.0, z0=-20.0, z1=0.0)], drilled_box(), CFG)
    assert (alone.z0, alone.z1) == (-20.0, 0.0)


def _coaxial_case():
    # repro du relecteur : boîte 60×30×100, longeron Ø8 en z 0–40, alésage coaxial Ø2 en z 60–100
    skin = (
        Box(60, 30, 100, align=(Align.CENTER, Align.CENTER, Align.MIN))
        - Cylinder(1, 40).moved(Location((10, 0, 80)))
    ).solids()[0]
    spar = Cylinder(4, 40).moved(Location((10, 0, 20)))
    return skin, spar


def test_coaxial_bore_not_linked_when_smaller_or_apart():
    skin, spar = _coaxial_case()
    (s,) = spar_bores([("SBAR", spar)], CFG)
    (b,) = extend_to_existing_bores([s], skin, CFG)
    assert (b.z0, b.z1) == (pytest.approx(0.0), pytest.approx(40.0))
    (p,) = passage_bores(skin, LINK, exclude=[b])  # reste un passage ordinaire, avec sa fente
    assert (p.x, p.y, p.radius, p.cut_hole) == (10.0, 0.0, 1.0, False)
    assert (p.z0, p.z1) == (pytest.approx(60.0), pytest.approx(100.0))


def test_linked_bores_must_touch_the_spar_range():
    # même diamètre que le longeron mais séparé de 20 mm : non relié ; contigu : relié (de proche en proche)
    base = Box(60, 30, 100, align=(Align.CENTER, Align.CENTER, Align.MIN))
    apart = (base - Cylinder(4, 40).moved(Location((10, 0, 80)))).solids()[0]
    s = bore("SBAR", 10.0, 0.0, 4.15, z0=0.0, z1=40.0)
    (b,) = extend_to_existing_bores([s], apart, CFG)
    assert (b.z0, b.z1) == (0.0, 40.0)
    assert len(passage_bores(apart, LINK, exclude=[b])) == 1
    chain = (
        base - Cylinder(4, 20).moved(Location((10, 0, 50))) - Cylinder(4.5, 40).moved(Location((10, 0, 80)))
    ).solids()[0]
    (c,) = extend_to_existing_bores([s], chain, CFG)
    assert (c.z0, c.z1) == (pytest.approx(0.0), pytest.approx(100.0))
    assert passage_bores(chain, LINK, exclude=[c]) == []


# --- attache côté opposé (P1b) ------------------------------------------------------------------


def test_spar_bore_anchor_from_settings():
    (b,) = spar_bores([("SBAR_14", Cylinder(7, 100))], CFG)
    assert b.anchor_gap == 1.0
    off = load_config(overrides={"spars": {"default": {"anchor": False}}})
    (b,) = spar_bores([("SBAR_14", Cylinder(7, 100))], off)
    assert b.anchor_gap == 0.0


def _flat_mid(thick_at_z0=20.0, thick_at_z1=20.0, ymid=0.0):
    xs, zs = np.linspace(-30, 30, 7), np.linspace(-20, 20, 5)
    th = np.tile(np.linspace(thick_at_z0, thick_at_z1, 5), (7, 1))
    return MidSurface(xs, zs, np.full((7, 5), ymid), th)


BB = Box(60, 30, 40).bounding_box()


@pytest.mark.parametrize("side, y_end", [(1, -9.0), (-1, 9.0)])
def test_anchor_tool_stops_at_gap_from_opposite_skin(side, y_end):
    b = bore(x=5.0, y=1.0, radius=4.0, side=side, z0=-10.0, z1=10.0, anchor_gap=1.0)
    (t,) = anchor_tools(b, _flat_mid(), BB)
    tb = t.bounding_box()
    assert (tb.min.X, tb.max.X) == (pytest.approx(4.925), pytest.approx(5.075))  # largeur = fente du trou
    lo, hi = sorted((1.0, y_end))  # du centre du trou jusqu'à 1 mm de la peau opposée (±10)
    assert (tb.min.Y, tb.max.Y) == (pytest.approx(lo), pytest.approx(hi))
    assert (tb.min.Z, tb.max.Z) == (pytest.approx(-10.15), pytest.approx(10.15))  # étendue du trou


def test_anchor_tool_follows_the_skin_and_skips_where_no_room():
    # épaisseur 30 → 6 mm en z : la peau opposée (y = −th/2) remonte ; pas d'attache quand th/2 < r + gap + 0,2
    b = bore(radius=4.0, z0=-30.0, z1=30.0, anchor_gap=1.0)  # extrémités au bord : ouvert au-delà
    (t,) = anchor_tools(b, _flat_mid(30.0, 6.0), BB)
    tb = t.bounding_box()
    # th/2 ≥ 5,2 ⇔ th ≥ 10,4 ⇔ z ≤ 20 − 40·(10,4 − 6)/24 ≈ 12,67
    assert tb.max.Z < 12.67 and tb.max.Z > 10.0
    assert tb.min.Z == pytest.approx(-30.0)
    assert tb.min.Y == pytest.approx(-15.0 + 1.0, abs=1e-6)  # au bas de l'aile (épaisseur 30 en z ≤ −20)
    # à z = 0 : épaisseur 18 → peau opposée en y = −9, fin de l'attache en −8
    (sec,) = t.intersect(Box(10, 40, 0.1))
    sec = sec.bounding_box()
    assert sec.min.Y == pytest.approx(-8.0, abs=0.05) and sec.max.Y == pytest.approx(0.0)


def test_anchor_tool_none_when_never_room():
    b = bore(radius=4.0, z0=-10.0, z1=10.0, anchor_gap=1.0)
    assert anchor_tools(b, _flat_mid(8.0, 8.0), BB) == []


def test_bore_cut_tools_include_anchor():
    b = bore(x=5.0, y=1.0, radius=4.0, z0=-10.0, z1=10.0, anchor_gap=1.0)
    tools = bore_cut_tools([b], BB, _flat_mid())
    assert len(tools) == 3  # trou, fente, attache
    with pytest.raises(ValueError, match="surface médiane"):
        bore_cut_tools([b], BB)
