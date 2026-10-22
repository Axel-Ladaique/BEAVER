"""Lecture d'un gabarit de projet Bambu Studio (.3mf) et écriture d'un projet multi-plateaux."""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import quoteattr

import numpy as np
import trimesh

# Fichiers du gabarit recopiés à l'identique (octets) : on ne reconstruit jamais les réglages.
_REQUIRED = ("[Content_Types].xml", "Metadata/project_settings.config")
_OPTIONAL = ("Metadata/slice_info.config",)  # recopié seulement s'il est dans le gabarit
_DEFAULT_APP = "BambuStudio-02.02.01.60"
_DEFAULT_VERSION = "1"
_IDENTITY = "1 0 0 0 1 0 0 0 1"
_GAP = 1.2  # espacement inter-plateaux de Bambu Studio (PartPlate)


# Réglages vase mode imposés à l'écriture (valeurs du gabarit PLA Aero, qui est en vase mode).
# Jamais de clé filament / matière ni imprimante : elles restent celles du gabarit.
# bottom_shell_layers = 0 : pièces ouvertes en bas. La 1re couche reste un tour fermé (Bambu Studio
# n'y met jamais la spirale avec une bordure ou skirt_height ≥ 1), la spirale part de la 2e. La bordure (brim_type) est un réglage du gabarit : outer_only (5 mm)
# dans les gabarits embarqués, le brim « auto » de Bambu Studio sortant rarement ; pas de jupe.
VASE_SETTINGS = {
    "spiral_mode": "1",
    "spiral_mode_smooth": "0",
    "wall_loops": "1",
    "top_shell_layers": "0",
    "bottom_shell_layers": "0",
    "sparse_infill_density": "0%",
    "skeleton_infill_density": "0%",
    "skin_infill_density": "0%",
}
# Bambu Studio ne garde d'un projet que les clés listées dans different_settings_to_system (1re entrée :
# process) ; les autres reprennent la valeur du preset système (bordure auto, 3 couches inférieures…).
# Toutes les clés imposées ou réglées par le gabarit y sont donc ajoutées.
DIFF_KEYS = (*VASE_SETTINGS, "brim_type", "brim_width", "brim_object_gap", "skirt_loops", "skirt_height")


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


def _vase_keys(settings: dict) -> list[str]:
    """Clés de VASE_SETTINGS dont la valeur du gabarit diffère (ou qui manquent)."""
    return [k for k, v in VASE_SETTINGS.items() if settings.get(k) != v]


def vase_warning(template: Template) -> str | None:
    """Avertissement français si write_project doit imposer le vase mode à ce gabarit, sinon None."""
    keys = _vase_keys(template.settings)
    if not keys:
        return None
    return f"gabarit {template.path.name} : réglages vase mode imposés ({', '.join(keys)})"


def _diff_list(settings: dict) -> list[str]:
    """different_settings_to_system complété des DIFF_KEYS présentes dans les réglages."""
    diff = list(settings.get("different_settings_to_system") or ["", "", ""])
    keys = [k for k in diff[0].split(";") if k] if diff else []
    keys += [k for k in DIFF_KEYS if k in settings and k not in keys]
    return [";".join(sorted(keys)), *diff[1:]]


def _force_vase(raw: bytes) -> bytes:
    """project_settings.config avec VASE_SETTINGS imposés et DIFF_KEYS déclarées ; octets inchangés si déjà conformes."""
    try:
        settings = json.loads(raw)
    except ValueError as exc:
        raise BambuError("project_settings.config illisible dans le gabarit.") from exc
    settings.update(VASE_SETTINGS)
    diff = _diff_list(settings)
    if not _vase_keys(json.loads(raw)) and settings.get("different_settings_to_system") == diff:
        return raw
    settings["different_settings_to_system"] = diff
    out = json.dumps(settings, indent=4, ensure_ascii=False)
    return (out + ("\n" if raw.endswith(b"\n") else "")).encode("utf-8")


