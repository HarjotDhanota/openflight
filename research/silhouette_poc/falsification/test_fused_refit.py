"""Re-solve clubhead orientation with the radar carrying range and the rotation axis.

Two defects in the shipped fit are corrected together, because correcting either
alone is neutral:

  RANGE. Every previous fit renders the clubhead at a constant 1581 mm, the
  camera-to-ball distance. The radar shows the club traversing 529 mm through
  that value during the fitted frames, and constant range is rejected at
  p ~ 0.04 on mask area alone. Here range comes from the radar's own range rate,
  anchored at the taped ball position at impact. It costs NO free parameter.

  ROTATION AXIS. The shipped fit leaves the angular-velocity axis free as two
  parameters, which is two parameters spent on something the sensors already
  determine. For a rigid body v = omega x r, so omega is PERPENDICULAR to the
  velocity. And the velocity is measurable without the silhouette:

      dP/dt = (dr/dt) * ray  +  r * d(ray)/dt
              \\__radar__/       \\___camera___/

  The radar supplies the radial component through `range_rate_ms`; the camera
  supplies the two perpendicular components through the clubhead centroid's
  motion across frames. That is the division of labour Trackman's OERT uses,
  and it leaves the axis with ONE degree of freedom -- its phase around v --
  instead of two.

So the fit goes from five free parameters at a wrong constant range to four at
a measured one.

VALIDATION WITHOUT TRUTH. There is no reference instrument here, but the club
itself is a constraint. The mesh carries its own 33.10 deg loft and 61.19 deg
lie, so `triad(0,0,0)` is already the club sitting square. The fitted yaw,
pitch and roll are therefore DEVIATIONS from a square club at impact, and a
real 7-iron delivery does not sit 66 deg off its lie. Any shot whose recovered
pose leaves a generous physical envelope is wrong, whatever it scores -- and the
shipped fit leaves it on essentially every shot.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path

import numpy as np
from scipy.optimize import minimize

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "research"))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from openflight.acoustic import impact_frame_from_trigger  # noqa: E402
from silhouette_poc.generator.mesh_truth import TriangleMesh  # noqa: E402
from silhouette_poc.replay.fit_real import (  # noqa: E402
    CAMERA_CENTER_WORLD,
    _ray_world,
    measured_camera,
    triad,
)
from silhouette_poc.replay.club_angles import (  # noqa: E402
    delivered_angles,
    in_envelope,
    square_pose,
)
from silhouette_poc.replay.fit_real import iou as _iou  # noqa: E402
from silhouette_poc.replay.pose_scores import chamfer_px  # noqa: E402
from silhouette_poc.replay.rigid_motion import rotation_from_omega_deg_s  # noqa: E402

from test_fusion_chamfer import MISS_PENALTY_PX, MPH_PER_MPS, load_prior_fits  # noqa: E402
from flight_track import track_flight  # noqa: E402
from test_meshfit_depth_ab import EXCLUDE, MESH, SESSION, club_masks  # noqa: E402
from test_rigid_rotation_prior import render  # noqa: E402

BALL_RANGE_MM = 1581.0
DEFAULT_SWING_RADIUS_M = 1.6

# Impact is measured per shot from the ball's own departure, NOT assumed from
# the trigger. An earlier version used a fixed `trigger - 6.0 frames`, which put
# impact at frame 68 on every shot and was wrong by 3.89 frames.
#
# Across 20 shots the ball tracker gives impact at 71.89 +- 0.77 (trigger is
# frame 74 on every capture), an implied lag of 2.11 frames = 4.52 ms. Sound
# covers the 1.575 m from ball to sensor in 4.59 ms, so the lag IS the acoustic
# time of flight and the hardware GATE->HOST_INT path (~10 us) is negligible.
#
# Do not "verify" this by eye from the clubhead's arrival: the head is 27 px
# wide and sits adjacent to the ball for two or three frames before it strikes.
# That misreading is what produced the 68 in the first place.
TRIGGER_FRAME_FALLBACK = 74.0
# Ball-to-microphone distance for this rig, from the tape chain. The fallback
# below derives the lag from THIS rather than from a frame constant, so a build
# at a different distance stays correct -- see `openflight.acoustic`.
TEE_RANGE_M = 1.575
CAPTURE_FPS = 467.6


def impact_frame_for(frames, row):
    """Impact frame from the ball's departure, with an acoustic fallback."""
    track = track_flight(frames)
    if track is not None and np.isfinite(track.impact_frame):
        trigger = float(
            row.get("camera_metadata_pre_trigger_frames") or TRIGGER_FRAME_FALLBACK
        )
        lag = trigger - float(track.impact_frame)
        # A lag far from the acoustic time of flight means the tracker latched
        # onto something else; fall back rather than anchor a range ramp on it.
        if 0.0 <= lag <= 5.0:
            return float(track.impact_frame)
    trigger = row.get("camera_metadata_pre_trigger_frames")
    if not trigger:
        return None
    fps = float(row.get("camera_metadata_delivered_fps") or CAPTURE_FPS)
    return impact_frame_from_trigger(float(trigger), fps, TEE_RANGE_M)


