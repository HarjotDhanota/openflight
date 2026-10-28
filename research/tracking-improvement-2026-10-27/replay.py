"""Replay manifest-associated shots; truth image pixels never enter estimation."""

import argparse
import csv
import hashlib
import json
import math
import platform
import time
from dataclasses import asdict, replace
from pathlib import Path

import cv2
import numpy as np
import scipy
from constrained_masks import fully_precontact_frames, select_component

from openflight.camera import head_track as ht, impact_live, impact_location as il
from openflight.camera.fusion_processing import process_camera_fusion

BUNDLE_SHA256 = "00529c92e6addf72e4f25208b0aab7d5752a3144e5b3a3e1be5c36542d4aeaec"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_package(package):
    assert digest(package / "session-bundle.zip") == BUNDLE_SHA256
    root = (package / "extracted").resolve()
    manifest = json.loads((root / "bundle_manifest.json").read_text(encoding="utf-8"))
    paths = []
    verified = 0
    for entry in manifest["entries"]:
        path = (root / entry["path"]).resolve()
        assert path.is_relative_to(root)
        assert path.stat().st_size == entry["size_bytes"]
        assert digest(path) == entry["sha256"], entry["path"]
        verified += 1
        paths.append(path)
    logs = [p for p in paths if p.name.startswith("session_") and p.suffix == ".jsonl"]
    assert len(logs) == 1
    rows = [json.loads(line) for line in logs[0].read_text(encoding="utf-8").splitlines()]
    return paths, rows, verified


def prepare_shot(paths, rows, number):
    shot = next(r for r in rows if r["type"] == "shot_detected" and r["shot_number"] == number)
    capture = next(r for r in rows if r["type"] == "camera_capture" and r["shot_number"] == number)
    name = Path(capture["capture_path"]).name
    matched = [p for p in paths if p.name == "frames.npz" and p.parent.name == name]
    assert len(matched) == 1
    path = matched[0]
    config = next(r for r in rows if r["type"] == "session_start")["config"]
    clip = impact_live.LiveAnalyser()._prepare(
        {
            "session_uuid": "offline-tracking-investigation",
            "shot_number": number,
            "clip_path": str(path.parent),
            "shot": shot,
            "start": {k: config[k] for k in impact_live.START_KEYS if k in config},
            "handedness": "right",
        }
    )
    with np.load(path) as data:
        archive = dict(data)
    archive["_capture_npz_sha256"] = digest(path)
    return clip, archive


