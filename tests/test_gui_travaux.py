"""Générations en arrière-plan (processus séparé) et historique des essais.

Les travaux lents ou en échec sont simulés par des variables d'environnement lues dans le processus fils
(le monkeypatch ne franchit pas la frontière spawn) : BEAVER_TEST_DELAI (secondes d'attente avant le calcul)
et BEAVER_TEST_PLANTAGE (le fils meurt sans message final).
"""

import json
import multiprocessing
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from beaver.gui import travaux
from beaver.gui.server import create_app
from beaver.io_step import write_step
from beaver.testing.synthetic import wing_x_bodies

TOKEN = "jeton-test"
H = {"X-Beaver-Token": TOKEN}
FAST = "material_settings: {layer_height: 1.0}\nrib_layers: [{spacing: 20}]\npassages: {mode: link}\n"
FAST_ASA = "material: asa_aero\nbambu: {template: null}\nmaterial_settings: {layer_height: 1.0}\nrib_layers: [{spacing: 25}]\npassages: {mode: link}\n"


@pytest.fixture(scope="module")
def step_bytes(tmp_path_factory):
    p = tmp_path_factory.mktemp("step") / "aile.step"
    write_step(p, wing_x_bodies())
    return p.read_bytes()


def _groupe(pgid: int) -> list[int]:
    """PIDs vivants du groupe de processus `pgid` (fils de génération et ses descendants : pool, tracker)."""
    r = subprocess.run(["pgrep", "-g", str(pgid)], capture_output=True, text=True)
    return [int(x) for x in r.stdout.split()]


def _groupe_vide(pgid: int, timeout: float = 15.0) -> list[int]:
    t0 = time.monotonic()
    while (restants := _groupe(pgid)) and time.monotonic() - t0 < timeout:
        time.sleep(0.2)
    return restants


@pytest.fixture(scope="module", autouse=True)
def aucun_processus_orphelin():
    """Aucun fils ni descendant (pool de validation) ne survit : chaque fils de génération est chef de son groupe."""
    pids: list[int] = []
    origine = travaux.JobManager.start

    def start(self, nom):
        job = origine(self, nom)
        pids.append(job.process.pid)
        return job

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(travaux.JobManager, "start", start)
        yield pids
    restants = multiprocessing.active_children()
    for p in restants:
        p.kill()
        p.join(5)
    assert not restants, "un processus de génération a survécu aux tests"
    if sys.platform != "win32":
        orphelins = {pid: r for pid in pids if (r := _groupe_vide(pid, 5.0))}
        for r in orphelins.values():
            for pid in r:
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        assert not orphelins, f"descendants du fils de génération survivants : {orphelins}"


def _app_client(root: Path, step_bytes: bytes, projets: dict[str, str]):
    app = create_app(root, TOKEN)
    c = TestClient(app, base_url="http://127.0.0.1")
    for nom, cfg in projets.items():
        r = c.post("/api/projets", headers=H, data={"nom": nom}, files={"fichier": ("aile.step", step_bytes)})
        assert r.status_code == 201, r.text
        r = c.put(f"/api/projets/{nom}/config", headers=H, json={"texte": cfg})
        assert r.status_code == 200, r.text
    return app, c


@pytest.fixture
def client(tmp_path, step_bytes):
    app, c = _app_client(tmp_path / "projets", step_bytes, {"aile1": FAST, "aile2": FAST})
    yield c
    app.state.travaux.shutdown()


def _sse(client, job_id: str, **params) -> list:
    """Événements du flux (dicts) et commentaires (« : ... ») dans l'ordre, jusqu'à la fermeture."""
    out = []
    with client.stream("GET", f"/api/generations/{job_id}/flux", headers=H, params=params) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        for line in r.iter_lines():
            if line.startswith("data: "):
                out.append(json.loads(line[6:]))
            elif line.startswith(":"):
                out.append(line)
    return out


