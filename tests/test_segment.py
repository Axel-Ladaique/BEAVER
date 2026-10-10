import pytest

from aerorib.segment import SegmentError, plan_cuts, split_part
from build123d import Plane
from aerorib.testing.synthetic import ribbed_wing


def test_plan_cuts_auto():
    assert plan_cuts(210, 245) == []
    assert plan_cuts(500, 245) == pytest.approx([500 / 3, 1000 / 3])


def test_plan_cuts_manual():
    assert plan_cuts(500, 245, [200, 400]) == [200, 400]
    assert plan_cuts(500, 245, [400, 200]) == [200, 400]
    with pytest.raises(SegmentError, match="tronçon de 300"):
        plan_cuts(500, 245, [300])
    for bad in ([0], [500], [-5], [600]):
        with pytest.raises(SegmentError):
            plan_cuts(500, 245, bad)


def _area(shape, z):
    # section plane exacte (aire de faces planes) : le pavage par maillage est bruité à ~2e-3 mm²
    return sum(f.area for f in (shape & Plane.XY.offset(z)).faces())


def test_split_part_ribbed_wing():
    part = ribbed_wing()
    pieces = split_part(part, [40, 70])
    assert len(pieces) == 3
    origin = [0, 40, 70]
    for piece, h, z0 in zip(pieces, [40, 30, 30], origin):
        bb = piece.bounding_box()
        assert bb.min.Z == pytest.approx(0, abs=1e-6)
        assert bb.size.Z == pytest.approx(h, abs=1e-3)
        mid = h / 2
        assert _area(piece, mid) == pytest.approx(_area(part, z0 + mid), abs=1e-3)
