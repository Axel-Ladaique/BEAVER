"""SBAR transversales et inclinées (spec découpe §6) : trou en goutte d'eau, débouchant ou relié par une fente."""

import math
import re
from pathlib import Path

import numpy as np
import pytest
from shapely.geometry import Point

from beaver import pipeline
from beaver.classify import classify
from beaver.config import load_config
from beaver.features.tubes import Prism, Region, band, beyond, is_transversal, teardrop
from beaver.io_step import NamedBody
from build123d import Location
from beaver.slicing import Layer, slice_layers, to_mesh
from beaver.testing.synthetic import cross_wing_bodies, naca4, rod, split_wing_bodies
from beaver.validate import check_connectivity, slicer_closing

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
    from beaver.orient import choose_axis

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



def test_both_ends_open_message_says_which_ends_to_close():
    with pytest.raises(pipeline.PipelineError) as err:
        _run(_through(), AXIS_Z)
    msg = str(err.value)
    assert "débouche aux deux bouts dans la même couche" in msg and "l'un des deux suffit" in msg
    ends = re.findall(r"le bout à x = (-?[\d.]+) mm, z = (-?[\d.]+) mm", msg)
    assert len(ends) == 2 and {e[1] for e in ends} == {"60.0"}  # axe à 60 mm de l'emplanture
    assert abs(float(ends[0][0]) - float(ends[1][0])) == pytest.approx(110.0, abs=0.5)  # les deux bouts de la SBAR


def test_geometry_errors_in_tubes_are_translated(tmp_path, monkeypatch):
    from shapely.errors import GEOSException

    from beaver import preview
    from beaver.features import tubes
    from beaver.io_step import write_step

    def boom(*a, **k):
        raise GEOSException("TopologyException: side location conflict")

    monkeypatch.setattr(tubes, "_envelopes", boom)
    bodies = cross_wing_bodies((40, CAMBER(55), 40), (70, CAMBER(55), 40), d=4)
    msg = r"SBAR_T : SBAR transversale impossible \(GEOSException : TopologyException"
    with pytest.raises(pipeline.PipelineError, match=msg):
        _run(bodies)
    prep = preview.prepare(write_step(tmp_path / "t.step", bodies), FAST)
    with pytest.raises(preview.PreviewError, match=msg):
        preview.plates_2d(prep, FAST)


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
    from beaver.report import format_bores

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
    from beaver import preview
    from beaver.io_step import read_step, write_step

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
    (vue,) = preview.planform(prep, plates, FAST).to_dict()["sbar_transversales"]  # vue de dessus : axe du trou
    assert vue["nom"] == "SBAR_T" and vue["type"] == "transversale" and vue["d"] == pytest.approx(4.0, abs=0.01)
    assert (vue["x0"], vue["z0"], vue["x1"], vue["z1"]) == pytest.approx((g.start[0], g.start[2], g.end[0], g.end[2]), abs=1e-3)
    assert vue["angle"] == pytest.approx(90, abs=0.5) and vue["fente"] is True


def test_preview_of_a_chord_cut_sbar(tmp_path):
    from beaver import preview
    from beaver.io_step import write_step

    cfg, bodies = _wide([{"spacing": 500, "phase": 250}])
    prep = preview.prepare(write_step(tmp_path / "w.step", bodies), cfg)
    (t,) = preview.tubes(prep, cfg)
    assert t.debouching  # même décision que la génération : la coupe dans la corde ouvre le trou


def test_unsolvable_sbar_reported_in_preview(tmp_path):
    from beaver import preview
    from beaver.io_step import write_step

    prep = preview.prepare(write_step(tmp_path / "b.step", _through()), AXIS_Z)
    with pytest.raises(preview.PreviewError, match="SBAR_T : à z = .* couper la couche en deux"):
        preview.plates_2d(prep, AXIS_Z)


# --- bout haut fermé : plafond imprimable quel que soit l'angle ------------------------------------------------


def _ceiling_overhang_ok(prism, overhang: float, dz: float = 0.05) -> list[float]:
    """Hauteurs où la matière d'une couche du trou surplombe le vide de la couche du dessous de plus que `overhang`
    (vide(z) amaigri de dz·tan(surplomb) non contenu dans vide(z + dz))."""
    lo, hi = prism.zrange()
    bad = []
    reach = dz * math.tan(math.radians(overhang)) * 1.05
    for z in np.arange(lo + dz / 2, hi, dz):
        below, above = prism.layer(float(z)), prism.layer(float(z + dz))
        if below is None:
            continue
        core = below.buffer(-reach)
        if core.is_empty:
            continue
        rest = core if above is None else core.difference(above.buffer(1e-6))
        if rest.area > 1e-4:
            bad.append(float(z))
    return bad


