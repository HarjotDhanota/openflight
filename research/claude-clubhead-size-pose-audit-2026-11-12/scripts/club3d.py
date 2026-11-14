"""A rigid 3-D club for the rear camera: the generic face outline WITHOUT its in-plane hosel stub, the sole strip,
and a hosel/shaft cylinder on the real 3-D axis (in front of the lofted face, as on an iron). One rigid body: three
rotation angles turn all of it together.

Generic numbers only (club_outline_reference.json, research/club-models README section 6): static loft 31.4, lie 62.3,
axis meets the ground 78.2 mm from the toe. Two assumptions of mine, marked: the axis stands AXIS_BEHIND_LE mm behind
the leading edge at the ground; hosel radius 6.5 mm (the drawing's 13.05 mm tube).

Pose = (face, loft, roll) as the application defines them (geom.axes) + a translation parallel to the image plane
(dx, dy mm) from a reference origin + optional size. Depth is NOT fitted."""
import math

import cv2
import numpy as np
from scipy.optimize import minimize
from scipy.spatial import cKDTree

import geom

SS = 4
STATIC_LOFT = 31.4
STATIC_LIE = geom.STATIC_LIE
TOE_U = float(geom.OUTLINE[:, 0].max())
SOLE_V = float(geom.OUTLINE[:, 1].min())
AXIS_GROUND_FROM_TOE = 78.2
AXIS_BEHIND_LE = 3.5      # assumption: hosel front 3 mm ahead of the leading edge, radius 6.5
HOSEL_RADIUS = 6.5
SHAFT_RADIUS = 4.7
HOSEL_LENGTH = 72.0       # the drawing's tube top, mm up the axis from the ground; compared only below a clip row


def head_outline():
    """The blade without the drawn neck and hosel stub.

    The drawing runs heel-bottom -> sole -> toe -> toe peak -> top line -> fillet -> tube stub -> heel edge. Keep it up
    to where the top line reaches the fillet (79 mm from the toe) and close straight down to the heel-bottom start;
    the neck is supplied by the 3-D hosel cylinder instead."""
    uv = geom.OUTLINE
    x = TOE_U - uv[:, 0]
    peak = int(np.argmax(uv[:, 1]))
    cut = peak + int(np.argmax(x[peak:] > 79.0))
    return uv[:cut]


HEAD = head_outline()


def _static_frame():
    lo, li = math.radians(STATIC_LOFT), math.radians(STATIC_LIE)
    # address frame: X toe-ward, Y up, Z to the target. Face-plane basis and the shaft in it.
    up0 = np.array([0.0, math.cos(lo), -math.sin(lo)])
    n0 = np.array([0.0, math.sin(lo), math.cos(lo)])
    toe0 = np.array([1.0, 0.0, 0.0])
    shaft0 = np.array([-math.cos(li), math.sin(li), 0.0])
    centre_h = -SOLE_V  # face centre's height along the face above the leading edge
    centre0 = centre_h * up0
    ground0 = np.array([-(AXIS_GROUND_FROM_TOE - TOE_U), 0.0, -AXIS_BEHIND_LE])
    rel = ground0 - centre0
    basis = np.stack([toe0, up0, n0])
    return basis @ shaft0, basis @ rel  # shaft direction and ground point in (toe, up, normal) coordinates


SHAFT_TUN, GROUND_TUN = _static_frame()


class Club:
    def __init__(self, scene, origin_ref, sole_width, bounce, shape):
        self.scene, self.origin_ref, self.sole, self.shape = scene, np.asarray(origin_ref, dtype=float), (sole_width, bounce), shape

    def parts(self, face, loft, roll, dx=0.0, dy=0.0, size=1.0, inplane_stub=False):
        """Pixel polygons of the silhouette's parts and the shaft axis (2 points)."""
        toe, up, normal = geom.axes(face, loft, roll)
        origin = self.origin_ref + np.array([dx, dy, 0.0])
        uv = (geom.OUTLINE if inplane_stub else HEAD) * size
        lead = origin + uv[:, :1] * toe + uv[:, 1:2] * up
        back = -np.array([normal[0], 0.0, normal[2]])
        back /= max(math.hypot(back[0], back[2]), 1e-12)
        b = math.radians(self.sole[1])
        offset = self.sole[0] * size * (math.cos(b) * back + math.sin(b) * np.array([0.0, 1.0, 0.0]))
        lead_px, trail_px = self.scene.project(lead), self.scene.project(lead + offset)
        polys = [lead_px]
        n = len(lead_px)
        for i in range(n):
            j = (i + 1) % n
            polys.append(np.array([lead_px[i], lead_px[j], trail_px[j], trail_px[i]]))
        direction = SHAFT_TUN[0] * toe + SHAFT_TUN[1] * up + SHAFT_TUN[2] * normal
        ground = origin + size * (GROUND_TUN[0] * toe + GROUND_TUN[1] * up + GROUND_TUN[2] * normal)
        axis_px = self.scene.project(np.stack([ground, ground + 150.0 * direction]))
        if not inplane_stub:
            # cylinder silhouette: offset the projected axis by the radius across the image
            for s0, s1, radius in ((8.0, HOSEL_LENGTH * size, HOSEL_RADIUS * size), (HOSEL_LENGTH * size, 150.0, SHAFT_RADIUS)):
                p0, p1 = ground + s0 * direction, ground + s1 * direction
                a, c = self.scene.project(np.stack([p0, p1]))
                t = (c - a) / np.linalg.norm(c - a)
                nrm = np.array([-t[1], t[0]])
                ra = radius * self.scene.focal / p0[2]
                rc = radius * self.scene.focal / p1[2]
                polys.append(np.array([a + ra * nrm, c + rc * nrm, c - rc * nrm, a - ra * nrm]))
        return polys, axis_px, lead_px

    def mask(self, *args, **kwargs):
        polys, axis_px, lead_px = self.parts(*args, **kwargs)
        canvas = np.zeros((self.shape[0] * SS, self.shape[1] * SS), dtype=np.uint8)
        for p in polys:
            cv2.fillPoly(canvas, [np.round((p + 0.5) * SS * 16.0).astype(np.int32)], 1, shift=4)
        return canvas.astype(bool), axis_px, lead_px


