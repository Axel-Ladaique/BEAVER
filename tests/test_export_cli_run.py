from pathlib import Path

import pytest
from build123d import Box, Location
from typer.testing import CliRunner

from aerorib import cli
from aerorib.cli import app
from aerorib.export import safe_name
from aerorib.features.bores import Bore, Passage
from aerorib.orient import Orientation
from aerorib.pipeline import BodyResult, RunResult
from aerorib.validate import Report
from aerorib.io_step import read_step, write_step
from aerorib.testing.synthetic import wing_x_bodies

runner = CliRunner()


def fast_cfg(tmp_path: Path, extra: str = "") -> Path:
    p = tmp_path / "c.yaml"
    p.write_text("material_settings: {layer_height: 1.0}\nrib_layers: [{spacing: 20}]\n" + extra, encoding="utf-8")
    return p


def test_safe_name():
    assert safe_name("Aile gauche/v2:é") == "Aile_gauche_v2_é"


def test_run_writes_outputs(tmp_path: Path):
    src = write_step(tmp_path / "aile.step", wing_x_bodies())
    out = tmp_path / "res"
    cfg = fast_cfg(tmp_path, "passages: {mode: link}\n")
    r = runner.invoke(app, ["run", str(src), "-c", str(cfg), "--out", str(out)])
    assert r.exit_code == 0, r.output
    assert "orientation : longeron SBAR_8" in r.output
    assert "✔ imprimable en vase mode" in r.output
    # décisions autonomes visibles : longeron Ø8 + jeu 2×0,15, passage Ø2,4 au BF (corde 100 → x = 30 − 70)
    assert "  trous : SBAR_8 (longeron, Ø8.3, attache intrados) ; passage 1 (Ø2.4, x=" in r.output
    for name in ("aile.step", "aile.stl", "aile-rapport.html"):
        assert (out / name).exists(), name
    (body,) = read_step(out / "aile.step")
    assert body.name == "Aile_test"
    bb = body.solid.bounding_box()
    assert abs(bb.min.Z) < 1e-3  # exporté dans le repère d'impression


def test_run_lists_filled_bores_by_default(tmp_path: Path):
    src = write_step(tmp_path / "aile.step", wing_x_bodies())
    r = runner.invoke(app, ["run", str(src), "-c", str(fast_cfg(tmp_path)), "--out", str(tmp_path / "res")])
    assert r.exit_code == 0, r.output
    assert "1 rebouché(s)" in r.output
    line = next(ln for ln in r.output.splitlines() if ln.startswith("  trous : "))
    assert line.startswith("  trous : SBAR_8 (longeron, Ø8.3, attache intrados) ; rebouchés : 1 × Ø2.4 (x=")
    assert "passage" not in line


def test_format_bores_groups_filled_by_diameter():
    spar = Bore("SBAR_14mm_1", 0.0, 0.0, 7.15, 0.8, 2.0, -1, True, 0.0, 210.0, 0.15, 0.15, 1.0)
    bare = Bore("SBAR_2", 5.0, 0.0, 2.15, 0.8, 2.0, 1, True, 0.0, 210.0, 0.15, 0.15, 0.0)
    filled = [Passage(61.54, 1.06, 1.2, 0.0, 210.0, 0.0), Passage(-40.0, -2.0, 1.2, 0.0, 210.0, 0.0),
              Passage(10.0, 0.0, 0.5, 0.0, 210.0, 0.0)]
    assert cli.format_bores([spar, bare], filled) == (
        "  trous : SBAR_14mm_1 (longeron, Ø14.3, attache extrados) ; SBAR_2 (longeron, Ø4.3) ; "
        "rebouchés : 2 × Ø2.4 (x=61.5, y=1.1 / x=-40.0, y=-2.0) ; 1 × Ø1.0 (x=10.0, y=0.0)"
    )
    assert cli.format_bores([], []) == "  trous : aucun"
    assert cli.format_bores([], filled[:1]) == "  trous : rebouchés : 1 × Ø2.4 (x=61.5, y=1.1)"


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
