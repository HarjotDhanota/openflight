"""Score lines in a face photo: each line fitted, its ends found, and the two perspective cues they carry.

  convergence   the lines are parallel on the club; in the photo they meet at a vanishing point. That gives the 3-D
                direction of the score lines relative to the camera.
  spacing       the lines are equally spaced on the club; in the photo the spacing changes steadily across the face.
                That gives the vanishing point of a line crossing them.
With the camera's focal length the two give the face plane's tilt, so the photo can be rectified to true proportions
(one overall scale is still needed; it comes from a spec dimension, not from this file).
Usage: photo_grooves.py <shot>   (writes results/photo-grooves-shot<N>.json and a check figure)"""
import json
import math
import sys

import cv2
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.optimize import least_squares

from common import AUDIT, ROOT

shot = int(sys.argv[1])
img = cv2.imread(str(ROOT / "face-photos" / ("shot-%04d.pgm" % shot)), cv2.IMREAD_GRAYSCALE).astype(np.float32)
H, W = img.shape
smooth = cv2.GaussianBlur(img, (0, 0), 0.8)
detail = smooth - cv2.GaussianBlur(smooth, (0, 0), 5.0)          # dark lines are negative

# ---- direction of the lines (coarse), from the middle of the photo
# a clean patch of scored face, and the range of directions to search, read off each photo by eye
REGIONS = {1: (235, 345, 150, 250, -40.0, -10.0), 3: (250, 380, 175, 228, -35.0, -5.0), 4: (290, 420, 222, 285, -35.0, -5.0),
           2: (290, 420, 100, 142, -35.0, -5.0)}   # shot 2 added 13 Nov AFTER its blind prediction was hashed (PROTOCOL note 10c)
rx0, rx1, ry0, ry1, a_lo, a_hi = REGIONS[shot]
CX, CY = (rx0 + rx1) / 2.0, (ry0 + ry1) / 2.0
best = None
ys, xs = np.mgrid[0:H, 0:W]
central = (xs >= rx0) & (xs < rx1) & (ys >= ry0) & (ys < ry1)
for ang in np.arange(a_lo, a_hi + 0.01, 0.1):
    a = math.radians(ang)
    across = (-math.sin(a) * xs + math.cos(a) * ys)[central]
    bins = np.round((across - across.min()) * 2).astype(int)
    prof = np.bincount(bins, weights=detail[central]) / np.maximum(np.bincount(bins), 1)
    score = float(np.var(prof[8:-8]))
    if best is None or score > best[0]:
        best = (score, ang)
angle = best[1]
# ---- rotate so the lines run left to right
M = cv2.getRotationMatrix2D((CX, CY), angle, 1.0)
rot = cv2.warpAffine(detail, M, (W, H), flags=cv2.INTER_LINEAR, borderValue=0.0)
Minv = cv2.invertAffineTransform(M)


def back(pts):
    pts = np.atleast_2d(pts)
    return pts @ Minv[:, :2].T + Minv[:, 2]


# ---- seed rows: minima of the mean profile over a central band of columns
band = rot[:, int(CX - 40):int(CX + 40)].mean(axis=1)
band_s = cv2.GaussianBlur(band.reshape(-1, 1), (0, 0), 1.0).ravel()
# thresholds follow this photo's own contrast: 0.4 of a typical score line's dip in the scored patch
rot_rows = (M @ np.array([[rx0, ry0, 1.0], [rx1, ry1, 1.0], [rx0, ry1, 1.0], [rx1, ry0, 1.0]]).T)[1]
r_lo, r_hi = int(rot_rows.min()), int(rot_rows.max())
dips = [-band_s[y] for y in range(max(r_lo, 2), min(r_hi, H - 2)) if band_s[y] < band_s[y - 1] and band_s[y] <= band_s[y + 1] and band_s[y] < 0]
typical = float(np.median(dips))                       # a typical score line's dip in this photo
seed_thr, depth_thr = 0.4 * typical, 0.4 * typical
print('typical dip %.2f DN: seed and tracking thresholds %.2f' % (typical, seed_thr))
seeds = [y for y in range(20, H - 20) if band_s[y] < band_s[y - 1] and band_s[y] <= band_s[y + 1] and band_s[y] < -seed_thr]
lines = []
for y0 in seeds:
    pts = []
    for direction in (1, -1):
        y = float(y0)
        x = int(CX)
        misses = 0
        while 5 < x < W - 5 and misses < 6:
            lo = int(round(y)) - 3
            col = rot[lo:lo + 7, x]
            if len(col) < 7:
                break
            i = int(np.argmin(col))
            depth = float(np.median(col) - col[i])
            if depth > depth_thr and 0 < i < 6:
                # parabola through the minimum and its neighbours
                d = col[i - 1] - 2 * col[i] + col[i + 1]
                sub = 0.5 * (col[i - 1] - col[i + 1]) / d if d > 1e-6 else 0.0
                y = lo + i + sub
                pts.append((x, y, depth))
                misses = 0
            else:
                misses += 1
            x += direction
    if len(pts) < 40:
        continue
    pts = np.asarray(sorted(pts))
    # keep the longest run without gaps larger than 6 columns (one physical line)
    keep = np.ones(len(pts), dtype=bool)
    slope, icpt = np.polyfit(pts[:, 0], pts[:, 1], 1)
    for _ in range(3):
        res = pts[:, 1] - (slope * pts[:, 0] + icpt)
        keep = np.abs(res) < max(0.6, 2.5 * np.std(res[keep]))
        slope, icpt = np.polyfit(pts[keep, 0], pts[keep, 1], 1)
    p = pts[keep]
    lines.append({"slope": float(slope), "icpt": float(icpt), "x0": float(p[:, 0].min()), "x1": float(p[:, 0].max()), "n": int(len(p)),
                  "rms": float(np.sqrt(np.mean((p[:, 1] - (slope * p[:, 0] + icpt)) ** 2)))})
