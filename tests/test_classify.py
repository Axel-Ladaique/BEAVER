import math

import pytest
from build123d import Box, Cylinder, Location

from aerorib.classify import ClassifyError, classify, detect_spar
from aerorib.config import load_config
from aerorib.io_step import NamedBody


def nb(*path, solid=None):
    return NamedBody(tuple(path), solid if solid is not None else Box(10, 10, 10))


def spar_solid():
    return Cylinder(7, 200).moved(Location((10, 5, 100)))


def roles(model):
    return {cb.body.display: cb.role for cb in model.bodies}


def test_prefix_case_insensitive_leaf():
    m = classify([nb("A", "skin_main"), nb("A", "spar_1", solid=spar_solid())], load_config())
    assert roles(m) == {"A/skin_main": "skin", "A/spar_1": "spar"}


def test_parent_level_match():
    m = classify([nb("Wing_L", "Body1")], load_config())
    assert roles(m) == {"Wing_L/Body1": "skin"}
    assert "Wing" in m.bodies[0].matched_by


def test_leaf_beats_parent():
    m = classify([nb("Wing_L", "Body1"), nb("Wing_L", "SPAR_main", solid=spar_solid())], load_config())
    assert roles(m)["Wing_L/SPAR_main"] == "spar"


def test_roles_override_and_ignore():
    cfg = load_config(overrides={"roles": {"body3": "spar", "Junk": "ignore"}})
    m = classify([nb("SKIN"), nb("Body3", solid=spar_solid()), nb("Junk")], cfg)
    assert roles(m) == {"SKIN": "skin", "Body3": "spar", "Junk": "ignore"}
    assert m.bodies[1].matched_by == "roles:body3"


def test_fallback_single_unassigned_is_skin():
    m = classify([nb("Body1"), nb("SPAR_x", solid=spar_solid())], load_config())
    assert roles(m)["Body1"] == "skin"
    assert m.bodies[0].matched_by == "seul corps restant"


def test_no_skin_raises_with_names():
    with pytest.raises(ClassifyError, match="Body1"):
        classify([nb("Body1"), nb("Body2")], load_config())


def test_spar_without_cylinder_raises():
    with pytest.raises(ClassifyError, match="cylindre"):
        classify([nb("SKIN"), nb("SPAR_box")], load_config())


def test_model_queries():
    m = classify([nb("SKIN"), nb("SPAR_a", solid=spar_solid()), nb("Other"), nb("Thing")], load_config())
    assert [c.body.name for c in m.of("spar")] == ["SPAR_a"]
    assert [c.body.name for c in m.unassigned] == ["Other", "Thing"]


def test_detect_spar_along_z():
    info = detect_spar(spar_solid())
    assert info.diameter == pytest.approx(14.0)
    assert info.axis_dir == pytest.approx((0, 0, 1), abs=1e-9)
    assert info.length == pytest.approx(200.0)
    assert info.axis_point == pytest.approx((10, 5, 0), abs=1e-6)


def test_detect_spar_along_x_normalized_sign():
    s = Cylinder(5, 80).moved(Location((0, 0, 0), (0, -90, 0)))
    info = detect_spar(s)
    assert info.diameter == pytest.approx(10.0)
    assert info.axis_dir == pytest.approx((1, 0, 0), abs=1e-9)
    assert info.length == pytest.approx(80.0)


def test_detect_spar_tube_takes_outer_diameter():
    tube = Cylinder(7, 100) - Cylinder(6, 120)
    assert detect_spar(tube).diameter == pytest.approx(14.0)


def test_detect_spar_none_for_box():
    assert detect_spar(Box(5, 5, 5)) is None


def test_fusion_fixture_roles_and_spar():
    from pathlib import Path

    from aerorib.io_step import read_step

    fixture = Path(__file__).parent / "fixtures" / "fusion_aile_4412_simple_sbar.step"
    m = classify(read_step(fixture), load_config())
    assert [c.body.name for c in m.of("skin")] == ["WING_1"]
    (spar,) = m.of("spar")
    assert spar.body.name == "SBAR_14mm_1"
    assert spar.spar.diameter == pytest.approx(14.0)
    assert spar.spar.axis_dir == pytest.approx((1, 0, 0), abs=1e-9)
    assert spar.spar.length == pytest.approx(210.0)


# --- rôle servo et gaines (servo, tâche 1) ----------------------------------


def cable_solid():
    return Cylinder(5.75, 190).moved(Location((40, 5, 95)))


def test_servo_leaf_beats_parent_skin():
    m = classify(
        [nb("Aile_4412", "WING_1"), nb("Aile_4412", "SERVO_1"), nb("Aile_4412", "SBAR_1", solid=spar_solid())],
        load_config(),
    )
    assert roles(m) == {"Aile_4412/WING_1": "skin", "Aile_4412/SERVO_1": "servo", "Aile_4412/SBAR_1": "spar"}
    assert [cb.body.name for cb in m.of("servo")] == ["SERVO_1"]
    assert "SERVO" in m.of("servo")[0].matched_by


def test_parent_explicit_role_does_not_override_leaf_prefix():
    cfg = load_config(overrides={"roles": {"Aile_4412": "skin"}})
    m = classify([nb("Aile_4412", "WING_1"), nb("Aile_4412", "SERVO_1")], cfg)
    assert roles(m)["Aile_4412/SERVO_1"] == "servo"


def test_leaf_explicit_role_still_wins():
    cfg = load_config(overrides={"roles": {"SERVO_1": "ignore"}})
    m = classify([nb("SKIN"), nb("SERVO_1")], cfg)
    assert roles(m)["SERVO_1"] == "ignore"


def test_servo_prefix_configurable():
    cfg = load_config(overrides={"naming": {"servo": ["BAIE"]}})
    m = classify([nb("SKIN"), nb("baie_aileron")], cfg)
    assert roles(m)["baie_aileron"] == "servo"


def test_cable_marker_marks_spar():
    m = classify(
        [nb("SKIN"), nb("A", "SBAR_CABLES_SERVO", solid=cable_solid()), nb("A", "SBAR_2", solid=spar_solid())],
        load_config(),
    )
    spars = {cb.body.name: cb.spar for cb in m.of("spar")}
    assert spars["SBAR_CABLES_SERVO"].cable is True
    assert spars["SBAR_2"].cable is False
    assert [cb.body.name for cb in m.cables] == ["SBAR_CABLES_SERVO"]
    assert m.of("spar")[0].is_cable and not m.of("spar")[1].is_cable


def test_cable_marker_case_insensitive_and_own_name_only():
    m = classify(
        [nb("SKIN"), nb("SPAR_fil_cable", solid=cable_solid()), nb("CABLES", "SPAR_main", solid=spar_solid())],
        load_config(),
    )
    spars = {cb.body.name: cb.spar.cable for cb in m.of("spar")}
    assert spars == {"SPAR_fil_cable": True, "SPAR_main": False}


def test_cable_markers_configurable():
    cfg = load_config(overrides={"naming": {"cable_markers": ["FIL"]}})
    m = classify([nb("SKIN"), nb("SPAR_FIL", solid=cable_solid()), nb("SPAR_CABLE", solid=spar_solid())], cfg)
    spars = {cb.body.name: cb.spar.cable for cb in m.of("spar")}
    assert spars == {"SPAR_FIL": True, "SPAR_CABLE": False}


def test_cable_marker_ignored_on_non_spar():
    m = classify([nb("SKIN"), nb("SERVO_CABLE")], load_config())
    assert roles(m)["SERVO_CABLE"] == "servo"
    assert m.cables == []
