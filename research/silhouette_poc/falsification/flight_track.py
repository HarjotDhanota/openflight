"""Post-impact ball pixel track from un-mirrored real frames.

Fail closed: a shot with no coherent receding track returns None rather
than a best-effort guess.
"""

from __future__ import annotations

import itertools
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "research") not in sys.path:
    sys.path.insert(0, str(ROOT / "research"))
from silhouette_poc.fusion.ball_detect import candidates  # noqa: E402


@dataclass
class FlightTrack:
    frames: np.ndarray
    uv: np.ndarray
    radius_px: np.ndarray
    tee_uv: np.ndarray
    tee_radius_px: float
    impact_frame: float
    rms_px: float


def teed_ball(F: np.ndarray) -> tuple[np.ndarray, float] | None:
    """Cluster present pre-impact and absent once the ball has gone."""
    early, late = range(18, 58, 2), range(88, len(F))
    hits, late_pts = [], []
    for i in early:
        for c in candidates(F[i], expected_radius_px=6.5, radius_tolerance=0.30):
            hits.append((np.asarray(c.center, float), c.radius_px))
    for i in late:
        for c in candidates(F[i], expected_radius_px=6.5, radius_tolerance=0.30):
            late_pts.append(np.asarray(c.center, float))
    clusters: list[list] = []
    for pt, r in hits:
        for cl in clusters:
            if np.linalg.norm(pt - cl[0][0]) < 4.0:
                cl.append((pt, r))
                break
        else:
            clusters.append([(pt, r)])
    best = None
    for cl in clusters:
        if len(cl) < 0.6 * len(list(early)):
            continue
        centre = np.median([p for p, _ in cl], axis=0)
        if any(np.linalg.norm(q - centre) < 4.0 for q in late_pts):
            continue
        if best is None or len(cl) > len(best[1]):
            best = (centre, cl)
    if best is None:
        return None
    return best[0], float(np.median([r for _, r in best[1]]))


def static_background(F: np.ndarray, *, persist_frac: float = 0.4) -> list[np.ndarray]:
    """Detection sites occupied in a large fraction of ALL frames.

    A ball in flight occupies any given site for one or two frames. Anything
    that persists is scenery, and a quadratic fits scenery perfectly -- which
    is how a static blob wins a RANSAC vote against the real ball.
    """
    seen: list[list[np.ndarray]] = []
    probe = range(0, len(F), 2)
    for i in probe:
        for c in candidates(F[i], expected_radius_px=5.0, radius_tolerance=0.75):
            pt = np.asarray(c.center, float)
            for grp in seen:
                if np.linalg.norm(pt - grp[0]) < 3.0:
                    grp.append(pt)
                    break
            else:
                seen.append([pt])
    n = len(list(probe))
    return [np.median(g, axis=0) for g in seen if len(g) >= persist_frac * n]


