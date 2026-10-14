"""Compatibilité du renommage AeroRib → BEAVER : variables d'environnement, en-tête du jeton, alias de commande."""
from beaver.env import lire_env


def test_env_beaver_prioritaire(monkeypatch):
    monkeypatch.setenv("AERORIB_ESSAI", "ancien")
    assert lire_env("ESSAI") == "ancien"
    monkeypatch.setenv("BEAVER_ESSAI", "nouveau")
    assert lire_env("ESSAI") == "nouveau"


def test_env_absente(monkeypatch):
    monkeypatch.delenv("BEAVER_ESSAI", raising=False)
    monkeypatch.delenv("AERORIB_ESSAI", raising=False)
    assert lire_env("ESSAI") is None


def test_ancien_entete_jeton_accepte(tmp_path):
    from fastapi.testclient import TestClient

    from beaver.gui.server import create_app

    c = TestClient(create_app(tmp_path, "jeton"), base_url="http://127.0.0.1")
    assert c.get("/api/sante", headers={"X-AeroRib-Token": "jeton"}).status_code == 200
    assert c.get("/api/sante", headers={"X-Beaver-Token": "jeton"}).status_code == 200
    assert c.get("/api/sante", headers={"X-AeroRib-Token": "faux"}).status_code == 401
