"""Tessellation et découpe en couches horizontales (Z = axe de construction)."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import trimesh
from build123d import Shape
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.StlAPI import StlAPI_Writer
from shapely.geometry import Polygon


@dataclass(frozen=True)
class Layer:
    z: float
    polygons: tuple[Polygon, ...]


def to_mesh(shape: Shape, tol: float = 0.02, angular: float = 0.3) -> trimesh.Trimesh:
    """Maillage OpenCascade natif (parallèle), relu via un STL binaire temporaire."""
    BRepMesh_IncrementalMesh(shape.wrapped, tol, False, angular, True)
    fd, tmp = tempfile.mkstemp(suffix=".stl")
    os.close(fd)
    try:
        writer = StlAPI_Writer()
        if hasattr(writer, "ASCIIMode"):
            writer.ASCIIMode = False
        writer.Write(shape.wrapped, tmp)
        return trimesh.load(tmp, file_type="stl", force="mesh")
    finally:
        os.remove(tmp)


def layer_heights(zmin: float, zmax: float, layer_height: float) -> np.ndarray:
    return np.arange(zmin + layer_height / 2, zmax, layer_height)


def slice_layers(shape: Shape, heights: Sequence[float], tol: float = 0.02) -> list[Layer]:
    mesh = to_mesh(shape, tol)
    zs = [float(z) for z in heights]
    sections = mesh.section_multiplane(plane_origin=[0, 0, 0], plane_normal=[0, 0, 1], heights=zs)
    layers = []
    for z, section in zip(zs, sections):
        polys = tuple(section.polygons_full) if section is not None else ()
        layers.append(Layer(z, polys))
    return layers
