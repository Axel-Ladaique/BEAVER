import io
import os
import stat
import time

import pytest
import trimesh
from fastapi.testclient import TestClient

from aerorib import preview
from aerorib.gui.server import create_app
from aerorib.io_step import write_step
from aerorib.testing.synthetic import wing_x_bodies

TOKEN = "jeton-test"
H = {"X-AeroRib-Token": TOKEN}
CFG = {
    "material_settings": {"layer_height": 1.0},
    "rib_layers": [{"spacing": 20}],
    "passages": {"mode": "link"},
    "segment": {"max_height": 30},
}


@pytest.fixture(scope="module")
def step_bytes(tmp_path_factory):
    p = tmp_path_factory.mktemp("step") / "aile.step"
    write_step(p, wing_x_bodies())
    return p.read_bytes()


@pytest.fixture
def client(tmp_path, step_bytes):
    preview.clear_cache()
    c = TestClient(create_app(tmp_path / "projets", TOKEN), base_url="http://127.0.0.1")
    r = c.post("/api/projets", headers=H, data={"nom": "aile1"}, files={"fichier": ("aile.step", step_bytes)})
    assert r.status_code == 201, r.text
    yield c
    preview.clear_cache()


def _post(client, config=CFG, z=None, **extra):
    return client.post("/api/projets/aile1/apercu", headers=H, json={"config": config, "z": z, **extra})


def _attendre(client, **kw):
    r = _post(client, attendre=True, **kw)
    assert r.status_code == 200, r.text
    return r


def test_reponse_complete(client):
    r = _attendre(client, z=40.5)
    d = r.json()
    assert d["preparation"] is False
    assert d["section"]["z"] == pytest.approx(40.5) and d["section"]["polygones"]
    assert d["planform"]["segments"] and len(d["planform"]["aile"]) == 4
    assert {t["type"] for t in d["planform"]["trous"]} == {"longeron", "passage relié"}
    assert d["estimates"]["plaques"] > 0
    assert sum(c["plaques"] for c in d["estimates"]["plaques_par_couche"]) == d["estimates"]["plaques"]
    assert isinstance(d["avertissements"], list) and all(isinstance(w, str) for w in d["avertissements"])


def test_z_nul_est_mi_envergure(client):
    d = _attendre(client).json()
    assert d["section"]["z"] == pytest.approx(40.5, abs=1.0)


def test_config_invalide_422(client):
    r = _post(client, config={"rib_layers": "nimporte quoi"})
    assert r.status_code == 422
    assert "invalide" in r.json()["erreur"]
    r = _post(client, config={"champ_inconnu": 1})
    assert r.status_code == 422 and "erreur" in r.json()
    r = _post(client, config=[1, 2])
    assert r.status_code == 422
    r = client.post("/api/projets/aile1/apercu", headers=H, json={"z": 1})
    assert r.status_code == 422


def test_z_invalide_422(client):
    assert _post(client, z="haut").status_code == 422


def test_deuxieme_appel_z_seul_rapide(client):
    _attendre(client, z=30.0)
    t = time.perf_counter()
    r = _post(client, z=50.0)
    dt = time.perf_counter() - t
    assert r.status_code == 200 and r.json()["section"]["z"] == pytest.approx(50.0)
    assert dt < 1.0, dt


def test_202_puis_200(client):
    r = _post(client, z=40.5)
    if r.status_code == 202:
        assert r.json() == {"preparation": True, "message": "préparation de la pièce…"}
    deadline = time.time() + 120
    while r.status_code == 202 and time.time() < deadline:
        time.sleep(0.2)
        r = _post(client, z=40.5)
    assert r.status_code == 200 and r.json()["preparation"] is False


