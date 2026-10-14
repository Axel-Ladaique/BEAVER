from pathlib import Path

import pytest

from beaver.config import Config, ConfigError, load_config


def test_defaults():
    cfg = load_config()
    assert cfg.material == "pla_aero"
    assert cfg.printer == "p1s"
    assert cfg.slit_width == 0.1
    assert cfg.naming.spar == ["SPAR", "SBAR", "Longeron"]
    assert cfg.preset.line_width == 0.42 and cfg.preset.max_overhang_deg == 45
    assert cfg.printer_spec.volume == (256, 256, 256)


def test_yaml_partial_nested_override(tmp_path: Path):
    p = tmp_path / "c.yaml"
    p.write_text("material: asa_aero\nnaming:\n  spar: [TUBE]\n", encoding="utf-8")
    cfg = load_config(p)
    assert cfg.material == "asa_aero"
    assert cfg.naming.spar == ["TUBE"]
    assert cfg.naming.skin == ["SKIN", "Wing", "Fuse", "Aile", "Peau"]  # défaut conservé


def test_overrides_win_over_yaml(tmp_path: Path):
    p = tmp_path / "c.yaml"
    p.write_text("slit_width: 0.2\nvalidation:\n  skin_tolerance: 0.1\n", encoding="utf-8")
    cfg = load_config(p, overrides={"slit_width": 0.15, "validation": {"max_mouth_width": 1.0}})
    assert cfg.slit_width == 0.15
    assert cfg.validation.skin_tolerance == 0.1
    assert cfg.validation.max_mouth_width == 1.0


def test_material_settings_patch_preset():
    cfg = load_config(overrides={"material_settings": {"layer_height": 0.25}})
    assert cfg.preset.layer_height == 0.25
    assert cfg.preset.line_width == 0.42


def test_unknown_material_setting_rejected():
    with pytest.raises(ConfigError, match="inconnu"):
        load_config(overrides={"material_settings": {"nope": 1.0}})


def test_unknown_key_rejected(tmp_path: Path):
    p = tmp_path / "c.yaml"
    p.write_text("slitwidth: 0.1\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(p)


def test_unknown_material_rejected():
    with pytest.raises(ConfigError, match="matière inconnue"):
        load_config(overrides={"material": "pet_carbon"})


def test_unknown_printer_rejected():
    with pytest.raises(ConfigError, match="imprimante inconnue"):
        load_config(overrides={"printer": "x9"})


def test_roles_mapping():
    cfg = load_config(overrides={"roles": {"Body3": "spar"}})
    assert cfg.roles == {"Body3": "spar"}
    with pytest.raises(ConfigError):
        load_config(overrides={"roles": {"Body3": "wing"}})


def test_yaml_not_a_mapping(tmp_path: Path):
    p = tmp_path / "c.yaml"
    p.write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="dictionnaire"):
        load_config(p)


def test_config_is_immutable():
    cfg = Config()
    with pytest.raises(Exception):
        cfg.slit_width = 0.3


def test_malformed_yaml(tmp_path: Path):
    p = tmp_path / "c.yaml"
    p.write_text("a: [1, 2\nb: : :\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="YAML mal formé"):
        load_config(p)


def test_non_utf8_yaml(tmp_path: Path):
    p = tmp_path / "c.yaml"
    p.write_bytes("naming:\n  skin: [Aile_légère]\n".encode("cp1252"))
    with pytest.raises(ConfigError, match="UTF-8"):
        load_config(p)


def test_missing_or_directory_config(tmp_path: Path):
    with pytest.raises(ConfigError, match="introuvable / illisible"):
        load_config(tmp_path / "nope.yaml")
    with pytest.raises(ConfigError, match="introuvable / illisible"):
        load_config(tmp_path)


@pytest.mark.parametrize(
    "overrides",
    [
        {"material_settings": {"layer_height": 0}},
        {"material_settings": {"max_overhang_deg": 120}},
        {"slit_width": -1},
        {"slit_width": 0},
        {"validation": {"skin_tolerance": 0}},
        {"validation": {"max_mouth_width": -0.1}},
        {"validation": {"tessellation_tol": 0}},
    ],
)
def test_non_positive_values_rejected(overrides):
    with pytest.raises(ConfigError):
        load_config(overrides=overrides)


def test_blank_naming_prefix_rejected():
    with pytest.raises(ConfigError, match="vide"):
        load_config(overrides={"naming": {"skin": [""]}})
    with pytest.raises(ConfigError, match="vide"):
        load_config(overrides={"naming": {"spar": ["  "]}})


def test_gabarit_avec_octet_nul_donne_config_error(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text('bambu:\n  template: "a\\0b"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="gabarit"):
        load_config(p)


def test_naming_servo_and_cable_defaults():
    cfg = load_config()
    assert cfg.naming.servo == ["SERVO"]
    assert cfg.naming.cable_markers == ["CABLE"]
    with pytest.raises(ConfigError):
        load_config(overrides={"naming": {"servo": [""]}})
    with pytest.raises(ConfigError):
        load_config(overrides={"naming": {"cable_markers": [" "]}})
    assert load_config(overrides={"roles": {"Baie": "servo"}}).roles == {"Baie": "servo"}
