"""How much does the mark's position depend on how the mark is outlined? Written after shot 2's blind prediction was
hashed and after seeing that the frozen detector's blob on shot 2 is cut flat by its by-eye box (bottom and right).

Three outlines of the mark per photo, all converted to mm the same way (photo_marks.py: score-line rectification,
shot 1's club constants; LINES and OUTLINE methods averaged):
   frozen     photo_marks.py as it stands (Otsu on brightness inside the by-eye box)
   flattened  the box grown 15 px each way and kept inside the head outline shrunk by 6 px; brightness minus its own
              40 px Gaussian blur (removes the face's shading); Otsu on that; same closing, opening and largest blob
   box        the centre of the mark's bounding box read by eye from an enlarged view (shot 2 only; crude)
The variants are run on shots 1, 3, 4 as a control: if "flattened" moves those marks by more than 1 mm it is not a
like-for-like alternative. Nothing here reads the prediction. Usage: PHOTO_SHOTS=1,2,3,4 photo_mark_variants.py
Writes results/PHOTO-MARK-VARIANTS.txt and figures/photo-mark-variants-shot<N>.png."""
import cv2
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

import photo_landmarks as pl
import photo_marks as pm
from common import AUDIT

OUT = []
EYE_BOX = {2: (312, 389, 147, 201)}   # x0, x1, y0, y1 of the dark print, read from figures/photo-grid-shot2-zoom.png


def say(t=""):
    OUT.append(t)
    print(t)


def to_mm(ph, c_px):
    c = ph.rect(np.asarray(c_px, dtype=float))[0]
    uT, vB, _n = pm.line_frame(ph)
    lines = (pm.C_TOE - (c[0] - uT), (c[1] - vB) + pm.C_BOT)
    outer = ph.rect(ph.snap(pl.OUTER[ph.shot], 4.0))
    outline = ((float(outer[:, 0].max()) - pm.D_TOE) - c[0], c[1] - (float(outer[:, 1].min()) + pm.D_LE))
    return 0.5 * (lines[0] + outline[0]), 0.5 * (lines[1] + outline[1]), lines, outline


def flattened(ph):
    x0, x1, y0, y1 = pm.MARK_ROI[ph.shot]
    roi = np.zeros(ph.img.shape, dtype=np.uint8)
    roi[max(y0 - 15, 0):y1 + 15, max(x0 - 15, 0):x1 + 15] = 1
    head = np.zeros(ph.img.shape, dtype=np.uint8)
    cv2.fillPoly(head, [np.round(ph.snap(pl.OUTER[ph.shot], 4.0)).astype(np.int32)], 1)
    head = cv2.erode(head, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (13, 13)))
    roi = (roi & head).astype(bool)
    flat = ph.smooth - cv2.GaussianBlur(ph.smooth, (0, 0), 40.0)
    vals = flat[roi]
    scaled = np.clip((vals - vals.min()) / (vals.max() - vals.min()) * 255.0, 0, 255).astype(np.uint8).reshape(-1, 1)
    thr, _b = cv2.threshold(scaled, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    level = vals.min() + thr / 255.0 * (vals.max() - vals.min())
    dark = (roi & (flat < level)).astype(np.uint8)
    dark = cv2.morphologyEx(dark, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)))
    dark = cv2.morphologyEx(dark, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))
    n, lab, st, _c = cv2.connectedComponentsWithStats(dark, connectivity=8)
    if n < 2:
        return None, None, roi
    blob = (lab == 1 + int(np.argmax(st[1:, cv2.CC_STAT_AREA]))).astype(np.uint8)
    flood = blob.copy()
    cv2.floodFill(flood, np.zeros((blob.shape[0] + 2, blob.shape[1] + 2), np.uint8), (0, 0), 2)
    m = flood != 2
    my, mx = np.nonzero(m)
    touches = bool((m & ~cv2.erode(roi.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)).any())
    return m, np.array([mx.mean(), my.mean()]), touches


