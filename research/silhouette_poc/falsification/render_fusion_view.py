"""Render what the fusion system currently produces, over the real frames.

Everything drawn here is a model's own output, never hand-drawn geometry:

  * the CLUB outline is the boundary of `render(...)` -- the mesh projected
    through the camera model at the fitted pose, unpadded;
  * the OBSERVED outline is the boundary of the mask the fit actually consumed;
  * the BALL circle is the tracker's own fitted circle at its own fitted radius.

If an outline looks wrong, the fit is wrong. Nothing is nudged to look better.

Frames outside the fitted window still show a projection, because the fit is a
single rigid rotation and extrapolating it is the model's own prediction -- but
those frames are labelled EXTRAPOLATED so nobody reads them as evidence.

Two shots are rendered rather than one. Showing only the best result would
misrepresent the state of the system: shot 18 is currently the most credible
delivery and shot 2 is among the worst, and the difference between them is the
honest status.
"""

from __future__ import annotations

import argparse
import base64
import csv
import json
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "research"))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from silhouette_poc.generator.mesh_truth import TriangleMesh  # noqa: E402
from silhouette_poc.replay.club_angles import delivered_angles, in_envelope  # noqa: E402
from silhouette_poc.replay.fit_real import CAMERA_CENTER_WORLD, triad  # noqa: E402
from silhouette_poc.replay.pose_scores import mask_edge  # noqa: E402
from silhouette_poc.replay.rigid_motion import rotation_from_omega_deg_s  # noqa: E402

from flight_track import teed_ball, track_flight  # noqa: E402
from test_fused_refit import build_context, omega_from_phase  # noqa: E402
from test_meshfit_depth_ab import MESH, SESSION, club_masks  # noqa: E402
from test_rigid_rotation_prior import render  # noqa: E402

# 2x is legible at 640x400 and keeps the page inside the 16 MB artifact
# limit with both shots present. Showing only one shot would be smaller
# and less honest.
SCALE = 2
CLUB_MODEL_BGR = (60, 140, 255)  # orange -- the mesh's own projection
CLUB_OBSERVED_BGR = (235, 200, 60)  # cyan   -- the mask the fit consumed
BALL_BGR = (110, 235, 140)  # green  -- the ball tracker's own circle


def draw_outline(canvas, mask, colour):
    """Paint a mask's own boundary. No dilation, no smoothing, no padding."""
    edge = mask_edge(np.asarray(mask).astype(np.uint8))
    big = cv2.resize(
        edge.astype(np.uint8),
        (canvas.shape[1], canvas.shape[0]),
        interpolation=cv2.INTER_NEAREST,
    ).astype(bool)
    canvas[big] = colour
    return canvas


def label(canvas, text, y, colour=(235, 235, 235), scale=0.42):
    cv2.putText(
        canvas, text, (7, y), cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), 3, cv2.LINE_AA
    )
    cv2.putText(
        canvas, text, (7, y), cv2.FONT_HERSHEY_SIMPLEX, scale, colour, 1, cv2.LINE_AA
    )


