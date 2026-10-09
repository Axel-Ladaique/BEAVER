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


def _brep_names(path: Path) -> list[tuple[str, int]]:
    """Noms des entités MANIFOLD_SOLID_BREP / BREP_WITH_VOIDS et nombre de faces de leur coque."""
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    if "DATA;" not in text:
        return []
    entities = {int(m.group(1)): (m.group(2), m.group(3)) for m in _ENTITY.finditer(text.split("DATA;", 1)[1])}
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
            found.append((name, len(_REF.findall(entities[refs[0]][1]))))
    return found


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
    pool = [(n, f) for n, f in _brep_names(path) if n not in named]
    return _assign_brep_names(found, pool)


def _assign_brep_names(
    found: list[tuple[tuple[str, ...], Solid, bool]], pool: list[tuple[str, int]]
) -> list[NamedBody]:
    """Attribue aux solides anonymes les noms du texte STEP, par nombre de faces.

    Si plusieurs noms libres avaient le même nombre de faces, l'appariement est une
    devinette : elle est signalée dans `NamedBody.note`.
    """
    used = [False] * len(pool)
    bodies: list[NamedBody] = []
    counter = 0
    for here, solid, anonymous in found:
        if not anonymous:
            bodies.append(NamedBody(here, solid))
            continue
        n_faces = len(solid.faces())
        candidates = [j for j, (_, f) in enumerate(pool) if not used[j] and f == n_faces]
        if candidates:
            used[candidates[0]] = True
            note = ""
            if len(candidates) > 1:
                note = "nom deviné parmi : " + ", ".join(pool[j][0] for j in candidates)
            bodies.append(NamedBody(here + (pool[candidates[0]][0],), solid, note))
        else:
            counter += 1
            base = here[-1] if here else "corps"
            bodies.append(NamedBody(here + (f"{base}#{counter}",), solid))
    return bodies


def write_step(path: str | Path, bodies: list[NamedBody], assembly_name: str = "AERORIB") -> Path:
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

