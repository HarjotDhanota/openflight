import copy
import hashlib
import json
from datetime import datetime
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from openflight import server as server_module
from openflight.camera import fusion_processing
from openflight.camera.ball_flight import (
    BallCandidate,
    CameraBallGeometry,
    _project,
    estimate_camera_ball_flight,
)
from openflight.camera.calibrated_projection import (
    build_calibrated_camera_model,
    calibrated_camera_model_from_snapshot,
    inspect_capture_compatibility,
    load_calibration_candidate,
)
from openflight.camera.capture_runtime import _capture_mode_metadata
from openflight.camera.club_delivery import (
    CameraDeliveryGeometry,
    ChainedDelivery,
    ReferenceBallTracker,
    _pixels_to_world,
)
from openflight.camera.fusion_sensitivity import run_sensitivity
from openflight.camera.geometry import unit_world_rays
from openflight.camera.geometry_contract import EffectiveCameraGeometryInputs
from openflight.camera.optical_calibration import validate_mode_profile
from openflight.camera.triggered_buffer import CameraFrame
from openflight.clubs import ClubType
from openflight.iwr6843.lcmf import BallRangeEvidence
from openflight.iwr6843.tracking import BallTrack, Geometry
from openflight.launch_monitor import Shot


def _profile(*, mirror=False, rotate=False):
    return {
        "version": 1,
        "camera_id": "camera-1",
        "lens_id": "lens-1",
        "focus_id": "fixed-1",
        "sensor_output": {
            "width": 1280,
            "height": 800,
            "bit_depth": 10,
            "raw_format": "SBGGR10_CSI2P",
            "mode_id": "mode-1",
        },
        "saved_image": {
            "width": 640,
            "height": 400,
            "stream": "raw",
            "rotate_180": rotate,
            "mirror": mirror,
        },
        "crop_readout_mapping": {
            "native_sensor_crop": "unknown",
            "scaler_crop": "unknown",
            "driver_vertical_offset_px": "unknown",
            "sensor_output_mapping": "unknown",
            "saved_image_mapping": "unknown",
        },
    }


def _artifact(profile=None, matrix=None, coefficients=None):
    profile = validate_mode_profile(profile or _profile())
    coefficients = coefficients or dict(
        zip(("k1", "k2", "p1", "p2", "k3"), (-0.12, 0.025, 0.001, -0.0008, -0.004))
    )
    return {
        "version": 1,
        "status": "candidate",
        "mode_profile": profile,
        "mode_profile_sha256": profile["sha256"],
        "camera_matrix": (matrix or [[610.0, 0.0, 307.0], [0.0, 600.0, 191.0], [0, 0, 1]]),
        "distortion_model": "opencv_brown_5",
        "distortion_convention": {
            "coefficient_order": ["k1", "k2", "p1", "p2", "k3"],
            "coordinates": "OpenCV normalized camera coordinates",
        },
        "distortion_coefficients": coefficients,
    }


def _placement(**changes):
    placement = {
        "schema": "openflight.camera.placement",
        "version": 1,
        "world_frame": "target_lfu",
        "rig_geometry_sha256": "rig-hash",
        "optical_to_enclosure_lfu": [[1, 0, 0], [0, 0, 1], [0, -1, 0]],
        "enclosure_to_target_lfu": [[0, -1, 0], [1, 0, 0], [0, 0, 1]],
        "camera_origin_lfu": [0.08, 0.04, 0.13],
        "radar_origin_lfu": [-0.02, 0.0, 0.05],
        "enclosure_pivot_lfu": [0.0, 0.0, 0.0],
        "reference_pose_deg": {"pitch": 7.0, "roll": -3.0},
        "origin_provenance": {
            "camera_origin_lfu": "measured optical center",
            "radar_origin_lfu": "measured antenna center",
            "enclosure_pivot_lfu": "declared front foot contact",
        },
        "rig_offset_consistency_tolerance_m": 0.002,
    }
    placement.update(changes)
    return placement


def _rotation(axis, degrees):
    angle = np.radians(degrees)
    sine, cosine = np.sin(angle), np.cos(angle)
    if axis == "pitch":
        return np.array([[1, 0, 0], [0, cosine, -sine], [0, sine, cosine]])
    return np.array([[cosine, 0, sine], [0, 1, 0], [-sine, 0, cosine]])


