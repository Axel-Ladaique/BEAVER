"""API des profils matière : lecture, écriture de la seule différence avec l'embarqué, copie."""

import stat

import pytest
import yaml
from fastapi.testclient import TestClient

from aerorib.gui.server import create_app
from aerorib.material_profiles import embedded_dir

TOKEN = "jeton-test"
H = {"X-AeroRib-Token": TOKEN}


@pytest.fixture
def udir(tmp_path):
    return tmp_path / "env_profiles"


@pytest.fixture
def client(tmp_path, udir, monkeypatch):
    udir.mkdir()
    monkeypatch.setenv("AERORIB_PROFILES", str(udir))  # le cœur lit le même dossier que l'API
    monkeypatch.chdir(tmp_path)
    with TestClient(create_app(tmp_path / "racine", TOKEN, profiles_dir=udir), base_url="http://127.0.0.1") as c:
        yield c


def valeurs(client, nom="pla_aero"):
    return client.get(f"/api/profils/{nom}", headers=H).json()["valeurs"]


def test_liste(client):
    r = client.get("/api/profils", headers=H)
    assert r.status_code == 200
    par_nom = {p["nom"]: p for p in r.json()}
    assert par_nom["pla_aero"]["origine"] == "embarqué"
    assert par_nom["pla_aero"]["gabarit"] == {"chemin_nom": "p1s_pla_aero.3mf", "existe": True}
    assert par_nom["pla_aero"]["existe"] is True
    assert par_nom["asa_aero"]["gabarit"] == {"chemin_nom": None, "existe": False}


def test_lecture_valeurs_et_provenance(client, udir):
    d = client.get("/api/profils/pla_aero", headers=H).json()
    assert d["valeurs"]["material_settings"]["density"] == 0.8
    assert d["valeurs"]["bambu"]["template"] == "p1s_pla_aero.3mf"
    assert {"chemin": ["slit_width"], "origine": "embarqué"} in d["provenance"]
    assert all(p["origine"] == "embarqué" for p in d["provenance"])
    assert "/" not in str(d)  # aucun chemin absolu
    (udir / "pla_aero.yaml").write_text("material_settings: {density: 0.9}\n")
    d = client.get("/api/profils/pla_aero", headers=H).json()
    assert d["origine"] == "utilisateur"
    assert d["valeurs"]["material_settings"]["density"] == 0.9
    prov = {tuple(p["chemin"]): p["origine"] for p in d["provenance"]}
    assert prov[("material_settings", "density")] == "utilisateur"
    assert prov[("material_settings", "layer_height")] == "embarqué"


def test_asa_sans_gabarit_signale(client):
    d = client.get("/api/profils/asa_aero", headers=H).json()
    assert d["gabarit"] == {"chemin_nom": None, "existe": False}


def test_gabarit_utilisateur_inexistant(client, udir):
    (udir / "asa_aero.yaml").write_text("bambu: {template: absent.3mf}\n")
    d = client.get("/api/profils/asa_aero", headers=H).json()
    assert d["gabarit"] == {"chemin_nom": "absent.3mf", "existe": False}
    assert str(udir) not in str(d)


def test_profil_inconnu_404(client):
    r = client.get("/api/profils/nexiste_pas", headers=H)
    assert r.status_code == 404
    assert "inconnu" in r.json()["erreur"]


@pytest.mark.parametrize("nom", ["PLA", "%2e%2e", "%2E%2E%2Fx", "a%2Fb", "a.b", "é", "x" * 41, "a b", "pla-aero", "..%5Cx"])
def test_noms_invalides(client, nom):
    for r in (
        client.get(f"/api/profils/{nom}", headers=H),
        client.put(f"/api/profils/{nom}", headers=H, json={"donnees": {}}),
        client.post(f"/api/profils/{nom}/copier", headers=H),
    ):
        assert r.status_code in (404, 422), (nom, r.status_code)
        assert "erreur" in r.json()
    assert client.put(f"/api/profils/{nom}", headers=H, json={"donnees": {}}).status_code != 200


def test_nom_majuscule_422(client):
    r = client.put("/api/profils/PLA", headers=H, json={"donnees": {}})
    assert r.status_code == 422 and "nom de profil invalide" in r.json()["erreur"]


