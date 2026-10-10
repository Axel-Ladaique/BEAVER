"""Lecture d'un gabarit de projet Bambu Studio (.3mf) et écriture d'un projet multi-plateaux."""

from __future__ import annotations

import json
import math
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import quoteattr

import numpy as np
import trimesh

# Fichiers du gabarit recopiés à l'identique (octets) : on ne reconstruit jamais les réglages.
_COPIED = (
    "[Content_Types].xml",
    "_rels/.rels",
    "Metadata/project_settings.config",
    "Metadata/slice_info.config",
)
_IDENTITY = "1 0 0 0 1 0 0 0 1"
_GAP = 1.2  # espacement inter-plateaux de Bambu Studio (PartPlate)


class BambuError(ValueError):
    """Gabarit Bambu invalide ou projet impossible à écrire."""


@dataclass(frozen=True)
class Template:
    path: Path
    settings: dict
    bed: tuple[float, float]
    height: float


def read_template(path: Path) -> Template:
    path = Path(path)
    try:
        with zipfile.ZipFile(path) as z:
            raw = z.read("Metadata/project_settings.config")
    except (zipfile.BadZipFile, KeyError, OSError) as exc:
        raise BambuError(
            f"« {path} » n'est pas un projet Bambu Studio valide "
            "(Metadata/project_settings.config introuvable)."
        ) from exc
    try:
        settings = json.loads(raw)
        pts = [tuple(float(c) for c in p.split("x")) for p in settings["printable_area"]]
        bed = (max(p[0] for p in pts), max(p[1] for p in pts))
        height = float(settings["printable_height"])
    except (ValueError, KeyError, TypeError) as exc:
        raise BambuError(
            f"« {path} » : réglages Bambu illisibles (printable_area / printable_height)."
        ) from exc
    return Template(path=path, settings=settings, bed=bed, height=height)


def plate_origin(i: int, n: int, bed: tuple[float, float]) -> tuple[float, float]:
    """Origine du plateau i (0-based) parmi n, selon la disposition de Bambu Studio."""
    cols = math.ceil(math.sqrt(n))
    col, row = i % cols, i // cols
    return (col * bed[0] * _GAP, -row * bed[1] * _GAP)


def _fmt(x: float) -> str:
    s = f"{x:.6f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def _uid(*parts) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "aerorib/" + "/".join(map(str, parts))))


_NS = (
    'unit="millimeter" xml:lang="en-US" '
    'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" '
    'xmlns:BambuStudio="http://schemas.bambulab.com/package/2021" '
    'xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06" '
    'requiredextensions="p"'
)
_XML = '<?xml version="1.0" encoding="UTF-8"?>\n'


