"""Known-answer calibrated signals through normal offline analysis and relocation."""

import hashlib
import json
import math
import shutil
from dataclasses import asdict
from types import SimpleNamespace

import numpy as np
import pytest

from openflight.camera.calibrated_projection import (
    CalibrationSet,
    build_calibrated_camera_model,
    calibration_pair,
)
from openflight.camera.fusion_processing import build_context
from openflight.camera.geometry_contract import EffectiveCameraGeometryInputs
from openflight.clubs import ClubType
from openflight.rig_geometry import RigGeometry, geometry_fingerprint
from scripts.analysis import analyze_tester_session as analyzer
from scripts.analysis.replay_raw_fusion import replay
from tests import session_fixtures as signals
from tests.camera_helpers import BROWN, calibration_artifact, camera_placement, mode_profile
from tests.test_calibrated_projection import _capture_mode
from tests.test_full_shot_replay import prepared_signals


def calibrated_signals(tmp_path):
    root, session = prepared_signals(tmp_path)
    rows = [json.loads(line) for line in session.read_text().splitlines()]
    config = rows[0]["config"]
    camera_event = next(row for row in rows if row["type"] == "camera_capture")
    capture = next(session.parent.rglob("frames.npz")).parent
    rig = RigGeometry(
        focal_px=500.0,
        image_width=640,
        image_height=400,
        boresight_pitch_deg=0.0,
        ops_offset_mm=None,
        iwr_offset_mm=(0.0, -57.0, 0.0),
        mic_offset_mm=None,
        provenance="synthetic known-answer calibration",
        lens_height_above_floor_mm=95.0,
        iwr_boresight_pitch_deg=None,
        iwr_board_rotation_deg=None,
        ops_boresight_pitch_deg=None,
        housing_tilt_deg=0.0,
        lis3dh_mount_pitch_deg=None,
        lis3dh_mount_roll_deg=None,
        lis3dh_mount_yaw_deg=None,
    )
    parameters = asdict(rig)
    rig_hash = geometry_fingerprint(parameters)
    config["rig_geometry"] = {"snapshot": {"parameters": parameters, "sha256": rig_hash}}
    camera = config["camera_capture"]
    camera.update(width=640, height=400, mount_height_m=0.095, forward_offset_m=0.0)
    camera["lens"] = {"lens_id": "lens-1", "intrinsics": "calibrated"}
    camera.pop("setup_ball", None)
    camera.pop("hitting_zone", None)
    profile = mode_profile()
    profile["crop_readout_mapping"].update(
        scaler_crop="not_applicable_raw_stream", driver_vertical_offset_px=174
    )
    artifact = calibration_artifact(
        profile=profile,
        matrix=[[500.0, 0.0, 307.0], [0.0, 500.0, 191.0], [0, 0, 1]],
        coefficients=dict.fromkeys(BROWN, 0.0),
    )
    placement = camera_placement(
        rig_geometry_sha256=rig_hash,
        enclosure_to_target_lfu=np.eye(3).tolist(),
        camera_origin_lfu=[0.0, 0.0, 0.095],
        radar_origin_lfu=[0.0, 0.0, 0.152],
        reference_pose_deg={"pitch": 0.0, "roll": 0.0},
    )
    pair = calibration_pair(
        artifact, placement, rig_params_sha256=rig_hash, iwr_offset_mm=rig.iwr_offset_mm
    )
    config["camera_calibrated_fusion"] = {
        **CalibrationSet(pairs=(pair,)).record(),
        "enabled": True,
        "in_use": pair.record(),
        "active_mode": "640x400",
        "principal_point": "calibrated",
    }
    model = build_calibrated_camera_model(
        artifact, placement, observed_pitch_deg=0.0, observed_roll_deg=0.0
    )
    frames = np.full((signals.FRAMES, 400, 640), 70, np.uint8)
    yy, xx = np.mgrid[:400, :640]
    elevation, azimuth = math.radians(signals.LAUNCH_DEG), math.radians(2.0)
    velocity = signals.BALL_SPEED_MS * np.array(
        [
            math.cos(elevation) * math.sin(azimuth),
            math.cos(elevation) * math.cos(azimuth),
            math.sin(elevation),
        ]
    )
    for index in range(signals.FRAMES):
        elapsed = max(0.0, (index - signals.PRE_TRIGGER + 0.5) / 120)
        point = np.array([0.0, 1.5, 0.152]) + velocity * elapsed
        optical = model.optical_to_world_lfu.T @ (point - model.camera_origin_lfu)
        x, y = 307 + 500 * optical[0] / optical[2], 191 + 500 * optical[1] / optical[2]
        radius = 250 * 0.0427 / np.linalg.norm(point - model.camera_origin_lfu)
        frames[index][(xx - x) ** 2 + (yy - y) ** 2 <= radius**2] = 230
    with np.load(capture / "frames.npz", allow_pickle=False) as saved:
        archive = {key: saved[key] for key in saved.files}
    archive["frames"] = frames
    np.savez(capture / "frames.npz", **archive)
    metadata = json.loads((capture / "metadata.json").read_text())
    metadata["settings"].update(width=640, height=400, stream="raw", setup_ball=None)
    metadata["settings"].pop("hitting_zone", None)
    mode = _capture_mode(artifact["mode_profile"])
    for key, values in mode["frames"].items():
        mode["frames"][key] = [values[0]] * signals.FRAMES
    metadata["capture_mode"] = mode
    (capture / "metadata.json").write_text(json.dumps(metadata))
    camera_event["metadata"] = metadata
    geometry = EffectiveCameraGeometryInputs.from_live(
        camera, SimpleNamespace(tee_range_m=1.5, radar_height_m=0.152, tee_ball_height_m=0.152)
    ).with_calibrated_model(
        model.snapshot,
        {
            "calibration_pair": pair.record(),
            "mode_profile_sha256": artifact["mode_profile_sha256"],
            "status": "unverified",
            "reasons": ["synthetic known-answer mode; physical calibration unqualified"],
            "accuracy_qualified": False,
        },
    )
    record = next(row for row in rows if row["type"] == "shot_detected")
    record["inclinometer"] = {
        "status": "stable",
        "stable": True,
        "calibrated_pitch_deg": 0.0,
        "x_g": 0.0,
        "y_g": 0.0,
        "z_g": 1.0,
    }
    record["camera_fusion_context"] = build_context(
        geometry=geometry,
        lighting_eligible=True,
        ball_range_evidence=None,
        club_range_evidence=None,
        ops_ball_speed_mph=100.7,
        ops_club_speed_mph=None,
        iwr_vertical_deg=18.0,
        iwr_horizontal_deg=None,
        iwr_horizontal_confidence=None,
        club=ClubType.IRON_7,
        capture_npz_sha256=hashlib.sha256((capture / "frames.npz").read_bytes()).hexdigest(),
        session_uuid=signals.SESSION_UUID,
        shot_number=1,
    )
    session.write_text("".join(json.dumps(row) + "\n" for row in rows))
    return root, session