def _inclination_attitude(pitch, roll):
    gravity = np.array(
        [
            np.sin(np.radians(roll)),
            np.sin(np.radians(pitch)),
            np.sqrt(1 - np.sin(np.radians(roll)) ** 2 - np.sin(np.radians(pitch)) ** 2),
        ]
    )
    x_angle = np.arcsin(gravity[1])
    y_angle = np.arctan2(-gravity[0], gravity[2])
    attitude = _rotation("pitch", np.degrees(x_angle)) @ _rotation("roll", np.degrees(y_angle))
    np.testing.assert_allclose(attitude.T @ np.array([0.0, 0.0, 1.0]), gravity)
    return attitude


def test_full_placement_applies_observed_pose_about_declared_pivot_and_replays():
    placement = _placement()
    model = build_calibrated_camera_model(
        _artifact(), placement, observed_pitch_deg=11.0, observed_roll_deg=2.0
    )
    alignment = np.asarray(placement["enclosure_to_target_lfu"], dtype=float)
    reference = _inclination_attitude(7.0, -3.0)
    observed = _inclination_attitude(11.0, 2.0)
    delta = alignment @ reference.T @ observed @ alignment.T
    np.testing.assert_allclose(
        model.camera_origin_lfu,
        delta @ np.asarray(placement["camera_origin_lfu"]),
        atol=1e-12,
    )
    restored = calibrated_camera_model_from_snapshot(model.snapshot)
    np.testing.assert_allclose(restored.optical_to_world_lfu, model.optical_to_world_lfu)
    assert restored.snapshot["sha256"] == model.snapshot["sha256"]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"rig_geometry_sha256": ""}, "rig_geometry_sha256"),
        ({"origin_provenance": {}}, "origin_provenance"),
        ({"world_frame": "camera_rdf"}, "target_lfu"),
        ({"rig_offset_consistency_tolerance_m": -1}, "consistency tolerance"),
    ],
)
def test_full_placement_rejects_missing_explicit_contract(change, message):
    with pytest.raises(ValueError, match=message):
        build_calibrated_camera_model(
            _artifact(), _placement(**change), observed_pitch_deg=7.0, observed_roll_deg=-3.0
        )


def test_full_placement_requires_live_pose_instead_of_assuming_reference():
    with pytest.raises(ValueError, match="observed pitch and roll"):
        build_calibrated_camera_model(_artifact(), _placement())


def test_full_placement_snapshot_is_detached_and_rejects_non_upright_aliases():
    artifact = _artifact()
    placement = _placement()
    model = build_calibrated_camera_model(
        artifact, placement, observed_pitch_deg=7.0, observed_roll_deg=-3.0
    )
    artifact["camera_matrix"][0][0] = 1.0
    placement["camera_origin_lfu"][0] = 99.0
    assert model.snapshot["artifact"]["camera_matrix"][0][0] == 610.0
    assert model.snapshot["placement"]["camera_origin_lfu"][0] == 0.08
    with pytest.raises(ValueError, match="upright"):
        build_calibrated_camera_model(
            _artifact(), _placement(), observed_pitch_deg=180.0, observed_roll_deg=0.0
        )


def test_ball_and_club_estimators_use_the_same_calibrated_full_placement():
    artifact = _artifact(
        matrix=[[500.0, 0.0, 320.0], [0.0, 500.0, 200.0], [0, 0, 1]],
        coefficients=dict(zip(("k1", "k2", "p1", "p2", "k3"), [0] * 5)),
    )
    placement = _placement(
        enclosure_to_target_lfu=[[1, 0, 0], [0, 1, 0], [0, 0, 1]],
        camera_origin_lfu=[0.08, 0.04, 0.13],
        radar_origin_lfu=[-0.02, 0.0, 0.05],
        reference_pose_deg={"pitch": 0.0, "roll": 0.0},
    )
    model = build_calibrated_camera_model(
        artifact, placement, observed_pitch_deg=0.0, observed_roll_deg=0.0
    )
    point = np.array([-0.11, 1.3, 0.19])
    ray_rdf = model.optical_to_world_lfu.T @ (
        (point - model.camera_origin_lfu) / np.linalg.norm(point - model.camera_origin_lfu)
    )
    pixel = np.array([500 * ray_rdf[0] / ray_rdf[2] + 320, 500 * ray_rdf[1] / ray_rdf[2] + 200])
    radar_range = np.linalg.norm(point - model.radar_origin_lfu)
    ball_geometry = CameraBallGeometry(0.13, 0.05, 1.5, 0.021, calibrated_model=model)
    candidate = BallCandidate(pixel[0], pixel[1], 20, 5, 5, 0.8, 0.9, 200)
    actual_ball = _project(
        candidate, radar_range, model=(1, 0, np.zeros(3)), geometry=ball_geometry
    )
    delivery_geometry = CameraDeliveryGeometry(0.13, 0.05, 1.5, 0.021, calibrated_model=model)
    actual_club = _pixels_to_world(
        pixel[None, :], radar_range, ball=None, geometry=delivery_geometry
    )[0]
    np.testing.assert_allclose(actual_ball, point, atol=1e-10)
    np.testing.assert_allclose(actual_club, point[[0, 2, 1]], atol=1e-10)


