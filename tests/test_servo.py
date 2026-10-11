"""Baies de servo : poche avec rampes, contournement par les nervures (servo, tâche 4)."""

import math
from pathlib import Path

import numpy as np
import pytest

from aerorib import preview
from aerorib.classify import classify
from aerorib.config import load_config
from aerorib.features.servo import ServoError, pocket_profile, pocket_zones_2d, servo_bays
from aerorib.io_step import read_step, write_step
from aerorib.midsurface import MidSurface
from aerorib.orient import orient
from aerorib.pipeline import PipelineError, run_model
from aerorib.report import format_bays
from aerorib.slicing import to_mesh
from aerorib.testing.synthetic import servo_wing_bodies

FIXTURES = Path(__file__).parent / "fixtures"
OVER = {"material_settings": {"layer_height": 1.0}, "rib_layers": [{"spacing": 20}], "passages": {"mode": "link"}}


def _oriented(bodies, cfg):
    model = classify(bodies, cfg)
    o = orient(model, cfg)
    skin = model.of("skin")[0].body.solid.moved(o.location)
    servos = [(s.body.name, s.body.solid.moved(o.location)) for s in model.of("servo")]
    return skin, servos


def _bays(bodies, cfg=None):
    cfg = cfg or load_config(overrides=OVER)
    skin, servos = _oriented(bodies, cfg)
    mid = MidSurface.from_skin(skin, *cfg.split.grid)
    return servo_bays(servos, skin, mid, cfg), skin, mid


def test_bay_side_box_and_ramp():
    cfg = load_config(overrides=OVER)
    (top,), skin, mid = _bays(servo_wing_bodies("extrados"), cfg)
    assert top.name == "SERVO_1" and top.side == 1
    assert top.x1 - top.x0 == pytest.approx(20.0, abs=0.05) and top.z1 - top.z0 == pytest.approx(35.0, abs=0.05)
    ym, th = mid.at(np.linspace(top.x0, top.x1, 21), np.full(21, top.z0))
    depth = float(np.max(ym + th / 2 - top.y_floor))
    assert top.depth == pytest.approx(depth, abs=0.1)
    tan = math.tan(math.radians(cfg.preset.max_overhang_deg))
    assert top.ramp == pytest.approx(top.depth / tan)
    lo, hi = top.opening
    assert lo == pytest.approx(top.z0 - top.ramp) and hi == pytest.approx(top.z1 + top.ramp)
    (bot,), _, _ = _bays(servo_wing_bodies("intrados"), cfg)
    assert bot.side == -1 and bot.depth > 0


def test_pocket_profile_ramps_follow_max_overhang():
    cfg = load_config(overrides={**OVER, "material_settings": {"layer_height": 1.0, "max_overhang_deg": 40}})
    (bay,), _, _ = _bays(servo_wing_bodies("extrados"), cfg)
    prof = pocket_profile(bay)
    pts = list(prof.exterior.coords)[:-1]
    assert len(pts) == 4
    # (z, y) : fond de z0 à z1, rampes jusqu'au-delà de la peau
    floor = [p for p in pts if p[1] == pytest.approx(bay.y_floor)]
    assert sorted(z for z, _ in floor) == pytest.approx([bay.z0, bay.z1])
    for z, y in pts:
        if y != pytest.approx(bay.y_floor):
            dz = (bay.z0 - z) if z < bay.z0 else (z - bay.z1)
            # inclinaison de la rampe par rapport à Z (direction d'impression) = surplomb max
            assert math.degrees(math.atan2(y - bay.y_floor, dz)) == pytest.approx(40)


