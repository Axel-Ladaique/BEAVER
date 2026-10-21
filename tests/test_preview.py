"""Aperçu rapide sans booléens (beaver.preview) comparé à la génération réelle sur l'aile synthétique."""

import io
import json
import math
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import trimesh
from shapely.ops import unary_union

from beaver import pipeline, preview
from beaver.config import load_config
from beaver.features import ribs
from beaver.io_step import write_step
from beaver.pipeline import generate_skin
from beaver.slicing import slice_layers
from beaver.testing.synthetic import wing_x_bodies
from beaver.validate import validate

FIXTURE = Path(__file__).parent / "fixtures" / "bambu_p1s_pla_aero.3mf"
Z = 40.5  # mi-hauteur, au centre d'une couche de 1 mm


def _cfg(**over):
    data = {
        "material_settings": {"layer_height": 1.0},
        "rib_layers": [{"spacing": 20}],
        "passages": {"mode": "link"},
        "bambu": {"template": str(FIXTURE)},
        "segment": {"max_height": 30},
    }
    data.update(over)
    return load_config(overrides=data)


@pytest.fixture(scope="module")
def step(tmp_path_factory):
    return write_step(tmp_path_factory.mktemp("apercu") / "aile.step", wing_x_bodies())


@pytest.fixture(scope="module")
def prep(step):
    preview.clear_cache()
    return preview.prepare(step, _cfg())


@pytest.fixture(scope="module")
def plates(prep):
    return preview.plates_2d(prep, _cfg())


@pytest.fixture(scope="module")
def real(prep):
    """Génération réelle (booléens) sur la même peau orientée."""
    cfg = _cfg()
    gen = generate_skin(prep.skin, list(prep.spars), cfg)
    report = validate(gen.result, cfg)
    return gen, report


def _area(polys):
    return sum(p.area for p in polys)


def test_prepare_is_cached(step, prep):
    t0 = time.perf_counter()
    again = preview.prepare(step, _cfg(rib_layers=[{"spacing": 35}]))  # réglage sans effet sur la préparation
    assert time.perf_counter() - t0 < 0.05
    assert again is prep
    assert preview.prepare_key(_cfg()) != preview.prepare_key(_cfg(split={"grid": [51, 11]}))
    assert preview.prepare_key(_cfg()) != preview.prepare_key(_cfg(passages={"mode": "fill"}))


def test_prepare_contents(prep):
    d = prep.to_dict()
    json.dumps(d)
    assert d["corps"] == "Aile_test"
    z0, z1 = d["boite"][2], d["boite"][5]
    assert z0 == pytest.approx(0.0, abs=1e-3) and z1 == pytest.approx(80.0, abs=1e-3)
    assert sorted(b["type"] for b in d["trous"]) == ["longeron", "passage relié"]
    assert prep.mid.ymid.shape == (101, 22)


def test_plates_2d(prep, plates):
    cfg = _cfg()
    assert plates and all(p.layer == 0 and p.layer_name == "principal" for p in plates)
    assert {p.family for p in plates} == {1, -1}
    ang = math.radians(cfg.rib_angle(cfg.rib_layers[0]))
    for p in plates:
        assert p.d1 == pytest.approx(math.cos(ang)) and abs(p.d0) == pytest.approx(math.sin(ang))
        assert p.depth == "full"
    json.dumps([p.to_dict() for p in plates])


def test_plates_count_matches_generation(plates, real):
    gen, _ = real
    assert len(plates) == gen.plates


def test_section_matches_real_layer(prep, plates, real):
    cfg = _cfg()
    gen, _ = real
    view = preview.section(prep, plates, cfg, Z)
    (layer,) = slice_layers(gen.result, [Z], cfg.validation.tessellation_tol)
    approx = view.polygons
    assert len(approx) == len(layer.polygons)
    assert all(not p.interiors for p in approx) and all(not p.interiors for p in layer.polygons)
    assert _area(approx) == pytest.approx(_area(layer.polygons), rel=0.05)
    real_union = unary_union(layer.polygons)
    approx_union = unary_union(approx)
    cutting = [f for f in view.footprints]
    assert cutting, "aucune plaque ne coupe la mi-hauteur"
    skin = unary_union(view.skin)
    for f in cutting:
        poly = f.polygon
        inside = poly.intersection(skin).area
        if inside < 1e-3:
            continue
        # encoche : l'empreinte de la plaque est vide de matière, dans l'aperçu comme dans la vraie couche
        assert approx_union.intersection(poly).area < 0.05 * inside
        assert real_union.intersection(poly).area < 0.2 * inside
    json.dumps(view.to_dict())


def test_section_reports_defects_and_band(prep, plates):
    cfg = _cfg()
    view = preview.section(prep, plates, cfg, Z)
    d = view.to_dict()
    assert d["z"] == Z and d["defauts"] == []  # aile synthétique imprimable
    assert len(d["bande"]) > 10
    assert {h["type"] for h in d["trous"]} == {"longeron", "passage relié"}


