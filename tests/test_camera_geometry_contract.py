"""Effective camera geometry persistence and replay contract tests."""

from types import SimpleNamespace

import pytest

from openflight import server
from openflight.camera.geometry_contract import SCHEMA, EffectiveCameraGeometryInputs


def _inputs(**changes):
    values = {
        "camera_height_m": 0.095,
        "radar_height_m": 0.051,
        "tee_slant_range_m": 1.5,
        "ball_height_m": 0.021335,
        "camera_lateral_offset_m": 0.004,
        "camera_forward_offset_m": 0.03,
        "image_width_px": 320,
        "image_height_px": 200,
        "horizontal_pixel_sign": -1.0,
        "roll_correction_deg": 2.5,
        "ball_horizontal_output_offset_deg": -0.4,
        "ball_diameter_m": 0.04267,
    }
    values.update(changes)
    return EffectiveCameraGeometryInputs(**values)


def test_snapshot_round_trip_is_detached_from_mutable_source_config():
    camera = {
        "mount_height_m": 0.095,
        "width": 320,
        "height": 200,
        "lateral_offset_m": 0.004,
        "forward_offset_m": 0.03,
        "mirror_horizontal": True,
        "roll_correction_deg": 2.5,
        "horizontal_offset_deg": -0.4,
    }
    calibration = SimpleNamespace(tee_range_m=1.5, radar_height_m=0.051, tee_ball_height_m=0.021335)
    effective = EffectiveCameraGeometryInputs.from_live(camera, calibration)
    snapshot = effective.snapshot()
    session = {"effective_camera_geometry": snapshot}

    camera["mount_height_m"] = 99
    calibration.tee_range_m = 99
    snapshot["parameters"]["camera_height_m"] = 99

    clean_session = {"effective_camera_geometry": effective.snapshot()}
    assert EffectiveCameraGeometryInputs.from_recorded_session(clean_session) == effective
    assert clean_session["effective_camera_geometry"]["parameters"]["camera_height_m"] == 0.095
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        EffectiveCameraGeometryInputs.from_recorded_session(session)


def test_legacy_record_uses_only_recorded_fields_and_explicit_legacy_defaults():
    session = {
        "camera_capture": {"mount_height_m": 0.1, "width": 640, "height": 400},
        "iwr6843": {
            "tee_slant_range_m": 1.4,
            "radar_height_m": 0.05,
            "ball_height_m": 0.02,
        },
    }
    effective = EffectiveCameraGeometryInputs.from_recorded_session(session)
    assert effective.camera_lateral_offset_m == 0
    assert effective.camera_forward_offset_m == 0
    assert effective.horizontal_pixel_sign == 1
    assert effective.roll_correction_deg == 0
    assert effective.ball_horizontal_output_offset_deg == 0


def test_ball_output_offset_is_not_applied_to_club_geometry():
    effective = _inputs(ball_horizontal_output_offset_deg=3.25)
    assert effective.ball_geometry().horizontal_offset_deg == 3.25
    assert not hasattr(effective.delivery_geometry(), "horizontal_offset_deg")


@pytest.mark.parametrize(
    "changes,match",
    [
        ({"camera_height_m": float("nan")}, "finite"),
        ({"tee_slant_range_m": 0.01}, "cannot reach"),
        ({"camera_forward_offset_m": 2.0}, "not in front"),
        ({"image_width_px": 0}, "positive integer"),
        ({"horizontal_pixel_sign": 0}, "must be -1 or 1"),
        ({"ball_diameter_m": 0}, "diameter must be positive"),
    ],
)
def test_rejects_nonfinite_or_impossible_geometry(changes, match):
    with pytest.raises(ValueError, match=match):
        _inputs(**changes)


def test_present_snapshot_never_falls_back_to_legacy_on_wrong_schema_or_hash():
    legacy = {
        "camera_capture": {"mount_height_m": 0.1, "width": 640, "height": 400},
        "iwr6843": {
            "tee_slant_range_m": 1.4,
            "radar_height_m": 0.05,
            "ball_height_m": 0.02,
        },
    }
    wrong_schema = _inputs().snapshot()
    wrong_schema["schema"] = SCHEMA + ".future"
    with pytest.raises(ValueError, match="unsupported"):
        EffectiveCameraGeometryInputs.from_recorded_session(
            {**legacy, "effective_camera_geometry": wrong_schema}
        )

    wrong_hash = _inputs().snapshot()
    wrong_hash["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        EffectiveCameraGeometryInputs.from_recorded_session(
            {**legacy, "effective_camera_geometry": wrong_hash}
        )


def test_archive_dimensions_must_match_recorded_geometry():
    effective = _inputs()
    effective.validate_archive_dimensions(320, 200)
    with pytest.raises(ValueError, match="do not match"):
        effective.validate_archive_dimensions(640, 400)


def test_snapshot_parameters_include_explicit_model_assumptions():
    snapshot = _inputs().snapshot()
    assumptions = snapshot["assumptions"]
    assert assumptions["focal_length"] == "inferred_from_reference_ball"
    assert assumptions["principal_point"] == "image_center"
    assert assumptions["distortion_model"] == "none"
    assert assumptions["yaw"] == "not_calibrated"
    assert assumptions["camera_origin"] == "lens_front_vs_optical_center_unresolved"


def test_session_start_records_actual_runtime_geometry(monkeypatch):
    monkeypatch.setattr(
        server,
        "camera_capture_config",
        {
            "enabled": True,
            "mount_height_m": 0.095,
            "width": 320,
            "height": 200,
            "forward_offset_m": 0.03,
        },
    )
    monkeypatch.setattr(
        server,
        "iwr6843_runtime",
        SimpleNamespace(
            calibration=SimpleNamespace(
                tee_range_m=1.5, radar_height_m=0.051, tee_ball_height_m=0.021335
            )
        ),
    )
    block = server._session_start_config()["effective_camera_geometry"]
    assert block["available"] is True
    assert block["parameters"]["camera_forward_offset_m"] == 0.03
    assert (
        EffectiveCameraGeometryInputs.from_recorded_session(
            {"effective_camera_geometry": block}
        ).radar_height_m
        == 0.051
    )


def test_session_start_records_explicit_unavailable_reason(monkeypatch):
    monkeypatch.setattr(server, "camera_capture_config", {"enabled": False})
    block = server._session_start_config()["effective_camera_geometry"]
    assert block == {
        "schema": SCHEMA,
        "version": 1,
        "available": False,
        "reason": "camera capture is disabled",
    }
    with pytest.raises(ValueError, match="camera capture is disabled"):
        EffectiveCameraGeometryInputs.from_recorded_session(
            {"effective_camera_geometry": block, "camera_capture": {}, "iwr6843": {}}
        )