def test_pocket_zones_cover_the_pocket_with_strip():
    cfg = load_config(overrides=OVER)
    (bay,), skin, _ = _bays(servo_wing_bodies("extrados"), cfg)
    zmin = skin.bounding_box().min.Z
    d0, d1 = math.sin(math.radians(45)), math.cos(math.radians(45))
    # plaque qui traverse le milieu de la boîte : x = c + s·d0, z = zmin + s·d1
    s_mid = ((bay.z0 + bay.z1) / 2 - zmin) / d1
    c = (bay.x0 + bay.x1) / 2 - s_mid * d0
    (zone,) = pocket_zones_2d(bay, c, d0, d1, zmin, zmin + 120, strip=2.0, slit=0.1)
    from shapely.geometry import Point

    assert zone.contains(Point(s_mid, bay.y_floor + 1))
    assert zone.contains(Point(s_mid, bay.y_floor - 1.9))  # bande de 2 mm sous le fond
    assert not zone.contains(Point(s_mid, bay.y_floor - 2.2))
    # plaque qui passe loin de la boîte : aucune zone
    assert pocket_zones_2d(bay, c + 200, d0, d1, zmin, zmin + 120, strip=2.0, slit=0.1) == []


def test_bay_errors_are_french():
    with pytest.raises(ServoError, match="SERVO_1 : la boîte ne traverse aucune peau"):
        _bays(servo_wing_bodies(box=(22.0, 42.0, 2.0, 6.0, 40.0, 75.0)))
    with pytest.raises(ServoError, match="SERVO_1 : le fond de la baie laisse .* mm avant l'intrados"):
        _bays(servo_wing_bodies(box=(22.0, 42.0, -1.0, 16.0, 40.0, 75.0)))
    with pytest.raises(ServoError, match="SERVO_1 : la boîte traverse l'extrados et l'intrados"):
        _bays(servo_wing_bodies(box=(22.0, 42.0, -8.0, 16.0, 40.0, 75.0)))
    with pytest.raises(ServoError, match="SERVO_1 : baie non alignée sur la corde / l'envergure"):
        _bays(servo_wing_bodies(tilt_deg=10))
    # léger défaut d'alignement (< 2°) toléré
    (bay,), _, _ = _bays(servo_wing_bodies(tilt_deg=1))
    assert bay.side == 1


def test_bay_error_reaches_pipeline_in_french():
    cfg = load_config(overrides=OVER)
    with pytest.raises(PipelineError, match="SERVO_1 : la boîte ne traverse aucune peau"):
        run_model(classify(servo_wing_bodies(box=(22.0, 42.0, 2.0, 6.0, 40.0, 75.0)), cfg), cfg)


def test_min_floor_gap_setting():
    cfg = load_config(overrides={**OVER, "servo": {"min_floor_gap": 7.0}})
    with pytest.raises(ServoError, match=r"servo\.min_floor_gap = 7"):
        _bays(servo_wing_bodies("extrados"), cfg)
    with pytest.raises(ValueError, match="servo"):
        load_config(overrides={"servo": {"strip": 0}})


_RUNS: dict = {}


@pytest.fixture(scope="module")
def run_of(tmp_path_factory):
    """(STEP, génération) de l'aile synthétique avec baie, une seule par côté pour tout le module. Lue depuis le
    STEP écrit, comme l'aperçu (l'aller-retour STEP déplace légèrement l'orientation)."""
    root = tmp_path_factory.mktemp("servo")

    def get(side):
        if side not in _RUNS:
            cfg = load_config(overrides=OVER)
            bodies = servo_wing_bodies("extrados", cable=True) if side == "gaine" else servo_wing_bodies(side)
            step = write_step(root / f"servo_{side}.step", bodies)
            _RUNS[side] = step, run_model(classify(read_step(step), cfg), cfg)
        return _RUNS[side]

    yield get
    _RUNS.clear()


@pytest.fixture(scope="module", params=["extrados", "intrados"])
def servo_run(request, run_of):
    return request.param, load_config(overrides=OVER), run_of(request.param)[1]


def test_cable_entering_bay_is_printable(run_of):
    """Gaine qui débouche dans la baie sous son fond (comme la fixture servo) : son bord dans la poche est une
    ouverture voulue, pas une « peau entamée »."""
    _, run = run_of("gaine")
    (body,) = run.bodies
    assert [b.cable for b in body.bores] == [True] and len(body.bays) == 1
    assert body.report.ok, [(i.z, i.message, i.bounds) for i in body.report.errors][:5]