def test_edition_ecrit_seulement_la_difference(client, udir):
    v = valeurs(client)
    v["material_settings"]["density"] = 0.95
    r = client.put("/api/profils/pla_aero", headers=H, json={"donnees": v})
    assert r.status_code == 200, r.text
    assert yaml.safe_load((udir / "pla_aero.yaml").read_text()) == {"material_settings": {"density": 0.95}}
    d = r.json()
    assert d["valeurs"]["material_settings"]["density"] == 0.95
    assert d["gabarit"]["existe"] is True  # gabarit embarqué conservé
    assert not list(udir.glob(".*"))
    assert stat.S_IMODE((udir / "pla_aero.yaml").stat().st_mode) & 0o444 == 0o444


def test_edition_liste_remplacee_en_bloc(client, udir):
    v = valeurs(client)
    v["rib_layers"][0]["spacing"] = 25
    assert client.put("/api/profils/pla_aero", headers=H, json={"donnees": v}).status_code == 200
    assert yaml.safe_load((udir / "pla_aero.yaml").read_text()) == {"rib_layers": [{**v["rib_layers"][0]}]}


def test_valeur_invalide_422_fichier_inchange(client, udir):
    f = udir / "pla_aero.yaml"
    f.write_text("slit_width: 0.12\n")
    avant = f.read_bytes()
    v = valeurs(client)
    v["material_settings"]["line_width"] = -1
    r = client.put("/api/profils/pla_aero", headers=H, json={"donnees": v})
    assert r.status_code == 422
    assert "line_width" in r.json()["erreur"]
    assert str(udir) not in r.json()["erreur"]
    assert f.read_bytes() == avant
    assert sorted(p.name for p in udir.iterdir()) == ["pla_aero.yaml"]


def test_valeur_invalide_sans_fichier_existant(client, udir):
    v = valeurs(client)
    v["slit_width"] = "beaucoup"
    r = client.put("/api/profils/pla_aero", headers=H, json={"donnees": v})
    assert r.status_code == 422 and "slit_width" in r.json()["erreur"]
    assert not udir.exists() or not list(udir.iterdir())


def test_cle_inconnue_422(client, udir):
    v = valeurs(client)
    v["naming"] = {"x": 1}
    r = client.put("/api/profils/pla_aero", headers=H, json={"donnees": v})
    assert r.status_code == 422 and "naming" in r.json()["erreur"]
    assert not udir.exists() or not list(udir.iterdir())


def test_corps_invalide(client):
    assert client.put("/api/profils/pla_aero", headers=H, json={}).status_code == 422
    assert client.put("/api/profils/pla_aero", headers=H, json={"donnees": 3}).status_code == 422


def test_nouveau_profil_ecrit_tout(client, udir):
    v = valeurs(client)
    v["bambu"]["template"] = None
    v["material_settings"]["density"] = 1.1
    r = client.put("/api/profils/petg_test", headers=H, json={"donnees": v})
    assert r.status_code == 200, r.text
    ecrit = yaml.safe_load((udir / "petg_test.yaml").read_text())
    assert ecrit["material_settings"]["density"] == 1.1 and ecrit["slit_width"] == 0.1
    d = client.get("/api/profils/petg_test", headers=H).json()
    assert d["origine"] == "utilisateur" and d["embarque"] is False
    assert all(p["origine"] == "utilisateur" for p in d["provenance"])
    assert "petg_test" in [p["nom"] for p in client.get("/api/profils", headers=H).json()]


def test_nouveau_profil_vide_reste_absent(client, udir):
    r = client.put("/api/profils/vide", headers=H, json={"donnees": {}})
    assert r.status_code == 404  # rien d'écrit, donc toujours inconnu
    assert not (udir / "vide.yaml").exists()


def test_diff_vide_fichier_absent_reste_absent(client, udir):
    r = client.put("/api/profils/pla_aero", headers=H, json={"donnees": valeurs(client)})
    assert r.status_code == 200
    assert not (udir / "pla_aero.yaml").exists()


def test_diff_vide_fichier_existant_devient_mapping_vide(client, udir):
    (udir / "pla_aero.yaml").write_text("slit_width: 0.12\n")
    base = yaml.safe_load((embedded_dir() / "pla_aero.yaml").read_text())
    v = valeurs(client)
    v["slit_width"] = base["slit_width"]
    r = client.put("/api/profils/pla_aero", headers=H, json={"donnees": v})
    assert r.status_code == 200
    assert yaml.safe_load((udir / "pla_aero.yaml").read_text()) == {}
    assert r.json()["valeurs"]["slit_width"] == 0.1