# triad(0,0,0) is NOT a square club -- the mesh's local +x points out its BACK,
# so the origin is a face aimed at the camera. Seeding a grid around zero
# searches the neighbourhood of a backwards club and never reaches the square
# one, which sits near pitch = -194 deg. An earlier version of this file did
# exactly that and returned face angles of 130-170 deg on every shot.
SQUARE_POSE = square_pose()


def clubhead_velocity_world(rays, ranges_mm, elapsed_s, range_rate_ms):
    """Velocity of the clubhead in world mm/s, fusing both sensors.

    dP/dt = (dr/dt) * ray + r * d(ray)/dt. The radar owns the first term, the
    camera the second. Neither instrument can supply the other's part: the
    radar has a 277 mm cross-range cell at this distance, and the camera has no
    depth.
    """
    rays = np.asarray(rays, dtype=float)
    times = np.asarray(elapsed_s, dtype=float)
    if len(times) < 2:
        return None
    # d(ray)/dt by least squares, per component, so a single noisy centroid
    # cannot swing the direction the way a finite difference would.
    dray = np.array([np.polyfit(times, rays[:, k], 1)[0] for k in range(3)])
    mid_range = float(np.mean(ranges_mm))
    mid_ray = rays[len(rays) // 2]
    return range_rate_ms * 1000.0 * mid_ray + mid_range * dray


def axis_basis(velocity_world):
    """Two orthonormal vectors spanning the plane perpendicular to v."""
    v = np.asarray(velocity_world, dtype=float)
    norm = np.linalg.norm(v)
    if not np.isfinite(norm) or norm < 1e-9:
        return None
    v = v / norm
    seed = np.array([0.0, 0.0, 1.0]) if abs(v[2]) < 0.9 else np.array([1.0, 0.0, 0.0])
    e1 = np.cross(v, seed)
    e1 /= np.linalg.norm(e1)
    return e1, np.cross(v, e1)


def omega_from_phase(basis, phase_deg, magnitude_deg_s):
    """Angular velocity perpendicular to v, at a given phase around it."""
    e1, e2 = basis
    phase = math.radians(float(phase_deg))
    return magnitude_deg_s * (math.cos(phase) * e1 + math.sin(phase) * e2)


def ranges_from_radar(elapsed_s, impact_elapsed_s, range_rate_ms):
    """Per-frame clubhead range: the ball's range at impact, walked back."""
    return BALL_RANGE_MM - range_rate_ms * 1000.0 * (
        impact_elapsed_s - np.asarray(elapsed_s, dtype=float)
    )


def score(mesh, camera, ctx, params, *, want="chamfer"):
    """Mean IoU and mean chamfer for a four-parameter fused pose sequence."""
    yaw, pitch, roll, phase = np.asarray(params, dtype=float)
    normal, width, height = triad(yaw, pitch, roll)
    basis0 = np.column_stack((normal, width, height))
    omega = omega_from_phase(ctx["axis_basis"], phase, ctx["omega_mag"])
    ious, chamfers = [], []
    for index, mask in enumerate(ctx["masks"]):
        rotation = rotation_from_omega_deg_s(omega, ctx["elapsed_s"][index])
        centre = CAMERA_CENTER_WORLD + ctx["rays"][index] * ctx["ranges_mm"][index]
        model = render(mesh, rotation @ basis0, centre, camera)
        if model is None:
            ious.append(0.0)
            chamfers.append(MISS_PENALTY_PX)
            continue
        ious.append(_iou(model, mask))
        distance = chamfer_px(model, mask)
        chamfers.append(distance if math.isfinite(distance) else MISS_PENALTY_PX)
    mean_iou, mean_chamfer = float(np.mean(ious)), float(np.mean(chamfers))
    if want == "cost_chamfer":
        return mean_chamfer
    if want == "cost_iou":
        return -mean_iou
    return mean_iou, mean_chamfer


def fit(mesh, camera, ctx, metric, maxiter):
    """Coarse grid over the four parameters, then Nelder-Mead from the best seeds."""
    cost = f"cost_{metric}"
    base_yaw, base_pitch, base_roll = SQUARE_POSE
    seeds = []
    for yaw in (base_yaw - 15.0, base_yaw, base_yaw + 15.0):
        for pitch in (base_pitch - 10.0, base_pitch, base_pitch + 10.0):
            for roll in (base_roll - 15.0, base_roll, base_roll + 15.0):
                for phase in (0.0, 45.0, 90.0, 135.0, 180.0, 225.0, 270.0, 315.0):
                    start = (yaw, pitch, roll, phase)
                    seeds.append((score(mesh, camera, ctx, start, want=cost), start))
    seeds.sort(key=lambda item: item[0])
    best = None
    for _value, start in seeds[:6]:
        result = minimize(
            lambda p: score(mesh, camera, ctx, p, want=cost),
            np.asarray(start, dtype=float),
            method="Nelder-Mead",
            options={"maxiter": maxiter, "xatol": 0.3, "fatol": 2e-4},
        )
        if best is None or result.fun < best.fun:
            best = result
    return best


def _basis(yaw, pitch, roll):
    normal, width, height = triad(yaw, pitch, roll)
    return np.column_stack((normal, width, height))


def plausible(params):
    """Is this pose a physically possible delivery of a lofted iron?

    Judged on delivered loft, face angle and lie rather than on the raw
    parameters, which are deviations from a backwards origin and carry no
    physical meaning on their own.
    """
    return in_envelope(delivered_angles(_basis(params[0], params[1], params[2])))


def build_context(row, mesh_masks, prior_record):
    """Everything the fit needs for one shot, or None when it cannot be built."""
    archive = np.load(SESSION / row["archive_frames_npz"])
    frames = archive["frames"][:, :, ::-1]
    sensor_s = archive["sensor_timestamp_ns"].astype(float) * 1e-9
    camera = measured_camera(frames.shape[2], frames.shape[1])
    all_masks = mesh_masks(frames)
    frame_ids = [f for f in prior_record["frames"] if f in all_masks]
    if len(frame_ids) < 4:
        return None, camera, f"only {len(frame_ids)} masks"
    rate = row.get("iwr_club_path_range_rate_ms")
    if not rate:
        return None, camera, "missing radar range rate"
    range_rate_ms = float(rate)
    impact_frame = impact_frame_for(frames, row)
    if impact_frame is None:
        return None, camera, "no impact frame"

    masks = [all_masks[f].astype(bool) for f in frame_ids]
    elapsed_s = sensor_s[frame_ids] - sensor_s[frame_ids[0]]
    impact_elapsed_s = float(
        np.interp(impact_frame, np.arange(len(sensor_s)), sensor_s)
        - sensor_s[frame_ids[0]]
    )
    rays = [
        _ray_world(
            np.asarray((np.nonzero(m)[1].mean(), np.nonzero(m)[0].mean()), float),
            camera,
        )
        for m in masks
    ]
    ranges_mm = ranges_from_radar(elapsed_s, impact_elapsed_s, range_rate_ms)
    velocity = clubhead_velocity_world(rays, ranges_mm, elapsed_s, range_rate_ms)
    if velocity is None:
        return None, camera, "no velocity"
    basis = axis_basis(velocity)
    if basis is None:
        return None, camera, "degenerate velocity"

    speed_ms = float(row["club_speed_mph"]) / MPH_PER_MPS
    ctx = {
        "frame_ids": frame_ids,
        "masks": masks,
        "rays": rays,
        "elapsed_s": elapsed_s,
        "ranges_mm": ranges_mm,
        "axis_basis": basis,
        "omega_mag": math.degrees(speed_ms / DEFAULT_SWING_RADIUS_M),
        "velocity_mm_s": velocity,
        "impact_frame": impact_frame,
        "range_rate_ms": range_rate_ms,
        "camera_speed_ratio": float(np.linalg.norm(velocity)) / (speed_ms * 1000.0),
    }
    return ctx, camera, None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--shot", type=int, action="append")
    parser.add_argument("--metric", choices=("chamfer", "iou"), default="chamfer")
    parser.add_argument("--maxiter", type=int, default=400)
    parser.add_argument(
        "--output", type=Path, default=Path(__file__).with_name("fused_refit.json")
    )
    args = parser.parse_args()

    mesh_data = np.load(MESH)
    mesh = TriangleMesh(
        mesh_data["vertices_local_mm"], mesh_data["faces"], "poc_7iron", "x" * 64
    )
    prior = load_prior_fits()
    with open(SESSION / "shots.csv", newline="", encoding="utf-8") as handle:
        rows = {
            int(r["shot_number"]): r
            for r in csv.DictReader(handle)
            if int(r["shot_number"]) not in EXCLUDE
        }
    selected = set(args.shot) if args.shot else set(prior)
    wanted = sorted(set(prior) & set(rows) & selected)

    results = []
    for shot in wanted:
        row = rows[shot]
        record = min(prior[shot], key=lambda r: abs(r["swing_radius_m"] - 1.6))
        ctx, camera, why = build_context(row, club_masks, record)
        if ctx is None:
            print(f"shot {shot:>3}: skipped -- {why}", flush=True)
            continue
        best = fit(mesh, camera, ctx, args.metric, args.maxiter)
        mean_iou, mean_chamfer = score(mesh, camera, ctx, best.x)
        old_iou, old_chamfer = record["free_per_frame_mean_iou"], None
        entry = {
            "shot": shot,
            "club": row["club"],
            "frames": ctx["frame_ids"],
            "metric": args.metric,
            "params": [float(v) for v in best.x],
            "mean_iou": mean_iou,
            "mean_chamfer_px": mean_chamfer,
            "plausible": bool(plausible(best.x)),
            "shipped_params": record["params"],
            "shipped_plausible": bool(plausible(list(record["params"][:3]) + [0.0])),
            "shipped_mean_iou": record["constrained_mean_iou"],
            "range_span_mm": float(ctx["ranges_mm"][-1] - ctx["ranges_mm"][0]),
            "camera_speed_ratio": ctx["camera_speed_ratio"],
        }
        results.append(entry)
        args.output.write_text(json.dumps(results, indent=2), encoding="utf-8")
        angles = delivered_angles(_basis(*best.x[:3]))
        entry["delivered"] = angles
        print(
            f"shot {shot:>3} {row['club']:>7}  loft {angles['dynamic_loft_deg']:+6.1f} "
            f"face {angles['face_angle_deg']:+6.1f} lie {angles['lie_deg']:5.1f}  "
            f"iou {mean_iou:.4f} cham {mean_chamfer:.3f}px  "
            f"{'IN ENVELOPE' if entry['plausible'] else 'outside   '}  "
            f"(shipped {'in' if entry['shipped_plausible'] else 'out'}, "
            f"v-ratio {ctx['camera_speed_ratio']:.2f})",
            flush=True,
        )
        del old_iou, old_chamfer

    if not results:
        raise SystemExit("no shots fitted")
    ok = sum(1 for r in results if r["plausible"])
    was = sum(1 for r in results if r["shipped_plausible"])
    print(f"\n=== {len(results)} shots, 4 free parameters, radar-derived range ===")
    print(
        f"  physically plausible pose: {ok}/{len(results)}   "
        f"(shipped 5-parameter fit: {was}/{len(results)})"
    )
    print("  static geometry for reference: loft 33.10 deg, lie 61.19 deg")
    for key, label in (
        ("dynamic_loft_deg", "dynamic loft"),
        ("face_angle_deg", "face angle"),
        ("lie_deg", "lie"),
    ):
        values = np.array([r["delivered"][key] for r in results])
        print(
            f"  {label:>14}: mean {values.mean():+6.1f} deg, sd {values.std():5.1f} deg, "
            f"range {values.min():+6.1f} to {values.max():+6.1f}"
        )
    ious = np.array([r["mean_iou"] for r in results])
    chams = np.array([r["mean_chamfer_px"] for r in results])
    print(
        f"  IoU     mean {ious.mean():.4f}  (shipped "
        f"{np.mean([r['shipped_mean_iou'] for r in results]):.4f})"
    )
    print(f"  chamfer mean {chams.mean():.3f} px")
    spans = np.array([r["range_span_mm"] for r in results])
    ratios = np.array([r["camera_speed_ratio"] for r in results])
    print(f"  radar range span across fitted frames: {spans.mean():+.0f} mm")
    print(
        f"  fused |v| / radar club speed: {ratios.mean():.2f} +- {ratios.std():.2f} "
        f"(1.00 would mean the two agree exactly)"
    )
    print(f"\nwrote {args.output}")


if __name__ == "__main__":
    main()
