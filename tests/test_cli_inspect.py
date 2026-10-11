from pathlib import Path

from build123d import Box, Cylinder, Location
from typer.testing import CliRunner

from aerorib.cli import app
from aerorib.io_step import NamedBody, write_step

runner = CliRunner()


def step_with(tmp_path: Path, *bodies: NamedBody) -> Path:
    return write_step(tmp_path / "in.step", list(bodies))


def test_inspect_lists_roles_and_spar(tmp_path: Path):
    f = step_with(
        tmp_path,
        NamedBody(("SKIN_wing",), Box(150, 20, 200)),
        NamedBody(("SPAR_main",), Cylinder(7, 200).moved(Location((10, 0, 100)))),
    )
    r = runner.invoke(app, ["inspect", str(f)])
    assert r.exit_code == 0, r.output
    assert "skin    AERORIB/SKIN_wing" in r.output
    assert "spar    AERORIB/SPAR_main" in r.output
    assert "Ø 14.0 mm · axe (0, 0, 1) · longueur 200.0 mm" in r.output


def test_inspect_uses_config_roles(tmp_path: Path):
    f = step_with(
        tmp_path,
        NamedBody(("Body1",), Box(150, 20, 200)),
        NamedBody(("Body2",), Cylinder(7, 200)),
    )
    cfg = tmp_path / "c.yaml"
    cfg.write_text("roles: {Body2: spar}\n", encoding="utf-8")
    r = runner.invoke(app, ["inspect", str(f), "-c", str(cfg)])
    assert r.exit_code == 0, r.output
    assert "spar    AERORIB/Body2" in r.output
    assert "skin    AERORIB/Body1" in r.output


def test_inspect_no_skin_fails(tmp_path: Path):
    f = step_with(tmp_path, NamedBody(("Body1",), Box(1, 1, 1)), NamedBody(("Body2",), Box(1, 1, 1)))
    r = runner.invoke(app, ["inspect", str(f)])
    assert r.exit_code == 1
    assert "erreur : aucune peau trouvée" in r.output


def test_inspect_missing_file(tmp_path: Path):
    r = runner.invoke(app, ["inspect", str(tmp_path / "nope.step")])
    assert r.exit_code == 1
    assert "erreur : fichier introuvable" in r.output


def test_inspect_shows_servo_and_gaine(tmp_path: Path):
    f = step_with(
        tmp_path,
        NamedBody(("SKIN_wing",), Box(150, 20, 200)),
        NamedBody(("SERVO_1",), Box(32, 20, 46.5).moved(Location((40, 0, 60)))),
        NamedBody(("SBAR_CABLES",), Cylinder(5.75, 190).moved(Location((10, 0, 95)))),
        NamedBody(("SPAR_main",), Cylinder(7, 200).moved(Location((-30, 0, 100)))),
    )
    r = runner.invoke(app, ["inspect", str(f)])
    assert r.exit_code == 0, r.output
    assert "servo   AERORIB/SERVO_1" in r.output
    assert "boîte 32.0 × 20.0 × 46.5 mm" in r.output
    assert "gaine   AERORIB/SBAR_CABLES" in r.output
    assert "marqueur de gaine 'CABLE'" in r.output
    assert "spar    AERORIB/SPAR_main" in r.output