@pytest.mark.parametrize("tilt", [45.0, 48.0])
def test_closed_upper_end_ceiling_within_asa_overhang(tmp_path, tilt):
    from beaver import preview
    from beaver.io_step import write_step

    asa = load_config(overrides={
        "material_settings": {"layer_height": 1.0, "max_overhang_deg": 40.0}, "rib_layers": [{"spacing": 20}],
    })
    prep = preview.prepare(write_step(tmp_path / "t.step", _tilted(tilt, start=(40.0, CAMBER(50), 30.0), length=30.0)), asa)
    (t,) = preview.tubes(prep, asa)
    assert not (t.open_start or t.open_end) and t.tilt == pytest.approx(tilt, abs=0.01)
    assert _ceiling_overhang_ok(t.hole, 40.0) == []
    # le longeron (+ jeu) reste dans le trou jusqu'à son bout
    (rod_solid,) = [s for n, s in prep.tube_spars if n == "SBAR_T"]
    tip = max(rod_solid.vertices(), key=lambda v: v.Z)
    sec = t.hole.layer(tip.Z - 0.01)
    assert sec is not None and sec.buffer(1e-6).contains(Point(tip.X, tip.Y))
    # outil de coupe (OCC) : prisme limité par la face verticale, même sommet que les sections
    solid = t.hole.solid()
    top = solid.bounding_box().max.Z
    assert solid.is_valid and top < t.hole.zrange()[1] - 1.0
    assert t.hole.layer(top - 0.01) is not None and t.hole.layer(top + 0.01) is None


def test_ignored_settings_and_bay_crossing_warn():
    from types import SimpleNamespace

    from beaver.features.tubes import tube_warnings

    cfg = load_config(overrides={"spars": {"sbar_t": {"anchor": True, "slit_side": "bottom", "clearance": 0.2}}})
    t = SimpleNamespace(name="SBAR_T", start=(-5.0, 0.0, 60.0), end=(105.0, 0.0, 60.0), radius=2.2, sleeve=0.8)
    far = SimpleNamespace(name="SERVO_1", x0=20.0, x1=40.0, opening=(70.0, 100.0))
    on = SimpleNamespace(name="SERVO_2", x0=20.0, x1=40.0, opening=(50.0, 80.0))
    attache, baie = tube_warnings([t], cfg, [far, on])
    assert attache.startswith("SBAR_T (SBAR transversale) : attache (anchor), côté de la fente (slit_side) sans effet")
    assert "SERVO_2" in baie and "SERVO_1" not in baie and "n'est pas traitée" in baie
    assert tube_warnings([t], FAST, [far]) == []  # défauts : aucun avertissement


def test_ignored_anchor_warns_in_preview_and_generation(tmp_path):
    from beaver import preview
    from beaver.io_step import write_step

    cfg = load_config(overrides={
        "material_settings": {"layer_height": 1.0}, "rib_layers": [{"spacing": 20}], "spars": {"SBAR_T": {"anchor": True}},
    })
    bodies = cross_wing_bodies((40, CAMBER(55), 40), (70, CAMBER(55), 40), d=4)
    prep = preview.prepare(write_step(tmp_path / "t.step", bodies), cfg)
    est = preview.estimates(prep, preview.plates_2d(prep, cfg), cfg)
    (w,) = [w for w in est["avertissements"] if "SBAR_T" in w]
    assert "attache (anchor)" in w
    (b,) = _run(bodies, cfg).bodies
    assert w in b.warnings


def test_preview_section_is_split_into_the_chord_pieces(tmp_path):
    from beaver import preview
    from beaver.io_step import write_step

    cfg, bodies = _wide([{"spacing": 500, "phase": 250}])
    prep = preview.prepare(write_step(tmp_path / "w.step", bodies), cfg)
    plates = preview.plates_2d(prep, cfg)
    (t,) = preview.tubes(prep, cfg)
    (plane,) = preview.planform(prep, plates, cfg).to_dict()["plans_corde"]
    view = preview.section(prep, plates, cfg, (t.start[2] + t.end[2]) / 2)
    # deux morceaux imprimés séparément : le trou de la SBAR débouche sur la face de coupe de chacun (un contour
    # par morceau, aucun défaut) — comme la validation morceau par morceau de la génération
    assert view.defects == [] and len(view.polygons) == 2
    left, right = sorted(view.polygons, key=lambda p: p.bounds[0])
    assert left.bounds[2] == pytest.approx(plane["x0"], abs=1e-6) and right.bounds[0] == pytest.approx(plane["x0"], abs=1e-6)
    assert not left.interiors and not right.interiors


