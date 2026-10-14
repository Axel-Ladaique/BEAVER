import dataclasses

import pytest
from build123d import Cylinder, Location
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import unary_union

from beaver import pipeline
from beaver.classify import classify
from beaver.config import load_config
from beaver.io_step import NamedBody
from beaver.pipeline import PipelineError, generate_skin, run_model, spar_obstruction
from beaver.slicing import slice_layers
from beaver.testing.synthetic import naca_wing, wing_x_bodies

FAST = load_config(overrides={"material_settings": {"layer_height": 1.0}, "rib_layers": [{"spacing": 20}]})
FAST_LINK = load_config(
    overrides={"material_settings": {"layer_height": 1.0}, "rib_layers": [{"spacing": 20}], "passages": {"mode": "link"}}
)


def test_synthetic_wing_is_printable():
    model = classify(wing_x_bodies(), FAST_LINK)
    messages = []
    run = run_model(model, FAST_LINK, progress=messages.append)
    assert run.ok, [i.message for b in run.bodies for i in b.report.errors][:5]
    (body,) = run.bodies
    assert body.name == "Aile_test" and body.plates > 10
    assert sorted(b.cut_hole for b in body.bores) == [False, True]
    assert body.filled == ()  # mode link : le passage est relié, pas rebouché
    assert body.result.is_valid and len(body.result.solids()) == 1
    bb = body.result.bounding_box()
    assert bb.min.Z == pytest.approx(0.0, abs=1e-3) and bb.max.Z == pytest.approx(80.0, abs=1e-3)
    assert body.result.volume < body.original.volume
    assert any("nervures" in m for m in messages)
    # longeron et passage relié comptés séparément
    assert any("1 longeron(s), 1 passage(s) relié(s), 0 rebouché(s)" in m for m in messages), messages


def test_spar_outside_skin_is_ignored():
    bodies = wing_x_bodies() + [NamedBody(("SBAR_far",), Cylinder(4, 80).moved(Location((40, 200, 0), (0, 90, 0))))]
    run = run_model(classify(bodies, FAST), FAST)
    assert [b.name for b in run.bodies[0].bores if b.cut_hole] == ["SBAR_8"]


# --- étendue des longerons et contrôle final d'obstruction (G1) ---------------------------------

# 60 mm et phase 15 : à z = 60, aucune nervure ne passe en x = 30 (position du longeron)
HALF = load_config(overrides={"rib_layers": [{"spacing": 60, "phase": 15}]})
HALF_LINK = load_config(overrides={"rib_layers": [{"spacing": 60, "phase": 15}], "passages": {"mode": "link"}})


def _half_span_case():
    wing, camber = naca_wing(chord=100.0, span=80.0)
    spar = Cylinder(4, 40).moved(Location((30, camber(30), 20)))  # z de 0 à 40 sur 80
    return wing.solids()[0], spar, camber


@pytest.fixture(scope="module")
def half_span():
    skin, spar, camber = _half_span_case()
    result, bores, _, _, _, _, *_ = generate_skin(skin, [("SBAR_demi", spar)], HALF)
    return skin, spar, camber, result, bores


def test_half_span_spar_has_no_slit_beyond_its_end(half_span):
    _, _, camber, result, bores = half_span
    (b,) = [b for b in bores if b.cut_hole]
    assert (b.z0, b.z1) == (pytest.approx(0.0), pytest.approx(40.0))
    yc = camber(30)
    probe = box(29.9, yc + 4.6, 30.1, yc + 5.4)  # entre le haut du trou et l'extrados, sur l'axe de la fente
    low, high = slice_layers(result, [20.0, 60.0])
    assert not unary_union(low.polygons).contains(probe)  # fente présente sous z1
    assert unary_union(high.polygons).contains(probe)  # rien au-dessus de z1 + marge
    assert unary_union(high.polygons).contains(box(29, yc - 1, 31, yc + 1))  # ni trou


def test_spar_obstruction(half_span):
    skin, spar, _, result, _ = half_span
    assert spar_obstruction(spar, skin) > 1000  # peau pleine : le longeron est dans la matière
    assert spar_obstruction(spar, result) < 1.0
    assert spar_obstruction(spar.moved(Location((1, 0, 0))), result) > 1.0


