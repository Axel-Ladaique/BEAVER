"""SBAR transversales et inclinées (spec découpe §6) : trou en goutte d'eau, débouchant ou relié par une fente."""

import math
from pathlib import Path

import numpy as np
import pytest
from shapely.geometry import Point

from aerorib import pipeline
from aerorib.classify import classify
from aerorib.config import load_config
from aerorib.features.tubes import Prism, Region, band, beyond, is_transversal, teardrop
from aerorib.io_step import NamedBody
from aerorib.slicing import Layer, slice_layers, to_mesh
from aerorib.testing.synthetic import cross_wing_bodies, naca4, rod, split_wing_bodies
from aerorib.validate import check_connectivity, slicer_closing

FIXTURE = Path(__file__).parent / "fixtures" / "bambu_p1s_pla_aero.3mf"  # plateau 256 × 256
FAST = load_config(overrides={"material_settings": {"layer_height": 1.0}, "rib_layers": [{"spacing": 20}]})
_, CAMBER = naca4("4412", 100)


AXIS_Z = load_config(overrides={
    "material_settings": {"layer_height": 1.0}, "rib_layers": [{"spacing": 20}], "orientation": {"axis": [0, 0, 1]},
})


def _through():
    """SBAR en corde du bord d'attaque au bord de fuite (débouchante aux deux bouts), sans longeron principal."""
    return cross_wing_bodies((-5, CAMBER(40), 60), (105, CAMBER(40), 60), d=4, span=120, spar_x=None)


def _run(bodies, cfg=FAST):
    return pipeline.run_model(classify(bodies, cfg), cfg)


def _tilted(deg: float, start=(50.0, None, 15.0), length: float = 40.0, d: float = 3.0):
    x, y, z = start
    y = CAMBER(62) if y is None else y
    a = math.radians(deg)
    return cross_wing_bodies((x, y, z), (x + length * math.sin(a), y, z + length * math.cos(a)), d=d)


def _single_contour_layers(body, step: float = 0.1):
    """Couches fines (pas `step`) sur la hauteur des trous des SBAR transversales : (z, défauts de connexité)."""
    lo = min(t.hole.zrange()[0] for t in body.tubes) - 0.5
    hi = max(t.hole.zrange()[1] for t in body.tubes) + 0.5
    bb = body.result.bounding_box()
    zs = [float(z) for z in np.arange(max(lo, bb.min.Z + step / 2), min(hi, bb.max.Z), step)]
    cfg = FAST.preset
    out = []
    for layer in slice_layers(body.result, zs):
        out += check_connectivity(Layer(layer.z, slicer_closing(layer.polygons, cfg.min_slit_width)))
    return zs, out


# --- géométrie --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("tilt", [90.0, 60.0])
def test_teardrop_ceiling_within_overhang_and_holds_the_rod(tilt):
    r, overhang = 2.15, 45.0
    pts = teardrop(r, tilt, overhang)
    ring = np.vstack([pts, pts[:1]])
    worst = 0.0
    for p, q in zip(ring[:-1], ring[1:]):
        e = q - p
        normal = np.array([e[1], -e[0]]) / np.linalg.norm(e)  # sortante (polygone dans le sens direct)
        if normal @ (p + q) < 0:
            normal = -normal
        dist = normal @ p
        assert dist >= r - 1e-9  # facette tangente ou au-delà du cercle : le longeron entre
        # surplomb de la paroi : asin(cos φ · sin(tilt)), cos φ = composante de la normale sur la pointe (t)
        worst = max(worst, math.degrees(math.asin(max(0.0, normal[0]) * math.sin(math.radians(tilt)))))
    assert worst <= overhang + 1e-6
    assert pts[:, 0].max() == pytest.approx(r / (math.sin(math.radians(overhang)) / math.sin(math.radians(tilt))))


def test_teardrop_without_tip_when_axis_close_to_z():
    pts = teardrop(2.0, 30.0, 45.0)
    radii = np.linalg.norm(pts, axis=1)
    assert np.allclose(radii, radii[0])  # polygone régulier circonscrit : pas de pointe


