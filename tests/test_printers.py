"""Choix de l'imprimante (P1S / H2S) et de son gabarit Bambu."""
import json
import zipfile
from pathlib import Path

import pytest

from aerorib.bambu import read_template
from aerorib.classify import classify
from aerorib.config import TEMPLATES_DIR, load_config, resolve_template
from aerorib.export import export_results
from aerorib.io_step import read_step, write_step
from aerorib.pipeline import _print_volume, run_model
from aerorib.testing.synthetic import wing_x_bodies

FAST = {"material_settings": {"layer_height": 1.0}, "rib_layers": [{"spacing": 20}],
        "passages": {"mode": "link"}, "segment": {"max_height": 30}}


@pytest.mark.parametrize("printer", ["p1s", "h2s"])
@pytest.mark.parametrize("material", ["pla_aero", "asa_aero"])
def test_each_printer_material_resolves_its_template(printer, material):
    cfg = load_config(overrides={"printer": printer, "material": material})
    assert resolve_template(cfg) == TEMPLATES_DIR / f"{printer}_{material}.3mf"
    assert resolve_template(cfg).is_file()


def test_explicit_template_wins(tmp_path):
    (tmp_path / "t.3mf").write_bytes(b"x")
    c = tmp_path / "c.yaml"
    c.write_text("printer: h2s\nbambu: {template: t.3mf}\n", encoding="utf-8")
    assert resolve_template(load_config(c)) == (tmp_path / "t.3mf").resolve()


def test_embedded_profiles_have_no_hardcoded_template():
    assert load_config(overrides={"printer": "h2s"}).bambu.template == "auto"


def test_h2s_usable_volume_from_template():
    (w, d, h), warn = _print_volume(load_config(overrides={"printer": "h2s"}))
    assert warn is None and (w, d) == (340, 320) and h == 340
    assert h - load_config().segment.margin == 335
    assert read_template(resolve_template(load_config(overrides={"printer": "h2s"}))).bed == (340, 320)


def test_h2s_default_segment_height_is_template_minus_margin():
    from aerorib.pipeline import usable_height

    cfg = load_config(overrides={"printer": "h2s"})
    assert usable_height(cfg) == pytest.approx(340 - cfg.segment.margin)


def _write(tmp_path, overrides):
    cfg = load_config(overrides={**FAST, **overrides})
    src = write_step(tmp_path / "aile.step", wing_x_bodies())
    run = run_model(classify(read_step(src), cfg), cfg)
    out = tmp_path / "res"
    (path,) = [p for p in export_results(run, out, "aile", ["3mf"], read_template(resolve_template(cfg)))
               if p.suffix == ".3mf"]
    return path


def _members(path):
    with zipfile.ZipFile(path) as z:
        return {n: z.read(n) for n in z.namelist()}


def test_h2s_3mf_written_with_h2s_model_and_vase(tmp_path):
    members = _members(_write(tmp_path, {"printer": "h2s"}))
    settings = json.loads(members["Metadata/project_settings.config"])
    assert settings["printer_model"] == "Bambu Lab H2S"
    assert str(settings["spiral_mode"]) == "1"


def test_p1s_pla_unchanged_vs_explicit_template(tmp_path):
    """Gabarit « auto » = ce que donnait l'ancien chemin codé en dur du profil (contenu des membres du zip)."""
    (a_dir := tmp_path / "a").mkdir()
    (b_dir := tmp_path / "b").mkdir()
    a = _members(_write(a_dir, {}))
    explicit = str(Path(TEMPLATES_DIR / "p1s_pla_aero.3mf"))
    b = _members(_write(b_dir, {"bambu": {"template": explicit}}))
    assert a == b
