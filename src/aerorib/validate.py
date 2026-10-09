"""Vérifications d'imprimabilité en vase mode, couche par couche (spec §8)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from build123d import Shape
from shapely.geometry import Polygon
from shapely.ops import unary_union

from aerorib.config import Config
from aerorib.slicing import Layer, layer_heights, slice_layers

Severity = Literal["error", "warning"]
MAX_FAILED_LAYERS = 20
# Une fente détectée doit avoir une aire > min_width * NARROW_AREA_FACTOR (sinon : bruit de buffer).
NARROW_AREA_FACTOR = 0.5


@dataclass(frozen=True)
class Issue:
    check: str
    severity: Severity
    z: float
    message: str
    bounds: tuple[float, float, float, float] | None = None


@dataclass(frozen=True)
class Report:
    issues: tuple[Issue, ...]
    stats: dict[str, float]
    failed_layers: dict[float, tuple[Polygon, ...]] = field(default_factory=dict)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def ok(self) -> bool:
        return not self.errors


def _parts(geom):
    return list(getattr(geom, "geoms", [geom])) if not geom.is_empty else []


def check_connectivity(layer: Layer) -> list[Issue]:
    if not layer.polygons:
        return [Issue("connectivity", "error", layer.z, "couche vide")]
    issues = []
    if len(layer.polygons) > 1:
        smallest = sorted(layer.polygons, key=lambda p: p.area)[:-1]
        bounds = unary_union(smallest).bounds
        issues.append(
            Issue("connectivity", "error", layer.z, f"{len(layer.polygons)} îlots au lieu d'un seul contour", bounds)
        )
    for poly in layer.polygons:
        for ring in poly.interiors:
            issues.append(
                Issue("connectivity", "error", layer.z, "trou isolé (non relié à la peau)", Polygon(ring).bounds)
            )
    return issues


def check_narrow_slits(layer: Layer, min_width: float) -> list[Issue]:
    r = min_width / 2
    issues = []
    for poly in layer.polygons:
        closed = poly.buffer(r, join_style="mitre").buffer(-r, join_style="mitre")
        # pré-filtre : aire totale ajoutée par la fermeture trop faible pour contenir une fente
        if closed.area - poly.area <= min_width * NARROW_AREA_FACTOR:
            continue
        for part in _parts(closed.difference(poly)):
            if part.area > min_width * NARROW_AREA_FACTOR:
                issues.append(
                    Issue("narrow_slit", "error", layer.z, f"fente plus étroite que {min_width} mm", part.bounds)
                )
    return issues


def check_skin(layer: Layer, original: Layer, max_mouth: float, tol: float) -> list[Issue]:
    if not original.polygons:
        return []
    orig_boundary = unary_union([p.boundary for p in original.polygons])
    res_boundary = unary_union([p.boundary for p in layer.polygons]) if layer.polygons else None
    missing = orig_boundary if res_boundary is None else orig_boundary.difference(res_boundary.buffer(tol))
    issues = []
    for piece in _parts(missing):
        # la tolérance érode `tol` à chaque extrémité de la portion manquante : on compare
        # la longueur réellement manquante, corrigée de 2·tol
        missing_length = piece.length + 2 * tol
        if missing_length > max_mouth:
            issues.append(
                Issue("skin", "error", layer.z, f"peau entamée sur {missing_length:.1f} mm", piece.bounds)
            )
    return issues


def validate(
    shape: Shape,
    cfg: Config,
    original: Shape | None = None,
    heights: Sequence[float] | None = None,
) -> Report:
    preset = cfg.preset
    tol = cfg.validation.tessellation_tol
    bb = shape.bounding_box()
    zs = list(heights) if heights is not None else list(layer_heights(bb.min.Z, bb.max.Z, preset.layer_height))
    layers = slice_layers(shape, zs, tol)
    originals = slice_layers(original, zs, tol) if original is not None else None

    issues: list[Issue] = []
    failed: dict[float, tuple[Polygon, ...]] = {}
    path_mm = 0.0
    for i, layer in enumerate(layers):
        found = check_connectivity(layer) + check_narrow_slits(layer, preset.min_slit_width)
        if originals is not None:
            found += check_skin(
                layer, originals[i], cfg.validation.max_mouth_width, cfg.validation.skin_tolerance
            )
        if any(x.severity == "error" for x in found) and len(failed) < MAX_FAILED_LAYERS:
            failed[layer.z] = layer.polygons
        issues.extend(found)
        path_mm += sum(p.length for p in layer.polygons)

    mass_g = path_mm * preset.line_width * preset.layer_height * preset.density / 1000.0
    stats = {
        "layers": float(len(layers)),
        "height_mm": float(bb.max.Z - bb.min.Z),
        "path_length_m": path_mm / 1000.0,
        "mass_g": mass_g,
    }
    return Report(tuple(issues), stats, failed)
