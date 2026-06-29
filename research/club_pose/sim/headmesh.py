"""Procedural generic clubhead meshes (body-frame), for silhouette experiments."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.spatial import ConvexHull

from ..types import ClubheadPose


class _ArrayWithPtp(np.ndarray):
    def ptp(self, axis=None, out=None, keepdims=False):
        return np.ptp(np.asarray(self), axis=axis, out=out, keepdims=keepdims)


@dataclass(frozen=True)
class HeadMesh:
    vertices: np.ndarray  # (N, 3) body coords
    faces: np.ndarray  # (M, 3) int indices
    category: str

    def transformed(self, pose: ClubheadPose) -> np.ndarray:
        return pose.body_to_world(self.vertices)


def _fib_sphere(n: int) -> np.ndarray:
    i = np.arange(n) + 0.5
    phi = np.arccos(1.0 - 2.0 * i / n)
    theta = np.pi * (1.0 + 5.0**0.5) * i
    return np.column_stack(
        [np.cos(theta) * np.sin(phi), np.sin(theta) * np.sin(phi), np.cos(phi)]
    )


def _hull_mesh(points: np.ndarray, category: str) -> HeadMesh:
    hull = ConvexHull(points)
    vertices = np.asarray(points, dtype=float).view(_ArrayWithPtp)
    return HeadMesh(vertices=vertices, faces=hull.simplices.astype(np.int64), category=category)


def procedural(category: str) -> HeadMesh:
    """Body frame: +X approx face/front, +Y=toe, -Y=heel, +Z=up. Hosel at heel (-Y), up (+Z)."""
    sphere = _fib_sphere(120)
    if category == "driver":
        body = sphere * np.array([45.0, 58.0, 28.0]) + np.array([-10.0, 0.0, 0.0])
        hosel = np.array([[0, -58, 28], [5, -60, 55], [-5, -60, 55], [0, -62, 80]], dtype=float)
    elif category == "iron":
        body = sphere * np.array([10.0, 40.0, 27.0]) + np.array([-3.0, 0.0, 0.0])
        hosel = np.array([[0, -40, 27], [4, -42, 50], [-4, -42, 50], [0, -44, 72]], dtype=float)
    else:
        raise ValueError(f"unknown category {category!r}")
    return _hull_mesh(np.vstack([body, hosel]), category)


def distinctive_test_mesh() -> HeadMesh:
    """A deliberately asymmetric mesh (3 different spikes) - machinery validation only."""
    corners = np.array(
        [[x, y, z] for x in (-20, 20) for y in (-30, 30) for z in (-15, 15)], dtype=float
    )
    spikes = np.array([[60, 0, 0], [0, 45, 0], [0, 0, 40]], dtype=float)
    return _hull_mesh(np.vstack([corners, spikes]), "test")


def load_obj(path: str) -> HeadMesh:
    verts, faces = [], []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            parts = line.split()
            if not parts:
                continue
            if parts[0] == "v":
                verts.append([float(c) for c in parts[1:4]])
            elif parts[0] == "f":
                idx = [int(p.split("/")[0]) - 1 for p in parts[1:4]]
                faces.append(idx)
    vertices = np.asarray(verts, dtype=float).view(_ArrayWithPtp)
    return HeadMesh(vertices, np.asarray(faces, dtype=np.int64), "obj")
