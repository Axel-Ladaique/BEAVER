"""Coupe plane dans la corde (spec découpe §4) : planificateur sans booléen, découpe, pipeline."""

import math
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pytest

from beaver import pipeline
from beaver.classify import classify
from beaver.config import load_config
from beaver.features.chord_cut import MAX_PIECES, Obstacle, bay_obstacles, plan_layout
from beaver.layout import fit_on_bed
from beaver.segment import MIN_SEGMENT, SegmentError, plan_pieces
from beaver.slicing import section_sampler
from beaver.testing.synthetic import swept_wing, wide_wing_bodies

FIXTURE = Path(__file__).parent / "fixtures" / "bambu_p1s_pla_aero.3mf"  # plateau 256 × 256, hauteur 250
P1S = (256.0, 256.0)
H2S = (340.0, 320.0)
MARGIN = 5.0


def _sections(solid):
    return section_sampler(solid, 0.05)


def _plan(solid, height, bed=P1S, **kw):
    return plan_layout(height, 245, _sections(solid), bed, MARGIN, **kw)


def _cfg(**overrides):
    base = {"material_settings": {"layer_height": 1.0}, "rib_layers": [{"spacing": 20}], "bambu": {"template": str(FIXTURE)}}
    return load_config(overrides={**base, **overrides})


def _lens(le: float, chord: float):
    """Section synthétique (losange) : bord d'attaque épais en x = le, bord de fuite en le + chord."""
    t = 0.06 * chord
    return np.array([(le, 0.0), (le + 0.3 * chord, t), (le + chord, 0.0), (le + 0.3 * chord, -t)])


# --- planificateur --------------------------------------------------------------------------------------------


def test_wide_chord_two_pieces_at_mid_chord():
    plan = _plan(swept_wing(chord=400, span=120, code="4412"), 120)
    (seg,) = plan.segments
    assert plan.cuts == () and plan.chord_cut and plan.names() == ["A", "B"]
    (p,) = seg.planes
    assert p.xa == pytest.approx(200, abs=1) and p.xb == pytest.approx(200, abs=1) and p.shift == 0
    assert p.inclination_deg == pytest.approx(0, abs=0.1)
    assert len(seg.placements) == 2 and all(pl.fits for pl in seg.placements)


def test_minimal_k():
    plan = _plan(swept_wing(chord=700, span=60, code="4412"), 60)
    (seg,) = plan.segments
    assert seg.k == 3 and plan.names() == ["A", "B", "C"]
    assert all(pl.fits for pl in seg.placements)


def test_plane_shifted_off_obstacle():
    spar = Obstacle("SBAR_8", 190.0, 214.0, 0.0, 120.0)
    plan = _plan(swept_wing(chord=400, span=120, code="4412"), 120, obstacles=[spar])
    (p,) = plan.segments[0].planes
    assert p.avoided == ("SBAR_8",)
    assert not (spar.x0 < p.x_at(0) < spar.x1) and not (spar.x0 < p.x_at(120) < spar.x1)
    assert abs(p.shift) == pytest.approx(min(200 - 190, 214 - 200), abs=0.5)  # position libre la plus proche
    assert all(pl.fits for pl in plan.segments[0].placements)


def test_obstacle_outside_segment_span_ignored():
    obs = Obstacle("SERVO_1", 150.0, 250.0, 200.0, 260.0)  # au-delà de l'envergure
    (p,) = _plan(swept_wing(chord=400, span=120, code="4412"), 120, obstacles=[obs]).segments[0].planes
    assert p.shift == 0


def test_no_free_position_lists_obstacles():
    obs = [Obstacle("SBAR_A", 5.0, 200.0, 0, 120), Obstacle("SERVO_1", 195.0, 395.0, 0, 120)]
    with pytest.raises(SegmentError, match="obstacles : SBAR_A, SERVO_1"):
        _plan(swept_wing(chord=400, span=120, code="4412"), 120, obstacles=obs)


def test_swept_wing_cut_is_vertical():
    # plans parallèles verticaux en priorité : morceaux moins larges qu'en suivant la flèche, face de coupe sans
    # surplomb ; le plan traverse la section à l'emplanture comme au saumon
    sweep = 20.0
    plan = _plan(swept_wing(chord=400, span=120, sweep_deg=sweep, code="4412"), 120)
    (seg,) = plan.segments
    (p,) = seg.planes
    shift = 120 * math.tan(math.radians(sweep))
    assert p.inclination_deg == pytest.approx(0, abs=1e-6) and p.shift == 0
    assert shift + 10 <= p.xa <= 400 - 10
    assert all(pl.fits for pl in seg.placements)


