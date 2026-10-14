"""Lecture/écriture STEP en conservant les noms des corps (spec §6)."""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from pathlib import Path

from build123d import Compound, Shape, Solid, export_step, import_step

_GENERIC_LABELS = {"COMPOUND", "SOLID"}
_ENTITY = re.compile(r"#(\d+)\s*=\s*([A-Z_][A-Z0-9_]*)\s*\((.*?)\)\s*;", re.S)
_REF = re.compile(r"#(\d+)")
_NAME = re.compile(r"\s*'((?:[^']|'')*)'")
_STEP_ESCAPE = re.compile(
    r"\\X2\\((?:[0-9A-Fa-f]{4})+)\\X0\\"
    r"|\\X4\\((?:[0-9A-Fa-f]{8})+)\\X0\\"
    r"|\\X\\([0-9A-Fa-f]{2})"
    r"|\\S\\(.)",
    re.S,
)
_NON_ASCII = re.compile(r"[^\x00-\x7f]+")


def _decode_step_string(s: str) -> str:
    """Décode les échappements ISO-10303-21 d'une chaîne STEP (\\X2\\, \\X4\\, \\X\\, \\S\\, '')."""

    def repl(m: re.Match) -> str:
        if m.group(1):
            return bytes.fromhex(m.group(1)).decode("utf-16-be", errors="replace")
        if m.group(2):
            h = m.group(2)
            return "".join(chr(int(h[i : i + 8], 16)) for i in range(0, len(h), 8))
        if m.group(3):
            return chr(int(m.group(3), 16))
        return chr(ord(m.group(4)) + 128)

    return _STEP_ESCAPE.sub(repl, s).replace("''", "'")


class StepReadError(ValueError):
    """Le fichier STEP est illisible ou ne contient aucun solide."""


@dataclass(frozen=True)
class NamedBody:
    path: tuple[str, ...]
    solid: Solid
    note: str = ""

    @property
    def name(self) -> str:
        return self.path[-1] if self.path else ""

    @property
    def display(self) -> str:
        return "/".join(self.path) if self.path else "<sans nom>"


# type d'entité de surface STEP → nom du GeomAbs_SurfaceType lu par OCC
_STEP_SURFACES = {
    "PLANE": "Plane",
    "CYLINDRICAL_SURFACE": "Cylinder",
    "CONICAL_SURFACE": "Cone",
    "SPHERICAL_SURFACE": "Sphere",
    "TOROIDAL_SURFACE": "Torus",
    "DEGENERATE_TOROIDAL_SURFACE": "Torus",
    "B_SPLINE_SURFACE": "BSplineSurface",
    "B_SPLINE_SURFACE_WITH_KNOTS": "BSplineSurface",
    "BEZIER_SURFACE": "BSplineSurface",
    "RATIONAL_B_SPLINE_SURFACE": "BSplineSurface",
    "SURFACE_OF_REVOLUTION": "SurfaceOfRevolution",
    "SURFACE_OF_LINEAR_EXTRUSION": "SurfaceOfExtrusion",
    "OFFSET_SURFACE": "OffsetSurface",
}
_COMPLEX = re.compile(r"#(\d+)\s*=\s*\((.*?)\)\s*;", re.S)
_KEYWORD = re.compile(r"([A-Z_][A-Z0-9_]*)\s*\(")
_LENGTH_UNIT = re.compile(r"#\d+\s*=\s*\(([^;]*?\bLENGTH_UNIT\s*\([^;]*?)\)\s*;", re.S)
_SI_PREFIX = {"$": 1000.0, ".KILO.": 1e6, ".DECI.": 100.0, ".CENTI.": 10.0, ".MILLI.": 1.0, ".MICRO.": 1e-3}
_CONVERTED = {"INCH": 25.4, "FOOT": 304.8, "MILLIMETRE": 1.0, "MILLIMETER": 1.0, "METRE": 1000.0}
RADIUS_DECIMALS = 2  # rayons de cylindre arrondis à 0,01 mm dans la signature


@dataclass(frozen=True)
class Signature:
    """Empreinte géométrique d'un solide : types de surfaces (triés) et rayons des cylindres (mm)."""

    types: tuple[str, ...]
    radii: tuple[float, ...]


@dataclass(frozen=True)
class _BrepEntry:
    name: str
    n_faces: int
    signature: Signature | None = None