def test_prism_section_is_exact_and_clipped():
    square = np.array([[0, 0, 0], [2, 0, 0], [2, 2, 0], [0, 2, 0]], dtype=float)
    p = Prism(square, square + [0, 0, 10])
    sec = p.layer(5.0)
    assert sec.area == pytest.approx(4.0) and sec.bounds == pytest.approx((0, 0, 2, 2))
    side = p.section((0, 1, 0), (1, 0, 0), (0, 0, 1))  # plan y = 1 : rectangle 2 × 10 en (x, z)
    assert side.bounds == pytest.approx((0, 0, 2, 10))
    clipped = Prism(square, square + [0, 0, 10], (((0.0, 0.0, -1.0), 4.0),))  # z ≤ 4
    assert clipped.layer(5.0) is None and clipped.zrange() == (0.0, 4.0)
    assert clipped.section((0, 1, 0), (1, 0, 0), (0, 0, 1)).bounds == pytest.approx((0, 0, 2, 4))


def test_region_between_inclined_planes():
    q = Region((*band(0.0, 10.0), beyond(5.0, 0.5, 1), beyond(20.0, 0.5, -1)))  # 5 + z/2 ≤ x ≤ 20 + z/2
    assert q.layer(4.0, 100).bounds[0] == pytest.approx(7.0) and q.layer(4.0, 100).bounds[2] == pytest.approx(22.0)
    assert q.layer(11.0, 100) is None


def test_transversal_detection_and_ambiguous_tilt():
    assert is_transversal(rod((0, 0, 0), (10, 0, 0), 4), FAST)
    assert is_transversal(rod((0, 0, 0), (5, 0, 8.66), 4), FAST)
    assert not is_transversal(rod((0, 0, 0), (0, 0, 50), 4), FAST)
    # 3° de Z : sous la tolérance d'un trou transversal, au-delà du jeu d'un trou vertical — ambigu, refusé
    with pytest.raises(pipeline.PipelineError, match=r"incliné de 3\.0°.*ambigu"):
        _run(cross_wing_bodies((60, CAMBER(60), 10), (60 + 60 * math.tan(math.radians(3)), CAMBER(60), 70), d=3))


def test_longest_spar_gives_the_span_when_spars_are_not_parallel():
    from aerorib.orient import choose_axis

    axis, source = choose_axis(classify(cross_wing_bodies((40, 3, 40), (70, 3, 40)), FAST), FAST)
    assert np.allclose(np.abs(axis), [0, 0, 1]) and "SBAR_8" in source


# --- pièces synthétiques -----------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def closed_run():
    """SBAR en corde (axe X), bouts fermés dans l'aile."""
    return _run(cross_wing_bodies((40, CAMBER(55), 40), (70, CAMBER(55), 40), d=4))


def test_closed_chordwise_sbar_gets_a_slit(closed_run):
    assert closed_run.ok, [i.message for i in closed_run.bodies[0].report.errors][:5]
    (b,) = closed_run.bodies
    (t,) = b.tubes
    assert t.name == "SBAR_T" and t.tilt == pytest.approx(90.0) and not (t.open_start or t.open_end)
    assert t.slits and not t.debouching
    zs, defects = _single_contour_layers(b)
    assert len(zs) > 40 and defects == []  # chaque couche : un seul contour, sans trou isolé


def test_hole_ceiling_overhang_measured_on_the_mesh(closed_run):
    (b,) = closed_run.bodies
    (t,) = b.tubes
    mesh = to_mesh(b.result, 0.01)
    d = t.axis
    a = np.asarray(t.start)
    rel = mesh.triangles_center - a
    axial = rel @ d
    radial = np.linalg.norm(rel - np.outer(axial, d), axis=1)
    length = np.linalg.norm(np.subtract(t.end, t.start))
    # paroi du trou (hors bouts) : à moins de la pointe de la goutte d'eau de l'axe, hors des fentes
    tip = t.radius * math.sqrt(2) + 0.05
    on_wall = (axial > 0.5) & (axial < length - 0.5) & (radial > t.radius - 0.05) & (radial < tip)
    for slit in t.slits:
        n = slit.ring1[0] - slit.ring0[0]
        n /= np.linalg.norm(n)
        mid = (slit.ring0[0] + slit.ring1[0]) / 2
        on_wall &= np.abs((mesh.triangles_center - mid) @ n) > np.linalg.norm(slit.ring1[0] - slit.ring0[0]) / 2 + 0.05
    normals = mesh.face_normals[on_wall]
    assert len(normals) > 50
    down = normals[normals[:, 2] < 0]
    worst = math.degrees(math.asin(min(1.0, float((-down[:, 2]).max()))))
    assert worst <= FAST.preset.max_overhang_deg + 1.0
    assert worst >= FAST.preset.max_overhang_deg - 1.0  # flancs de la goutte au surplomb max (le cercle seul : 90°)


