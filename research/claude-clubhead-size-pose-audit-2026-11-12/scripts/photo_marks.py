"""Where the ball marked the face, shots 1, 3 and 4, in mm from the TOE EXTREME along the score lines and mm above the
LEADING EDGE: the two numbers the pipeline's impact reading is given in, meaningful on any model of this club.

Each photo is rectified by its own score lines (photo_grooves.py) and scaled by the club's score-line spacing
(2.63 mm, from shot 1's photo with the published toe height). The mark is then placed two ways:
   LINES    relative to the score lines themselves: along them from the line of their toe ends, across them from the
            lowest line. The two offsets to the toe extreme and the leading edge are constants of the club, taken once
            from shot 1's photo. No outline is traced on shots 3 and 4 for this.
   OUTLINE  relative to the head outline landmarked by eye on that photo (outer edge), moved by shot 1's difference
            between the outer edge and the model's edge convention.
Writes results/PHOTO-MARKS.txt, results/photo-marks.json, figures/photo-marks-shot<N>.png. Usage: photo_marks.py"""
import json
import math

import cv2
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

import photo_landmarks as pl
from common import AUDIT, ROOT

OUT = []


def say(t=""):
    OUT.append(t)
    print(t)


F, PP = 1000.0, np.array([319.5, 199.5])
PITCH_MM = json.loads((AUDIT / "results" / "ts4-model.json").read_text())["score_line_pitch"]
MARK_ROI = {1: (126, 212, 178, 258), 3: (262, 342, 100, 178), 4: (384, 482, 160, 222)}   # x0, x1, y0, y1 by eye
MARK_ROI[2] = (300, 402, 138, 210)   # shot 2, by eye on 13 Nov after its blind prediction was hashed (PROTOCOL note 10c)
import os  # noqa: E402
# PHOTO_SHOTS=1,2,3,4 adds shot 2; the default reproduces the earlier outputs and file names exactly
SHOTS = tuple(int(v) for v in os.environ.get("PHOTO_SHOTS", "1,3,4").split(","))
SUFFIX = "" if SHOTS == (1, 3, 4) else "-" + "".join(str(v) for v in SHOTS)


def ray(px):
    px = np.atleast_2d(px)
    r = np.column_stack([(px[:, 0] - PP[0]) / F, (px[:, 1] - PP[1]) / F, np.ones(len(px))])
    return r / np.linalg.norm(r, axis=1)[:, None]


