import numpy as np
import pytest
from build123d import Cone, Location

from aerorib.classify import classify
from aerorib.config import load_config
from aerorib.io_step import NamedBody
from aerorib.midsurface import camber_offset
from aerorib.orient import _needs_flip, choose_axis, orient, orientation_location
from aerorib.slicing import slice_layers
from aerorib.testing.synthetic import naca_wing

X_SPAN = Location((0, 0, 0), (0, 90, 0))  # envergure Z → X (comme l'aile Fusion)


def wing_along_x(flip: bool = False):
    wing, _ = naca_wing(chord=150.0, span=100.0)
    if flip:  # extrados vers −Y avant orientation
        wing = wing.moved(Location((0, 0, 0), (180, 0, 0)))
    return wing.moved(Location((-30, 12, 5)) * X_SPAN).solids()[0]


@pytest.mark.parametrize("flip", [False, True])
def test_wing_stands_on_root_with_chord_x_and_extrados_up(flip):
    w = wing_along_x(flip)
    o = orientation_location(w, np.array([1.0, 0, 0]))
    ow = w.moved(o)
    bb = ow.bounding_box()
    assert bb.min.Z == pytest.approx(0.0, abs=1e-3) and bb.max.Z == pytest.approx(100.0, abs=1e-3)
    assert bb.max.X - bb.min.X == pytest.approx(150.0, abs=0.5)
    assert bb.max.Y - bb.min.Y < 25.0
    (root,) = slice_layers(ow, [0.5])
    assert camber_offset(root.polygons[0]) > 2.0


def test_root_is_the_larger_end():
    cone = Cone(20, 10, 100).moved(X_SPAN)  # on teste les deux sens de l'axe
    for axis in (np.array([1.0, 0, 0]), np.array([-1.0, 0, 0])):
        oc = cone.moved(orientation_location(cone, axis))
        low, high = slice_layers(oc, [1.0, 99.0])
        assert low.polygons[0].area > high.polygons[0].area


def test_choose_axis_priorities():
    from build123d import Box, Cylinder

    spar = Cylinder(4, 100).moved(Location((0, 0, 0), (0, 90, 0)))
    model = classify([NamedBody(("Aile",), Box(100, 40, 10)), NamedBody(("SBAR_x",), spar)], load_config())
    axis, src = choose_axis(model, load_config())
    assert np.allclose(np.abs(axis), [1, 0, 0]) and src == "longeron SBAR_x"
    axis, src = choose_axis(model, load_config(overrides={"orientation": {"axis": [0, 2, 0]}}))
    assert np.allclose(axis, [0, 1, 0]) and src == "axe forcé"
    no_spar = classify([NamedBody(("Aile",), Box(100, 40, 10))], load_config())
    axis, src = choose_axis(no_spar, load_config())
    assert np.allclose(np.abs(axis), [1, 0, 0]) and src == "plus grande dimension"


def test_orient_model_uses_spar():
    w = wing_along_x()
    from build123d import Cylinder

    spar = Cylinder(4, 100).moved(Location((-30 + 0, 12, 5)) * X_SPAN)
    model = classify([NamedBody(("Aile",), w), NamedBody(("SBAR_a",), spar)], load_config())
    o = orient(model, load_config())
    assert o.source == "longeron SBAR_a"
    assert w.moved(o.location).bounding_box().min.Z == pytest.approx(0.0, abs=1e-3)


def test_flip_branch_is_exercised():
    for flip in (False, True):
        w = wing_along_x(flip)
        o = orientation_location(w, np.array([1.0, 0, 0]))
        # sans correction de cambrure, l'aile retournée aurait l'extrados vers −Y
        assert _needs_flip(w.moved(o), 0.5, 0.02) is False
    # vérifie la décision elle-même sur l'aile retournée en +Y puis forcée à l'envers
    w = wing_along_x(False)
    o = orientation_location(w, np.array([1.0, 0, 0]))
    upside = w.moved(o).moved(Location((0, 0, 100)) * Location((0, 0, 0), (180, 0, 0)))
    assert _needs_flip(upside, 0.5, 0.02) is True


def test_degenerate_root_section_has_french_error(monkeypatch):
    import aerorib.orient as orient_mod
    from build123d import Box

    # section d'emplanture dégénérée (tous les sommets confondus) : corde de longueur nulle
    monkeypatch.setattr(orient_mod, "_section_area", lambda mesh, axis, t: (1.0, np.zeros((3, 3))))
    with pytest.raises(ValueError, match="corde de longueur nulle"):
        orientation_location(Box(10, 10, 10).solids()[0], np.array([0.0, 0, 1]))


# --- emplanture : côté gaine / imposée ---------------------------------------------------------------------


