"""Which degrees of freedom does the silhouette actually constrain?

"Why can't the fit get orientation right" is answerable by measurement rather
than argument. Take each frame's own best pose, then walk ONE parameter at a
time and watch what the objective does. A degree of freedom the outline
constrains shows a sharp peak; one it does not shows a flat plateau, and on a
plateau the fitted value is whatever noise picks.

In the fitter's convention (`fit_real.triad`):
    yaw   - about world up      = FACE ANGLE (open/closed)
    pitch - about world right   = DYNAMIC LOFT
    roll  - about the face normal = LIE / toe-up
    plus range, the depth along the camera ray.

Face angle and loft are the two quantities impact location needs.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "research"))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from silhouette_poc.generator.mesh_truth import TriangleMesh  # noqa: E402
from silhouette_poc.replay.fit_real import (  # noqa: E402
    CAMERA_CENTER_WORLD,
    _ray_world,
    iou,
    measured_camera,
    render_mask_6dof,
)
from test_meshfit_depth_ab import (  # noqa: E402
    EXCLUDE,
    SESSION,
    club_masks,
    fit_frame_pinned,
)

SWEEPS = {
    "yaw (FACE ANGLE)": ("yaw", np.arange(-40.0, 40.1, 2.5)),
    "pitch (DYNAMIC LOFT)": ("pitch", np.arange(-40.0, 40.1, 2.5)),
    "roll (lie / toe-up)": ("roll", np.arange(-40.0, 40.1, 2.5)),
    "range (depth, mm)": ("range", np.arange(-400.0, 400.1, 25.0)),
}
# How far can each parameter move before the objective drops by this much of
# its peak? A wide half-width means the outline does not pin that DoF.
DROP = 0.05


def main():
    import csv  # noqa: PLC0415

    d = np.load(ROOT / "research/silhouette_poc/meshes/assets/poc_7iron.npz")
    mesh = TriangleMesh(d["vertices_local_mm"], d["faces"], "poc_7iron", "x" * 64)
    with open(SESSION / "shots.csv", newline="", encoding="utf-8") as h:
        rows = [r for r in csv.DictReader(h) if int(r["shot_number"]) not in EXCLUDE]

    curves: dict[str, list] = {k: [] for k in SWEEPS}
    widths: dict[str, list] = {k: [] for k in SWEEPS}
    n_frames = 0
    for row in rows:
        frames = np.load(SESSION / row["archive_frames_npz"])["frames"][:, :, ::-1]
        cam = measured_camera(frames.shape[2], frames.shape[1])
        for k, m in sorted(club_masks(frames).items()):
            fit = fit_frame_pinned(mesh, m, cam)
            if not fit.get("ok"):
                continue
            obs = m.astype(bool)
            ys, xs = np.nonzero(obs)
            ray = _ray_world(np.array([xs.mean(), ys.mean()], dtype=float), cam)
            base = dict(
                yaw=fit["yaw_deg"],
                pitch=fit["pitch_deg"],
                roll=fit["roll_deg"],
                range=fit["range_mm"],
            )
            n_frames += 1
            for label, (param, offsets) in SWEEPS.items():
                vals = []
                for off in offsets:
                    p = dict(base)
                    p[param] = base[param] + off
                    r = render_mask_6dof(
                        mesh,
                        CAMERA_CENTER_WORLD + ray * p["range"],
                        p["yaw"],
                        p["pitch"],
                        p["roll"],
                        cam,
                    )
                    vals.append(0.0 if r is None else iou(r, obs))
                vals = np.asarray(vals)
                peak = vals.max()
                curves[label].append(vals / max(peak, 1e-9))
                ok = np.nonzero(vals >= peak * (1.0 - DROP))[0]
                widths[label].append(float(offsets[ok[-1]] - offsets[ok[0]]))
        print(
            f"  shot {int(row['shot_number']):>3} done ({n_frames} frames)", flush=True
        )

    print(
        f"\n=== objective shape around each frame's own optimum, {n_frames} frames ==="
    )
    print(
        f"{'degree of freedom':>24} {'width within 5% of peak':>26} "
        f"{'IoU at +-10 units':>18}"
    )
    out = {}
    for label, (param, offsets) in SWEEPS.items():
        c = np.asarray(curves[label])
        w = np.asarray(widths[label])
        mean_curve = c.mean(0)
        unit = 10.0 if param != "range" else 100.0
        idx = int(np.argmin(np.abs(offsets - unit)))
        idx0 = int(np.argmin(np.abs(offsets + unit)))
        keep = 0.5 * (mean_curve[idx] + mean_curve[idx0])
        suffix = "deg" if param != "range" else "mm"
        print(f"{label:>24} {np.median(w):>18.1f} {suffix:<7} {100 * keep:>16.1f}%")
        out[label] = dict(
            offsets=offsets.tolist(),
            mean=mean_curve.tolist(),
            median_width=float(np.median(w)),
            unit=suffix,
        )
    (Path(__file__).parent / "dof_sensitivity.json").write_text(
        json.dumps(out), encoding="utf-8"
    )
    print("\nA DoF the outline constrains has a NARROW width and loses IoU quickly.")
    print("A wide width means the objective is flat: the fitted value is noise.")
    print(f"\nwrote {Path(__file__).parent / 'dof_sensitivity.json'}")


if __name__ == "__main__":
    main()
