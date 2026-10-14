from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool

import pytest

from beaver import validate as validate_mod
from beaver.config import load_config
from beaver.testing.synthetic import broken_wing, naca_wing, ribbed_wing
from beaver.validate import validate

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
    [("hole", ["connectivity"]), ("island", ["connectivity"]), ("hole_narrow", ["connectivity"])],
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


def test_narrow_slit_is_only_a_warning():
    report = validate(broken_wing("narrow"), CFG, heights=ZS)
    assert report.ok
    assert {i.check for i in report.warnings} == {"narrow_slit"}


@pytest.fixture(scope="module")
def hole_layers():
    heights = [1.0 + i * 0.45 for i in range(200)]  # ≥ PARALLEL_MIN_LAYERS, espacées sur l'envergure
    assert len(heights) >= validate_mod.PARALLEL_MIN_LAYERS
    return broken_wing("hole"), heights


def _serial(monkeypatch, part, heights):
    with monkeypatch.context() as m:
        m.setattr(validate_mod, "PARALLEL_MIN_LAYERS", 10**9)
        return validate(part, CFG, heights=heights)


def test_parallel_and_serial_agree(monkeypatch, hole_layers):
    part, heights = hole_layers
    pools = []

    class SpyPool(ProcessPoolExecutor):
        def __init__(self, *a, **k):
            pools.append(self)
            super().__init__(*a, **k)

    monkeypatch.setattr(validate_mod, "ProcessPoolExecutor", SpyPool)
    par = validate(part, CFG, heights=heights)
    assert len(pools) == 1  # le pool a bien servi
    ser = _serial(monkeypatch, part, heights)
    assert len(pools) == 1  # … et pas en série
    assert not par.ok and len(par.errors) == 200
    assert par.issues == ser.issues and par.stats == ser.stats


class _NoProcesses:
    def __init__(self, *a, **k):
        raise OSError("processus indisponibles")


class _BrokenPool:
    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def map(self, *a, **k):
        raise BrokenProcessPool("un processus est mort")


@pytest.mark.parametrize("pool", [_NoProcesses, _BrokenPool])
def test_parallel_failure_falls_back_to_serial(monkeypatch, hole_layers, pool):
    part, heights = hole_layers
    ser = _serial(monkeypatch, part, heights)
    monkeypatch.setattr(validate_mod, "ProcessPoolExecutor", pool)
    assert validate(part, CFG, heights=heights).issues == ser.issues


def test_thin_neck_detected_by_validate():
    from build123d import Align, Box, Location

    # deux blocs reliés par un col de 0,3 mm : un seul contour, mais le slicer le séparerait
    part = Box(25, 10, 4, align=(Align.MIN, Align.MIN, Align.MIN)) - Box(
        5, 4.85, 4, align=(Align.MIN, Align.MIN, Align.MIN)
    ).moved(Location((10, 0, 0))) - Box(5, 4.85, 4, align=(Align.MIN, Align.MIN, Align.MIN)).moved(
        Location((10, 5.15, 0))
    )
    report = validate(part, CFG, heights=[2.0])
    assert checks(report) == ["material_width"]
