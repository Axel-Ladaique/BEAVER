import multiprocessing

import pytest
from shapely.geometry import Point, Polygon, box

from aerorib.slicing import Layer
from aerorib.validate import (
    check_connectivity,
    check_material_width,
    check_narrow_slits,
    check_skin,
    slicer_closing,
)

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
    assert issues[0].severity == "warning"
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


def test_slicer_closing_closes_narrow_notch_only():
    narrow, wide = notch(10, 0.05), notch(10, 0.1)
    assert slicer_closing([narrow], 0.08)[0].area == pytest.approx(BASE.area, abs=1e-3)
    assert slicer_closing([wide], 0.08)[0].area == pytest.approx(wide.area, abs=1e-3)


def test_skin_ignores_enlarged_inner_hole():
    orig = Layer(1.0, (BASE.difference(Point(10, 5).buffer(2.0)),))
    res = Layer(1.0, (BASE.difference(Point(10, 5).buffer(2.3)),))
    assert check_skin(res, orig, max_mouth=0.5, tol=0.05) == []


def necked(width):
    """Deux blocs de 10 × 10 reliés par un col de `width` mm (5 mm de long)."""
    return box(0, 0, 10, 10).union(box(10, 5 - width / 2, 15, 5 + width / 2)).union(box(15, 0, 25, 10))


def test_material_width_thin_neck_is_error():
    issues = check_material_width(Layer(1.0, (necked(0.3),)), line_width=0.45)
    assert [(i.check, i.severity) for i in issues] == [("material_width", "error")]
    assert "plus fine qu'une largeur de ligne" in issues[0].message
    x0, _, x1, _ = issues[0].bounds
    assert x1 - x0 < 10.5  # la plus petite partie : un des deux blocs


def test_material_width_wide_neck_ok():
    assert check_material_width(Layer(1.0, (necked(1.0),)), line_width=0.45) == []


def test_material_width_tapered_trailing_edge_ok():
    # pointe effilée (bord de fuite) : elle disparaît à l'érosion sans créer d'îlot
    wedge = Polygon([(0, 0), (40, 4.5), (40, 5.5), (0, 10)]).union(box(-10, 0, 0, 10))
    assert check_material_width(Layer(1.0, (wedge,)), line_width=0.45) == []


def test_material_width_reports_each_polygon_once():
    # deux îlots déjà distincts : c'est l'affaire du contrôle de connexité, pas de celui-ci
    assert check_material_width(Layer(1.0, (box(0, 0, 1, 1), box(5, 0, 6, 1))), line_width=0.45) == []


def _worker_daemon(q):
    import os

    from aerorib.validate import PARALLEL_ENV, _check_layer, _run_checks

    os.environ[PARALLEL_ENV] = "1"
    jobs = [(float(i), (BASE,), None, 0.4, 0.4, 2.0, 0.1) for i in range(4)]
    try:
        q.put(("ok", _run_checks(jobs) == [_check_layer(j) for j in jobs]))
    except BaseException as e:  # noqa: BLE001
        q.put(("exc", repr(e)))


def test_run_checks_repli_serie_dans_processus_demon():
    ctx = multiprocessing.get_context("spawn")
    q = ctx.Queue()
    p = ctx.Process(target=_worker_daemon, args=(q,), daemon=True)
    p.start()
    try:
        assert q.get(timeout=60) == ("ok", True)
    finally:
        p.join(10)
        if p.is_alive():
            p.kill()
            p.join()
