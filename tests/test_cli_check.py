from pathlib import Path

from typer.testing import CliRunner

from beaver.cli import app
from beaver.io_step import NamedBody, write_step
from beaver.testing.synthetic import broken_wing, naca_wing, ribbed_wing

runner = CliRunner()


def save(tmp_path: Path, name: str, part, body_name="SKIN_wing") -> Path:
    return write_step(tmp_path / name, [NamedBody((body_name,), part.solids()[0])])


def coarse_cfg(tmp_path: Path) -> Path:
    # couches de 10 mm pour garder le test rapide
    p = tmp_path / "c.yaml"
    p.write_text("material_settings: {layer_height: 10.0}\n", encoding="utf-8")
    return p


def test_check_good_with_original(tmp_path: Path):
    wing, _ = naca_wing()
    good = save(tmp_path, "good.step", ribbed_wing())
    orig = save(tmp_path, "orig.step", wing)
    r = runner.invoke(app, ["check", str(good), "--original", str(orig), "-c", str(coarse_cfg(tmp_path))])
    assert r.exit_code == 0, r.output
    assert "✔ imprimable en vase mode" in r.output


def test_check_broken_fails_and_writes_html(tmp_path: Path):
    bad = save(tmp_path, "bad.step", broken_wing("hole"))
    html = tmp_path / "rapport.html"
    r = runner.invoke(app, ["check", str(bad), "-c", str(coarse_cfg(tmp_path)), "--html", str(html)])
    assert r.exit_code == 1
    assert "✘ NON imprimable en vase mode" in r.output
    assert "contour non unique" in r.output
    assert html.exists() and "<svg" in html.read_text(encoding="utf-8")


def test_check_skin_damage_with_original(tmp_path: Path):
    wing, _ = naca_wing()
    bad = save(tmp_path, "bad.step", broken_wing("skin"))
    orig = save(tmp_path, "orig.step", wing)
    r = runner.invoke(app, ["check", str(bad), "--original", str(orig), "-c", str(coarse_cfg(tmp_path))])
    assert r.exit_code == 1
    assert "peau entamée" in r.output


def test_check_original_mismatch(tmp_path: Path):
    wing, _ = naca_wing()
    solid = wing.solids()[0]
    a = write_step(
        tmp_path / "a.step", [NamedBody(("SKIN_a",), solid), NamedBody(("SKIN_b",), solid)]
    )
    o = write_step(
        tmp_path / "o.step", [NamedBody(("SKIN_x",), solid), NamedBody(("SKIN_y",), solid)]
    )
    r = runner.invoke(app, ["check", str(a), "--original", str(o), "-c", str(coarse_cfg(tmp_path))])
    assert r.exit_code == 1
    assert "erreur : pas de peau d'origine pour SKIN_a" in r.output
