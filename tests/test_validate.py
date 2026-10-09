import pytest

from aerorib.config import load_config
from aerorib.testing.synthetic import broken_wing, naca_wing, ribbed_wing
from aerorib.validate import validate

ZS = [5.0, 50.0, 95.0]
CFG = load_config()


def checks(report):
    return sorted({i.check for i in report.errors})


def test_good_ribbed_wing_passes():
    wing, _ = naca_wing()
    report = validate(ribbed_wing(), CFG, original=wing, heights=ZS)
    assert report.ok, report.issues
    assert report.failed_layers == {}


@pytest.mark.parametrize(
    "kind, expected",
    [("hole", ["connectivity"]), ("island", ["connectivity"]), ("narrow", ["narrow_slit"])],
)
def test_broken_variants_detected(kind, expected):
    report = validate(broken_wing(kind), CFG, heights=ZS)
    assert not report.ok
    assert checks(report) == expected
    assert set(report.failed_layers) == set(ZS)


def test_skin_damage_needs_original():
    wing, _ = naca_wing()
    assert validate(broken_wing("skin"), CFG, heights=ZS).ok
    report = validate(broken_wing("skin"), CFG, original=wing, heights=ZS)
    assert checks(report) == ["skin"]


def test_stats():
    wing, _ = naca_wing()
    report = validate(wing, CFG, heights=ZS)
    assert report.stats["layers"] == 3
    # périmètre mesuré du NACA 4412 corde 150 ≈ 307 mm, 3 couches
    assert report.stats["path_length_m"] == pytest.approx(3 * 0.307, rel=0.02)
    assert report.stats["mass_g"] > 0


def test_default_heights_cover_part():
    wing, _ = naca_wing(span=2.0)
    report = validate(wing, CFG)
    assert report.stats["layers"] == 10  # 2 mm / 0.2 mm
    assert report.stats["height_mm"] == pytest.approx(2.0)
