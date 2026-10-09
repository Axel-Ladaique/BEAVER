from pathlib import Path

import pytest
from build123d import Box, Compound, Cylinder, Location, export_step

from aerorib.io_step import NamedBody, StepReadError, _brep_names, read_step, write_step


def _spar():
    return Cylinder(7, 200).moved(Location((10, 0, 100)))


def test_roundtrip_flat_assembly(tmp_path: Path):
    skin = Box(100, 20, 200)
    out = write_step(
        tmp_path / "a.step",
        [NamedBody(("SKIN_wing",), skin), NamedBody(("SPAR_main",), _spar())],
    )
    bodies = read_step(out)
    assert [b.path for b in bodies] == [("AERORIB", "SKIN_wing"), ("AERORIB", "SPAR_main")]
    assert [b.name for b in bodies] == ["SKIN_wing", "SPAR_main"]
    assert bodies[0].solid.volume == pytest.approx(100 * 20 * 200, rel=1e-6)


def test_nested_assembly_paths(tmp_path: Path):
    skin = Box(100, 20, 200)
    skin.label = "SKIN"
    spar = _spar()
    spar.label = "SPAR_main"
    left = Compound(children=[skin, spar], label="LEFT")
    root = Compound(children=[left], label="PLANE")
    export_step(root, str(tmp_path / "n.step"))
    paths = [b.path for b in read_step(tmp_path / "n.step")]
    assert paths == [("PLANE", "LEFT", "SKIN"), ("PLANE", "LEFT", "SPAR_main")]


def test_single_solid_file(tmp_path: Path):
    box = Box(10, 10, 10)
    box.label = "Wing_tip"
    export_step(box, str(tmp_path / "s.step"))
    bodies = read_step(tmp_path / "s.step")
    assert len(bodies) == 1
    assert bodies[0].name == "Wing_tip"


def test_display():
    assert NamedBody(("A", "B"), Box(1, 1, 1)).display == "A/B"
    assert NamedBody((), Box(1, 1, 1)).display == "<sans nom>"


def test_missing_file(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        read_step(tmp_path / "nope.step")


def test_garbage_file(tmp_path: Path):
    p = tmp_path / "bad.step"
    p.write_text("not a step file", encoding="utf-8")
    with pytest.raises(StepReadError):
        read_step(p)


FUSION = Path(__file__).parent / "fixtures" / "fusion_aile_4412_simple_sbar.step"


def test_brep_names_from_text():
    assert _brep_names(FUSION) == [("SBAR_14mm_1", 3), ("WING_1", 9)]


def test_fusion_body_names_recovered():
    bodies = read_step(FUSION)
    assert [b.path for b in bodies] == [
        ("Aile_4412_simple_Sbar_v1", "SBAR_14mm_1"),
        ("Aile_4412_simple_Sbar_v1", "WING_1"),
    ]
    assert [len(b.solid.faces()) for b in bodies] == [3, 9]


def test_roundtrip_shared_tshape_keeps_both_names(tmp_path: Path):
    b = Box(10, 10, 10)
    out = write_step(
        tmp_path / "s.step",
        [NamedBody(("SKIN_L",), b), NamedBody(("SKIN_R",), b.moved(Location((50, 0, 0))))],
    )
    bodies = read_step(out)
    assert sorted(b.name for b in bodies) == ["SKIN_L", "SKIN_R"]
    xs = {b.name: b.solid.center().X for b in bodies}
    assert xs["SKIN_L"] == pytest.approx(0, abs=1e-6)
    assert xs["SKIN_R"] == pytest.approx(50, abs=1e-6)


def test_roundtrip_identical_bodies_same_place(tmp_path: Path):
    out = write_step(
        tmp_path / "i.step",
        [NamedBody(("SKIN_a",), Box(10, 10, 10)), NamedBody(("SKIN_b",), Box(10, 10, 10))],
    )
    assert sorted(b.name for b in read_step(out)) == ["SKIN_a", "SKIN_b"]
