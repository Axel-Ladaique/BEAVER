"""Pas de micro-arêtes au bout de l'aile ; l'export ne laisse pas de triangulation sur la pièce."""

from pathlib import Path

import numpy as np
from OCP.BRep import BRep_Tool
from OCP.TopLoc import TopLoc_Location

from aerorib.bambu import read_template
from aerorib.classify import classify
from aerorib.config import load_config
from aerorib.export import export_results
from aerorib.features.ribs import _MIN_VERTEX_GAP, _end_vertices, _merge_close, _shrink_off
from aerorib.pipeline import run_model
from aerorib.testing.synthetic import wing_x_bodies

TEMPLATE = Path(__file__).parent / "fixtures" / "bambu_p1s_pla_aero.3mf"


def test_merge_close_keeps_band_vertices_and_drops_near_grid_ones():
    grid = np.array([0.0, 10.0, 20.00001, 30.0, 40.03])
    band = [0.0, 5.0, 20.0, 40.0]
    s = _merge_close(grid, keep=band)
    assert set(band) <= set(s.tolist())  # sommets de bande (garantie band_tol) tous gardés
    assert 20.00001 not in s and 40.03 not in s and 10.0 in s and 30.0 in s
    assert np.diff(s).min() >= _MIN_VERTEX_GAP


def test_merge_close_without_keep_spaces_vertices():
    s = _merge_close([0.0, 1e-5, 0.02, 0.06, 0.07, 1.0])
    assert s.tolist() == [0.0, 0.06, 1.0]


def test_end_window_has_three_shared_vertices():
    # fenêtre de bout a ± h : seuls a − h, a, a + h ; un sommet à moins de l'écart min hors fenêtre est retiré aussi
    s = _end_vertices(np.array([-5.0, 99.0, 99.8, 100.1, 100.33, 103.0]), -5.0, 103.0, [(100.0, 0.3)])
    assert s.tolist() == [-5.0, 99.0, 99.7, 100.0, 100.3, 103.0]
    # bornes de tronçon jamais dans une fenêtre : le tronçon raccourcit
    assert _shrink_off([(-5.0, 100.1), (99.9, 120.0)], [(99.65, 100.35)]) == [(-5.0, 99.65), (100.35, 120.0)]


def _faces_meshed(solid) -> int:
    loc = TopLoc_Location()
    return sum(BRep_Tool.Triangulation_s(f.wrapped, loc) is not None for f in solid.faces())


def test_wing_tip_has_no_micro_edges_and_stays_valid_after_export(tmp_path):
    # envergure 90 = 6 demi-périodes du losange (pas 30, 45°) : deux plaques se croisent exactement sur chaque
    # plan de bout — avant correction, arêtes de 3e-4 mm sur le bout
    cfg = load_config(overrides={"material_settings": {"layer_height": 1.0}})
    run = run_model(classify(wing_x_bodies(span=90.0), cfg), cfg)
    (body,) = run.bodies
    (seg,) = body.segments
    assert min(e.length for e in seg.edges()) >= 1e-3
    assert seg.is_valid
    export_results(run, tmp_path, "aile", ["stl", "3mf"], template=read_template(TEMPLATE))
    assert _faces_meshed(seg) == 0  # pas de triangulation résiduelle
    assert seg.is_valid and len(seg.solids()) == 1
