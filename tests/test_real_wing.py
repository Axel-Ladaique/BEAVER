from pathlib import Path

import pytest

from aerorib.classify import classify
from aerorib.config import load_config
from aerorib.io_step import read_step
from aerorib.pipeline import run_model

FUSION = Path(__file__).parent / "fixtures" / "fusion_aile_4412_simple_sbar.step"


@pytest.mark.slow
def test_real_fusion_wing_is_printable():
    cfg = load_config()
    run = run_model(classify(read_step(FUSION), cfg), cfg)
    assert run.orientation.source == "longeron SBAR_14mm_1"
    (body,) = run.bodies
    assert body.report.ok, [i.message for i in body.report.errors][:5]
    assert body.report.stats["layers"] == 1050
    assert sum(b.cut_hole for b in body.bores) == 1
    assert sum(not b.cut_hole for b in body.bores) == 4  # les 4 alésages Ø2,4
    assert body.plates > 50
