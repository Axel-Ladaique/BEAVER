import json
import shutil
import zipfile
from pathlib import Path

import pytest
import xml.etree.ElementTree as ET

from build123d import Box, Pos

from aerorib import pipeline
from aerorib.bambu import BambuError, read_template
from aerorib.classify import classify
from aerorib.config import load_config
from aerorib.export import export_results
from aerorib.io_step import read_step
from aerorib.pipeline import PipelineError, run_model
from aerorib.testing.synthetic import wing_x_bodies

NS = {"c": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}
FIXTURE = Path(__file__).parent / "fixtures" / "bambu_p1s_pla_aero.3mf"


def _cfg(template=FIXTURE, **segment):
    return load_config(overrides={
        "material_settings": {"layer_height": 1.0},
        "rib_layers": [{"spacing": 20}],
        "passages": {"mode": "link"},
        "bambu": {"template": str(template)},
        "segment": segment,
    })


@pytest.fixture(scope="module")
def run3():
    cfg = _cfg(max_height=30)
    messages = []
    run = run_model(classify(wing_x_bodies(), cfg), cfg, progress=messages.append)
    return cfg, run, messages


def test_three_segments(run3):
    _, run, messages = run3
    (body,) = run.bodies
    assert len(body.segments) == 3
    for s in body.segments:
        assert s.is_valid and len(s.solids()) == 1
        bb = s.bounding_box()
        assert bb.min.Z == pytest.approx(0.0, abs=1e-3) and bb.max.Z <= 30 + 1e-3
    assert any("découpe en 3 tronçons" in m for m in messages)
    assert body.result.bounding_box().max.Z == pytest.approx(80.0, abs=1e-3)


def test_export_segments(run3, tmp_path):
    cfg, run, _ = run3
    paths = export_results(run, tmp_path, "aile", ["step", "stl", "3mf"], template=read_template(FIXTURE))
    names = sorted(p.name for p in paths)
    assert [n for n in names if n.endswith(".stl")] == ["aile-t1.stl", "aile-t2.stl", "aile-t3.stl"]
    assert "aile.step" in names and "aile.3mf" in names
    bodies = read_step(tmp_path / "aile.step")
    assert sorted(b.path[-1] for b in bodies) == ["Aile_test_t1", "Aile_test_t2", "Aile_test_t3"]
    with zipfile.ZipFile(tmp_path / "aile.3mf") as z:
        plates = ET.fromstring(z.read("Metadata/model_settings.config")).findall("plate")
    assert len(plates) == 3
    with zipfile.ZipFile(tmp_path / "aile.3mf") as z:
        root = ET.fromstring(z.read("Metadata/model_settings.config"))
        obj_names = [
            next(m.get("value") for m in o.findall("metadata") if m.get("key") == "name")
            for o in root.findall("object")
        ]
        assert obj_names == ["Aile_test_t1", "Aile_test_t2", "Aile_test_t3"]
        heights = []
        for k in (1, 2, 3):
            m = ET.fromstring(z.read(f"3D/Objects/object_{k}.model"))
            zs = [float(v.get("z")) for v in m.iterfind(".//c:vertex", NS)]
            heights.append(max(zs) - min(zs))
    for h, seg in zip(heights, run.bodies[0].segments):
        assert h == pytest.approx(seg.bounding_box().max.Z, abs=0.05)


def test_3mf_without_template_writes_nothing(run3, tmp_path):
    _, run, _ = run3
    with pytest.raises(BambuError):
        export_results(run, tmp_path / "o", "aile", ["step", "stl", "3mf"])
    assert not (tmp_path / "o").exists()


def _part():
    return Pos(0, 0, 40) * Box(20, 20, 80)


def _template_height(tmp_path, h):
    p = tmp_path / "t.3mf"
    with zipfile.ZipFile(FIXTURE) as zi, zipfile.ZipFile(p, "w") as zo:
        for item in zi.infolist():
            data = zi.read(item.filename)
            if item.filename == "Metadata/project_settings.config":
                st = json.loads(data)
                st["printable_height"] = str(h)
                data = json.dumps(st).encode()
            zo.writestr(item, data)
    return p