def test_tapered_swept_wing_vertical_planes_few_pieces():
    # aile effilée à bord de fuite droit et forte flèche du bord d'attaque (corde 850 → 553 mm sur 250 mm, H2S) :
    # les plans aux points i/k de la corde s'ouvraient en éventail (jusqu'à 44°) et donnaient 5 morceaux cisaillés ;
    # des plans verticaux en donnent 3
    height = 250.0

    def sections(zs):
        return [_lens(-237.0 + 1.19 * z, 850.0 - 1.19 * z) for z in zs]

    plan = plan_layout(height, 335, sections, H2S, MARGIN)
    (seg,) = plan.segments
    assert seg.k == 3 and all(p.inclination_deg == pytest.approx(0, abs=1e-6) for p in seg.planes)
    assert all(pl.fits for pl in seg.placements)


def test_too_inclined_raises():
    # flèche telle qu'aucun plan vertical ne traverse la section aux deux bouts : plan incliné de la flèche
    with pytest.raises(SegmentError, match="inclinée de 75°.*surplomb max 45°.*imprimante plus grande"):
        _plan(swept_wing(chord=400, span=120, sweep_deg=75, code="4412"), 120, cuts_at=[])


def test_inclination_reduced_by_more_z_segments():
    # aile à bord d'attaque droit et corde elliptique : plan à mi-corde emplanture → saumon trop incliné ;
    # des tronçons plus courts le redressent près de l'emplanture et le saumon tient sans coupe dans la corde
    height = 120.0

    def sections(zs):
        return [_lens(0.0, 400 * math.sqrt(max(1 - (z / height) ** 2, 1e-4))) for z in zs]

    plan = plan_layout(height, 245, sections, P1S, MARGIN)
    assert plan.extra_z and len(plan.segments) >= 2
    assert all(p.inclination_deg <= 45 for s in plan.segments for p in s.planes)
    assert plan.segments[0].planes and not plan.segments[-1].planes
    assert all(pl.fits for s in plan.segments for pl in s.placements)
    assert all(b - a >= MIN_SEGMENT - 1e-9 for a, b in zip((0, *plan.cuts), (*plan.cuts, height)))


def test_chord_off_raises():
    with pytest.raises(SegmentError, match="ne tient pas sur le plateau.*segment.chord: off"):
        _plan(swept_wing(chord=400, span=120, code="4412"), 120, chord="off")


def test_h2s_no_chord_cut():
    plan = _plan(swept_wing(chord=400, span=120, code="4412"), 120, bed=H2S)
    assert not plan.chord_cut and plan.names() == ["T1"]
    assert plan.segments[0].placements[0].fits
    assert _plan(swept_wing(chord=400, span=120, code="4412"), 120).chord_cut  # P1S : coupe nécessaire


def test_z_then_chord_names():
    plan = _plan(swept_wing(chord=400, span=300, code="4412"), 300)
    assert len(plan.cuts) == 1 and plan.names() == ["T1-A", "T1-B", "T2-A", "T2-B"]


def test_leading_edge_side_named_a():
    def sections(zs):  # bord d'attaque (épais) côté x grands
        return [_lens(0.0, 400)[:, :] * [-1, 1] + [400, 0] for _ in zs]

    (seg,) = plan_layout(60, 245, sections, P1S, MARGIN).segments
    assert not seg.le_at_min and seg.order() == [1, 0]


def test_too_wide_even_in_max_pieces():
    def sections(zs):
        return [np.array([(0, 0), (100, 300), (200, 0), (100, -300)]) for _ in zs]  # 600 d'épaisseur

    with pytest.raises(SegmentError, match=f"même en {MAX_PIECES} morceaux"):
        plan_layout(60, 245, sections, P1S, MARGIN)


# --- coupes en Z : défauts de la tâche 3 ------------------------------------------------------------------------


