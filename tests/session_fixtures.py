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
from types import SimpleNamespace

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
RIG_PARAMETERS = {
    "camera_mount_height_m": 0.095,
    "iwr_board_rotation_deg": 90.0,
    "lis3dh_mount_yaw_deg": 180.0,
}


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


# The synthetic flight: the IWR dump's ball (45 m/s, 18 deg up) leaving a tee 1.5 m
# out at the radar's height, seen from the kiosk's camera block.
FLIGHT_HORIZONTAL_DEG = 2.0
_RADAR_HEIGHT_M = 0.152
_CAMERA_ORIGIN = np.array([0.0, 0.03, 0.095])


def _flight_frames() -> np.ndarray:
    """The resting setup ball, then the flight projected through the camera it implies.

    The camera model is the one the ball-flight estimator infers from the resting
    ball (``reference_ball_camera_model``), so the rendered flight is physically
    consistent with the geometry, the OPS speed and the IWR track.
    """
    from openflight.camera.geometry import reference_ball_camera_model  # noqa: PLC0415

    ball = np.array([0.0, 1.5, _RADAR_HEIGHT_M])
    focal, pitch, _ = reference_ball_camera_model(
        ball_x_px=SETUP_BALL["x"],
        ball_y_px=SETUP_BALL["y"],
        ball_diameter_px=SETUP_BALL["diameter_px"],
        ball_diameter_m=0.04267,
        image_width_px=WIDTH,
        image_height_px=HEIGHT,
        horizontal_pixel_sign=1.0,
        roll_correction_deg=0.0,
        camera_origin_lfu=_CAMERA_ORIGIN,
        radar_origin_lfu=np.array([0.0, 0.0, _RADAR_HEIGHT_M]),
        ball_position_lfu=ball,
    )
    elevation, azimuth = math.radians(LAUNCH_DEG), math.radians(FLIGHT_HORIZONTAL_DEG)
    velocity = BALL_SPEED_MS * np.array(
        [
            math.cos(elevation) * math.sin(azimuth),
            math.cos(elevation) * math.cos(azimuth),
            math.sin(elevation),
        ]
    )
    frames = np.full((FRAMES, HEIGHT, WIDTH), 70, np.uint8)
    rows, columns = np.mgrid[:HEIGHT, :WIDTH]
    for index in range(FRAMES):
        # contact half a frame before the first post-trigger exposure
        elapsed = max(0.0, (index - PRE_TRIGGER + 0.5) * 1 / 120.0)
        offset = ball + velocity * elapsed - _CAMERA_ORIGIN
        forward = offset[1] * math.cos(pitch) + offset[2] * math.sin(pitch)
        up = -offset[1] * math.sin(pitch) + offset[2] * math.cos(pitch)
        x = WIDTH / 2 + focal * offset[0] / forward
        y = HEIGHT / 2 - focal * up / forward
        radius = 0.5 * focal * 0.04267 / float(np.linalg.norm(offset))
        if index < PRE_TRIGGER:
            x, y, radius = SETUP_BALL["x"], SETUP_BALL["y"], SETUP_BALL["diameter_px"] / 2
        frames[index][(columns - x) ** 2 + (rows - y) ** 2 <= radius**2] = 230
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


def _tester_setup_config() -> dict:
    """What a tester setup hands the kiosk: the box, the setup ball and an experimental tee.

    The camera block carries what ``server.init_camera_capture`` records, including
    the setup ball and placement box (P7-8, P7-15); the tee range is the setup's
    experimental save (D11), handed over as ``tester_server`` does.
    """
    return {
        "camera_capture": {
            "fps": 120.0,
            "mount_height_m": 0.095,
            "lateral_offset_m": 0.0,
            "forward_offset_m": 0.03,
            "horizontal_offset_deg": 0.0,
            "roll_correction_deg": 0.0,
            "auto_exposure_enabled": False,
            "setup_ball": dict(SETUP_BALL),
            "hitting_zone": list(SETUP_BOX),
        },
        "iwr6843": {
            "tee_slant_range_m": 1.5,
            "radar_height_m": 0.152,
            "ball_height_m": 0.152,
            "horizontal_phase_reference_rad": None,
        },
        "tee_range_handoff": {
            "tee_slant_range_m": 1.5,
            "status": "configured",
            "source": "unqualified_static_iwr",
            "candidate_id": "iwr-static-fixture",
        },
    }


