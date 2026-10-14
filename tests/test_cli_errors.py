import subprocess
import sys
from pathlib import Path

import pytest
from build123d import Box
from typer.testing import CliRunner

from beaver.cli import app
from beaver.io_step import NamedBody, write_step

runner = CliRunner()


@pytest.fixture
def step(tmp_path: Path) -> Path:
    return write_step(tmp_path / "in.step", [NamedBody(("SKIN_a",), Box(10, 10, 10))])


def _cfg(tmp_path: Path, content: str | bytes) -> Path:
    p = tmp_path / "c.yaml"
    p.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
    return p


def _assert_french_error(r):
    assert r.exit_code == 1
    assert r.output.startswith("erreur : ")
    assert "Traceback" not in r.output


@pytest.mark.parametrize(
    "content",
    [
        "a: [1, 2\nb: : :\n",
        "naming:\n  skin: [Aile_légère]\n".encode("cp1252"),
        "material_settings: {layer_height: 0}\n",
        "slit_width: -1\n",
        'naming: {skin: [""]}\n',
    ],
    ids=["yaml-malformed", "cp1252", "layer-height-0", "slit-negative", "blank-prefix"],
)
def test_bad_config_is_clean_error(tmp_path: Path, step: Path, content):
    r = runner.invoke(app, ["inspect", str(step), "-c", str(_cfg(tmp_path, content))])
    _assert_french_error(r)


def test_missing_config_french_message(tmp_path: Path, step: Path):
    r = runner.invoke(app, ["inspect", str(step), "-c", str(tmp_path / "nope.yaml")])
    _assert_french_error(r)
    assert "introuvable / illisible" in r.output


def test_inspect_directory(tmp_path: Path):
    r = runner.invoke(app, ["inspect", str(tmp_path)])
    _assert_french_error(r)
    assert "lecture impossible" in r.output


def test_check_directory(tmp_path: Path):
    r = runner.invoke(app, ["check", str(tmp_path)])
    _assert_french_error(r)


def test_stdout_utf8_when_piped(tmp_path: Path, step: Path):
    """Sous Windows, stdout redirigé est en cp1252 : on force UTF-8 (✔ doit sortir)."""
    r = subprocess.run(
        [sys.executable, "-m", "beaver", "check", str(step)],
        capture_output=True,
        env={**__import__("os").environ, "PYTHONIOENCODING": "cp1252"},
    )
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    assert "✔" in r.stdout.decode("utf-8")
