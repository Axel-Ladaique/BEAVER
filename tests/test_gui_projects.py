import threading

import pytest
import yaml
from fastapi.testclient import TestClient

from aerorib.gui import projects, server
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


@pytest.mark.parametrize("nom", ["../x", "a/b", "..", ".cache", "a\\b", "", "con", "x.", " x", "x ", "aile²", "日本"])
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
    prov = {".".join(e["chemin"]): e["origine"] for e in r.json()["provenance"]}
    assert isinstance(r.json()["provenance"][0]["chemin"], list)
    val = r.json()["valeurs"]
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


def _multipart(nom, data, boundary="XBOUNDARYX"):
    return (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"nom\"\r\n\r\n{nom}\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"fichier\"; filename=\"a.step\"\r\n"
        f"Content-Type: application/octet-stream\r\n\r\n"
    ).encode() + data + f"\r\n--{boundary}--\r\n".encode()


def test_limite_corps_content_length_voie_rapide(client, step_bytes, monkeypatch):
    monkeypatch.setattr(server, "MAX_UPLOAD_BODY", 500)
    assert _creer(client, step_bytes).status_code == 413


def test_limite_corps_reel_content_length_sous_estime(client, step_bytes, monkeypatch):
    monkeypatch.setattr(server, "MAX_UPLOAD_BODY", 500)
    body = _multipart("aile1", b"ISO-10303-21;" + b"x" * 5000)
    h = {**H, "Content-Type": "multipart/form-data; boundary=XBOUNDARYX", "Content-Length": "100"}
    r = client.post("/api/projets", headers=h, content=body)
    assert r.status_code == 413
    assert r.json() == {"erreur": "fichier trop volumineux (max 200 Mo)"}
    assert client.get("/api/projets", headers=H).json() == []


def test_limite_corps_chunked_sans_content_length(client, monkeypatch):
    monkeypatch.setattr(server, "MAX_UPLOAD_BODY", 500)
    body = _multipart("aile1", b"ISO-10303-21;" + b"x" * 5000)

    def morceaux():
        for i in range(0, len(body), 200):
            yield body[i : i + 200]

    h = {**H, "Content-Type": "multipart/form-data; boundary=XBOUNDARYX"}
    r = client.post("/api/projets", headers=h, content=morceaux())
    assert r.status_code == 413
    assert client.get("/api/projets", headers=H).json() == []


def test_put_config_corps_trop_gros(client, step_bytes):
    _creer(client, step_bytes)
    r = client.put("/api/projets/aile1/config", headers=H, json={"texte": "# " + "x" * (1024 * 1024 + 10)})
    assert r.status_code == 413 and "erreur" in r.json()


def test_ecritures_config_concurrentes(client, step_bytes, tmp_path):
    _creer(client, step_bytes)
    store = ProjectStore(tmp_path / "projets")
    valeurs = [0.1 + 0.01 * i for i in range(6)]
    erreurs = []

    def ecrire(v):
        try:
            for _ in range(30):
                store.write_config("aile1", {"slit_width": v})
        except Exception as exc:  # noqa: BLE001
            erreurs.append(exc)

    fils = [threading.Thread(target=ecrire, args=(v,)) for v in valeurs]
    [f.start() for f in fils]
    [f.join() for f in fils]
    assert erreurs == []
    final = yaml.safe_load((tmp_path / "projets/aile1/config.yaml").read_text())
    assert final["slit_width"] in valeurs
    assert not list((tmp_path / "projets/aile1").glob(".config.*"))


def test_erreurs_validation_en_francais(client, step_bytes):
    r = client.post("/api/projets", headers=H, data={"nom": "x"})
    assert r.status_code == 422 and r.json() == {"erreur": "requête invalide : champ « fichier » manquant"}
    r = client.post("/api/projets", headers=H, files={"fichier": ("a.step", b"x")})
    assert r.status_code == 422 and "« nom » manquant" in r.json()["erreur"]
    _creer(client, step_bytes)
    r = client.put("/api/projets/aile1/config", headers=H, json=[1, 2])
    assert r.status_code == 422 and r.json()["erreur"].startswith("requête invalide")
    r = client.put("/api/projets/aile1/config", headers=H, content=b"{pas json",
                   )
    assert r.status_code == 422 and "erreur" in r.json()
    r = client.put("/api/projets/aile1/config", headers=H, json={"autre": 1})
    assert r.status_code == 422 and "erreur" in r.json()


