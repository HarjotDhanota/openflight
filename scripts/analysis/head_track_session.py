#!/usr/bin/env python3
"""Replay every shot of a session through the club chain and tabulate the camera head track.

Research only. Each shot's camera fusion is replayed (``replay_camera_fusion``), so the
table is what the kiosk would show; the head track's own record adds its chords, its
uncertainty and why it rejected. ``--tee-m`` stands in for a session that recorded no
tee range; ``--repick-ops`` re-reads each shot's OPS club speed from its rolling-buffer
I/Q with the current processor. ``--render`` draws the tracked hosel on the frames.

    uv run python scripts/analysis/head_track_session.py SESSION.jsonl --tee-m 1.91 \\
        --repick-ops --out OUT_DIR --render 3
"""

from __future__ import annotations

import argparse
import copy
import csv
import json
import sys
from pathlib import Path
from typing import Any

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from replay_camera_fusion import _read_events, replay_frozen_shot  # noqa: E402

from openflight.clubs import ClubType  # noqa: E402
from openflight.rolling_buffer.processor import RollingBufferProcessor  # noqa: E402
from openflight.rolling_buffer.types import IQCapture  # noqa: E402

COLUMNS = (
    "shot",
    "exposure_us",
    "ops_club_mph",
    "status",
    "club_path_deg",
    "attack_angle_deg",
    "head_track_status",
    "head_track_path_deg",
    "head_track_attack_deg",
    "path_sigma_deg",
    "attack_sigma_deg",
    "turning",
    "turn_rate_ratio",
    "frames",
    "image_speed_px_per_ms",
    "transverse_over_ops",
    "contact_sigma_ms",
    "flags",
)


def _repicked_club_speeds(events: list[dict[str, Any]], club: ClubType) -> dict[int, Any]:
    processor = RollingBufferProcessor()
    speeds = {}
    for event in events:
        if event.get("type") != "rolling_buffer_capture":
            continue
        i_samples, q_samples = event["i_samples"], event["q_samples"]
        capture = IQCapture(
            sample_time=event["sample_time"],
            trigger_time=event["trigger_time"],
            i_samples=json.loads(i_samples) if isinstance(i_samples, str) else i_samples,
            q_samples=json.loads(q_samples) if isinstance(q_samples, str) else q_samples,
        )
        processed = processor.process_capture(capture, club_type=club)
        speeds[event["shot_number"]] = getattr(processed, "club_speed_mph", None)
    return speeds


def _row(shot_number: int, archive: dict, delivery: dict, ops_mph: Any) -> dict[str, Any]:
    track = delivery.get("head_track") or {}
    chords = track.get("chords") or []
    speed = track.get("speed_mph")
    flags = [flag for flag in (track.get("club_path_flag"), track.get("attack_angle_flag")) if flag]
    return {
        "shot": shot_number,
        "exposure_us": int(np.asarray(archive.get("exposure_us", [0]))[0]),
        "ops_club_mph": round(ops_mph, 1) if isinstance(ops_mph, float) else ops_mph,
        "status": delivery.get("status"),
        "club_path_deg": delivery.get("club_path_deg"),
        "attack_angle_deg": delivery.get("attack_angle_deg"),
        "head_track_status": track.get("status"),
        "head_track_path_deg": track.get("club_path_deg"),
        "head_track_attack_deg": track.get("attack_angle_deg"),
        "path_sigma_deg": track.get("club_path_sigma_deg"),
        "attack_sigma_deg": track.get("attack_angle_sigma_deg"),
        "turning": track.get("turning"),
        "turn_rate_ratio": track.get("turn_rate_ratio"),
        "frames": " ".join(str(point["frame"]) for point in track.get("track") or []),
        "image_speed_px_per_ms": " ".join(str(c["image_speed_px_per_ms"]) for c in chords),
        "transverse_over_ops": " ".join(
            f"{c['transverse_mps'] * 2.23694 / speed:.2f}" for c in chords if speed
        ),
        "contact_sigma_ms": track.get("contact_sigma_ms"),
        "flags": "; ".join(flags),
    }