def _attendre_fin(client, job_id: str, timeout=120) -> dict:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        d = client.get(f"/api/generations/{job_id}", headers=H).json()
        if d["statut"] != "en cours":
            return d
        time.sleep(0.2)
    raise AssertionError("génération trop longue")


# --- deux générations réelles partagées (PLA avec gabarit, ASA sans gabarit) ------------------------
@pytest.fixture(scope="module")
def genere(tmp_path_factory, step_bytes):
    root = tmp_path_factory.mktemp("gen") / "projets"
    app, c = _app_client(root, step_bytes, {"aile1": FAST})
    t0 = time.monotonic()
    r = c.post("/api/projets/aile1/generations", headers=H)
    assert r.status_code == 202, r.text
    id_a = r.json()["id"]
    flux = _sse(c, id_a)  # en direct : la génération tourne pendant la lecture
    duree = time.monotonic() - t0
    statut_a = c.get(f"/api/generations/{id_a}", headers=H).json()
    r = c.put("/api/projets/aile1/config", headers=H, json={"texte": FAST_ASA})
    assert r.status_code == 200, r.text
    id_b = c.post("/api/projets/aile1/generations", headers=H).json()["id"]
    statut_b = _attendre_fin(c, id_b)
    yield {"client": c, "root": root, "flux": flux, "a": statut_a, "b": statut_b, "duree": duree, "app": app}
    app.state.travaux.shutdown()


def test_generation_complete(genere):
    c, a = genere["client"], genere["a"]
    assert a["statut"] == "ok", a
    essai = a["essai"]
    d = genere["root"] / "aile1" / "essais" / essai
    resume = json.loads((d / "resume.json").read_text(encoding="utf-8"))
    assert resume["statut"] == "ok"
    assert resume["nervures"] > 10 and resume["troncons"] >= 1
    assert resume["masse"] > 0 and resume["trajet"] > 0 and resume["duree"] > 0
    assert resume["erreurs"] == [] and isinstance(resume["avertissements"], list)
    (corps,) = resume["corps"]  # morceaux imprimés : un seul ici, plateau 1
    (m,) = corps["liste_morceaux"]
    assert (m["nom"], m["plateau"], m["ok"], m["tient"]) == ("T1", 1, True, True) and m["rotation"] == 0
    assert resume["morceaux"] == 1 and resume["notices"] == []
    for f in ("aile1.step", "aile1.stl", "aile1.3mf", "aile1-rapport.html"):
        assert f in resume["fichiers"] and (d / f).is_file(), f
    cfg = yaml.safe_load((d / "config.yaml").read_text(encoding="utf-8"))
    assert cfg["rib_layers"][0]["spacing"] == 20 and cfg["material"] == "pla_aero"
    assert cfg["bambu"]["template"]  # configuration résolue : profil inclus
    # aucun fichier écrit ailleurs (output.dir ignoré)
    assert not (genere["root"] / "aile1" / "out").exists()
    # historique
    liste = c.get("/api/projets/aile1/essais", headers=H).json()
    assert [e["essai"] for e in liste] == [genere["b"]["essai"], essai]  # plus récent d'abord
    e = next(e for e in liste if e["essai"] == essai)
    assert e["statut"] == "ok" and e["nervures"] == resume["nervures"] and e["masse"] == resume["masse"]
    assert e["morceaux"] == 1
    det = c.get(f"/api/projets/aile1/essais/{essai}", headers=H).json()
    assert det["resume"]["statut"] == "ok" and det["config"]["rib_layers"][0]["spacing"] == 20
    # fichiers servis
    r = c.get(f"/api/projets/aile1/essais/{essai}/fichiers/aile1-rapport.html", headers=H)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    r = c.get(f"/api/projets/aile1/essais/{essai}/fichiers/aile1.stl", params={"token": TOKEN})
    assert r.status_code == 200 and r.headers["content-type"] == "model/stl" and len(r.content) > 1000
    r = c.get(f"/api/projets/aile1/essais/{essai}/fichiers/aile1.3mf", headers=H)
    assert r.status_code == 200 and r.headers["content-type"] == "model/3mf"
    r = c.get(f"/api/projets/aile1/essais/{essai}/fichiers/resume.json", headers=H)
    assert r.status_code == 200 and r.json()["statut"] == "ok"
    r = c.get(f"/api/projets/aile1/essais/{essai}/fichiers/config.yaml", headers=H)
    assert r.status_code == 200 and "yaml" in r.headers["content-type"]
    # le projet liste ses essais
    assert c.get("/api/projets/aile1", headers=H).json()["nb_essais"] == 2


