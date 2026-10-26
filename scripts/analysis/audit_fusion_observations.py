"""Audit saved camera observations and compare wider hosel searches offline only."""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import logging
import math
import sys
from dataclasses import asdict
from pathlib import Path

import cv2
import numpy as np

from openflight import kiosk_extensions, server
from openflight.camera import club_delivery, head_track
from openflight.raw_radar_replay import load_session_events
from scripts.analysis import replay_shot_chain as chain


def discover(inputs, last_rest, *, full_frame=False):
    frames = inputs.frames
    scale = inputs.camera.focal_px / float(inputs.ball_range_m)
    start, stop = (
        last_rest - head_track.BACKGROUND_FRAMES[0],
        last_rest - head_track.BACKGROUND_FRAMES[1],
    )
    stack = frames[max(0, start) : max(1, stop)].astype(np.float32)
    background = np.median(stack, axis=0)
    height, width = frames.shape[1:]
    bx, by = inputs.ball_px
    x0 = 0 if full_frame else max(0, int(bx - head_track.ROI_HALF_WIDTH_M * scale))
    x1 = width if full_frame else min(width, int(bx + head_track.ROI_HALF_WIDTH_M * scale) + 1)
    y1 = height if full_frame else min(height, int(by + head_track.ROI_BELOW_M * scale) + 1)
    noise = (
        float(np.std(stack[-1, :y1, x0:x1] - stack[-2, :y1, x0:x1]) / math.sqrt(2.0))
        if len(stack) >= 2
        else 0.0
    )
    threshold = max(head_track.MOVING_SIGMAS * noise, head_track.MOVING_FLOOR_DN)
    walk = max(head_track.WALK_SIGMAS * noise, head_track.WALK_FLOOR_DN)
    found, rejected = {}, {}
    for index in range(last_rest + 1):
        moving = np.abs(frames[index, :y1, x0:x1].astype(np.float32) - background[:y1, x0:x1])
        end = head_track._shaft_end(
            moving > threshold,
            moving > walk,
            min_length_px=head_track.SHAFT_MIN_LENGTH_M * scale,
            band_px=max(3.0, head_track.SHAFT_BAND_M * scale),
            gap_px=max(3, head_track.FERRULE_GAP_M * scale),
            golfer_on_left=head_track._golfer_on_image_left(inputs),
        )
        if end is None:
            rejected[index] = "no shaft endpoint"
        elif min(end[0], end[1], x1 - x0 - 1 - end[0], y1 - 1 - end[1]) < 4:
            rejected[index] = "endpoint at image/search boundary"
        else:
            found[index] = (end[0] + x0, end[1])
    return found, rejected


def contiguous_approach(points, last_rest, inputs):
    """Retain the last connected approach; do not join a backswing across a gap."""
    if not points:
        return {}
    last = max(points)
    scale = inputs.camera.focal_px / float(inputs.ball_range_m)
    if (
        last < last_rest - 1
        or math.dist(points[last], inputs.ball_px) > head_track.HOSEL_NEAR_BALL_M * scale
    ):
        return {}
    first = last
    while first - 1 in points:
        first -= 1
    return {i: points[i] for i in range(first, last + 1)}


def estimate_points(inputs, points):
    original = head_track._track_hosel
    try:
        head_track._track_hosel = lambda *_args: points
        return head_track.estimate_head_track(inputs).to_dict()
    except (ValueError, IndexError, KeyError) as error:
        return {"status": "prototype_error", "reason": str(error), "track": []}
    finally:
        head_track._track_hosel = original


def render(frames, stamps, contact, baseline, variants, path):
    tiles = []
    for i, frame in enumerate(frames):
        image = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
        for key, points, color in [
            (
                "old",
                {int(p["frame"]): (p["x_px"], p["y_px"]) for p in baseline.get("track", [])},
                (0, 255, 255),
            ),
            ("near", variants.get("near", {}).get("discovered", {}), (0, 255, 0)),
            ("full", variants.get("full", {}).get("discovered", {}), (255, 120, 0)),
        ]:
            if i in points:
                cv2.drawMarker(
                    image, tuple(round(v) for v in points[i]), color, cv2.MARKER_CROSS, 20, 2
                )
        image = cv2.resize(image, (384, 240))
        elapsed = (float(stamps[i]) - contact) / 1e6 if contact is not None else math.nan
        cv2.putText(
            image,
            f"f{i}  {elapsed:+.1f}ms",
            (6, 18),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (80, 255, 255),
            1,
        )
        tiles.append(image)
    while len(tiles) % 4:
        tiles.append(np.zeros_like(tiles[0]))
    sheet = np.vstack([np.hstack(tiles[i : i + 4]) for i in range(0, len(tiles), 4)])
    cv2.imwrite(str(path), sheet)


