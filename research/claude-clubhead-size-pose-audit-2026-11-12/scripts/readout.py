"""PROTOCOL addendum 1 (M7): what the shaft-anchored placement implies at contact. ILLUSTRATION ONLY.

No parameter is fitted here and no photo is read. The generic model placed on the IN-AIR frame (translation only)
is carried to the NEAR frame by the shaft (carry.py), then moved to the application's contact instant by the
ferrule's own image motion across the bracket (linear, the application's contact fraction). The ball's contact
point (centre minus r along the supplied face normal) is then read in the model's face coordinates through the
affine map of the carried outline. Also: how much of the application's drawn model lies off the head on the NEAR
frame, taking the club's own carried silhouette (clipped at the lowest visible row) as the head.

Writes results/READOUT.txt. Usage: readout.py"""
import json
import math

import cv2
import numpy as np

import carry_lib as cl
import diag_lib as dl
import geom
from common import AUDIT

OUT = []


def say(text=""):
    OUT.append(text)
    print(text)


def fill(poly, shape, clip_row=None):
    canvas = np.zeros(shape, dtype=np.uint8)
    cv2.fillPoly(canvas, [np.round(poly * 16.0).astype(np.int32)], 1, shift=4)
    if clip_row is not None:
        canvas[int(round(clip_row + 0.5)):, :] = 0
    return canvas.astype(bool)


centre_ref = json.loads((geom.APP / "src/openflight/camera/face_centre_reference.json").read_text())["categories"]["iron"]
uv = geom.OUTLINE
for shot in (1, 3, 4):
    c = cl.carry(shot)
    s, B = c["s"], c["B"]
    prep, d = s["prep"], s["d"]
    fb = c["frames"][1]
    after = d["bracket_frames"][1]
    C = dl.observe_frame(s, after)
    frac = prep["contact_fraction"]
    fer_b = np.array([B["ferrule"]["x"], B["ferrule"]["y"]])
    mm = c["mm"]
    # overhang of the application's model on the NEAR frame against the club's own carried silhouette
    shape = s["frames"][fb].shape
    own = fill(c["own_bw"], shape, B["bottom_y"])
    own[:, : int(math.ceil(B["x_cut"]))] = False
    for name, poly in (("application", s["arrays"]["outline_%d" % fb]), ("carried generic", c["gen_bw"])):
        m = fill(poly, shape, B["bottom_y"])
        m[:, : int(math.ceil(B["x_cut"]))] = False
        inter, union = np.count_nonzero(m & own), np.count_nonzero(m | own)
        say("shot %d f%d %-16s visible model %4d px^2; off the head %4d (%2.0f %%); head not covered %4d (%2.0f %% of %d); IoU %.2f" % (
            shot, fb, name, m.sum(), np.count_nonzero(m & ~own), 100.0 * np.count_nonzero(m & ~own) / max(m.sum(), 1), np.count_nonzero(own & ~m), 100.0 * np.count_nonzero(own & ~m) / max(own.sum(), 1), own.sum(), inter / max(union, 1)))
    if C is None or C["ferrule"] is None:
        say("shot %d: no ferrule on the after-contact frame %d; no readout" % (shot, after))
        continue
    fer_c = np.array([C["ferrule"]["x"], C["ferrule"]["y"]])
    step = frac * (fer_c - fer_b)
    model = c["gen_bw"] + step
    # affine map (u, v, 1) -> px of the carried outline
    design = np.column_stack([uv, np.ones(len(uv))])
    coef, *_ = np.linalg.lstsq(design, model, rcond=None)
    fit_rms = float(np.sqrt(np.mean((design @ coef - model) ** 2)))
    face, loft, lie = s["angles"]
    normal = geom.axes(face, loft, lie)[2]
    r_px = prep["ball"]["diameter_px"] / 2.0
    contact_px = np.array([prep["ball"]["x"], prep["ball"]["y"]]) - r_px * normal[:2]
    lin = coef[:2].T
    u, v = np.linalg.solve(lin, contact_px - coef[2])
    # top line height above the face centre at this u (highest outline point within 1.5 mm of u)
    near = np.abs(uv[:, 0] - u) < 1.5
    top_v = float(uv[near, 1].max()) if near.any() else math.nan
    pl = s["state"]["placement"]
    say("shot %d: ferrule moved (%+.1f, %+.1f) px across the bracket f%d -> f%d; contact fraction %.3f; outline affine fit rms %.2f px" % (shot, *(fer_c - fer_b), fb, after, frac, fit_rms))
    say("   shaft-anchored placement at contact: %+.1f mm toe-ward of the face centre, %+.1f mm up from it = %.1f mm above the leading edge, %.1f mm below the top line there" % (u, v, v + centre_ref["centre_above_leading_edge_mm"], top_v - v))
    for dy in (-3.0, 3.0):
        u2, v2 = np.linalg.solve(lin, contact_px - coef[2] - np.array([0.0, dy]))
        say("      if the carried model is %+.0f px (%.0f mm) %s: %+.1f toe-ward, %+.1f up" % (dy, abs(dy) * mm, "lower" if dy > 0 else "higher", u2, v2))
    say("   application 0fa9870b: %s toe-ward (+- %s), height %s: %s" % (
        "%+.1f mm" % pl["impact_offset_mm"] if pl["impact_offset_mm"] is not None else "none", "%.1f" % pl["impact_offset_sigma_mm"] if pl["impact_offset_sigma_mm"] else "?",
        pl["height_status"], "%+.1f mm" % pl["impact_height_mm"] if pl["impact_height_mm"] is not None else pl["height_reason"]))

(AUDIT / "results" / "READOUT.txt").write_text("\n".join(OUT) + "\n")