def test_202_garanti_par_clef_inedite(client, monkeypatch):
    import threading
    gate = threading.Event()
    real = preview.prepare_status

    def slow(step, cfg):
        gate.wait(30)
        return real(step, cfg)

    monkeypatch.setattr(preview, "prepare_status", slow)
    try:
        r = _post(client)
        assert r.status_code == 202 and r.json()["preparation"] is True
        assert _post(client).status_code == 202
        s = client.get("/api/projets/aile1/apercu/peau.stl", headers=H)
        assert s.status_code == 409 and s.json() == {"erreur": "aperçu en préparation"}
    finally:
        gate.set()
    r = _attendre(client)
    assert r.json()["preparation"] is False


def test_echec_de_preparation_puis_reessai(client, monkeypatch):
    real = preview.prepare_status
    calls = []

    def boom(step, cfg):
        calls.append(1)
        if len(calls) == 1:
            raise preview.PreviewError("pièce illisible dans /tmp/secret/piece.step")
        return real(step, cfg)

    monkeypatch.setattr(preview, "prepare_status", boom)
    r = _post(client, attendre=True)
    assert r.status_code == 422
    assert r.json()["erreur"].startswith("aperçu impossible : ") and "/tmp/secret" not in r.json()["erreur"]
    assert _post(client, attendre=True).status_code == 200


