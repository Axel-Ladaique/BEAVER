"""Rôles des corps d'après leurs noms (spec §6) et détection des longerons."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

import numpy as np
from build123d import GeomType, Solid
from OCP.BRepAdaptor import BRepAdaptor_Surface

from beaver.config import Config, Role
from beaver.io_step import NamedBody

_PREFIX_ORDER: tuple[Role, ...] = ("spar", "servo", "split", "norib", "cut", "skin")


class ClassifyError(ValueError):
    """Impossible d'attribuer les rôles (message en français)."""


@dataclass(frozen=True)
class SparInfo:
    diameter: float
    axis_point: tuple[float, float, float]
    axis_dir: tuple[float, float, float]
    length: float
    # gaine de câble : longeron dont le nom propre contient un `naming.cable_markers`
    # (trou + fente vers la peau, sans manchon ni attache — traité par les tâches suivantes)
    cable: bool = False


@dataclass(frozen=True)
class ClassifiedBody:
    body: NamedBody
    role: Role | None
    matched_by: str
    spar: SparInfo | None = None

    @property
    def is_cable(self) -> bool:
        return self.spar is not None and self.spar.cable


@dataclass(frozen=True)
class Model:
    bodies: tuple[ClassifiedBody, ...]

    def of(self, role: Role) -> list[ClassifiedBody]:
        return [b for b in self.bodies if b.role == role]

    @property
    def cables(self) -> list[ClassifiedBody]:
        """Gaines de câble (sous-ensemble de `of("spar")`)."""
        return [b for b in self.bodies if b.is_cable]

    @property
    def unassigned(self) -> list[ClassifiedBody]:
        return [b for b in self.bodies if b.role is None]


def _clean(vec: np.ndarray) -> tuple[float, float, float]:
    # round + 0.0 : évite les -0.0 dans les résultats
    return tuple(float(round(c, 9)) + 0.0 for c in vec)  # type: ignore[return-value]


def detect_spar(solid: Solid) -> SparInfo | None:
    best = None
    for face in solid.faces():
        if face.geom_type != GeomType.CYLINDER:
            continue
        cyl = BRepAdaptor_Surface(face.wrapped).Cylinder()
        if best is None or cyl.Radius() > best.Radius():
            best = cyl
    if best is None:
        return None
    ax = best.Axis()
    d = np.array([ax.Direction().X(), ax.Direction().Y(), ax.Direction().Z()], dtype=float)
    d /= np.linalg.norm(d)
    if d[np.argmax(np.abs(d))] < 0:
        d = -d
    origin = np.array([ax.Location().X(), ax.Location().Y(), ax.Location().Z()], dtype=float)
    pts = np.array([[v.X, v.Y, v.Z] for v in solid.vertices()], dtype=float)
    proj = (pts - origin) @ d
    start = origin + d * proj.min()
    return SparInfo(
        diameter=2 * best.Radius(),
        axis_point=_clean(start),
        axis_dir=_clean(d),
        length=float(proj.max() - proj.min()),
    )


_NAME_DIAMETER = re.compile(r"(\d+(?:[.,]\d+)?)\s*mm", re.I)
NAME_DIAMETER_TOL = 0.5  # mm : écart toléré entre le diamètre écrit dans le nom et celui mesuré


def diameter_note(cb: ClassifiedBody) -> str:
    """Longeron dont le nom annonce un diamètre (« SBAR_14mm_1 ») différent du diamètre mesuré (plus grand cylindre
    du solide) : le nom ou la CAO est à vérifier. Chaîne vide sinon."""
    if cb.spar is None:
        return ""
    m = _NAME_DIAMETER.search(cb.body.name)
    if m is None:
        return ""
    named = float(m.group(1).replace(",", "."))
    measured = cb.spar.diameter
    if abs(named - measured) <= NAME_DIAMETER_TOL:
        return ""
    why = ("nom deviné (voir ci-dessus) : il appartient peut-être à un autre corps"
           if cb.body.note else "le nom est celui du corps dans le fichier : la géométrie de la CAO diffère du nom")
    return (f"le nom indique Ø {named:g} mm, mais le plus grand cylindre de ce corps mesure Ø {measured:.1f} mm — "
            f"{why} ; c'est le diamètre mesuré qui est utilisé")


def _match(body: NamedBody, cfg: Config) -> tuple[Role | None, str]:
    """Rôle d'après le nom : le nom propre du corps d'abord, les niveaux parents ensuite.

    À chaque niveau (du corps vers la racine), `roles:` explicite puis préfixes. Un corps
    dont le nom porte un préfixe connu (ex. `SERVO_1`) n'est donc jamais reclassé par le
    nom d'un composant parent (ex. `Aile_…` → peau).
    """
    explicit = {k.lower(): (k, v) for k, v in cfg.roles.items()}
    for level in reversed(body.path):
        low = level.lower()
        if low in explicit:
            key, role = explicit[low]
            return role, f"roles:{key}"
        for role in _PREFIX_ORDER:
            for prefix in getattr(cfg.naming, role):
                if low.startswith(prefix.lower()):
                    return role, f"préfixe '{prefix}' sur '{level}'"
    return None, ""


def cable_marker(body: NamedBody, cfg: Config) -> str | None:
    """Marqueur de gaine trouvé dans le nom propre du corps (insensible à la casse)."""
    low = body.name.lower()
    return next((m for m in cfg.naming.cable_markers if m.lower() in low), None)


def classify(bodies: list[NamedBody], cfg: Config) -> Model:
    matched = [(b, *_match(b, cfg)) for b in bodies]
    if not any(role == "skin" for _, role, _ in matched):
        free = [i for i, (_, role, _) in enumerate(matched) if role is None]
        if len(free) == 1:
            b, _, _ = matched[free[0]]
            matched[free[0]] = (b, "skin", "seul corps restant")
        else:
            names = ", ".join(b.display for b in bodies)
            raise ClassifyError(
                f"aucune peau trouvée. Corps présents : {names}. "
                f"Nommez la peau avec un préfixe {cfg.naming.skin} ou utilisez 'roles:' dans la config."
            )
    result = []
    for body, role, how in matched:
        spar = None
        if role == "spar":
            spar = detect_spar(body.solid)
            if spar is None:
                raise ClassifyError(f"{body.display} : marqué longeron mais aucun cylindre détecté")
            if cable_marker(body, cfg) is not None:
                spar = replace(spar, cable=True)
        result.append(ClassifiedBody(body, role, how, spar))
    return Model(tuple(result))
