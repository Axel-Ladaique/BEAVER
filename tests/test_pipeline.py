import pytest

from aerorib.classify import classify
from aerorib.config import load_config
from aerorib.pipeline import run_model
from aerorib.testing.synthetic import wing_x_bodies

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
    from build123d import Cylinder, Location

    from aerorib.io_step import NamedBody

    bodies = wing_x_bodies() + [NamedBody(("SBAR_far",), Cylinder(4, 80).moved(Location((40, 200, 0), (0, 90, 0))))]
    run = run_model(classify(bodies, FAST), FAST)
    assert [b.name for b in run.bodies[0].bores if b.cut_hole] == ["SBAR_8"]