def test_section_flags_isolated_passage(step):
    cfg = _cfg(passages={"mode": "off"})
    prep = preview.prepare(step, cfg)
    view = preview.section(prep, preview.plates_2d(prep, cfg), cfg, Z)
    holes = [d for d in view.to_dict()["defauts"] if d["check"] == "connectivity"]
    assert holes, view.to_dict()["defauts"]
    x0, _, x1, _ = holes[0]["bounds"]
    assert x1 - x0 == pytest.approx(2.4, abs=0.1)  # le passage Ø2,4 laissé isolé


def test_section_fill_mode_closes_passage(step):
    cfg = _cfg(passages={"mode": "fill"})
    prep = preview.prepare(step, cfg)
    view = preview.section(prep, [], cfg, Z)
    assert {h["type"] for h in view.to_dict()["trous"]} == {"longeron", "passage rebouché"}
    (poly,) = [p for p in view.polygons if p.area > 1]
    # x = 70 : plus de trou de passage dans la coupe (rebouché), seul le longeron reste relié par sa fente
    assert not poly.interiors


def test_planform(prep, plates):
    cfg = _cfg()
    view = preview.planform(prep, plates, cfg)
    ang = cfg.rib_angle(cfg.rib_layers[0])
    angles = set()
    for seg in view.segments:
        dx, dz = seg.x1 - seg.x0, seg.z1 - seg.z0
        assert dz > 0
        angles.add(round(math.degrees(math.atan2(dx, dz)), 6))
        assert -1e-6 <= seg.z0 and seg.z1 <= 80 + 1e-6
    assert angles == {round(ang, 6), round(-ang, 6)}
    assert view.wing[1] == pytest.approx(0.0, abs=1e-3) and view.wing[3] == pytest.approx(80.0, abs=1e-3)
    assert view.cuts == pytest.approx([80 / 3, 160 / 3], abs=1e-3)
    # trous vus de dessus (spec « Écrans » §3 : le longeron) : étendue z × diamètre, repère orienté
    trous = view.to_dict()["trous"]
    assert sorted(t["type"] for t in trous) == ["longeron", "passage relié"]
    for t in trous:
        b = next(b for b in prep.bores if b.name == t["nom"])
        assert t["x"] == pytest.approx(b.x, abs=1e-3) and t["r"] == pytest.approx(b.radius, abs=1e-3)
        assert view.wing[0] < t["x"] < view.wing[2] and t["r"] > 0
        assert -1e-6 <= t["z0"] < t["z1"] <= 80 + 1e-6
    longeron = next(t for t in trous if t["type"] == "longeron")
    assert longeron["manchon"] > longeron["r"]  # enveloppe trou + manchon
    json.dumps(view.to_dict())


def test_planform_passage_rebouche(step):
    cfg = _cfg(passages={"mode": "fill"})
    prep = preview.prepare(step, cfg)
    trous = preview.planform(prep, [], cfg).to_dict()["trous"]
    assert sorted(t["type"] for t in trous) == ["longeron", "passage rebouché"]
    f = next(t for t in trous if t["type"] == "passage rebouché")
    assert f["z0"] < f["z1"] and f["r"] > 0


def test_estimates(prep, plates, real):
    cfg = _cfg()
    gen, report = real
    est = preview.estimates(prep, plates, cfg)
    json.dumps(est)
    assert est["plaques"] == gen.plates
    assert est["plaques_par_couche"] == [{"indice": 0, "nom": "principal", "plaques": gen.plates}]
    assert est["troncons"] == pytest.approx([80 / 3] * 3, abs=1e-3)
    assert est["trajet_m"] == pytest.approx(report.stats["path_length_m"], rel=0.10)
    assert est["masse_g"] == pytest.approx(report.stats["mass_g"], rel=0.10)
    assert est["duree_s"] == pytest.approx(gen.plates * preview.SECONDS_PER_PLATE + prep.prepare_s)
    # préparation servie par le cache : sa durée n'est pas recomptée
    cached = preview.estimates(prep, plates, cfg, cached=True)
    assert cached["duree_s"] == pytest.approx(gen.plates * preview.SECONDS_PER_PLATE)
    assert cached["preparation_s"] == prep.prepare_s


