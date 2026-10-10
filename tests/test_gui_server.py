import socket
import time

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from aerorib import __version__
from aerorib.cli import app
from aerorib.gui import server
from aerorib.gui.server import create_app

TOKEN = "jeton-test"


def _client(tmp_path):
    return TestClient(create_app(tmp_path, TOKEN), base_url="http://127.0.0.1")


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
    cfg = server.build_uvicorn_config(create_app(tmp_path, TOKEN))
    assert cfg.host == "127.0.0.1"


def _fake_server(monkeypatch, record):
    class Fake:
        def __init__(self, config):
            record["config"] = config
            self.started = False
            self.should_exit = False

        def run(self, sockets=None):
            record["sockets"] = sockets
            self.started = True
            time.sleep(0.3)  # laisse le fil d'ouverture réagir
            record["opened_at_run_end"] = list(record["opened"])
            sockets[0].close()

    monkeypatch.setattr(server.uvicorn, "Server", Fake)


def test_run_gui_socket_unique_et_navigateur_apres_demarrage(tmp_path, monkeypatch):
    record = {"opened": []}
    monkeypatch.setattr(server.webbrowser, "open", lambda u: record["opened"].append(u))
    _fake_server(monkeypatch, record)
    server.run_gui(tmp_path, port=0, open_browser=True)
    assert record["config"].host == "127.0.0.1"
    port = record["sockets"][0].family and record["opened"][0].split(":")[2].split("/")[0]
    assert record["opened_at_run_end"][0].startswith(f"http://127.0.0.1:{port}/?token=")


def test_run_gui_sans_navigateur(tmp_path, monkeypatch):
    record = {"opened": []}
    monkeypatch.setattr(server.webbrowser, "open", lambda u: record["opened"].append(u))
    _fake_server(monkeypatch, record)
    server.run_gui(tmp_path, port=0, open_browser=False)
    assert record["opened"] == []


def test_port_occupe(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(server.uvicorn, "Server", lambda c: called.append(c))
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen(1)
        port = busy.getsockname()[1]
        r = CliRunner().invoke(app, ["gui", "--dossier", str(tmp_path), "--port", str(port)])
    assert r.exit_code == 1
    assert f"port {port} déjà utilisé" in r.output
    assert called == []


def test_route_api_ajoutee_protegee(tmp_path):
    a = create_app(tmp_path, TOKEN)

    def autre():
        return {"x": 1}

    a.add_api_route("/api/autre", autre)
    a.router.routes.insert(0, a.router.routes.pop())  # avant le montage statique

    c = TestClient(a, base_url="http://127.0.0.1")
    assert c.get("/api/autre").status_code == 401
    assert c.get("/api/autre", headers={"X-AeroRib-Token": TOKEN}).status_code == 200


def test_api_inconnue_sans_jeton(tmp_path):
    assert _client(tmp_path).get("/api/inconnu").status_code == 401


def test_jeton_faux_en_tete_et_parametre(tmp_path):
    c = _client(tmp_path)
    assert c.get("/api/sante", params={"token": "faux"}).status_code == 401
    assert c.get("/api/sante", headers={"X-AeroRib-Token": "faux"}).status_code == 401


def test_index_sans_jeton_et_referrer(tmp_path):
    html = _client(tmp_path).get("/", params={"token": TOKEN}).text
    assert TOKEN not in html
    assert 'name="referrer" content="no-referrer"' in html


def test_hote_inconnu_refuse(tmp_path):
    c = TestClient(create_app(tmp_path, TOKEN), base_url="http://evil.example")
    assert c.get("/").status_code == 400


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
