from pathlib import Path

import pytest
from shapely.geometry import LineString, Polygon

from aerorib.classify import classify
from aerorib.config import load_config
from aerorib.io_step import read_step
from aerorib.pipeline import run_model
from aerorib.slicing import slice_layers

FUSION = Path(__file__).parent / "fixtures" / "fusion_aile_4412_simple_sbar.step"
FUSION_V2 = Path(__file__).parent / "fixtures" / "fusion_aile_4412_simple_sbar_v2.step"
RECTANGLE = Path(__file__).parent / "fixtures" / "aile_rectangle_2sbar.step"
DOUBLES = Path(__file__).parents[1] / "examples" / "config-doubles-nervures.yaml"


def _vertical(poly, x):
    cut = poly.intersection(LineString([(x, -1e3), (x, 1e3)]))
    segs = [] if cut.is_empty else list(getattr(cut, "geoms", [cut]))
    return sorted((g.bounds[1], g.bounds[3]) for g in segs)


@pytest.mark.slow
def test_real_fusion_wing_is_printable():
    cfg = load_config()
    run = run_model(classify(read_step(FUSION), cfg), cfg)
    assert run.orientation.source == "longeron SBAR_14mm_1"
    (body,) = run.bodies
    assert body.report.ok, [i.message for i in body.report.errors][:5]
    assert body.report.stats["layers"] == 1050
    (spar,) = body.bores  # le longeron seul : les 4 alésages Ø2,4 parasites sont rebouchés
    assert spar.cut_hole and spar.anchor_gap == 1.0
    assert len(body.filled) == 4 and all(f.r == pytest.approx(1.2, abs=0.01) for f in body.filled)
    assert body.plates > 50
    # attache présente à mi-envergure : sous le trou, seul reste le pont de 1 mm contre la peau opposée
    bb = body.result.bounding_box()
    (layer,) = slice_layers(body.result, [(bb.min.Z + bb.max.Z) / 2])
    (poly,) = layer.polygons
    assert not poly.interiors
    opposite = (lambda s: s[1] < spar.y - spar.radius - 0.1) if spar.side > 0 else (
        lambda s: s[0] > spar.y + spar.radius + 0.1
    )
    (bridge,) = [s for s in _vertical(poly, spar.x) if opposite(s)]
    skin = _vertical(Polygon(poly.exterior), spar.x)
    assert bridge[0 if spar.side > 0 else 1] == pytest.approx(skin[0][0] if spar.side > 0 else skin[-1][1], abs=1e-3)
    assert bridge[1] - bridge[0] == pytest.approx(1.0, abs=0.2)


@pytest.mark.slow
def test_real_wing_v2_double_ribs():
    import time

    cfg = load_config(DOUBLES)
    stamps = []
    t0 = time.perf_counter()
    run = run_model(
        classify(read_step(FUSION_V2), cfg), cfg, progress=lambda m: stamps.append((time.perf_counter() - t0, m))
    )
    total = time.perf_counter() - t0
    (body,) = run.bodies
    assert body.report.ok, [i.message for i in body.report.errors][:5]
    (spar,) = body.bores
    assert spar.cut_hole and body.filled == ()  # v2 : aucun alésage parasite
    assert body.plates > 200  # deux réseaux : bien plus que le seul réseau principal (~50–60)
    gen = next(t for t, m in stamps if "validation" in m) - next(t for t, m in stamps if "surface médiane" in m)
    print(
        f"\naile v2 doubles nervures : {body.plates} plaques, génération (surface médiane + coupe) {gen:.0f} s, "
        f"total {total:.0f} s, {len(body.report.warnings)} avertissement(s)"
    )
    for w in body.report.warnings:
        print(f"  {w.check} z={w.z:.1f} {tuple(round(v, 2) for v in w.bounds)}")
    # réseau de peau présent à mi-envergure : encoches de ~3 mm ouvertes sur la peau
    bb = body.result.bounding_box()
    z = (bb.min.Z + bb.max.Z) / 2
    (layer,) = slice_layers(body.result, [z])
    (poly,) = layer.polygons
    (orig,) = slice_layers(body.original, [z])
    skin = Polygon(orig.polygons[0].exterior)
    depths = []
    x = bb.min.X
    while x < bb.max.X:
        outer, cols = _vertical(skin, x), _vertical(poly, x)
        if outer and cols and outer[-1][1] - cols[-1][1] > 0.1:
            depths.append(outer[-1][1] - cols[-1][1])
        x += 0.02
    assert any(abs(d - 3.0) <= 0.3 for d in depths), sorted(depths)


