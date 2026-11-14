"""Rigid 3-D club for the rear camera, parameterised by a club SPEC so that the generic model and the research TS4
model run through identical code. Supersedes club3d.py's module-level constants (club3d.py is kept: earlier results
were produced with it).

A spec is the blade outline about the face centre (mm: u toward the toe, v up the face), the leading-edge level, the
static loft and lie that fix the shaft's direction relative to the face, where the shaft axis meets the ground, the
hosel and shaft radii, the sole strip. Pose = (face, loft, roll) as the application defines them + the 3-D position
of the face centre in the camera frame."""
import json
import math
from dataclasses import dataclass, replace

import cv2
import numpy as np
from scipy.spatial import cKDTree

import geom
from common import AUDIT

SS = 4


@dataclass(frozen=True)
class Spec:
    name: str
    blade: np.ndarray            # (N, 2) polygon, heel-bottom -> sole -> toe -> top line -> heel-top
    toe_u: float                 # toe extreme, mm toe-ward of the face centre
    le_v: float                  # leading edge, mm below the face centre (negative)
    static_loft: float
    static_lie: float
    axis_ground_from_toe: float
    axis_behind_le: float
    hosel_radius: float
    shaft_radius: float
    sole_width: float
    bounce: float

    def scaled(self, k):
        return replace(self, name="%s x %.3f" % (self.name, k), blade=self.blade * k, toe_u=self.toe_u * k, le_v=self.le_v * k,
                       axis_ground_from_toe=self.axis_ground_from_toe * k, axis_behind_le=self.axis_behind_le * k, hosel_radius=self.hosel_radius * k,
                       sole_width=self.sole_width * k)

    def shaft_frame(self):
        """(shaft direction, axis ground point) in (toe, up, normal) coordinates about the face centre."""
        lo, li = math.radians(self.static_loft), math.radians(self.static_lie)
        direction = np.array([-math.cos(li), math.sin(li) * math.cos(lo), math.sin(li) * math.sin(lo)])
        ground = np.array([self.toe_u - self.axis_ground_from_toe, self.le_v + self.axis_behind_le * math.sin(lo), -self.axis_behind_le * math.cos(lo)])
        return direction, ground


def generic(sole_width=19.9, bounce=3.0):
    uv = geom.OUTLINE
    toe_u, le_v = float(uv[:, 0].max()), float(uv[:, 1].min())
    x = toe_u - uv[:, 0]
    peak = int(np.argmax(uv[:, 1]))
    cut = peak + int(np.argmax(x[peak:] > 79.0))
    return Spec("generic iron", uv[:cut].copy(), toe_u, le_v, 31.4, geom.STATIC_LIE, 78.2, 3.5, 6.5, 4.7, sole_width, bounce)


def ts4():
    m = json.loads((AUDIT / "results" / "ts4-model.json").read_text())
    uv = np.asarray(m["outline_uv"], dtype=float)
    # the photo's outline stops short of the heel on the sole; close it under the hosel (the cylinder covers the neck)
    heel = np.array([[m["toe_u"] - (m["axis_ground_from_toe"] + 2.0), m["le_v"] + 3.0]])
    return Spec("TS4 7-iron (research)", np.vstack([heel, uv]), m["toe_u"], m["le_v"], m["static_loft"], m["static_lie"], m["axis_ground_from_toe"], m["axis_behind_le"],
                m["hosel_radius"], m["shaft_radius"], m["sole_width"], m["bounce"])