for n, ph in pm.photos.items():
    m0, c0, _thr = ph.mark()
    x0, x1, y0, y1 = pm.MARK_ROI[n]
    ys, xs = np.nonzero(m0)
    clipped = [side for side, hit in (("left", xs.min() <= x0), ("right", xs.max() >= x1 - 1), ("top", ys.min() <= y0), ("bottom", ys.max() >= y1 - 1)) if hit]
    f0 = to_mm(ph, c0)
    m1, c1, touches = flattened(ph)
    say("shot %d   frozen: centroid (%.1f, %.1f) px -> %.1f mm from the toe, %.1f mm above the leading edge; blob touches its box on: %s" % (n, *c0, f0[0], f0[1], ", ".join(clipped) or "no side"))
    if c1 is not None:
        f1 = to_mm(ph, c1)
        say("         flattened: centroid (%.1f, %.1f) px -> %.1f, %.1f (moves %+.1f, %+.1f mm); touches its search region: %s" % (*c1, f1[0], f1[1], f1[0] - f0[0], f1[1] - f0[1], "yes" if touches else "no"))
        # added after looking at the two outlines: each leaks somewhere the other does not (the frozen one into its
        # box corners, the flattened one along dark bands at the face's edges), so their overlap is a third estimate
        both = (m0 & m1).astype(np.uint8)
        nb, labb, stb, _cb = cv2.connectedComponentsWithStats(both, connectivity=8)
        if nb > 1:
            keep = labb == 1 + int(np.argmax(stb[1:, cv2.CC_STAT_AREA]))
            by, bx = np.nonzero(keep)
            f3 = to_mm(ph, [bx.mean(), by.mean()])
            say("         overlap of the two: centroid (%.1f, %.1f) px -> %.1f, %.1f (moves %+.1f, %+.1f mm)" % (bx.mean(), by.mean(), f3[0], f3[1], f3[0] - f0[0], f3[1] - f0[1]))
    else:
        say("         flattened: no blob")
    if n in EYE_BOX:
        bx0, bx1, by0, by1 = EYE_BOX[n]
        f2 = to_mm(ph, [(bx0 + bx1) / 2.0, (by0 + by1) / 2.0])
        say("         box centre by eye (%.1f, %.1f) px -> %.1f, %.1f (moves %+.1f, %+.1f mm)" % ((bx0 + bx1) / 2.0, (by0 + by1) / 2.0, f2[0], f2[1], f2[0] - f0[0], f2[1] - f0[1]))
    fig, ax = plt.subplots(figsize=(13, 9))
    vx0, vx1, vy0, vy1 = x0 - 45, x1 + 45, y0 - 40, y1 + 40
    crop = ph.img[vy0:vy1, vx0:vx1]
    lo, hi = np.percentile(crop, [1, 99.5])
    ax.imshow(np.clip((crop - lo) / (hi - lo), 0, 1), cmap="gray", interpolation="nearest", extent=(vx0 - .5, vx1 - .5, vy1 - .5, vy0 - .5))
    gx, gy = np.arange(ph.img.shape[1]), np.arange(ph.img.shape[0])
    ax.contour(gx, gy, m0.astype(float), levels=[0.5], colors=["#ff2bd6"], linewidths=1.2)
    ax.scatter([c0[0]], [c0[1]], s=160, marker="+", c="#ff2bd6", linewidths=2, label="frozen detector")
    if c1 is not None:
        ax.contour(gx, gy, m1.astype(float), levels=[0.5], colors=["#39ff14"], linewidths=1.2)
        ax.scatter([c1[0]], [c1[1]], s=160, marker="x", c="#39ff14", linewidths=2, label="flattened")
    ax.plot([x0, x1, x1, x0, x0], [y0, y0, y1, y1, y0], color="#ffe600", linewidth=0.6, linestyle=":", label="by-eye box given to the frozen detector")
    if n in EYE_BOX:
        ax.scatter([(bx0 + bx1) / 2.0], [(by0 + by1) / 2.0], s=120, marker="D", facecolors="none", edgecolors="#00e5ff", linewidths=1.5, label="box centre by eye")
    ax.set_xlim(vx0 - .5, vx1 - .5)
    ax.set_ylim(vy1 - .5, vy0 - .5)
    ax.legend(fontsize=9, loc="upper left")
    ax.set_title("shot %d: the mark outlined two ways" % n, fontsize=11)
    fig.tight_layout()
    fig.savefig(AUDIT / "figures" / ("photo-mark-variants-shot%d.png" % n), dpi=70)
    plt.close(fig)
(AUDIT / "results" / "PHOTO-MARK-VARIANTS.txt").write_text("\n".join(OUT) + "\n")