def test_effective_geometry_freezes_calibrated_model_for_replay():
    model = build_calibrated_camera_model(
        _artifact(), _placement(), observed_pitch_deg=9.0, observed_roll_deg=-1.0
    )
    geometry = EffectiveCameraGeometryInputs(
        camera_height_m=0.095,
        radar_height_m=0.051,
        tee_slant_range_m=1.5,
        ball_height_m=0.021,
        camera_lateral_offset_m=0.0,
        camera_forward_offset_m=0.03,
        image_width_px=640,
        image_height_px=400,
        horizontal_pixel_sign=1.0,
        roll_correction_deg=0.0,
        ball_horizontal_output_offset_deg=0.0,
        ball_diameter_m=0.04267,
    ).with_calibrated_model(
        model.snapshot,
        {
            "status": "unverified",
            "reasons": ["calibration remains unqualified"],
            "mode_profile_sha256": model.snapshot["artifact"]["mode_profile_sha256"],
        },
    )
    frozen = geometry.snapshot()
    restored = EffectiveCameraGeometryInputs.from_recorded_session(
        {"effective_camera_geometry": frozen}
    )
    pixel = np.array([320.0, 200.0])
    np.testing.assert_allclose(
        restored.calibrated_model().rays(pixel), geometry.calibrated_model().rays(pixel)
    )
    assert restored.calibrated_mode_evidence["status"] == "unverified"

    context = fusion_processing.build_context(
        geometry=geometry,
        lighting_eligible=True,
        ball_tracker=ReferenceBallTracker(),
        club_tracker=ReferenceBallTracker(),
        ball_range_evidence=None,
        club_range_evidence=None,
        ops_ball_speed_mph=100.0,
        ops_club_speed_mph=75.0,
        iwr_vertical_deg=18.0,
        iwr_horizontal_deg=1.0,
        iwr_horizontal_confidence=0.8,
        club=ClubType.IRON_7,
        capture_npz_sha256="capture-hash",
        session_uuid="session-a",
        shot_number=1,
    )
    archive = {
        "frames": np.zeros((4, 400, 640), np.uint8),
        "host_timestamp_ns": np.arange(4, dtype=np.int64),
        "trigger_host_timestamp_ns": np.asarray(1, dtype=np.int64),
        "pre_trigger_count": np.asarray(2, dtype=np.int64),
        "_capture_npz_sha256": "capture-hash",
    }
    first = fusion_processing.process_camera_fusion(context, archive)
    replay = fusion_processing.process_camera_fusion(context, archive)
    assert first == replay
    assert first["ball_estimate"]["status"] == "rejected_calibrated_requires_iwr_range"


def test_calibrated_ball_candidate_withholds_without_recorded_iwr_range():
    model = build_calibrated_camera_model(
        _artifact(), _placement(), observed_pitch_deg=7.0, observed_roll_deg=-3.0
    )
    geometry = CameraBallGeometry(0.13, 0.05, 1.5, 0.021, calibrated_model=model)
    result = estimate_camera_ball_flight(
        np.zeros((4, 400, 640), np.uint8),
        np.arange(4, dtype=np.int64),
        trigger_ns=1,
        range_evidence=None,
        geometry=geometry,
        ops_ball_speed_mph=100.0,
    )
    assert result.status == "rejected_calibrated_requires_iwr_range"


def test_server_initializes_valid_calibration_and_rig_consistent_placement(tmp_path, monkeypatch):
    monkeypatch.setattr(server_module, "camera_optical_calibration", None)
    monkeypatch.setattr(server_module, "camera_placement", None)
    artifact = _artifact()
    placement = _placement(rig_geometry_sha256="loaded-rig")
    mount = np.asarray(placement["optical_to_enclosure_lfu"], dtype=float)
    alignment = np.asarray(placement["enclosure_to_target_lfu"], dtype=float)
    declared = np.asarray(placement["radar_origin_lfu"]) - np.asarray(
        placement["camera_origin_lfu"]
    )
    rig_offset_rdf_mm = mount.T @ alignment.T @ declared * 1000.0
    monkeypatch.setattr(
        server_module,
        "rig_geometry",
        type("Rig", (), {"iwr_offset_mm": tuple(rig_offset_rdf_mm)})(),
    )
    monkeypatch.setattr(
        server_module,
        "rig_geometry_config",
        {"snapshot": {"sha256": "loaded-rig"}},
    )
    artifact_path = tmp_path / "optical.json"
    placement_path = tmp_path / "placement.json"
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
    placement_path.write_text(json.dumps(placement), encoding="utf-8")
    server_module.init_camera_calibrated_fusion(artifact_path, placement_path)
    assert (
        server_module.camera_optical_calibration["mode_profile_sha256"]
        == artifact["mode_profile_sha256"]
    )
    assert server_module.camera_placement["rig_geometry_sha256"] == "loaded-rig"


