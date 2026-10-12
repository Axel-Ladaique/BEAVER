"""Bout en bout par l'API : aile Fusion réelle (v2), aperçu puis génération complète, config par défaut."""

import multiprocessing
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aerorib import preview
from aerorib.gui.server import create_app

FUSION_V2 = Path(__file__).parent / "fixtures" / "fusion_aile_4412_simple_sbar_v2.step"
SERVO = Path(__file__).parent / "fixtures" / "aile_4412_servo_extrados.step"
TOKEN = "jeton-test"
H = {"X-AeroRib-Token": TOKEN}
TIMEOUT = 15 * 60  # la machine peut être chargée


@pytest.mark.slow
def test_aile_fusion_v2_apercu_et_generation(tmp_path, monkeypatch):
    (tmp_path / "profils").mkdir()
    monkeypatch.setenv("AERORIB_PROFILES", str(tmp_path / "profils"))
    preview.clear_cache()
    app = create_app(tmp_path / "projets", TOKEN)
    c = TestClient(app, base_url="http://127.0.0.1")
    try:
        r = c.post("/api/projets", headers=H, data={"nom": "aile"},
                   files={"fichier": ("aile.step", FUSION_V2.read_bytes())})
        assert r.status_code == 201, r.text

        r = c.post("/api/projets/aile/apercu", headers=H, json={"config": {}, "z": None, "attendre": True})
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["estimates"]["plaques"] == 84
        assert d["section"] and d["section"]["polygones"]

        r = c.post("/api/projets/aile/generations", headers=H)
        assert r.status_code == 202, r.text
        job = r.json()["id"]
        t0 = time.monotonic()
        while True:
            st = c.get(f"/api/generations/{job}", headers=H).json()
            if st["statut"] != "en cours":
                break
            assert time.monotonic() - t0 < TIMEOUT, "génération trop longue"
            time.sleep(1)
        assert st["statut"] == "ok", st

        essai = st["essai"]
        det = c.get(f"/api/projets/aile/essais/{essai}", headers=H).json()
        assert det["resume"]["statut"] == "ok"
        d_essai = tmp_path / "projets" / "aile" / "essais" / essai
        for ext in ("step", "stl", "3mf"):
            nom = f"aile.{ext}"
            assert nom in det["resume"]["fichiers"] and (d_essai / nom).is_file(), nom
        assert "aile-rapport.html" in det["resume"]["fichiers"] and (d_essai / "aile-rapport.html").is_file()
    finally:
        app.state.travaux.shutdown()
        preview.clear_cache()
        for p in multiprocessing.active_children():
            p.kill()
            p.join(5)
    assert not multiprocessing.active_children()


@pytest.mark.slow
def test_aile_servo_apercu_et_generation(tmp_path, monkeypatch):
    """Fixture servo : la baie est dans la vue de dessus de l'aperçu, puis listée dans l'essai (statut ok)."""
    (tmp_path / "profils").mkdir()
    monkeypatch.setenv("AERORIB_PROFILES", str(tmp_path / "profils"))
    preview.clear_cache()
    app = create_app(tmp_path / "projets", TOKEN)
    c = TestClient(app, base_url="http://127.0.0.1")
    try:
        r = c.post("/api/projets", headers=H, data={"nom": "servo"},
                   files={"fichier": ("servo.step", SERVO.read_bytes())})
        assert r.status_code == 201, r.text

        r = c.post("/api/projets/servo/apercu", headers=H, json={"config": {}, "z": None, "attendre": True})
        assert r.status_code == 200, r.text
        d = r.json()
        (baie,) = d["planform"]["baies"]
        assert baie["nom"] == "SERVO_1" and baie["cote"] == "extrados" and baie["renfort"] is True
        assert baie["ouverture"][0] < baie["z0"] < baie["z1"] < baie["ouverture"][1]
        assert any(t["type"] == "gaine" for t in d["planform"]["trous"])
        plaques = d["estimates"]["plaques"]

        r = c.post("/api/projets/servo/generations", headers=H)
        assert r.status_code == 202, r.text
        job = r.json()["id"]
        t0 = time.monotonic()
        while True:
            st = c.get(f"/api/generations/{job}", headers=H).json()
            if st["statut"] != "en cours":
                break
            assert time.monotonic() - t0 < TIMEOUT, "génération trop longue"
            time.sleep(1)
        assert st["statut"] == "ok", st
        det = c.get(f"/api/projets/servo/essais/{st['essai']}", headers=H).json()
        resume = det["resume"]
        assert resume["statut"] == "ok" and resume["nervures"] == plaques  # aperçu = génération
        (corps,) = resume["corps"]
        assert corps["baies"].startswith("SERVO_1 (baie extrados") and "gaine" in corps["trous"]
        rapport = tmp_path / "projets" / "servo" / "essais" / st["essai"] / resume["rapport"]
        assert "SERVO_1 (baie extrados" in rapport.read_text(encoding="utf-8")
    finally:
        app.state.travaux.shutdown()
        preview.clear_cache()
        for p in multiprocessing.active_children():
            p.kill()
            p.join(5)
    assert not multiprocessing.active_children()
