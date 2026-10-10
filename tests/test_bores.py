import pytest
from build123d import Box, Cylinder, GeomType, Location, fillet

from aerorib.config import load_config
from aerorib.features.bores import Bore, BoreError, bore_cut_tools, find_passages, passage_bores, spar_bores

CFG = load_config()


def drilled_box():
    return Box(60, 30, 40) - Cylinder(1.2, 60).moved(Location((10, 3, 0))) - Cylinder(
        7.0, 60
    ).moved(Location((-15, 0, 0)))


def test_find_passages_concave_parallel_to_z_only():
    found = sorted(find_passages(drilled_box(), 5.0))
    assert [(round(x, 3), round(y, 3), round(r, 3)) for x, y, r in found] == [(-15.0, 0.0, 7.0), (10.0, 3.0, 1.2)]
    boss = Box(60, 30, 40) + Cylinder(3, 60).moved(Location((40, 0, 0)))
    assert find_passages(boss, 5.0) == []
    tilted = Box(60, 30, 40) - Cylinder(1, 100).moved(Location((0, 0, 0), (0, 90, 0)))
    assert find_passages(tilted, 5.0) == []


def test_spar_bore_from_oriented_spar():
    (b,) = spar_bores([("SBAR_14", Cylinder(7, 100).moved(Location((-15, 0, 0))))], CFG)
    assert (b.name, b.x, b.y, b.cut_hole, b.side) == ("SBAR_14", -15.0, 0.0, True, 1)
    assert b.radius == pytest.approx(7.15) and b.strip == 2.0 and b.sleeve == 0.5


def test_spar_slit_side_override():
    cfg = load_config(overrides={"spars": {"SBAR_14": {"slit_side": "bottom"}}})
    (b,) = spar_bores([("SBAR_14", Cylinder(7, 100))], cfg)
    assert b.side == -1


def test_tilted_spar_rejected():
    with pytest.raises(BoreError, match="P5"):
        spar_bores([("SBAR_x", Cylinder(7, 100).moved(Location((0, 0, 0), (0, 90, 0))))], CFG)


def test_passages_exclude_spar_bore_and_mode_off():
    spar = Bore("SBAR", -15.0, 0.0, 7.15, 0.5, 2.0, 1, True)
    ps = passage_bores(drilled_box(), CFG, exclude=[spar])
    assert [(p.name, p.x, p.y, p.radius, p.cut_hole, p.strip) for p in ps] == [
        ("passage 1", 10.0, 3.0, 1.2, False, 1.0)
    ]
    assert passage_bores(drilled_box(), load_config(overrides={"passages": {"mode": "off"}}), []) == []


def test_bore_cut_tools_counts():
    bb = Box(60, 30, 40).bounding_box()
    tools = bore_cut_tools(
        [Bore("S", 0, 0, 7.15, 0.5, 2, 1, True), Bore("p", 10, 3, 1.2, 0.5, 1, -1, False)], bb, 0.1
    )
    assert len(tools) == 3
    slit_down = tools[2].bounding_box()
    assert slit_down.max.Y == pytest.approx(3.0) and slit_down.min.Y < -15
    assert slit_down.max.X - slit_down.min.X == pytest.approx(0.1)


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
    found = find_passages(part, 5.0)
    assert found == [(10.0, 3.0, 1.2)]


def test_spar_without_cylinder_rejected():
    with pytest.raises(BoreError, match="aucun cylindre"):
        spar_bores([("SBAR_box", Box(10, 10, 10))], CFG)
