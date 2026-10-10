import zipfile
from dataclasses import replace
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pytest
import trimesh

from aerorib.bambu import BambuError, plate_origin, read_template, write_project

FIXTURE = Path(__file__).parent / "fixtures" / "bambu_p1s_pla_aero.3mf"
NS = {"c": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}


def test_read_template():
    t = read_template(FIXTURE)
    assert t.bed == (256.0, 256.0)
    assert t.height == 250.0
    assert t.settings["spiral_mode"] == "1"
    assert t.settings["filament_settings_id"] == ["Bambu PLA Aero @BBL X1C"]


def test_read_template_invalid(tmp_path):
    p = tmp_path / "x.3mf"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("a.txt", "hello")
    with pytest.raises(BambuError, match="Bambu"):
        read_template(p)


def test_plate_origin():
    assert plate_origin(0, 1, (256, 256)) == (0, 0)
    assert plate_origin(0, 3, (256, 256)) == (0, 0)
    assert plate_origin(1, 3, (256, 256)) == pytest.approx((307.2, 0))
    assert plate_origin(2, 3, (256, 256)) == pytest.approx((0, -307.2))


def _read_mesh(data: bytes):
    root = ET.fromstring(data)
    v = np.array(
        [[float(e.get(a)) for a in "xyz"] for e in root.iterfind(".//c:vertex", NS)]
    )
    f = np.array(
        [[int(e.get(a)) for a in ("v1", "v2", "v3")] for e in root.iterfind(".//c:triangle", NS)]
    )
    return v, f


def test_write_project(tmp_path):
    t = read_template(FIXTURE)
    objs = [
        (f"seg{k}", trimesh.creation.box(extents=(20 + k, 10, 30)).apply_translation((50 * k, 7, 100)))
        for k in range(3)
    ]
    out = write_project(t, objs, tmp_path / "out.3mf")
    with zipfile.ZipFile(out) as z, zipfile.ZipFile(FIXTURE) as zt:
        names = z.namelist()
        for n in ("Metadata/project_settings.config", "Metadata/slice_info.config",
                  "[Content_Types].xml"):
            assert z.read(n) == zt.read(n)
        assert not [n for n in names if "plate_" in n and n.endswith((".png", ".json"))]
        assert sorted(n for n in names if n.startswith("3D/Objects/")) == [
            f"3D/Objects/object_{k}.model" for k in (1, 2, 3)
        ]
        rels = z.read("3D/_rels/3dmodel.model.rels").decode()
        for k in (1, 2, 3):
            assert f"/3D/Objects/object_{k}.model" in rels

        for rn in ("_rels/.rels", "3D/_rels/3dmodel.model.rels"):
            for r in ET.fromstring(z.read(rn)):
                assert r.get("Target").lstrip("/") in names
        cfg = ET.fromstring(z.read("Metadata/model_settings.config"))
        assert all(o.find("metadata[@face_count]").get("face_count") == "12" for o in cfg.findall("object"))
        assert all(o.find("part/mesh_stat").get("face_count") == "12" for o in cfg.findall("object"))
        plates = cfg.findall("plate")
        assert len(plates) == 3
        obj_ids = []
        for i, p in enumerate(plates, 1):
            md = {m.get("key"): m.get("value") for m in p.findall("metadata")}
            assert md["plater_id"] == str(i)
            insts = p.findall("model_instance")
            assert len(insts) == 1
            obj_ids.append(
                {m.get("key"): m.get("value") for m in insts[0].findall("metadata")}["object_id"]
            )
        assert len(set(obj_ids)) == 3
        assert sorted(o.get("id") for o in cfg.findall("object")) == sorted(obj_ids)
        assert len(cfg.find("assemble").findall("assemble_item")) == 3

        model = ET.fromstring(z.read("3D/3dmodel.model"))
        items = model.findall(".//c:item", NS)
        assert len(items) == 3
        for i, it in enumerate(items):
            ox, oy = plate_origin(i, 3, t.bed)
            tr = [float(x) for x in it.get("transform").split()]
            assert tr[9] == pytest.approx(ox + 128, abs=1e-6)
            assert tr[10] == pytest.approx(oy + 128, abs=1e-6)
            assert tr[11] == pytest.approx(15, abs=1e-6)  # h/2
            assert it.get("objectid") == obj_ids[i]
        for k in (1, 2, 3):
            v, f = _read_mesh(z.read(f"3D/Objects/object_{k}.model"))
            assert len(f) == 12
            assert v[:, 2].min() + 15 == pytest.approx(0, abs=1e-6)
            assert v[:, 2].max() == pytest.approx(15, abs=1e-6)
            c = (v[:, :2].min(0) + v[:, :2].max(0)) / 2
            assert c == pytest.approx([0, 0], abs=1e-6)


def test_write_project_empty(tmp_path):
    with pytest.raises(BambuError):
        write_project(read_template(FIXTURE), [], tmp_path / "o.3mf")


def _template_variant(tmp_path, drop=(), app=None):
    out = tmp_path / "variant.3mf"
    with zipfile.ZipFile(FIXTURE) as zt, zipfile.ZipFile(out, "w") as z:
        for n in zt.namelist():
            if n in drop:
                continue
            data = zt.read(n)
            if n == "3D/3dmodel.model" and app:
                data = data.replace(b"BambuStudio-02.02.01.60", app.encode())
            z.writestr(n, data)
    return out


def _box(name="s"):
    return (name, trimesh.creation.box(extents=(10, 10, 10)))


def test_write_project_copies_application_from_template(tmp_path):
    t = read_template(_template_variant(tmp_path, app="BambuStudio-09.9.9.9"))
    out = write_project(t, [_box()], tmp_path / "o.3mf")
    with zipfile.ZipFile(out) as z:
        model = z.read("3D/3dmodel.model").decode()
    assert '<metadata name="Application">BambuStudio-09.9.9.9</metadata>' in model


def test_write_project_object_ids_like_bambu(tmp_path):
    out = write_project(read_template(FIXTURE), [_box("a"), _box("b")], tmp_path / "o.3mf")
    with zipfile.ZipFile(out) as z:
        for k in (1, 2):
            mesh_obj = ET.fromstring(z.read(f"3D/Objects/object_{k}.model")).find(".//c:object", NS)
            assert mesh_obj.get("id") == str(2 * k - 1)
        model = ET.fromstring(z.read("3D/3dmodel.model"))
        comps = model.findall(".//c:component", NS)
        assert [c.get("objectid") for c in comps] == ["1", "3"]
        assert [o.get("id") for o in model.findall("c:resources/c:object", NS)] == ["2", "4"]
        cfg = ET.fromstring(z.read("Metadata/model_settings.config"))
        assert [o.find("part").get("id") for o in cfg.findall("object")] == ["1", "3"]


def test_write_project_without_optional_files(tmp_path):
    t = read_template(_template_variant(tmp_path, drop=("Metadata/slice_info.config",)))
    out = write_project(t, [_box()], tmp_path / "o.3mf")
    with zipfile.ZipFile(out) as z:
        assert "Metadata/slice_info.config" not in z.namelist()
        assert "Metadata/project_settings.config" in z.namelist()


def test_write_project_template_without_settings_is_error(tmp_path):
    t = read_template(FIXTURE)
    broken = _template_variant(tmp_path, drop=("Metadata/project_settings.config",))
    with pytest.raises(BambuError, match="project_settings"):
        write_project(replace(t, path=broken), [_box()], tmp_path / "o.3mf")
