"""Choix de l'imprimante (P1S / H2S) et de son gabarit Bambu."""
import json
import zipfile
from pathlib import Path

import pytest

from beaver.bambu import read_template
from beaver.classify import classify
from beaver.config import TEMPLATES_DIR, load_config, resolve_template
from beaver.export import export_results
from beaver.io_step import read_step, write_step
from beaver.pipeline import _print_volume, run_model
from beaver.testing.synthetic import wing_x_bodies

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
    (w, d, h), warn, _ = _print_volume(load_config(overrides={"printer": "h2s"}))
    assert warn is None and (w, d) == (340, 320) and h == 340
    assert read_template(resolve_template(load_config(overrides={"printer": "h2s"}))).bed == (340, 320)


def test_h2s_default_segment_height_is_template_minus_margin():
    from beaver.pipeline import usable_height

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
    assert settings["bottom_shell_layers"] == "0"
    assert settings["brim_type"] == "outer_only" and settings["brim_width"] == "5"


@pytest.mark.slow  # ≈ 13 s : deux générations complètes
def test_p1s_pla_unchanged_vs_explicit_template(tmp_path):
    """Gabarit « auto » = ce que donnait l'ancien chemin codé en dur du profil (contenu des membres du zip)."""
    (a_dir := tmp_path / "a").mkdir()
    (b_dir := tmp_path / "b").mkdir()
    a = _members(_write(a_dir, {}))
    explicit = str(Path(TEMPLATES_DIR / "p1s_pla_aero.3mf"))
    b = _members(_write(b_dir, {"bambu": {"template": explicit}}))
    assert a == b


def test_unknown_printer_message():
    from beaver.config import ConfigError

    with pytest.raises(ConfigError, match="imprimante inconnue 'x9' \\(connues : h2s, p1s\\)"):
        load_config(overrides={"printer": "x9"})


def test_pinned_template_printer_mismatch_warns():
    p1s = str(TEMPLATES_DIR / "p1s_pla_aero.3mf")
    cfg = load_config(overrides={"printer": "h2s", "bambu": {"template": p1s}})
    assert resolve_template(cfg).name == "p1s_pla_aero.3mf"  # l'explicite l'emporte
    vol, warn, _ = _print_volume(cfg)
    assert vol[:2] == (256, 256)
    assert warn == ("gabarit p1s_pla_aero.3mf (Bambu Lab P1S) ≠ imprimante choisie h2s\u00a0: "
                    "le volume et le projet suivent le gabarit")
    assert _print_volume(load_config(overrides={"printer": "p1s", "bambu": {"template": p1s}}))[1] is None


# --- gabarit épinglé ≠ imprimante : libellés et aperçu (revue finale I1) ------------------------------------------

P1S_TEMPLATE = str(TEMPLATES_DIR / "p1s_pla_aero.3mf")
MISMATCH = "gabarit p1s_pla_aero.3mf (Bambu Lab P1S) ≠ imprimante choisie h2s"


def _pinned(**segment):
    return load_config(overrides={"printer": "h2s", "bambu": {"template": P1S_TEMPLATE},
                                  "orientation": {"axis": [0, 0, 1]}, "segment": segment})


def test_print_volume_label_comes_from_the_template():
    assert _print_volume(_pinned()).label == "P1S"
    assert _print_volume(load_config(overrides={"printer": "h2s"})).label == "H2S"
    assert _print_volume(load_config()).label == "P1S"


def test_notices_name_the_template_printer():
    from beaver.pipeline import body_notice, segment_part
    from beaver.testing.synthetic import swept_wing

    seg = segment_part("B", swept_wing(chord=400, span=60), _pinned(), lambda m: None)
    (n,) = seg.notices
    assert "plateau P1S (256 × 256 mm)" in n and "H2S" not in n
    assert any(MISMATCH in w for w in seg.warnings)
    assert "(P1S)" in body_notice(2, _pinned())


@pytest.mark.parametrize("mode", ["auto", "off"])
def test_preview_shows_template_mismatch(tmp_path, mode):
    from beaver import preview
    from beaver.io_step import NamedBody
    from beaver.testing.synthetic import swept_wing

    step = write_step(tmp_path / "large.step", [NamedBody(("Aile_large",), swept_wing(chord=400, span=60))])
    cfg = _pinned(mode=mode)
    prep = preview.prepare(step, cfg)
    view = preview.planform(prep, [], cfg)
    assert any(w.startswith(MISMATCH) for w in view.warnings), view.warnings
    if mode == "auto":
        assert any("plateau P1S (256 × 256 mm)" in n for n in view.notices)
    else:  # même avertissement « trop grand » que la génération
        assert any("dépasse le volume d'impression" in w and "segment.mode: off" in w for w in view.warnings)