def test_calibrated_pair_inputs_rebuild_real_camera_and_impact_context(tmp_path):
    root, session = calibrated_signals(tmp_path)
    fresh = replay(analyzer._replay_args(session, 1, root))["stages"]["full_shot"]
    assert fresh["calibration"]["status"] == "restored", fresh
    assert fresh["status"] == "replayed" and fresh["errors"] == {}
    assert len(fresh["metrics"]) == 37
    assert fresh["metrics"]["ball_speed"]["value"] == pytest.approx(100.7, abs=0.3)
    assert fresh["metrics"]["launch_horizontal"]["value"] == pytest.approx(2.0, abs=0.5)
    parameters = fresh["camera_fusion_context"]["geometry"]["parameters"]
    assert parameters["calibrated_model_snapshot"] is not None
    assert parameters["calibrated_model_snapshot"]["artifact"]["camera_matrix"][0][2] == 307.0
    assert parameters["calibrated_mode_evidence"]["accuracy_qualified"] is False
    assert fresh["calibration"]["pair"]["optical_path"] is None
    assert fresh["calibration"]["pair"]["optical_file_sha256"] is None
    assert parameters["calibrated_model_snapshot"]["origins"]["basis"] == (
        "rig_lens_and_iwr_phase_centre"
    )
    optics = fresh["impact"]["optics_input_decision"]
    assert optics["applied"] is True and optics["physical_qualified"] is False
    assert optics["principal_point_px"] == [307.0, 191.0]
    assert fresh["impact"]["reason_code"] == "no_ball_range_for_this_shot"


def test_normal_calibrated_package_reproduces_after_original_tree_is_removed(tmp_path):
    root, _session = calibrated_signals(tmp_path)
    assert analyzer.analyze(root, signals.TESTER, package=True, viewer=None) == 0
    job = json.loads((root / signals.TESTER / "analysis/job.json").read_text())
    bundle = root / job["bundle"]["path"]
    relocated = tmp_path / "relocated"
    relocated.mkdir()
    copied = relocated / bundle.name
    copied.write_bytes(bundle.read_bytes())
    copied.with_suffix(".zip.sha256").write_bytes(bundle.with_suffix(".zip.sha256").read_bytes())
    shutil.rmtree(root)
    verified = analyzer.verify(copied, tmp_path)
    assert verified["status"] == "reproduced", verified["mismatches"]