def anchored_candidates(shot, archive, out, number):
    stamps = archive["sensor_timestamp_ns"].astype(np.float64)
    exposures = archive["exposure_us"].astype(np.float64)
    last_rest = shot.contact.last_rest_frame
    context = ht.head_track_context(archive)
    earliest = float(stamps[last_rest])
    if context.contact_window_ns is not None:
        earliest = min(earliest, context.contact_window_ns[0])
    complete = fully_precontact_frames(stamps, exposures, earliest)
    if not complete.size:
        return {"reason": "no_fully_precontact_exposure"}
    last = int(complete[-1])
    h, w = shot.frames.shape[1:]
    inputs = ht.HeadTrackInputs(
        frames=shot.frames,
        timestamps_ns=stamps,
        exposure_us=exposures,
        camera=ht.HeadTrackCamera(shot.camera.focal_px, 0.0, w, h),
        ball_px=(shot.ball.x, shot.ball.y),
        ball_range_m=shot.ball_range_mm / 1000,
        ops_club_speed_mph=shot.club_speed_mps * ht.MPH_PER_MPS,
        contact_window_ns=(earliest, earliest),
        handedness="RH",
    )
    started = time.perf_counter()
    anchors = ht._track_hosel(shot.frames, last, inputs, shot.camera.focal_px / inputs.ball_range_m)
    tracking_ms = (time.perf_counter() - started) * 1000
    wanted = tuple(sorted(set(shot.window) | set(anchors)))
    stop = min(shot.window[0], last_rest + 1 - math.ceil(il.BACKGROUND_LEAD_S * shot.fps))
    backgrounds = {
        "original_background": slice(0, stop),
        "local_background": slice(
            max(0, last - ht.BACKGROUND_FRAMES[0]), max(1, last - ht.BACKGROUND_FRAMES[1])
        ),
    }
    results = {}
    yy, xx = np.mgrid[:h, :w]
    occlusion = (xx - shot.ball.x) ** 2 + (yy - shot.ball.y) ** 2 <= (
        shot.ball.diameter_px / 2
    ) ** 2
    for variant, background in backgrounds.items():
        started = time.perf_counter()
        moving = il._moving_masks(shot.frames, background, wanted)
        moving_ms = (time.perf_counter() - started) * 1000
        selection_ms = 0.0
        records = []
        tiles = []
        for index in wanted:
            current = moving[index]
            anchor = anchors.get(index)
            head = None
            if stamps[index] + exposures[index] * 1000 > earliest:
                reason = "not_fully_precontact"
            else:
                selection_started = time.perf_counter()
                head, reason = select_component(
                    current, shot.mm_per_px(index), anchor, occlusion=occlusion
                )
                selection_ms += (time.perf_counter() - selection_started) * 1000
            n, labels, stats, _ = cv2.connectedComponentsWithStats(current.astype(np.uint8), 8)
            components = []
            scale = shot.mm_per_px(index)
            for label in range(1, n):
                component = (labels == label).astype(np.uint8)
                ys, xs = np.nonzero(component)
                gap = (
                    None
                    if anchor is None
                    else float(np.sqrt((xs - anchor[0]) ** 2 + (ys - anchor[1]) ** 2).min())
                )
                eligible_area = stats[label, 4] * scale**2 >= il.MIN_COMPONENT_AREA_MM2
                if not eligible_area and (gap is None or gap > ht.HOSEL_PIXEL_SIGMA):
                    continue
                distance = cv2.distanceTransform(component, cv2.DIST_L2, 5)
                components.append(
                    {
                        "label": label,
                        "area_px": int(stats[label, 4]),
                        "max_core_px": float(distance.max()),
                        "anchor_gap_px": gap,
                        "area_mm2": float(stats[label, 4] * scale**2),
                        "passes_component_area": bool(eligible_area),
                    }
                )
            record = {
                "frame": index,
                "anchor_px": anchor,
                "scale_mm_per_px": scale,
                "required_core_px": il.HEAD_CORE_MM / scale,
                "reason": reason,
                "components": components,
                "mask_pixels": 0 if head is None else int(head.sum()),
                "landmarks": None,
            }
            if head is not None:
                record["landmarks"] = {
                    k: v.tolist() for k, v in il.bootstrap_landmarks(head).items()
                }
            records.append(record)
            image = cv2.cvtColor(shot.frames[index], cv2.COLOR_GRAY2BGR)
            contours, _ = cv2.findContours(
                current.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
            )
            cv2.drawContours(image, contours, -1, (0, 160, 255), 1)
            if head is not None:
                image[head] = (0, 200, 0)
            if anchor is not None:
                cv2.circle(image, tuple(round(x) for x in anchor), 5, (255, 0, 255), 1)
            cv2.circle(
                image,
                (round(shot.ball.x), round(shot.ball.y)),
                round(shot.ball.diameter_px / 2),
                (255, 255, 0),
                1,
            )
            cv2.putText(
                image,
                f"f{index} {reason}",
                (4, 18),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.42,
                (255, 255, 255),
                1,
            )
            tiles.append(cv2.resize(image, (480, 300)))
        columns = 3
        sheet = np.zeros((math.ceil(len(tiles) / columns) * 300, columns * 480, 3), np.uint8)
        for j, tile in enumerate(tiles):
            r, c = divmod(j, columns)
            sheet[r * 300 : (r + 1) * 300, c * 480 : (c + 1) * 480] = tile
        cv2.imwrite(str(out / f"shot-{number:02}-{variant}.jpg"), sheet)
        results[variant] = {
            "records": records,
            "elapsed_ms": (time.perf_counter() - started) * 1000,
            "moving_masks_ms": moving_ms,
            "selection_ms": selection_ms,
            "candidate_frames": [r["frame"] for r in records if r["mask_pixels"]],
        }
    return {
        "label": "offline_candidate_only_no_live_routing",
        "last_complete_frame": last,
        "earliest_contact_sensor_ns": earliest,
        "scanned_frames": list(range(last + 1)),
        "hosel_frames": list(anchors),
        "tracking_ms": tracking_ms,
        "variants": results,
    }


def baseline_overlay(archive, clip, delivery, out, number):
    points = {p["frame"]: p for p in (delivery.get("head_track") or {}).get("track", [])}
    frames = archive["frames"]
    trigger = int(archive["pre_trigger_count"])
    indices = list(range(max(0, trigger - 10), min(len(frames), trigger + 2)))
    sheet = np.zeros((math.ceil(len(indices) / 3) * 220, 960, 3), np.uint8)
    for j, index in enumerate(indices):
        image = cv2.cvtColor(frames[index], cv2.COLOR_GRAY2BGR)
        if clip.ball:
            cv2.circle(
                image,
                (round(clip.ball.x), round(clip.ball.y)),
                round(clip.ball.diameter_px / 2),
                (255, 255, 0),
                1,
            )
        if index in points:
            p = points[index]
            cv2.circle(image, (round(p["x_px"]), round(p["y_px"])), 6, (0, 255, 0), 2)
        row, col = divmod(j, 3)
        sheet[row * 220 : row * 220 + 200, col * 320 : (col + 1) * 320] = cv2.resize(
            image, (320, 200)
        )
        cv2.putText(
            sheet,
            f"f{index} normal route",
            (col * 320 + 3, row * 220 + 215),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.4,
            (255, 255, 255),
            1,
        )
    cv2.imwrite(str(out / f"shot-{number:02}-baseline.jpg"), sheet)