def test_generation_avec_pool_de_validation(client, monkeypatch):
    """Seuil abaissé à 1 couche : la validation ouvre un pool depuis le fils de génération."""
    monkeypatch.setenv("BEAVER_PARALLEL_MIN_LAYERS", "1")
    r = client.post("/api/projets/aile1/generations", headers=H)
    assert r.status_code == 202, r.text
    d = _attendre_fin(client, r.json()["id"])
    assert d["statut"] in ("ok", "non imprimable"), d
    if sys.platform != "win32":  # fin normale : le pool et le fils sont partis
        assert _groupe_vide(client.app.state.travaux.get(r.json()["id"]).process.pid) == []


@pytest.mark.skipif(sys.platform == "win32", reason="groupes de processus POSIX (pgrep)")
def test_annulation_pendant_validation_tue_le_pool(client, monkeypatch):
    """Annuler pendant la validation parallèle tue tout l'arbre : fils, pool de validation et tracker."""
    monkeypatch.setenv("BEAVER_PARALLEL_MIN_LAYERS", "1")
    monkeypatch.setenv("BEAVER_TEST_DELAI_VALIDATION", "30")  # chaque couche validée attend : pool occupé
    r = client.post("/api/projets/aile1/generations", headers=H)
    assert r.status_code == 202, r.text
    job = r.json()
    pid = client.app.state.travaux.get(job["id"]).process.pid
    t0 = time.monotonic()
    while len(_groupe(pid)) < 3:  # fils + au moins deux processus du pool
        assert time.monotonic() - t0 < 120, "le pool de validation n'a pas démarré"
        assert client.get(f"/api/generations/{job['id']}", headers=H).json()["statut"] == "en cours"
        time.sleep(0.2)
    descendants = subprocess.run(["pgrep", "-P", str(pid)], capture_output=True, text=True).stdout.split()
    assert descendants  # le pool est bien un descendant du fils
    st = client.post(f"/api/generations/{job['id']}/annuler", headers=H).json()
    assert st["statut"] == "annulé"
    assert _groupe_vide(pid) == []
    for d in descendants:
        assert subprocess.run(["kill", "-0", d], capture_output=True).returncode != 0, d


def test_flux_progression_puis_final(genere):
    flux = [e for e in genere["flux"] if isinstance(e, dict)]
    types = [e["type"] for e in flux]
    assert types[-1] == "fin" and types.count("fin") == 1
    assert types[0] == "progression" and all(t == "progression" for t in types[:-1])
    assert any("nervures" in e["message"] for e in flux[:-1])
    assert flux[-1]["resume"]["statut"] == "ok"
    # rejoué après coup : mêmes messages, puis fermeture
    replay = [e for e in _sse(genere["client"], genere["a"]["id"]) if isinstance(e, dict)]
    assert replay == flux


def test_sans_gabarit_pas_de_3mf(genere):
    b = genere["b"]
    assert b["statut"] in ("ok", "non imprimable"), b
    d = genere["root"] / "aile1" / "essais" / b["essai"]
    resume = json.loads((d / "resume.json").read_text(encoding="utf-8"))
    assert resume["statut"] == b["statut"] and (resume["erreurs"] == []) == (b["statut"] == "ok")
    assert not list(d.glob("*.3mf")) and "aile1.step" in resume["fichiers"]
    assert any("3MF" in w and "ASA Aero" in w for w in resume["avertissements"]), resume["avertissements"]
    c = genere["client"]
    r = c.post(f"/api/projets/aile1/essais/{b['essai']}/ouvrir-bambu", headers=H)
    assert r.status_code == 404 and "3mf" in r.json()["erreur"].lower()


