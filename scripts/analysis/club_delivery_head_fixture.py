#!/usr/bin/env python3
"""Build the club-delivery head-track fixture from logged Oct 27 outdoor shots.

Research only. Each shot is replayed through ``process_camera_fusion`` from its logged
``camera_fusion_context`` (as research/iwr-angle-audit-2026-10-30/head-track did); the
inputs that reach ``estimate_chained_delivery`` are captured and saved with the frames
round impact, so tests/test_camera_club_delivery_field.py can rerun the club stage on
real pixels without the session folder.

Kept per shot: the median of the clip's first 15 frames (the estimator's background),
frames impact-4 .. impact+2 (or the given count after impact) with their clocks, rows above ``KEEP_ROWS`` only (the floor
below the ball is replaced by that background: it never moves and nothing reads it),
the reference ball, trigger index, OPS club speed, delivery geometry and IWR club range
evidence (JSON).

    uv run python scripts/analysis/club_delivery_head_fixture.py RUN_DIR CLIP:SHOT[:AFTER] ... \\
        --out tests/fixtures/club_delivery/outdoor-test-2.npz
"""

from __future__ import annotations

import argparse
import copy
import dataclasses
import hashlib
import io
import json
from pathlib import Path

import numpy as np

from openflight.camera import fusion_processing as fp

BACKGROUND_FRAMES = 15
BEFORE, AFTER = 4, 2
KEEP_ROWS = 230


def _context(run: Path, shot: int) -> dict:
    found = None
    for log in sorted(run.glob("session_*.jsonl")):
        for line in log.read_text(encoding="utf-8").splitlines():
            event = json.loads(line)
            if (
                event.get("type") == "shot_updated"
                and event.get("shot_number") == shot
                and event.get("camera_fusion_context")
            ):
                found = event["camera_fusion_context"]
    if found is None:
        raise SystemExit(f"no camera_fusion_context for shot {shot}")
    return found


def _club_inputs(run: Path, clip: str, shot: int) -> tuple[dict, dict]:
    raw = (run / "arm6" / "camera" / clip / "frames.npz").read_bytes()
    archive = dict(np.load(io.BytesIO(raw), allow_pickle=False))
    archive["_capture_npz_sha256"] = hashlib.sha256(raw).hexdigest()
    context = copy.deepcopy(_context(run, shot))
    context.pop("sha256", None)
    context["sha256"] = fp.geometry_fingerprint(context)
    seen: dict = {}
    real = fp.estimate_chained_delivery

    def spy(frames, host_timestamp_ns, **kwargs):
        result = real(frames, host_timestamp_ns, **kwargs)
        seen.update(kwargs, result=result)
        return result

    fp.estimate_chained_delivery = spy
    try:
        fp.process_camera_fusion(context, archive)
    finally:
        fp.estimate_chained_delivery = real
    return archive, seen


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path, help="arm6/paired/run-01 of the session")
    parser.add_argument(
        "shots", nargs="+", help="clip_dir_name:shot_number[:frames kept after impact]"
    )
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    payload: dict[str, np.ndarray] = {}
    for spec in args.shots:
        clip, shot_text, *after_text = spec.split(":")
        shot = int(shot_text)
        after = int(after_text[0]) if after_text else AFTER
        archive, seen = _club_inputs(args.run, clip, shot)
        frames = archive["frames"]
        impact = int(seen["result"].impact_frame)
        lo, hi = impact - BEFORE, impact + after + 1
        background = np.median(frames[:BACKGROUND_FRAMES], axis=0).astype(np.uint8)
        window = np.repeat(background[None], hi - lo, axis=0)
        window[:, :KEEP_ROWS] = frames[lo:hi, :KEEP_ROWS]
        ball = seen["reference_ball"]
        prefix = f"s{shot}_"
        payload.update(
            {
                prefix + "background": background,
                prefix + "frames": window,
                prefix + "first_frame": np.asarray(lo),
                prefix + "host_timestamp_ns": archive["host_timestamp_ns"][lo:hi],
                prefix + "sensor_timestamp_ns": archive["sensor_timestamp_ns"][lo:hi],
                prefix + "trigger_index": np.asarray(seen["trigger_index"]),
                prefix + "impact_frame": np.asarray(impact),
                prefix + "inputs_json": np.asarray(
                    json.dumps(
                        {
                            "clip": clip,
                            "reference_ball": dataclasses.asdict(ball),
                            "ops_club_speed_mph": seen["ops_club_speed_mph"],
                            "geometry": {
                                key: value
                                for key, value in dataclasses.asdict(seen["geometry"]).items()
                                if key != "calibrated_model"
                            },
                            "calibrated_model": seen["geometry"].calibrated_model is not None,
                            "club_range_evidence": fp._range_snapshot(  # pylint: disable=protected-access
                                seen["range_evidence"], "club"
                            ),
                            "recorded": {
                                "status": seen["result"].status,
                                "attack_angle_deg": seen["result"].attack_angle_deg,
                                "club_path_deg": seen["result"].club_path_deg,
                            },
                        }
                    )
                ),
            }
        )
        print(
            shot,
            clip,
            "impact",
            impact,
            "trigger",
            seen["trigger_index"],
            seen["result"].status,
            seen["result"].attack_angle_deg,
            seen["result"].club_path_deg,
        )
    payload["shots"] = np.asarray([int(spec.split(":")[1]) for spec in args.shots])
    args.out.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(args.out, **payload)
    print(args.out, args.out.stat().st_size, "bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
