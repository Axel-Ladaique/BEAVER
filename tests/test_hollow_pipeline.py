"""Évidements de bout en bout : aile synthétique imprimable, gain de masse et de trajet, aperçu = génération."""

from pathlib import Path

import pytest

from beaver import preview
from beaver.classify import classify
from beaver.config import load_config
from beaver.features import ribs
from beaver.io_step import read_step, write_step
from beaver.pipeline import generate_skin, run_model
from beaver.slicing import slice_layers
from beaver.testing.synthetic import wing_x_bodies

FIXTURE = Path(__file__).parent / "fixtures" / "bambu_p1s_pla_aero.3mf"
FUSION_V2 = Path(__file__).parent / "fixtures" / "fusion_aile_4412_simple_sbar_v2.step"


def _cfg(mode: str, **over):
    # espacement par défaut (30) : à 20 mm, les croisements (14 mm le long de la plaque) laissent peu à évider
    data = {"material_settings": {"layer_height": 1.0}, "passages": {"mode": "link"}, "ribs_hollow": {"mode": mode}}
    data.update(over)
    return load_config(overrides=data)


def _run(mode: str):
    cfg = _cfg(mode)
    run = run_model(classify(wing_x_bodies(), cfg), cfg)
    (body,) = run.bodies
    return run, body


@pytest.fixture(scope="module")
def full_run():
    return _run("off")


def _check_printable(mode: str, full_run):
    run, body = _run(mode)
    _, full = full_run
    assert run.ok, [(i.z, i.message) for i in body.report.errors][:5]
    assert body.result.is_valid and len(body.result.solids()) == 1
    assert body.plates == full.plates  # mêmes plaques, échancrées
    # chaque couche : un seul contour, sans trou (vase mode)
    zs = [z + 0.5 for z in range(0, 80, 7)]
    for layer in slice_layers(body.result, zs):
        assert len(layer.polygons) == 1 and not layer.polygons[0].interiors, layer.z
    for key in ("path_length_m", "mass_g"):
        assert body.report.stats[key] < 0.97 * full.report.stats[key], key
    return body


def test_synthetic_wing_hollow_at_crossings_is_printable(full_run):
    _check_printable("croisements", full_run)


@pytest.mark.slow
def test_synthetic_wing_hollow_at_regular_pitch_is_printable(full_run):
    _check_printable("pas", full_run)


def test_preview_equals_generation_with_hollow(tmp_path, monkeypatch):
    step = write_step(tmp_path / "aile.step", wing_x_bodies())
    cfg = _cfg("croisements", bambu={"template": str(FIXTURE)})
    prep = preview.prepare(step, cfg)
    # les évidements ne changent pas la préparation (réutilisée) mais changent les plaques
    assert preview.prepare_key(cfg) == preview.prepare_key(_cfg("off", bambu={"template": str(FIXTURE)}))
    plates = preview.plates_2d(prep, cfg)
    assert [p.polygon.wkb for p in plates] != [p.polygon.wkb for p in preview.plates_2d(prep, _cfg("off"))]
    seen = []
    real = ribs.diamond_profiles

    def spy(*a, **k):
        out = real(*a, **k)
        seen.extend(out)
        return out

    monkeypatch.setattr(ribs, "diamond_profiles", spy)
    gen = generate_skin(prep.skin, list(prep.spars), cfg)
    assert gen.plates == len(plates) == len(seen)
    assert [p.polygon.wkb for p in seen] == [p.polygon.wkb for p in plates]


@pytest.mark.slow
def test_real_wing_v2_hollow_at_crossings():
    """Aile réelle v2 (sans évidement : 84 plaques, ≈ 909 m, ≈ 61 g) évidée aux croisements : imprimable, plus légère."""
    cfg = load_config(overrides={"ribs_hollow": {"mode": "croisements"}})
    run = run_model(classify(read_step(FUSION_V2), cfg), cfg)
    (body,) = run.bodies
    assert body.report.ok, [(i.z, i.message) for i in body.report.errors][:5]
    assert body.plates == 84
    assert body.report.stats["path_length_m"] < 0.9 * 909
    assert body.report.stats["mass_g"] < 0.9 * 61