def _wing_with_conduits(*ends: str, root: str | None = None):
    """Aile droite le long de X (longeron pleine envergure) + gaines courtes de 20 mm aux bouts demandés."""
    from build123d import Cylinder

    from aerorib.testing.synthetic import wing_x_bodies

    bodies = wing_x_bodies()
    z_of = {"lo": 10.0, "hi": 70.0}  # envergure 80 mm, avant rotation vers X
    for i, e in enumerate(ends):
        cyl = Cylinder(2, 20).moved(Location((60, 2 * i, z_of[e]))).moved(Location((0, 0, 0), (0, 90, 0)))
        bodies.append(NamedBody((f"SBAR_CABLES_{e}",), cyl.solids()[0]))
    overrides = {"naming": {"cable_markers": ["CABLES"]}}
    if root:
        overrides["orientation"] = {"root": root}
    cfg = load_config(overrides=overrides)
    return classify(bodies, cfg), cfg


def _cable_z(model, o, name):
    (c,) = [b for b in model.cables if b.body.name == name]
    bb = c.body.solid.moved(o.location).bounding_box()
    return bb.min.Z, bb.max.Z


@pytest.mark.parametrize("end", ["lo", "hi"])
def test_auto_root_follows_single_conduit_end(end):
    model, cfg = _wing_with_conduits(end)
    o = orient(model, cfg)
    assert o.source == f"longeron SBAR_8 ; emplanture côté gaine SBAR_CABLES_{end}"
    zmin, zmax = _cable_z(model, o, f"SBAR_CABLES_{end}")
    assert zmin == pytest.approx(0.0, abs=0.6) and zmax == pytest.approx(20.0, abs=0.6)


def test_forced_root_debut_fin_override_conduit():
    model, cfg = _wing_with_conduits("hi", root="debut")
    o = orient(model, cfg)
    assert o.source == "longeron SBAR_8 ; emplanture imposée (debut)"
    assert _cable_z(model, o, "SBAR_CABLES_hi")[1] == pytest.approx(80.0, abs=0.6)
    model, cfg = _wing_with_conduits(root="fin")
    o = orient(model, cfg)
    assert o.source == "longeron SBAR_8 ; emplanture imposée (fin)"
    skin = model.of("skin")[0].body.solid.moved(o.location).bounding_box()
    assert skin.min.Z == pytest.approx(0.0, abs=1e-3)
    # `fin` = l'autre bout que `debut`
    o2 = orient(*_wing_with_conduits(root="debut"))
    assert abs(o.location.position.X - o2.location.position.X) > 1 or abs(o.location.position.Z - o2.location.position.Z) > 1


def test_conduits_at_both_ends_or_none_keep_section_rule():
    ref_model, ref_cfg = _wing_with_conduits()
    ref = orient(ref_model, ref_cfg)
    assert ref.source == "longeron SBAR_8"
    model, cfg = _wing_with_conduits("lo", "hi")
    o = orient(model, cfg)
    assert o.source == "longeron SBAR_8"
    assert o.location.position.X == pytest.approx(ref.location.position.X, abs=1e-6)


def test_orientation_root_config_and_prepare_key():
    from aerorib.preview import prepare_key

    assert load_config().orientation == "auto"
    keys = {prepare_key(load_config(overrides={"orientation": {"root": r}})) for r in ("auto", "debut", "fin")}
    assert len(keys) == 3
    assert prepare_key(load_config()) != prepare_key(load_config(overrides={"orientation": {"root": "fin"}}))
    with pytest.raises(Exception, match="root"):
        load_config(overrides={"orientation": {"root": "milieu"}})


FIXTURES = __import__("pathlib").Path(__file__).parent / "fixtures"


def _oriented(step, **ov):
    from aerorib.io_step import read_step

    cfg = load_config(overrides=ov or None)
    model = classify(read_step(FIXTURES / step), cfg)
    o = orient(model, cfg)
    return model, o


def test_servo_fixture_root_at_conduit_end():
    model, o = _oriented("aile_4412_servo_extrados.step")
    assert "emplanture côté gaine SBAR_CABLES_SERVO" in o.source
    (c,) = model.cables
    bb = c.body.solid.moved(o.location).bounding_box()
    assert bb.min.Z == pytest.approx(0.0, abs=0.6)


def test_simple_sbar_v2_orientation_unchanged():
    model, o = _oriented("fusion_aile_4412_simple_sbar_v2.step")
    assert o.source == "longeron SBAR_14mm_1"
    bb = model.of("skin")[0].body.solid.moved(o.location).bounding_box()
    assert bb.min.Z == pytest.approx(0.0, abs=1e-3)