def test_offset_spar_is_refused(monkeypatch):
    skin, spar, _ = _half_span_case()
    real = pipeline.spar_bores
    monkeypatch.setattr(pipeline, "spar_bores", lambda *a: [dataclasses.replace(b, x=b.x + 2) for b in real(*a)])
    monkeypatch.setattr(pipeline, "diamond_plates", lambda *a, **k: [])  # rapide : seul le trou compte
    with pytest.raises(PipelineError, match="traverse la matière"):
        generate_skin(skin, [("SBAR_demi", spar)], HALF)


def test_tilted_spar_is_refused():
    skin, _, camber = _half_span_case()
    spar = Cylinder(4, 40).moved(Location((30, camber(30), 20), (0, 3, 0)))
    with pytest.raises(PipelineError, match="incliné de 3.0°"):
        generate_skin(skin, [("SBAR_penche", spar)], HALF)


# --- erreurs lisibles (G4) ; plusieurs peaux : tests/test_pieces.py ---------------------------------------------


def test_orientation_failure_is_pipeline_error(monkeypatch):
    def boom(model, cfg):
        raise ValueError("section d'emplanture introuvable")

    monkeypatch.setattr(pipeline, "orient", boom)
    with pytest.raises(PipelineError, match="orientation impossible : section d'emplanture introuvable"):
        run_model(classify(wing_x_bodies(), FAST), FAST)


@pytest.mark.parametrize("exc", [ValueError("Null TopoDS_Shape"), RuntimeError("BRep_API: command not done")])
def test_generation_failure_is_pipeline_error(monkeypatch, exc):
    def boom(*a, **k):
        raise exc

    monkeypatch.setattr(pipeline, "generate_skin", boom)
    with pytest.raises(PipelineError, match="Aile_test : génération impossible"):
        run_model(classify(wing_x_bodies(), FAST), FAST)


def test_occ_failure_is_pipeline_error(monkeypatch):
    from OCP.Standard import Standard_Failure

    def boom(*a, **k):
        raise Standard_Failure("BOPAlgo")

    monkeypatch.setattr(pipeline, "generate_skin", boom)
    with pytest.raises(PipelineError, match="génération impossible"):
        run_model(classify(wing_x_bodies(), FAST), FAST)


def test_coaxial_small_bore_is_not_recut_by_the_spar(monkeypatch):
    from build123d import Align, Box
    from shapely.geometry import Point

    monkeypatch.setattr(pipeline, "diamond_plates", lambda *a, **k: [])  # rapide : seuls les trous comptent
    skin = (
        Box(60, 30, 100, align=(Align.CENTER, Align.CENTER, Align.MIN))
        - Cylinder(1, 40).moved(Location((10, 0, 80)))
    ).solids()[0]
    spar = Cylinder(4, 40).moved(Location((10, 0, 20)))
    result, bores, _, _, _, _, *_ = generate_skin(skin, [("SBAR", spar)], HALF_LINK)
    assert sorted((b.name, b.cut_hole, round(b.radius, 2)) for b in bores) == [
        ("SBAR", True, 4.15), ("passage 1", False, 1.0)
    ]
    mid, upper = (unary_union(layer.polygons) for layer in slice_layers(result, [50.0, 80.0]))
    assert mid.contains(Point(10, 0).buffer(4.5))  # aucune coupe entre le longeron et l'alésage
    assert upper.contains(Point(10, -2.5)) and not upper.contains(Point(10, -0.5))  # Ø2 resté Ø2


# --- P1b : attache côté opposé et rebouchage (aile synthétique, réglages par défaut) ------------


def _vertical(poly, x, y_lo=-100.0, y_hi=100.0):
    """Segments de matière de la tranche le long de la verticale x (triés par y)."""
    cut = poly.intersection(LineString([(x, y_lo), (x, y_hi)]))
    segs = [] if cut.is_empty else list(getattr(cut, "geoms", [cut]))
    return sorted((g.bounds[1], g.bounds[3]) for g in segs)


@pytest.fixture(scope="module")
def default_run():
    return run_model(classify(wing_x_bodies(), FAST), FAST)