def _render(path: Path, archive: dict, delivery: dict, shot_number: int) -> None:
    track = delivery.get("head_track") or {}
    points = {point["frame"]: point for point in track.get("track") or []}
    last = track.get("last_rest_frame")
    if last is None:
        return
    frames = archive["frames"]
    xs = [p["x_px"] for p in points.values()] or [frames.shape[2] / 2]
    ys = [p["y_px"] for p in points.values()] or [frames.shape[1] / 2]
    cx, cy = int(np.mean(xs)), int(np.mean(ys))
    x0, y0 = max(0, cx - 260), max(0, cy - 230)
    tiles = []
    for index in range(last - 2, min(len(frames), last + 2)):
        if index < 0:
            continue
        crop = frames[index, y0 : y0 + 400, x0 : x0 + 520].astype(np.float32)
        low, high = np.percentile(crop, 1), np.percentile(crop, 99.7)
        scaled = np.clip((crop - low) * 255 / max(1.0, high - low), 0, 255).astype(np.uint8)
        tile = cv2.cvtColor(scaled, cv2.COLOR_GRAY2BGR)
        for other in points.values():
            centre = (int(other["x_px"] - x0), int(other["y_px"] - y0))
            cv2.circle(tile, centre, 3, (0, 160, 255), 1)
        if index in points:
            centre = (int(points[index]["x_px"] - x0), int(points[index]["y_px"] - y0))
            cv2.drawMarker(tile, centre, (0, 0, 255), cv2.MARKER_CROSS, 18, 2)
        label = f"{shot_number} f{index - last:+d} {int(archive['exposure_us'][index])}us"
        cv2.putText(tile, label, (6, 20), 0, 0.55, (0, 255, 255), 1)
        tiles.append(tile)
    summary = (
        f"{track.get('status')} path {track.get('club_path_deg')} "
        f"+-{track.get('club_path_sigma_deg')} aoa {track.get('attack_angle_deg')} "
        f"+-{track.get('attack_angle_sigma_deg')} {track.get('turning')}"
    )
    image = np.hstack(tiles)
    cv2.putText(image, summary, (6, image.shape[0] - 10), 0, 0.55, (0, 255, 0), 1)
    cv2.imwrite(str(path), image)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session_file", type=Path)
    parser.add_argument("--tee-m", type=float, help="tee slant range for a session without one")
    parser.add_argument("--repick-ops", action="store_true")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--render", type=int, default=0, help="shots rendered per exposure")
    args = parser.parse_args()
    events = copy.deepcopy(_read_events(args.session_file))
    start = next(event for event in events if event.get("type") == "session_start")
    if args.tee_m is not None:
        start["config"].setdefault("iwr6843", {})["tee_slant_range_m"] = args.tee_m
    shots = [event for event in events if event.get("type") == "shot_detected"]
    club = ClubType(shots[0].get("club") or ClubType.DRIVER.value) if shots else ClubType.UNKNOWN
    speeds = _repicked_club_speeds(events, club) if args.repick_ops else {}
    for shot in shots:
        if args.repick_ops:
            shot["club_speed_mph"] = speeds.get(shot["shot_number"])
    args.out.mkdir(parents=True, exist_ok=True)
    rows, rendered = [], {}
    for shot in shots:
        number = shot["shot_number"]
        try:
            result = replay_frozen_shot(events, args.session_file, number)
        except (KeyError, OSError, ValueError) as error:
            rows.append({"shot": number, "status": f"replay failed: {error}"})
            continue
        delivery = result["replay"]["club_delivery"]
        row = _row(number, result["_archive"], delivery, shot.get("club_speed_mph"))
        rows.append(row)
        (args.out / f"shot{number:02d}.json").write_text(json.dumps(delivery, indent=1) + "\n")
        exposure = row["exposure_us"]
        if rendered.get(exposure, 0) < args.render and delivery.get("head_track"):
            _render(args.out / f"track_{number:02d}.png", result["_archive"], delivery, number)
            rendered[exposure] = rendered.get(exposure, 0) + 1
        print(json.dumps(row), flush=True)
    with (args.out / "head_track.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
