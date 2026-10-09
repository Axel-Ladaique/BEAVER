from typer.testing import CliRunner

from aerorib.cli import app


def test_version_flag_prints_version():
    result = CliRunner().invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "aerorib 0.1.0" in result.output
