import dataclasses

import pytest
from build123d import Cylinder, Location
from shapely.geometry import box
from shapely.ops import unary_union

from aerorib import pipeline
from aerorib.classify import classify
from aerorib.config import load_config
from aerorib.io_step import NamedBody
from aerorib.pipeline import PipelineError, generate_skin, run_model, spar_obstruction
from aerorib.slicing import slice_layers
from aerorib.testing.synthetic import naca_wing, wing_x_bodies

FAST = load_config(overrides={"material_settings": {"layer_height": 1.0}, "rib_layers": [{"spacing": 20}]})


def test_synthetic_wing_is_printable():
    model = classify(wing_x_bodies(), FAST)
    messages = []
    run = run_model(model, FAST, progress=messages.append)
    assert run.ok, [i.message for b in run.bodies for i in b.report.errors][:5]
    (body,) = run.bodies
    assert body.name == "Aile_test" and body.plates > 10
    assert sorted(b.cut_hole for b in body.bores) == [False, True]
    assert body.result.is_valid and len(body.result.solids()) == 1
    bb = body.result.bounding_box()
    assert bb.min.Z == pytest.approx(0.0, abs=1e-3) and bb.max.Z == pytest.approx(80.0, abs=1e-3)
    assert body.result.volume < body.original.volume
    assert any("nervures" in m for m in messages)


def test_spar_outside_skin_is_ignored():
    bodies = wing_x_bodies() + [NamedBody(("SBAR_far",), Cylinder(4, 80).moved(Location((40, 200, 0), (0, 90, 0))))]
    run = run_model(classify(bodies, FAST), FAST)
    assert [b.name for b in run.bodies[0].bores if b.cut_hole] == ["SBAR_8"]


# --- étendue des longerons et contrôle final d'obstruction (G1) ---------------------------------

# 60 mm et phase 15 : à z = 60, aucune nervure ne passe en x = 30 (position du longeron)
HALF = load_config(overrides={"rib_layers": [{"spacing": 60, "phase": 15}]})


def _half_span_case():
    wing, camber = naca_wing(chord=100.0, span=80.0)
    spar = Cylinder(4, 40).moved(Location((30, camber(30), 20)))  # z de 0 à 40 sur 80
    return wing.solids()[0], spar, camber


@pytest.fixture(scope="module")
def half_span():
    skin, spar, camber = _half_span_case()
    result, bores, _ = generate_skin(skin, [("SBAR_demi", spar)], HALF)
    return skin, spar, camber, result, bores


def test_half_span_spar_has_no_slit_beyond_its_end(half_span):
    _, _, camber, result, bores = half_span
    (b,) = [b for b in bores if b.cut_hole]
    assert (b.z0, b.z1) == (pytest.approx(0.0), pytest.approx(40.0))
    yc = camber(30)
    probe = box(29.9, yc + 4.6, 30.1, yc + 5.4)  # entre le haut du trou et l'extrados, sur l'axe de la fente
    low, high = slice_layers(result, [20.0, 60.0])
    assert not unary_union(low.polygons).contains(probe)  # fente présente sous z1
    assert unary_union(high.polygons).contains(probe)  # rien au-dessus de z1 + marge
    assert unary_union(high.polygons).contains(box(29, yc - 1, 31, yc + 1))  # ni trou


def test_spar_obstruction(half_span):
    skin, spar, _, result, _ = half_span
    assert spar_obstruction(spar, skin) > 1000  # peau pleine : le longeron est dans la matière
    assert spar_obstruction(spar, result) < 1.0
    assert spar_obstruction(spar.moved(Location((1, 0, 0))), result) > 1.0


def test_offset_spar_is_refused(monkeypatch):
    skin, spar, _ = _half_span_case()
    real = pipeline.spar_bores
    monkeypatch.setattr(pipeline, "spar_bores", lambda *a: [dataclasses.replace(b, x=b.x + 2) for b in real(*a)])
    monkeypatch.setattr(pipeline, "diamond_plates", lambda *a, **k: [])  # rapide : seul le trou compte
    with pytest.raises(PipelineError, match="traverse la matière"):
        generate_skin(skin, [("SBAR_demi", spar)], HALF)


def test_tilted_spar_is_refused():
    skin, _, camber = _half_span_case()
    spar = Cylinder(4, 40).moved(Location((30, camber(30), 20), (0, 3, 0)))
    with pytest.raises(PipelineError, match="incliné de 3.0°"):
        generate_skin(skin, [("SBAR_penche", spar)], HALF)


# --- erreurs lisibles (G4) et plusieurs peaux (G5) ---------------------------------------------


def test_orientation_failure_is_pipeline_error(monkeypatch):
    def boom(model, cfg):
        raise ValueError("section d'emplanture introuvable")

    monkeypatch.setattr(pipeline, "orient", boom)
    with pytest.raises(PipelineError, match="orientation impossible : section d'emplanture introuvable"):
        run_model(classify(wing_x_bodies(), FAST), FAST)


@pytest.mark.parametrize("exc", [ValueError("Null TopoDS_Shape"), RuntimeError("BRep_API: command not done")])
def test_generation_failure_is_pipeline_error(monkeypatch, exc):
    def boom(*a, **k):
        raise exc

    monkeypatch.setattr(pipeline, "generate_skin", boom)
    with pytest.raises(PipelineError, match="Aile_test : génération impossible"):
        run_model(classify(wing_x_bodies(), FAST), FAST)


def test_occ_failure_is_pipeline_error(monkeypatch):
    from OCP.Standard import Standard_Failure

    def boom(*a, **k):
        raise Standard_Failure("BOPAlgo")

    monkeypatch.setattr(pipeline, "generate_skin", boom)
    with pytest.raises(PipelineError, match="génération impossible"):
        run_model(classify(wing_x_bodies(), FAST), FAST)


def test_several_skins_refused():
    from build123d import Box

    bodies = wing_x_bodies() + [NamedBody(("Aile_B",), Box(10, 10, 10))]
    with pytest.raises(PipelineError, match=r"plusieurs peaux \(Aile_test, Aile_B\) : en P1, traiter une pièce"):
        run_model(classify(bodies, FAST), FAST)


def test_coaxial_small_bore_is_not_recut_by_the_spar(monkeypatch):
    from build123d import Align, Box
    from shapely.geometry import Point

    monkeypatch.setattr(pipeline, "diamond_plates", lambda *a, **k: [])  # rapide : seuls les trous comptent
    skin = (
        Box(60, 30, 100, align=(Align.CENTER, Align.CENTER, Align.MIN))
        - Cylinder(1, 40).moved(Location((10, 0, 80)))
    ).solids()[0]
    spar = Cylinder(4, 40).moved(Location((10, 0, 20)))
    result, bores, _ = generate_skin(skin, [("SBAR", spar)], HALF)
    assert sorted((b.name, b.cut_hole, round(b.radius, 2)) for b in bores) == [
        ("SBAR", True, 4.15), ("passage 1", False, 1.0)
    ]
    mid, upper = (unary_union(layer.polygons) for layer in slice_layers(result, [50.0, 80.0]))
    assert mid.contains(Point(10, 0).buffer(4.5))  # aucune coupe entre le longeron et l'alésage
    assert upper.contains(Point(10, -2.5)) and not upper.contains(Point(10, -0.5))  # Ø2 resté Ø2