def test_live_adapter_and_replay_share_successful_calibrated_projection(tmp_path, monkeypatch):
    import openflight.camera.ball_flight as ball_module
    import openflight.camera.fusion_processing as fusion_module

    profile = _profile()
    profile["crop_readout_mapping"].update(
        scaler_crop="not_applicable_raw_stream", driver_vertical_offset_px=174
    )
    artifact = _artifact(
        profile=profile, coefficients=dict(zip(("k1", "k2", "p1", "p2", "k3"), [0] * 5))
    )
    placement = _placement(
        enclosure_to_target_lfu=[[1, 0, 0], [0, 1, 0], [0, 0, 1]],
        camera_origin_lfu=[0.0, 0.0, 0.095],
        radar_origin_lfu=[0.0, -0.03, 0.051],
        enclosure_pivot_lfu=[0.0, 0.0, 0.0],
        reference_pose_deg={"pitch": 0.0, "roll": 0.0},
    )
    model = build_calibrated_camera_model(
        artifact, placement, observed_pitch_deg=0.0, observed_roll_deg=0.0
    )
    times = np.arange(12, dtype=float) * 0.0035
    trigger_index = 2
    relative = times - times[trigger_index]
    velocity = np.array([3.0, 44.0, 10.0])
    tee = np.array([0.0, 1.5, 0.021])
    points = np.asarray([tee + velocity * item for item in relative])
    pixels = []
    candidates = []
    for point in points:
        ray = model.optical_to_world_lfu.T @ (
            (point - model.camera_origin_lfu) / np.linalg.norm(point - model.camera_origin_lfu)
        )
        pixel = np.array(
            [
                artifact["camera_matrix"][0][0] * ray[0] / ray[2] + artifact["camera_matrix"][0][2],
                artifact["camera_matrix"][1][1] * ray[1] / ray[2] + artifact["camera_matrix"][1][2],
            ]
        )
        pixels.append(pixel)
        candidates.append(BallCandidate(pixel[0], pixel[1], 80, 10, 10, 0.8, 0.9, 220))
    frames = np.zeros((12, 400, 640), np.uint8)
    frames[:, 0, 0] = np.arange(12)
    timestamps = np.asarray(times * 1e9, dtype=np.int64)
    radar_ranges = np.linalg.norm(points - model.radar_origin_lfu, axis=1)
    range_res = 6.0 / 128
    slope, intercept = np.polyfit(relative, radar_ranges / range_res, 1)
    track = BallTrack(45.1, slope, intercept, 0.1, 12, relative[0], relative[-1], False)
    radar_geometry = Geometry(12, 8, 2, 4, 128, 0.0035, trigger_index)
    evidence = BallRangeEvidence(track, radar_geometry, 0.0)
    anchor = SimpleNamespace(
        x=float(pixels[trigger_index][0]),
        y=float(pixels[trigger_index][1]),
        diameter_px=14.0,
        area_px=140,
    )
    monkeypatch.setattr(ball_module, "detect_reference_ball", lambda _frames: anchor)
    monkeypatch.setattr(
        ball_module,
        "_candidates",
        lambda frame, *_args, **_kwargs: [candidates[int(frame[0, 0])]],
    )
    monkeypatch.setattr(
        ball_module,
        "_pixel_paths",
        lambda nodes, _anchor: [[(index, node[0]) for index, node in enumerate(nodes)]],
    )
    monkeypatch.setattr(
        fusion_module,
        "estimate_chained_delivery",
        lambda *_args, **_kwargs: ChainedDelivery(status="rejected_test_club"),
    )
    capture_dir = tmp_path / "camera_1"
    capture_dir.mkdir()
    np.savez(
        capture_dir / "frames.npz",
        frames=frames,
        host_timestamp_ns=timestamps,
        trigger_host_timestamp_ns=np.int64(timestamps[trigger_index]),
        pre_trigger_count=np.int32(trigger_index + 1),
    )
    startup = _capture_mode(validate_mode_profile(profile))["contexts"][0]["startup"]
    mode_frames = tuple(
        CameraFrame(
            frames[index],
            index,
            index,
            1000,
            2.0,
            frame_duration_us=3500,
            capture_mode=startup,
        )
        for index in range(12)
    )
    capture = SimpleNamespace(
        valid=True,
        path=capture_dir,
        metadata={"capture_mode": _capture_mode_metadata(mode_frames)},
    )
    monkeypatch.setattr(server_module, "camera_optical_calibration", artifact)
    monkeypatch.setattr(server_module, "camera_placement", placement)
    monkeypatch.setattr(server_module, "tester_setup_required", False)
    monkeypatch.setattr(
        server_module, "camera_capture_runtime", SimpleNamespace(camera_analysis_eligible=True)
    )
    monkeypatch.setattr(
        server_module,
        "camera_capture_config",
        {"mount_height_m": 0.095, "width": 640, "height": 400},
    )
    monkeypatch.setattr(
        server_module,
        "iwr6843_runtime",
        SimpleNamespace(
            calibration=SimpleNamespace(
                tee_range_m=1.5, radar_height_m=0.051, tee_ball_height_m=0.021
            )
        ),
    )
    monkeypatch.setattr(
        server_module, "camera_ball_flight_reference_tracker", ReferenceBallTracker()
    )
    monkeypatch.setattr(server_module, "camera_reference_ball_tracker", ReferenceBallTracker())
    shot = Shot(
        ball_speed_mph=float(np.linalg.norm(velocity) * 2.23694),
        timestamp=datetime.now(),
        shot_number=1,
        iwr6843_horizontal_deg=3.9,
        iwr6843_horizontal_confidence=0.8,
        iwr6843_ball_range_evidence=evidence,
        inclinometer={
            "status": "stable",
            "stable": True,
            "calibrated_pitch_deg": 0.0,
            "x_g": 0.0,
            "y_g": 0.0,
            "z_g": 1.0,
        },
    )
    shot.camera_fusion_session_uuid = "session-a"
    server_module._fuse_camera_measurements(shot, capture)
    archive = server_module._load_camera_capture_archive(capture)
    replay = fusion_module.process_camera_fusion(shot.camera_fusion_context, archive)
    assert shot.calibrated_camera_status == "experimental_unqualified"
    assert shot.camera_fusion_processing == replay
    assert replay["ball_estimate"]["status"] == "accepted"
    assert replay["ball_estimate"]["horizontal_deg"] == pytest.approx(3.9, abs=0.5)
    frozen = {"_context": shot.camera_fusion_context, "_archive": archive, "replay": replay}
    original_context = copy.deepcopy(shot.camera_fusion_context)
    sensitivity = run_sensitivity(
        frozen,
        {
            "schema_version": 1,
            "variants": [
                {"id": "zero", "kind": "calibrated_focal_scale", "scale": 1.0},
                {"id": "focal", "kind": "calibrated_focal_scale", "scale": 1.1},
                {
                    "id": "origin",
                    "kind": "camera_translation_m",
                    "lateral_m": 0.02,
                    "forward_m": 0.0,
                    "height_m": 0.0,
                },
                {"id": "no-depth", "kind": "remove_ball_range_evidence"},
            ],
        },
    )
    variants = {item["id"]: item for item in sensitivity["variants"]}
    assert variants["zero"]["result"] == replay
    assert variants["focal"]["result"]["ball_estimate"]["horizontal_deg"] != pytest.approx(
        replay["ball_estimate"]["horizontal_deg"], abs=1e-6
    )
    assert variants["origin"]["result"]["ball_estimate"]["horizontal_deg"] != pytest.approx(
        replay["ball_estimate"]["horizontal_deg"], abs=1e-6
    )
    assert variants["no-depth"]["result"]["ball_estimate"]["status"] != "accepted"
    assert shot.camera_fusion_context == original_context

    baseline = []
    monkeypatch.setattr(
        server_module, "_fuse_camera_ball_flight", lambda *_args: baseline.append("ball")
    )
    monkeypatch.setattr(
        server_module, "_fuse_camera_club_delivery", lambda *_args: baseline.append("club")
    )
    inverted = Shot(
        ball_speed_mph=shot.ball_speed_mph,
        timestamp=datetime.now(),
        shot_number=2,
        inclinometer={
            "status": "stable",
            "stable": True,
            "calibrated_pitch_deg": 0.0,
            "x_g": 0.0,
            "y_g": 0.0,
            "z_g": -1.0,
        },
    )
    inverted.camera_fusion_session_uuid = "session-a"
    server_module._fuse_camera_measurements(inverted, capture)
    assert inverted.calibrated_camera_status == "rejected"
    assert "upside down" in inverted.calibrated_camera_reason
    assert baseline == ["ball", "club"]