def plate_origin(i: int, n: int, bed: tuple[float, float]) -> tuple[float, float]:
    """Origine du plateau i (0-based) parmi n, selon la disposition de Bambu Studio."""
    cols = math.ceil(math.sqrt(n))
    col, row = i % cols, i // cols
    return (col * bed[0] * _GAP, -row * bed[1] * _GAP)


def _fmt(x: float) -> str:
    s = f"{x:.6f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def _uid(*parts) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "beaver/" + "/".join(map(str, parts))))


ROOT_RELS = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
    ' <Relationship Target="/3D/3dmodel.model" Id="rel-1" '
    'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>\n'
    "</Relationships>"
)

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
    return v - (lo + hi) / 2


def _rotation(angle_deg: float) -> str:
    """Matrice 3MF d'une rotation anti-horaire autour de Z : 3×3 par lignes (convention p' = p·M, la
    translation suit, comme dans le reste du fichier)."""
    if not angle_deg:
        return _IDENTITY
    c, s = math.cos(math.radians(angle_deg)), math.sin(math.radians(angle_deg))
    return f"{_fmt(c)} {_fmt(s)} 0 {_fmt(-s)} {_fmt(c)} 0 0 0 1"


def _rotated_center(mesh: trimesh.Trimesh, angle_deg: float) -> tuple[float, float]:
    """Centre XY de l'emprise du maillage centré, après rotation autour de son centre."""
    v = _centered(mesh)[:, :2]
    a = math.radians(angle_deg)
    c, s = math.cos(a), math.sin(a)
    x, y = c * v[:, 0] - s * v[:, 1], s * v[:, 0] + c * v[:, 1]
    return float(x.max() + x.min()) / 2, float(y.max() + y.min()) / 2


def _height(mesh: trimesh.Trimesh) -> float:
    v = np.asarray(mesh.vertices, dtype=float)
    return float(v[:, 2].max() - v[:, 2].min())


def _template_meta(zt: zipfile.ZipFile, name: str, default: str) -> str:
    """Métadonnée du 3D/3dmodel.model du gabarit (Application, version du format), sinon `default`."""
    try:
        text = zt.read("3D/3dmodel.model").decode("utf-8", errors="replace")
    except KeyError:
        return default
    m = re.search(rf'<metadata name="{re.escape(name)}">([^<]*)</metadata>', text)
    return m.group(1) if m and m.group(1) else default


def _object_file(k: int, mesh: trimesh.Trimesh, version: str = _DEFAULT_VERSION) -> str:
    v = _centered(mesh)
    f = np.asarray(mesh.faces)
    out = [
        _XML, f"<model {_NS}>\n",
        f' <metadata name="BambuStudio:3mfVersion">{version}</metadata>\n',
        " <resources>\n",
        f'  <object id="{2 * k - 1}" p:UUID="{_uid("obj", k)}" type="model">\n',
        "   <mesh>\n    <vertices>\n",
    ]
    out += [f'     <vertex x="{_fmt(a)}" y="{_fmt(b)}" z="{_fmt(c)}"/>\n' for a, b, c in v]
    out.append("    </vertices>\n    <triangles>\n")
    out += [f'     <triangle v1="{a}" v2="{b}" v3="{c}"/>\n' for a, b, c in f]
    out.append("    </triangles>\n   </mesh>\n  </object>\n </resources>\n <build/>\n</model>\n")
    return "".join(out)