class Photo:
    def __init__(self, shot):
        self.shot = shot
        self.img = cv2.imread(str(ROOT / "face-photos" / ("shot-%04d.pgm" % shot)), cv2.IMREAD_GRAYSCALE).astype(np.float32)
        self.smooth = cv2.GaussianBlur(self.img, (0, 0), 1.0)
        self.mag = np.hypot(cv2.Sobel(self.smooth, cv2.CV_32F, 1, 0, ksize=3), cv2.Sobel(self.smooth, cv2.CV_32F, 0, 1, ksize=3))
        g = json.loads((AUDIT / "results" / ("photo-grooves-shot%d.json" % shot)).read_text())
        self.g = g
        ends = np.array([[l["toe_end"], l["heel_end"]] for l in g["lines"]])
        centre = ends.reshape(-1, 2).mean(axis=0)
        a = math.radians(-g["angle_deg"])
        u_img = np.array([math.cos(a), math.sin(a)])
        if u_img[0] < 0:
            u_img = -u_img
        t_img = np.array([u_img[1], -u_img[0]])
        if t_img[1] > 0:
            t_img = -t_img
        # a vanishing point is used only where the convergence is measured to better than half its size
        slopes_ok = abs(g["beta"]) > 1e-9 and np.linalg.norm(np.array(g["vp_u"]) - centre) < 12000
        d_u = ray(np.array(g["vp_u"]))[0] if slopes_ok else np.array([u_img[0], u_img[1], 0.0])
        if d_u[:2] @ u_img < 0:
            d_u = -d_u
        d_t = ray(np.array(g["vp_t"]))[0] if (g["vp_t"] is not None and abs(g["c"]) > 2.0 * g["c_sigma"]) else np.array([t_img[0], t_img[1], 0.0])
        if d_t[:2] @ t_img < 0:
            d_t = -d_t
        self.r0 = ray(centre)[0]
        n = np.cross(d_u, d_t)
        n /= np.linalg.norm(n)
        if n @ self.r0 > 0:
            n = -n
        self.d_u, self.normal = d_u, n
        self.e_v = np.cross(-d_u, n)
        self.tilt_u = math.degrees(math.asin(abs(d_u[2])))
        self.tilt_v = math.degrees(math.asin(abs(d_t[2])))
        raw = [np.array([self._rect(np.array(l["toe_end"]))[0], self._rect(np.array(l["heel_end"]))[0]]) for l in g["lines"]]
        idx = np.array([l["index"] for l in g["lines"]], dtype=float)
        v_mean = np.array([r[:, 1].mean() for r in raw])
        self.scale = PITCH_MM / abs(np.polyfit(idx, v_mean, 1)[0])
        self.lines = [r * self.scale for r in raw]
        self.index = idx

    def _rect(self, px):
        r = ray(px)
        X = r * (float(self.normal @ self.r0) / (r @ self.normal))[:, None]
        return np.column_stack([-(X @ self.d_u), X @ self.e_v])

    def rect(self, px):
        return self._rect(px) * self.scale

    def snap(self, landmarks, reach):
        pts = np.asarray(landmarks, dtype=float)
        out = []
        for i, p in enumerate(pts):
            t = pts[min(i + 1, len(pts) - 1)] - pts[max(i - 1, 0)]
            n = np.array([-t[1], t[0]]) / np.linalg.norm(t)
            cand = [p + s * n for s in np.arange(-reach, reach + 0.01, 0.5)]
            out.append(cand[int(np.argmax([self.mag[int(round(c[1])), int(round(c[0]))] for c in cand]))])
        return np.asarray(out)

    def mark(self):
        x0, x1, y0, y1 = MARK_ROI[self.shot]
        roi = np.zeros(self.img.shape, dtype=bool)
        roi[y0:y1, x0:x1] = True
        if self.shot == 1:
            ys_, xs_ = np.mgrid[0:self.img.shape[0], 0:self.img.shape[1]]
            roi &= xs_ > (128.0 + (ys_ - 190.0) * 0.267 + 3.0)       # stay inside the face: right of the toe's dark rim
        vals = np.clip(self.smooth[roi], 0, 255).astype(np.uint8).reshape(-1, 1)
        thr, _b = cv2.threshold(vals, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        dark = (roi & (self.smooth < thr)).astype(np.uint8)
        dark = cv2.morphologyEx(dark, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)))
        dark = cv2.morphologyEx(dark, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))
        n, lab, st, _c = cv2.connectedComponentsWithStats(dark, connectivity=8)
        blob = (lab == 1 + int(np.argmax(st[1:, cv2.CC_STAT_AREA]))).astype(np.uint8)
        flood = blob.copy()
        fm = np.zeros((blob.shape[0] + 2, blob.shape[1] + 2), np.uint8)
        cv2.floodFill(flood, fm, (0, 0), 2)
        m = flood != 2
        my, mx = np.nonzero(m)
        return m, np.array([mx.mean(), my.mean()]), float(thr)


photos = {n: Photo(n) for n in SHOTS}
# ---- constants of the club from shot 1: toe extreme and leading edge relative to the score lines; outer-to-model edge offsets
p1 = photos[1]
outer1, inner1 = p1.rect(p1.snap(pl.OUTER[1], 4.0)), p1.rect(p1.snap(pl.INNER[1], 2.0))


def dense(poly, n=12):
    return np.vstack([np.linspace(poly[i], poly[i + 1], n, endpoint=False) for i in range(len(poly) - 1)] + [poly[-1:]])


inner_d = dense(inner1)
mid1 = np.array([0.5 * (p + inner_d[int(np.argmin(np.linalg.norm(inner_d - p, axis=1)))]) for p in outer1])


def line_frame(ph):
    lengths = np.array([np.linalg.norm(l[0] - l[1]) for l in ph.lines])
    long_ = lengths >= 0.85 * lengths.max()
    u_toe_ends = float(np.median([l[0, 0] for l, ok in zip(ph.lines, long_) if ok]))
    bottom = ph.lines[int(np.argmax(ph.index))]
    return u_toe_ends, float(bottom[:, 1].mean()), int(long_.sum())


