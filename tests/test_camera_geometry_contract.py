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
        "ball_height_m": 0.02135,
        "camera_lateral_offset_m": 0.004,
        "camera_forward_offset_m": 0.03,
        "image_width_px": 320,
        "image_height_px": 200,
        "horizontal_pixel_sign": -1.0,
        "roll_correction_deg": 2.5,
        "ball_horizontal_output_offset_deg": -0.4,
        "ball_diameter_m": 0.0427,
    }
    values.update(changes)
    return EffectiveCameraGeometryInputs(**values)


LEGACY = {
    "camera_capture": {"mount_height_m": 0.1, "width": 640, "height": 400},
    "iwr6843": {"tee_slant_range_m": 1.4, "radar_height_m": 0.05, "ball_height_m": 0.02},
}


def _calibration():
    return SimpleNamespace(tee_range_m=1.5, radar_height_m=0.051, tee_ball_height_m=0.02135)


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
    calibration = _calibration()
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
    effective = EffectiveCameraGeometryInputs.from_recorded_session(LEGACY)
    assert effective.camera_lateral_offset_m == 0
    assert effective.camera_forward_offset_m == 0
    assert effective.horizontal_pixel_sign == 1
    assert effective.roll_correction_deg == 0
    # a legacy record set no target line: unset, not 0.0
    assert effective.ball_horizontal_output_offset_deg is None


def test_horizontal_offset_rotates_ball_and_club_geometry_together():
    effective = _inputs(ball_horizontal_output_offset_deg=3.25)
    assert effective.ball_geometry().horizontal_offset_deg == 3.25
    assert effective.delivery_geometry().horizontal_offset_deg == 3.25


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
    for field, value, match in (
        ("schema", SCHEMA + ".future", "unsupported"),
        ("sha256", "0" * 64, "fingerprint mismatch"),
    ):
        snapshot = {**_inputs().snapshot(), field: value}
        with pytest.raises(ValueError, match=match):
            EffectiveCameraGeometryInputs.from_recorded_session(
                {**LEGACY, "effective_camera_geometry": snapshot}
            )


def test_archive_dimensions_must_match_recorded_geometry():
    effective = _inputs()
    effective.validate_archive_dimensions(320, 200)
    with pytest.raises(ValueError, match="do not match"):
        effective.validate_archive_dimensions(640, 400)


def test_session_start_records_actual_runtime_geometry(monkeypatch):
    camera = {"enabled": True, "mount_height_m": 0.095, "width": 320, "height": 200}
    monkeypatch.setattr(server, "camera_capture_config", {**camera, "forward_offset_m": 0.03})
    monkeypatch.setattr(server, "iwr6843_runtime", SimpleNamespace(calibration=_calibration()))
    block = server._session_start_config()["effective_camera_geometry"]
    assert block["available"] is True
    assert block["parameters"]["camera_forward_offset_m"] == 0.03
    restored = EffectiveCameraGeometryInputs.from_recorded_session(
        {"effective_camera_geometry": block}
    )
    assert restored.radar_height_m == 0.051


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


def test_the_setup_tee_side_offset_places_the_ball_for_both_estimators():
    camera = {"mount_height_m": 0.095, "width": 640, "height": 400, "tee_side_offset_m": 0.12}
    effective = EffectiveCameraGeometryInputs.from_live(camera, _calibration())

    assert effective.ball_lateral_offset_m == pytest.approx(0.12)
    assert effective.ball_lateral_offset_source == "setup_patch_pair"
    for geometry in (effective.ball_geometry(), effective.delivery_geometry()):
        assert geometry.ball_lateral_offset_m == pytest.approx(0.12)
        forward = (1.5**2 - (0.02135 - 0.051) ** 2 - 0.12**2) ** 0.5
        assert geometry.ball_forward_m == pytest.approx(forward)
        assert list(geometry.ball_position_lfu) == pytest.approx([0.12, forward, 0.02135])
    snapshot = effective.snapshot()
    assert snapshot["assumptions"]["tee_lateral_position"] == "setup_patch_pair"
    restored = EffectiveCameraGeometryInputs.from_recorded_session(
        {"effective_camera_geometry": snapshot}
    )
    assert restored == effective


def test_without_a_measured_side_offset_the_ball_is_labelled_dead_ahead():
    camera = {"mount_height_m": 0.095, "width": 640, "height": 400}
    effective = EffectiveCameraGeometryInputs.from_live(camera, _calibration())

    assert effective.ball_lateral_offset_m == 0.0
    assert effective.ball_lateral_offset_source == "assumed_directly_downrange"
    assert effective.snapshot()["assumptions"]["tee_lateral_position"] == (
        "assumed_zero_directly_downrange"
    )
