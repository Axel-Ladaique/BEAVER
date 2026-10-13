"""Coupes en Z choisies selon l'empreinte sur le plateau (et pas seulement selon la hauteur)."""

from pathlib import Path

import numpy as np
import pytest
from shapely.geometry import box

from aerorib import pipeline
from aerorib.config import load_config
from aerorib.layout import fit_on_bed
from aerorib.segment import MIN_SEGMENT, SegmentError, plan_pieces
from aerorib.slicing import section_sampler
from aerorib.testing.synthetic import swept_wing

FIXTURE = Path(__file__).parent / "fixtures" / "bambu_p1s_pla_aero.3mf"  # plateau 256 × 256, hauteur 250
BED = (256.0, 256.0)
MARGIN = 5.0


def _cfg(**segment):
    return load_config(overrides={"bambu": {"template": str(FIXTURE)}, "segment": segment})


def _sections(solid):
    return section_sampler(solid, 0.05)


def _segments_fit(solid, cuts, height):
    """Chaque tronçon réel (sections denses, 1 mm) tient sur le plateau, éventuellement tourné."""
    sample = _sections(solid)
    bounds = [0.0, *cuts, height]
    out = []
    for z0, z1 in zip(bounds, bounds[1:]):
        zs = np.clip(np.linspace(z0, z1, int(z1 - z0) + 1), 0.01, height - 0.01)
        pts = np.concatenate([p for p in sample(zs) if len(p)])
        out.append(fit_on_bed(pts, BED, MARGIN).fits)
    return out


# --- planificateur seul ---------------------------------------------------------------------------------------

def test_straight_wing_one_piece():
    wing = swept_wing(chord=150, span=200)
    plan = plan_pieces(200, 245, _sections(wing), BED, MARGIN)
    assert plan.cuts == () and plan.fits and plan.chord is None and plan.reason is None
    assert plan.placements[0].angle_deg == 0


def test_swept_wing_more_segments_each_fits():
    wing = swept_wing(chord=60, span=200, sweep_deg=60)  # empreinte ≈ 406 × 7 mm : ne tient pas même en diagonale
    assert not fit_on_bed(box(0, -4, 406, 4), BED, MARGIN).fits
    plan = plan_pieces(200, 245, _sections(wing), BED, MARGIN)
    assert len(plan.cuts) >= 1 and plan.height_n == 1  # la hauteur seule n'en demandait qu'un
    assert plan.fits and plan.chord is None
    assert all(_segments_fit(wing, plan.cuts, 200))
    assert "empreinte" in plan.reason and "plateau" in plan.reason
    # n minimal : un tronçon de moins ne tient pas
    n = len(plan.cuts) + 1
    if n > 2:
        fewer = [200 * i / (n - 1) for i in range(1, n - 1)]
        assert not all(_segments_fit(wing, fewer, 200))


def test_swept_wing_diagonal_is_enough():
    wing = swept_wing(chord=60, span=200, sweep_deg=45)  # empreinte ≈ 260 × 7 : tient en diagonale
    plan = plan_pieces(200, 245, _sections(wing), BED, MARGIN)
    assert plan.cuts == () and plan.fits and plan.placements[0].angle_deg != 0


def test_dihedral_more_segments():
    wing = swept_wing(chord=60, span=200, dihedral_deg=60)
    plan = plan_pieces(200, 245, _sections(wing), BED, MARGIN)
    assert len(plan.cuts) >= 1 and plan.fits and plan.chord is None
    assert all(_segments_fit(wing, plan.cuts, 200))


def test_wide_chord_needs_chord_cut_not_z_cuts():
    wing = swept_wing(chord=400, span=60)  # section 400 × 48 : ne tient pas même en diagonale
    plan = plan_pieces(60, 245, _sections(wing), BED, MARGIN)
    assert plan.cuts == ()  # aucune coupe en Z supplémentaire : elles n'aideraient pas
    assert not plan.fits
    assert plan.chord is not None
    assert plan.chord.z0 == pytest.approx(0, abs=0.1) and plan.chord.z1 == pytest.approx(60, abs=0.1)
    assert plan.chord.size[0] == pytest.approx(400, abs=1)


def test_height_cuts_kept_with_chord_needed():
    wing = swept_wing(chord=400, span=300)
    plan = plan_pieces(300, 245, _sections(wing), BED, MARGIN)
    assert len(plan.cuts) == 1 and plan.chord is not None  # coupe de hauteur seulement


