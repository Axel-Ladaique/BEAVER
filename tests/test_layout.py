import json
import shutil
import zipfile
from pathlib import Path
import xml.etree.ElementTree as ET

import numpy as np
import pytest
import trimesh
from shapely.geometry import box

from aerorib import pipeline
from aerorib.bambu import plate_origin, read_template, write_project
from aerorib.classify import classify
from aerorib.config import load_config
from aerorib.export import export_results
from aerorib.layout import fit_on_bed, format_rotations
from aerorib.pipeline import run_model
from aerorib.testing.synthetic import wing_x_bodies

NS = {"c": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}
FIXTURE = Path(__file__).parent / "fixtures" / "bambu_p1s_pla_aero.3mf"
BED = (256.0, 256.0)


def test_diagonal_rectangle():
    pl = fit_on_bed(box(0, 0, 300, 40), BED, 5)
    assert pl.fits and 30 < abs(pl.angle_deg) < 60
    assert pl.size[0] <= 246 and pl.size[1] <= 246 and pl.slack >= 0


def test_straight_rectangle():
    pl = fit_on_bed(box(0, 0, 200, 40), BED, 5)
    assert pl.angle_deg == 0 and pl.fits


def test_too_big():
    pl = fit_on_bed(box(0, 0, 400, 60), BED, 5)
    assert not pl.fits and pl.slack < 0


def test_rotations_lines():
    pls = [fit_on_bed(box(0, 0, 200, 40), BED, 5), fit_on_bed(box(0, 0, 300, 40), BED, 5)]
    (line,) = format_rotations(pls)
    assert line.startswith("T2 : tourné de ") and line.endswith("° pour tenir sur le plateau")


def _read_mesh(data):
    root = ET.fromstring(data)
    return np.array([[float(e.get(a)) for a in "xyz"] for e in root.iterfind(".//c:vertex", NS)])


def test_3mf_rotation(tmp_path):
    t = read_template(FIXTURE)
    mesh = trimesh.creation.box(extents=(100, 10, 20)).apply_translation((30, 5, 50))
    out = write_project(t, [("a", mesh), ("b", mesh, 30.0)], tmp_path / "o.3mf")
    th = np.radians(30)
    c, s = np.cos(th), np.sin(th)
    with zipfile.ZipFile(out) as z:
        model = ET.fromstring(z.read("3D/3dmodel.model"))
        cfg = ET.fromstring(z.read("Metadata/model_settings.config"))
        items = model.findall(".//c:item", NS)
        tr0 = [float(x) for x in items[0].get("transform").split()]
        assert tr0[:9] == [1, 0, 0, 0, 1, 0, 0, 0, 1]
        for tr in ([float(x) for x in items[1].get("transform").split()],
                   [float(x) for x in cfg.findall("assemble/assemble_item")[1].get("transform").split()]):
            assert tr[:9] == pytest.approx([c, s, 0, -s, c, 0, 0, 0, 1], abs=1e-5)
        tr = [float(x) for x in items[1].get("transform").split()]
        v = _read_mesh(z.read("3D/Objects/object_2.model"))
        # convention 3MF : p' = p·M + t
        p = v @ np.array(tr[:9]).reshape(3, 3) + np.array(tr[9:])
        ox, oy = plate_origin(1, 2, t.bed)
        ctr = (p[:, :2].min(0) + p[:, :2].max(0)) / 2
        assert ctr == pytest.approx([ox + 128, oy + 128], abs=1e-4)
        assert p[:, 2].min() == pytest.approx(0, abs=1e-6)
        # rotation anti-horaire : l'axe X du maillage tourne vers +Y
        i, j = v[:, 0].argmax(), v[:, 0].argmin()
        d = (p[i] - p[j])[:2]
        assert np.degrees(np.arctan2(d[1], d[0])) == pytest.approx(30, abs=1e-3)


def _cfg(**seg):
    return load_config(overrides={
        "material_settings": {"layer_height": 1.0},
        "rib_layers": [{"spacing": 20}],
        "passages": {"mode": "link"},
        "bambu": {"template": str(FIXTURE)},
        "segment": seg,
    })


def test_wide_chord_wing_rotates(tmp_path):
    cfg = _cfg()
    msgs = []
    run = run_model(classify(wing_x_bodies(chord=300, span=30), cfg), cfg, progress=msgs.append)
    (body,) = run.bodies
    (pl,) = body.placements
    assert pl.fits and 0 < abs(pl.angle_deg) < 90
    assert any("T1 : tourné de" in m and "pour tenir sur le plateau" in m for m in msgs)
    assert not any("plateau" in w for w in body.warnings)
    assert format_rotations(body.placements)
    paths = export_results(run, tmp_path, "aile", ["step", "3mf"], template=read_template(FIXTURE))
    p3 = next(p for p in paths if p.suffix == ".3mf")
    with zipfile.ZipFile(p3) as z:
        tr = [float(x) for x in ET.fromstring(z.read("3D/3dmodel.model")).find(".//c:item", NS).get("transform").split()]
    assert tr[0] == pytest.approx(np.cos(np.radians(pl.angle_deg)), abs=1e-5)
    assert tr[1] == pytest.approx(np.sin(np.radians(pl.angle_deg)), abs=1e-5)
    html = (tmp_path / "aile-rapport.html").read_text(encoding="utf-8")
    assert "tourné de" in html


def test_straight_wing_no_rotation():
    cfg = _cfg()
    run = run_model(classify(wing_x_bodies(), cfg), cfg)
    (body,) = run.bodies
    assert len(body.segments) == 1 and body.placements[0].angle_deg == 0
    assert format_rotations(body.placements) == []
