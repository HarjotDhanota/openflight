import copy
import json

import cv2
import numpy as np
import pytest

from openflight.camera.optical_calibration import (
    calibrate_checkerboard,
    validate_mode_profile,
)


def _profile():
    return {
        "version": 1,
        "camera_id": "camera-serial-1",
        "lens_id": "lens-1",
        "focus_id": "locked-ring-mark-1",
        "sensor_output": {
            "width": 1280,
            "height": 720,
            "bit_depth": 10,
            "raw_format": "SBGGR10",
            "mode_id": "libcamera-mode-1280x720-10bit",
        },
        "saved_image": {
            "width": 640,
            "height": 480,
            "stream": "main",
            "rotate_180": False,
            "mirror": False,
        },
        "crop_readout_mapping": {
            "native_sensor_crop": None,
            "scaler_crop": None,
            "driver_vertical_offset_px": None,
            "sensor_output_mapping": None,
            "saved_image_mapping": None,
        },
    }


def _board():
    return {
        "inner_corners_columns": 8,
        "inner_corners_rows": 6,
        "square_size_mm": 24.0,
        "square_size_uncertainty_mm": 0.1,
        "board_id": "printed-board-1",
    }


def _synthetic_observations(validation_shift=(0.0, 0.0)):
    profile = validate_mode_profile(_profile())
    object_points = np.zeros((8 * 6, 3), np.float32)
    object_points[:, :2] = np.mgrid[0:8, 0:6].T.reshape(-1, 2) * 24.0
    camera_matrix = np.array([[610.0, 0.0, 318.0], [0.0, 600.0, 237.0], [0, 0, 1]])
    distortion = np.array([-0.12, 0.025, 0.001, -0.0008, -0.004])
    observations = []
    poses = [
        (-0.18, -0.10, -0.05, -75, -45, 580),
        (-0.12, 0.08, 0.03, 20, -55, 650),
        (-0.05, -0.16, 0.08, -30, 20, 720),
        (0.02, 0.12, -0.10, 60, 25, 610),
        (0.08, -0.08, 0.14, -65, 40, 760),
        (0.14, 0.04, -0.03, 45, -20, 680),
        (0.19, -0.02, 0.05, -20, -25, 820),
        (-0.16, 0.14, -0.12, 70, -50, 740),
        (0.11, -0.15, 0.09, 10, 50, 630),
        (-0.08, 0.02, 0.16, -55, 10, 700),
        (0.06, 0.18, -0.05, 35, 35, 790),
        (-0.20, 0.06, 0.11, 5, -40, 670),
        (0.16, -0.12, -0.08, -40, 30, 730),
    ]
    for index, pose in enumerate(poses):
        rvec = np.asarray(pose[:3], dtype=float)
        tvec = np.asarray(pose[3:], dtype=float)
        corners, _ = cv2.projectPoints(object_points, rvec, tvec, camera_matrix, distortion)
        corners = corners.reshape(-1, 2).astype(float)
        split = "fit" if index < 10 else "validation"
        if split == "validation":
            corners += np.asarray(validation_shift)
        observations.append(
            {
                "id": f"view-{index}",
                "split": split,
                "group_id": f"pose-{index}",
                "profile_sha256": profile["sha256"],
                "image_sha256": f"{'a' * 56}{index:08x}",
                "image_size": [640, 480],
                "corners": corners.tolist(),
            }
        )
    return observations


def test_profile_is_canonical_and_requires_explicit_unknown_mapping():
    first = validate_mode_profile(_profile())
    reordered = dict(reversed(list(_profile().items())))
    assert validate_mode_profile(reordered)["sha256"] == first["sha256"]

    missing_mapping = _profile()
    del missing_mapping["crop_readout_mapping"]["scaler_crop"]
    with pytest.raises(ValueError, match="missing explicit fields"):
        validate_mode_profile(missing_mapping)

    unexpected = _profile()
    unexpected["derived_scale"] = 0.5
    with pytest.raises(ValueError, match="unknown fields"):
        validate_mode_profile(unexpected)


def test_synthetic_fit_and_heldout_validation_are_serializable():
    result = calibrate_checkerboard(
        board=_board(), mode_profile=_profile(), observations=_synthetic_observations()
    )

    assert result["status"] == "candidate"
    assert result["accuracy_qualified"] is False
    assert result["mode_binding"]["status"] == "unverified"
    assert result["fit"]["evaluated_view_count"] == 10
    assert result["validation"]["evaluated_view_count"] == 3
    assert result["fit"]["rms_px"] < 0.01
    assert result["validation"]["rms_px"] < 0.01
    recovered = np.asarray(result["camera_matrix"])
    np.testing.assert_allclose(
        [recovered[0, 0], recovered[1, 1], recovered[0, 2], recovered[1, 2]],
        [610.0, 600.0, 318.0, 237.0],
        atol=0.02,
    )
    assert result["distortion_coefficients"] == pytest.approx(
        {"k1": -0.12, "k2": 0.025, "p1": 0.001, "p2": -0.0008, "k3": -0.004},
        abs=1e-4,
    )
    assert list(result["distortion_coefficients"]) == ["k1", "k2", "p1", "p2", "k3"]
    assert result["residual_interpretation"]["sign"] == "projected_minus_observed"
    assert all("pose" in view for view in result["views"] if view["status"] == "evaluated")
    json.dumps(result, allow_nan=False)