def boundary(mask_ss):
    """Outer boundary points of a supersampled mask, in native pixel coordinates."""
    cs, _h = cv2.findContours(mask_ss.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not cs:
        return np.zeros((0, 2))
    c = max(cs, key=cv2.contourArea)[:, 0, :].astype(float)
    return (c + 0.5) / SS - 0.5


def polygon_mask(poly, shape):
    canvas = np.zeros((shape[0] * SS, shape[1] * SS), dtype=np.uint8)
    cv2.fillPoly(canvas, [np.round((np.asarray(poly) + 0.5) * SS * 16.0).astype(np.int32)], 1, shift=4)
    return canvas.astype(bool)


def compare(model_ss, target_ss, row_lo, row_hi=None):
    """Symmetric boundary distance (px) and area overlap between two supersampled masks, inside rows [row_lo, row_hi)."""
    m, t = model_ss.copy(), target_ss.copy()
    lo = int(round((row_lo + 0.5) * SS))
    m[:lo], t[:lo] = False, False
    if row_hi is not None:
        hi = int(round((row_hi + 0.5) * SS))
        m[hi:], t[hi:] = False, False
    bm, bt = boundary(m), boundary(t)
    if len(bm) < 10 or len(bt) < 10:
        return {"mean": 99.0, "p95": 99.0, "iou": 0.0, "xor": 1e9}
    # ignore the artificial straight cuts at the clip rows
    def real(b):
        keep = b[:, 1] > row_lo + 0.6
        if row_hi is not None:
            keep &= b[:, 1] < row_hi - 0.6
        return b[keep]
    bm, bt = real(bm), real(bt)
    d1 = cKDTree(bt).query(bm)[0]
    d2 = cKDTree(bm).query(bt)[0]
    d = np.concatenate([d1, d2])
    inter, union = np.count_nonzero(m & t), np.count_nonzero(m | t)
    return {"mean": float(np.mean(d)), "rms": float(np.sqrt(np.mean(d ** 2))), "p95": float(np.percentile(d, 95)), "max": float(d.max()),
            "iou": inter / max(union, 1), "xor": np.count_nonzero(m ^ t) / float(SS * SS), "off_target": np.count_nonzero(m & ~t) / max(np.count_nonzero(m), 1),
            "uncovered": np.count_nonzero(t & ~m) / max(np.count_nonzero(t), 1)}


FREE = {"translation": ("dx", "dy"), "translation+size": ("dx", "dy", "size"), "translation+face": ("dx", "dy", "face"),
        "translation+face+loft+roll": ("dx", "dy", "face", "loft", "roll"), "translation+face+loft+roll+size": ("dx", "dy", "face", "loft", "roll", "size")}
SCALE = {"dx": 4.0, "dy": 4.0, "face": 6.0, "loft": 6.0, "roll": 3.0, "size": 0.04}
BOUNDS = {"face": (-40.0, 60.0), "loft": (0.0, 60.0), "roll": (-20.0, 20.0), "size": (0.75, 1.25), "dx": (-80.0, 80.0), "dy": (-80.0, 80.0)}


def fit(club, target_ss, start, free, row_lo, row_hi=None, inplane_stub=False, starts=None):
    """Minimise the rms symmetric boundary distance over the named free parameters (Nelder-Mead, several starts)."""
    names = FREE[free]

    def params(x):
        p = dict(start)
        for n, v in zip(names, x):
            p[n] = float(np.clip(start[n] + v * SCALE[n], *BOUNDS[n]))
        return p

    def cost(x):
        p = params(x)
        m, _a, _l = club.mask(p["face"], p["loft"], p["roll"], p["dx"], p["dy"], p["size"], inplane_stub=inplane_stub)
        return compare(m, target_ss, row_lo, row_hi)["rms"]

    seeds = [np.zeros(len(names))]
    for extra in (starts or []):
        seeds.append(np.array([(extra.get(n, start[n]) - start[n]) / SCALE[n] for n in names]))
    best = None
    for seed in seeds:
        res = minimize(cost, seed, method="Nelder-Mead", options={"xatol": 0.02, "fatol": 0.002, "maxiter": 900, "initial_simplex": np.vstack([seed, seed + np.eye(len(names))])})
        if best is None or res.fun < best.fun:
            best = res
    p = params(best.x)
    m, axis_px, lead_px = club.mask(p["face"], p["loft"], p["roll"], p["dx"], p["dy"], p["size"], inplane_stub=inplane_stub)
    out = compare(m, target_ss, row_lo, row_hi)
    out.update({"params": p, "evals": int(best.nfev)})
    return out, m, axis_px