def track_flight(
    F: np.ndarray,
    *,
    min_points: int = 8,
    inlier_px: float = 1.5,
    min_rise_px: float = 40.0,
) -> FlightTrack | None:
    """RANSAC a quadratic image track through the receding ball."""
    tee = teed_ball(F)
    if tee is None:
        return None
    tee_uv, tee_r = tee
    background = static_background(F)

    pts: list[tuple[int, np.ndarray, float]] = []
    for i in range(60, len(F)):
        for c in candidates(F[i], expected_radius_px=5.0, radius_tolerance=0.75):
            pt = np.asarray(c.center, float)
            if any(np.linalg.norm(pt - b) < 3.5 for b in background):
                continue  # scenery, not the ball
            if (
                pt[1] < tee_uv[1] - 3.0
                and abs(pt[0] - tee_uv[0]) < 45.0
                and 2.0 <= c.radius_px <= tee_r + 1.5
            ):
                pts.append((i, pt, c.radius_px))
    if len(pts) < min_points:
        return None

    f_all = np.array([p[0] for p in pts], float)
    u_all = np.array([p[1][0] for p in pts])
    v_all = np.array([p[1][1] for p in pts])

    best: tuple[int, np.ndarray] | None = None
    for trio in itertools.combinations(range(len(pts)), 3):
        fs = f_all[list(trio)]
        if len(set(fs)) < 3 or np.ptp(fs) > 30 or np.ptp(fs) < 4:
            continue
        try:
            cv = np.polyfit(fs, v_all[list(trio)], 2)
        except np.linalg.LinAlgError:
            continue
        resid = np.abs(np.polyval(cv, f_all) - v_all)
        keep = resid < inlier_px
        # one point per frame: nearest to the model
        chosen: dict[int, int] = {}
        for idx in np.nonzero(keep)[0]:
            fi = int(f_all[idx])
            if fi not in chosen or resid[idx] < resid[chosen[fi]]:
                chosen[fi] = idx
        idxs = np.array(sorted(chosen.values(), key=lambda i: f_all[i]))
        if len(idxs) < min_points:
            continue
        vv = v_all[idxs]
        if vv[0] - vv[-1] < min_rise_px:  # the ball must actually climb
            continue
        if np.any(np.diff(vv) > 0.75):  # and climb monotonically
            continue
        if best is None or len(idxs) > len(best[1]):
            best = (len(idxs), idxs)
    if best is None or len(best[1]) < min_points:
        return None
    idxs = best[1]

    # refit on inliers, then a final consistency pass in u as well
    frames = f_all[idxs]
    cv = np.polyfit(frames, v_all[idxs], 2)
    cu = np.polyfit(frames, u_all[idxs], 2)
    res = np.hypot(
        np.polyval(cu, frames) - u_all[idxs], np.polyval(cv, frames) - v_all[idxs]
    )
    keep = res < 3.0
    idxs = idxs[keep]
    if len(idxs) < min_points:
        return None
    # Extend outward from the RANSAC core using a LOCAL model. The early
    # frames sit closest to launch and matter most; a global quadratic
    # extrapolated ten frames does not fit them, a local one does.
    kept = set(int(i) for i in idxs)
    for _ in range(2):
        cur = np.array(sorted(kept, key=lambda i: f_all[i]))
        for direction in (-1, +1):
            edge = cur[:5] if direction < 0 else cur[-5:]
            if len(edge) < 3:
                continue
            ce_v = np.polyfit(f_all[edge], v_all[edge], 2)
            ce_u = np.polyfit(f_all[edge], u_all[edge], 1)
            anchor = f_all[edge[0] if direction < 0 else edge[-1]]
            for step in range(1, 7):
                fi = anchor + direction * step
                cand = [j for j in range(len(pts)) if f_all[j] == fi and j not in kept]
                if not cand:
                    continue
                pred = np.array([np.polyval(ce_u, fi), np.polyval(ce_v, fi)])
                j = min(
                    cand, key=lambda k: np.hypot(u_all[k] - pred[0], v_all[k] - pred[1])
                )
                if np.hypot(u_all[j] - pred[0], v_all[j] - pred[1]) < 2.5:
                    kept.add(j)
                else:
                    break
        idxs = np.array(sorted(kept, key=lambda i: f_all[i]))

    frames = f_all[idxs]
    cv = np.polyfit(frames, v_all[idxs], 2)
    rms = float(np.sqrt(np.mean((np.polyval(cv, frames) - v_all[idxs]) ** 2)))

    # departure frame: where the fitted image track crosses the tee row
    roots = np.roots([cv[0], cv[1], cv[2] - tee_uv[1]])
    real = [r.real for r in roots if abs(r.imag) < 1e-6]
    inside = [r for r in real if frames[0] - 12 <= r <= frames[0] + 2]
    impact_frame = float(max(inside)) if inside else float("nan")

    return FlightTrack(
        frames,
        np.column_stack([u_all[idxs], v_all[idxs]]),
        np.array([pts[i][2] for i in idxs]),
        tee_uv,
        tee_r,
        impact_frame,
        rms,
    )
