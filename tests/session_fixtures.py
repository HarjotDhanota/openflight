"""A synthetic tester folder shaped like the Pi's, with Pi paths in its session log.

Every sensor stage has real evidence to process: OPS I/Q with a ball return, an
IWR6843 dump packed in the firmware wire format with its recorded runtime
snapshot, and camera frames bound to a camera fusion context. Nothing here is a
recording; values are synthetic and redistributable.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np

from openflight import rig_geometry
from openflight.camera.club_delivery import ReferenceBallTracker
from openflight.camera.fusion_processing import build_context
from openflight.camera.geometry_contract import EffectiveCameraGeometryInputs
from openflight.clubs import ClubType
from tests.test_iwr6843_pipeline import synth_shot

TESTER = "pilot-1"
SESSION_UUID = "session-bundle"
PI_RUN = f"/home/pi/openflight_sessions/tester_pilot/{TESTER}/arm5/paired/run-01"
REPO = Path(__file__).resolve().parents[1]
RADAR_CONFIG = REPO / "config" / "iwr6843_l3dump_wide_24f3ms_53bin_iq16.cfg"
BALL_SPEED_MS = 45.0
LAUNCH_DEG = 18.0
WIDTH, HEIGHT, FRAMES, PRE_TRIGGER = 320, 200, 24, 18
SETUP_HASH = "5e" * 32
RIG_PARAMETERS = {"camera_mount_height_m": 0.095, "lis3dh_mount_yaw_deg": 180.0}


def ops_capture(shot: int) -> dict:
    """OPS I/Q whose outbound tone is the synthetic ball's radial speed."""
    tone_hz = 2 * BALL_SPEED_MS / 0.01243
    return {
        "type": "rolling_buffer_capture",
        "shot_number": shot,
        "sample_time": 10.0,
        "trigger_time": 10.1,
        "processor_config": {"sample_rate_hz": 30_000, "club_type": "7-iron"},
        "i_samples": [
            2048 + int(500 * math.cos(2 * math.pi * tone_hz * i / 30_000)) for i in range(4096)
        ],
        "q_samples": [
            2048 + int(500 * math.sin(2 * math.pi * tone_hz * i / 30_000)) for i in range(4096)
        ],
    }


def pgm(path: Path, image: np.ndarray | None = None) -> None:
    if image is None:
        path.write_bytes(b"P5\n8 4\n255\n" + bytes(range(32)))
        return
    height, width = image.shape
    path.write_bytes(f"P5\n{width} {height}\n255\n".encode("ascii") + image.tobytes())


def _iwr_runtime() -> dict:
    calibration = {
        "elem_phase_rad": [0.0] * 8,
        "elem_gain": [1.0] * 8,
        "tilt_deg": 10.4,
        "range_bias_const_m": 0.0,
    }
    config = RADAR_CONFIG.read_text(encoding="utf-8")
    runtime = {
        "net_range_m": 4.0,
        "tdm_sign_policy": "positive",
        "azimuth_offset_deg": 0.0,
        "horizontal_phase_reference_rad": None,
        "club_window_policy": {},
        "club_impact_correction_s": -0.002,
        "recovery_observations": [],
        "calibration": {
            "source_payload": calibration,
            "source_sha256": hashlib.sha256(json.dumps(calibration).encode()).hexdigest(),
            "effective": {
                "tilt_deg": 10.4,
                "tee_slant_range_m": 1.5,
                "radar_height_m": 0.152,
                "ball_height_m": 0.152,
            },
        },
        "radar_config": {
            "source_text": config,
            "source_sha256": hashlib.sha256(config.encode("utf-8")).hexdigest(),
        },
    }
    canonical = json.dumps(runtime, sort_keys=True, separators=(",", ":")).encode("utf-8")
    runtime["sha256"] = hashlib.sha256(canonical).hexdigest()
    return runtime


def _frames() -> np.ndarray:
    """A lit ball resting low in the frame, then leaving up and to the right."""
    frames = np.full((FRAMES, HEIGHT, WIDTH), 70, np.uint8)
    rows, columns = np.mgrid[:HEIGHT, :WIDTH]
    for index in range(FRAMES):
        step = max(0, index - PRE_TRIGGER + 1)
        centre_x, centre_y = 160 + 12 * step, 140 - 6 * step
        frames[index][(columns - centre_x) ** 2 + (rows - centre_y) ** 2 <= 36] = 230
    return frames


def _capture_metadata(capture_path: str) -> dict:
    settings = {
        "width": WIDTH,
        "height": HEIGHT,
        "fps": 120.0,
        "stream": "raw",
        "rotate_180": False,
        "mirror_horizontal": False,
        "exposure_us": 300,
        "gain": 4.0,
        "scaler_crop": None,
        "roll_correction_deg": 0.0,
    }
    startup = {
        "settings": settings,
        "resolved_config": {"raw": {"format": "R8", "size": [WIDTH, HEIGHT]}},
        "driver": {"strip_y_offset": {"value_px": 0}},
    }
    return {
        "frame_count": FRAMES,
        "delivered_fps": 119.6,
        "gap_count": 0,
        "median_interval_ms": 8.33,
        "pre_trigger_frames": PRE_TRIGGER,
        "post_trigger_frames": FRAMES - PRE_TRIGGER,
        "trigger_timestamp": 1790301037.2129,
        "trigger_host_timestamp_ns": 17 * 8_333_333,
        "capture_path": capture_path,
        "settings": settings,
        "settings_scope": "capture_startup",
        "resolved": startup["resolved_config"],
        "capture_mode": {
            "version": 1,
            "context_status": "uniform",
            "contexts": [{"id": "ctx", "startup": startup}],
            "frames": {
                "saved_width": [WIDTH] * FRAMES,
                "saved_height": [HEIGHT] * FRAMES,
            },
        },
        "tester_setup": {
            "config_hash": SETUP_HASH,
            "ready": True,
            "observations": {
                "lis3dh": {"placement_guard": {"warned": False, "pitch_deg": 0.4, "roll_deg": -0.2}}
            },
        },
    }


