import subprocess
import zipfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from aerorib.cli import app
from aerorib.io_step import write_step
from aerorib.testing.synthetic import wing_x_bodies

runner = CliRunner()
FIXTURE = Path(__file__).parent / "fixtures" / "bambu_p1s_pla_aero.3mf"


def _cfg(tmp_path: Path, template=FIXTURE, extra="") -> Path:
    p = tmp_path / "c.yaml"
    p.write_text(
        "material_settings: {layer_height: 1.0}\nrib_layers: [{spacing: 20}]\npassages: {mode: link}\n"
        f"segment: {{max_height: 30}}\nbambu: {{template: '{template}'}}\n" + extra,
        encoding="utf-8",
    )
    return p


@pytest.fixture(scope="module")
def run_out(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("cli3mf")
    src = write_step(tmp / "aile.step", wing_x_bodies())
    out = tmp / "res"
    r = runner.invoke(app, ["run", str(src), "-c", str(_cfg(tmp)), "--out", str(out), "--3mf"])
    return r, out


def test_run_3mf(run_out):
    r, out = run_out
    assert r.exit_code == 0, r.output
    assert "tronçons : 3" in r.output and "→ plateaux 1 à 3" in r.output
    with zipfile.ZipFile(out / "aile.3mf") as z:
        assert "Metadata/model_settings.config" in z.namelist()
        assert z.read("Metadata/model_settings.config").count(b"<plate>") == 3


def test_open_calls_popen_once(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: calls.append(a[0]))
    src = write_step(tmp_path / "aile.step", wing_x_bodies())
    out = tmp_path / "res"
    r = runner.invoke(app, ["run", str(src), "-c", str(_cfg(tmp_path)), "--out", str(out), "--open"])
    assert r.exit_code == 0, r.output
    assert (out / "aile.3mf").exists()
    assert len(calls) == 1 and str(out / "aile.3mf") in calls[0]


def test_open_failure_does_not_fail_run(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError(2, "No such file")
    monkeypatch.setattr(subprocess, "Popen", boom)
    src = write_step(tmp_path / "aile.step", wing_x_bodies())
    r = runner.invoke(app, ["run", str(src), "-c", str(_cfg(tmp_path)), "--out", str(tmp_path / "r"), "--open"])
    assert r.exit_code == 0, r.output
    assert "ouverture de Bambu Studio impossible" in r.output


def test_invalid_template(tmp_path):
    bad = tmp_path / "bad.3mf"
    bad.write_bytes(b"pas un zip")
    src = write_step(tmp_path / "aile.step", wing_x_bodies())
    r = runner.invoke(app, ["run", str(src), "-c", str(_cfg(tmp_path, bad)), "--3mf"])
    assert r.exit_code == 1 and r.output.startswith("erreur : ")


def test_no_template_for_asa(tmp_path):
    src = write_step(tmp_path / "aile.step", wing_x_bodies())
    cfg = tmp_path / "c.yaml"
    cfg.write_text("material: asa_aero\n", encoding="utf-8")
    r = runner.invoke(app, ["run", str(src), "-c", str(cfg), "--3mf"])
    assert r.exit_code == 1 and "pas de gabarit Bambu" in r.output
    assert "…" not in r.output  # échec avant le pipeline