def _bambu_wing(tmp_path, overrides):
    """Aile v2 réelle, config par défaut (+ overrides) : run, export STEP/STL/3MF, plateaux lus dans le 3MF."""
    import zipfile
    import xml.etree.ElementTree as ET

    from aerorib.bambu import read_template
    from aerorib.config import resolve_template
    from aerorib.export import export_results

    cfg = load_config(overrides=overrides)
    run = run_model(classify(read_step(FUSION_V2), cfg), cfg)
    (body,) = run.bodies
    assert body.report.ok, [i.message for i in body.report.errors][:5]
    paths = export_results(run, tmp_path, "aile", ["stl", "3mf"], template=read_template(resolve_template(cfg)))
    with zipfile.ZipFile(tmp_path / "aile.3mf") as z:
        plates = ET.fromstring(z.read("Metadata/model_settings.config")).findall("plate")
    return body, paths, plates


@pytest.mark.slow
def test_real_wing_v2_bambu_one_segment(tmp_path):
    body, paths, plates = _bambu_wing(tmp_path, {})
    assert len(body.segments) == 1 and len(plates) == 1  # 210 mm <= 245 mm
    (seg,) = body.segments
    assert seg.is_valid and len(seg.solids()) == 1
    assert sorted(p.name for p in paths) == ["aile-rapport.html", "aile.3mf", "aile.stl"]


@pytest.mark.slow
def test_real_wing_v2_bambu_two_segments(tmp_path):
    body, paths, plates = _bambu_wing(tmp_path, {"segment": {"max_height": 120}})
    assert len(body.segments) == 2 and len(plates) == 2
    heights = []
    for seg in body.segments:
        assert seg.is_valid and len(seg.solids()) == 1
        bb = seg.bounding_box()
        assert bb.min.Z == pytest.approx(0.0, abs=1e-3)
        heights.append(bb.max.Z)
    # coupe nominale à 105 mm = croisement des plaques (pas 30, 45° : tous les 15 mm) : écartée de 0,5 mm
    assert sorted(heights) == pytest.approx([104.5, 105.5], abs=0.01)
    assert sorted(p.name for p in paths) == ["aile-rapport.html", "aile-t1.stl", "aile-t2.stl", "aile.3mf"]


@pytest.mark.slow
def test_blunt_edged_wing_is_printable():
    # profil « boîte arrondie » (~20 mm d'épaisseur au nez, bords quasi verticaux), deux longerons : les plaques
    # s'arrêtent avant les parois raides au lieu de les raser (peau entamée, îlots, matière trop fine)
    cfg = load_config()
    run = run_model(classify(read_step(RECTANGLE), cfg), cfg)
    (body,) = run.bodies
    assert body.report.ok, [(i.z, i.message) for i in body.report.errors][:10]
    assert not body.report.errors
    assert len(body.bores) == 2
    assert body.plates > 40


DOUBLE_SBAR = Path(__file__).parent / "fixtures" / "aile_4412_double_sbar.step"


@pytest.mark.slow
def test_real_double_sbar_wing_unchanged():
    """Non-régression (contraintes globales) : aile à deux SBAR parallèles, ✔, 98 plaques, un seul morceau (P1S)."""
    cfg = load_config()
    run = run_model(classify(read_step(DOUBLE_SBAR), cfg), cfg)
    (body,) = run.bodies
    assert body.report.ok, [i.message for i in body.report.errors][:5]
    assert body.plates == 98
    assert len(body.segments) == 1 and body.tubes == () and body.notices == ()
