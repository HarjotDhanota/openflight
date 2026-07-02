"""Marked clubhead rigs for the Stage 0C behind-ball accuracy budget."""
from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np

from ..template import ClubTemplate, default_template


@dataclass(frozen=True)
class Marker:
    name: str
    xyz: np.ndarray
    normal: np.ndarray


@dataclass(frozen=True)
class MarkerRig:
    name: str
    markers: dict[str, Marker]
    template: ClubTemplate

    def visible_markers(self, pose, camera) -> list[Marker]:
        visible = []
        w, h = camera.intrinsics.width, camera.intrinsics.height
        for marker in self.markers.values():
            p_world = pose.body_to_world(marker.xyz)
            n_world = pose.direction_to_world(marker.normal)
            if float(n_world @ (camera.center_world - p_world)) <= 0.0:
                continue
            (uv,), in_front = camera.project(p_world[None, :])
            if in_front[0] and 0 <= uv[0] < w and 0 <= uv[1] < h:
                visible.append(marker)
        return visible


def _unit(v) -> np.ndarray:
    arr = np.asarray(v, dtype=float)
    return arr / np.linalg.norm(arr)


def _marker(name: str, xyz, normal) -> Marker:
    return Marker(name, np.asarray(xyz, dtype=float), _unit(normal))


def _rig(name: str, rows, template: ClubTemplate) -> MarkerRig:
    return MarkerRig(name, {n: _marker(n, xyz, normal) for n, xyz, normal in rows}, template)


def driver_markers() -> MarkerRig:
    """Driver markers on crown/back/hosel/shaft surfaces visible from behind."""
    template = default_template("driver")
    rows = [
        ("crown_toe", (-22.0, 43.0, 16.0), (-0.25, 0.57, 0.79)),
        ("crown_heel", (-22.0, -40.0, 17.0), (-0.25, -0.52, 0.82)),
        ("crown_front", (8.0, 5.0, 29.0), (0.13, 0.03, 0.99)),
        ("crown_back", (-55.0, 0.0, 10.0), (-0.83, 0.0, 0.56)),
        ("hosel", (-2.0, -58.0, 48.0), (-0.50, -0.18, 0.85)),
        ("shaft_low", (-6.0, -58.0, 88.0), (-0.55, -0.05, 0.83)),
        ("shaft_high", (-8.0, -62.0, 138.0), (-0.55, -0.05, 0.83)),
    ]
    return _rig("driver", rows, template)


def iron_markers() -> MarkerRig:
    """Iron markers on the cavity/topline/hosel/shaft, avoiding the wear face."""
    template = default_template("iron").with_loft_override(34.0)
    rows = [
        ("cavity_toe", (-10.0, 32.0, 8.0), (-0.82, 0.35, 0.45)),
        ("cavity_heel", (-10.0, -30.0, 8.0), (-0.84, -0.20, 0.50)),
        ("cavity_low", (-12.0, 0.0, -14.0), (-0.94, 0.0, -0.34)),
        ("topline_mid", (0.0, 0.0, 27.0), (-0.05, 0.0, 1.0)),
        ("hosel", (0.0, -42.0, 46.0), (-0.48, -0.12, 0.87)),
        ("shaft_low", (-4.0, -45.0, 86.0), (-0.55, -0.05, 0.83)),
    ]
    return _rig("iron", rows, template)


def calibrated_copy(rig: MarkerRig, sigma_cal: float, rng) -> MarkerRig:
    """Return the fitter's calibrated rig, separate from the true rig."""
    sigma = float(sigma_cal)
    markers = {}
    for name, marker in rig.markers.items():
        xyz = marker.xyz + (rng.normal(0.0, sigma, 3) if sigma > 0 else 0.0)
        markers[name] = Marker(name, np.asarray(xyz, dtype=float), marker.normal.copy())

    if sigma > 0:
        face_center = rig.template.face_center_offset + rng.normal(0.0, sigma, 3)
        loft = rig.template.static_loft_deg + float(rng.normal(0.0, 0.5 * sigma))
    else:
        face_center = rig.template.face_center_offset.copy()
        loft = rig.template.static_loft_deg
    template = replace(rig.template, static_loft_deg=loft, face_center_offset=face_center)
    return MarkerRig(rig.name, markers, template)
