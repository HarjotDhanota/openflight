"""The server attaches full speeds; simulators take a calculated full speed."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

import openflight.server as server_module
from openflight.full_speed import CALCULATED, NOT_MEASURED
from openflight.launch_monitor import SMASH_RADIAL_RATIO, Shot
from openflight.rig_geometry import RigGeometry

WINDOW_MS = 128 / 30.0


def _track(radials, impact_ms=40.0):
    return {
        "clock": "ops_capture",
        "impact_ms": impact_ms,
        "impact_source": "ops_transition",
        "impact_uncertainty_ms": 0.6,
        "window_ms": WINDOW_MS,
        "radial_resolution_mph": 0.05,
        "canonical_radial_mph": max(radials),
        "outbound": [[impact_ms + 0.5 + 1.0667 * i, speed] for i, speed in enumerate(radials)],
    }


def _shot(**overrides):
    values = dict(
        ball_speed_mph=118.0,
        club_speed_mph=88.0,
        timestamp=datetime.now(),
        launch_angle_vertical=16.0,
        launch_angle_vertical_source="radar",
        inclinometer={"applied_to": ["camera", "iwr6843"]},
        launch_angle_horizontal=2.0,
        launch_angle_horizontal_source="radar",
        launch_angle_horizontal_frame="unit_boresight",
    )
    values.update(overrides)
    shot = Shot(**values)
    shot.ops_speed_track = _track([114.0, 114.6, 115.2, 115.7, 116.3, 118.0])
    return shot


@pytest.fixture
def fusion_geometry(monkeypatch):
    rig = RigGeometry.from_json("config/enclosure_v3_rig_geometry.json")
    calibration = SimpleNamespace(
        tee_range_m=2.10,
        tee_ball_height_m=0.02135,
        radar_height_m=rig.enclosure_setup().radar_height_m,
    )
    monkeypatch.setattr(server_module, "rig_geometry", rig)
    monkeypatch.setattr(server_module, "iwr6843_runtime", SimpleNamespace(calibration=calibration))
    monkeypatch.setattr(server_module, "horizontal_offset_deg", None)
    scene = dict(server_module.setup_handoff_config.get("scene") or {})
    scene["tee_side_offset_m"] = 0.03
    monkeypatch.setitem(server_module.setup_handoff_config, "scene", scene)
    monkeypatch.setattr(server_module, "ball_speed_correction_enabled", False)
    return rig


def test_the_server_attaches_a_calculated_full_ball_speed(fusion_geometry):
    shot = _shot()

    server_module._attach_ball_speed_contract(shot)

    # canonical stays the radial reading (speed contract)
    assert shot.ball_speed_mph == 118.0
    assert shot.ball_speed_contract == "radial"
    full = shot.ball_speed_full
    assert full["status"] == CALCULATED
    assert full["value_mph"] > full["reading"]["radial_mph"]
    assert full["inputs"]["tee_side_offset_m"] == 0.03
    # the OPS sits 85 mm left of the lens in this rig
    assert full["geometry_m"]["ops"][1] == pytest.approx(-0.085 + 0.0018575, abs=1e-6)


def test_the_payload_carries_the_full_speeds_and_keeps_the_radial(fusion_geometry):
    shot = _shot()
    server_module._attach_ball_speed_contract(shot)

    payload = server_module.shot_to_dict(shot)

    assert payload["ball_speed_mph"] == 118.0
    assert payload["ball_speed_full"]["status"] == CALCULATED
    assert payload["club_speed_full"]["status"] == NOT_MEASURED
    assert payload["smash_factor_full"]["status"] == NOT_MEASURED
    assert "ops_speed_track" not in payload


def test_club_speed_is_not_full_and_smash_keeps_its_radial_ratio(fusion_geometry, monkeypatch):
    """D21: no full / radial mix. The shown smash stays radial / radial, labelled."""
    shot = _shot()
    server_module._open_smash_factor_status(shot)
    server_module._attach_ball_speed_contract(shot)

    assert shot.club_speed_full["status"] == NOT_MEASURED
    assert "no camera head track" in shot.club_speed_full["reason"]
    assert shot.smash_factor_full["status"] == NOT_MEASURED
    assert "club speed is not a full speed" in shot.smash_factor_full["reason"]
    assert shot.smash_factor_status == SMASH_RADIAL_RATIO
    assert shot.smash_factor == pytest.approx(118.0 / 88.0)


def test_without_the_setup_side_offset_the_ops_total_projection_resolves_it(
    fusion_geometry, monkeypatch
):
    """The launch-based projection refuses an unmeasured side offset; the OPS total-speed
    projection takes the ball dead ahead, estimated, and says so."""
    scene = dict(server_module.setup_handoff_config["scene"])
    scene["tee_side_offset_m"] = None
    monkeypatch.setitem(server_module.setup_handoff_config, "scene", scene)
    shot = _shot()

    server_module._attach_ball_speed_contract(shot)

    assert shot.ball_speed_full["route"] == "ops_total_projection"
    assert shot.ball_speed_full["status"] == "estimated"
    tried = shot.input_sources["ball_speed_full"]["tried"]
    assert "side offset" in tried[0]["reason"]


def test_not_measured_on_a_unit_without_the_iwr(monkeypatch):
    monkeypatch.setattr(server_module, "iwr6843_runtime", None)
    monkeypatch.setattr(server_module, "ball_speed_correction_enabled", False)
    shot = _shot()

    server_module._attach_ball_speed_contract(shot)

    assert shot.ball_speed_full["status"] == NOT_MEASURED
    assert shot.ball_speed_full["value_mph"] is None
    assert shot.ball_speed_mph == 118.0


def test_not_measured_with_an_estimated_launch(fusion_geometry):
    shot = _shot(launch_angle_vertical_source="estimated")
    server_module._attach_ball_speed_contract(shot)
    assert shot.ball_speed_full["status"] == NOT_MEASURED
    assert "vertical launch" in shot.ball_speed_full["reason"]


def test_a_radar_height_that_is_not_set_is_not_measured(fusion_geometry, monkeypatch):
    class Unset:
        tee_range_m = 2.1
        tee_ball_height_m = 0.02135

        @property
        def radar_height_m(self):
            raise ValueError("IWR6843 radar height is not set")

    monkeypatch.setattr(server_module, "iwr6843_runtime", SimpleNamespace(calibration=Unset()))
    shot = _shot()
    server_module._attach_ball_speed_contract(shot)
    assert shot.ball_speed_full["status"] == NOT_MEASURED
    assert "height" in shot.ball_speed_full["reason"]


def test_the_flight_model_s_field_carries_the_calculated_full_speed(fusion_geometry):
    shot = _shot()
    server_module._attach_ball_speed_contract(shot)

    assert shot.ball_speed_full_mph == shot.ball_speed_full["value_mph"]
    assert shot.ball_speed_mph == 118.0
    assert server_module.shot_to_dict(shot)["ball_speed_full_mph"] == pytest.approx(
        shot.ball_speed_full_mph
    )


def test_the_flight_model_s_field_is_none_when_not_measured(fusion_geometry):
    shot = _shot(launch_angle_vertical_source="estimated")
    shot.ball_speed_full_mph = 999.0  # a stale value is never kept
    server_module._attach_ball_speed_contract(shot)

    assert shot.ball_speed_full["status"] == NOT_MEASURED
    assert shot.ball_speed_full_mph is None


def test_a_camera_launch_gives_an_experimental_full_speed_the_flight_still_uses(fusion_geometry):
    """The camera fallback's launch on a nominal lens: the full speed is projected,
    labelled experimental on the shot field as on the record, never sent."""
    shot = _shot(launch_angle_vertical_source="camera_fallback")

    server_module._attach_ball_speed_contract(shot)

    assert shot.ball_speed_full["status"] == "experimental"
    assert shot.to_dict()["metrics"]["ball_speed_full"]["status"] == "experimental"
    assert "camera fallback" in shot.ball_speed_full["note"]
    # the flight flies it, labelled by it; simulators take only a calculated one
    assert shot.ball_speed_full_mph == shot.ball_speed_full["value_mph"]


def test_an_uncalibrated_azimuth_gives_an_estimated_full_speed(fusion_geometry):
    """Today's field state (IWR azimuth uncalibrated): resolved, labelled estimated."""
    shot = _shot(
        launch_angle_horizontal_status="azimuth_uncalibrated",
        inclinometer={"applied_to": ["camera", "iwr6843"]},
    )

    server_module._attach_ball_speed_contract(shot)

    assert shot.ball_speed_full["status"] == "estimated"
    assert shot.ball_speed_full_mph == shot.ball_speed_full["value_mph"]
    assert shot.to_dict()["metrics"]["ball_speed_full"]["status"] == "estimated"


def test_a_configured_tilt_gives_an_estimated_full_speed(fusion_geometry):
    shot = _shot(inclinometer=None)  # no LIS3DH reading applied to the IWR

    server_module._attach_ball_speed_contract(shot)

    assert shot.ball_speed_full["status"] == "estimated"
    assert "tilt is the configured value" in shot.ball_speed_full["note"]


def test_a_calibrated_lens_lifts_the_camera_fallback_full_speed_to_estimated(fusion_geometry):
    shot = _shot(
        launch_angle_vertical_source="camera_fallback",
        camera_lens_intrinsics="calibrated",
    )

    server_module._attach_ball_speed_contract(shot)

    assert shot.ball_speed_full["status"] == "estimated"
