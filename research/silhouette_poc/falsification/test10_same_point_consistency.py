"""Falsification test 10: do camera and radar follow the same club point?

For each consecutive pre-impact optical head-centroid pair, use the measured
camera geometry and the OPS speed magnitude to solve the camera velocity.  Its
projection onto the radar line of sight predicts a radial speed without using
the radar range *rate*.  Compare that prediction with the IWR club track.

A constant residual could be calibrated.  Large within-shot drift means the
optical centroid and radar scattering centre are not one stable body point and
must not be fused as a single 3-D trajectory.
"""

from __future__ import annotations

import csv
import json
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "research"))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from silhouette_poc.replay.fit_real import measured_camera  # noqa: E402
from test_meshfit_depth_ab import EXCLUDE, SESSION, club_masks  # noqa: E402

MPH_PER_MPS = 2.2369362920544
CAMERA_HEIGHT_M = 0.2032
CAMERA_LATERAL_M = -0.060325
RADAR_HEIGHT_M = 0.1651
BALL_HEIGHT_M = 0.040
TEE_SLANT_M = 1.524


def optical_velocity(
    first_uv: np.ndarray,
    second_uv: np.ndarray,
    elapsed_s: float,
    speed_mps: float,
    focal_px: float,
    principal_uv: tuple[float, float],
    camera_ball_range_m: float,
    ball_uv: np.ndarray,
) -> np.ndarray | None:
    """OPS-constrained world velocity from one optical centroid interval."""
    cx, cy = principal_uv

    def normalized(uv: np.ndarray) -> tuple[float, float]:
        return (float((uv[0] - cx) / focal_px), float(-(uv[1] - cy) / focal_px))

    ball_x, ball_z = normalized(ball_uv)
    expected_azimuth = math.atan2(
        -CAMERA_LATERAL_M,
        math.sqrt(TEE_SLANT_M**2 - (BALL_HEIGHT_M - RADAR_HEIGHT_M) ** 2),
    )
    yaw = expected_azimuth - math.atan2(ball_x, 1.0)
    expected_elevation = math.atan2(
        BALL_HEIGHT_M - CAMERA_HEIGHT_M,
        math.sqrt(TEE_SLANT_M**2 - (BALL_HEIGHT_M - RADAR_HEIGHT_M) ** 2),
    )
    pitch = expected_elevation - math.atan2(ball_z, 1.0)
    contact_depth = camera_ball_range_m / math.sqrt(1.0 + ball_x**2 + ball_z**2)

    first_x, first_z = normalized(first_uv)
    last_x, last_z = normalized(second_uv)
    x_rate = (last_x - first_x) / elapsed_s
    z_rate = (last_z - first_z) / elapsed_s
    a = 1.0 + last_x**2 + last_z**2
    b = 2.0 * contact_depth * (last_x * x_rate + last_z * z_rate)
    c = contact_depth**2 * (x_rate**2 + z_rate**2) - speed_mps**2
    discriminant = b**2 - 4.0 * a * c
    if discriminant < 0.0:
        return None
    camera_forward = (-b + math.sqrt(discriminant)) / (2.0 * a)
    if camera_forward <= 0.0:
        return None
    camera_lateral = contact_depth * x_rate + last_x * camera_forward
    camera_vertical = contact_depth * z_rate + last_z * camera_forward
    horizontal_forward = (
        math.cos(pitch) * camera_forward - math.sin(pitch) * camera_vertical
    )
    vertical = math.sin(pitch) * camera_forward + math.cos(pitch) * camera_vertical
    lateral = math.cos(yaw) * camera_lateral + math.sin(yaw) * horizontal_forward
    forward = -math.sin(yaw) * camera_lateral + math.cos(yaw) * horizontal_forward
    return np.asarray([lateral, vertical, forward])