@pytest.mark.parametrize(
    ("pose", "reason"),
    [
        (
            {
                "status": "moving",
                "stable": False,
                "calibrated_pitch_deg": 0.0,
                "x_g": 0.0,
                "y_g": 0.0,
                "z_g": 1.0,
            },
            "stable LIS3DH",
        ),
        (
            {
                "status": "stale",
                "stable": True,
                "calibrated_pitch_deg": 0.0,
                "x_g": 0.0,
                "y_g": 0.0,
                "z_g": 1.0,
            },
            "stable LIS3DH",
        ),
        (
            {
                "status": "stable",
                "stable": True,
                "calibrated_pitch_deg": 0.0,
                "x_g": 0.0,
                "y_g": 0.0,
                "z_g": -1.0,
            },
            "upside down",
        ),
    ],
)
def test_calibrated_pose_rejects_unstable_stale_and_inverted_readings(pose, reason):
    with pytest.raises(ValueError, match=reason):
        server_module._calibrated_pose_evidence(  # pylint: disable=protected-access
            pose, _placement(reference_pose_deg={"pitch": 0.0, "roll": 0.0})
        )


def _physical_to_saved_artifact(mirror, rotate):
    width, height = 640, 400
    matrix = np.array([[610.0, 0.0, 307.0], [0.0, 600.0, 191.0], [0, 0, 1.0]])
    distortion = np.array([-0.12, 0.025, 0.001, -0.0008, -0.004])
    saved_matrix = matrix.copy()
    saved_distortion = distortion.copy()
    if rotate:
        saved_matrix[0, 2] = width - 1 - saved_matrix[0, 2]
        saved_matrix[1, 2] = height - 1 - saved_matrix[1, 2]
        saved_distortion[2:4] *= -1
    if mirror:
        saved_matrix[0, 2] = width - 1 - saved_matrix[0, 2]
        saved_distortion[3] *= -1
    profile = _profile(mirror=mirror, rotate=rotate)
    artifact = _artifact(
        profile,
        saved_matrix.tolist(),
        dict(zip(("k1", "k2", "p1", "p2", "k3"), saved_distortion)),
    )
    return artifact, matrix, distortion