def test_default_run_anchors_spar_and_fills_passage(default_run):
    assert default_run.ok, [i.message for b in default_run.bodies for i in b.report.errors][:5]
    (body,) = default_run.bodies
    (b,) = body.bores  # le passage est rebouché : seul le longeron reste
    assert b.cut_hole and b.anchor_gap == 1.0 and b.side == 1
    (f,) = body.filled
    assert f.r == pytest.approx(1.2)
    assert body.result.is_valid and len(body.result.solids()) == 1
    (layer,) = slice_layers(body.result, [40.0])
    (poly,) = layer.polygons  # un seul contour
    assert not poly.interiors  # aucun anneau intérieur : ni trou isolé, ni passage
    disc = Point(f.x, f.y).buffer(f.r)
    assert poly.intersection(disc).area > 0.85 * disc.area  # le passage est plein (au plus une nervure le croise)


def test_default_run_anchor_notch_under_the_spar(default_run):
    (body,) = default_run.bodies
    (b,) = body.bores
    (layer,) = slice_layers(body.result, [40.0])
    (poly,) = layer.polygons
    skin_bottom = _vertical(Polygon(poly.exterior), b.x)[0][0]
    hole_bottom = b.y - b.radius
    # sous le trou, une seule matière sur l'axe : le pont de anchor_gap (±0,2) contre la peau intrados
    below = [s for s in _vertical(poly, b.x) if s[1] < hole_bottom - 0.1]
    assert len(below) == 1
    lo, hi = below[0]
    assert lo == pytest.approx(skin_bottom, abs=1e-3)
    assert hi - lo == pytest.approx(1.0, abs=0.2)
    # largeur de l'encoche ≈ slit_width, matière de part et d'autre (bande sans nervure)
    y = (hole_bottom + hi) / 2
    cut = poly.intersection(LineString([(b.x - 1.0, y), (b.x + 1.0, y)]))
    segs = sorted(g.bounds for g in getattr(cut, "geoms", [cut]))
    assert len(segs) == 2
    assert segs[1][0] - segs[0][2] == pytest.approx(b.slit, abs=0.05)


def test_half_span_spar_anchor_stops_with_the_spar(half_span):
    _, _, camber, result, _ = half_span
    yc = camber(30)
    notch = box(29.95, yc - 4.9, 30.05, yc - 4.4)  # entre le bas du trou (yc − 4,15) et la fin de l'attache
    low, high = (unary_union(layer.polygons) for layer in slice_layers(result, [20.0, 60.0]))
    assert low.intersection(notch).area < 0.2 * notch.area  # attache présente sous z1
    assert high.contains(notch)  # rien au-delà du longeron


@pytest.fixture
def no_ribs(monkeypatch):
    monkeypatch.setattr(pipeline, "diamond_plates", lambda *a, **k: [])  # rapide : seuls les trous comptent


def test_anchor_off_has_no_notch(no_ribs):
    skin, spar, camber = _half_span_case()
    cfg = load_config(overrides={"spars": {"default": {"anchor": False}}})
    result, (b,), _, _, _, _, *_ = generate_skin(skin, [("SBAR_demi", spar)], cfg)
    assert b.anchor_gap == 0.0
    yc = camber(30)
    (layer,) = slice_layers(result, [20.0])
    assert unary_union(layer.polygons).contains(box(29.95, yc - 4.9, 30.05, yc - 4.4))


def _parasitic_case():
    wing, camber = naca_wing(chord=100.0, span=80.0)
    skin = (wing - Cylinder(1.2, 200).moved(Location((70, camber(70), 40)))).solids()[0]
    spar = Cylinder(4, 80).moved(Location((30, camber(30), 40)))
    return skin, spar, camber


def test_parasitic_bore_is_filled_by_default(no_ribs):
    skin, spar, camber = _parasitic_case()
    result, bores, _, filled, _, _, *_ = generate_skin(skin, [("SBAR", spar)], HALF)
    assert [b.name for b in bores] == ["SBAR"]
    (f,) = filled
    assert (f.x, f.y, f.r) == (pytest.approx(70.0), pytest.approx(camber(70)), pytest.approx(1.2))
    assert (f.z0, f.z1) == (pytest.approx(0.0), pytest.approx(80.0))
    assert result.is_valid and len(result.solids()) == 1
    bb = result.bounding_box()
    assert bb.min.Z == pytest.approx(0.0, abs=1e-3) and bb.max.Z == pytest.approx(80.0, abs=1e-3)  # rien au-delà
    (layer,) = slice_layers(result, [40.0])
    (poly,) = layer.polygons
    assert not poly.interiors
    yc = camber(70)
    assert poly.contains(Point(70, yc).buffer(1.5))  # plus de trou
    top = _vertical(Polygon(poly.exterior), 70.0)[0][1]
    assert poly.contains(box(69.9, yc, 70.1, top - 0.3))  # ni de fente vers la peau