# --- SBAR près de la peau, prolongement d'un bout débouchant (revue finale I2) -------------------------------------


def test_sbar_hugging_the_skin_is_refused(tmp_path):
    """SBAR en corde à 0,1 mm sous une face plane de la peau, fermée aux deux bouts (CAO saine) : avec le jeu, le
    trou ouvre la peau sur toute sa longueur. La couche reste d'un seul tenant (l'analyse vase mode l'accepte, trou
    « débouchant ») et le trou est retiré de la référence de validation : la fente dans la peau serait livrée ✔.
    Refus clair (même cas qu'un longeron vertical près de la peau, déjà signalé)."""
    from beaver import preview
    from beaver.io_step import write_step

    bodies = _slotted_bodies((10.0, 20.0 - 2.1, 40.0), (40.0, 20.0 - 2.1, 40.0))
    msg = (r"SBAR_T : trop près de la peau \(paroi < largeur de ligne 0\.42 mm\) à z = .* — l'éloigner de la peau "
           r"ou réduire le jeu")
    with pytest.raises(pipeline.PipelineError, match=msg):
        _run(bodies, SLOT_CFG)
    prep = preview.prepare(write_step(tmp_path / "t.step", bodies), SLOT_CFG)  # aperçu = génération
    with pytest.raises(preview.PreviewError, match=msg):
        preview.plates_2d(prep, SLOT_CFG)
    _run(_slotted_bodies((10.0, 20.0 - 3.5, 40.0), (40.0, 20.0 - 3.5, 40.0)), SLOT_CFG)  # paroi suffisante : admise


SLOT_CFG = load_config(overrides={"material_settings": {"layer_height": 1.0}, "rib_layers": [{"spacing": 500, "phase": 250}],
                                  "orientation": {"axis": [0, 0, 1]}})


def _slotted_bodies(p0, p1):
    """Pièce à encoche (zone concave) : profil 100 × 20 (y ≤ 20 : face plane) creusé d'une fente x ∈ [58, 70],
    y ≥ 6, extrudé sur 80 mm ; SBAR_T Ø4 de p0 à p1 (repère de construction)."""
    from build123d import BuildPart, BuildSketch, Polygon as Poly, extrude

    with BuildPart() as bp:
        with BuildSketch():
            Poly((0, 0), (100, 0), (100, 20), (70, 20), (70, 6), (58, 6), (58, 20), (0, 20), align=None)
        extrude(amount=80)
    t = rod(p0, p1, 4)
    return [NamedBody(("Aile_T",), (bp.part - t).solids()[0]), NamedBody(("SBAR_T",), t)]


def test_open_end_extension_stops_before_a_concave_wall():
    """Bout débouchant à 9 mm de l'autre paroi d'une encoche : l'ancien prolongement de 10 mm l'entaillait, et la
    référence de validation (trou prolongé compris) cachait l'entaille. Le trou s'arrête dès qu'il est sorti."""
    # SBAR en corde (y = 13) fermée dans la matière côté x = 30, débouchant dans la fente en x = 61
    run = _run(_slotted_bodies((30.0, 13.0, 40.0), (61.0, 13.0, 40.0)), SLOT_CFG)
    assert run.ok, [i.message for i in run.bodies[0].report.errors][:5]
    (b,) = run.bodies
    (t,) = b.tubes
    assert (t.open_start or t.open_end) and t.debouching

    def oriented(x, y, z):  # repère de construction → repère orienté de la pièce
        return np.asarray(tuple((run.orientation.location * Location((x, y, z))).position))

    mouth = np.asarray(t.start if t.open_start else t.end)
    assert np.linalg.norm(mouth - oriented(61.0, 13.0, 40.0)) <= 0.5 + 1e-6  # sortie franche : pas de prolongement
    (layer,) = slice_layers(b.result, [40.0])
    for x in (70.2, 71.0):  # autre paroi de l'encoche intacte (l'ancien prolongement allait jusqu'à x = 71)
        q = oriented(x, 13.0, 40.0)
        assert any(poly.contains(Point(q[0], q[1])) for poly in layer.polygons)


