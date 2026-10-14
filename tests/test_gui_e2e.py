"""Bout en bout par l'API : aile Fusion réelle (v2), aperçu puis génération complète, config par défaut."""

import multiprocessing
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from beaver import preview
from beaver.gui.server import create_app

FUSION_V2 = Path(__file__).parent / "fixtures" / "fusion_aile_4412_simple_sbar_v2.step"
SERVO = Path(__file__).parent / "fixtures" / "aile_4412_servo_extrados.step"
TOKEN = "jeton-test"
H = {"X-Beaver-Token": TOKEN}
TIMEOUT = 15 * 60  # la machine peut être chargée


@pytest.mark.slow
def test_aile_fusion_v2_apercu_et_generation(tmp_path, monkeypatch):
    (tmp_path / "profils").mkdir()
    monkeypatch.setenv("BEAVER_PROFILES", str(tmp_path / "profils"))
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
    monkeypatch.setenv("BEAVER_PROFILES", str(tmp_path / "profils"))
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


@pytest.mark.slow
def test_aile_large_decoupe_visible_dans_l_apercu_puis_generation(tmp_path, monkeypatch):
    """Aile synthétique à grande corde (P1S) : l'aperçu montre les deux morceaux, le plan de coupe dans la corde et la
    décision automatique ; la génération donne le même plan (essai ok, 2 morceaux, notice présente)."""
    from beaver.io_step import write_step
    from beaver.testing.synthetic import wide_wing_bodies

    (tmp_path / "profils").mkdir()
    monkeypatch.setenv("BEAVER_PROFILES", str(tmp_path / "profils"))
    step = write_step(tmp_path / "large.step", wide_wing_bodies())
    rapide = {"material_settings": {"layer_height": 1.0}, "rib_layers": [{"spacing": 20}]}
    preview.clear_cache()
    app = create_app(tmp_path / "projets", TOKEN)
    c = TestClient(app, base_url="http://127.0.0.1")
    try:
        r = c.post("/api/projets", headers=H, data={"nom": "large"}, files={"fichier": ("large.step", step.read_bytes())})
        assert r.status_code == 201, r.text
        r = c.put("/api/projets/large/config", headers=H, json={"donnees": rapide})
        assert r.status_code == 200, r.text

        r = c.post("/api/projets/large/apercu", headers=H, json={"config": rapide, "z": None, "attendre": True})
        assert r.status_code == 200, r.text
        d = r.json()
        pf = d["planform"]
        assert [(m["nom"], m["plateau"], m["tient"]) for m in pf["morceaux"]] == [("A", 1, True), ("B", 2, True)]
        (plan,) = pf["plans_corde"]
        assert plan["evite"] == ["SBAR_8"] and d["estimates"]["morceaux"] == 2
        (notice,) = d["notices"]
        assert notice.startswith("découpe automatique : coupe dans la corde en 2 morceaux")
        assert notice not in d["avertissements"]

        r = c.post("/api/projets/large/generations", headers=H)
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
        resume = c.get(f"/api/projets/large/essais/{st['essai']}", headers=H).json()["resume"]
        assert resume["statut"] == "ok" and resume["morceaux"] == 2 and resume["troncons"] == 1
        assert resume["notices"] == [f"Aile_large : {notice}"]  # même décision que l'aperçu
        assert resume["nervures"] == d["estimates"]["plaques"]
        (corps,) = resume["corps"]
        assert [(m["nom"], m["plateau"], m["ok"]) for m in corps["liste_morceaux"]] == [("A", 1, True), ("B", 2, True)]
        for m, vu in zip(corps["liste_morceaux"], pf["morceaux"]):
            assert m["rotation"] == pytest.approx(vu["rotation"], abs=1.0)
        rapport = (tmp_path / "projets" / "large" / "essais" / st["essai"] / resume["rapport"]).read_text(encoding="utf-8")
        assert "Morceaux imprimés" in rapport and "coupe dans la corde en 2 morceaux" in rapport
        assert {"large-a.stl", "large-b.stl", "large.3mf"} <= set(resume["fichiers"])
    finally:
        app.state.travaux.shutdown()
        preview.clear_cache()
        for p in multiprocessing.active_children():
            p.kill()
            p.join(5)
    assert not multiprocessing.active_children()
