"""Structured generic driver: a driver-proportioned mesh + labeled body-frame keypoints."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os

import numpy as np
from scipy.spatial import ConvexHull

from ..template import default_template
from .headmesh import _ArrayWithPtp, _fib_sphere, HeadMesh


@dataclass(frozen=True)
class Keypoint:
    name: str
    xyz: np.ndarray      # body coords (mm)
    normal: np.ndarray   # unit outward normal (body)


@dataclass(frozen=True)
class StructuredHead:
    mesh: HeadMesh
    keypoints: dict
    template: object


# body frame: +X face/front, +Y toe, -Y heel, +Z up. (name, xyz, normal)
_KP = {
    "crown_apex": ((-10, 0, 30), (0, 0, 1)),
    "crown_back": ((-50, 0, 18), (-0.6, 0, 0.8)),
    "crown_toe": ((-15, 40, 24), (-0.2, 0.5, 0.84)),
    "crown_heel": ((-15, -38, 24), (-0.2, -0.5, 0.84)),
    "hosel_top": ((-8, -52, 52), (-0.5, -0.5, 0.7)),
    "hosel_base": ((-6, -48, 28), (-0.4, -0.7, 0.6)),
    "back_skirt": ((-50, 0, -10), (-0.85, 0, -0.5)),
    "sole_center": ((-10, 0, -28), (0, 0, -1)),
    "face_center": ((50, 0, 0), (0.983, 0, 0.182)),
    "leading_edge_toe": ((44, 40, -18), (0.7, 0, -0.7)),
    "leading_edge_heel": ((44, -38, -18), (0.7, 0, -0.7)),
    "topline_toe": ((40, 35, 20), (0.6, 0, 0.6)),
}


def driver_keypoints() -> dict:
    out = {}
    for name, (p, n) in _KP.items():
        nv = np.asarray(n, dtype=float)
        out[name] = Keypoint(name, np.asarray(p, dtype=float), nv / np.linalg.norm(nv))
    return out


def structured_driver() -> StructuredHead:
    template = default_template("driver")
    kps = driver_keypoints()
    # driver-proportioned ellipsoid (face reaches ~+50 in X), with the keypoints forced onto the
    # hull so they are genuine surface points; the mesh is for the silhouette baseline only.
    body = _fib_sphere(140) * np.array([55.0, 58.0, 30.0]) + np.array([-5.0, 0.0, 0.0])
    anchors = np.array([k.xyz for k in kps.values()], dtype=float)
    pts = np.vstack([body, anchors])
    hull = ConvexHull(pts)
    verts = np.asarray(pts, dtype=float).view(_ArrayWithPtp)
    mesh = HeadMesh(verts, hull.simplices.astype(np.int64), "driver_structured")
    return StructuredHead(mesh=mesh, keypoints=kps, template=template)


def structured_driver_from_obj(obj_path, kp_path) -> StructuredHead:
    from .headmesh import load_obj

    mesh = load_obj(obj_path)
    with open(kp_path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    kps = {}
    for name, rec in raw.items():
        nv = np.asarray(rec["normal"], dtype=float)
        kps[name] = Keypoint(name, np.asarray(rec["xyz"], dtype=float), nv / np.linalg.norm(nv))
    return StructuredHead(mesh=mesh, keypoints=kps, template=default_template("driver"))
