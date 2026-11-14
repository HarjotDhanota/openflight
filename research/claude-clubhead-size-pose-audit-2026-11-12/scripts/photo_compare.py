"""Shot 1: the photographed mark against the impact point read from the placed 3-D club (results/IMPACT-SHOT1.txt).
The reading was written BEFORE this script existed and is not changed here. The photo is read-only.

The photo is mapped into the model's face coordinates. Choices that matter are all run, none is picked afterwards:
  head outline on the photo, two conventions
     INNER  the edge of the flat face itself (inside the dark rim that is the toe wall and the sole seen obliquely)
     OUTER  the outside of that dark rim (the silhouette as photographed)
  mapping, two kinds
     LANDMARKS  axes from the score lines; toe tip, leading-edge tangent and toe peak give origin and ONE scale
                (photo toe-peak height = the model's at size 0.919), used along the score lines too
     AFFINE     a free 6-number affine map of the model's blade outline (toe, top line, sole out to 70 mm) onto the
                traced outline, started from LANDMARKS
The mark is the dark dimple print: darker-than-face pixels near the toe, largest component, holes filled.
Writes results/PHOTO-SHOT1.txt, figures/photo-compare-shot1.png. Usage: photo_compare.py"""
import json
import math
import re

import cv2
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.optimize import minimize
from scipy.spatial import cKDTree

import club3d
import geom
from common import AUDIT, ROOT

OUT = []


def say(t=""):
    OUT.append(t)
    print(t)


SIZE = json.loads((AUDIT / "results" / "fit-trace.json").read_text())["translation+size, 3-D hosel"]["params"]["size"]
img = cv2.imread(str(ROOT / "face-photos" / "shot-0001.pgm"), cv2.IMREAD_GRAYSCALE).astype(np.float32)
smooth = cv2.GaussianBlur(img, (0, 0), 1.0)

# ---------------------------------------------------------------- score lines: direction and pitch
region = (slice(150, 250), slice(235, 345))  # y, x: middle of the scored area, clear of the mark and the edges
patch = smooth[region] - cv2.GaussianBlur(smooth[region], (0, 0), 6.0)
yy, xx = np.mgrid[0:patch.shape[0], 0:patch.shape[1]]
best = None
for ang in np.arange(-40.0, -10.0, 0.1):  # direction of the lines in the image, degrees (y down)
    a = math.radians(ang)
    across = -math.sin(a) * xx + math.cos(a) * yy
    bins = np.round((across - across.min()) * 2).astype(int)
    prof = np.bincount(bins.ravel(), weights=patch.ravel()) / np.maximum(np.bincount(bins.ravel()), 1)
    prof = prof[10:-10]
    score = float(np.var(prof))
    if best is None or score > best[0]:
        best = (score, ang, prof)
_s, groove_deg, prof = best
p0 = prof - prof.mean()
ac = np.correlate(p0, p0, mode="full")[len(p0) - 1:]
lags = np.arange(len(ac)) * 0.5
sel = (lags >= 7.0) & (lags <= 15.0)
pitch_px = float(lags[sel][int(np.argmax(ac[sel]))])
a = math.radians(groove_deg)
u_heel = np.array([math.cos(a), math.sin(a)])          # along the score lines, toward the heel (image right)
u_toe = -u_heel
v_up = np.array([math.sin(a), -math.cos(a)])            # across them, up the face
if v_up[1] > 0:
    v_up = -v_up
say("score lines: direction %.1f deg in the image (rising toward the heel), pitch %.1f px across them (autocorrelation, 7 to 15 px searched)" % (-groove_deg, pitch_px))

# ---------------------------------------------------------------- head outline on the photo, two conventions (read by eye from enlarged views)
OUTER = [(385, 129), (370, 128), (340, 126), (300, 123), (250, 119), (200, 117), (160, 116), (140, 117), (128, 124), (120, 135), (115, 148), (113, 165),
         (114, 185), (118, 205), (123, 225), (129, 238), (135, 250), (142, 259), (150, 267), (160, 274), (170, 280), (180, 285), (190, 288.5), (200, 290),
         (210, 289.5), (220, 288), (230, 285.5), (240, 282), (250, 278), (260, 274), (270, 270), (279, 266), (300, 252), (325, 238), (350, 222), (375, 205)]