def test_copier_comme_la_cli(client, udir):
    r = client.post("/api/profils/asa_aero/copier", headers=H)
    assert r.status_code == 201, r.text
    assert (udir / "asa_aero.yaml").is_file()
    assert "bambu" not in yaml.safe_load((udir / "asa_aero.yaml").read_text())
    assert r.json()["origine"] == "utilisateur"
    assert client.post("/api/profils/asa_aero/copier", headers=H).status_code == 409


def test_copier_sans_embarque_404(client):
    r = client.post("/api/profils/inconnu/copier", headers=H)
    assert r.status_code == 404


def test_copier_vers_nouveau_nom(client, udir):
    r = client.post("/api/profils/pla_aero/copier", headers=H, json={"vers": "mon_pla"})
    assert r.status_code == 201, r.text
    assert yaml.safe_load((udir / "mon_pla.yaml").read_text())["bambu"] == {"template": None}
    assert r.json()["nom"] == "mon_pla" and r.json()["valeurs"]["material_settings"]["density"] == 0.8
    assert "mon_pla" in client.get("/api/schema", headers=H).json()["matieres"]  # proposé aussitôt dans Réglages
    assert client.post("/api/profils/pla_aero/copier", headers=H, json={"vers": "mon_pla"}).status_code == 409
    assert client.post("/api/profils/pla_aero/copier", headers=H, json={"vers": "asa_aero"}).status_code == 409
    assert client.post("/api/profils/pla_aero/copier", headers=H, json={"vers": "Mauvais"}).status_code == 422
    assert client.post("/api/profils/pla_aero/copier", headers=H, json={"vers": 3}).status_code == 422


def test_ecriture_jamais_dans_le_paquet(client):
    avant = sorted(p.name for p in embedded_dir().iterdir())
    v = valeurs(client)
    v["slit_width"] = 0.13
    client.put("/api/profils/pla_aero", headers=H, json={"donnees": v})
    assert sorted(p.name for p in embedded_dir().iterdir()) == avant
    assert yaml.safe_load((embedded_dir() / "pla_aero.yaml").read_text())["slit_width"] == 0.1


def test_dossier_par_defaut_env(tmp_path, monkeypatch):
    d = tmp_path / "env_profils"
    d.mkdir()
    monkeypatch.setenv("AERORIB_PROFILES", str(d))
    with TestClient(create_app(tmp_path / "r", TOKEN), base_url="http://127.0.0.1") as c:
        v = c.get("/api/profils/pla_aero", headers=H).json()["valeurs"]
        v["slit_width"] = 0.13
        assert c.put("/api/profils/pla_aero", headers=H, json={"donnees": v}).status_code == 200
    assert (d / "pla_aero.yaml").is_file()


def test_dossier_par_defaut_cwd(tmp_path, monkeypatch):
    monkeypatch.delenv("AERORIB_PROFILES", raising=False)
    monkeypatch.chdir(tmp_path)
    with TestClient(create_app(tmp_path / "r", TOKEN), base_url="http://127.0.0.1") as c:
        c.post("/api/profils/asa_aero/copier", headers=H)
    assert (tmp_path / "profiles" / "asa_aero.yaml").is_file()


def test_fichier_utilisateur_illisible_422_sans_chemin(client, udir):
    (udir / "pla_aero.yaml").write_text("a: [unclosed\n")
    r = client.get("/api/profils/pla_aero", headers=H)
    assert r.status_code == 422 and str(udir) not in r.text


@pytest.mark.parametrize(
    "methode,chemin,corps",
    [
        ("get", "/api/profils", None),
        ("get", "/api/profils/pla_aero", None),
        ("put", "/api/profils/pla_aero", {"donnees": {}}),
        ("post", "/api/profils/pla_aero/copier", None),
    ],
)
def test_sans_jeton_401(client, methode, chemin, corps):
    r = getattr(client, methode)(chemin, **({"json": corps} if corps is not None else {}))
    assert r.status_code == 401


def test_methode_non_autorisee(client):
    assert client.delete("/api/profils/pla_aero", headers=H).status_code == 405