def audit_input(inputs, baseline, output, number, *, render_frames=True):
    exposure = (
        np.asarray(inputs.exposure_us)
        if inputs.exposure_us is not None
        else np.zeros(len(inputs.frames))
    )
    mid = np.asarray(inputs.timestamps_ns, float) + exposure * 500
    last = int(np.searchsorted(mid, inputs.contact_window_ns[0])) - 1
    variants = {}
    if 0 <= last < len(inputs.frames) and inputs.ball_range_m and inputs.ball_px:
        for key, full in [("near", False), ("full", True)]:
            found, rejected = discover(inputs, last, full_frame=full)
            selected = contiguous_approach(found, last, inputs)
            variants[key] = {
                "discovered": found,
                "rejected": rejected,
                "selected": selected,
                "estimate": estimate_points(inputs, selected),
            }
    contact = sum(inputs.contact_window_ns) / 2
    if render_frames:
        render(
            inputs.frames,
            mid,
            contact,
            baseline,
            variants,
            output / "pictures" / f"shot{number:02d}-all-frames.jpg",
        )
    return {
        "last_precontact_frame": last,
        "contact_sigma_ms": (inputs.contact_window_ns[1] - inputs.contact_window_ns[0])
        / 1e6
        / math.sqrt(12),
        "variants": variants,
        "camera": asdict(inputs.camera),
        "range_m": inputs.ball_range_m,
    }


def capture_diagnostics(archive, metadata):
    stamps = np.asarray(archive["sensor_timestamp_ns"], dtype=float)
    exposure = np.asarray(archive.get("exposure_us", np.zeros(len(stamps))), dtype=float)
    latency = head_track.gate_latency_ns(metadata)
    window = head_track.head_track_context(archive, gate_latency=latency).contact_window_ns
    trigger = float(np.asarray(archive["trigger_boottime_ns"]))
    lighting = metadata.get("auto_exposure") or {}
    eligibility = lighting.get("analysis_eligibility") or {}
    result = {
        "post_callback_exposures": int(np.sum(stamps > trigger)),
        "fully_pre_contact_model": int(np.sum(stamps + exposure * 1e3 <= window[0])),
        "fully_post_contact_model": int(np.sum(stamps >= window[1])),
        "max_interval_ms": float(np.max(np.diff(stamps))) / 1e6,
        "exposure_us": float(np.median(exposure)),
        "lighting_analysis_eligible": eligibility.get(
            "eligible", lighting.get("analysis_eligible")
        ),
        "lighting_reason": eligibility.get("reason"),
        "exposure_ball_diameter_px": (eligibility.get("ball") or {}).get("diameter_px"),
    }
    offset = (metadata.get("trigger_clocks") or {}).get("boottime_minus_monotonic_ns")
    if offset is not None and archive.get("host_timestamp_ns") is not None:
        result["median_delivery_age_ms"] = (
            float(np.median(np.asarray(archive["host_timestamp_ns"], float) + offset - stamps))
            / 1e6
        )
    return result


