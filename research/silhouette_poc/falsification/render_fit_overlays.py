"""Draw what the mesh fit actually produces, under each depth treatment.

Section 11f reported the three-arm result as numbers. Numbers are not a picture
of a fit, and the project's own rule is that an overlay draws the MODEL'S OWN
OUTPUT: the outline here is the boundary of `render_mask_6dof` at the fitted
pose, rasterised by the same code the objective scores. Nothing is padded,
smoothed or hand-drawn. If an outline looks wrong, the fit is wrong.

Two figures:

  strip_<shot>.png   one row per arm, one column per frame. Cyan is the
  observed silhouette the fit consumed; orange is the fitted
  mesh. Reading across a row shows pose coherence
  reading
  down a column shows what the depth treatment changed.

  scale_<shot>.png   the observed silhouette against the mesh rendered at the; tape-measured 1581 mm in the fit's own best orientation.
                     This is the scale mismatch that arms A and B were escaping
                     by pulling the range in.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "research"))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).parent))

import cv2  # noqa: E402
from silhouette_poc.generator.mesh_truth import TriangleMesh  # noqa: E402
from silhouette_poc.replay.fit_real import (  # noqa: E402
    CAMERA_CENTER_WORLD,
    _ray_world,
    fit_frame_6dof,
    measured_camera,
    render_mask_6dof,
)
from test_meshfit_depth_ab import (  # noqa: E402
    NEW_GRID,
    OLD_GRID,
    SESSION,
    club_masks,
    fit_frame_pinned,
)

OBS = (240, 200, 60)  # cyan-ish, BGR: the observation
FIT = (60, 120, 245)  # orange, BGR: the model's own output
PAD, ZOOM = 26, 6


def outline(mask: np.ndarray) -> np.ndarray:
    m = (np.asarray(mask) > 0).astype(np.uint8)
    er = cv2.erode(m, np.ones((3, 3), np.uint8), iterations=1)
    return (m - er).astype(bool)


def panel(frame, obs_mask, fit_mask, box):
    """One zoomed crop with both outlines drawn on the real pixels."""
    x0, y0, x1, y1 = box
    base = cv2.cvtColor(frame[y0:y1, x0:x1], cv2.COLOR_GRAY2BGR)
    big = cv2.resize(base, None, fx=ZOOM, fy=ZOOM, interpolation=cv2.INTER_NEAREST)
    big = (big * 0.62).astype(np.uint8)  # dim so the outlines read
    for mask, colour in ((obs_mask, OBS), (fit_mask, FIT)):
        if mask is None:
            continue
        o = outline(mask)[y0:y1, x0:x1]
        o = cv2.resize(
            o.astype(np.uint8), None, fx=ZOOM, fy=ZOOM, interpolation=cv2.INTER_NEAREST
        ).astype(bool)
        big[o] = colour
    return big


def label(img, text, colour=(235, 235, 235)):
    cv2.putText(
        img, text, (6, 17), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 0, 0), 3, cv2.LINE_AA
    )
    cv2.putText(
        img, text, (6, 17), cv2.FONT_HERSHEY_SIMPLEX, 0.42, colour, 1, cv2.LINE_AA
    )
    return img


def rendered(mesh, cam, mask, fit):
    ys, xs = np.nonzero(mask.astype(bool))
    ray = _ray_world(np.array([xs.mean(), ys.mean()], dtype=float), cam)
    return render_mask_6dof(
        mesh,
        CAMERA_CENTER_WORLD + ray * fit["range_mm"],
        fit["yaw_deg"],
        fit["pitch_deg"],
        fit["roll_deg"],
        cam,
    )


def main(shot_dir: str, n_frames: int = 6, out_dir: Path | None = None):
    out_dir = out_dir or Path(".")
    out_dir.mkdir(parents=True, exist_ok=True)
    frames = np.load(SESSION / "shots" / shot_dir / "frames.npz")["frames"][:, :, ::-1]
    cam = measured_camera(frames.shape[2], frames.shape[1])
    d = np.load(ROOT / "research/silhouette_poc/meshes/assets/poc_7iron.npz")
    mesh = TriangleMesh(d["vertices_local_mm"], d["faces"], "poc_7iron", "x" * 64)

    masks = club_masks(frames)
    keys = sorted(masks)[-n_frames:]
    print(f"{shot_dir}: frames {keys}")

    # a common crop box so the strip is spatially comparable across arms
    ys, xs = np.nonzero(np.any([masks[k].astype(bool) for k in keys], axis=0))
    box = (
        max(int(xs.min()) - PAD, 0),
        max(int(ys.min()) - PAD, 0),
        min(int(xs.max()) + PAD, frames.shape[2]),
        min(int(ys.max()) + PAD, frames.shape[1]),
    )

    arms = {
        "A  grid 1300-1550 (shipped)": lambda m: fit_frame_6dof(
            mesh, m, cam, range_grid_mm=OLD_GRID
        ),
        "B  grid 1456-1706 (corrected)": lambda m: fit_frame_6dof(
            mesh, m, cam, range_grid_mm=NEW_GRID
        ),
        "C  range pinned at 1581 mm": lambda m: fit_frame_pinned(mesh, m, cam),
    }

    rows, pinned_fit = [], {}
    for name, fitter in arms.items():
        cells = []
        for k in keys:
            fit = fitter(masks[k])
            ren = rendered(mesh, cam, masks[k], fit) if fit.get("ok") else None
            if name.startswith("C"):
                pinned_fit[k] = (fit, ren)
            cell = panel(frames[k], masks[k], ren, box)
            cells.append(
                label(
                    cell,
                    f"f{k}  IoU {fit.get('iou', 0):.2f}  "
                    f"{fit.get('range_mm', 0):.0f}mm",
                )
            )
            print(
                f"  {name[:1]} f{k}: IoU {fit.get('iou', 0):.3f} "
                f"range {fit.get('range_mm', 0):.0f}",
                flush=True,
            )
        row = np.hstack(cells)
        banner = np.zeros((26, row.shape[1], 3), np.uint8)
        rows.append(np.vstack([label(banner, name, (255, 235, 190)), row]))
    strip = np.vstack(rows)
    cv2.imwrite(str(out_dir / f"strip_{shot_dir}.png"), strip)
    print(f"wrote {out_dir / f'strip_{shot_dir}.png'}  {strip.shape}")

    # scale figure: observation vs the model at the KNOWN range
    cells = []
    for k in keys[:4]:
        fit, ren = pinned_fit[k]
        obs_n = int(masks[k].sum())
        ren_n = int(ren.sum()) if ren is not None else 0
        cell = panel(frames[k], masks[k], ren, box)
        cells.append(
            label(
                cell,
                f"f{k}  obs {obs_n}px  mesh {ren_n}px  "
                f"{100 * ren_n / max(obs_n, 1):.0f}%",
            )
        )
    cv2.imwrite(str(out_dir / f"scale_{shot_dir}.png"), np.hstack(cells))
    print(f"wrote {out_dir / f'scale_{shot_dir}.png'}")


if __name__ == "__main__":
    main(
        sys.argv[1] if len(sys.argv) > 1 else "shot_029_9-iron",
        out_dir=ROOT / "research/silhouette_poc/falsification/renders",
    )
