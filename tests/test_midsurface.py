import numpy as np
import pytest
from build123d import Cylinder, Location
from shapely.geometry import Polygon, box

from beaver.midsurface import MidSurface, camber_offset, mid_line
from beaver.testing.synthetic import naca4, naca_wing


def test_mid_line_on_box():
    ym, th = mid_line(box(0, -2, 10, 4), np.array([-1.0, 5.0]), -10, 10)
    assert np.isnan(ym[0]) and th[0] == 0
    assert ym[1] == pytest.approx(1.0) and th[1] == pytest.approx(6.0)


def test_camber_offset_sign():
    pts, _ = naca4("4412", 150.0)
    sec = Polygon(pts)
    assert camber_offset(sec) > 2.0
    flipped = Polygon([(x, -y) for x, y in pts])
    assert camber_offset(flipped) < -2.0
    assert camber_offset(Polygon(naca4("0012", 150.0)[0])) == pytest.approx(0.0, abs=0.05)


def test_midsurface_follows_naca_camber_and_thickness():
    wing, camber = naca_wing(chord=150.0, span=100.0)
    mid = MidSurface.from_skin(wing, nx=61, nz=5)
    ym, th = mid.at(np.array([60.0, -10.0]), np.array([50.0, 50.0]))
    assert ym[0] == pytest.approx(camber(60.0), abs=0.1)
    assert th[0] == pytest.approx(17.40, abs=0.2)  # 2·yt(0.4)·corde pour un 4412 (= 17.399)
    assert th[1] == 0.0


def test_midsurface_ignores_inner_bore():
    wing, camber = naca_wing(chord=150.0, span=100.0)
    bored = wing - Cylinder(4, 200).moved(Location((60, camber(60), 50)))
    a = MidSurface.from_skin(wing, nx=61, nz=5).at(np.array([60.0]), np.array([50.0]))
    b = MidSurface.from_skin(bored, nx=61, nz=5).at(np.array([60.0]), np.array([50.0]))
    assert b[0][0] == pytest.approx(a[0][0], abs=1e-6) and b[1][0] == pytest.approx(a[1][0], abs=1e-6)


def test_swept_leading_edge_has_no_thickness_in_front_of_the_nose():
    # bord d'attaque en flèche de 50° : entre deux rangées de la grille, le nez avance de plusieurs mm ; lu à x fixe,
    # un point devant le nez avait encore de l'épaisseur (les nervures y entaillaient la peau)
    from beaver.testing.synthetic import swept_wing

    m = MidSurface.from_skin(swept_wing(chord=200, span=120, sweep_deg=50, code="4412"), 101, 22)
    z = np.full(3, 60.0)
    le = 60 * np.tan(np.radians(50))
    _, th = m.at(le + np.array([-1.0, -0.2, 50.0]), z)
    assert th[0] == 0 and th[1] == 0 and th[2] == pytest.approx(23.8, abs=0.3)
    # sans flèche ni effilement : identique à la lecture à x fixe
    s = MidSurface.from_skin(swept_wing(chord=200, span=120, code="4412"), 101, 22)
    fixed = MidSurface(s.xs, s.zs, s.ymid, s.thick)
    xs, zs = np.linspace(-5, 205, 50), np.linspace(1, 119, 50)
    assert np.allclose(s.at(xs, zs)[1], fixed.at(xs, zs)[1], atol=0.05)


@pytest.mark.slow
def test_swept_wing_ribs_leave_the_nose_intact():
    from collections import Counter

    from beaver import pipeline
    from beaver.classify import classify
    from beaver.config import load_config
    from beaver.io_step import NamedBody
    from beaver.testing.synthetic import swept_wing

    cfg = load_config(overrides={"printer": "h2s"})
    run = pipeline.run_model(classify([NamedBody(("Aile",), swept_wing(chord=200, span=120, sweep_deg=50, code="4412"))], cfg), cfg)
    errors = Counter(i.check for i in run.bodies[0].report.errors)
    assert run.ok, errors