@pytest.mark.parametrize("mirror", [False, True])
@pytest.mark.parametrize("rotate", [False, True])
def test_saved_orientation_brown_projection_recovers_physical_rays(mirror, rotate):
    artifact, matrix, distortion = _physical_to_saved_artifact(mirror, rotate)
    physical = np.array([[0.13, -0.08, 1.0], [-0.18, 0.12, 1.0]])
    pixels = cv2.projectPoints(
        physical,
        np.zeros(3),
        np.zeros(3),
        matrix,
        distortion,
    )[0].reshape(-1, 2)
    if rotate:
        pixels = np.array([639.0, 399.0]) - pixels
    if mirror:
        pixels[:, 0] = 639.0 - pixels[:, 0]
    rotation = cv2.Rodrigues(np.array([0.08, -0.16, 0.21]))[0]

    projection = load_calibration_candidate(artifact, mode_profile=artifact["mode_profile"])
    actual = projection.pixel_rays_lfu(pixels, optical_to_world_lfu=rotation)
    expected = physical @ rotation.T
    expected /= np.linalg.norm(expected, axis=1, keepdims=True)
    np.testing.assert_allclose(actual, expected, atol=2e-9)


def test_known_point_reconstruction_with_offset_origins_and_rotation():
    artifact = _artifact(coefficients=dict(zip(("k1", "k2", "p1", "p2", "k3"), [0] * 5)))
    projection = load_calibration_candidate(artifact, mode_profile=artifact["mode_profile"])
    rotation = np.array([[1.0, 0.0, 0.0], [0.0, 0.0, 1.0], [0.0, -1.0, 0.0]])
    camera = np.array([0.08, 0.04, 0.13])
    radar = np.array([-0.02, 0.0, 0.05])
    point = np.array([-0.11, 1.3, 0.19])
    rdf = rotation.T @ ((point - camera) / np.linalg.norm(point - camera))
    pixel = np.array([610 * rdf[0] / rdf[2] + 307, 600 * rdf[1] / rdf[2] + 191])
    actual = projection.reconstruct_at_radar_range(
        pixel,
        np.linalg.norm(point - radar),
        optical_to_world_lfu=rotation,
        camera_origin_lfu=camera,
        radar_origin_lfu=radar,
    )
    np.testing.assert_allclose(actual, point, atol=1e-10)