def test_estimates_per_layer_with_same_names(step, monkeypatch):
    # deux couches sans nom (toutes deux « principal ») : une pleine, une peu profonde sur une partie des mêmes plans
    cfg = _cfg(rib_layers=[{"spacing": 20}, {"spacing": 10, "depth": 2}])
    prep = preview.prepare(step, cfg)
    est = preview.estimates(prep, preview.plates_2d(prep, cfg), cfg)
    real_counts = []

    def spy(*a, **k):
        out = ribs.diamond_plates(*a, **k)
        real_counts.append(len(out))  # un appel par couche, dans l'ordre de rib_layers
        return out

    monkeypatch.setattr(pipeline, "diamond_plates", spy)
    gen = generate_skin(prep.skin, list(prep.spars), cfg)
    assert [c["nom"] for c in est["plaques_par_couche"]] == ["principal", "principal"]
    assert [c["indice"] for c in est["plaques_par_couche"]] == [0, 1]
    assert [c["plaques"] for c in est["plaques_par_couche"]] == real_counts
    assert all(n > 0 for n in real_counts)
    assert est["plaques"] == gen.plates == sum(real_counts)


def test_prepare_shares_inflight_work(step, monkeypatch):
    preview.clear_cache()
    calls = []
    real_prepare = preview._prepare

    def counting(*a, **k):
        calls.append(1)
        time.sleep(0.3)  # laisse les autres threads arriver pendant le calcul
        return real_prepare(*a, **k)

    monkeypatch.setattr(preview, "_prepare", counting)
    cfg = _cfg(split={"grid": [41, 9]})
    with ThreadPoolExecutor(6) as pool:
        results = list(pool.map(lambda _: preview.prepare_status(step, cfg), range(6)))
    assert len(calls) == 1
    assert len({id(p) for p, _ in results}) == 1
    assert sorted(hit for _, hit in results) == [False] + [True] * 5


def test_prepare_failure_is_not_cached(step, monkeypatch):
    preview.clear_cache()
    calls = []

    def failing(*a, **k):
        calls.append(1)
        raise preview.PreviewError("échec simulé")

    monkeypatch.setattr(preview, "_prepare", failing)
    cfg = _cfg(split={"grid": [41, 9]})
    for _ in range(2):
        with pytest.raises(preview.PreviewError, match="échec simulé"):
            preview.prepare(step, cfg)
    assert len(calls) == 2  # pas d'échec mémorisé : chaque appel réessaie


def test_prepare_missing_file(tmp_path):
    with pytest.raises(preview.PreviewError, match="introuvable"):
        preview.prepare(tmp_path / "absent.step", _cfg())


def test_plates_mesh(prep, plates):
    data = preview.plates_mesh(prep, plates)
    mesh = trimesh.load(io.BytesIO(data), file_type="stl", force="mesh")
    assert len(mesh.faces) > 0
    with pytest.raises(preview.PreviewError, match="600"):
        preview.plates_mesh(prep, plates * (preview.MAX_MESH_PLATES // len(plates) + 1))


def test_skin_mesh(prep):
    mesh = trimesh.load(io.BytesIO(preview.skin_mesh(prep)), file_type="stl", force="mesh")
    assert len(mesh.faces) > 0
    assert mesh.bounds[1][2] == pytest.approx(80.0, abs=1e-2)


def test_walls_removing_every_plate_warn_in_the_preview(step, monkeypatch):
    import numpy as np

    monkeypatch.setattr(ribs, "steep_samples", lambda mid, x, z, **k: np.ones(len(x), dtype=bool))
    cfg = _cfg()
    prep = preview.prepare(step, cfg)
    plates = preview.plates_2d(prep, cfg)
    assert plates == []
    est = preview.estimates(prep, plates, cfg)
    assert [w for w in est["avertissements"] if "parois" in w and "principal" in w]


def test_no_wall_warning_when_plates_remain(prep, plates):
    est = preview.estimates(prep, plates, _cfg())
    assert not [w for w in est["avertissements"] if "parois" in w]


def test_estimates_hollow_gain(step):
    """Évidements : chiffres de la pièce évidée et, pour comparaison, sans évidement (profils seulement)."""
    plein = _cfg(rib_layers=[{"spacing": 30}])
    evide = _cfg(rib_layers=[{"spacing": 30}], ribs_hollow={"mode": "croisements"})
    prep = preview.prepare(step, evide)
    off = preview.estimates(prep, preview.plates_2d(prep, plein), plein)
    assert off["evidements"] is None
    est = preview.estimates(prep, preview.plates_2d(prep, evide), evide)
    json.dumps(est)
    g = est["evidements"]
    assert g["resume"] == "ponts à 1 croisement sur 3, ponts 4 mm, 20 % de la demi-épaisseur"
    assert est["masse_g"] < g["masse_sans_g"] and est["trajet_m"] < g["trajet_sans_m"]
    assert g["masse_sans_g"] == pytest.approx(off["masse_g"]) and g["trajet_sans_m"] == pytest.approx(off["trajet_m"])
    assert g["gain_masse"] == pytest.approx(1 - est["masse_g"] / off["masse_g"]) and g["gain_masse"] > 0.02
    assert g["gain_trajet"] == pytest.approx(1 - est["trajet_m"] / off["trajet_m"])