def current_speed_inputs(shot, ops_track):
    """Use one current raw replay for both aggregate speeds and the timed readings."""
    if not isinstance(ops_track, dict) or ops_track.get("canonical_radial_mph") is None:
        raise ValueError("current speed replay requires a successfully replayed OPS capture")
    shot.ball_speed_mph = float(ops_track["canonical_radial_mph"])
    reading = ops_track.get("club_reading")
    shot.club_speed_mph = float(reading["radial_mph"]) if isinstance(reading, dict) else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--ignore-iwr", action="store_true")
    parser.add_argument("--production-only", action="store_true")
    parser.add_argument("--no-render", action="store_true")
    parser.add_argument("--current-speed-inputs", action="store_true")
    parser.add_argument("--baseline-head-source", type=Path)
    parser.add_argument("--shots")
    args = parser.parse_args()
    logging.basicConfig(level=logging.ERROR)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "pictures").mkdir(exist_ok=True)
    digest, start, events = load_session_events(args.session)
    chain.configure_kiosk(start, ignore_iwr=args.ignore_iwr, aim_uncertainty_deg=None)
    original = club_delivery.estimate_head_track
    if args.baseline_head_source:
        module_name = "openflight.camera._audit_baseline_head_track"
        spec = importlib.util.spec_from_file_location(module_name, args.baseline_head_source)
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        original = module.estimate_head_track
    estimator_hash = hashlib.sha256(Path(original.__code__.co_filename).read_bytes()).hexdigest()
    script_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    captured = {}
    wanted = {int(value) for value in args.shots.split(",")} if args.shots else None

    def observe(inputs):
        result = original(inputs)
        captured["inputs"], captured["baseline"] = inputs, result.to_dict()
        return result

    club_delivery.estimate_head_track = observe
    results = []
    try:
        for record in events:
            if record.get("type") != "shot_detected":
                continue
            n = record["shot_number"]
            if wanted is not None and n not in wanted:
                continue
            captured.clear()
            shot = chain.rebuild_shot(record, ignore_iwr=args.ignore_iwr)
            shot.camera_fusion_session_uuid = start.get("session_uuid")
            shot.ops_speed_track = chain.ops_track_for(events, n, shot.club.value)
            if args.current_speed_inputs:
                current_speed_inputs(shot, shot.ops_speed_track)
            capture, archive = chain.load_clip(events, n, args.session.parent)
            server._fuse_camera_measurements(shot, capture, archive)
            server._isolated_stage(
                shot, "after_enrichment", kiosk_extensions.call_all, "after_enrichment", shot
            )
            server._finalize_shot_detected(shot, emit_event="shot")
            data = shot.to_dict()
            row = {
                "shot": n,
                "baseline": captured.get("baseline"),
                "metrics": data.get("metrics"),
                "club_delivery": data.get("club_delivery"),
                "club_delivery_status": (data.get("club_delivery") or {})
                .get("cross_check", {})
                .get("chained", {})
                .get("status"),
                "ball_range_source": data.get("ball_range_source"),
                "ball_range_m": data.get("ball_range_m"),
                "stage_errors": data.get("stage_errors"),
                "capture_sha256": archive.get("_capture_npz_sha256"),
                "frame_count": len(archive["frames"]),
                "delivered_fps": capture.metadata.get("delivered_fps"),
                "median_interval_ms": capture.metadata.get("median_interval_ms"),
                "trigger_pre_exposure": capture.metadata.get("pre_trigger_frames_by_exposure"),
                "gate_latency_ms": head_track.gate_latency_ns(capture.metadata) / 1e6
                if head_track.gate_latency_ns(capture.metadata) is not None
                else None,
            }
            row["capture_diagnostics"] = capture_diagnostics(archive, capture.metadata)
            if captured.get("inputs") is not None and not args.production_only:
                row.update(
                    audit_input(
                        captured["inputs"],
                        captured["baseline"],
                        args.output,
                        n,
                        render_frames=not args.no_render,
                    )
                )
            elif not args.no_render:
                frames = archive["frames"]
                render(
                    frames,
                    archive["sensor_timestamp_ns"],
                    None,
                    {},
                    {},
                    args.output / "pictures" / f"shot{n:02d}-all-frames.jpg",
                )
            results.append(row)
            print(
                f"shot {n}: baseline {row.get('baseline', {}).get('status') if row.get('baseline') else 'not run'}; wide {row.get('variants', {}).get('near', {}).get('estimate', {}).get('status')}",
                flush=True,
            )
    finally:
        club_delivery.estimate_head_track = original
    payload = {
        "source_session_sha256": digest,
        "ignore_iwr": args.ignore_iwr,
        "current_speed_inputs": args.current_speed_inputs,
        "head_estimator_source_sha256": estimator_hash,
        "audit_script_sha256": script_hash,
        "rows": results,
    }
    (args.output / "audit.json").write_text(
        json.dumps(chain._plain(payload), indent=2) + "\n", encoding="utf-8"
    )
    with (args.output / "frame-counts.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "shot",
                "baseline_status",
                "baseline_frames",
                "wide_status",
                "wide_frames",
                "full_frame_status",
                "full_frame_frames",
            ],
        )
        writer.writeheader()
        for row in results:
            old = row.get("baseline") or {}
            near = row.get("variants", {}).get("near", {}).get("estimate", {})
            full = row.get("variants", {}).get("full", {}).get("estimate", {})
            writer.writerow(
                {
                    "shot": row["shot"],
                    "baseline_status": old.get("status"),
                    "baseline_frames": len(old.get("track", [])),
                    "wide_status": near.get("status"),
                    "wide_frames": len(near.get("track", [])),
                    "full_frame_status": full.get("status"),
                    "full_frame_frames": len(full.get("track", [])),
                }
            )


if __name__ == "__main__":
    main()
