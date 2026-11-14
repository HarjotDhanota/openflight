"""PROTOCOL E2: model-free observables from one prepared frame. No model enters this file."""
import numpy as np
import cv2


def reference(frames):
    stack = frames[:31].astype(np.float32)
    return np.median(stack, axis=0), np.std(stack, axis=0)


def changed_mask(frame, ref, sigma):
    diff = frame.astype(np.float32) - ref
    return diff, np.abs(diff) > np.maximum(4.0 * sigma, 8.0)


def blob(mask, seed_xy):
    """Connected changed component nearest the seed (8-connectivity)."""
    count, labels = cv2.connectedComponents(mask.astype(np.uint8), connectivity=8)
    x, y = int(round(seed_xy[0])), int(round(seed_xy[1]))
    h, w = mask.shape
    x, y = min(max(x, 0), w - 1), min(max(y, 0), h - 1)
    if labels[y, x] == 0:
        ys, xs = np.nonzero(labels)
        if len(xs) == 0:
            return np.zeros_like(mask)
        nearest = np.argmin((xs - x) ** 2 + (ys - y) ** 2)
        x, y = xs[nearest], ys[nearest]
    return labels == labels[y, x]


def shaft_ridge(diff, rows, guess_line, half_width=6, level=25.0):
    """Per-row centroid of the bright run nearest the guessed shaft x. guess_line(y) -> x."""
    points = []
    for y in rows:
        if y < 0 or y >= diff.shape[0]:
            continue
        gx = guess_line(y)
        lo, hi = int(max(gx - half_width, 0)), int(min(gx + half_width + 1, diff.shape[1]))
        seg = diff[y, lo:hi]
        w = np.clip(seg - level, 0.0, None)
        if w.sum() > 0:
            points.append((lo + float((w * np.arange(len(seg))).sum() / w.sum()), float(y)))
    return np.asarray(points, dtype=float).reshape(-1, 2)


def fit_line_x_of_y(points):
    """x = a + b*y least squares with one 2.5-sigma rejection pass. Returns a, b, rms, kept count."""
    if len(points) < 4:
        return None
    y, x = points[:, 1], points[:, 0]
    keep = np.ones(len(x), dtype=bool)
    for _ in range(2):
        b, a = np.polyfit(y[keep], x[keep], 1)
        res = x - (a + b * y)
        rms = float(np.sqrt(np.mean(res[keep] ** 2)))
        keep = np.abs(res) <= max(2.5 * rms, 0.5)
    return float(a), float(b), rms, int(keep.sum())


def extremes(component):
    """toe(y): right-most pixel per row; sole(x): lowest per column; top(x): highest per column."""
    ys, xs = np.nonzero(component)
    toe = {int(y): int(xs[ys == y].max()) for y in np.unique(ys)}
    sole = {int(x): int(ys[xs == x].max()) for x in np.unique(xs)}
    top = {int(x): int(ys[xs == x].min()) for x in np.unique(xs)}
    return toe, sole, top
