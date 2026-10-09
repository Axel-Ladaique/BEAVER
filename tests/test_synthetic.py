import pytest

from aerorib.testing.synthetic import broken_wing, naca4, naca_wing, ribbed_wing


def test_naca4_shape():
    pts, camber = naca4("4412", chord=150.0, n=80)
    xs = [p[0] for p in pts]
    # l'épaisseur est appliquée perpendiculairement à la cambrure : x déborde un peu de [0, corde]
    assert min(xs) == pytest.approx(0.0, abs=0.1)
    assert max(xs) == pytest.approx(150.0, abs=0.1)
    assert camber(0.0) == pytest.approx(0.0, abs=1e-6)
    assert camber(60.0) == pytest.approx(0.04 * 150, rel=2e-3)  # cambrure max 4 % à 40 % de corde


def test_naca_wing_is_valid_solid():
    wing, _ = naca_wing(span=100.0)
    assert wing.is_valid
    bb = wing.bounding_box()
    assert bb.min.Z == pytest.approx(0.0, abs=1e-6)
    assert bb.max.Z == pytest.approx(100.0, abs=1e-6)


def test_ribbed_wing_removes_material():
    wing, _ = naca_wing()
    ribbed = ribbed_wing()
    assert ribbed.is_valid
    assert ribbed.volume < wing.volume


@pytest.mark.parametrize("kind", ["hole", "island", "narrow", "skin"])
def test_broken_variants_are_valid_solids(kind):
    part = broken_wing(kind)
    assert part.is_valid