def test_diff_entre_essais(genere):
    c, a, b = genere["client"], genere["a"]["essai"], genere["b"]["essai"]
    r = c.get("/api/projets/aile1/essais/diff", headers=H, params={"a": a, "b": b})
    assert r.status_code == 200, r.text
    diff = {tuple(d["chemin"]): (d["a"], d["b"]) for d in r.json()}
    assert diff[("material",)] == ("pla_aero", "asa_aero")
    assert diff[("rib_layers",)][0][0]["spacing"] == 20 and diff[("rib_layers",)][1][0]["spacing"] == 25
    assert ("slit_width",) not in diff  # valeurs identiques absentes
    assert c.get("/api/projets/aile1/essais/diff", headers=H, params={"a": a, "b": a}).json() == []
    r = c.get("/api/projets/aile1/essais/diff", headers=H, params={"a": a, "b": "20991231-000000"})
    assert r.status_code == 404 and "erreur" in r.json()
    r = c.get("/api/projets/aile1/essais/diff", headers=H, params={"a": a, "b": "../x"})
    assert r.status_code == 422


def test_ouvrir_bambu_et_dossier(genere, monkeypatch):
    from beaver import bambu

    c, essai = genere["client"], genere["a"]["essai"]
    ouverts = []
    monkeypatch.setattr(bambu, "open_in_bambu", lambda p: ouverts.append(("bambu", Path(p))))
    monkeypatch.setattr(travaux, "ouvrir_dossier", lambda p: ouverts.append(("dossier", Path(p))))
    r = c.post(f"/api/projets/aile1/essais/{essai}/ouvrir-bambu", headers=H)
    assert r.status_code == 200, r.text
    r = c.post(f"/api/projets/aile1/essais/{essai}/ouvrir-dossier", headers=H)
    assert r.status_code == 200, r.text
    d = (genere["root"] / "aile1" / "essais" / essai).resolve()
    assert ouverts == [("bambu", d / "aile1.3mf"), ("dossier", d)]


def test_ouvrir_dossier_commande_par_systeme(monkeypatch, tmp_path):
    lances = []
    monkeypatch.setattr(travaux.subprocess, "Popen", lambda args: lances.append(args))
    monkeypatch.setattr(travaux.sys, "platform", "darwin")
    travaux.ouvrir_dossier(tmp_path)
    monkeypatch.setattr(travaux.sys, "platform", "linux")
    travaux.ouvrir_dossier(tmp_path)
    assert lances == [["open", str(tmp_path)], ["xdg-open", str(tmp_path)]]
    monkeypatch.setattr(travaux.sys, "platform", "win32")
    monkeypatch.setattr(travaux.os, "startfile", lambda p: lances.append(["startfile", p]), raising=False)
    travaux.ouvrir_dossier(tmp_path)
    assert lances[-1] == ["startfile", str(tmp_path)]