def test_height_only_reason():
    wing = swept_wing(chord=60, span=300)
    plan = plan_pieces(300, 245, _sections(wing), BED, MARGIN)
    assert plan.cuts == (150.0,) and plan.fits and plan.height_n == 2
    assert "hauteur" in plan.reason


def test_explicit_cuts_respected_even_if_not_fitting():
    wing = swept_wing(chord=60, span=200, sweep_deg=60)
    plan = plan_pieces(200, 245, _sections(wing), BED, MARGIN, cuts_at=[30])
    assert plan.cuts == (30.0,)
    assert [p.fits for p in plan.placements] == [True, False]
    assert plan.reason is None  # pas une décision automatique


def test_stops_at_min_segment():
    # empreinte qui ne tient jamais par tronçons : section qui tient seule, mais forte flèche sur peu de hauteur
    wing = swept_wing(chord=60, span=20, sweep_deg=89)
    height = 20.0
    plan = plan_pieces(height, 245, _sections(wing), BED, MARGIN)
    assert height / (len(plan.cuts) + 1) >= MIN_SEGMENT - 1e-9
    assert not plan.fits and plan.chord is None


def test_invalid_explicit_cuts_still_raise():
    wing = swept_wing(chord=60, span=200)
    with pytest.raises(SegmentError):
        plan_pieces(200, 245, _sections(wing), BED, MARGIN, cuts_at=[5])


# --- pipeline ---------------------------------------------------------------------------------------------------

def test_segment_swept_wing_cut_and_notified():
    msgs = []
    wing = swept_wing(chord=60, span=200, sweep_deg=60)
    pieces, warns = pipeline._segment("B", wing, _cfg(), msgs.append)
    assert len(pieces) >= 2 and warns == ()
    line = next(m for m in msgs if "découpe automatique" in m)
    assert f"{len(pieces)} tronçons en Z" in line and "empreinte" in line
    placements, pwarns = pipeline.place_pieces("B", pieces, _cfg(), msgs.append)
    assert all(p.fits for p in placements) and pwarns == ()


def test_segment_wide_chord_warns_chord_needed():
    msgs = []
    wing = swept_wing(chord=400, span=60)
    pieces, warns = pipeline._segment("B", wing, _cfg(), msgs.append)
    assert len(pieces) == 1
    (w,) = [w for w in warns if "corde" in w]
    assert "la section 400×48 mm" in w and "ne tient pas sur le plateau" in w
    assert "une coupe dans la corde sera nécessaire" in w
    assert f"⚠ {w}" in msgs


def test_segment_explicit_cuts_not_fitting_warned():
    msgs = []
    cfg = _cfg(cuts_at=[30])
    wing = swept_wing(chord=60, span=200, sweep_deg=60)
    pieces, _ = pipeline._segment("B", wing, cfg, msgs.append)
    assert len(pieces) == 2
    assert not any("découpe automatique" in m for m in msgs)
    placements, pwarns = pipeline.place_pieces("B", pieces, cfg, msgs.append)
    assert not placements[1].fits and any("tronçon 2" in w for w in pwarns)


def test_non_fitting_placement_is_consistent_with_zero_angle():
    wing = swept_wing(chord=400, span=60)
    (pl,), _ = pipeline.place_pieces("B", (wing,), _cfg(), lambda m: None)
    bb = wing.bounding_box(optimal=True)
    w, d = bb.max.X - bb.min.X, bb.max.Y - bb.min.Y
    assert pl.angle_deg == 0 and not pl.fits
    assert pl.size == pytest.approx((w, d), abs=1e-6)
    assert pl.slack == pytest.approx(min(246 - w, 246 - d), abs=1e-6)


def test_preview_cuts_match_generation(tmp_path):
    from aerorib import preview
    from aerorib.io_step import NamedBody, write_step

    step = write_step(tmp_path / "fleche.step", [NamedBody(("Aile_fleche",), swept_wing(chord=60, span=200, sweep_deg=60))])
    cfg = load_config(overrides={"bambu": {"template": str(FIXTURE)}, "orientation": {"axis": [0, 0, 1]}})
    prep = preview.prepare(step, cfg)
    cuts, warns = preview._cuts(prep, cfg)
    assert warns == [] and len(cuts) >= 1
    pieces, _ = pipeline._segment("B", prep.skin, cfg, lambda m: None)
    heights = [p.bounding_box(optimal=True).max.Z for p in pieces]
    assert np.cumsum(heights)[:-1] == pytest.approx(cuts, abs=1e-3)