def test_erreur_geometrique_422(client, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("boum")

    monkeypatch.setattr(preview, "section", boom)
    r = _post(client, attendre=True)
    assert r.status_code == 422 and r.json()["erreur"] == "aperçu impossible : boum"


def test_stl_servis(client):
    assert client.get("/api/projets/aile1/apercu/plaques.stl", headers=H).status_code in (409, 200)
    _attendre(client)
    for nom in ("plaques", "peau"):
        r = client.get(f"/api/projets/aile1/apercu/{nom}.stl", headers=H)
        assert r.status_code == 200, r.text
        assert r.headers["content-type"].startswith("model/stl")
        mesh = trimesh.load(io.BytesIO(r.content), file_type="stl")
        assert len(mesh.faces) > 0


def test_stl_trop_de_plaques(client, monkeypatch):
    monkeypatch.setattr(preview, "MAX_MESH_PLATES", 1)
    _attendre(client)
    r = client.get("/api/projets/aile1/apercu/plaques.stl", headers=H)
    assert r.status_code == 422 and "vue 3D limitée" in r.json()["erreur"]


def test_stl_sans_apercu_utilise_config_enregistree(client):
    preview.clear_cache()
    r = client.get("/api/projets/aile1/apercu/peau.stl", headers=H)
    assert r.status_code == 409
    deadline = time.time() + 120
    while r.status_code == 409 and time.time() < deadline:
        time.sleep(0.2)
        r = client.get("/api/projets/aile1/apercu/peau.stl", headers=H)
    assert r.status_code == 200


def test_projet_sans_piece_404(client, tmp_path):
    (tmp_path / "projets" / "vide").mkdir()
    for method, url, kw in [
        ("post", "/api/projets/vide/apercu", {"json": {"config": {}}}),
        ("get", "/api/projets/vide/apercu/peau.stl", {}),
        ("get", "/api/projets/vide/apercu/plaques.stl", {}),
        ("get", "/api/projets/vide/corps", {}),
        ("get", "/api/projets/vide/config", {}),
        ("put", "/api/projets/vide/config", {"json": {"donnees": {}}}),
        ("get", "/api/projets/vide/config/effective", {}),
    ]:
        r = getattr(client, method)(url, headers=H, **kw)
        assert r.status_code == 404, (url, r.status_code)
        assert not (tmp_path / "projets" / "vide" / "config.yaml").exists()


def test_mauvaise_methode_405(client):
    for method, url in [("get", "/api/projets/aile1/apercu"), ("delete", "/api/projets/aile1/config"),
                        ("post", "/api/projets/aile1/apercu/peau.stl"), ("post", "/api/sante")]:
        r = getattr(client, method)(url, headers=H)
        assert r.status_code == 405 and r.json() == {"erreur": "méthode non autorisée"}, url
    assert client.get("/api/inconnue", headers=H).status_code == 404


@pytest.mark.skipif(os.name == "nt", reason="modes POSIX")
def test_config_ecrite_selon_umask(client, tmp_path):
    old = os.umask(0o022)
    try:
        r = client.put("/api/projets/aile1/config", headers=H, json={"donnees": {"material": "pla_aero"}})
    finally:
        os.umask(old)
    assert r.status_code == 200
    mode = stat.S_IMODE((tmp_path / "projets" / "aile1" / "config.yaml").stat().st_mode)
    assert mode == 0o644


def _variante(i):
    return {**CFG, "validation": {"tessellation_tol": 0.02 + 0.001 * i}}


def test_jobs_bornes_et_annulation(client, tmp_path, monkeypatch):
    import threading
    from aerorib.gui.apercu import Apercu
    from aerorib.gui.projects import ProjectStore

    api = Apercu(ProjectStore(tmp_path / "projets"))
    gate = threading.Event()
    real = preview.prepare_status

    def slow(step, cfg):
        gate.wait(30)
        return real(step, cfg)

    monkeypatch.setattr(preview, "prepare_status", slow)
    try:
        for i in range(4):  # 1 en cours + 3 demandes successives : les mises en file sont annulées
            assert api.post("aile1", {"config": _variante(i)}, False).status_code == 202
        assert len(api._jobs) == 2
        assert sum(1 for f in api._jobs.values() if f.running()) == 1
    finally:
        gate.set()
    # la dernière demande aboutit ; une fois servie, plus rien n'est retenu
    assert api.post("aile1", {"config": _variante(3), "attendre": True}, False)["preparation"] is False
    assert len(api._jobs) <= 1  # au plus la préparation 0, terminée mais jamais relevée
    for i in range(10, 16):  # préparations réussies successives : la carte reste vide
        api.post("aile1", {"config": _variante(i), "attendre": True}, False)
        assert len(api._jobs) <= 1
    assert api._jobs == {} or all(f.done() for f in api._jobs.values())


def test_corps_trop_gros_413(client):
    gros = {"config": {"bambu": {"template": "x" * (1024 * 1024 + 10)}}}
    r = client.post("/api/projets/aile1/apercu", headers=H, json=gros)
    assert r.status_code == 413 and r.json() == {"erreur": "configuration trop volumineuse (max 1 Mo)"}


def test_octet_nul_dans_gabarit_422(client):
    r = _post(client, config={"bambu": {"template": "a\x00b"}})
    assert r.status_code == 422 and "gabarit" in r.json()["erreur"]


def test_config_postee_pilote_le_resultat(client, tmp_path):
    cfg_path = tmp_path / "projets" / "aile1" / "config.yaml"
    avant = cfg_path.read_bytes()
    deux = _attendre(client, config={**CFG, "rib_layers": [{"spacing": 20}]}).json()["estimates"]["plaques"]
    dix = _attendre(client, config={**CFG, "rib_layers": [{"spacing": 10}]}).json()["estimates"]["plaques"]
    assert dix > deux
    assert cfg_path.read_bytes() == avant
    # le STL utilise la dernière config postée (spacing 10), pas celle de config.yaml
    import trimesh
    r = client.get("/api/projets/aile1/apercu/plaques.stl", headers=H)
    assert r.status_code == 200
    n_dix = len(trimesh.load(io.BytesIO(r.content), file_type="stl").faces)
    _attendre(client, config={**CFG, "rib_layers": [{"spacing": 20}]})
    r = client.get("/api/projets/aile1/apercu/plaques.stl", headers=H)
    n_vingt = len(trimesh.load(io.BytesIO(r.content), file_type="stl").faces)
    assert n_dix > n_vingt


def test_sans_jeton_401(client):
    for method, url in [("post", "/api/projets/aile1/apercu"), ("get", "/api/projets/aile1/apercu/peau.stl"),
                        ("get", "/api/projets/aile1/apercu/plaques.stl")]:
        assert getattr(client, method)(url).status_code == 401


def test_head_sur_route_get_405(client):
    r = client.head("/api/projets/aile1/apercu", headers=H)
    assert r.status_code == 405