def capture_tree(root: Path, shots=(1, 2)) -> Path:
    run = root / TESTER / "arm5" / "paired" / "run-01"
    (run / "iwr6843").mkdir(parents=True)
    runtime = _iwr_runtime()
    dump = synth_shot(speed_ms=BALL_SPEED_MS, launch_deg=LAUNCH_DEG, tee_m=1.5, tilt_deg=10.4)
    frames = _frames()
    geometry = EffectiveCameraGeometryInputs(
        camera_height_m=0.095,
        radar_height_m=0.051,
        tee_slant_range_m=1.5,
        ball_height_m=0.021,
        camera_lateral_offset_m=0.0,
        camera_forward_offset_m=0.03,
        image_width_px=WIDTH,
        image_height_px=HEIGHT,
        horizontal_pixel_sign=1.0,
        roll_correction_deg=0.0,
        ball_horizontal_output_offset_deg=0.0,
        ball_diameter_m=0.04267,
    )
    events = [
        {
            "type": "session_start",
            "session_uuid": SESSION_UUID,
            "config": {
                "camera_capture": {
                    "output_dir": f"{PI_RUN}/arm5/camera",
                    "width": WIDTH,
                    "height": HEIGHT,
                    "rotate_180": False,
                    "mirror_horizontal": False,
                },
                "rig_geometry": {
                    "snapshot": {
                        "parameters": RIG_PARAMETERS,
                        "sha256": rig_geometry.geometry_fingerprint(RIG_PARAMETERS),
                    }
                },
            },
        }
    ]
    for shot in shots:
        name = f"camera_00{shot}"
        capture = run / "arm5" / "camera" / name
        capture.mkdir(parents=True)
        np.savez(
            capture / "frames.npz",
            frames=frames,
            sensor_timestamp_ns=np.arange(FRAMES, dtype=np.int64) * 8_333_333,
            host_timestamp_ns=np.arange(FRAMES, dtype=np.int64) * 8_333_333,
            exposure_us=np.full(FRAMES, 300, np.int32),
            analogue_gain=np.full(FRAMES, 4.0, np.float32),
            pre_trigger_count=np.int32(PRE_TRIGGER),
            trigger_host_timestamp_ns=np.int64(17 * 8_333_333),
            trigger_epoch_timestamp=np.float64(1790301037.2129),
        )
        metadata = _capture_metadata(f"{PI_RUN}/arm5/camera/{name}")
        (capture / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
        for label, index in (("first", 0), ("trigger", PRE_TRIGGER - 1), ("last", FRAMES - 1)):
            pgm(capture / f"{label}.pgm", frames[index])
        (run / "iwr6843" / f"iwr_00{shot}.l3dump").write_bytes(dump)
        context = build_context(
            geometry=geometry,
            lighting_eligible=True,
            ball_tracker=ReferenceBallTracker(),
            club_tracker=ReferenceBallTracker(),
            ball_range_evidence=None,
            club_range_evidence=None,
            ops_ball_speed_mph=100.7,
            ops_club_speed_mph=75.0,
            iwr_vertical_deg=LAUNCH_DEG,
            iwr_horizontal_deg=None,
            iwr_horizontal_confidence=None,
            club=ClubType.IRON_7,
            capture_npz_sha256=hashlib.sha256((capture / "frames.npz").read_bytes()).hexdigest(),
            session_uuid=SESSION_UUID,
            shot_number=shot,
        )
        events += [
            ops_capture(shot),
            {
                "type": "shot_detected",
                "shot_number": shot,
                "ball_speed_mph": 100.7,
                "camera_fusion_context": context,
            },
            {
                "type": "camera_capture",
                "shot_number": shot,
                "capture_path": f"{PI_RUN}/arm5/camera/{name}",
                "trigger_timestamp": metadata["trigger_timestamp"],
                "trigger_delta_ms": 1.5,
                "metadata": metadata,
            },
            {
                "type": "iwr6843_capture",
                "shot_number": shot,
                "capture_path": f"{PI_RUN}/iwr6843/iwr_00{shot}.l3dump",
                "capture_bytes": len(dump),
                "runtime_config": runtime,
                "runtime_config_sha256": runtime["sha256"],
            },
        ]
    (run / "session_20260924_185002_arm5.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
    )
    (root / TESTER / "arm5" / "arm.json").write_text('{"gain": 6.0}\n', encoding="utf-8")
    (root / TESTER / "impact").mkdir()
    pgm(root / TESTER / "impact" / "camera_001.pgm")
    (root / TESTER / "ladder.json").write_text(
        json.dumps(
            {"rungs": {}, "photos": {"camera_001": f"/home/pi/x/{TESTER}/impact/camera_001.pgm"}}
        ),
        encoding="utf-8",
    )
    (root / "tester-server.log").write_bytes(b"tester alive\n")
    (root / "tester-server.log.1").write_bytes(b"prior run\n")
    return root