def test_oversized_section_does_not_disable_footprint_cuts_elsewhere():
    # emplanture trop large (coupe dans la corde) + partie en flèche 60° qui doit, elle, être recoupée en Z
    height = 200.0

    def sections(zs):
        return [_lens(0, 400) if z < 20 else _lens((z - 20) * math.tan(math.radians(60)), 60) for z in zs]

    plan = plan_pieces(height, 245, sections, P1S, MARGIN)
    assert plan.chord is not None and len(plan.cuts) >= 2
    bounds = [0.0, *plan.cuts, height]
    for (a, b), pl in zip(zip(bounds, bounds[1:]), plan.placements):
        assert pl.fits or a < 20  # seuls les tronçons de l'emplanture restent à couper dans la corde
    assert "empreinte" in plan.reason


def test_nothing_fits_before_min_segment_falls_back_to_height_cuts():
    wing = swept_wing(chord=60, span=20, sweep_deg=89)
    plan = plan_pieces(20.0, 245, _sections(wing), P1S, MARGIN)
    assert plan.cuts == () and plan.reason is None and not plan.fits


# --- découpe (géométrie seule, sans nervures) ------------------------------------------------------------------


def test_segment_part_wide_chord_pieces_valid():
    wing = swept_wing(chord=400, span=120, code="4412")
    seg = pipeline.segment_part("B", wing, _cfg(), lambda m: None, original=wing)
    assert seg.names == ("A", "B") and len(seg.pieces) == 2 and len(seg.checks) == 2
    for piece in seg.pieces:
        assert piece.is_valid and len(piece.solids()) == 1
        bb = piece.bounding_box()
        assert bb.min.Z == pytest.approx(0, abs=1e-3) and bb.max.Z == pytest.approx(120, abs=1e-3)
        pts = np.array([[v.X, v.Y] for v in piece.vertices()])
        assert fit_on_bed(pts, P1S, MARGIN).fits
    a, b = (p.bounding_box(optimal=True) for p in seg.pieces)
    assert a.max.X == pytest.approx(b.min.X, abs=1e-3)  # même plan
    assert sum(p.volume for p in seg.pieces) == pytest.approx(wing.volume, rel=1e-3)


def test_segment_part_swept_cut_is_vertical():
    wing = swept_wing(chord=400, span=120, sweep_deg=20, code="4412")
    seg = pipeline.segment_part("B", wing, _cfg(), lambda m: None)
    a, b = seg.pieces
    # le morceau A (bord d'attaque, x petits) s'arrête au plan vertical : même x en bas et en haut
    top = [v.X for v in a.vertices() if v.Z > 119.9]
    bottom = [v.X for v in a.vertices() if v.Z < 0.1]
    assert max(top) == pytest.approx(max(bottom), abs=0.5)


def test_segment_part_h2s_no_chord_cut():
    wing = swept_wing(chord=400, span=120, code="4412")  # la même aile que sur P1S (2 morceaux)
    cfg = load_config(overrides={"printer": "h2s"})
    seg = pipeline.segment_part("B", wing, cfg, lambda m: None)
    assert len(seg.pieces) == 1 and seg.notices == () and not seg.chord


def test_segment_part_steep_sweep_gets_more_z_segments():
    # flèche de 75° : pas de plan vertical sur toute la hauteur → tronçons en Z ajoutés, plans ≤ surplomb
    wing = swept_wing(chord=400, span=120, sweep_deg=75, code="4412")
    seg = pipeline.segment_part("B", wing, _cfg(), lambda m: None)
    assert len(set(seg.spans)) >= 2 and seg.chord and any("tronçons" in n or "T2" in n for n in seg.notices)


def test_segment_part_chord_off_error():
    wing = swept_wing(chord=400, span=120, code="4412")
    with pytest.raises(pipeline.PipelineError, match="segment.chord: off"):
        pipeline.segment_part("B", wing, _cfg(segment={"chord": "off"}), lambda m: None)


def test_segment_part_z_and_chord():
    wing = swept_wing(chord=400, span=300, code="4412")
    seg = pipeline.segment_part("B", wing, _cfg(), lambda m: None)
    assert seg.names == ("T1-A", "T1-B", "T2-A", "T2-B")
    assert [s for span in seg.spans for s in span] == pytest.approx([0, 150, 0, 150, 150, 300, 150, 300], abs=1e-3)
    assert all(p.is_valid for p in seg.pieces)
    assert any("2 tronçons en Z" in n for n in seg.notices)
    assert sum("coupe dans la corde" in n for n in seg.notices) == 2


