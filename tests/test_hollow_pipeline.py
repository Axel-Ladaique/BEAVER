"""Évidements de bout en bout : aile synthétique imprimable, gain de masse et de trajet, aperçu = génération."""

from pathlib import Path

import pytest
import numpy as np
from shapely.geometry import LineString

from beaver import preview
from beaver.classify import classify
from beaver.config import load_config
from beaver.features import ribs
from beaver.io_step import read_step, write_step
from beaver.pipeline import generate_skin, run_model
from beaver.slicing import slice_layers
from beaver.testing.synthetic import wide_wing_bodies, wing_x_bodies

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
    # gain estimé pour le rapport (profils des plaques, sans booléen) : proche du gain mesuré
    assert full.hollow is None and full.hollow_saved_g == 0
    assert body.hollow.startswith("ponts ")
    measured = full.report.stats["mass_g"] - body.report.stats["mass_g"]
    assert body.hollow_saved_g == pytest.approx(measured, rel=0.3)
    return body


def test_synthetic_wing_hollow_at_crossings_is_printable(full_run):
    _check_printable("croisements", full_run)


@pytest.mark.slow
def test_synthetic_wing_hollow_at_regular_pitch_is_printable(full_run):
    _check_printable("pas", full_run)


CUT = {"bambu": {"template": str(FIXTURE)}, "segment": {"max_height": 30}}  # 80 mm ⇒ coupes en Z à 26,7 et 53,3


def _tip(poly, s, top):
    cut = poly.intersection(LineString([(s, -1e3), (s, 1e3)]))
    return None if cut.is_empty else (cut.bounds[1] if top else cut.bounds[3])


def _full_near(prof, ref, s, w):
    """Sur [s − w, s + w], le fond de fente du profil évidé `prof` est celui du profil plein `ref` (à 0,05 mm près :
    les bornes d'évidement deviennent des sommets du bord, ce qui le décale de quelques µm à centièmes)."""
    for v in np.linspace(s - w, s + w, 41):
        a, b = _tip(prof.polygon, v, prof.top), _tip(ref.polygon, v, ref.top)
        if (a is None) != (b is None) or (a is not None and abs(a - b) > 0.05):
            return False
    return True


def test_hollow_plates_full_near_z_cuts_preview_equals_generation(tmp_path, monkeypatch):
    step = write_step(tmp_path / "aile.step", wing_x_bodies())
    cfg = _cfg("croisements", **CUT)
    prep = preview.prepare(step, cfg)
    # les évidements ne changent pas la préparation (réutilisée) mais changent les plaques
    assert preview.prepare_key(cfg) == preview.prepare_key(_cfg("off", **CUT))
    plates = preview.plates_2d(prep, cfg)
    full = preview.plates_2d(prep, _cfg("off", **CUT))
    assert [p.polygon.wkb for p in plates] != [p.polygon.wkb for p in full]
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
    # le plan de découpe calculé avant les nervures est celui de la découpe (et celui de l'aperçu)
    assert gen.plan is not None and len(gen.plan.cuts) == 2
    assert [c["z0"] for c in preview.planform(prep, plates, cfg).to_dict()["morceaux"]][1:] == pytest.approx(
        [prep.zrange[0] + c for c in gen.plan.cuts], abs=0.01)
    # pleine hauteur à ± bridge_width (4 mm, en s) de chaque coupe en Z ; évidé ailleurs
    w = cfg.ribs_hollow.bridge_width
    for p, q in zip(plates, full):
        for zc in gen.plan.cuts:
            s = (prep.zrange[0] + zc - p.zmin) / p.d1
            assert _full_near(p, q, s, 0.98 * w), (p.c, p.top, zc)


def test_hollow_plates_full_near_chord_cut(tmp_path):
    """Aile large (corde 400 mm, coupée dans la corde sur P1S) : nervures pleines à ± bridge_width du plan."""
    step = write_step(tmp_path / "large.step", wide_wing_bodies())
    over = {"bambu": {"template": str(FIXTURE)}, "rib_layers": [{"spacing": 40}]}
    cfg = load_config(overrides={**over, "ribs_hollow": {"mode": "croisements"}})
    prep = preview.prepare(step, cfg)
    g = preview.guides(prep, cfg)
    assert g is not None and g.lines and not g.z
    plates = preview.plates_2d(prep, cfg)
    full = preview.plates_2d(prep, load_config(overrides=over))
    assert len(plates) == len(full)
    w, hit = cfg.ribs_hollow.bridge_width, 0
    for p, q in zip(plates, full):
        for x0, m, z_lo, z_hi in g.lines:
            s = (x0 + m * p.zmin - p.c) / (p.d0 - m * p.d1)  # x = c + s·d0 sur x = x0 + m·z, z = zmin + s·d1
            if z_lo <= p.zmin + s * p.d1 <= z_hi and p.polygon.bounds[0] < s < p.polygon.bounds[2]:
                hit += 1
                assert _full_near(p, q, s, 0.98 * w), (p.c, p.top)
    assert hit >= 4
    assert sum(p.polygon.area for p in plates) < sum(p.polygon.area for p in full)


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
    # gain estimé du rapport (profils) proche du gain mesuré (≈ 9,8 g)
    assert body.hollow_saved_g == pytest.approx(61.09 - body.report.stats["mass_g"], rel=0.25)


SERVO = Path(__file__).parent / "fixtures" / "aile_4412_servo_extrados.step"


@pytest.mark.slow
def test_real_servo_wing_hollow_at_crossings():
    """Fixture servo (sans évidement : 111 plaques) évidée aux croisements : imprimable, baie intacte, plus légère."""
    cfg = load_config(overrides={"ribs_hollow": {"mode": "croisements"}})
    run = run_model(classify(read_step(SERVO), cfg), cfg)
    (body,) = run.bodies
    assert body.report.ok, [(i.z, i.message) for i in body.report.errors][:5]
    assert body.plates == 111 and len(body.bays) == 1
    assert body.hollow_saved_g > 0
