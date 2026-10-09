"""Rôles des corps d'après leurs noms (spec §6) et détection des longerons."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from build123d import GeomType, Solid
from OCP.BRepAdaptor import BRepAdaptor_Surface

from aerorib.config import Config, Role
from aerorib.io_step import NamedBody

_PREFIX_ORDER: tuple[Role, ...] = ("spar", "split", "norib", "cut", "skin")


class ClassifyError(ValueError):
    """Impossible d'attribuer les rôles (message en français)."""


@dataclass(frozen=True)
class SparInfo:
    diameter: float
    axis_point: tuple[float, float, float]
    axis_dir: tuple[float, float, float]
    length: float


@dataclass(frozen=True)
class ClassifiedBody:
    body: NamedBody
    role: Role | None
    matched_by: str
    spar: SparInfo | None = None


@dataclass(frozen=True)
class Model:
    bodies: tuple[ClassifiedBody, ...]

    def of(self, role: Role) -> list[ClassifiedBody]:
        return [b for b in self.bodies if b.role == role]

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


def _match(body: NamedBody, cfg: Config) -> tuple[Role | None, str]:
    explicit = {k.lower(): (k, v) for k, v in cfg.roles.items()}
    for level in reversed(body.path):
        if level.lower() in explicit:
            key, role = explicit[level.lower()]
            return role, f"roles:{key}"
    for level in reversed(body.path):
        low = level.lower()
        for role in _PREFIX_ORDER:
            for prefix in getattr(cfg.naming, role):
                if low.startswith(prefix.lower()):
                    return role, f"préfixe '{prefix}' sur '{level}'"
    return None, ""


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
        result.append(ClassifiedBody(body, role, how, spar))
    return Model(tuple(result))