def test_chordwise_sbar_open_at_the_leading_edge_needs_no_slit():
    run = _run(cross_wing_bodies((-5, CAMBER(10), 40), (18, CAMBER(10), 40), d=4))
    assert run.ok
    (b,) = run.bodies
    (t,) = b.tubes
    assert (t.open_start or t.open_end) and t.debouching
    _, defects = _single_contour_layers(b)
    assert defects == []


def test_sbar_open_at_both_ends_in_the_same_layers_is_refused():
    # axe horizontal du bord d'attaque au bord de fuite : chaque couche du trou serait coupée en deux
    with pytest.raises(pipeline.PipelineError, match=r"SBAR_T : à z = .* couper la couche en deux .* vase mode"):
        _run(_through(), AXIS_Z)


def test_sbar_tilted_30_degrees_inside_the_wing():
    run = _run(_tilted(30.0))
    assert run.ok, [i.message for i in run.bodies[0].report.errors][:5]
    (b,) = run.bodies
    (t,) = b.tubes
    assert t.tilt == pytest.approx(30.0, abs=0.01) and t.slits
    # fente dans le plan de l'axe (parois inclinées de 30°, imprimables), pas de pointe de goutte d'eau
    normal = t.slits[0].ring1[0] - t.slits[0].ring0[0]
    assert abs(normal[2]) / np.linalg.norm(normal) == pytest.approx(math.sin(math.radians(30)), abs=0.01)
    zs, defects = _single_contour_layers(b)
    assert len(zs) > 300 and defects == []


def test_sbar_tilted_60_degrees_gets_vertical_slit_steps():
    run = _run(_tilted(60.0, start=(40.0, CAMBER(50), 30.0), length=30.0))
    assert run.ok, [i.message for i in run.bodies[0].report.errors][:5]
    (b,) = run.bodies
    (t,) = b.tubes
    assert len(t.slits) > 1 and all(abs((s.ring1[0] - s.ring0[0])[2]) < 1e-9 for s in t.slits)  # marches verticales
    _, defects = _single_contour_layers(b)
    assert defects == []


def test_sbar_across_a_cad_chord_joint_opens_on_each_body():
    one, two, spar = split_wing_bodies("chord")
    t = rod((35, CAMBER(50), 40), (65, CAMBER(50), 40), 4)
    bodies = [NamedBody(one.path, (one.solid - t).solids()[0]), NamedBody(two.path, (two.solid - t).solids()[0]),
              spar, NamedBody(("SBAR_T",), t)]
    run = _run(bodies)
    assert run.ok
    for b in run.bodies:
        (tube,) = b.tubes
        assert (tube.open_start != tube.open_end) and tube.debouching  # ouvert sur la face de jonction seulement


def test_report_line():
    from aerorib.report import format_bores

    class T:
        name, radius, tilt = "SBAR_T", 2.15, 90.0

    assert format_bores([], (), [T()]) == "  trous : SBAR_T (transversale, Ø4.3, axe 90°)"


# --- coupe dans la corde -----------------------------------------------------------------------------------------


def _wide(ribs):
    cfg = load_config(overrides={
        "material_settings": {"layer_height": 1.0}, "rib_layers": ribs, "bambu": {"template": str(FIXTURE)},
    })
    _, camber = naca4("4412", 400)
    bodies = cross_wing_bodies(
        (170, camber(200), 60), (230, camber(200), 60), d=6, chord=400, span=120, spar_x=120, spar_d=8, wide=True,
    )
    return cfg, bodies