def test_bay_obstacle_extent():
    from beaver.features.servo import ServoBay

    bay = ServoBay("SERVO_1", 1, 190.0, 215.0, 10.0, 45.0, 40.0, 75.0, 20.0, 5.0, 0.25, 50.0, 0.0, 120.0, 37.0,
                   rf_offset=1.0, rf_width=0.4)
    (o,) = bay_obstacles([bay], 0.0, strip=0.5)
    d = bay.reach + 0.5
    assert (o.x0, o.x1) == pytest.approx((190 - d, 215 + d))
    assert o.z0 < 40 - d and o.z1 > 75 + d  # rampes comprises


# --- pipeline complet (nervures, longeron, baie) ---------------------------------------------------------------


@pytest.fixture(scope="module")
def wide_run():
    cfg = _cfg()
    msgs = []
    run = pipeline.run_model(classify(wide_wing_bodies(), cfg), cfg, progress=msgs.append)
    return cfg, run, msgs


@pytest.mark.slow
def test_wide_wing_two_pieces_validated(wide_run):
    _, run, msgs = wide_run
    (b,) = run.bodies
    assert b.names == ("A", "B") and len(b.segments) == 2
    assert all(p.fits for p in b.placements)
    assert [r.ok for r in b.piece_reports] == [True, True] and b.report.ok and run.ok
    assert b.report.stats["path_length_m"] == pytest.approx(sum(r.stats["path_length_m"] for r in b.piece_reports))
    (n,) = b.notices
    assert "coupe dans la corde en 2 morceaux" in n and "P1S" in n and "SBAR_8" in n
    assert any("découpe automatique" in m for m in msgs)
    assert b.warnings == ()


@pytest.mark.slow
def test_wide_wing_plane_off_the_spar(wide_run):
    _, run, _ = wide_run
    (b,) = run.bodies
    (spar,) = [x for x in b.bores if x.cut_hole]
    keep_out = spar.radius + spar.sleeve + spar.strip
    # le plan de coupe passe hors du longeron (trou + manchon + bande) : un seul morceau contient le trou entier
    xs = sorted([(p.bounding_box(optimal=True).min.X, p.bounding_box(optimal=True).max.X) for p in b.segments])
    cut = xs[0][1]
    assert cut == pytest.approx(xs[1][0], abs=1e-3)
    assert abs(cut - spar.x) >= keep_out - 1e-3


@pytest.mark.slow
def test_wide_wing_export(wide_run, tmp_path):
    from beaver.bambu import read_template
    from beaver.export import export_results

    _, run, _ = wide_run
    paths = export_results(run, tmp_path, "aile", ["step", "stl", "3mf"], template=read_template(FIXTURE))
    names = sorted(p.name for p in paths)
    assert [n for n in names if n.endswith(".stl")] == ["aile-a.stl", "aile-b.stl"]
    with zipfile.ZipFile(tmp_path / "aile.3mf") as z:
        root = ET.fromstring(z.read("Metadata/model_settings.config"))
    assert len(root.findall("plate")) == 2
    html = (tmp_path / "aile-rapport.html").read_text(encoding="utf-8")
    assert "coupe dans la corde" in html and "A ✔" in html and "B ✔" in html


@pytest.mark.slow
def test_wide_wing_bay_in_the_way():
    # baie de servo à mi-corde (extrados), longeron loin de la coupe : le plan est décalé hors de la baie
    cfg = _cfg()
    bodies = wide_wing_bodies(spar_x=100.0, servo=(190.0, 215.0, 15.0, 45.0, 40.0, 75.0))
    run = pipeline.run_model(classify(bodies, cfg), cfg)
    (b,) = run.bodies
    (bay,) = b.bays
    assert b.names == ("A", "B") and all(r.ok for r in b.piece_reports), [r.errors[:3] for r in b.piece_reports]
    assert all(p.fits for p in b.placements)
    xs = sorted([(p.bounding_box(optimal=True).min.X, p.bounding_box(optimal=True).max.X) for p in b.segments])
    cut = xs[0][1]
    d = bay.reach + cfg.servo_strip()
    assert not (bay.x0 - d < cut < bay.x1 + d)
    assert "SERVO_1" in b.notices[0]


# --- validation des faces de coupe ------------------------------------------------------------------------------


