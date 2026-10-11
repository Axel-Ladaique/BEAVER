"""Gaines de câble allégées : trou + fente, sans manchon ni attache (servo, tâche 3)."""

from pathlib import Path

import pytest

from aerorib import preview
from aerorib.classify import classify
from aerorib.cli import format_bores
from aerorib.config import load_config
from aerorib.features.bores import spar_bores
from aerorib.io_step import NamedBody, read_step, write_step
from aerorib.orient import orient
from aerorib.pipeline import run_model
from aerorib.testing.synthetic import wing_x_bodies

FIXTURES = Path(__file__).parent / "fixtures"
OVER = {"material_settings": {"layer_height": 1.0}, "rib_layers": [{"spacing": 20}], "passages": {"mode": "link"}}


def cable_bodies():
    wing, spar = wing_x_bodies()
    return [wing, NamedBody(("SBAR_CABLES_8",), spar.solid)]


def test_cable_settings_precedence():
    cfg = load_config(
        overrides={
            "spars": {
                "default": {"clearance": 0.2, "slit_side": "bottom", "slit_width": 0.2, "sleeve": 1.0},
                "cable": {"strip": 1.5, "clearance": 0.3},
                "SBAR_CABLES_X": {"strip": 3.0},
            }
        }
    )
    lw = cfg.preset.line_width
    plain = cfg.spar_settings("SBAR_main")
    assert plain.sleeve == 1.0 and plain.anchor is True and plain.strip == 2.0  # la gaine ne déteint pas
    c = cfg.spar_settings("SBAR_CABLES_8", cable=True)
    assert (c.sleeve, c.anchor, c.strip) == (pytest.approx(1.2 * lw), False, 1.5)
    assert (c.clearance, c.slit_side, c.slit_width) == (0.3, "bottom", 0.2)  # cable > default
    n = cfg.spar_settings("sbar_cables_x", cable=True)  # nom > cable
    assert n.strip == 3.0 and n.clearance == 0.3
    # défaut de la bande : largeur de ligne du preset
    assert load_config().spar_settings("G", cable=True).strip == pytest.approx(lw)


def test_cable_config_errors_are_french():
    with pytest.raises(ValueError, match="spars 'cable'"):
        load_config(overrides={"spars": {"cable": {"slit_width": 0.001}}})
    with pytest.raises(ValueError, match="manchon"):
        load_config(overrides={"spars": {"cable": {"sleeve": 0.2}}})  # manchon < 1 ligne : refusé


def test_cable_bore_is_light():
    cfg = load_config(overrides=OVER)
    model = classify(cable_bodies(), cfg)
    assert [c.body.name for c in model.cables] == ["SBAR_CABLES_8"]
    (spar,) = model.of("spar")
    solid = spar.body.solid.moved(orient(model, cfg).location)
    (b,) = spar_bores([(spar.body.name, solid)], cfg, frozenset({spar.body.name}))
    ordinary_sleeve = cfg.spar_settings("x").sleeve
    assert b.cable and b.cut_hole and b.sleeve < ordinary_sleeve and b.anchor_gap == 0
    assert b.slit == cfg.spar_settings("x", cable=True).slit_width and b.side == 1  # fente vers l'extrados
    assert b.strip == pytest.approx(cfg.preset.line_width)
    (ordinary,) = spar_bores([(spar.body.name, solid)], cfg)
    assert not ordinary.cable and ordinary.sleeve > 0 and ordinary.anchor_gap > 0
    assert "SBAR_CABLES_8 (gaine, Ø" in format_bores([b])


@pytest.mark.slow
def test_preview_bores_equal_pipeline_bores(tmp_path):
    cfg = load_config(overrides={**OVER, "segment": {"max_height": 30}})
    step = write_step(tmp_path / "aile.step", cable_bodies())
    preview.clear_cache()
    prep = preview.prepare(step, cfg)
    run = run_model(classify(read_step(step), cfg), cfg)
    assert run.ok, [i.message for b in run.bodies for i in b.report.errors][:5]
    assert prep.bores == run.bodies[0].bores
    assert any(b.cable for b in prep.bores) and prep.anchors[0] == ()
    assert {h["type"] for h in prep.holes()} >= {"gaine"}


@pytest.mark.slow
def test_cable_wing_validates_and_slit_reaches_skin():
    cfg = load_config(overrides=OVER)
    msgs = []
    run = run_model(classify(cable_bodies(), cfg), cfg, progress=msgs.append)
    assert run.ok, [i.message for b in run.bodies for i in b.report.errors][:5]
    assert any("0 longeron(s), 1 gaine(s)" in m for m in msgs), msgs
    (body,) = run.bodies
    (gaine,) = [b for b in body.bores if b.cable]
    assert gaine.anchor_gap == 0 and gaine.sleeve < cfg.spar_settings("x").sleeve


def test_real_servo_fixture_cable_is_light():
    from aerorib.orient import orient

    cfg = load_config()
    model = classify(read_step(FIXTURES / "aile_4412_servo_extrados.step"), cfg)
    assert model.cables, "la gaine doit être détectée"
    loc = orient(model, cfg).location
    names = frozenset(c.body.name for c in model.cables)
    spars = [(s.body.name, s.body.solid.moved(loc)) for s in model.of("spar")]
    bores = spar_bores(spars, cfg, names)
    cab = [b for b in bores if b.cable]
    assert [b.name for b in cab] == sorted(names)
    assert all(b.sleeve < cfg.spar_settings("x").sleeve and b.anchor_gap == 0 and b.cut_hole for b in cab)
    assert all(not b.cable and b.sleeve > 0 for b in bores if b.name not in names)
    assert "gaine, Ø11.8" in format_bores(cab)
