from pathlib import Path

from typer.testing import CliRunner

from aerorib.cli import app
from aerorib.export import safe_name
from aerorib.io_step import read_step, write_step
from aerorib.testing.synthetic import wing_x_bodies

runner = CliRunner()


def fast_cfg(tmp_path: Path) -> Path:
    p = tmp_path / "c.yaml"
    p.write_text("material_settings: {layer_height: 1.0}\nrib_layers: [{spacing: 20}]\n", encoding="utf-8")
    return p


def test_safe_name():
    assert safe_name("Aile gauche/v2:é") == "Aile_gauche_v2_é"


def test_run_writes_outputs(tmp_path: Path):
    src = write_step(tmp_path / "aile.step", wing_x_bodies())
    out = tmp_path / "res"
    r = runner.invoke(app, ["run", str(src), "-c", str(fast_cfg(tmp_path)), "--out", str(out)])
    assert r.exit_code == 0, r.output
    assert "orientation : longeron SBAR_8" in r.output
    assert "✔ imprimable en vase mode" in r.output
    for name in ("aile.step", "aile.stl", "aile-rapport.html"):
        assert (out / name).exists(), name
    (body,) = read_step(out / "aile.step")
    assert body.name == "Aile_test"
    bb = body.solid.bounding_box()
    assert abs(bb.min.Z) < 1e-3  # exporté dans le repère d'impression


def test_check_orient_on_raw_wing(tmp_path: Path):
    src = write_step(tmp_path / "brute.step", wing_x_bodies())
    cfg = fast_cfg(tmp_path)
    r = runner.invoke(app, ["check", str(src), "-c", str(cfg), "--orient"])
    # pièce brute orientée : l'alésage du longeron et le passage sont des trous isolés
    assert r.exit_code == 1
    assert "trou isolé" in r.output
    assert "hauteur : 80.0 mm" in r.output