# drop duplicates (two seeds on one line) and order top to bottom at the central column
xc = CX
lines.sort(key=lambda l: l["slope"] * xc + l["icpt"])
uniq = []
for l in lines:
    yc = l["slope"] * xc + l["icpt"]
    if uniq and yc - (uniq[-1]["slope"] * xc + uniq[-1]["icpt"]) < 4.0:
        if l["n"] > uniq[-1]["n"]:
            uniq[-1] = l
        continue
    uniq.append(l)
med = float(np.median([l["slope"] for l in uniq]))
dropped = [l for l in uniq if abs(l["slope"] - med) >= 0.03 or l["n"] < 60]
lines = [l for l in uniq if abs(l["slope"] - med) < 0.03 and l["n"] >= 60]   # head edges and the mat are not score lines
print("dropped %d candidates that are not parallel to the rest or too short" % len(dropped))
yc = np.array([l["slope"] * xc + l["icpt"] for l in lines])
gaps = np.diff(yc)
pitch = float(np.median(gaps))
# index the lines by their spacing (a hidden or missed line leaves a double gap)
index = [0]
for g in gaps:
    index.append(index[-1] + int(round(g / pitch)))
index = np.array(index)
print("shot %d photo: lines at %.2f deg; %d score lines found, median spacing %.2f px, indices %s" % (shot, -angle, len(lines), pitch, index.tolist()))

# ---- convergence: slope against position (rotated frame) -> vanishing point of the score lines
slopes = np.array([l["slope"] for l in lines])
beta, alpha = np.polyfit(yc, slopes, 1)
vp_u_rot = np.array([xc - 1.0 / beta, -alpha / beta]) if abs(beta) > 1e-9 else None
slope_rms = float(np.sqrt(np.mean((slopes - (alpha + beta * yc)) ** 2)))
print("   slope changes by %.5f per px across the lines (scatter %.5f): score lines converge %.0f px away along their length" % (beta, slope_rms, abs(1.0 / beta) if abs(beta) > 1e-9 else float("inf")))

# ---- spacing: position against index, projective  y = (a k + b) / (c k + 1)
def proj(p, k):
    return (p[0] * k + p[1]) / (p[2] * k + 1.0)

sol = least_squares(lambda p: proj(p, index) - yc, [pitch, yc[0], 0.0], x_scale=[1.0, 10.0, 1e-3])
a_, b_, c_ = sol.x
J = sol.jac
cov = np.linalg.pinv(J.T @ J) * float(np.mean(sol.fun ** 2)) * len(yc) / max(len(yc) - 3, 1)
print("   spacing: projective term c = %.5f +- %.5f per line (0 = no perspective across the lines); fit rms %.2f px" % (c_, math.sqrt(cov[2, 2]), float(np.sqrt(np.mean(sol.fun ** 2)))))
vp_t_rot = np.array([xc, a_ / c_]) if abs(c_) > 1e-7 else None

out = {"shot": shot, "angle_deg": -angle, "pitch_px": pitch, "index": index.tolist(), "c": float(c_), "c_sigma": float(math.sqrt(cov[2, 2])), "beta": float(beta),
       "vp_u": back(vp_u_rot)[0].tolist() if vp_u_rot is not None else None, "vp_t": back(vp_t_rot)[0].tolist() if vp_t_rot is not None else None,
       "lines": []}
for l, k in zip(lines, index):
    e0 = back([l["x0"], l["slope"] * l["x0"] + l["icpt"]])[0]
    e1 = back([l["x1"], l["slope"] * l["x1"] + l["icpt"]])[0]
    out["lines"].append({"index": int(k), "toe_end": e0.tolist(), "heel_end": e1.tolist(), "length_px": float(np.linalg.norm(e1 - e0)), "rms_px": l["rms"]})
(AUDIT / "results" / ("photo-grooves-shot%d.json" % shot)).write_text(json.dumps(out, indent=1))

fig, ax = plt.subplots(figsize=(18, 11))
lo, hi = np.percentile(img, [1, 99.5])
ax.imshow(np.clip((img - lo) / (hi - lo), 0, 1), cmap="gray", interpolation="nearest")
for l in out["lines"]:
    ax.plot([l["toe_end"][0], l["heel_end"][0]], [l["toe_end"][1], l["heel_end"][1]], color="#39ff14", linewidth=0.9)
    ax.text(l["heel_end"][0] + 3, l["heel_end"][1], str(l["index"]), color="#ffe600", fontsize=7)
ax.set_title("shot %d face photo: %d score lines fitted (numbers = line index; gaps in the numbering are lines hidden by the print)" % (shot, len(lines)), fontsize=10)
ax.set_xlim(90, 560)
ax.set_ylim(380, 60)
fig.tight_layout()
fig.savefig(AUDIT / "figures" / ("photo-grooves-shot%d.png" % shot), dpi=60)
