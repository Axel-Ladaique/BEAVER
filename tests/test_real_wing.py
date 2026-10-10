from pathlib import Path

import pytest
from shapely.geometry import LineString, Polygon

from aerorib.classify import classify
from aerorib.config import load_config
from aerorib.io_step import read_step
from aerorib.pipeline import run_model
from aerorib.slicing import slice_layers

FUSION = Path(__file__).parent / "fixtures" / "fusion_aile_4412_simple_sbar.step"


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