def main() -> None:
    with open(SESSION / "shots.csv", newline="", encoding="utf-8") as handle:
        rows = [
            row
            for row in csv.DictReader(handle)
            if int(row["shot_number"]) not in EXCLUDE
        ]
    records = []
    radar_los = np.asarray(
        [
            0.0,
            BALL_HEIGHT_M - RADAR_HEIGHT_M,
            math.sqrt(TEE_SLANT_M**2 - (BALL_HEIGHT_M - RADAR_HEIGHT_M) ** 2),
        ]
    )
    radar_los /= np.linalg.norm(radar_los)
    camera_ball_range = math.sqrt(
        CAMERA_LATERAL_M**2 + (BALL_HEIGHT_M - CAMERA_HEIGHT_M) ** 2 + radar_los[2] ** 2
    )

    for row in rows:
        shot = int(row["shot_number"])
        archive = np.load(SESSION / row["archive_frames_npz"])
        frames = archive["frames"][:, :, ::-1]
        timestamps = archive["sensor_timestamp_ns"].astype(float) * 1e-9
        masks = club_masks(frames)
        camera = measured_camera(frames.shape[2], frames.shape[1])
        from flight_track import track_flight  # noqa: PLC0415

        flight = track_flight(frames)
        if flight is None:
            records.append(
                {"shot": shot, "club": row["club"], "status": "no_ball_track"}
            )
            continue
        ball_uv = np.asarray(flight.tee_uv, dtype=float)
        centroids = {}
        for frame, mask in masks.items():
            yy, xx = np.nonzero(mask)
            centroids[frame] = np.asarray([xx.mean(), yy.mean()])
        predicted = []
        intervals = []
        speed_mps = float(row["club_speed_mph"]) / MPH_PER_MPS
        for first in sorted(centroids):
            second = first + 1
            if second not in centroids:
                continue
            velocity = optical_velocity(
                centroids[first],
                centroids[second],
                timestamps[second] - timestamps[first],
                speed_mps,
                camera.fx,
                (camera.cx, camera.cy),
                camera_ball_range,
                ball_uv,
            )
            if velocity is None:
                continue
            predicted.append(float(np.dot(velocity, radar_los)))
            intervals.append([first, second])
        radar_rate = abs(float(row["iwr_club_path_range_rate_ms"]))
        if not predicted:
            record = {
                "shot": shot,
                "club": row["club"],
                "status": "no_consecutive_optical_interval",
                "n_head_frames": len(masks),
                "radar_rate_mps": radar_rate,
            }
        else:
            values = np.asarray(predicted)
            residuals = values - radar_rate
            record = {
                "shot": shot,
                "club": row["club"],
                "status": "available",
                "n_head_frames": len(masks),
                "intervals": intervals,
                "radar_rate_mps": radar_rate,
                "camera_predicted_rate_mps": values.tolist(),
                "residual_mps": residuals.tolist(),
                "median_residual_mps": float(np.median(residuals)),
                "within_shot_residual_range_mps": float(np.ptp(residuals)),
            }
        records.append(record)
        print(
            f"shot {shot:>3}: {record['status']:<32} "
            f"radar={radar_rate:5.1f} "
            f"pred={np.round(predicted, 1).tolist()}",
            flush=True,
        )

    available = [record for record in records if record["status"] == "available"]
    output = Path(__file__).with_name("test10_same_point_consistency.json")
    output.write_text(json.dumps(records, indent=2), encoding="utf-8")
    residuals = np.asarray(
        [value for record in available for value in record["residual_mps"]], dtype=float
    )
    drifts = np.asarray(
        [record["within_shot_residual_range_mps"] for record in available]
    )
    print(
        f"\n=== all {len(rows)} shots attempted; {len(available)} with usable intervals ==="
    )
    print(
        f"interval residual median {np.median(residuals):+.1f} m/s; "
        f"MAD {np.median(np.abs(residuals - np.median(residuals))):.1f} m/s"
    )
    print(f"within-shot residual range median {np.median(drifts):.1f} m/s")
    print(f"wrote {output}")


if __name__ == "__main__":
    main()
