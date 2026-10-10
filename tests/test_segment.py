import math

import pytest
from build123d import Align, Box, Pos

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


@pytest.mark.parametrize(
    "args, msg",
    [
        ((500, 245, [200, 200]), "double"),
        ((500, 600, [1e-4]), "trop court"),
        ((500, 600, [500 - 1e-4]), "trop court"),
        ((500, 300, [1, 250]), "trop court"),
        ((500, 300, [250, 255]), "trop court"),
        ((500, 0), "invalide"),
        ((0, 245), "invalide"),
        ((500, 245, [math.nan]), "hors"),
        ((500, 245, [math.inf]), "hors"),
    ],
)
def test_plan_cuts_invalid(args, msg):
    with pytest.raises(SegmentError, match=msg):
        plan_cuts(*args)


def test_plan_cuts_auto_epsilon():
    assert len(plan_cuts(490.0000000001, 245)) == 1


def test_split_part_invalid_cuts():
    part = ribbed_wing()
    for cuts in ([40, 40], [0], [100], [math.nan], [150]):
        with pytest.raises(SegmentError):
            split_part(part, cuts)


def test_split_part_cut_on_internal_face():
    # boîte avec une fente borgne se terminant exactement à z = 40 ; coupe à 40
    part = Box(20, 20, 100, align=(Align.CENTER, Align.CENTER, Align.MIN))
    part = part - Pos(0, 0, 0) * Box(4, 4, 80, align=(Align.CENTER, Align.CENTER, Align.MIN))
    part = part - Pos(0, 0, 60) * Box(4, 4, 20, align=(Align.CENTER, Align.CENTER, Align.MIN))
    assert part.bounding_box().min.Z == pytest.approx(0, abs=1e-9)
    try:
        pieces = split_part(part, [40])
    except SegmentError:
        return
    assert len(pieces) == 2
    for p in pieces:
        assert p.is_valid
        assert p.bounding_box().min.Z == pytest.approx(0, abs=1e-6)