def write_frame_table(report, out):
    with (out / "frames.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=[
                "shot",
                "frame",
                "variant",
                "normal_hosel",
                "baseline_mask",
                "candidate_pixels",
                "reason",
                "landmarks",
            ],
        )
        writer.writeheader()
        for shot in report["shots"]:
            normal = {
                p["frame"]
                for p in (shot["normal_delivery"].get("head_track") or {}).get("track", [])
            }
            variants = shot["prototype"].get("variants", {})
            for frame in range(shot["frame_shape"][0]):
                writer.writerow(
                    {
                        "shot": shot["shot"],
                        "frame": frame,
                        "variant": "baseline_normal",
                        "normal_hosel": frame in normal,
                        "baseline_mask": frame in shot["baseline_mask_frames"],
                        "candidate_pixels": 0,
                        "reason": shot["baseline_mask_reasons"].get(
                            frame,
                            shot["baseline_mask_reasons"].get(
                                str(frame),
                                "not_in_impact_window"
                                if shot["baseline_contact"]["status"] == "ok"
                                else shot["baseline_impact_reason"],
                            ),
                        ),
                        "landmarks": "",
                    }
                )
            for variant, values in variants.items():
                for item in values["records"]:
                    writer.writerow(
                        {
                            "shot": shot["shot"],
                            "frame": item["frame"],
                            "variant": variant,
                            "normal_hosel": item["frame"] in normal,
                            "baseline_mask": False,
                            "candidate_pixels": item["mask_pixels"],
                            "reason": item["reason"],
                            "landmarks": json.dumps(item["landmarks"]),
                        }
                    )


def run(package, out):
    out.mkdir(parents=True, exist_ok=True)
    paths, rows, verified = load_package(package)
    report = {
        "bundle_sha256": BUNDLE_SHA256,
        "verified_manifest_members": verified,
        "source_sha256": {
            p.name: digest(p)
            for p in [Path(__file__), Path(__file__).with_name("constrained_masks.py")]
        },
        "environment": {
            "python": platform.python_version(),
            "opencv": cv2.__version__,
            "numpy": np.__version__,
            "scipy": scipy.__version__,
        },
        "shots": [],
    }
    for shot in [r for r in rows if r["type"] == "shot_detected"]:
        number = shot["shot_number"]
        prepare_started = time.perf_counter()
        clip, archive = prepare_shot(paths, rows, number)
        prepare_ms = (time.perf_counter() - prepare_started) * 1000
        updated = next(
            r for r in rows if r["type"] == "shot_updated" and r["shot_number"] == number
        )
        normal = process_camera_fusion(updated["camera_fusion_context"], archive)
        baseline_overlay(archive, clip, normal["club_delivery"], out, number)
        prepared = clip.prepared
        baseline_masks = prepared.masks
        record = {
            "shot": number,
            "baseline_prepare_ms": prepare_ms,
            "capture_sha256": archive["_capture_npz_sha256"],
            "sequence": int(clip.path.name.rsplit("_", 1)[-1]),
            "frame_shape": list(archive["frames"].shape),
            "fps": clip.fps,
            "exposures_us": sorted(np.unique(archive["exposure_us"]).tolist()),
            "normal_delivery": normal["club_delivery"],
            "normal_ball": normal["ball_estimate"],
            "baseline_contact": asdict(prepared.contact),
            "ball": asdict(clip.ball),
            "ball_source": clip.ball_source,
            "range_mm": clip.range_mm,
            "range_source": clip.range_source,
            "baseline_mask_frames": list(baseline_masks.masks) if baseline_masks else [],
            "baseline_window": list(prepared.window),
            "baseline_mask_reasons": baseline_masks.reasons if baseline_masks else {},
            "baseline_impact_reason": il.locate_impact(prepared, None).reason,
        }
        if prepared.contact.status == "ok":
            full_frames = archive["frames"][:, :, ::-1] if prepared.flipped else archive["frames"]
            x0, y0 = prepared.roi_origin
            cx, cy = prepared.camera.principal_point_px
            full_shot = replace(
                prepared,
                frames=np.ascontiguousarray(full_frames),
                roi_origin=(0, 0),
                ball=replace(prepared.ball, x=prepared.ball.x + x0, y=prepared.ball.y + y0),
                camera=replace(prepared.camera, principal_point_px=(cx + x0, cy + y0)),
            )
            record["prototype"] = anchored_candidates(full_shot, archive, out, number)
        else:
            record["prototype"] = {
                "reason": prepared.contact.reason,
                "candidate_frames": [],
                "label": "not_routed_missing_prerequisite",
            }
        report["shots"].append(record)
        (out / "replay.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        head = normal["club_delivery"].get("head_track")
        print(
            number,
            normal["club_delivery"]["status"],
            None if head is None else len(head["track"]),
            record["baseline_impact_reason"],
            flush=True,
        )
    write_frame_table(report, out)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    run(args.package.resolve(), args.out.resolve())