class Club:
    def __init__(self, scene, spec, shape):
        self.scene, self.spec, self.shape = scene, spec, shape

    def parts(self, angles, origin):
        """(silhouette polygons in px, shaft axis 2 px points, blade outline px)."""
        sp = self.spec
        toe, up, normal = geom.axes(*angles)
        lead = origin + sp.blade[:, :1] * toe + sp.blade[:, 1:2] * up
        back = -np.array([normal[0], 0.0, normal[2]])
        back /= max(math.hypot(back[0], back[2]), 1e-12)
        b = math.radians(sp.bounce)
        offset = sp.sole_width * (math.cos(b) * back + math.sin(b) * np.array([0.0, 1.0, 0.0]))
        lead_px, trail_px = self.scene.project(lead), self.scene.project(lead + offset)
        polys = [lead_px]
        n = len(lead_px)
        for i in range(n):
            j = (i + 1) % n
            polys.append(np.array([lead_px[i], lead_px[j], trail_px[j], trail_px[i]]))
        d_tun, g_tun = sp.shaft_frame()
        direction = d_tun[0] * toe + d_tun[1] * up + d_tun[2] * normal
        ground = origin + g_tun[0] * toe + g_tun[1] * up + g_tun[2] * normal
        axis_px = self.scene.project(np.stack([ground, ground + 150.0 * direction]))
        for s0, s1, radius in ((8.0, 72.0, sp.hosel_radius), (72.0, 150.0, sp.shaft_radius)):
            p0, p1 = ground + s0 * direction, ground + s1 * direction
            a, c = self.scene.project(np.stack([p0, p1]))
            t = (c - a) / np.linalg.norm(c - a)
            nrm = np.array([-t[1], t[0]])
            ra, rc = radius * self.scene.focal / p0[2], radius * self.scene.focal / p1[2]
            polys.append(np.array([a + ra * nrm, c + rc * nrm, c - rc * nrm, a - ra * nrm]))
        return polys, axis_px, lead_px

    def mask(self, angles, origin):
        polys, axis_px, lead_px = self.parts(angles, origin)
        canvas = np.zeros((self.shape[0] * SS, self.shape[1] * SS), dtype=np.uint8)
        for p in polys:
            cv2.fillPoly(canvas, [np.round((p + 0.5) * SS * 16.0).astype(np.int32)], 1, shift=4)
        return canvas.astype(bool), axis_px, lead_px

    def shaft_point(self, angles, origin, along):
        toe, up, normal = geom.axes(*angles)
        d_tun, g_tun = self.spec.shaft_frame()
        direction = d_tun[0] * toe + d_tun[1] * up + d_tun[2] * normal
        return origin + g_tun[0] * toe + g_tun[1] * up + g_tun[2] * normal + along * direction


def boundary(mask_ss):
    cs, _h = cv2.findContours(mask_ss.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not cs:
        return np.zeros((0, 2))
    c = max(cs, key=cv2.contourArea)[:, 0, :].astype(float)
    return (c + 0.5) / SS - 0.5


def polygon_mask(poly, shape):
    canvas = np.zeros((shape[0] * SS, shape[1] * SS), dtype=np.uint8)
    cv2.fillPoly(canvas, [np.round((np.asarray(poly) + 0.5) * SS * 16.0).astype(np.int32)], 1, shift=4)
    return canvas.astype(bool)


def compare(model_ss, target_ss, row_lo):
    """Symmetric boundary distance (px) and overlap between two supersampled masks below row_lo."""
    m, t = model_ss.copy(), target_ss.copy()
    lo = int(round((row_lo + 0.5) * SS))
    m[:lo], t[:lo] = False, False
    bm, bt = boundary(m), boundary(t)
    if len(bm) < 10 or len(bt) < 10:
        return {"rms": 99.0, "mean": 99.0, "p95": 99.0, "iou": 0.0}
    bm, bt = bm[bm[:, 1] > row_lo + 0.6], bt[bt[:, 1] > row_lo + 0.6]
    d = np.concatenate([cKDTree(bt).query(bm)[0], cKDTree(bm).query(bt)[0]])
    inter, union = np.count_nonzero(m & t), np.count_nonzero(m | t)
    return {"rms": float(np.sqrt(np.mean(d ** 2))), "mean": float(np.mean(d)), "p95": float(np.percentile(d, 95)), "iou": inter / max(union, 1)}


def top_at(poly, x):
    """Highest y of a closed polygon at column x."""
    a, b = poly, np.roll(poly, -1, axis=0)
    hit = ((a[:, 0] <= x) & (b[:, 0] > x)) | ((b[:, 0] <= x) & (a[:, 0] > x))
    if not hit.any():
        return math.nan
    t = (x - a[hit, 0]) / (b[hit, 0] - a[hit, 0])
    return float((a[hit, 1] + t * (b[hit, 1] - a[hit, 1])).min())