def test_zero_distortion_matches_legacy_ray_for_equivalent_model():
    artifact = _artifact(
        matrix=[[500.0, 0.0, 320.0], [0.0, 500.0, 200.0], [0, 0, 1]],
        coefficients=dict(zip(("k1", "k2", "p1", "p2", "k3"), [0] * 5)),
    )
    pixel = np.array([271.0, 176.0])
    rotation = np.array([[1.0, 0.0, 0.0], [0.0, 0.0, 1.0], [0.0, -1.0, 0.0]])
    projection = load_calibration_candidate(artifact, mode_profile=artifact["mode_profile"])
    actual = projection.pixel_rays_lfu(pixel, optical_to_world_lfu=rotation)
    expected = unit_world_rays(
        pixel,
        focal_px=500,
        pitch_rad=0,
        image_width_px=640,
        image_height_px=400,
        horizontal_pixel_sign=1,
        roll_correction_deg=0,
    )
    np.testing.assert_allclose(actual, expected, atol=1e-12)


@pytest.mark.parametrize(
    "change,match",
    [
        (lambda item: item.update(version=2), "version"),
        (lambda item: item.update(status="failed"), "status"),
        (lambda item: item["camera_matrix"].__setitem__(0, [610, 1, 307]), "canonical"),
        (lambda item: item["camera_matrix"][0].__setitem__(0, float("nan")), "finite"),
        (lambda item: item["distortion_coefficients"].update(k1=float("nan")), "finite"),
    ],
)
def test_loader_rejects_invalid_candidates(change, match):
    artifact = _artifact()
    change(artifact)
    with pytest.raises(ValueError, match=match):
        load_calibration_candidate(artifact, mode_profile=artifact["mode_profile"])


def test_loader_rejects_profile_mismatch_and_brown_order():
    artifact = _artifact()
    other = copy.deepcopy(artifact["mode_profile"])
    other.pop("sha256")
    other["focus_id"] = "other-focus"
    with pytest.raises(ValueError, match="exactly match"):
        load_calibration_candidate(artifact, mode_profile=other)
    artifact["distortion_coefficients"] = dict(
        reversed(list(artifact["distortion_coefficients"].items()))
    )
    load_calibration_candidate(artifact, mode_profile=artifact["mode_profile"])


def test_loader_accepts_sorted_cli_candidate_wrapper(tmp_path):
    artifact = _artifact()
    wrapper = {
        "version": 1,
        "candidate": artifact,
        "reason": None,
        "mode_profile_sha256": artifact["mode_profile_sha256"],
    }
    path = tmp_path / "camera_intrinsics_candidate.json"
    path.write_text(json.dumps(wrapper, sort_keys=True), encoding="utf-8")
    projection = load_calibration_candidate(path, mode_profile=artifact["mode_profile"])
    assert projection.image_size == (640, 400)


def test_projection_rejects_out_of_bounds_rotation_and_failed_inversion(monkeypatch):
    artifact = _artifact()
    projection = load_calibration_candidate(artifact, mode_profile=artifact["mode_profile"])
    with pytest.raises(ValueError, match="outside"):
        projection.pixel_rays_lfu([640, 0], optical_to_world_lfu=np.eye(3))
    with pytest.raises(ValueError, match="proper"):
        projection.pixel_rays_lfu([300, 200], optical_to_world_lfu=np.diag([-1, 1, 1]))
    inversion_name = (
        "undistortPointsIter" if hasattr(cv2, "undistortPointsIter") else "undistortPoints"
    )
    monkeypatch.setattr(cv2, inversion_name, lambda *args, **kwargs: np.full((1, 1, 2), np.nan))
    with pytest.raises(ValueError, match="non-finite"):
        projection.pixel_rays_lfu([300, 200], optical_to_world_lfu=np.eye(3))


def test_opencv_five_inversion_path_receives_explicit_criteria(monkeypatch):
    artifact = _artifact()
    projection = load_calibration_candidate(artifact, mode_profile=artifact["mode_profile"])
    observed = {}

    def record_criteria(*args, **kwargs):
        observed["criteria"] = kwargs.get("criteria")
        return np.zeros((1, 1, 2), dtype=float)

    monkeypatch.delattr(cv2, "undistortPointsIter", raising=False)
    monkeypatch.setattr(cv2, "undistortPoints", record_criteria)
    projection.pixel_rays_lfu([307, 191], optical_to_world_lfu=np.eye(3))
    assert observed["criteria"] == (
        cv2.TERM_CRITERIA_COUNT | cv2.TERM_CRITERIA_EPS,
        50,
        1e-12,
    )