# Where the synthetic setup saw the resting ball, and the box it confirmed.
SETUP_BALL = {"x": 160.0, "y": 140.0, "diameter_px": 12.0}
SETUP_BOX = (130, 110, 190, 170)


def _tester_capture_metadata(metadata: dict, frames: np.ndarray) -> dict:
    """Trigger readiness bound to the session, and eligibility judged on the setup ball."""
    from openflight.camera.capture_runtime import CameraCaptureRuntime  # noqa: PLC0415

    metadata = json.loads(json.dumps(metadata))
    setup = metadata["tester_setup"]
    setup["required"] = True
    setup["observations"]["runtime"] = {"run_dir": PI_RUN, "session_uuid": SESSION_UUID}
    manual = {
        "status": "manual",
        "analysis_eligible": True,
        "observation": {"status": "good", "clipped_pct": 0.0, "zone_source": "placement_box"},
        "enabled": False,
        "exposure_us": 300,
        "gain": 4.0,
    }
    settings = SimpleNamespace(
        setup_ball=dict(SETUP_BALL), auto_exposure=False, hitting_zone=SETUP_BOX
    )
    # the capture runtime's own judgement when it saves the clip (P7-8)
    runtime = SimpleNamespace(settings=settings)
    metadata["auto_exposure"] = CameraCaptureRuntime._judged_on_setup_ball(  # pylint: disable=protected-access
        runtime, manual, frames
    )
    return metadata


def capture_tree(root: Path, shots=(1, 2), *, tester_setup: bool = False) -> Path:
    """A tester folder; ``tester_setup`` adds what a full tester session records.

    With it the session carries the setup's box, setup ball and experimental tee
    range, the clips' setup-ball eligibility and session-bound trigger readiness,
    and the run's setup admission, so the pipeline check reaches every stage.
    """
    run = root / TESTER / "arm5" / "paired" / "run-01"
    (run / "iwr6843").mkdir(parents=True)
    runtime = _iwr_runtime()
    dump = synth_shot(speed_ms=BALL_SPEED_MS, launch_deg=LAUNCH_DEG, tee_m=1.5, tilt_deg=10.4)
    frames = _flight_frames() if tester_setup else _frames()
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
    if tester_setup:
        # the geometry the kiosk builds from its camera block and IWR calibration
        geometry = EffectiveCameraGeometryInputs.from_live(
            {"width": WIDTH, "height": HEIGHT, **_tester_setup_config()["camera_capture"]},
            SimpleNamespace(tee_range_m=1.5, radar_height_m=0.152, tee_ball_height_m=0.152),
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
                # a qualified setup handed the kiosk its tee (the review needs its source)
                "tee_range_handoff": {
                    "tee_slant_range_m": 1.5,
                    "status": "configured",
                    "source": "qualified_static_iwr",
                    "candidate_id": "iwr-static-fixture",
                },
            },
        }
    ]
    if tester_setup:
        config = events[0]["config"]
        setup = _tester_setup_config()
        config["camera_capture"].update(setup["camera_capture"])
        config["iwr6843"] = setup["iwr6843"]
        config["tee_range_handoff"] = setup["tee_range_handoff"]
        (run / "setup_admission.json").write_text(
            json.dumps(
                {
                    "type": "setup_admission",
                    "tester_id": TESTER,
                    "config_hash": SETUP_HASH,
                    "blockers": [],
                    "warnings": [],
                }
            ),
            encoding="utf-8",
        )
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
        if tester_setup:
            metadata = _tester_capture_metadata(metadata, frames)
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
