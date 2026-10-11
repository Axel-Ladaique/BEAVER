"""Vérifications d'imprimabilité en vase mode, couche par couche (spec §8)."""

from __future__ import annotations

import os
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
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
# en dessous, le coût de démarrage des processus (spawn sous macOS/Windows) dépasse le gain
PARALLEL_MIN_LAYERS = 200
# Réglage de test/ajustement : AERORIB_PARALLEL_MIN_LAYERS remplace le seuil ci-dessus. Lu à chaque
# appel (donc hérité par les processus lancés en « spawn »).
PARALLEL_ENV = "AERORIB_PARALLEL_MIN_LAYERS"


def parallel_min_layers() -> int:
    try:
        return int(os.environ[PARALLEL_ENV])
    except (KeyError, ValueError):
        return PARALLEL_MIN_LAYERS


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
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors


def _parts(geom):
    return list(getattr(geom, "geoms", [geom])) if not geom.is_empty else []


def slicer_closing(polygons: Sequence[Polygon], min_width: float) -> tuple[Polygon, ...]:
    """Ce que le slicer imprime : les fentes plus étroites que min_width sont refermées."""
    r = min_width / 2
    out: list[Polygon] = []
    for poly in polygons:
        closed = poly.buffer(r, join_style="mitre").buffer(-r, join_style="mitre")
        out.extend(p for p in _parts(closed) if isinstance(p, Polygon))
    return tuple(out)


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
                    Issue(
                        "narrow_slit",
                        "warning",
                        layer.z,
                        f"fente plus étroite que {min_width} mm (refermée par le slicer)",
                        part.bounds,
                    )
                )
    return issues


def check_material_width(layer: Layer, line_width: float) -> list[Issue]:
    """Un contour qui se sépare quand on l'érode d'une demi-largeur de ligne a un col trop fin.

    À appliquer sur la couche refermée (slicer_closing). Chaque polygone est traité seul : des îlots
    déjà distincts relèvent de check_connectivity.
    """
    issues = []
    for poly in layer.polygons:
        eroded = poly.buffer(-line_width / 2, join_style="mitre")
        parts = [p for p in _parts(eroded) if p.area > line_width**2]
        if len(parts) > 1:
            smallest = min(parts, key=lambda p: p.area)
            issues.append(
                Issue(
                    "material_width",
                    "error",
                    layer.z,
                    "matière plus fine qu'une largeur de ligne : le contour se séparerait au slicer",
                    smallest.bounds,
                )
            )
    return issues


def check_skin(layer: Layer, original: Layer, max_mouth: float, tol: float) -> list[Issue]:
    if not original.polygons:
        return []
    orig_boundary = unary_union([p.exterior for p in original.polygons])
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


def _check_layer(args) -> list[Issue]:
    """Contrôles d'une couche ; fonction de module (picklable) pour le pool de processus."""
    z, polygons, original_polygons, min_slit, line_width, max_mouth, skin_tol = args
    layer = Layer(z, polygons)
    closed = Layer(z, slicer_closing(polygons, min_slit))
    found = check_connectivity(closed)
    found += check_material_width(closed, line_width)
    found += check_narrow_slits(layer, min_slit)
    if original_polygons is not None:
        found += check_skin(layer, Layer(z, original_polygons), max_mouth, skin_tol)
    return found


def _run_checks(jobs: list) -> list[list[Issue]]:
    if len(jobs) >= parallel_min_layers():
        try:
            with ProcessPoolExecutor() as pool:
                return list(pool.map(_check_layer, jobs, chunksize=16))
        except (BrokenProcessPool, OSError, AssertionError):
            # processus indisponibles ou morts, ou parent démon (AssertionError) : repli en série, même résultat
            pass
    return [_check_layer(job) for job in jobs]


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
    v = cfg.validation
    jobs = [
        (
            layer.z,
            layer.polygons,
            originals[i].polygons if originals is not None else None,
            preset.min_slit_width,
            preset.line_width,
            v.max_mouth_width,
            v.skin_tolerance,
        )
        for i, layer in enumerate(layers)
    ]
    per_layer = _run_checks(jobs)
    for layer, found in zip(layers, per_layer):
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