def test_corrupting_holdout_does_not_change_fit():
    clean = calibrate_checkerboard(
        board=_board(), mode_profile=_profile(), observations=_synthetic_observations()
    )
    corrupt = calibrate_checkerboard(
        board=_board(),
        mode_profile=_profile(),
        observations=_synthetic_observations(validation_shift=(15.0, -8.0)),
    )

    np.testing.assert_allclose(corrupt["camera_matrix"], clean["camera_matrix"], atol=1e-8)
    assert corrupt["distortion_coefficients"] == pytest.approx(
        clean["distortion_coefficients"], abs=1e-8
    )


def test_failed_view_is_retained_without_image_evidence():
    observations = _synthetic_observations()
    profile_hash = validate_mode_profile(_profile())["sha256"]
    observations.append(
        {
            "id": "unreadable",
            "split": "validation",
            "group_id": "unreadable-pose",
            "profile_sha256": profile_hash,
            "status": "failed",
            "reason": "decode failed",
        }
    )
    result = calibrate_checkerboard(
        board=_board(), mode_profile=_profile(), observations=observations
    )
    failed = next(view for view in result["views"] if view["id"] == "unreadable")
    assert failed == {
        "id": "unreadable",
        "split": "validation",
        "group_id": "unreadable-pose",
        "profile_sha256": profile_hash,
        "status": "failed",
        "reason": "decode failed",
    }
    assert result["validation"]["attempted_view_count"] == 4


@pytest.mark.parametrize("duplicate_field", ["id", "image_sha256"])
def test_duplicate_ids_and_pixel_content_are_rejected_across_splits(duplicate_field):
    observations = _synthetic_observations()
    observations[-1][duplicate_field] = observations[0][duplicate_field]
    with pytest.raises(ValueError, match="duplicate"):
        calibrate_checkerboard(board=_board(), mode_profile=_profile(), observations=observations)


def test_mixed_profile_and_dimensions_are_rejected():
    observations = _synthetic_observations()
    observations[0]["profile_sha256"] = "different"
    with pytest.raises(ValueError, match="different mode profile"):
        calibrate_checkerboard(board=_board(), mode_profile=_profile(), observations=observations)

    observations = _synthetic_observations()
    observations[0]["image_size"] = [320, 240]
    with pytest.raises(ValueError, match="dimensions"):
        calibrate_checkerboard(board=_board(), mode_profile=_profile(), observations=observations)


def test_insufficient_and_malformed_observations_are_rejected():
    with pytest.raises(ValueError, match="at least 10"):
        calibrate_checkerboard(
            board=_board(),
            mode_profile=_profile(),
            observations=_synthetic_observations()[:9],
        )

    observations = _synthetic_observations()
    observations[0]["corners"][0][0] = float("nan")
    with pytest.raises(ValueError, match="finite"):
        calibrate_checkerboard(board=_board(), mode_profile=_profile(), observations=observations)

    degenerate = copy.deepcopy(_synthetic_observations())
    degenerate[0]["corners"] = [[100.0, 100.0]] * 48
    with pytest.raises(ValueError, match="degenerate"):
        calibrate_checkerboard(board=_board(), mode_profile=_profile(), observations=degenerate)


def test_pose_group_cannot_cross_split_or_inflate_minimum():
    observations = _synthetic_observations()
    observations[-1]["group_id"] = observations[0]["group_id"]
    with pytest.raises(ValueError, match="spans fit and validation"):
        calibrate_checkerboard(board=_board(), mode_profile=_profile(), observations=observations)

    observations = _synthetic_observations()
    observations[1]["group_id"] = observations[0]["group_id"]
    with pytest.raises(ValueError, match="at least 10 detected fit groups"):
        calibrate_checkerboard(board=_board(), mode_profile=_profile(), observations=observations)


@pytest.mark.parametrize("failure", ["exception", "nonfinite"])
def test_validation_pose_failure_is_retained_and_prevents_candidate(monkeypatch, failure):
    original = cv2.solvePnP
    calls = 0

    def fail_first(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            if failure == "exception":
                raise cv2.error("synthetic solve failure")
            return True, np.full((3, 1), np.nan), np.zeros((3, 1))
        return original(*args, **kwargs)

    monkeypatch.setattr(cv2, "solvePnP", fail_first)
    result = calibrate_checkerboard(
        board=_board(), mode_profile=_profile(), observations=_synthetic_observations()
    )

    assert result["status"] == "failed"
    assert result["validation"]["evaluated_group_count"] == 2
    failed = next(
        view
        for view in result["views"]
        if view["split"] == "validation" and view["status"] == "failed"
    )
    assert "pose" in failed["reason"]
    json.dumps(result, allow_nan=False)


def test_nonpositive_focal_length_is_rejected(monkeypatch):
    original = cv2.calibrateCamera

    def invalid_focal(*args, **kwargs):
        result = list(original(*args, **kwargs))
        result[1][0, 0] = 0.0
        return tuple(result)

    monkeypatch.setattr(cv2, "calibrateCamera", invalid_focal)
    with pytest.raises(ValueError, match="invalid camera parameters"):
        calibrate_checkerboard(
            board=_board(), mode_profile=_profile(), observations=_synthetic_observations()
        )


def test_nonfinite_fit_pose_is_rejected_without_metrics(monkeypatch):
    original = cv2.calibrateCamera

    def invalid_pose(*args, **kwargs):
        result = list(original(*args, **kwargs))
        result[3] = list(result[3])
        result[3][0] = np.full((3, 1), np.nan)
        return tuple(result)

    monkeypatch.setattr(cv2, "calibrateCamera", invalid_pose)
    with pytest.raises(ValueError, match="non-finite fit pose"):
        calibrate_checkerboard(
            board=_board(), mode_profile=_profile(), observations=_synthetic_observations()
        )