def test_parasitic_bore_linked_in_link_mode(no_ribs):
    skin, spar, camber = _parasitic_case()
    result, bores, _, filled, _, _, *_ = generate_skin(skin, [("SBAR", spar)], HALF_LINK)
    assert filled == ()
    assert [b.name for b in bores] == ["SBAR", "passage 1"]
    (layer,) = slice_layers(result, [40.0])
    yc = camber(70)
    assert not unary_union(layer.polygons).contains(Point(70, yc))  # trou gardé
    assert not unary_union(layer.polygons).contains(box(69.99, yc + 1.5, 70.01, yc + 2.0))  # fente vers l'extrados


def test_fill_refused_when_bore_breaks_the_surface(no_ribs):
    from build123d import Align, Box

    base = Box(60, 30, 40, align=(Align.CENTER, Align.CENTER, Align.MIN))
    skin = (base - Cylinder(1.2, 40).moved(Location((0, 15 - 1.21, 20)))).solids()[0]  # paroi de 0,01 mm
    with pytest.raises(PipelineError, match=r"passage 1 \(x=0.0, y=13.8, Ø2.4\) : le rebouchage \(Ø2.44\) dépasserait la surface de 0.01 mm — rebouchage impossible"):
        generate_skin(skin, [], HALF)


def test_fill_refused_for_tilted_passage(no_ribs):
    from build123d import Align, Box

    base = Box(60, 30, 40, align=(Align.CENTER, Align.CENTER, Align.MIN))
    skin = (base - Cylinder(1.2, 80).moved(Location((10, 3, 20), (0, 2, 0)))).solids()[0]
    with pytest.raises(PipelineError, match=r"passage 1.*incliné de 2.0°.*\(dérive 1.40 mm\) — rebouchage impossible"):
        generate_skin(skin, [], HALF)


def test_passages_searched_once(no_ribs, monkeypatch):
    from beaver.features import bores as bores_mod

    calls = []
    real = bores_mod.find_passages
    spy = lambda *a: calls.append(1) or real(*a)  # noqa: E731
    monkeypatch.setattr(bores_mod, "find_passages", spy)
    monkeypatch.setattr(pipeline, "find_passages", spy)
    skin, spar, _ = _parasitic_case()
    generate_skin(skin, [("SBAR", spar)], HALF)
    assert len(calls) == 1


# --- P3 : doubles nervures (réseau principal + réseau de peau peu profond) ----------------------

DOUBLE = load_config(
    overrides={
        "material_settings": {"layer_height": 1.0},
        "rib_layers": [{"spacing": 20}, {"name": "peau", "spacing": 10, "depth": 3, "phase": 5}],
    }
)


@pytest.fixture(scope="module")
def double_run():
    return run_model(classify(wing_x_bodies(), DOUBLE), DOUBLE)


def _notch_depths(poly, skin, xs):
    """Profondeur (depuis l'extrados) des encoches ouvertes sur l'extrados le long des verticales xs."""
    depths = []
    for x in xs:
        outer = _vertical(skin, x)
        cols = _vertical(poly, x)
        if not outer or not cols:
            continue
        top_skin, top_mat = outer[-1][1], cols[-1][1]
        if top_skin - top_mat > 0.1:  # la verticale tombe dans une fente ouverte sur l'extrados
            depths.append(top_skin - top_mat)
    return depths


def test_double_ribs_wing_is_printable(double_run, default_run):
    assert double_run.ok, [i.message for b in double_run.bodies for i in b.report.errors][:5]
    (body,) = double_run.bodies
    assert body.result.is_valid and len(body.result.solids()) == 1
    assert body.plates > default_run.bodies[0].plates


