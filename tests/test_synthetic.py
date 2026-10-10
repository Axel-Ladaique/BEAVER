import math

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


@pytest.mark.parametrize("kind", ["hole", "island", "narrow", "skin", "hole_narrow"])
def test_broken_variants_are_valid_solids(kind):
    part = broken_wing(kind)
    assert part.is_valid


def test_ribbed_wing_is_one_solid():
    # la bande de séparation relie les segments : la pièce valide reste d'un seul tenant
    assert len(ribbed_wing().solids()) == 1


def test_island_variant_has_two_solids():
    # la fente traversante coupe l'aile en deux îlots
    assert len(broken_wing("island").solids()) == 2


@pytest.mark.parametrize("kind", ["hole", "narrow", "skin", "hole_narrow"])
def test_non_island_variants_have_one_solid(kind):
    assert len(broken_wing(kind).solids()) == 1


def test_hole_variant_removes_cylinder_volume():
    # cylindre de rayon 4 mm entièrement dans l'aile, sur toute l'envergure de 100 mm
    wing, _ = naca_wing()
    hole = broken_wing("hole")
    assert hole.volume == pytest.approx(wing.volume - math.pi * 4**2 * 100, rel=1e-3)


def test_slit_width_scales_removed_volume():
    # même fente, seule la largeur change : 3.0 mm contre 0.05 mm.
    # Le volume retiré se mesure par la différence booléenne (petit solide), et non par
    # la différence de deux volumes complets : ces derniers ont une erreur d'environ 26 mm3,
    # du même ordre que la fente fine (~28 mm3).
    wing, _ = naca_wing()
    removed_narrow = (wing - broken_wing("narrow")).volume
    removed_skin = (wing - broken_wing("skin")).volume
    assert removed_skin / removed_narrow == pytest.approx(3.0 / 0.05, rel=1e-2)
