import json
import shutil
import zipfile
from pathlib import Path

import pytest
import xml.etree.ElementTree as ET

from aerorib.bambu import read_template
from aerorib.classify import classify
from aerorib.config import load_config
from aerorib.export import export_results
from aerorib.io_step import read_step
from aerorib.pipeline import PipelineError, run_model
from aerorib.testing.synthetic import wing_x_bodies

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


def test_single_segment_unchanged():
    cfg = _cfg()  # hauteur utile 245 : pas de découpe
    run = run_model(classify(wing_x_bodies(), cfg), cfg)
    (body,) = run.bodies
    assert len(body.segments) == 1


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