def write_project(
    template: Template, objects: list[tuple], path: Path
) -> Path:
    """Écrit un projet 3MF : un objet par plateau, dans l'ordre, centré et posé à z = 0.

    Chaque objet est (nom, maillage) ou (nom, maillage, angle_deg) : rotation anti-horaire autour de Z (à travers
    le centre du maillage), écrite dans les transformations de l'objet ; l'emprise tournée reste centrée."""
    if not objects:
        raise BambuError("Aucun objet à écrire dans le projet Bambu.")
    objects = [(o[0], o[1], float(o[2]) if len(o) > 2 else 0.0) for o in objects]
    n = len(objects)
    path = Path(path)
    W, D = template.bed

    try:
        with zipfile.ZipFile(template.path) as zt:
            names = set(zt.namelist())
            missing = [n for n in _REQUIRED if n not in names]
            if missing:
                raise BambuError(f"« {template.path} » : {', '.join(missing)} introuvable dans le gabarit.")
            app = _template_meta(zt, "Application", _DEFAULT_APP)
            version = _template_meta(zt, "BambuStudio:3mfVersion", _DEFAULT_VERSION)
            copied = {n: zt.read(n) for n in (*_REQUIRED, *_OPTIONAL) if n in names}
    except (zipfile.BadZipFile, OSError) as exc:
        raise BambuError(f"« {template.path} » n'est pas un projet Bambu Studio valide.") from exc
    res, items, rels, objcfg, plates, assemble = [], [], [], [], [], []
    for k, (name, mesh, angle) in enumerate(objects, 1):
        oid = 2 * k  # objet-assemblage ; son maillage (object_k.model) porte l'id 2k-1, comme Bambu Studio
        mid = oid - 1
        ox, oy = plate_origin(k - 1, n, template.bed)
        cx, cy = _rotated_center(mesh, angle) if angle else (0.0, 0.0)
        tr = f"{_rotation(angle)} {_fmt(ox + W / 2 - cx)} {_fmt(oy + D / 2 - cy)} {_fmt(_height(mesh) / 2)}"
        qn = quoteattr(name)
        res.append(
            f'  <object id="{oid}" p:UUID="{_uid("res", k)}" type="model">\n'
            "   <components>\n"
            f'    <component p:path="/3D/Objects/object_{k}.model" objectid="{mid}" '
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
            f'    <metadata face_count="{len(mesh.faces)}"/>\n'
            f'    <part id="{mid}" subtype="normal_part">\n'
            f'      <metadata key="name" value={qn}/>\n'
            '      <metadata key="matrix" value="1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1"/>\n'
            f'      <mesh_stat face_count="{len(mesh.faces)}" edges_fixed="0" degenerate_facets="0" '
            'facets_removed="0" facets_reversed="0" backwards_edges="0"/>\n'
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
        f' <metadata name="Application">{app}</metadata>\n'
        f' <metadata name="BambuStudio:3mfVersion">{version}</metadata>\n'
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
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", copied["[Content_Types].xml"])
        z.writestr("_rels/.rels", ROOT_RELS)
        z.writestr("3D/_rels/3dmodel.model.rels", rels_xml)
        z.writestr("3D/3dmodel.model", model)
        for k, (_name, mesh, _angle) in enumerate(objects, 1):
            z.writestr(f"3D/Objects/object_{k}.model", _object_file(k, mesh, version))
        z.writestr("Metadata/model_settings.config", cfg)
        for name, data in copied.items():
            if name == "Metadata/project_settings.config":
                data = _force_vase(data)
            if name != "[Content_Types].xml":
                z.writestr(name, data)
    return path


def open_in_bambu(path: Path) -> None:
    """Ouvre le projet dans Bambu Studio sans bloquer ; BambuError (français) si l'ouverture échoue."""
    path = Path(path)
    try:
        if sys.platform == "darwin":
            if Path("/Applications/BambuStudio.app").exists():
                subprocess.Popen(["open", "-a", "BambuStudio", str(path)])
            else:
                subprocess.Popen(["open", str(path)])
        elif sys.platform == "win32":
            exe = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Bambu Studio" / "bambu-studio.exe"
            if exe.is_file():
                subprocess.Popen([str(exe), str(path)])
            else:
                os.startfile(str(path))  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except OSError as exc:
        raise BambuError(f"ouverture de Bambu Studio impossible : {exc.strerror or exc} (ouvrez {path} à la main)") from exc