INNER = [(370, 130.5), (340, 128.5), (300, 125.5), (250, 121.5), (200, 119.5), (160, 119), (145, 120), (137, 122.5), (130, 127), (125.5, 133), (123, 140),
         (122, 147), (123, 155), (124, 165), (125.5, 175), (127, 185), (128.5, 195), (130, 205), (132, 215), (134.5, 225), (137, 235), (140.5, 245),
         (146, 255), (153, 264.5), (161, 271), (170, 276), (180, 279.5), (190, 281.5), (200, 282), (210, 281), (220, 278), (230, 275.5), (240, 272),
         (250, 268.5), (260, 264.5), (270, 260.5), (279, 257), (300, 243), (325, 229), (350, 213)]
gx = cv2.Sobel(smooth, cv2.CV_32F, 1, 0, ksize=3)
gy = cv2.Sobel(smooth, cv2.CV_32F, 0, 1, ksize=3)
mag = np.hypot(gx, gy)


def snap(landmarks, reach):
    pts = np.asarray(landmarks, dtype=float)
    out = []
    for i, p in enumerate(pts):
        t = pts[min(i + 1, len(pts) - 1)] - pts[max(i - 1, 0)]
        n = np.array([-t[1], t[0]]) / np.linalg.norm(t)
        cand = [p + s * n for s in np.arange(-reach, reach + 0.01, 0.5)]
        out.append(cand[int(np.argmax([mag[int(round(c[1])), int(round(c[0]))] for c in cand]))])
    out = np.asarray(out)
    dense = np.vstack([np.linspace(out[i], out[i + 1], 15, endpoint=False) for i in range(len(out) - 1)] + [out[-1:]])
    return dense, float(np.median(np.linalg.norm(out - pts, axis=1)))


# ---------------------------------------------------------------- the mark
face_level = float(np.median(img[150:180, 150:200]))       # clean face just above the print
roi = np.zeros(img.shape, dtype=bool)
roi[178:258, 126:212] = True
ys_, xs_ = np.mgrid[0:img.shape[0], 0:img.shape[1]]
roi &= xs_ > (128.0 + (ys_ - 190.0) * 0.267 + 3.0)          # stay inside the face: right of the toe's dark rim
vals = smooth[roi]
thr, _b = cv2.threshold(np.clip(vals, 0, 255).astype(np.uint8).reshape(-1, 1), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
dark = roi & (smooth < thr)
closed = cv2.morphologyEx(dark.astype(np.uint8), cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)))
# score lines are dark too and run into the print: an opening wider than a line (about 2 px) cuts them off
closed = cv2.morphologyEx(closed, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))
n, lab, st, _cen = cv2.connectedComponentsWithStats(closed, connectivity=8)
blob = (lab == 1 + int(np.argmax(st[1:, cv2.CC_STAT_AREA]))).astype(np.uint8)
flood = blob.copy()
fmask = np.zeros((blob.shape[0] + 2, blob.shape[1] + 2), np.uint8)
cv2.floodFill(flood, fmask, (0, 0), 2)
mark = flood != 2
my, mx = np.nonzero(mark)
mark_pts = np.column_stack([mx, my]).astype(float)
mark_c = mark_pts.mean(axis=0)
al, ac_ = mark_pts @ u_heel, mark_pts @ v_up
ext_u, ext_v = float(np.ptp(al)) + 1.0, float(np.ptp(ac_)) + 1.0
mark_box = (al.max() + al.min()) / 2.0 * u_heel + (ac_.max() + ac_.min()) / 2.0 * v_up
say("mark: face level %.0f DN, Otsu threshold %.0f DN; %d px^2; centroid (%.1f, %.1f); centre of its extent (%.1f, %.1f); %.1f px along the score lines x %.1f px across (ratio %.2f)" % (
    face_level, thr, mark.sum(), *mark_c, *mark_box, ext_u, ext_v, ext_u / ext_v))

