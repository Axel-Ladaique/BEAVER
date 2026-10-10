import pytest
from build123d import Box, Cylinder, GeomType, Location, fillet

from aerorib.config import load_config
from aerorib.features.bores import (
    Bore,
    BoreError,
    bore_cut_tools,
    bore_extent,
    extend_to_existing_bores,
    find_passages,
    passage_bores,
    spar_bores,
)

CFG = load_config()


def bore(name="S", x=0.0, y=0.0, radius=7.15, *, sleeve=0.7, strip=2.0, side=1, cut_hole=True, z0=-20.0, z1=20.0,
         slit=0.15, clearance=0.15):
    return Bore(name, x, y, radius, sleeve, strip, side, cut_hole, z0, z1, slit, clearance)


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
    assert b.radius == pytest.approx(7.15) and b.strip == 2.0 and b.sleeve == 0.5
    assert (b.z0, b.z1, b.clearance) == (pytest.approx(-50.0), pytest.approx(50.0), 0.15)


def test_spar_slit_side_override():
    cfg = load_config(overrides={"spars": {"SBAR_14": {"slit_side": "bottom"}}})
    (b,) = spar_bores([("SBAR_14", Cylinder(7, 100))], cfg)
    assert b.side == -1


def test_tilted_spar_rejected():
    with pytest.raises(BoreError, match="P5"):
        spar_bores([("SBAR_x", Cylinder(7, 100).moved(Location((0, 0, 0), (0, 90, 0))))], CFG)


def test_passages_exclude_spar_bore_and_mode_off():
    spar = bore("SBAR", -15.0, 0.0)
    ps = passage_bores(drilled_box(), CFG, exclude=[spar])
    assert [(p.name, p.x, p.y, p.radius, p.cut_hole, p.strip, p.clearance) for p in ps] == [
        ("passage 1", 10.0, 3.0, 1.2, False, 1.0, 0.0)
    ]
    assert (ps[0].z0, ps[0].z1) == (pytest.approx(-20.0), pytest.approx(20.0))
    assert passage_bores(drilled_box(), load_config(overrides={"passages": {"mode": "off"}}), []) == []


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
        passage_bores(part, CFG, exclude=[])


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