def solid_signature(solid: Solid) -> Signature:
    """Signature des faces OCC d'un solide (types GeomAbs, rayons de cylindre)."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_SurfaceType

    types, radii = [], []
    for face in solid.faces():
        surf = BRepAdaptor_Surface(face.wrapped)
        kind = GeomAbs_SurfaceType(surf.GetType()).name.removeprefix("GeomAbs_")
        if kind == "BezierSurface":
            kind = "BSplineSurface"
        types.append(kind)
        if kind == "Cylinder":
            radii.append(round(surf.Cylinder().Radius(), RADIUS_DECIMALS))
    return Signature(tuple(sorted(types)), tuple(sorted(radii)))


def _length_factor(data: str) -> float:
    """Facteur fichier → mm de l'unité de longueur déclarée (OCC convertit en mm à la lecture)."""
    m = _LENGTH_UNIT.search(data)
    if not m:
        return 1.0
    body = m.group(1)
    conv = re.search(r"CONVERSION_BASED_UNIT\s*\(\s*'([^']*)'", body)
    if conv:
        return _CONVERTED.get(conv.group(1).strip().upper(), 1.0)
    si = re.search(r"SI_UNIT\s*\(\s*([^,\s]+)\s*,\s*\.METRE\.", body)
    if si:
        return _SI_PREFIX.get(si.group(1).upper(), 1.0)
    return 1.0


def _surface_kind(kind: str, args: str) -> str:
    if kind:
        return _STEP_SURFACES.get(kind, kind)
    # entité complexe (« #n=(BOUNDED_SURFACE() B_SPLINE_SURFACE(…) …); »)
    for keyword in _KEYWORD.findall(args):
        if keyword in _STEP_SURFACES:
            return _STEP_SURFACES[keyword]
    return "Other"


def _brep_entries(path: Path) -> list[_BrepEntry]:
    """Entités MANIFOLD_SOLID_BREP / BREP_WITH_VOIDS : nom, nombre de faces de la coque, signature."""
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    if "DATA;" not in text:
        return []
    data = text.split("DATA;", 1)[1]
    entities = {int(m.group(1)): (m.group(2), m.group(3)) for m in _ENTITY.finditer(data)}
    for m in _COMPLEX.finditer(data):
        entities.setdefault(int(m.group(1)), ("", m.group(2)))
    factor = _length_factor(data)

    def signature(face_refs: list[int]) -> Signature | None:
        types, radii = [], []
        for ref in face_refs:
            face = entities.get(ref)
            if face is None or face[0] not in ("ADVANCED_FACE", "FACE_SURFACE"):
                return None
            surf_refs = _REF.findall(face[1])
            surf = entities.get(int(surf_refs[-1])) if surf_refs else None
            if surf is None:
                return None
            kind = _surface_kind(*surf)
            types.append(kind)
            if kind == "Cylinder":
                try:
                    radius = float(surf[1].rsplit(",", 1)[1]) * factor
                except (IndexError, ValueError):
                    return None
                radii.append(round(radius, RADIUS_DECIMALS))
        return Signature(tuple(sorted(types)), tuple(sorted(radii)))

    found = []
    for kind, args in entities.values():
        if kind not in ("MANIFOLD_SOLID_BREP", "BREP_WITH_VOIDS"):
            continue
        name_match = _NAME.match(args)
        refs = [int(r) for r in _REF.findall(args)]
        if not name_match or not refs or refs[0] not in entities:
            continue
        name = _decode_step_string(name_match.group(1)).strip()
        if name:
            face_refs = [int(r) for r in _REF.findall(entities[refs[0]][1])]
            found.append(_BrepEntry(name, len(face_refs), signature(face_refs)))
    return found


def _brep_names(path: Path) -> list[tuple[str, int]]:
    """Noms des entités MANIFOLD_SOLID_BREP / BREP_WITH_VOIDS et nombre de faces de leur coque."""
    return [(e.name, e.n_faces) for e in _brep_entries(path)]


def read_step(path: str | Path) -> list[NamedBody]:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"fichier introuvable : {path}")
    # OCP affiche sur stderr un message d'erreur brut pour les non-STEP : on filtre avant.
    with path.open("rb") as fh:
        if not fh.read(64).lstrip().startswith(b"ISO-10303-21"):
            raise StepReadError(f"{path} : ce fichier n'est pas un STEP (en-tête ISO-10303-21 absent)")
    try:
        root = import_step(str(path))
    except Exception as exc:  # OCP lève des types variés
        raise StepReadError(f"{path} : lecture STEP impossible ({exc})") from exc

    # (chemin, solide, anonyme ?)
    found: list[tuple[tuple[str, ...], Solid, bool]] = []

    def walk(shape: Shape, prefix: tuple[str, ...]) -> None:
        label = (shape.label or "").strip()
        generic = not label or label.upper() in _GENERIC_LABELS
        here = prefix if generic else prefix + (label,)
        children = list(getattr(shape, "children", ()) or ())
        if children:
            for child in children:
                walk(child, here)
            return
        solids = shape.solids()
        anonymous = generic or len(solids) > 1
        for solid in solids:
            found.append((here, solid, anonymous))

    walk(root, ())
    if not found:
        raise StepReadError(f"{path} : aucun solide trouvé")

    named = {p[-1] for p, _, anon in found if p and not anon}
    pool = [e for e in _brep_entries(path) if e.name not in named]
    return _assign_brep_names(found, pool)


