"""Known-answer optics controls; no physical strike qualification."""

import copy

import cv2
import numpy as np
import pytest

from openflight.camera import impact_location as il, impact_optics as optics
from openflight.camera.calibrated_projection import load_calibration_candidate
from tests.camera_helpers import calibration_artifact, mode_profile


@pytest.mark.parametrize("status", ["rejected", "experimental_unqualified"])
def test_declared_calibrated_route_cannot_fall_back_after_its_snapshot_is_lost(status):
    shot = {
        "shot_number": 1,
        "calibrated_camera_status": status,
        "camera_fusion_context": {"available": False, "reason": "mode mismatch"},
    }
    with pytest.raises(ValueError, match="declared calibrated"):
        optics.recorded_model(shot, "s", "a" * 64)


@pytest.mark.parametrize(
    "mirror,rotate", [(False, False), (True, False), (False, True), (True, True)]
)
def test_rectified_pixels_preserve_brown_rays_and_saved_orientation(mirror, rotate):
    profile = mode_profile(width=96, height=64, mirror=mirror, rotate=rotate)
    artifact = calibration_artifact(profile, matrix=[[130, 0, 43], [0, 118, 27], [0, 0, 1]])
    frames = np.arange(96 * 64, dtype=np.uint8).reshape(1, 64, 96)
    original = frames.copy()
    result = optics.rectify(frames, artifact)
    projection = load_calibration_candidate(artifact, mode_profile=profile)
    pixels = np.array([[20, 20], [60, 45], [45, 31]], dtype=float)
    rays = projection.pixel_rays_lfu(pixels, optical_to_world_lfu=np.eye(3))
    expected = rays[:, :2] / rays[:, 2:] * result.camera.focal_px
    expected += result.camera.principal_point_px
    np.testing.assert_allclose(result.points(pixels), expected, atol=1e-9)
    np.testing.assert_array_equal(frames, original)
    assert result.frames.shape == frames.shape
    assert result.decision["physical_qualified"] is False
    assert result.decision["coordinate_frame"] == "rectified_optical_rdf"
    # Image intensities must sample the same saved coordinates as the ray mapping.
    for target in ([30, 20], [55, 40]):
        source = result.saved_points(np.array([target], dtype=float))
        sampled = cv2.remap(
            original[0],
            source[:, 0].astype(np.float32).reshape(1, 1),
            source[:, 1].astype(np.float32).reshape(1, 1),
            cv2.INTER_LINEAR,
        )[0, 0]
        assert result.frames[0, target[1], target[0]] == sampled


def test_rectified_camera_remains_correct_after_binning_and_handed_crop():
    artifact = calibration_artifact(
        mode_profile(width=96, height=64),
        matrix=[[130, 0, 43], [0, 118, 27], [0, 0, 1]],
    )
    result = optics.rectify(np.zeros((5, 64, 96), np.uint8), artifact)
    camera = result.binned_camera(2)
    np.testing.assert_allclose(
        camera.principal_point_px, (np.array(result.camera.principal_point_px) + 0.5) / 2 - 0.5
    )
    assert camera.focal_px == result.camera.focal_px / 2
    ball = il.RestingBall(25, 20, 3)
    for hand in ("right", "left"):
        prepared = il.prepare_shot(
            np.zeros((5, 32, 48), np.uint8),
            ball,
            fps=300,
            camera=camera,
            handedness=hand,
            ball_range_mm=1500,
        )
        cx, cy = camera.principal_point_px
        expected_x = 47 - cx if hand == "left" else cx
        np.testing.assert_allclose(
            prepared.camera.principal_point_px,
            [expected_x - prepared.roi_origin[0], cy - prepared.roi_origin[1]],
        )


def test_identity_optics_preserve_pixels_without_resampling_drift():
    artifact = calibration_artifact(
        mode_profile(width=96, height=64),
        matrix=[[128, 0, 43], [0, 128, 27], [0, 0, 1]],
        coefficients=dict.fromkeys(("k1", "k2", "p1", "p2", "k3"), 0),
    )
    frames = np.random.default_rng(15).integers(0, 256, (2, 64, 96), dtype=np.uint8)
    result = optics.rectify(frames, artifact)
    np.testing.assert_array_equal(result.frames, frames)
    assert result.decision["zoom"] == 1