uT1, vB1, _n = line_frame(p1)
C_TOE = float(mid1[:, 0].max()) - uT1          # toe extreme beyond the score lines' toe ends, mm
C_BOT = vB1 - float(mid1[:, 1].min())          # lowest score line above the leading edge, mm
D_TOE = float(outer1[:, 0].max() - mid1[:, 0].max())
D_LE = float(mid1[:, 1].min() - outer1[:, 1].min())
say("club constants from shot 1's photo: toe extreme %.1f mm beyond the score lines' toe ends; lowest score line %.1f mm above the leading edge; outer edge is %.1f mm (toe) and %.1f mm (bottom) outside the model's edge" % (C_TOE, C_BOT, D_TOE, D_LE))

record = {}
for n, ph in photos.items():
    m, c_px, thr = ph.mark()
    c = ph.rect(c_px)[0]
    uT, vB, n_long = line_frame(ph)
    lines_ft, lines_al = C_TOE - (c[0] - uT), (c[1] - vB) + C_BOT
    outer = ph.rect(ph.snap(pl.OUTER[n], 4.0))
    out_ft, out_al = (float(outer[:, 0].max()) - D_TOE) - c[0], c[1] - (float(outer[:, 1].min()) + D_LE)
    toe_h = float(np.ptp(outer[:, 1]))
    ext = ph.rect(np.column_stack(np.nonzero(m)[::-1]).astype(float))
    say("shot %d photo: %d score lines %.2f px apart; face tilted %.1f deg along the lines and %.1f deg across them; outer toe-peak height %.1f mm (shot 1: 56.8)" % (
        n, len(ph.lines), ph.g["pitch_px"], ph.tilt_u, ph.tilt_v, toe_h))
    say("   mark: centroid (%.1f, %.1f) px, %.0f x %.0f mm; LINES: %.1f mm from the toe, %.1f mm above the leading edge | OUTLINE: %.1f, %.1f | the two differ by %.1f, %.1f mm" % (
        *c_px, np.ptp(ext[:, 0]), np.ptp(ext[:, 1]), lines_ft, lines_al, out_ft, out_al, lines_ft - out_ft, lines_al - out_al))
    record[str(n)] = {"centroid_px": c_px.tolist(), "lines": [lines_ft, lines_al], "outline": [out_ft, out_al], "n_lines": len(ph.lines)}
    fig, ax = plt.subplots(figsize=(17, 10.5))
    lo, hi = np.percentile(ph.img, [1, 99.5])
    ax.imshow(np.clip((ph.img - lo) / (hi - lo), 0, 1), cmap="gray", interpolation="nearest")
    for l, k in zip(ph.g["lines"], ph.index):
        ax.plot([l["toe_end"][0], l["heel_end"][0]], [l["toe_end"][1], l["heel_end"][1]], color="#39ff14", linewidth=0.7)
        ax.text(l["heel_end"][0] + 3, l["heel_end"][1], "%d" % k, color="#ffe600", fontsize=7)
    op = ph.snap(pl.OUTER[n], 4.0)
    ax.plot(op[:, 0], op[:, 1], color="#00e5ff", linewidth=0.9, label="head outline (landmarks by eye, snapped)")
    ax.contour(np.arange(ph.img.shape[1]), np.arange(ph.img.shape[0]), m.astype(float), levels=[0.5], colors=["#ff2bd6"], linewidths=1.0)
    ax.scatter([c_px[0]], [c_px[1]], s=120, marker="+", c="#ff2bd6", linewidths=1.8, label="mark and its centroid")
    ax.set_xlim(90, 580)
    ax.set_ylim(385, 20)
    ax.legend(fontsize=8, loc="lower right")
    ax.set_title("shot %d face photo: score lines (numbered from the top), outline and mark" % n, fontsize=10)
    fig.tight_layout()
    fig.savefig(AUDIT / "figures" / ("photo-marks-shot%d.png" % n), dpi=58)
    plt.close(fig)
(AUDIT / "results" / ("PHOTO-MARKS%s.txt" % SUFFIX)).write_text("\n".join(OUT) + "\n")
(AUDIT / "results" / ("photo-marks%s.json" % SUFFIX)).write_text(json.dumps(record, indent=1))