def test_open_end_near_a_cambered_trailing_edge_does_not_hide_a_cut():
    """SBAR qui sort par l'intrados cambré à 5 mm du bord de fuite (bout réel 0,5 mm sous l'intrados) : le trou, plus
    gros que le bord de fuite mince, le tranche au-delà du bout du longeron. Avant, le prolongement de 10 mm y était
    retiré de la référence de validation : bord de fuite coupé, livré ✔. Désormais la référence s'arrête à la
    bouche du bout réel : l'entaille est signalée."""
    pts, camber = naca4("4412", 100)
    lower = np.asarray(pts[79:])  # intrados, du bord d'attaque au bord de fuite
    y_end = float(np.interp(95.0, lower[:, 0], lower[:, 1])) - 0.5
    run = _run(cross_wing_bodies((75.0, camber(75.0), 40.0), (95.0, y_end, 40.0), d=3))
    (b,) = run.bodies
    (t,) = b.tubes
    assert t.open_end and not t.open_start
    assert not run.ok and {i.check for i in b.report.errors} == {"skin"}
    # la référence (bouche admise : bout réel + OPEN_END + rayon max) s'arrête avant le bout de l'outil
    length = lambda pr: float(np.linalg.norm(pr.ring1[0] - pr.ring0[0]))  # noqa: E731
    assert length(t.hole) > length(t.opening) + 1.0


def test_sbar_running_along_the_skin_from_an_open_end_is_refused():
    """Sonde de la re-revue : SBAR Ø4 à 0,1 mm sous la face plane, qui longe la peau dès son bout débouchant. Sa
    « bouche » ne s'écarte pas de la peau (distance axe–peau en palier) : elle s'arrête tout de suite, le reste du
    trou ouvre la face ⇒ refus. Avant (c7b0484) : bouche de ≈ 40 mm, livrée ✔ face ouverte sur ≈ 30 mm. (Deux bouts
    débouchants dans les mêmes couches : refus vase mode en amont ; bouches jointives : test suivant.)"""
    with pytest.raises(pipeline.PipelineError, match=r"SBAR_T : trop près de la peau"):
        _run(_slotted_bodies((-1.0, 17.9, 40.0), (30.0, 17.9, 40.0)), SLOT_CFG)  # débouchante en x = 0, fermée en 30


@pytest.mark.parametrize("y, refused", [(17.9, True), (10.0, False)])
def test_short_sbar_open_at_both_ends_gets_no_blanket_excuse(y, refused):
    """Bouches jointives : SBAR Ø4 de 32 mm, débouchante aux deux bouts d'un pavé 30 × 20 (enveloppe connue, sans
    maillage), qui longe la face y = 20 à 0,1 mm. Chaque bouche est bornée seule (palier de la distance axe–peau) :
    le trou ouvre la face entre les deux ⇒ TubeError. Avant (c7b0484), deux bouches de ≈ 40 mm se rejoignaient et
    tout le trou était excusé. Au milieu de l'épaisseur (y = 10), rien à redire."""
    from shapely.geometry import box

    from beaver.features.tubes import TubeError, _check_wall, _prism

    env = box(0.0, 0.0, 30.0, 20.0)
    zr = (0.0, 80.0)
    d = np.array([1.0, 0.0, 0.0])
    a, b = np.array([-1.0, y, 40.0]), np.array([31.0, y, 40.0])
    profile = teardrop(2.15, 90.0, 45.0)
    hole = _prism(a - 0.5 * d, b + 0.5 * d, profile, np.array([0.0, 0.0, 1.0]), np.array([0.0, -1.0, 0.0]))

    def inside(pts):
        return np.array([zr[0] < p[2] < zr[1] and env.contains(Point(p[0], p[1])) for p in pts])

    def env_at(zs):
        return [env] * len(zs)

    zs = [float(z) for z in np.arange(37.0, 43.0, 0.25)]
    check = lambda: _check_wall("SBAR_T", hole, profile, a, b, d, (True, True), zs, env_at(zs), inside, env_at, zr, 0.42)  # noqa: E731
    if refused:
        with pytest.raises(TubeError, match="trop près de la peau"):
            check()
    else:
        check()