def test_calibrated_offset_gives_known_ball_forward_depth():
    from types import SimpleNamespace

    from openflight.camera.impact_session import ball_depth_mm

    camera = il.ImpactCamera(600, (307, 191))
    offset = np.array([45, -30, 22])
    point = np.array([100, 50, 1500])
    ball = il.RestingBall(*(point[:2] / point[2] * 600 + [307, 191]), 17)
    depth = ball_depth_mm(
        SimpleNamespace(iwr_offset_mm=None),
        ball,
        camera,
        np.linalg.norm(point - offset),
        radar_offset_mm=offset,
    )
    assert depth == pytest.approx(point[2], abs=1e-9)


@pytest.mark.parametrize("change", ["size", "mirror", "rotate", "stream", "crop", "missing_mode"])
def test_research_mode_never_overrides_a_known_contradiction(change):
    profile = mode_profile(width=96, height=64)
    profile["crop_readout_mapping"]["scaler_crop"] = [8, 8, 1280, 800]
    artifact = calibration_artifact(profile)
    metadata = {"settings": {"mirror_horizontal": False, "rotate_180": False}}
    frames = np.zeros((3, 64, 96), np.uint8)
    if change == "size":
        frames = frames[:, :, :-1]
    elif change in ("mirror", "rotate"):
        metadata["settings"]["mirror_horizontal" if change == "mirror" else "rotate_180"] = True
    elif change == "stream":
        metadata["settings"]["stream"] = "main-y"
    elif change == "crop":
        metadata["settings"]["scaler_crop"] = [0, 0, 1280, 800]
    else:
        with pytest.raises(ValueError, match="capture_mode"):
            optics.check_mode(frames, metadata, artifact, research_override=False)
        return
    with pytest.raises(ValueError):
        optics.check_mode(frames, metadata, artifact, research_override=True)


def test_frozen_optics_require_own_shot_archive_session_and_fingerprint():
    from openflight.camera.calibrated_projection import build_calibrated_camera_model
    from openflight.rig_geometry import geometry_fingerprint
    from tests.camera_helpers import camera_placement
    from tests.test_camera_fusion_processing import _context_kwargs

    model = build_calibrated_camera_model(
        calibration_artifact(), camera_placement(), observed_pitch_deg=7, observed_roll_deg=-3
    )
    mode = {
        "status": "unverified",
        "reasons": ["calibration remains unqualified"],
        "mode_profile_sha256": model.projection.mode_profile["sha256"],
    }
    geometry = _context_kwargs()["geometry"].with_calibrated_model(model.snapshot, mode).snapshot()
    context = {
        "schema": "openflight.camera.fusion_context",
        "version": 2,
        "available": True,
        "session_uuid": "s",
        "shot_number": 1,
        "capture_npz_sha256": "a" * 64,
        "geometry": geometry,
    }
    context["sha256"] = geometry_fingerprint(context)
    shot = {"shot_number": 1, "camera_fusion_context": context}
    assert optics.recorded_model(shot, "s", "a" * 64).snapshot == model.snapshot
    for field, value in (
        ("session_uuid", "other"),
        ("shot_number", 2),
        ("capture_npz_sha256", "b" * 64),
        ("available", False),
    ):
        changed = copy.deepcopy(shot)
        changed["camera_fusion_context"][field] = value
        changed["camera_fusion_context"].pop("sha256")
        changed["camera_fusion_context"]["sha256"] = geometry_fingerprint(
            changed["camera_fusion_context"]
        )
        with pytest.raises(ValueError):
            optics.recorded_model(changed, "s", "a" * 64)
    shot["camera_fusion_context"]["geometry"]["parameters"]["calibrated_model_snapshot"][
        "observed_pose_deg"
    ]["pitch"] = 8
    with pytest.raises(ValueError, match="fingerprint"):
        optics.recorded_model(shot, "s", "a" * 64)
