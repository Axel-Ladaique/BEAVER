import json
import zipfile
from dataclasses import replace
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pytest
import trimesh

from beaver.bambu import BambuError, plate_origin, read_template, write_project

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
        for n in ("Metadata/slice_info.config", "[Content_Types].xml"):
            assert z.read(n) == zt.read(n)
        # réglages recopiés, seule la liste des clés déclarées au preset système est complétée
        new, old = (json.loads(f.read("Metadata/project_settings.config")) for f in (z, zt))
        assert {k for k in new if new[k] != old.get(k)} <= {"different_settings_to_system"}
        assert "brim_type" in new["different_settings_to_system"][0].split(";")
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


# --- réglages vase mode imposés -------------------------------------------------------------------
ASA = Path(__file__).parents[1] / "src" / "beaver" / "templates" / "p1s_asa_aero.3mf"
PLA = Path(__file__).parents[1] / "src" / "beaver" / "templates" / "p1s_pla_aero.3mf"


def _settings_bytes(path: Path) -> bytes:
    with zipfile.ZipFile(path) as z:
        return z.read("Metadata/project_settings.config")


def test_pla_settings_byte_identical(tmp_path):
    from beaver.bambu import vase_warning

    t = read_template(PLA)
    assert vase_warning(t) is None
    out = write_project(t, [_box()], tmp_path / "o.3mf")
    assert _settings_bytes(out) == _settings_bytes(PLA)


def test_asa_vase_forced_keeps_filament(tmp_path):
    import json

    from beaver.bambu import VASE_SETTINGS, vase_warning

    t = read_template(ASA)
    assert t.settings["spiral_mode"] == "0"  # read_template lit le fichier d'origine
    w = vase_warning(t)
    assert w.startswith("gabarit p1s_asa_aero.3mf : réglages vase mode imposés (spiral_mode")
    out = write_project(t, [_box()], tmp_path / "o.3mf")
    new, old = json.loads(_settings_bytes(out)), json.loads(_settings_bytes(ASA))
    assert all(new[k] == v for k, v in VASE_SETTINGS.items())
    assert new["spiral_mode"] == "1" and new["wall_loops"] == "1" and new["top_shell_layers"] == "0"
    changed = {k for k in new if new[k] != old.get(k)}
    assert changed <= {*VASE_SETTINGS, "different_settings_to_system"} and "spiral_mode" in changed
    assert new["filament_settings_id"] == ["Bambu ASA-Aero @BBL X1C"]
    assert new["filament_type"] == ["ASA-AERO"] and new["nozzle_temperature"] == ["270"]
    assert _settings_bytes(out).endswith(b"\n")


def test_asa_template_resolves_and_run_writes_3mf(tmp_path):
    from typer.testing import CliRunner

    from beaver.cli import app
    from beaver.config import load_config, resolve_template
    from beaver.io_step import write_step
    from beaver.testing.synthetic import wing_x_bodies

    assert resolve_template(load_config(overrides={"material": "asa_aero"})).name == "p1s_asa_aero.3mf"
    src = write_step(tmp_path / "aile.step", wing_x_bodies())
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        "material: asa_aero\nmaterial_settings: {layer_height: 1.0}\nrib_layers: [{spacing: 20}]\n"
        "passages: {mode: link}\nsegment: {max_height: 30}\n", encoding="utf-8")
    r = CliRunner().invoke(app, ["run", str(src), "-c", str(cfg), "--out", str(tmp_path / "res"), "--3mf"])
    # code 1 toléré : la pièce synthétique reste « non imprimable » avec le surplomb ASA (40°), mais les
    # fichiers sont écrits pour inspection ; seul l'écriture du 3MF est testée ici.
    assert r.exit_code in (0, 1) and "erreur" not in r.output.lower(), r.output
    assert "gabarit p1s_asa_aero.3mf : réglages vase mode imposés" in r.output
    assert b'"spiral_mode": "1"' in _settings_bytes(tmp_path / "res" / "aile.3mf")


# --- fond ouvert et bordure -----------------------------------------------------------------------
TEMPLATES = sorted((Path(__file__).parents[1] / "src" / "beaver" / "templates").glob("*.3mf"))


@pytest.mark.parametrize("path", TEMPLATES, ids=lambda p: p.name)
def test_templates_no_bottom_layers_and_brim(path):
    """Aucune couche inférieure (pièce ouverte en bas), bordure extérieure de 5 mm, pas de jupe."""
    import json

    s = json.loads(_settings_bytes(path))
    assert s["bottom_shell_layers"] == "0"
    assert s["brim_type"] == "outer_only" and s["brim_width"] == "5"
    assert s["skirt_loops"] == "0"
    # déclarées au preset système, sinon Bambu Studio les remet à ses valeurs (bordure auto, 3 couches)
    declared = s["different_settings_to_system"][0].split(";")
    assert {"bottom_shell_layers", "brim_type", "brim_width", "skirt_loops"} <= set(declared)


def test_old_template_bottom_layers_forced_to_zero(tmp_path):
    """Gabarit utilisateur en vase mode mais avec 3 couches inférieures : 0 imposé, avertissement."""
    import json

    from beaver.bambu import vase_warning

    src = tmp_path / "ancien.3mf"
    with zipfile.ZipFile(FIXTURE) as zt, zipfile.ZipFile(src, "w") as z:
        for n in zt.namelist():
            data = zt.read(n)
            if n == "Metadata/project_settings.config":
                data = data.replace(b'"bottom_shell_layers": "0"', b'"bottom_shell_layers": "3"')
            z.writestr(n, data)
    t = read_template(src)
    assert vase_warning(t) == "gabarit ancien.3mf : réglages vase mode imposés (bottom_shell_layers)"
    out = write_project(t, [_box()], tmp_path / "o.3mf")
    new, old = json.loads(_settings_bytes(out)), json.loads(_settings_bytes(src))
    assert new["bottom_shell_layers"] == "0"
    assert {k for k in new if new[k] != old.get(k)} == {"bottom_shell_layers", "different_settings_to_system"}
    assert "bottom_shell_layers" in new["different_settings_to_system"][0].split(";")
