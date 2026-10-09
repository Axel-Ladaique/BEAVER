from pathlib import Path

import pytest

from aerorib.config import MATERIALS, Config, ConfigError, load_config


def test_defaults():
    cfg = load_config()
    assert cfg.material == "pla_aero"
    assert cfg.printer == "p1s"
    assert cfg.slit_width == 0.1
    assert cfg.naming.spar == ["SPAR", "SBAR", "Longeron"]
    assert cfg.preset == MATERIALS["pla_aero"]
    assert cfg.printer_spec.volume == (256, 256, 256)


def test_yaml_partial_nested_override(tmp_path: Path):
    p = tmp_path / "c.yaml"
    p.write_text("material: asa_aero\nnaming:\n  spar: [TUBE]\n", encoding="utf-8")
    cfg = load_config(p)
    assert cfg.material == "asa_aero"
    assert cfg.naming.spar == ["TUBE"]
    assert cfg.naming.skin == ["SKIN", "Wing", "Fuse"]  # défaut conservé


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
    assert cfg.preset.line_width == MATERIALS["pla_aero"].line_width


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