# --- concurrence, annulation, plantage ---------------------------------------------------------------
def test_deuxieme_lancement_409_puis_annulation(client, monkeypatch, tmp_path):
    monkeypatch.setenv("BEAVER_TEST_DELAI", "60")
    monkeypatch.setattr(travaux, "HEARTBEAT", 0.3)
    r = client.post("/api/projets/aile1/generations", headers=H)
    assert r.status_code == 202, r.text
    job = r.json()
    assert job["statut"] == "en cours" and job["projet"] == "aile1"
    for nom in ("aile1", "aile2"):
        r = client.post(f"/api/projets/{nom}/generations", headers=H)
        assert r.status_code == 409 and r.json() == {"erreur": "une génération est déjà en cours"}
    d = tmp_path / "projets" / "aile1" / "essais" / job["essai"]
    assert json.loads((d / "resume.json").read_text(encoding="utf-8"))["statut"] == "en cours"
    assert (d / "config.yaml").is_file()
    liste = client.get("/api/projets/aile1/essais", headers=H).json()
    assert liste[0]["statut"] == "en cours" and liste[0]["id"] == job["id"]

    def annuler():
        time.sleep(1.5)
        client.post(f"/api/generations/{job['id']}/annuler", headers=H)

    t = threading.Thread(target=annuler)
    t.start()
    t0 = time.monotonic()
    flux = _sse(client, job["id"])
    t.join()
    assert time.monotonic() - t0 < 30
    assert any(isinstance(e, str) and e.startswith(":") for e in flux)  # battement de cœur
    final = [e for e in flux if isinstance(e, dict)][-1]
    assert final["type"] == "annule"
    st = client.get(f"/api/generations/{job['id']}", headers=H).json()
    assert st["statut"] == "annulé"
    resume = json.loads((d / "resume.json").read_text(encoding="utf-8"))
    assert resume["statut"] == "annulé" and resume["duree"] > 0
    assert not multiprocessing.active_children()
    assert client.get("/api/projets/aile1/essais", headers=H).json()[0]["statut"] == "annulé"
    # annuler à nouveau : sans effet ; un nouveau travail peut démarrer
    assert client.post(f"/api/generations/{job['id']}/annuler", headers=H).json()["statut"] == "annulé"
    r = client.post("/api/projets/aile2/generations", headers=H)
    assert r.status_code == 202
    client.post(f"/api/generations/{r.json()['id']}/annuler", headers=H)


def test_essais_meme_seconde_sans_collision(client, monkeypatch):
    monkeypatch.setenv("BEAVER_TEST_DELAI", "60")
    figee = travaux.datetime(2026, 10, 11, 12, 0, 0)
    monkeypatch.setattr(travaux, "_maintenant", lambda: figee)
    essais = []
    for _ in range(2):
        r = client.post("/api/projets/aile1/generations", headers=H)
        assert r.status_code == 202, r.text
        essais.append(r.json()["essai"])
        client.post(f"/api/generations/{r.json()['id']}/annuler", headers=H)
    assert essais == ["20261011-120000", "20261011-120000-2"]


@pytest.mark.skipif(sys.platform == "win32", reason="SIGINT POSIX (Ctrl+C du terminal)")
def test_ctrl_c_annule_proprement(client, monkeypatch, tmp_path):
    """Ctrl+C atteint aussi le fils : il doit l'ignorer ; l'extinction du serveur annule l'essai."""
    monkeypatch.setenv("BEAVER_TEST_DELAI", "60")
    r = client.post("/api/projets/aile1/generations", headers=H)
    job = r.json()
    manager = client.app.state.travaux
    t0 = time.monotonic()
    while not client.get(f"/api/generations/{job['id']}", headers=H).json()["messages"]:  # SIGINT ignoré
        assert time.monotonic() - t0 < 60
        time.sleep(0.1)
    proc = manager.get(job["id"]).process
    os.kill(proc.pid, signal.SIGINT)
    time.sleep(1.0)
    assert proc.is_alive()
    assert client.get(f"/api/generations/{job['id']}", headers=H).json()["statut"] == "en cours"
    manager.shutdown()
    st = client.get(f"/api/generations/{job['id']}", headers=H).json()
    assert st["statut"] == "annulé"
    texte = (tmp_path / "projets" / "aile1" / "essais" / job["essai"] / "resume.json").read_text(encoding="utf-8")
    resume = json.loads(texte)
    assert resume["statut"] == "annulé" and resume["message"] == "génération annulée"
    assert "Traceback" not in texte and "KeyboardInterrupt" not in texte
    final = [e for e in _sse(client, job["id"]) if isinstance(e, dict)][-1]
    assert final == {"type": "annule", "message": "génération annulée"}