def _check_wide(cfg, run):
    (b,) = run.bodies
    assert b.names == ("A", "B") and [r.ok for r in b.piece_reports] == [True, True] and run.ok
    (t,) = b.tubes
    assert t.debouching and not (t.open_start or t.open_end)  # fermée dans l'aile, ouverte par la coupe
    # chaque morceau : à la hauteur de l'axe, le trou débouche sur la face de coupe (une encoche, un seul contour)
    zc = (np.asarray(t.start)[2] + np.asarray(t.end)[2]) / 2
    for piece, (z0, _) in zip(b.segments, b.spans):
        (layer,) = slice_layers(piece, [zc - z0 - b.result.bounding_box().min.Z])
        assert len(layer.polygons) == 1 and not layer.polygons[0].interiors
        pb = piece.bounding_box()
        a, e = np.asarray(t.start), np.asarray(t.end)
        c = (a[0] + e[0]) / 2
        x_face = pb.min.X if abs(pb.min.X - c) < abs(pb.max.X - c) else pb.max.X  # face de coupe : côté du trou
        inward = 1.0 if x_face == pb.min.X else -1.0
        y_axis = a[1] + (e[1] - a[1]) * (x_face - a[0]) / (e[0] - a[0])
        # point du trou contre la face de coupe : hors matière
        assert not layer.polygons[0].contains(Point(x_face + inward * 0.5, y_axis))
        assert layer.polygons[0].exterior.distance(Point(x_face, y_axis)) > 1.0  # bouche : la face est ouverte là


def test_sbar_across_a_chord_cut_aligns_both_pieces():
    cfg, bodies = _wide([{"spacing": 500, "phase": 250}])  # sans nervure : le test porte sur le trou
    _check_wide(cfg, pipeline.run_model(classify(bodies, cfg), cfg))


@pytest.mark.slow
def test_sbar_across_a_chord_cut_with_ribs():
    cfg, bodies = _wide([{"spacing": 20}])
    _check_wide(cfg, pipeline.run_model(classify(bodies, cfg), cfg))


# --- aperçu = génération ----------------------------------------------------------------------------------------


def test_preview_matches_generation(tmp_path):
    from aerorib import preview
    from aerorib.io_step import read_step, write_step

    path = write_step(tmp_path / "t.step", cross_wing_bodies((40, CAMBER(55), 40), (70, CAMBER(55), 40), d=4))
    (b,) = _run(read_step(path)).bodies  # même fichier que l'aperçu
    prep = preview.prepare(path, FAST)
    plates = preview.plates_2d(prep, FAST)
    assert len(plates) == b.plates
    (t,) = preview.tubes(prep, FAST)
    (g,) = b.tubes
    assert len(t.slits) == len(g.slits) >= 1
    for p, q in zip((t.hole, *t.slits, t.zone, *t.slit_zones), (g.hole, *g.slits, g.zone, *g.slit_zones)):
        assert np.allclose(p.vertices, q.vertices, atol=1e-6)
    zc = (t.start[2] + t.end[2]) / 2
    view = preview.section(prep, plates, FAST, zc)
    assert len(view.polygons) == 1 and not view.polygons[0].interiors and view.defects == []
    assert "SBAR_T" in [h["nom"] for h in view.holes]
    est = preview.estimates(prep, plates, FAST)
    assert est["plaques"] == b.plates


def test_preview_of_a_chord_cut_sbar(tmp_path):
    from aerorib import preview
    from aerorib.io_step import write_step

    cfg, bodies = _wide([{"spacing": 500, "phase": 250}])
    prep = preview.prepare(write_step(tmp_path / "w.step", bodies), cfg)
    (t,) = preview.tubes(prep, cfg)
    assert t.debouching  # même décision que la génération : la coupe dans la corde ouvre le trou


def test_unsolvable_sbar_reported_in_preview(tmp_path):
    from aerorib import preview
    from aerorib.io_step import write_step

    prep = preview.prepare(write_step(tmp_path / "b.step", _through()), AXIS_Z)
    with pytest.raises(preview.PreviewError, match="SBAR_T : à z = .* couper la couche en deux"):
        preview.plates_2d(prep, AXIS_Z)