def _centered(mesh: trimesh.Trimesh) -> np.ndarray:
    v = np.asarray(mesh.vertices, dtype=float)
    lo, hi = v.min(axis=0), v.max(axis=0)
    return v - np.array([(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, lo[2]])


def _object_file(k: int, mesh: trimesh.Trimesh) -> str:
    v = _centered(mesh)
    f = np.asarray(mesh.faces)
    out = [
        _XML, f"<model {_NS}>\n",
        ' <metadata name="BambuStudio:3mfVersion">1</metadata>\n',
        " <resources>\n",
        f'  <object id="1" p:UUID="{_uid("obj", k)}" type="model">\n',
        "   <mesh>\n    <vertices>\n",
    ]
    out += [f'     <vertex x="{_fmt(a)}" y="{_fmt(b)}" z="{_fmt(c)}"/>\n' for a, b, c in v]
    out.append("    </vertices>\n    <triangles>\n")
    out += [f'     <triangle v1="{a}" v2="{b}" v3="{c}"/>\n' for a, b, c in f]
    out.append("    </triangles>\n   </mesh>\n  </object>\n </resources>\n <build/>\n</model>\n")
    return "".join(out)


def write_project(
    template: Template, objects: list[tuple[str, trimesh.Trimesh]], path: Path
) -> Path:
    """Écrit un projet 3MF : un objet par plateau, dans l'ordre, centré et posé à z = 0."""
    if not objects:
        raise BambuError("Aucun objet à écrire dans le projet Bambu.")
    n = len(objects)
    path = Path(path)
    W, D = template.bed

    res, items, rels, objcfg, plates, assemble = [], [], [], [], [], []
    for k, (name, _mesh) in enumerate(objects, 1):
        oid = 2 * k
        ox, oy = plate_origin(k - 1, n, template.bed)
        tr = f"{_IDENTITY} {_fmt(ox + W / 2)} {_fmt(oy + D / 2)} 0"
        qn = quoteattr(name)
        res.append(
            f'  <object id="{oid}" p:UUID="{_uid("res", k)}" type="model">\n'
            "   <components>\n"
            f'    <component p:path="/3D/Objects/object_{k}.model" objectid="1" '
            f'p:UUID="{_uid("cmp", k)}" transform="{_IDENTITY} 0 0 0"/>\n'
            "   </components>\n  </object>\n"
        )
        items.append(
            f'  <item objectid="{oid}" p:UUID="{_uid("item", k)}" transform="{tr}" printable="1"/>\n'
        )
        rels.append(
            f' <Relationship Target="/3D/Objects/object_{k}.model" Id="rel-{k}" '
            'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>\n'
        )
        objcfg.append(
            f'  <object id="{oid}">\n'
            f'    <metadata key="name" value={qn}/>\n'
            '    <metadata key="extruder" value="1"/>\n'
            '    <part id="1" subtype="normal_part">\n'
            f'      <metadata key="name" value={qn}/>\n'
            '      <metadata key="matrix" value="1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1"/>\n'
            "    </part>\n  </object>\n"
        )
        plates.append(
            "  <plate>\n"
            f'    <metadata key="plater_id" value="{k}"/>\n'
            f'    <metadata key="plater_name" value={qn}/>\n'
            '    <metadata key="locked" value="false"/>\n'
            '    <metadata key="filament_map_mode" value="Auto For Flush"/>\n'
            '    <metadata key="filament_maps" value="1"/>\n'
            "    <model_instance>\n"
            f'      <metadata key="object_id" value="{oid}"/>\n'
            '      <metadata key="instance_id" value="0"/>\n'
            f'      <metadata key="identify_id" value="{100 + k}"/>\n'
            "    </model_instance>\n  </plate>\n"
        )
        assemble.append(
            f'   <assemble_item object_id="{oid}" instance_id="0" transform="{tr}" '
            'offset="0 0 0" />\n'
        )

    model = (
        f"{_XML}<model {_NS}>\n"
        ' <metadata name="Application">BambuStudio-02.02.01.60</metadata>\n'
        ' <metadata name="BambuStudio:3mfVersion">1</metadata>\n'
        " <resources>\n" + "".join(res) + " </resources>\n"
        f' <build p:UUID="{_uid("build")}">\n' + "".join(items) + " </build>\n</model>\n"
    )
    rels_xml = (
        f'{_XML}<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
        + "".join(rels) + "</Relationships>\n"
    )
    cfg = (
        f"{_XML}<config>\n" + "".join(objcfg) + "".join(plates)
        + "  <assemble>\n" + "".join(assemble) + "  </assemble>\n</config>\n"
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(template.path) as zt, zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", zt.read("[Content_Types].xml"))
        z.writestr("_rels/.rels", zt.read("_rels/.rels"))
        z.writestr("3D/_rels/3dmodel.model.rels", rels_xml)
        z.writestr("3D/3dmodel.model", model)
        for k, (_name, mesh) in enumerate(objects, 1):
            z.writestr(f"3D/Objects/object_{k}.model", _object_file(k, mesh))
        z.writestr("Metadata/model_settings.config", cfg)
        for name in _COPIED[2:]:
            z.writestr(name, zt.read(name))
    return path