def test_fils_tue_pendant_extinction_annule(client, monkeypatch):
    """Un fils qui meurt sans message final pendant l'extinction (ou d'un signal d'arrêt) : « annulé »."""
    monkeypatch.setenv("BEAVER_TEST_DELAI", "60")
    job = client.post("/api/projets/aile1/generations", headers=H).json()
    proc = client.app.state.travaux.get(job["id"]).process
    proc.terminate()  # SIGTERM extérieur, ni annulation ni extinction demandée
    st = _attendre_fin(client, job["id"], timeout=30)
    assert st["statut"] == "annulé" and "signal" in st["resume"]["message"]


def test_processus_mort_sans_message_final(client, monkeypatch):
    monkeypatch.setenv("BEAVER_TEST_PLANTAGE", "1")
    r = client.post("/api/projets/aile1/generations", headers=H)
    st = _attendre_fin(client, r.json()["id"], timeout=60)
    assert st["statut"] == "erreur"
    assert "processus" in st["resume"]["erreurs"][0]
    final = [e for e in _sse(client, r.json()["id"]) if isinstance(e, dict)][-1]
    assert final["type"] == "erreur" and "processus" in final["message"]


def test_generation_projet_inconnu_ou_config_invalide(client, tmp_path):
    assert client.post("/api/projets/inconnu/generations", headers=H).status_code == 404
    assert client.post("/api/projets/..%2Fx/generations", headers=H).status_code in (404, 422)
    (tmp_path / "projets" / "aile1" / "config.yaml").write_text("rib_layers: 3\n", encoding="utf-8")
    r = client.post("/api/projets/aile1/generations", headers=H)
    assert r.status_code == 422 and "erreur" in r.json()
    assert not (tmp_path / "projets" / "aile1" / "essais").exists() or not any(
        (tmp_path / "projets" / "aile1" / "essais").iterdir())
    assert client.get("/api/generations/inconnu", headers=H).status_code == 404
    assert client.post("/api/generations/inconnu/annuler", headers=H).status_code == 404
    assert client.get("/api/generations/inconnu/flux", headers=H).status_code == 404
    assert client.get("/api/generations/inconnu", headers={"X-Beaver-Token": "faux"}).status_code == 401


# --- historique sans travail réel --------------------------------------------------------------------
def _faux_essai(root: Path, essai: str, resume: dict | None, config: dict | None = None) -> Path:
    d = root / "aile1" / "essais" / essai
    d.mkdir(parents=True)
    if resume is not None:
        (d / "resume.json").write_text(json.dumps(resume), encoding="utf-8")
    (d / "config.yaml").write_text(yaml.safe_dump(config or {"slit_width": 0.1}), encoding="utf-8")
    return d


def test_essai_en_cours_orphelin_interrompu(client, tmp_path):
    root = tmp_path / "projets"
    _faux_essai(root, "20261001-100000", {"statut": "en cours"})
    _faux_essai(root, "20261001-110000", None)
    _faux_essai(root, "20261001-120000", {"statut": "ok", "nervures": 3})
    _faux_essai(root, "20261399-999999", {"statut": "ok"})  # nom plausible, date impossible
    (root / "aile1" / "essais" / "pas-un-essai").mkdir()
    liste = client.get("/api/projets/aile1/essais", headers=H).json()
    assert [(e["essai"], e["statut"]) for e in liste] == [
        ("20261399-999999", "ok"), ("20261001-120000", "ok"), ("20261001-110000", "interrompu"), ("20261001-100000", "interrompu")]
    assert client.get("/api/projets/aile1/essais/20261001-100000", headers=H).json()["resume"]["statut"] == "interrompu"