def _round_trip(client, udir, nom, contenu):
    f = udir / f"{nom}.yaml"
    f.write_text(contenu)
    avant = f.read_bytes()
    v = client.get(f"/api/profils/{nom}", headers=H).json()["valeurs"]
    r = client.put(f"/api/profils/{nom}", headers=H, json={"donnees": v})
    assert r.status_code == 200, r.text
    return avant, f


@pytest.mark.parametrize(
    "tpl", ["../tpl/mine.3mf", "/opt/x/mine.3mf", "p1s_pla_aero.3mf", "mine.3mf"]
)
def test_aller_retour_gabarit_utilisateur_intact(client, udir, tpl):
    _, f = _round_trip(client, udir, "pla_aero", f"bambu: {{template: {tpl}}}\n")
    assert yaml.safe_load(f.read_text()) == {"bambu": {"template": tpl}}


def test_aller_retour_sans_gabarit_utilisateur(client, udir):
    _, f = _round_trip(client, udir, "pla_aero", "slit_width: 0.12\n")
    assert yaml.safe_load(f.read_text()) == {"slit_width": 0.12}


def test_put_ne_change_pas_le_gabarit(client, udir):
    (udir / "asa_aero.yaml").write_text("bambu: {template: ../tpl/mine.3mf}\n")
    v = valeurs(client, "asa_aero")
    v["bambu"]["template"] = "autre.3mf"
    assert client.put("/api/profils/asa_aero", headers=H, json={"donnees": v}).status_code == 200
    assert yaml.safe_load((udir / "asa_aero.yaml").read_text()) == {"bambu": {"template": "../tpl/mine.3mf"}}
    # sans gabarit utilisateur au départ : toujours rien d'écrit pour lui
    v = valeurs(client, "pla_aero")
    v["bambu"]["template"] = "autre.3mf"
    client.put("/api/profils/pla_aero", headers=H, json={"donnees": v})
    assert not (udir / "pla_aero.yaml").exists()


def test_entiers_equivalent_aux_flottants(client, udir):
    v = valeurs(client)
    assert v["split"]["min_rib_depth"] == 1.0
    v["split"]["min_rib_depth"] = 1  # ce que renvoie JSON.stringify
    v["passages"]["max_tilt_deg"] = 5
    v["spars"]["default"]["strip"] = 2
    r = client.put("/api/profils/pla_aero", headers=H, json={"donnees": v})
    assert r.status_code == 200
    assert not (udir / "pla_aero.yaml").exists()
    v["split"]["gap"] = 1
    client.put("/api/profils/pla_aero", headers=H, json={"donnees": v})
    assert yaml.safe_load((udir / "pla_aero.yaml").read_text()) == {"split": {"gap": 1}}


def test_memes_couches_que_le_coeur(client, udir, tmp_path):
    from aerorib.material_profiles import load_profile

    cwd_profiles = tmp_path / "profiles"
    cwd_profiles.mkdir()
    (cwd_profiles / "pla_aero.yaml").write_text("slit_width: 0.2\nmaterial_settings: {density: 0.9}\n")
    (udir / "pla_aero.yaml").write_text("material_settings: {density: 1.0}\n")
    d = client.get("/api/profils/pla_aero", headers=H).json()
    coeur, _ = load_profile("pla_aero")
    assert d["valeurs"]["slit_width"] == coeur["slit_width"] == 0.2
    assert d["valeurs"]["material_settings"]["density"] == coeur["material_settings"]["density"] == 1.0
    prov = {tuple(p["chemin"]): p["origine"] for p in d["provenance"]}
    assert prov[("slit_width",)] == "utilisateur"
    assert prov[("material_settings", "layer_height")] == "embarqué"
    # revenir à la valeur embarquée (0.1) : la couche ./profiles reprendrait la main, donc elle est écrite
    v = d["valeurs"]
    v["slit_width"] = 0.1
    assert client.put("/api/profils/pla_aero", headers=H, json={"donnees": v}).status_code == 200
    assert yaml.safe_load((udir / "pla_aero.yaml").read_text())["slit_width"] == 0.1
    assert load_profile("pla_aero")[0]["slit_width"] == 0.1
    # valeur égale à celle de ./profiles : non écrite
    v["material_settings"]["density"] = 0.9
    client.put("/api/profils/pla_aero", headers=H, json={"donnees": v})
    assert "material_settings" not in yaml.safe_load((udir / "pla_aero.yaml").read_text())
    assert load_profile("pla_aero")[0]["material_settings"]["density"] == 0.9