# ---------------------------------------------------------------- model numbers and the reading
uv = geom.OUTLINE * SIZE
toe_u, le_v, peak_v = float(uv[:, 0].max()), float(uv[:, 1].min()), float(uv[:, 1].max())
model_height = peak_v - le_v
text = (AUDIT / "results" / "IMPACT-SHOT1.txt").read_text()
m = re.search(r"all eight: toe-ward ([-+\d.]+) \.\. ([-+\d.]+) mm \(median ([-+\d.]+)\); up ([-+\d.]+) \.\. ([-+\d.]+) mm \(median ([-+\d.]+)\)", text)
r_u, r_v = (float(m.group(1)), float(m.group(2)), float(m.group(3))), (float(m.group(4)), float(m.group(5)), float(m.group(6)))
say("reading from the placed 3-D club (unchanged): toe-ward %+.1f .. %+.1f (median %+.1f) mm; up %+.1f .. %+.1f (median %+.1f) mm" % (*r_u, *r_v))
say("model at size %.3f: toe tip %.1f mm from the centre, leading edge %.1f mm below it, toe peak %.1f mm above the leading edge" % (SIZE, toe_u, -le_v, model_height))
blade = club3d.HEAD * SIZE
keep_model = blade[(toe_u - blade[:, 0]) < 70.0 * SIZE]

results, maps = {}, {}
for conv, landmarks, reach in (("INNER", INNER, 2.0), ("OUTER", OUTER, 4.0)):
    dense, moved = snap(landmarks, reach)
    toe_tip = dense[int(np.argmax(dense @ u_toe))]
    le_level, peak_level = float((dense @ v_up).min()), float((dense @ v_up).max())
    height_px = peak_level - le_level
    scale = height_px / model_height
    say()
    say("%s outline: %d landmarks, each moved to the strongest edge within %.0f px (median move %.1f px); toe tip (%.1f, %.1f); toe-peak face height %.1f px -> %.2f px per mm (score-line pitch would be %.2f mm)" % (
        conv, len(landmarks), reach, moved, *toe_tip, height_px, scale, pitch_px / scale))
    for name, p in (("centroid", mark_c), ("centre of extent", mark_box)):
        d_toe = float((p - toe_tip) @ u_heel) / scale
        h_le = (float(p @ v_up) - le_level) / scale
        u, v = toe_u - d_toe, h_le + le_v
        results[(conv, "LANDMARKS", name)] = (u, v)
        say("   LANDMARKS  mark %-17s %5.1f mm from the toe tip, %5.1f mm above the leading edge = %+5.1f toe-ward of centre, %+5.1f up | minus the reading's median %+5.1f, %+5.1f mm" % (
            name + ":", d_toe, h_le, u, v, u - r_u[2], v - r_v[2]))
    say("   LANDMARKS  mark size %.1f x %.1f mm; scale-free: centroid at %.2f of the toe-peak face height (reading %.2f, %.2f .. %.2f)" % (
        ext_u / scale, ext_v / scale, (float(mark_c @ v_up) - le_level) / height_px, (r_v[2] - le_v) / model_height, (r_v[0] - le_v) / model_height, (r_v[1] - le_v) / model_height))
    A0 = np.column_stack([u_toe * scale, v_up * scale])
    origin0 = toe_tip + u_heel * toe_u * scale
    origin0 = origin0 + v_up * ((le_level - le_v * scale) - float(origin0 @ v_up))
    tree = cKDTree(dense)
    near = dense[(dense - toe_tip) @ u_heel < 62.0 * SIZE * scale]

    def mapped(x, pts_uv, A0=A0, origin0=origin0):
        A = A0 @ (np.eye(2) + np.array([[x[0], x[1]], [x[2], x[3]]]))
        return pts_uv @ A.T + origin0 + np.array([x[4], x[5]]) * 10.0, A

    def cost(x, tree=tree, near=near, mapped=mapped):
        q, _A = mapped(x, keep_model)
        d1 = tree.query(q)[0]
        d2 = cKDTree(q).query(near)[0]
        return float(np.mean(np.minimum(d1, 12.0) ** 2) + np.mean(np.minimum(d2, 12.0) ** 2))

    res = minimize(cost, np.zeros(6), method="Nelder-Mead", options={"xatol": 1e-4, "fatol": 1e-4, "maxiter": 8000})
    res = minimize(cost, res.x, method="Nelder-Mead", options={"xatol": 1e-4, "fatol": 1e-4, "maxiter": 8000})
    _q, A = mapped(res.x, keep_model)
    origin = mapped(res.x, np.zeros((1, 2)))[0][0]
    su, sv = float(np.linalg.norm(A[:, 0])), float(np.linalg.norm(A[:, 1]))
    skew = math.degrees(math.acos(float(A[:, 0] @ A[:, 1]) / (su * sv))) - 90.0
    turn = math.degrees(math.atan2(float(np.cross(u_toe, A[:, 0])), float(u_toe @ A[:, 0])))
    say("   AFFINE     outline distance %.2f px rms at the LANDMARKS start, %.2f after the fit; %.2f px/mm along the score lines, %.2f across; axes %+.1f deg from square; model's score-line axis %+.1f deg from the photo's" % (
        math.sqrt(cost(np.zeros(6)) / 2.0), math.sqrt(res.fun / 2.0), su, sv, skew, turn))
    for name, p in (("centroid", mark_c), ("centre of extent", mark_box)):
        u, v = np.linalg.solve(A, p - origin)
        results[(conv, "AFFINE", name)] = (float(u), float(v))
        say("   AFFINE     mark %-17s %5.1f mm from the toe tip, %5.1f mm above the leading edge = %+5.1f toe-ward of centre, %+5.1f up | minus the reading's median %+5.1f, %+5.1f mm" % (
            name + ":", toe_u - u, v - le_v, u, v, u - r_u[2], v - r_v[2]))
    maps[conv] = (A, origin, dense)