def test_chemins_absolus_masques(client, step_bytes, tmp_path):
    _creer(client, step_bytes)
    r = client.put("/api/projets/aile1/config", headers=H, json={"donnees": {"slit_width": -1}})
    assert r.status_code == 422
    msg = r.json()["erreur"]
    assert "pla_aero.yaml" in msg  # le nom de fichier reste
    assert str(tmp_path) not in msg and "/Users" not in msg and "/private" not in msg and ".venv" not in msg


@pytest.mark.parametrize("brut", ["%2e%2e", "a%5Cb", "a%2Fb", "%2e%2e%2f%2e%2e"])
def test_parametre_encode_refuse(client, step_bytes, brut):
    _creer(client, step_bytes)
    for suffixe in ("", "/corps", "/config"):
        r = client.get(f"/api/projets/{brut}{suffixe}", headers=H)
        assert r.status_code in (404, 422), (brut, suffixe, r.status_code)
    assert [p["nom"] for p in client.get("/api/projets", headers=H).json()] == ["aile1"]


def test_projet_malforme_404(client, tmp_path):
    (tmp_path / "projets" / "vide").mkdir(parents=True)
    assert client.get("/api/projets/vide", headers=H).status_code == 404
    assert client.get("/api/projets", headers=H).json() == []


def test_config_non_utf8_422(client, step_bytes, tmp_path):
    _creer(client, step_bytes)
    (tmp_path / "projets/aile1/config.yaml").write_bytes(b"\xff\xfe\x00bad")
    r = client.get("/api/projets/aile1/config", headers=H)
    assert r.status_code == 422 and "UTF-8" in r.json()["erreur"]


def _prov(client):
    r = client.get("/api/projets/aile1/config/effective", headers=H)
    assert r.status_code == 200, r.text
    return {".".join(e["chemin"]): e["origine"] for e in r.json()["provenance"]}


def test_provenance_cas_limites(client, step_bytes):
    _creer(client, step_bytes)
    client.put("/api/projets/aile1/config", headers=H, json={"donnees": {
        "material": "pla_aero",
        "rib_layers": [{"name": "a", "pattern": "diamond", "spacing": 25}],
        "spars": {"default": {"clearance": 0.15}},
    }})
    prov = _prov(client)
    assert prov["material"] == "projet"  # explicite, même égal au défaut
    assert prov["rib_layers"] == "projet"
    assert prov["spars.default.clearance"] == "projet"  # explicite, même égal au profil
    assert prov["printer"] == "défaut"


def test_projet_invisible_pendant_l_import(tmp_path, step_bytes, monkeypatch):
    """Pendant la lecture du STEP importé, le projet n'est ni listé ni ouvrable ; piece.step arrive en dernier."""
    store = ProjectStore(tmp_path)
    vu = {}
    lire = projects.read_step

    def lecture(path):
        vu["liste"] = store.list()
        vu["piece"] = (tmp_path / "aile1" / "piece.step").exists()
        with pytest.raises(ProjectError) as exc:
            store.require("aile1")
        vu["statut"] = exc.value.status
        return lire(path)

    monkeypatch.setattr(projects, "read_step", lecture)
    store.create("aile1", step_bytes, "aile.step")
    assert vu == {"liste": [], "piece": False, "statut": 404}
    assert [p["nom"] for p in store.list()] == ["aile1"] and (tmp_path / "aile1" / "config.yaml").is_file()
    assert sorted(p.name for p in (tmp_path / "aile1").iterdir()) == ["config.yaml", "piece.step"]


def test_casse_differente_meme_projet_sur_disque_insensible(client, step_bytes, tmp_path):
    """Disque insensible à la casse : /api/projets/AILE1 désigne « aile1 » (nom réel), pas un double."""
    assert _creer(client, step_bytes).status_code == 201
    if not (tmp_path / "projets" / "AILE1").exists():
        pytest.skip("système de fichiers sensible à la casse")
    assert client.get("/api/projets/AILE1", headers=H).json()["nom"] == "aile1"
    store = ProjectStore(tmp_path / "projets")
    assert store.require("AILE1").name == "aile1" and store.config_path("Aile1").parent.name == "aile1"
    assert _creer(client, step_bytes, nom="AILE1").status_code == 409