def _radii_close(a: tuple[float, ...], b: tuple[float, ...]) -> bool:
    tol = 10 ** -RADIUS_DECIMALS + 1e-9  # deux arrondis à 0,01 de part et d'autre d'une demi-unité
    return len(a) == len(b) and all(abs(x - y) <= tol for x, y in zip(a, b))


def _signature_filter(sig: Signature, cands: list[int], pool: list[_BrepEntry]) -> list[int]:
    """Candidats compatibles avec la signature : complète d'abord, rayons seuls ensuite."""
    known = [j for j in cands if pool[j].signature is not None]
    full = [
        j
        for j in known
        if pool[j].signature.types == sig.types and _radii_close(pool[j].signature.radii, sig.radii)
    ]
    if full:
        return full
    return [j for j in known if _radii_close(pool[j].signature.radii, sig.radii)]


def _assign_brep_names(
    found: list[tuple[tuple[str, ...], Solid, bool]],
    pool: list[_BrepEntry] | list[tuple[str, int]],
) -> list[NamedBody]:
    """Attribue aux solides anonymes les noms du texte STEP.

    Critères : nombre de faces, puis signature géométrique (types de surfaces et rayons des
    cylindres, lus dans le texte STEP et comparés aux faces OCC). Un premier passage attribue
    les correspondances uniques ; ensuite seulement, s'il reste plusieurs noms libres
    indiscernables, l'appariement est une devinette signalée dans `NamedBody.note`.
    """
    entries = [p if isinstance(p, _BrepEntry) else _BrepEntry(p[0], p[1]) for p in pool]
    used = [False] * len(entries)
    chosen: dict[int, tuple[int, str]] = {}
    sigs: dict[int, Signature] = {}

    def candidates(i: int, solid: Solid) -> list[int]:
        n_faces = len(solid.faces())
        cands = [j for j, e in enumerate(entries) if not used[j] and e.n_faces == n_faces]
        if len(cands) > 1 and any(entries[j].signature is not None for j in cands):
            if i not in sigs:
                sigs[i] = solid_signature(solid)
            cands = _signature_filter(sigs[i], cands, entries) or cands
        return cands

    # 1er passage : correspondances uniques (le nombre de faces ou la signature suffit)
    for i, (_, solid, anonymous) in enumerate(found):
        if anonymous:
            cands = candidates(i, solid)
            if len(cands) == 1:
                used[cands[0]] = True
                chosen[i] = (cands[0], "")
    # 2e passage : le reste, dans l'ordre (devinette signalée)
    for i, (_, solid, anonymous) in enumerate(found):
        if anonymous and i not in chosen:
            cands = candidates(i, solid)
            if cands:
                used[cands[0]] = True
                note = ""
                if len(cands) > 1:
                    note = "nom deviné parmi : " + ", ".join(entries[j].name for j in cands)
                chosen[i] = (cands[0], note)

    bodies: list[NamedBody] = []
    counter = 0
    for i, (here, solid, anonymous) in enumerate(found):
        if not anonymous:
            bodies.append(NamedBody(here, solid))
        elif i in chosen:
            j, note = chosen[i]
            bodies.append(NamedBody(here + (entries[j].name,), solid, note))
        else:
            counter += 1
            base = here[-1] if here else "corps"
            bodies.append(NamedBody(here + (f"{base}#{counter}",), solid))
    return bodies


def write_step(path: str | Path, bodies: list[NamedBody], assembly_name: str = "BEAVER") -> Path:
    path = Path(path)
    children = []
    for body in bodies:
        solid = copy.deepcopy(body.solid)
        solid.label = body.name
        children.append(solid)
    export_step(Compound(children=children, label=assembly_name), str(path))
    _fix_label_encoding(path)
    return path


def _fix_label_encoding(path: Path) -> None:
    """OCP réécrit les octets UTF-8 des libellés comme des caractères latin-1 (« é » → « Ã© »).

    On rétablit le texte puis on l'échappe en \\X2\\…\\X0\\ (STEP reste ainsi du pur ASCII).
    """

    def fix(m: re.Match) -> str:
        run = m.group()
        try:
            run = run.encode("latin-1").decode("utf-8")
        except UnicodeError:
            pass
        return "\\X2\\" + run.encode("utf-16-be").hex().upper() + "\\X0\\"

    text = path.read_text(encoding="utf-8")
    if _NON_ASCII.search(text):
        path.write_text(_NON_ASCII.sub(fix, text), encoding="utf-8", newline="")