def test_projection_rejects_singular_or_folded_distortion():
    artifact = _artifact(coefficients=dict(zip(("k1", "k2", "p1", "p2", "k3"), [-4.0, 0, 0, 0, 0])))
    projection = load_calibration_candidate(artifact, mode_profile=artifact["mode_profile"])
    with pytest.raises(ValueError, match="converge|folded"):
        projection.pixel_rays_lfu([600, 191], optical_to_world_lfu=np.eye(3))


def _capture_mode(profile):
    startup = {
        "settings": {
            "width": 640,
            "height": 400,
            "stream": "raw",
            "rotate_180": False,
            "mirror_horizontal": False,
            "scaler_crop": None,
        },
        "resolved_config": {
            "raw": {"size": [1280, 800], "format": "SBGGR10_CSI2P"},
            "sensor": {"output_size": [1280, 800], "bit_depth": 10},
        },
        "driver": {"strip_y_offset": {"value_px": 174, "scope": "startup"}},
    }
    encoded = json.dumps(startup, sort_keys=True, separators=(",", ":"))
    return {
        "version": 1,
        "binding": "unverified",
        "context_status": "uniform",
        "contexts": [{"id": hashlib.sha256(encoded.encode()).hexdigest(), "startup": startup}],
        "frames": {
            "context_index": [0, 0],
            "scaler_crop": [None, None],
            "frame_duration_us": [2500, 2500],
            "saved_width": [profile["saved_image"]["width"]] * 2,
            "saved_height": [profile["saved_image"]["height"]] * 2,
        },
    }


def test_capture_evidence_can_only_be_unverified_or_incompatible():
    profile = _profile()
    capture = _capture_mode(profile)
    result = inspect_capture_compatibility(capture, mode_profile=profile, frame_count=2)
    assert result.status == "unverified"
    assert any("identity" in reason for reason in result.reasons)

    capture["frames"]["saved_width"][1] = 641
    result = inspect_capture_compatibility(capture, mode_profile=profile, frame_count=2)
    assert result.status == "incompatible"
    assert any("dimensions" in reason for reason in result.reasons)


def test_capture_evidence_rejects_hash_index_alignment_and_known_mismatches():
    profile = _profile()
    capture = _capture_mode(profile)
    capture["contexts"][0]["id"] = "0" * 64
    assert inspect_capture_compatibility(capture, mode_profile=profile).status == "incompatible"


def test_inspector_accepts_runtime_capture_mode_schema_as_unverified():
    profile = _profile()
    startup = _capture_mode(profile)["contexts"][0]["startup"]
    frames = tuple(
        CameraFrame(
            image=np.zeros((400, 640), dtype=np.uint8),
            sensor_timestamp_ns=index,
            host_timestamp_ns=index,
            exposure_us=1000,
            analogue_gain=2.0,
            frame_duration_us=2500,
            capture_mode=startup,
        )
        for index in range(2)
    )
    runtime_block = _capture_mode_metadata(frames)
    result = inspect_capture_compatibility(
        {"frame_count": 2, "capture_mode": runtime_block}, mode_profile=profile
    )
    assert result.status == "unverified"

    capture = _capture_mode(profile)
    capture["frames"]["context_index"] = [0]
    assert inspect_capture_compatibility(capture, mode_profile=profile).status == "incompatible"


def test_runtime_capture_mode_can_supply_complete_unqualified_candidate_evidence():
    profile = _profile()
    profile["crop_readout_mapping"].update(
        scaler_crop="not_applicable_raw_stream", driver_vertical_offset_px=174
    )
    profile = validate_mode_profile(profile)
    startup = _capture_mode(profile)["contexts"][0]["startup"]
    frames = tuple(
        CameraFrame(
            image=np.zeros((400, 640), dtype=np.uint8),
            sensor_timestamp_ns=index,
            host_timestamp_ns=index,
            exposure_us=1000,
            analogue_gain=2.0,
            frame_duration_us=2500,
            capture_mode=startup,
        )
        for index in range(2)
    )
    runtime_block = _capture_mode_metadata(frames)
    result = inspect_capture_compatibility(
        {"frame_count": 2, "capture_mode": runtime_block},
        mode_profile=profile,
    )
    assert result.status == "unverified"
    assert set(result.reasons) == {
        "startup driver parameter does not prove an applied sensor offset",
        "capture mode identity is not independently established",
        "calibration remains unqualified",
    }

    capture = _capture_mode(profile)
    capture["contexts"][0]["startup"]["settings"]["stream"] = "main-y"
    startup = capture["contexts"][0]["startup"]
    encoded = json.dumps(startup, sort_keys=True, separators=(",", ":"))
    capture["contexts"][0]["id"] = hashlib.sha256(encoded.encode()).hexdigest()
    assert inspect_capture_compatibility(capture, mode_profile=profile).status == "incompatible"
