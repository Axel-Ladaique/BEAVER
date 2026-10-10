from pathlib import Path

import pytest
from typer.testing import CliRunner

from aerorib.cli import app
from aerorib.config import ConfigError, load_config, resolve_template

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Ni ./profiles ni AERORIB_PROFILES de la machine ne doivent influencer les tests."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AERORIB_PROFILES", raising=False)


def user_profile(tmp_path: Path, monkeypatch, name: str, text: str) -> Path:
    d = tmp_path / "profiles"
    d.mkdir(exist_ok=True)
    monkeypatch.setenv("AERORIB_PROFILES", str(d))
    f = d / f"{name}.yaml"
    f.write_text(text, encoding="utf-8")
    return f


def test_pla_profile_values():
    cfg = load_config()
    assert cfg.preset.line_width == 0.42 and cfg.preset.min_slit_width == 0.10
    assert cfg.spars["default"]["sleeve"] == 0.8
    assert resolve_template(cfg).name == "p1s_pla_aero.3mf"


def test_asa_profile_overhang_and_no_template():
    cfg = load_config(overrides={"material": "asa_aero"})
    assert cfg.preset.max_overhang_deg == 40
    assert cfg.rib_angle(cfg.rib_layers[0]) == 40
    assert cfg.bambu.template is None


def test_asa_without_template_refused_clearly():
    with pytest.raises(ConfigError, match="pas de gabarit Bambu pour asa_aero : enregistrer un projet ASA Aero"):
        resolve_template(load_config(overrides={"material": "asa_aero"}))


def test_material_from_project_yaml_and_template_in_project(tmp_path):
    (tmp_path / "t.3mf").write_bytes(b"x")
    c = tmp_path / "c.yaml"
    c.write_text("material: asa_aero\nbambu: {template: t.3mf}\n", encoding="utf-8")
    cfg = load_config(c)
    assert cfg.preset.max_overhang_deg == 40
    assert resolve_template(cfg) == (tmp_path / "t.3mf").resolve()


def test_user_profile_wins_and_project_yaml_wins_over_it(tmp_path, monkeypatch):
    user_profile(tmp_path, monkeypatch, "pla_aero", "split: {gap: 0.9}\n")
    assert load_config().split.gap == 0.9
    c = tmp_path / "c.yaml"
    c.write_text("split: {gap: 0.7}\n", encoding="utf-8")
    assert load_config(c).split.gap == 0.7
    assert load_config(c, overrides={"split": {"gap": 0.65}}).split.gap == 0.65


def test_cwd_profiles_dir_used(tmp_path):
    d = tmp_path / "profiles"
    d.mkdir()
    (d / "pla_aero.yaml").write_text("split: {gap: 0.8}\n", encoding="utf-8")
    assert load_config().split.gap == 0.8


def test_user_profile_new_name(tmp_path, monkeypatch):
    user_profile(tmp_path, monkeypatch, "petg", "material_settings: {max_overhang_deg: 35}\n")
    assert load_config(overrides={"material": "petg"}).preset.max_overhang_deg == 35


def test_invalid_profile_names_the_file(tmp_path, monkeypatch):
    f = user_profile(tmp_path, monkeypatch, "pla_aero", "slit_width: 0.05\n")
    with pytest.raises(ConfigError) as e:
        load_config()
    assert str(f) in str(e.value) and "profil matière" in str(e.value)


def test_profile_unknown_key_names_the_file(tmp_path, monkeypatch):
    f = user_profile(tmp_path, monkeypatch, "pla_aero", "naming: {skin: [X]}\n")
    with pytest.raises(ConfigError, match="non autorisée") as e:
        load_config()
    assert str(f) in str(e.value)


def test_malformed_profile_names_the_file(tmp_path, monkeypatch):
    f = user_profile(tmp_path, monkeypatch, "pla_aero", "a: [1, 2\n")
    with pytest.raises(ConfigError, match="YAML mal formé") as e:
        load_config()
    assert str(f) in str(e.value)


def test_unknown_material_lists_profiles():
    with pytest.raises(ConfigError, match=r"profils disponibles : asa_aero, pla_aero"):
        load_config(overrides={"material": "pet_carbon"})


def test_cli_profils_lists_both():
    r = runner.invoke(app, ["profils"])
    assert r.exit_code == 0
    assert "pla_aero" in r.output and "asa_aero" in r.output
    assert "embarqué" in r.output and "aucun" in r.output and "p1s_pla_aero.3mf" in r.output


def test_cli_profils_copier(tmp_path):
    r = runner.invoke(app, ["profils", "--copier", "asa_aero"])
    assert r.exit_code == 0, r.output
    assert (tmp_path / "profiles" / "asa_aero.yaml").is_file()
    assert "utilisateur" in runner.invoke(app, ["profils"]).output
    assert load_config(overrides={"material": "asa_aero"}).preset.max_overhang_deg == 40
    again = runner.invoke(app, ["profils", "--copier", "asa_aero"])
    assert again.exit_code == 1 and "existe déjà" in again.output


def test_cli_copied_pla_keeps_working_template(tmp_path):
    assert runner.invoke(app, ["profils", "--copier", "pla_aero"]).exit_code == 0
    assert load_config() == load_config(Path(__file__).parents[1] / "examples" / "config-exemple.yaml")
    assert resolve_template(load_config()).is_file()


def test_cli_copier_unknown():
    r = runner.invoke(app, ["profils", "--copier", "nope"])
    assert r.exit_code == 1 and "embarqué" in r.output
