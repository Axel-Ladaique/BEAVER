import subprocess
import zipfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from beaver.cli import app
from beaver.io_step import write_step
from beaver.testing.synthetic import wing_x_bodies

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


def test_open_skipped_when_not_printable(tmp_path, monkeypatch):
    from beaver.pipeline import RunResult

    calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: calls.append(a[0]))
    monkeypatch.setattr(RunResult, "ok", property(lambda self: False))
    src = write_step(tmp_path / "aile.step", wing_x_bodies())
    out = tmp_path / "res"
    r = runner.invoke(app, ["run", str(src), "-c", str(_cfg(tmp_path)), "--out", str(out), "--open"])
    assert r.exit_code == 1, r.output
    assert calls == []
    assert "projet non imprimable : non ouvert dans Bambu Studio" in r.output
    assert (out / "aile.3mf").exists()


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
    cfg.write_text("material: asa_aero\nbambu: {template: null}\n", encoding="utf-8")
    r = runner.invoke(app, ["run", str(src), "-c", str(cfg), "--3mf"])
    assert r.exit_code == 1 and "pas de gabarit Bambu" in r.output
    assert "…" not in r.output  # échec avant le pipeline


# --- open_in_bambu : branches par plateforme (sans lancer de vrai processus) ---
from types import SimpleNamespace  # noqa: E402

from beaver import bambu  # noqa: E402


def _platform(monkeypatch, name):
    monkeypatch.setattr(bambu, "sys", SimpleNamespace(platform=name))


def test_open_windows_uses_program_files_exe(tmp_path, monkeypatch):
    exe = tmp_path / "Bambu Studio" / "bambu-studio.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"")
    _platform(monkeypatch, "win32")
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    calls, started = [], []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: calls.append(a[0]))
    monkeypatch.setattr(bambu.os, "startfile", started.append, raising=False)
    bambu.open_in_bambu(tmp_path / "p.3mf")
    assert calls == [[str(exe), str(tmp_path / "p.3mf")]] and started == []


def test_open_windows_falls_back_to_startfile(tmp_path, monkeypatch):
    _platform(monkeypatch, "win32")
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "absent"))
    calls, started = [], []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: calls.append(a[0]))
    monkeypatch.setattr(bambu.os, "startfile", started.append, raising=False)
    bambu.open_in_bambu(tmp_path / "p.3mf")
    assert calls == [] and started == [str(tmp_path / "p.3mf")]


def test_open_windows_startfile_error_is_french(tmp_path, monkeypatch):
    _platform(monkeypatch, "win32")
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "absent"))

    def boom(_):
        raise OSError(1155, "Aucune application associée")

    monkeypatch.setattr(bambu.os, "startfile", boom, raising=False)
    with pytest.raises(bambu.BambuError, match="ouverture de Bambu Studio impossible"):
        bambu.open_in_bambu(tmp_path / "p.3mf")


def test_open_linux_uses_xdg_open(tmp_path, monkeypatch):
    _platform(monkeypatch, "linux")
    calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: calls.append(a[0]))
    bambu.open_in_bambu(tmp_path / "p.3mf")
    assert calls == [["xdg-open", str(tmp_path / "p.3mf")]]


def test_open_linux_missing_xdg_open_is_french(tmp_path, monkeypatch):
    _platform(monkeypatch, "linux")

    def boom(*a, **k):
        raise FileNotFoundError(2, "No such file or directory")

    monkeypatch.setattr(subprocess, "Popen", boom)
    with pytest.raises(bambu.BambuError, match="ouvrez .* à la main"):
        bambu.open_in_bambu(tmp_path / "p.3mf")


@pytest.mark.parametrize("installed, expected", [(True, ["open", "-a", "BambuStudio"]), (False, ["open"])])
def test_open_macos_app_or_default(tmp_path, monkeypatch, installed, expected):
    _platform(monkeypatch, "darwin")
    real_exists = Path.exists
    monkeypatch.setattr(
        Path, "exists", lambda self: installed if str(self) == "/Applications/BambuStudio.app" else real_exists(self)
    )
    calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: calls.append(a[0]))
    bambu.open_in_bambu(tmp_path / "p.3mf")
    assert calls == [[*expected, str(tmp_path / "p.3mf")]]


def test_export_bambu_error_is_clean(tmp_path, monkeypatch):
    from beaver import export

    def boom(*a, **k):
        raise bambu.BambuError("projet impossible à écrire")

    monkeypatch.setattr(export, "write_project", boom)
    src = write_step(tmp_path / "aile.step", wing_x_bodies())
    r = runner.invoke(app, ["run", str(src), "-c", str(_cfg(tmp_path)), "--out", str(tmp_path / "r"), "--3mf"])
    assert r.exit_code == 1 and "erreur : projet impossible à écrire" in r.output
    assert "erreur interne" not in r.output
