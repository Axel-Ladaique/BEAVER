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
