"""Tessellation et découpe en couches horizontales (Z = axe de construction)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import trimesh
from build123d import Shape
from shapely.geometry import Polygon


@dataclass(frozen=True)
class Layer:
    z: float
    polygons: tuple[Polygon, ...]


def to_mesh(shape: Shape, tol: float = 0.005) -> trimesh.Trimesh:
    vertices, triangles = shape.tessellate(tol, 0.1)
    verts = np.array([[v.X, v.Y, v.Z] for v in vertices], dtype=float)
    return trimesh.Trimesh(verts, np.array(triangles, dtype=np.int64), process=True)


def layer_heights(zmin: float, zmax: float, layer_height: float) -> np.ndarray:
    return np.arange(zmin + layer_height / 2, zmax, layer_height)


def slice_layers(shape: Shape, heights: Sequence[float], tol: float = 0.005) -> list[Layer]:
    mesh = to_mesh(shape, tol)
    zs = [float(z) for z in heights]
    sections = mesh.section_multiplane(plane_origin=[0, 0, 0], plane_normal=[0, 0, 1], heights=zs)
    layers = []
    for z, section in zip(zs, sections):
        polys = tuple(section.polygons_full) if section is not None else ()
        layers.append(Layer(z, polys))
    return layers
