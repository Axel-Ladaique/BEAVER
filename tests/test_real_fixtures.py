from pathlib import Path

import pytest

from aerorib.classify import classify
from aerorib.config import load_config
from aerorib.io_step import read_step

FIXTURES = Path(__file__).parent / "fixtures"
FILES = sorted(p for p in FIXTURES.iterdir() if p.suffix.lower() in {".step", ".stp"})


@pytest.mark.skipif(not FILES, reason="aucune fixture réelle dans tests/fixtures/")
@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_real_export_has_skin_and_spar(path: Path):
    model = classify(read_step(path), load_config())
    assert model.of("skin"), "aucune peau détectée"
    assert model.of("spar"), "aucun longeron détecté"


@pytest.mark.parametrize(
    "inch", [p for p in FILES if p.stem.endswith("_inch")], ids=lambda p: p.name
)
def test_inch_export_matches_mm_twin(inch: Path):
    twin = inch.with_name(inch.stem.removesuffix("_inch") + inch.suffix)
    if not twin.exists():
        # extension du jumeau éventuellement différente (.STEP / .step / .stp)
        twin = next(
            (p for p in FILES if p.stem == inch.stem.removesuffix("_inch")), twin
        )
    if not twin.exists():
        pytest.skip(f"jumeau en mm absent : {twin.name}")
    cfg = load_config()
    d_in = classify(read_step(inch), cfg).of("spar")[0].spar.diameter
    d_mm = classify(read_step(twin), cfg).of("spar")[0].spar.diameter
    assert d_in == pytest.approx(d_mm, rel=1e-4)