def test_double_ribs_skin_notches_are_shallow(double_run):
    (body,) = double_run.bodies
    bb = body.result.bounding_box()
    z = (bb.min.Z + bb.max.Z) / 2
    (layer,) = slice_layers(body.result, [z])
    (poly,) = layer.polygons
    (orig,) = slice_layers(body.original, [z])
    skin = Polygon(orig.polygons[0].exterior)
    xs = [bb.min.X + i * 0.02 for i in range(int((bb.max.X - bb.min.X) / 0.02))]
    depths = _notch_depths(poly, skin, xs)
    shallow = [d for d in depths if abs(d - 3.0) <= 0.3]
    assert shallow, sorted(depths)
    # et le réseau principal descend toujours jusqu'à la bande de séparation (plus profond que 3,3 mm)
    assert any(d > 3.5 for d in depths), sorted(depths)


def test_fill_refused_with_thin_positive_wall_keeps_wall_wording(no_ribs, monkeypatch):
    from build123d import Align, Box

    monkeypatch.setattr(pipeline, "_FILL_WALL", 0.05)  # paroi restante de ~0,03 mm : refusée, mais positive
    base = Box(60, 30, 40, align=(Align.CENTER, Align.CENTER, Align.MIN))
    skin = (base - Cylinder(1.2, 40).moved(Location((0, 15 - 1.25, 20)))).solids()[0]
    with pytest.raises(PipelineError, match=r"paroi de 0.03 mm entre le rebouchage \(Ø2.44\) et la surface"):
        generate_skin(skin, [], HALF)


def test_partial_layer_skips_plates_of_deeper_layers(monkeypatch):
    # phase 0 : une nervure de peau sur trois tombe sur une principale (contenue dedans) → pas recoupée
    from beaver.features import ribs

    seen = {}

    def spy(bb, mid, bores, layer, **kw):
        seen[layer.name] = len(ribs.diamond_plates(bb, mid, bores, layer, **kw))
        return []

    monkeypatch.setattr(pipeline, "diamond_plates", spy)
    skin, spar, _ = _half_span_case()
    same = load_config(overrides={"rib_layers": [{"spacing": 30}, {"name": "peau", "spacing": 10, "depth": 3}]})
    alone = load_config(overrides={"rib_layers": [{"name": "peau", "spacing": 10, "depth": 3}]})
    generate_skin(skin, [("SBAR_demi", spar)], same)
    with_main = dict(seen)
    generate_skin(skin, [("SBAR_demi", spar)], alone)
    assert 0 < with_main["peau"] < seen["peau"]
    assert with_main["peau"] + with_main["principal"] >= seen["peau"]  # chaque doublon est couvert par une principale


def test_main_solid_retire_les_eclats_negligeables():
    from build123d import Box, Compound, Pos

    from beaver.pipeline import main_solid

    big = Box(10, 10, 10).solid()
    sliver = (Pos(50, 0, 0) * Box(0.05, 1.0, 0.1)).solid()
    solid, dropped = main_solid(Compound([big, sliver]))
    assert dropped == 1
    assert abs(solid.volume - 1000) < 1e-6


def test_main_solid_refuse_deux_vrais_morceaux():
    from build123d import Box, Compound, Pos

    from beaver.pipeline import PipelineError, main_solid

    a = Box(10, 10, 10).solid()
    b = (Pos(50, 0, 0) * Box(10, 10, 10)).solid()
    with pytest.raises(PipelineError, match="2 morceaux"):
        main_solid(Compound([a, b]))


def test_walls_removing_every_plate_are_reported(monkeypatch):
    # garde-fou : un niveau vidé par le masque des parois raides ne doit jamais passer en silence (aile « imprimable »
    # mais sans nervure) ; avertissement dans le résultat (CLI, résumé de la GUI)
    import numpy as np

    from beaver.features import ribs

    monkeypatch.setattr(ribs, "steep_samples", lambda mid, x, z, **k: np.ones(len(x), dtype=bool))
    run = run_model(classify(wing_x_bodies(), FAST_LINK), FAST_LINK)
    (body,) = run.bodies
    assert body.plates == 0
    (warn,) = [w for w in body.warnings if "parois" in w]
    assert "principal" in warn and "slit_width" in warn and "max_mouth_width" in warn
