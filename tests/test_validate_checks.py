from shapely.geometry import Point, Polygon, box

from aerorib.slicing import Layer
from aerorib.validate import check_connectivity, check_narrow_slits, check_skin

BASE = box(0, 0, 20, 10)


def notch(x, width, depth=5.0):
    return BASE.difference(box(x - width / 2, 10 - depth, x + width / 2, 10.5))


def test_connectivity_ok():
    assert check_connectivity(Layer(1.0, (notch(10, 0.1),))) == []


def test_connectivity_islands():
    issues = check_connectivity(Layer(1.0, (box(0, 0, 1, 1), box(5, 0, 6, 1))))
    assert [i.check for i in issues] == ["connectivity"]
    assert issues[0].severity == "error"
    assert "2 îlots" in issues[0].message


def test_connectivity_interior_hole():
    poly = BASE.difference(Point(10, 5).buffer(2))
    issues = check_connectivity(Layer(1.0, (poly,)))
    assert len(issues) == 1
    assert "trou isolé" in issues[0].message
    x0, y0, x1, y1 = issues[0].bounds
    assert 7.9 < x0 < 8.1 and 11.9 < x1 < 12.1


def test_connectivity_empty_layer():
    issues = check_connectivity(Layer(1.0, ()))
    assert len(issues) == 1 and "vide" in issues[0].message


def test_narrow_slit_detected():
    issues = check_narrow_slits(Layer(1.0, (notch(10, 0.05),)), min_width=0.08)
    assert len(issues) == 1
    assert issues[0].check == "narrow_slit"
    x0, _, x1, _ = issues[0].bounds
    assert 9.9 < x0 and x1 < 10.1


def test_wide_enough_slit_ok():
    assert check_narrow_slits(Layer(1.0, (notch(10, 0.1),)), min_width=0.08) == []


def test_skin_ok_with_slit_mouth():
    orig = Layer(1.0, (BASE,))
    res = Layer(1.0, (notch(10, 0.1),))
    assert check_skin(res, orig, max_mouth=0.5, tol=0.05) == []


def test_skin_damage_detected():
    orig = Layer(1.0, (BASE,))
    res = Layer(1.0, (notch(10, 3.0),))
    issues = check_skin(res, orig, max_mouth=0.5, tol=0.05)
    assert len(issues) == 1
    assert issues[0].check == "skin"
    assert "3.0 mm" in issues[0].message


def test_skin_threshold_uses_real_missing_length():
    orig = Layer(1.0, (BASE,))
    # ouverture réelle de 0.55 mm > 0.5 : détectée (la portion érodée ne mesure que ~0.45)
    assert len(check_skin(Layer(1.0, (notch(10, 0.55),)), orig, max_mouth=0.5, tol=0.05)) == 1
    assert check_skin(Layer(1.0, (notch(10, 0.45),)), orig, max_mouth=0.5, tol=0.05) == []
