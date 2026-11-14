"""Silhouette tools for the size/pose audit. Model-free observation on one side, the application's generic model
(projected by geom.py, which reproduces the application's own outline to 1e-4 px) on the other.

Nothing here reads a face photo or an impact mark. Analysis coordinates are the prepared ROI's native pixels."""
import math

import cv2
import numpy as np

import geom
import observe

SS = 4  # supersampling of masks: 0.25 px
KERNEL = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 7))


def changed(frame, ref, sigma, floor):
    diff = frame.astype(np.float32) - ref
    return diff, np.abs(diff) > np.maximum(4.0 * sigma, floor)


def head_component(mask):
    """Largest changed component after a small closing (the moving club: shaft, hosel and head)."""
    closed = cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_CLOSE, KERNEL)
    count, labels, stats, _c = cv2.connectedComponentsWithStats(closed, connectivity=8)
    if count < 2:
        return None
    return labels == 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))


def shaft_axis(diff, comp):
    """Ridge line x = a + b*y of the bright shaft above the head, and the head's first wide row."""
    widths = comp.sum(axis=1)
    rows = np.nonzero(widths)[0]
    wide = [int(y) for y in rows if widths[y] > 22]
    if not wide:
        return None
    bottom = max(wide)
    top = bottom
    wide = set(wide)
    while top - 1 in wide or top - 2 in wide:
        top -= 1
    bright = np.clip(diff - 20.0, 0.0, None) * comp
    count = (bright > 0).sum(axis=1)
    pts = []
    for y in rows:
        if y < top - 4 and 3 <= count[y] <= 12 and widths[y] <= 16:
            pts.append((float((bright[y] * np.arange(diff.shape[1])).sum() / bright[y].sum()), float(y)))
    pts = np.asarray(pts, dtype=float).reshape(-1, 2)
    line = observe.fit_line_x_of_y(pts) if len(pts) >= 6 else None
    return {"line": line, "head_top": top, "head_bottom": bottom, "ridge": pts}


def ferrule(frame, line, y_from, y_to):
    """Centre of the dark gap (ferrule) on the shaft axis between the bright shaft and the bright hosel.

    Samples the raw frame along x = a + b*y (max over +-1.5 px across the axis) and returns the middle of the
    first run darker than half the shaft's median brightness that is followed by a bright hosel run."""
    a, b = line[0], line[1]
    ys = np.arange(int(y_from), int(y_to) + 1)
    prof = []
    for y in ys:
        x = a + b * y
        xs = np.clip(np.round(np.array([x - 1.5, x - 0.5, x + 0.5, x + 1.5])).astype(int), 0, frame.shape[1] - 1)
        prof.append(float(frame[y, xs].max()))
    prof = np.asarray(prof)
    if len(prof) < 12:
        return None
    level = 0.5 * float(np.median(prof[: max(6, len(prof) // 2)]))
    dark = prof < level
    start = None
    for i in range(4, len(prof)):
        if dark[i] and start is None:
            start = i
        elif start is not None and not dark[i]:
            return {"y": float(ys[start] + ys[i - 1]) / 2.0, "x": a + b * (ys[start] + ys[i - 1]) / 2.0, "rows": [int(ys[start]), int(ys[i - 1])], "level": level}
    return None


def head_silhouette(comp, x_cut):
    """Column-filled silhouette of the changed component right of x_cut (the head without shaft and hosel).

    Column filling closes the dark-on-dark holes inside the head; the outer boundary stays the observed one."""
    out = np.zeros_like(comp)
    for x in range(int(math.ceil(x_cut)), comp.shape[1]):
        ys = np.nonzero(comp[:, x])[0]
        if len(ys):
            out[ys.min():ys.max() + 1, x] = True
    count, labels, stats, _c = cv2.connectedComponentsWithStats(out.astype(np.uint8), connectivity=8)
    if count < 2:
        return out
    return labels == 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))


def upsample(mask):
    return np.kron(mask.astype(np.uint8), np.ones((SS, SS), dtype=np.uint8)).astype(bool)


def model_mask(scene, ax, origin, shape, sole_width, bounce, size=1.0):
    """Filled silhouette (face outline plus sole strip) at SS x resolution, and the outline pixels."""
    toe, up, normal = ax
    uv = geom.OUTLINE * size
    lead = origin + uv[:, :1] * toe + uv[:, 1:2] * up
    back = -np.array([normal[0], 0.0, normal[2]])
    back /= max(math.hypot(back[0], back[2]), 1e-12)
    b = math.radians(bounce)
    offset = sole_width * size * (math.cos(b) * back + math.sin(b) * np.array([0.0, 1.0, 0.0]))
    lead_px, trail_px = scene.project(lead), scene.project(lead + offset)
    canvas = np.zeros((shape[0] * SS, shape[1] * SS), dtype=np.uint8)

    def poly(points):
        # pixel centres at integers: pixel x covers [x-0.5, x+0.5) -> canvas coordinate (x+0.5)*SS
        pts = np.round((points + 0.5) * SS * 16.0).astype(np.int32)
        cv2.fillPoly(canvas, [pts], 1, lineType=cv2.LINE_8, shift=4)

    poly(lead_px)
    n = len(lead_px)
    for i in range(n):
        j = (i + 1) % n
        poly(np.array([lead_px[i], lead_px[j], trail_px[j], trail_px[i]]))
    return canvas.astype(bool), lead_px


def shift(mask, dx_sub, dy_sub):
    """mask moved by integer supersampled steps (no wrap)."""
    out = np.zeros_like(mask)
    h, w = mask.shape
    xs0, xs1 = max(dx_sub, 0), min(w + dx_sub, w)
    ys0, ys1 = max(dy_sub, 0), min(h + dy_sub, h)
    if xs1 > xs0 and ys1 > ys0:
        out[ys0:ys1, xs0:xs1] = mask[ys0 - dy_sub:ys1 - dy_sub, xs0 - dx_sub:xs1 - dx_sub]
    return out


def register(model, obs_up, window_up, reach_px=30):
    """Best integer-quarter-pixel shift of the model mask onto the observed mask inside window; cost = XOR area (px^2)."""
    best = None
    obs_w = obs_up & window_up
    for step, centre, reach in ((SS, (0, 0), reach_px * SS), (1, None, SS)):
        cx, cy = centre if centre is not None else best[1]
        for dy in range(cy - reach, cy + reach + 1, step):
            for dx in range(cx - reach, cx + reach + 1, step):
                cost = int(np.count_nonzero((shift(model, dx, dy) & window_up) ^ obs_w))
                if best is None or cost < best[0]:
                    best = (cost, (dx, dy))
    return best[0] / float(SS * SS), best[1][0] / float(SS), best[1][1] / float(SS)


def score(model, obs_up, window_up):
    """(XOR, overhang = model not on the observed head, uncovered = observed head the model misses, IoU), px^2."""
    m, o = model & window_up, obs_up & window_up
    k = float(SS * SS)
    inter = np.count_nonzero(m & o)
    union = np.count_nonzero(m | o)
    return {"xor": np.count_nonzero(m ^ o) / k, "overhang": np.count_nonzero(m & ~o) / k, "uncovered": np.count_nonzero(o & ~m) / k,
            "model": np.count_nonzero(m) / k, "observed": np.count_nonzero(o) / k, "iou": inter / union if union else 0.0}
