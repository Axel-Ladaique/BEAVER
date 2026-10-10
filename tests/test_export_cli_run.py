from pathlib import Path

import pytest
from build123d import Box, Location
from typer.testing import CliRunner

from aerorib import cli
from aerorib.cli import app
from aerorib.export import safe_name
from aerorib.orient import Orientation
from aerorib.pipeline import BodyResult, RunResult
from aerorib.validate import Report
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
    # décisions autonomes visibles : longeron Ø8 + jeu 2×0,15, passage Ø2,4 au BF (corde 100 → x = 30 − 70)
    assert "  trous reliés : SBAR_8 (longeron, Ø8.3) ; passage 1 (Ø2.4, x=" in r.output
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


def _fake_run(*args, **kwargs) -> RunResult:
    part = Box(10, 10, 10).solids()[0]
    stats = {"layers": 1.0, "height_mm": 10.0, "path_length_m": 0.0, "mass_g": 0.0}
    body = BodyResult("Aile_test", part, part, Report((), stats), (), 0)
    return RunResult(Orientation(Location(), (0.0, 0.0, 1.0), "axe forcé"), (body,))


def test_run_unwritable_output_is_clean_error(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(cli, "run_model", _fake_run)  # rapide : seule l'écriture est testée
    src = write_step(tmp_path / "aile.step", wing_x_bodies())
    blocker = tmp_path / "res"
    blocker.write_text("un fichier, pas un dossier", encoding="utf-8")
    r = runner.invoke(app, ["run", str(src), "--out", str(blocker)])
    assert r.exit_code == 1
    assert "erreur : écriture impossible" in r.output and "Traceback" not in r.output


@pytest.mark.parametrize("exc", [KeyError("x"), ZeroDivisionError("division by zero")])
def test_run_internal_error_is_clean(tmp_path: Path, monkeypatch, exc):
    def boom(*a, **k):
        raise exc

    monkeypatch.setattr(cli, "run_model", boom)
    src = write_step(tmp_path / "aile.step", wing_x_bodies())
    r = runner.invoke(app, ["run", str(src), "--out", str(tmp_path / "o")])
    assert r.exit_code == 1
    assert r.output.startswith("erreur : erreur interne : ") and type(exc).__name__ in r.output
    assert "merci de transmettre le fichier STEP" in r.output and "Traceback" not in r.output
