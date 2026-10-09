import numpy as np
import pytest
from build123d import Box, Compound, Cylinder, Location

from aerorib.slicing import layer_heights, slice_layers, to_mesh


def test_layer_heights():
    zs = layer_heights(-15.0, 15.0, 0.2)
    assert len(zs) == 150
    assert zs[0] == pytest.approx(-14.9)
    assert zs[-1] == pytest.approx(14.9)


def test_to_mesh_watertight():
    assert to_mesh(Box(10, 20, 30)).is_watertight


def test_box_single_polygon():
    layers = slice_layers(Box(10, 20, 30), [-10.0, 0.0, 10.0])
    assert [len(l.polygons) for l in layers] == [1, 1, 1]
    assert layers[1].polygons[0].area == pytest.approx(200.0, rel=1e-6)
    assert layers[1].z == 0.0


def test_hole_gives_interior():
    part = Box(10, 20, 30) - Cylinder(2, 40)
    (layer,) = slice_layers(part, [0.0])
    assert len(layer.polygons) == 1
    assert len(layer.polygons[0].interiors) == 1


def test_two_bodies_two_polygons():
    part = Compound([Box(10, 10, 10), Box(10, 10, 10).moved(Location((50, 0, 0)))])
    (layer,) = slice_layers(part, [0.0])
    assert len(layer.polygons) == 2


def test_slit_keeps_single_polygon():
    part = Box(100, 20, 50) - Box(0.1, 15, 60).moved(Location((0, 5, 0)))
    (layer,) = slice_layers(part, [0.0])
    assert len(layer.polygons) == 1
    # fente de 0.1 × 12.5 mm (y de -2.5 à +10, bord de la boîte)
    assert layer.polygons[0].area == pytest.approx(2000 - 1.25, abs=0.05)


def test_outside_height_is_empty():
    (layer,) = slice_layers(Box(10, 10, 10), [100.0])
    assert layer.polygons == ()