def test_height_precedence(tmp_path):
    msgs = []
    cfg = _cfg(template=_template_height(tmp_path, 50))
    pieces, warns = pipeline._segment("B", _part(), cfg, msgs.append)
    assert len(pieces) == 2 and warns == ()  # 50 - 5 = 45 < 80
    cfg = load_config(overrides={"bambu": {"template": None}})  # repli imprimante : 256 - 5
    pieces, warns = pipeline._segment("B", _part(), cfg, msgs.append)
    assert len(pieces) == 1 and warns == ()
    cfg = _cfg(max_height=100)  # max_height prioritaire sur le gabarit (250 - 5)
    pieces, warns = pipeline._segment("B", _part(), cfg, msgs.append)
    assert len(pieces) == 1 and warns == ()


def test_warnings(tmp_path):
    msgs = []
    pieces, warns = pipeline._segment("B", _part(), _cfg(max_height=300), msgs.append)
    assert len(pieces) == 1 and any("max_height" in w for w in warns)
    assert all(f"⚠ {w}" in msgs for w in warns)
    bad = tmp_path / "bad.3mf"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("a.txt", "x")
    pieces, warns = pipeline._segment("B", _part(), _cfg(template=bad), msgs.append)
    assert any("illisible" in w for w in warns)


def test_mode_off_warns_not_raises():
    msgs = []
    cfg = _cfg(mode="off", max_height=30)
    pieces, warns = pipeline._segment("B", _part(), cfg, msgs.append)
    assert len(pieces) == 1
    assert any("dépasse le volume d'impression" in w and "segment.mode: off" in w for w in warns)
    assert any(m.startswith("⚠ B") for m in msgs)


def test_single_segment_unchanged(tmp_path):
    cfg = _cfg()  # hauteur utile 245 : pas de découpe
    run = run_model(classify(wing_x_bodies(), cfg), cfg)
    (body,) = run.bodies
    assert len(body.segments) == 1
    a, b = body.segments[0].bounding_box(), body.result.bounding_box()
    for u, v in zip((a.min.X, a.min.Y, a.min.Z, a.max.X, a.max.Y, a.max.Z), (b.min.X, b.min.Y, b.min.Z, b.max.X, b.max.Y, b.max.Z)):
        assert u == pytest.approx(v, abs=1e-6)
    assert body.warnings == ()
    out = tmp_path
    export_results(run, out, "aile", ["step", "stl"])
    assert (out / "aile.stl").exists() and not (out / "aile-t1.stl").exists()
    assert [x.path[-1] for x in read_step(out / "aile.step")] == ["Aile_test"]


def test_too_wide(tmp_path):
    small = tmp_path / "small.3mf"
    with zipfile.ZipFile(FIXTURE) as zi, zipfile.ZipFile(small, "w") as zo:
        for item in zi.infolist():
            data = zi.read(item.filename)
            if item.filename == "Metadata/project_settings.config":
                s = json.loads(data)
                s["printable_area"] = ["0x0", "60x60", "60x60", "0x60"]
                data = json.dumps(s).encode()
            zo.writestr(item, data)
    cfg = _cfg(template=small, max_height=30)
    with pytest.raises(PipelineError, match="plus grand que le plateau"):
        run_model(classify(wing_x_bodies(), cfg), cfg)


def test_missing_template_warns(tmp_path):
    msgs = []
    cfg = _cfg(template=tmp_path / "absent.3mf")
    pieces, warns = pipeline._segment("B", _part(), cfg, msgs.append)
    assert len(pieces) == 1 and any("introuvable" in w and "volume de l'imprimante" in w for w in warns)


def test_cuts_are_relative_to_part_root_not_world_zero():
    cfg = _cfg(cuts_at=[30])
    part = Pos(0, 0, 50) * Box(20, 20, 80)  # emplanture à z = 10, pas 0
    pieces, _ = pipeline._segment("B", part, cfg, lambda m: None)
    assert [round(p.bounding_box().max.Z, 3) for p in pieces] == [30.0, 50.0]