def test_chemins_hors_essai_refuses(client, tmp_path):
    root = tmp_path / "projets"
    d = _faux_essai(root, "20261001-100000", {"statut": "ok"})
    (d / "sous").mkdir()
    (d / "sous" / "a.json").write_text("{}", encoding="utf-8")
    secret = tmp_path / "secret.txt"
    secret.write_text("secret", encoding="utf-8")
    (d / "lien.txt").symlink_to(secret)
    (d / "lien_dossier").symlink_to(tmp_path, target_is_directory=True)
    base = "/api/projets/aile1/essais/20261001-100000/fichiers/"
    assert client.get(base + "sous/a.json", headers=H).status_code == 200
    for chemin in ("../../config.yaml", "..%2F..%2Fconfig.yaml", "%2e%2e/%2e%2e/config.yaml", "sous/../../../piece.step",
                   "lien.txt", "lien_dossier/secret.txt", "%2Fetc%2Fpasswd", "..\\..\\config.yaml", "inexistant.json",
                   "sous"):
        r = client.get(base + chemin, headers=H)
        assert r.status_code in (403, 404, 422), (chemin, r.status_code)
        assert "secret" not in r.text and "erreur" in r.json()
    # (httpx normalise « .. » brut côté client : on envoie les variantes encodées)
    for essai in ("%2e%2e", "x", "20261001-100000%2F%2e%2e", "20261001", "20261001-100000-", "..%2F..%2Faile1",
                  "20261001-100000%0A", "%D9%A2%D9%A0%D9%A2%D9%A6%D9%A1%D9%A0%D9%A0%D9%A1-100000"):
        r = client.get(f"/api/projets/aile1/essais/{essai}", headers=H)
        assert r.status_code in (404, 422), (essai, r.status_code)
        r = client.get(f"/api/projets/aile1/essais/{essai}/fichiers/resume.json", headers=H)
        assert r.status_code in (404, 422), (essai, r.status_code)
    assert client.get("/api/projets/aile1/essais/20261002-100000", headers=H).status_code == 404
    assert client.post("/api/projets/aile1/essais/20261001-100000/ouvrir-bambu", headers=H).status_code == 404


def test_methode_non_autorisee_405(client):
    assert client.put("/api/generations/x/annuler", headers=H).status_code == 405
    assert client.get("/api/projets/aile1/generations", headers=H).status_code == 405


class _FauxProc:
    pid = 4242

    def __init__(self):
        self.appels = []
        self.vivant = True

    def join(self, timeout=None):
        self.appels.append("join")

    def is_alive(self):
        return self.vivant

    def terminate(self):
        self.appels.append("terminate")

    def kill(self):
        self.appels.append("kill")
        self.vivant = False


def test_tuer_arbre_windows_taskkill(monkeypatch):
    """Windows : taskkill /T /F tue l'arbre (pool compris) ; kill() en dernier recours."""
    tues = []
    monkeypatch.setattr(travaux.sys, "platform", "win32")
    monkeypatch.setattr(travaux, "_taskkill", tues.append)
    proc = _FauxProc()
    travaux._tuer_arbre(proc)
    assert tues == [4242] and proc.appels == ["join", "kill", "join"]


@pytest.mark.skipif(sys.platform == "win32", reason="signaux POSIX")
def test_tuer_arbre_posix_groupe(monkeypatch):
    """POSIX : SIGTERM au groupe, attente bornée, SIGKILL au groupe ; terminate() si pas encore de groupe."""
    signaux = []
    monkeypatch.setattr(travaux.sys, "platform", "darwin")
    monkeypatch.setattr(travaux, "_killpg", lambda pgid, sig: signaux.append((pgid, sig)) or True)
    proc = _FauxProc()
    proc.vivant = False
    travaux._tuer_arbre(proc)
    assert signaux == [(4242, signal.SIGTERM), (4242, signal.SIGKILL)] and proc.appels == ["join", "join"]
    monkeypatch.setattr(travaux, "_killpg", lambda pgid, sig: False)
    proc = _FauxProc()
    travaux._tuer_arbre(proc)
    assert proc.appels == ["terminate", "join", "kill", "join"]