all_u = np.array([r[0] for r in results.values()])
all_v = np.array([r[1] for r in results.values()])
say()
say("PHOTO, all eight: toe-ward %+.1f .. %+.1f mm (median %+.1f); up %+.1f .. %+.1f mm (median %+.1f)" % (all_u.min(), all_u.max(), np.median(all_u), all_v.min(), all_v.max(), np.median(all_v)))
say("READING, all eight: toe-ward %+.1f .. %+.1f mm (median %+.1f); up %+.1f .. %+.1f mm (median %+.1f)" % (*r_u, *r_v))
say("photo median minus reading median: %+.1f mm toe-ward, %+.1f mm up" % (float(np.median(all_u)) - r_u[2], float(np.median(all_v)) - r_v[2]))
app = (32.746, -10.793)
say("photo median minus application 0fa9870b (+32.7, -10.8): %+.1f mm toe-ward, %+.1f mm up" % (float(np.median(all_u)) - app[0], float(np.median(all_v)) - app[1]))
say("mark half-width along the score lines: %.1f px = about %.1f mm at 3.5 px/mm" % (ext_u / 2.0, ext_u / 2.0 / 3.5))
(AUDIT / "results" / "PHOTO-SHOT1.txt").write_text("\n".join(OUT) + "\n")

# ---------------------------------------------------------------- figure (INNER affine map for drawing)
A, origin, dense = maps["INNER"]
fig, axs = plt.subplots(1, 2, figsize=(24, 9.5), gridspec_kw={"width_ratios": [1.45, 1]})
a0 = axs[0]
x0, x1, y0, y1 = 95, 440, 95, 305
crop = img[y0:y1, x0:x1]
lo, hi = np.percentile(crop, [1, 99.5])
a0.imshow(np.clip((crop - lo) / (hi - lo), 0, 1), cmap="gray", interpolation="nearest", extent=(x0 - .5, x1 - .5, y1 - .5, y0 - .5))
a0.plot(dense[:, 0], dense[:, 1], color="#00e5ff", linewidth=0.9, label="face edge traced on the photo (inner convention)")
full = np.vstack([blade, blade[:1]]) @ A.T + origin
a0.plot(full[:, 0], full[:, 1], color="#ffe600", linewidth=1.0, linestyle="--", label="model blade mapped onto the photo (affine)")
a0.contour(np.arange(img.shape[1]), np.arange(img.shape[0]), mark.astype(float), levels=[0.5], colors=["#ff2bd6"], linewidths=0.9)
a0.scatter([mark_c[0]], [mark_c[1]], s=110, marker="+", c="#ff2bd6", linewidths=1.8, label="photographed mark: outline and centroid")
corners = np.array([[r_u[0], r_v[0]], [r_u[1], r_v[0]], [r_u[1], r_v[1]], [r_u[0], r_v[1]], [r_u[0], r_v[0]]])
box_px = corners @ A.T + origin
med = np.array([r_u[2], r_v[2]])
med_px = med @ A.T + origin
a0.plot(box_px[:, 0], box_px[:, 1], color="#39ff14", linewidth=1.3)
a0.scatter([med_px[0]], [med_px[1]], s=110, marker="x", c="#39ff14", linewidths=2.0, label="impact point read from the placed 3-D club (box: its eight variants)")
app_px = np.array(app) @ A.T + origin
a0.scatter([app_px[0]], [app_px[1]], s=90, marker="x", c="#ff8c00", linewidths=1.8, label="application 0fa9870b")
a0.set_xlim(x0 - .5, x1 - .5)
a0.set_ylim(y1 - .5, y0 - .5)
a0.legend(fontsize=8, loc="upper right")
a0.set_title("shot 1 face photo (original pixels) with the readings mapped onto it", fontsize=10)
a1 = axs[1]
o = geom.OUTLINE * SIZE
a1.fill(-o[:, 0], o[:, 1], color="#d9dde3", zorder=0)
a1.plot(-np.append(o[:, 0], o[0, 0]), np.append(o[:, 1], o[0, 1]), color="#555", linewidth=1.0)
cloud = np.linalg.solve(A, (mark_pts[::3] - origin).T).T
a1.scatter(-cloud[:, 0], cloud[:, 1], s=2, c="#ff2bd6", alpha=0.25)
shown = set()
for (conv, method, name), (u, v) in results.items():
    lab = "photographed mark, 8 ways of mapping it" if not shown else None
    shown.add(1)
    a1.scatter([-u], [v], s=70, marker="+", c="#b0007a", linewidths=1.4, label=lab)
a1.plot(-corners[:, 0], corners[:, 1], color="#1a9a1a", linewidth=1.3)
a1.scatter([-med[0]], [med[1]], s=100, marker="x", c="#1a9a1a", linewidths=2.0, label="read from the placed 3-D club (box: its eight variants)")
a1.scatter([-app[0]], [app[1]], s=80, marker="x", c="#ff8c00", linewidths=1.8, label="application 0fa9870b")
a1.axhline(0, color="#999", linewidth=0.5)
a1.axvline(0, color="#999", linewidth=0.5)
a1.set_aspect("equal")
a1.set_xlim(-48, 62)
a1.set_xlabel("face-on, toe LEFT; mm from the face centre (model at size %.2f)" % SIZE)
a1.set_ylabel("mm up from the face centre")
a1.legend(fontsize=8, loc="upper right")
a1.set_title("the same in the model's face coordinates (pink cloud: the mark's pixels, inner affine map)", fontsize=10)
fig.tight_layout()
fig.savefig(AUDIT / "figures" / "photo-compare-shot1.png", dpi=60)
