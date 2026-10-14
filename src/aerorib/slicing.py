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
    """Maillage OpenCascade natif (parallèle), relu via un STL binaire temporaire.

    La triangulation est stockée sur la forme par OpenCascade et réutilisée par les appels suivants.
    """
    BRepMesh_IncrementalMesh(shape.wrapped, tol, False, angular, True)
    fd, tmp = tempfile.mkstemp(suffix=".stl")
    os.close(fd)
    try:
        writer = StlAPI_Writer()
        if hasattr(writer, "ASCIIMode"):
            writer.ASCIIMode = False
        if not writer.Write(shape.wrapped, tmp):
            raise RuntimeError(f"maillage : écriture STL impossible ({tmp})")
        return trimesh.load(tmp, file_type="stl", force="mesh")
    finally:
        os.remove(tmp)


LAYER_TOP_GAP = 1e-3  # mm : une couche dont le milieu est à moins de cela du sommet n'est pas tranchée


def layer_heights(zmin: float, zmax: float, layer_height: float) -> np.ndarray:
    """Milieux des couches d'une pièce posée en zmin, comme le slicer la tranche depuis son propre bas : strictement
    à l'intérieur de [zmin, zmax]. Sans la marge LAYER_TOP_GAP, une hauteur ≡ ½ couche (mod. couche) — par ex. une
    coupe en Z écartée de 0,5 mm d'un plan critique, couche 0,2 mm — mettrait la dernière couche (arrondi de
    `arange`) sur la face du sommet : section vide, « couche vide » à tort."""
    return np.arange(zmin + layer_height / 2, zmax - LAYER_TOP_GAP, layer_height)


def slice_layers(shape: Shape, heights: Sequence[float], tol: float = 0.02) -> list[Layer]:
    mesh = to_mesh(shape, tol)
    zs = [float(z) for z in heights]
    sections = mesh.section_multiplane(plane_origin=[0, 0, 0], plane_normal=[0, 0, 1], heights=zs)
    layers = []
    for z, section in zip(zs, sections):
        polys = tuple(section.polygons_full) if section is not None else ()
        layers.append(Layer(z, polys))
    return layers


def section_sampler(shape: Shape, tol: float = 0.02):
    """Fonction zs → contours XY (un tableau N×2 de points par z, vide si pas de matière) des sections z = cte.

    La pièce est maillée une seule fois ; chaque appel ne fait que des sections du maillage (pas de booléen) :
    sert au planificateur de découpe, en génération comme dans l'aperçu."""
    return mesh_sections(to_mesh(shape, tol))


def mesh_sections(mesh: trimesh.Trimesh):
    """Comme `section_sampler`, sur un maillage déjà calculé."""

    def sections(zs: Sequence[float]) -> list[np.ndarray]:
        zs = [float(z) for z in zs]
        if not zs:
            return []
        cuts = mesh.section_multiplane(plane_origin=[0, 0, 0], plane_normal=[0, 0, 1], heights=zs)
        out = []
        for c in cuts:
            polys = list(c.polygons_full) if c is not None else []
            pts = [np.asarray(p.exterior.coords)[:, :2] for p in polys]
            out.append(np.concatenate(pts) if pts else np.empty((0, 2)))
        return out

    return sections
