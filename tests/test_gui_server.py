from fastapi.testclient import TestClient
from typer.testing import CliRunner

from aerorib import __version__
from aerorib.cli import app
from aerorib.gui import server
from aerorib.gui.server import create_app

TOKEN = "jeton-test"


def _client(tmp_path):
    return TestClient(create_app(tmp_path, TOKEN))


def test_sante_avec_jeton_en_tete(tmp_path):
    r = _client(tmp_path).get("/api/sante", headers={"X-AeroRib-Token": TOKEN})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "version": __version__}


def test_sante_avec_jeton_parametre(tmp_path):
    assert _client(tmp_path).get("/api/sante", params={"token": TOKEN}).status_code == 200


def test_sante_sans_jeton_ou_mauvais(tmp_path):
    c = _client(tmp_path)
    for r in (c.get("/api/sante"), c.get("/api/sante", headers={"X-AeroRib-Token": "x"})):
        assert r.status_code == 401
        assert r.json() == {"erreur": "jeton manquant ou invalide"}


def test_racine_sert_html(tmp_path):
    r = _client(tmp_path).get("/")
    assert r.status_code == 200
    assert "AeroRib" in r.text


def test_config_uvicorn_127(tmp_path):
    cfg = server.build_uvicorn_config(create_app(tmp_path, TOKEN), 5555)
    assert cfg.host == "127.0.0.1"
    assert cfg.port == 5555


def test_run_gui(tmp_path, monkeypatch):
    opened, ran = [], {}
    monkeypatch.setattr(server.webbrowser, "open", lambda u: opened.append(u))
    monkeypatch.setattr(server.uvicorn, "run", lambda app, **kw: ran.update(kw))
    server.run_gui(tmp_path, port=0, open_browser=True)
    assert ran["host"] == "127.0.0.1" and ran["port"] > 0
    assert opened[0].startswith(f"http://127.0.0.1:{ran['port']}/?token=")


def test_run_gui_sans_navigateur(tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr(server.webbrowser, "open", lambda u: opened.append(u))
    monkeypatch.setattr(server.uvicorn, "run", lambda app, **kw: None)
    server.run_gui(tmp_path, port=0, open_browser=False)
    assert opened == []


def test_commande_gui_help():
    r = CliRunner().invoke(app, ["gui", "--help"])
    assert r.exit_code == 0
    assert "--dossier" in r.output and "--sans-navigateur" in r.output


def test_commande_gui_appelle_run_gui(tmp_path, monkeypatch):
    calls = {}
    monkeypatch.setattr(
        "aerorib.gui.server.run_gui", lambda root, port, open_browser: calls.update(r=root, p=port, o=open_browser)
    )
    r = CliRunner().invoke(app, ["gui", "--dossier", str(tmp_path), "--port", "1234", "--sans-navigateur"])
    assert r.exit_code == 0
    assert calls == {"r": tmp_path, "p": 1234, "o": False}