def render_shot(mesh, shot, row, fit_record, span_before=6, span_after=6):
    """One frame strip for a shot, plus the facts to caption it with."""
    ctx, camera, why = build_context(row, club_masks, fit_record)
    if ctx is None:
        return None, why

    archive = np.load(SESSION / row["archive_frames_npz"])
    frames = archive["frames"][:, :, ::-1]
    sensor_s = archive["sensor_timestamp_ns"].astype(float) * 1e-9
    track = track_flight(frames)
    tee = teed_ball(frames)
    ball_at = {}
    if track is not None:
        ball_at = {
            int(f): (np.asarray(uv, float), float(r))
            for f, uv, r in zip(track.frames, track.uv, track.radius_px)
        }

    yaw, pitch, roll, phase = fit_record["params"]
    normal, width, height = triad(yaw, pitch, roll)
    basis0 = np.column_stack((normal, width, height))
    omega = omega_from_phase(ctx["axis_basis"], phase, ctx["omega_mag"])
    fitted = set(ctx["frame_ids"])
    all_masks = club_masks(frames)

    impact = ctx["impact_frame"]
    first = max(int(impact) - span_before, 0)
    last = min(int(impact) + span_after, len(frames) - 1)
    t0 = sensor_s[ctx["frame_ids"][0]]

    panels = []
    for index in range(first, last + 1):
        base = cv2.cvtColor(frames[index], cv2.COLOR_GRAY2BGR)
        canvas = cv2.resize(
            base,
            (base.shape[1] * SCALE, base.shape[0] * SCALE),
            interpolation=cv2.INTER_NEAREST,
        )

        elapsed = sensor_s[index] - t0
        range_mm = ctx["ranges_mm"][0] + ctx["range_rate_ms"] * 1000.0 * (
            elapsed - ctx["elapsed_s"][0]
        )
        # Only project while the club is still approaching. After contact the
        # rigid-rotation model no longer describes anything real.
        if index <= impact + 0.5:
            # Propagate the centre with the FUSED velocity. An earlier version
            # reused the nearest fitted frame's ray, so on unfitted frames the
            # projection sat at a fixed screen position and only changed size --
            # it did not follow the club, which is exactly what it looked like.
            centre = (
                CAMERA_CENTER_WORLD
                + ctx["rays"][0] * ctx["ranges_mm"][0]
                + ctx["velocity_mm_s"] * (elapsed - ctx["elapsed_s"][0])
            )
            model = render(
                mesh, rotation_from_omega_deg_s(omega, elapsed) @ basis0, centre, camera
            )
            if model is not None and model.any():
                draw_outline(canvas, model, CLUB_MODEL_BGR)
        if index in all_masks:
            draw_outline(canvas, all_masks[index], CLUB_OBSERVED_BGR)
        # The ball tracker only follows the departing ball, so the teed ball
        # needs its own detection or the pre-impact frames show no ball at all.
        drawn = ball_at.get(index)
        if drawn is None and tee is not None and index <= impact:
            drawn = (np.asarray(tee[0], float), float(tee[1]))
        if drawn is not None:
            uv, radius = drawn
            cv2.circle(
                canvas,
                (int(round(uv[0] * SCALE)), int(round(uv[1] * SCALE))),
                max(int(round(radius * SCALE)), 2),
                BALL_BGR,
                1,
                cv2.LINE_AA,
            )

        # A fitted frame after impact is a defect, not a credential: the
        # rigid-rotation model does not describe a club that has already struck
        # the ball. Labelled so it cannot be read as supporting evidence.
        if index in fitted:
            state = "FITTED (post-impact)" if index > impact else "FITTED"
        else:
            state = "post-impact" if index > impact else "extrapolated"
        label(canvas, f"f{index}", 18)
        label(canvas, f"{range_mm:.0f} mm", 34, (170, 200, 220), 0.38)
        colour = {"FITTED": (110, 235, 140), "FITTED (post-impact)": (90, 90, 235)}.get(
            state, (150, 150, 150)
        )
        label(canvas, state, canvas.shape[0] - 10, colour, 0.36)
        panels.append(canvas)

    angles = delivered_angles(basis0)
    facts = {
        "shot": shot,
        "pre_impact_masks": sorted(k for k in all_masks if k < impact),
        "post_impact_fitted": sorted(f for f in fitted if f > impact),
        "club": row["club"],
        "impact_frame": impact,
        "frames": [first, last],
        "fitted_frames": sorted(fitted),
        "range_span_mm": [float(ctx["ranges_mm"][0]), float(ctx["ranges_mm"][-1])],
        "camera_speed_ratio": ctx["camera_speed_ratio"],
        "delivered": angles,
        "in_envelope": bool(in_envelope(angles)),
        "mean_iou": fit_record["mean_iou"],
        "mean_chamfer_px": fit_record["mean_chamfer_px"],
    }
    return (panels, facts), None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--shot", type=int, action="append", help="defaults to a best and a worst case"
    )
    parser.add_argument(
        "--out", type=Path, default=Path(__file__).with_name("renders") / "fusion_view"
    )
    parser.add_argument(
        "--fits", type=Path, default=Path(__file__).with_name("fused_refit.json")
    )
    args = parser.parse_args()

    fits = {
        int(r["shot"]): r for r in json.loads(args.fits.read_text(encoding="utf-8"))
    }
    with open(SESSION / "shots.csv", newline="", encoding="utf-8") as handle:
        rows = {int(r["shot_number"]): r for r in csv.DictReader(handle)}
    wanted = args.shot or [18, 2]

    mesh_data = np.load(MESH)
    mesh = TriangleMesh(
        mesh_data["vertices_local_mm"], mesh_data["faces"], "poc_7iron", "x" * 64
    )
    args.out.mkdir(parents=True, exist_ok=True)
    manifest = []
    for shot in wanted:
        if shot not in fits:
            print(f"shot {shot}: no fit on record, skipped")
            continue
        result, why = render_shot(mesh, shot, rows[shot], fits[shot])
        if result is None:
            print(f"shot {shot}: {why}")
            continue
        panels, facts = result
        encoded = []
        for offset, panel in enumerate(panels):
            ok, buffer = cv2.imencode(".png", panel, [cv2.IMWRITE_PNG_COMPRESSION, 9])
            assert ok, "png encode failed"
            encoded.append(base64.b64encode(buffer.tobytes()).decode("ascii"))
            cv2.imwrite(
                str(args.out / f"shot{shot:03d}_f{facts['frames'][0] + offset}.png"),
                panel,
            )
        facts["png_base64"] = encoded
        manifest.append(facts)
        angles = facts["delivered"]
        print(
            f"shot {shot:>3} {facts['club']:>7}: {len(panels)} frames, "
            f"loft {angles['dynamic_loft_deg']:+.1f} face {angles['face_angle_deg']:+.1f} "
            f"lie {angles['lie_deg']:.1f}  "
            f"{'in envelope' if facts['in_envelope'] else 'OUTSIDE envelope'}"
        )

    out_json = args.out / "manifest.json"
    out_json.write_text(json.dumps(manifest), encoding="utf-8")
    total = sum(len(m["png_base64"]) for m in manifest)
    print(f"\nwrote {total} frames across {len(manifest)} shots to {args.out}")


if __name__ == "__main__":
    main()
