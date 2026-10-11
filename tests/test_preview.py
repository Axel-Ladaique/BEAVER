"""Aperçu rapide sans booléens (aerorib.preview) comparé à la génération réelle sur l'aile synthétique."""

import io
import json
import math
import time
from pathlib import Path

import pytest
import trimesh
from shapely.ops import unary_union

from aerorib import preview
from aerorib.config import load_config
from aerorib.io_step import write_step
from aerorib.pipeline import generate_skin
from aerorib.slicing import slice_layers
from aerorib.testing.synthetic import wing_x_bodies
from aerorib.validate import validate

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
    json.dumps(view.to_dict())


def test_estimates(prep, plates, real):
    cfg = _cfg()
    gen, report = real
    est = preview.estimates(prep, plates, cfg)
    json.dumps(est)
    assert est["plaques"] == {"principal": gen.plates}
    assert est["troncons"] == pytest.approx([80 / 3] * 3, abs=1e-3)
    assert est["trajet_m"] == pytest.approx(report.stats["path_length_m"], rel=0.10)
    assert est["masse_g"] == pytest.approx(report.stats["mass_g"], rel=0.10)
    assert est["duree_s"] >= gen.plates * preview.SECONDS_PER_PLATE


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