def _face_layers(result):
    from shapely.geometry import box as sbox

    from beaver.validate import Layer, check_skin

    orig = Layer(1.0, (sbox(0, -10, 100, 10),))  # face de coupe en x = 100
    exempt = (sbox(99.95, -1e5, 100.05, 1e5), 0.15)  # bande ±0,05 ; fente de nervure ≈ 0,15 mm en couche
    layer = Layer(1.0, (result,))
    return check_skin(layer, orig, 0.5, 0.05), check_skin(layer, orig, 0.5, 0.05, exempt)


def test_cut_face_slit_mouth_tolerated():
    from shapely.geometry import box as sbox

    # fente de nervure (0,12 mm) qui longe la face de coupe et y débouche, de l'intrados jusqu'à 2 mm de l'extrados
    piece = sbox(0, -10, 100, 10).difference(sbox(99.9, -11, 100.1, 8))
    strict, tolerated = _face_layers(piece)
    assert strict and "peau entamée" in strict[0].message  # sans tolérance de face : signalée
    assert tolerated == []


def test_cut_face_notch_still_reported():
    from shapely.geometry import box as sbox

    # entaille 5 × 16 mm débouchant sur la face de coupe : la face reste une paroi contrôlée
    piece = sbox(0, -10, 100, 10).difference(sbox(95, -8, 101, 8))
    _, tolerated = _face_layers(piece)
    assert [i.check for i in tolerated] == ["skin"]
    # 16 mm moins l'écart toléré (0,15 mm) à chaque bout
    assert "peau entamée sur 15.7 mm" in tolerated[0].message


def test_notched_chord_piece_fails_validation():
    from build123d import Box, Pos

    from beaver.validate import validate

    wing = swept_wing(chord=400, span=40, code="4412")
    cfg = _cfg()
    seg = pipeline.segment_part("B", wing, cfg, lambda m: None, original=wing)
    piece, (ref, exempt) = seg.pieces[0], seg.checks[0]
    x_cut = piece.bounding_box(optimal=True).max.X
    assert validate(piece, cfg, original=ref, skin_exempt=exempt).ok
    notched = piece - Pos(x_cut, 10, 20) * Box(10, 16, 60)  # entaille sur la face de coupe
    report = validate(notched, cfg, original=ref, skin_exempt=exempt)
    assert not report.ok and any(i.check == "skin" and "peau entamée" in i.message for i in report.errors)


def test_failing_piece_fails_merged_report():
    from beaver.validate import Issue, Report

    stats = {"layers": 120.0, "height_mm": 120.0, "path_length_m": 1.0, "mass_g": 2.0}
    good = Report((), stats)
    bad = Report((Issue("skin", "error", 10.5, "peau entamée sur 16.0 mm"),), stats, {10.5: ()})
    merged = pipeline.merge_reports([good, bad], ["A", "B"], [3.0, 3.0], 120.0)
    assert not merged.ok
    (issue,) = merged.errors
    assert issue.message == "B : peau entamée sur 16.0 mm" and issue.z == pytest.approx(13.5)
    assert list(merged.failed_layers) == [pytest.approx(13.5)]
    assert merged.stats["layers"] == 120  # couches de la pièce, pas la somme des morceaux
    assert merged.stats["path_length_m"] == pytest.approx(2.0)
    # deux tronçons en Z, chacun coupé en deux : couches des deux tronçons
    split = pipeline.merge_reports([good] * 4, ["T1-A", "T1-B", "T2-A", "T2-B"], [0, 0, 120, 120], 240.0)
    assert split.stats["layers"] == 240


def test_merged_report_draws_at_most_max_failed_layers():
    from beaver.validate import MAX_FAILED_LAYERS, Issue, Report

    stats = {"layers": 120.0, "height_mm": 120.0, "path_length_m": 1.0, "mass_g": 2.0}

    def bad(z0):
        zs = [z0 + i for i in range(MAX_FAILED_LAYERS)]
        return Report(tuple(Issue("skin", "error", z, "peau entamée") for z in zs), stats, {z: () for z in zs})

    merged = pipeline.merge_reports([bad(0.5), bad(0.75)], ["A", "B"], [0.0, 0.0], 120.0)
    assert len(merged.errors) == 2 * MAX_FAILED_LAYERS  # tous les défauts restent listés
    assert len(merged.failed_layers) == MAX_FAILED_LAYERS and min(merged.failed_layers) == 0.5


