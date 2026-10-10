import pytest
import yaml
from fastapi.testclient import TestClient

from aerorib.gui import projects
from aerorib.gui.projects import ProjectError, ProjectStore
from aerorib.gui.server import create_app
from aerorib.io_step import write_step
from aerorib.testing.synthetic import wing_x_bodies

TOKEN = "jeton-test"
H = {"X-AeroRib-Token": TOKEN}


@pytest.fixture(scope="module")
def step_bytes(tmp_path_factory):
    p = tmp_path_factory.mktemp("step") / "aile.step"
    write_step(p, wing_x_bodies())
    return p.read_bytes()


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(tmp_path / "projets", TOKEN), base_url="http://127.0.0.1")


def _creer(client, step_bytes, nom="aile1", filename="aile.step"):
    return client.post("/api/projets", headers=H, data={"nom": nom}, files={"fichier": (filename, step_bytes)})


def test_creation_et_liste(client, step_bytes):
    r = _creer(client, step_bytes)
    assert r.status_code == 201, r.text
    assert r.json()["nom"] == "aile1" and r.json()["nb_essais"] == 0
    assert [p["nom"] for p in client.get("/api/projets", headers=H).json()] == ["aile1"]
    assert client.get("/api/projets/aile1", headers=H).json()["piece"] == "piece.step"
    assert client.get("/api/projets/aile1/config", headers=H).json()["donnees"] == {}


@pytest.mark.parametrize("nom", ["../x", "a/b", "..", ".cache", "a\\b", "", "con", "x."])
def test_nom_invalide_refuse(client, step_bytes, nom):
    r = _creer(client, step_bytes, nom=nom)
    assert r.status_code in (404, 422)
    assert client.get("/api/projets", headers=H).json() == []


def test_doublon_refuse(client, step_bytes):
    assert _creer(client, step_bytes).status_code == 201
    r = _creer(client, step_bytes)
    assert r.status_code == 409 and "existe déjà" in r.json()["erreur"]


def test_fichier_trop_gros(client, step_bytes, monkeypatch):
    monkeypatch.setattr(projects, "MAX_STEP_BYTES", 1000)
    r = _creer(client, step_bytes)
    assert r.status_code == 413
    assert r.json()["erreur"] == "fichier trop volumineux (max 200 Mo)"
    assert client.get("/api/projets", headers=H).json() == []


def test_extension_et_contenu_refuses(client):
    r = _creer(client, b"ISO-10303-21;", filename="a.txt")
    assert r.status_code == 422 and ".step" in r.json()["erreur"]
    r = _creer(client, b"pas du step du tout", filename="a.step")
    assert r.status_code == 422 and "pas un STEP" in r.json()["erreur"]
    assert client.get("/api/projets", headers=H).json() == []


def test_inspection(client, step_bytes):
    _creer(client, step_bytes)
    r = client.get("/api/projets/aile1/corps", headers=H)
    assert r.status_code == 200, r.text
    corps = r.json()["corps"]
    assert sorted(c["role"] for c in corps) == ["skin", "spar"]
    spar = next(c for c in corps if c["role"] == "spar")
    assert spar["longeron"]["diametre"] == pytest.approx(8.0)
    assert r.json()["diametre_longeron"] == pytest.approx(8.0)


def test_config_invalide_422_fichier_inchange(client, step_bytes, tmp_path):
    _creer(client, step_bytes)
    cfg = tmp_path / "projets" / "aile1" / "config.yaml"
    avant = cfg.read_text()
    for corps in ({"donnees": {"slit_width": -1}}, {"donnees": {"inconnu": 1}}, {"texte": "a: [b"}):
        r = client.put("/api/projets/aile1/config", headers=H, json=corps)
        assert r.status_code == 422
        assert "invalide" in r.json()["erreur"] or "mal formé" in r.json()["erreur"]
        assert cfg.read_text() == avant
    assert not list(cfg.parent.glob(".*tmp"))


def test_config_valide_ecrite(client, step_bytes, tmp_path):
    _creer(client, step_bytes)
    r = client.put("/api/projets/aile1/config", headers=H, json={"donnees": {"slit_width": 0.2}})
    assert r.status_code == 200 and r.json()["donnees"] == {"slit_width": 0.2}
    assert yaml.safe_load((tmp_path / "projets/aile1/config.yaml").read_text()) == {"slit_width": 0.2}


def test_provenance(client, step_bytes):
    _creer(client, step_bytes)
    client.put("/api/projets/aile1/config", headers=H, json={"donnees": {"slit_width": 0.2}})
    r = client.get("/api/projets/aile1/config/effective", headers=H)
    assert r.status_code == 200
    prov, val = r.json()["provenance"], r.json()["valeurs"]
    assert val["slit_width"] == 0.2 and prov["slit_width"] == "projet"
    assert val["material_settings"]["line_width"] == 0.42
    assert prov["material_settings.line_width"] == "profil"
    assert prov["split.gap"] == "profil"
    assert prov["printer"] == "défaut" and prov["material"] == "défaut"


def test_schema(client):
    r = client.get("/api/schema", headers=H)
    assert r.status_code == 200
    j = r.json()
    assert "slit_width" in j["schema"]["properties"]
    assert {"pla_aero", "asa_aero"} <= set(j["matieres"])


def test_projet_inconnu(client):
    assert client.get("/api/projets/nope", headers=H).status_code == 404


def test_store_direct(tmp_path, step_bytes):
    s = ProjectStore(tmp_path)
    s.create("p", step_bytes, "a.stp", material="asa_aero")
    assert s.read_config("p")["donnees"] == {"material": "asa_aero"}
    with pytest.raises(ProjectError):
        s.create("../p2", step_bytes, "a.step")
