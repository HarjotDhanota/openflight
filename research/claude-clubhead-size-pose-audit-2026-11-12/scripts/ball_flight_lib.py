"""Sub-pixel measurement of a golf ball in one frame: a circle through the half-level edge along radial rays.

The ball is shaded (bright top, dark underside), so the half-level edge sits a little inside the true edge and the
fitted centre sits a little high. Always measure the RESTING ball with the same call and the same arc, and use
differences only."""
import math

import cv2
import numpy as np
from scipy.optimize import least_squares


def measure(image, guess, radius_guess, arc=(-10.0, 190.0), step_min=15.0):
    """arc in degrees: 0 = image right, 90 = up. Returns {centre, radius, points, rms} or None."""
    img = cv2.GaussianBlur(image.astype(np.float32), (0, 0), 0.6)
    centre, radius = np.array(guess, dtype=float), float(radius_guess)
    h, w = img.shape
    sol, pts = None, None

    def at(p):
        x, y = int(round(p[0])), int(round(p[1]))
        return float(img[y, x]) if 0 <= x < w and 0 <= y < h else float("nan")

    for _ in range(5):
        inner = [at(centre + 0.35 * radius * np.array([math.cos(a), -math.sin(a)])) for a in np.linspace(0.3, math.pi - 0.3, 9)]
        inside = float(np.nanmedian(inner))
        pts = []
        for ang in np.arange(arc[0], arc[1] + 0.1, 5.0):
            d = np.array([math.cos(math.radians(ang)), -math.sin(math.radians(ang))])
            rs = np.arange(0.45 * radius, 1.9 * radius, 0.25)
            prof = np.array([at(centre + r * d) for r in rs])
            if np.isnan(prof).any():
                continue
            outside = float(np.min(prof[rs > 1.25 * radius]))
            if inside - outside < step_min:
                continue
            half = 0.5 * (inside + outside)
            below = np.nonzero(prof < half)[0]
            if len(below) == 0 or below[0] == 0:
                continue
            i = below[0]
            r_edge = rs[i - 1] + (prof[i - 1] - half) / (prof[i - 1] - prof[i]) * (rs[i] - rs[i - 1])
            if 0.6 * radius < r_edge < 1.5 * radius:
                pts.append(centre + r_edge * d)
        pts = np.asarray(pts)
        if len(pts) < 6:
            return None

        def res(p, pts=pts):
            return np.hypot(pts[:, 0] - p[0], pts[:, 1] - p[1]) - p[2]

        sol = least_squares(res, [centre[0], centre[1], radius], loss="soft_l1", f_scale=0.5)
        centre, radius = sol.x[:2], float(sol.x[2])
        # guard added 13 Nov (PROTOCOL addendum 10): a fit that runs away (seen when the club covers the ball) made the
        # next pass sample millions of points and hang. A circle 3x the guess, or 3 radii from it, is not the ball.
        if not (0.33 * radius_guess < radius < 3.0 * radius_guess) or np.linalg.norm(centre - np.array(guess, dtype=float)) > 3.0 * radius_guess:
            return None
    r = np.hypot(pts[:, 0] - centre[0], pts[:, 1] - centre[1]) - radius
    return {"centre": centre, "radius": radius, "points": pts, "rms": float(np.sqrt(np.mean(r ** 2)))}