def test_servo_wing_is_printable(servo_run):
    side, cfg, run = servo_run
    (body,) = run.bodies
    assert body.report.ok, [(i.z, i.message) for i in body.report.errors][:5]
    assert not [i for i in body.report.issues if i.check == "connectivity"]  # un seul contour par couche
    (bay,) = body.bays
    assert bay.side == (1 if side == "extrados" else -1)
    # la poche est bien ouverte : à mi-baie, plus de matière dans la boîte
    from aerorib.slicing import slice_layers

    (layer,) = slice_layers(body.result, [(bay.z0 + bay.z1) / 2], cfg.validation.tessellation_tol)
    from shapely.geometry import box

    inner = box(bay.x0 + 0.5, min(bay.y_floor, bay.y_box) + 0.5, bay.x1 - 0.5, max(bay.y_floor, bay.y_box) - 0.5)
    assert sum(p.intersection(inner).area for p in layer.polygons) < 0.5
    text = format_bays(body.bays)
    assert f"SERVO_1 (baie {side}, 20.0 × 35.0 mm utiles, ouverture" in text


def test_ramps_have_no_overhang_beyond_limit(servo_run):
    _, cfg, run = servo_run
    (body,) = run.bodies
    (bay,) = body.bays
    limit = cfg.preset.max_overhang_deg
    mesh = to_mesh(body.result, cfg.validation.tessellation_tol)
    c = mesh.triangles_center
    n = mesh.face_normals
    lo, hi = bay.opening
    ylo, yhi = sorted((bay.y_floor, bay.y_out))
    near = (
        (c[:, 0] > bay.x0 - 0.3) & (c[:, 0] < bay.x1 + 0.3)
        & (c[:, 2] > lo - 0.3) & (c[:, 2] < hi + 0.3)
        & (c[:, 1] > ylo - 0.3) & (c[:, 1] < yhi + 0.3)
    )
    assert near.sum() > 10
    down = near & (n[:, 2] < 0)
    over = np.degrees(np.arcsin(np.clip(-n[down, 2], 0, 1)))
    assert over.max() <= limit + 1.0
    # les rampes existent aux deux bouts (pente à la limite, vers le haut et vers le bas)
    assert over.max() == pytest.approx(limit, abs=1.0)
    up = near & (n[:, 2] > 0)
    assert np.degrees(np.arcsin(np.clip(n[up, 2], 0, 1))).max() == pytest.approx(limit, abs=1.0)


def test_preview_plates_equal_generation_with_servo(run_of):
    cfg = load_config(overrides=OVER)
    step, run = run_of("extrados")
    preview.clear_cache()
    prep = preview.prepare(step, cfg)
    plates = preview.plates_2d(prep, cfg)
    assert prep.bays == run.bodies[0].bays
    assert len(plates) == run.bodies[0].plates
    # la baie retire bien de la matière de plaques (zones de poche + ombre)
    from dataclasses import replace

    free = preview.plates_2d(replace(prep, bays=()), cfg)
    assert sum(p.polygon.area for p in plates) < sum(p.polygon.area for p in free) - 50
    # coupe d'aperçu à mi-baie : la poche y est retirée, sans défaut de connexité
    (bay,) = prep.bays
    view = preview.section(prep, plates, cfg, (bay.z0 + bay.z1) / 2)
    from shapely.geometry import Point

    assert not any(p.contains(Point((bay.x0 + bay.x1) / 2, bay.y_floor + 0.5)) for p in view.polygons)
    assert not [d for d in view.defects if d.check == "connectivity"]


def test_prepare_key_follows_servo_and_material():
    base = preview.prepare_key(load_config())
    assert base != preview.prepare_key(load_config(overrides={"servo": {"min_floor_gap": 2.0}}))
    assert base != preview.prepare_key(load_config(overrides={"material_settings": {"max_overhang_deg": 40}}))


@pytest.mark.slow
def test_servo_fixture_end_to_end():
    cfg = load_config()
    run = run_model(classify(read_step(FIXTURES / "aile_4412_servo_extrados.step"), cfg), cfg)
    (body,) = run.bodies
    assert body.report.ok, [(i.z, i.message, i.bounds) for i in body.report.errors][:10]
    (bay,) = body.bays
    assert bay.name == "SERVO_1" and bay.side == 1
    print(format_bays(body.bays), body.plates, body.report.stats, body.warnings)