def test_notice_names_the_bed_of_the_template():
    from beaver.features.chord_cut import plan_layout

    wing = swept_wing(chord=400, span=60, code="4412")
    plan = _plan(wing, 60)
    (n,) = pipeline.layout_notices(plan, _cfg(), (256.0, 256.0))
    assert "plateau P1S (256 × 256 mm) même en diagonale" in n


# --- aperçu : plan de découpe visible, identique à la génération -----------------------------------------------


def _wide_preview(tmp_path, cfg, bodies=None):
    from beaver import preview
    from beaver.io_step import write_step

    prep = preview.prepare(write_step(tmp_path / "large.step", bodies or wide_wing_bodies()), cfg)
    plates = preview.plates_2d(prep, cfg)
    return preview.planform(prep, plates, cfg).to_dict(), preview.estimates(prep, plates, cfg)


def test_preview_shows_the_cut_plan(tmp_path):
    plan, est = _wide_preview(tmp_path, _cfg())
    a, b = plan["morceaux"]
    assert (a["nom"], b["nom"]) == ("A", "B") and (a["plateau"], b["plateau"]) == (1, 2)
    assert a["corps"] == b["corps"] == "Aile_large" and a["tient"] and b["tient"]
    assert (a["z0"], a["z1"]) == (pytest.approx(0.0, abs=1e-3), pytest.approx(120.0, abs=1e-3))
    (p,) = plan["plans_corde"]
    assert p["troncon"] == "T1" and p["evite"] == ["SBAR_8"] and p["inclinaison"] == pytest.approx(0, abs=0.1)
    # plan décalé hors du longeron (trou + manchon + bande) ; les deux morceaux se rejoignent sur le plan
    (spar,) = [t for t in plan["trous"] if t["type"] == "longeron"]
    assert abs(p["x0"] - spar["x"]) > spar["manchon"]
    assert a["droite"] == b["gauche"] == [p["x0"], p["x1"]]
    assert a["x1"] == pytest.approx(p["x0"]) and b["x0"] == pytest.approx(p["x0"])
    assert max(a["largeur"], a["profondeur"]) <= 246 and max(b["largeur"], b["profondeur"]) <= 246
    (n,) = plan["notices"]
    assert n.startswith("découpe automatique : coupe dans la corde en 2 morceaux") and "SBAR_8" in n
    assert plan["coupes"] == [] and plan["avertissements"] == []
    assert est["morceaux"] == 2 and len(est["troncons"]) == 1


def test_preview_chord_cut_off_is_refused_like_generation(tmp_path):
    plan, est = _wide_preview(tmp_path, _cfg(segment={"chord": "off"}))
    assert plan["morceaux"] == [] and est["morceaux"] is None and est["troncons"] is None
    (w,) = plan["avertissements"]
    assert "découpe en tronçons impossible" in w and "segment.chord: off" in w


@pytest.mark.slow
def test_preview_cut_plan_matches_generation(wide_run, tmp_path):
    cfg, run, _ = wide_run
    (b,) = run.bodies
    plan, est = _wide_preview(tmp_path, cfg)
    pieces = plan["morceaux"]
    assert [p["nom"] for p in pieces] == list(b.names)
    assert [(p["z0"], p["z1"]) for p in pieces] == [pytest.approx(s, abs=1e-3) for s in b.spans]
    assert plan["notices"] == list(b.notices)
    assert est["morceaux"] == len(b.segments) and est["plaques"] == b.plates
    # chaque morceau généré s'étend en corde entre les plans de l'aperçu (même plan de coupe) ; `wide_run` part des
    # corps en mémoire, l'aperçu du STEP écrit : repères comparés depuis le bord d'attaque de chaque peau
    dx = b.original.bounding_box(optimal=True).min.X - pieces[0]["x0"]
    for p, solid in zip(pieces, b.segments):
        bb = solid.bounding_box(optimal=True)
        assert (bb.min.X - dx, bb.max.X - dx) == (pytest.approx(p["x0"], abs=0.05), pytest.approx(p["x1"], abs=0.05))
    for p, pl in zip(pieces, b.placements):
        assert p["tient"] == pl.fits and p["rotation"] == pytest.approx(pl.angle_deg, abs=1.0)
