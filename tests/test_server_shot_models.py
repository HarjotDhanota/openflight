"""The kiosk server with the shot models loaded (Part E): spin axis, face angle and carry.

These are tests/test_server.py's cases that run through the collision and flight models.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from openflight import (  # noqa: F401
    collision_model,
    flight as flight_module,
    flight_sim,
    server as server_module,
    shot_models,
)
from openflight.clubs import ClubType
from openflight.iwr6843.calibration import Calibration
from openflight.launch_monitor import Shot
from openflight.server import on_shot_detected
from tests import test_server
from tests.test_horizontal_frame import OFFSET_DEG, _shot
from tests.test_server import (
    OnShotDetectedHelpers,
    _wait_for_shot_finalization_idle,
)
from tests.test_server_ball_range import _shot as _range_shot


class TestSpinAxisFromTheCollisionModel(OnShotDetectedHelpers):
    @staticmethod
    def _expected_spin_axis():
        """E2: the collision model's centre-strike axis, not horizontal launch minus path."""
        return (
            collision_model.evaluate(
                club=ClubType.DRIVER,
                club_path=collision_model.Input(-1.5),
                attack_angle=collision_model.Input(2.0),
                launch_horizontal=collision_model.Input(3.2),
                launch_vertical=collision_model.Input(14.0),
                club_speed_mph=collision_model.Input(100.0),
            )
            .metrics["spin_axis"]
            .value
        )

    def test_spin_axis_emitted_when_horizontal_confidence_clears_gate(self, monkeypatch):
        shot = self._spin_axis_shot(horizontal_confidence=server_module.SPIN_AXIS_MIN_CONFIDENCE)

        self._run_with_no_radar_hardware(monkeypatch, shot)

        assert shot.spin_axis_deg == pytest.approx(self._expected_spin_axis(), abs=0.05)
        assert shot.spin_axis_source == collision_model.SPIN_AXIS_SOURCE_MODEL

    def test_spin_axis_names_the_frame_its_two_legs_share(self, monkeypatch):
        """D36 item 2: face to path cancels the horizontal offset only within one frame."""
        shot = self._spin_axis_shot(horizontal_confidence=server_module.SPIN_AXIS_MIN_CONFIDENCE)
        shot.launch_angle_horizontal_frame = "target_line"
        shot.club_path_frame = "unit_boresight"

        self._run_with_no_radar_hardware(monkeypatch, shot)

        assert shot.spin_axis_deg == pytest.approx(self._expected_spin_axis(), abs=0.05)
        assert shot.to_dict()["spin_axis_frame"] == "mixed"

    def test_spin_axis_estimated_when_horizontal_confidence_below_gate(self, monkeypatch):
        """Below the horizontal leg's confidence floor the axis is labelled estimated,
        never calculated (V-formulas C2; it used to be withheld)."""
        shot = self._spin_axis_shot(
            horizontal_confidence=server_module.SPIN_AXIS_MIN_CONFIDENCE - 0.01
        )

        self._run_with_no_radar_hardware(monkeypatch, shot)

        assert shot.spin_axis_deg == pytest.approx(self._expected_spin_axis(), abs=0.05)
        assert shot.spin_axis_source.startswith("estimated")


class TestFaceAngleFromTheCollisionModel:
    _run = staticmethod(test_server.TestIwrAzimuthCalibration._run)

    def test_without_a_reference_face_angle_uses_camera_paths_only(self, monkeypatch):
        shot = self._run(monkeypatch, phase_reference=None)
        # no attack: the face from the horizontal ratio alone (the IWR attack candidate
        # is an estimated attack route since decision 3: test_attack_angle_chain)
        shot.experimental_attack_angle_deg = None
        # The camera horizontal (camera bearing on accepted IWR depth) outranks
        # the radar one and carries no azimuth mark.
        server_module._apply_camera_horizontal_decision(
            shot, 2.0, 0.75, "camera_assisted_experimental"
        )
        assert shot.launch_angle_horizontal_status is None

        shot_models._attach_collision_model(shot)
        assert shot.experimental_face_angle_deg is None
        assert shot.experimental_face_angle_status == "missing_accepted_club_path"

        shot.experimental_fused_club_path_deg = -4.0
        shot.experimental_fused_status = "chained_high"
        shot_models._attach_collision_model(shot)
        # E2: the PING weight for this club (Wood et al. 2018), not a fixed 0.8
        weight = collision_model.weights_for_club(shot.club).horizontal
        assert shot.experimental_face_angle_deg == pytest.approx(-4.0 + 6.0 / weight, abs=0.05)
        assert shot.experimental_face_angle_path_source == "camera_fused_chained"

    def test_an_uncalibrated_radar_start_direction_gives_a_labelled_face_angle(self, monkeypatch):
        """D15 (P8-7): computed, and labelled with the mixed zeros and the path source.

        An uncalibrated start direction is an estimated input, so the face is estimated."""
        shot = self._run(monkeypatch, phase_reference=None)
        shot.experimental_attack_angle_deg = None  # the horizontal ratio alone, as above
        shot.experimental_fused_club_path_deg = -4.0
        shot.experimental_fused_status = "chained_high"

        shot_models._attach_collision_model(shot)

        weight = collision_model.weights_for_club(shot.club).horizontal
        assert shot.experimental_face_angle_deg == pytest.approx(-4.0 + 6.25 / weight, abs=0.05)
        assert (
            shot.experimental_face_angle_status == "d_plane_estimate_azimuth_uncalibrated_estimated"
        )
        assert shot.collision_model["metrics"]["face_angle"]["status"] == "estimated"
        assert shot.experimental_face_angle_path_source == "camera_fused_chained"
        assert shot.experimental_face_angle_launch_source == "radar"


class TestFlightModelCarryPrecedence:
    _isolate_finalization = test_server.TestBallisticCarryPrecedence._isolate_finalization
    _shot = staticmethod(test_server.TestBallisticCarryPrecedence._shot)

    def test_simulator_overrides_prefilled_table_carry(self, monkeypatch):
        monkeypatch.setattr(server_module, "ballistics_enabled", True)
        shot = self._shot(launch_angle=19.1, prefilled_carry=119.2)

        server_module._finalize_shot_detected(shot, emit_event="shot")

        expected = flight_module.simulate(flight_module.resolve_launch(shot)).carry_yards
        assert shot.carry_spin_adjusted == pytest.approx(expected)
        assert shot.carry_spin_adjusted != pytest.approx(119.2)
        assert shot.carry_spin_adjusted > 135.0


class TestCarryComputation:
    """Tests for the ballistic carry path in on_shot_detected."""

    def _patch_environment(self, monkeypatch):
        # an OPS-only install: no IWR or camera; nothing is filled in (owner decision 1)
        monkeypatch.setattr(server_module, "iwr6843_runtime", None)
        monkeypatch.setattr(server_module, "camera_capture_runtime", None)
        monkeypatch.setattr(server_module, "kld7_vertical", None)
        monkeypatch.setattr(server_module, "kld7_horizontal", None)
        monkeypatch.setattr(server_module, "monitor", None)
        monkeypatch.setattr(server_module, "debug_mode", False)
        monkeypatch.setattr(server_module, "get_session_logger", lambda: None)
        monkeypatch.setattr(server_module.socketio, "emit", lambda *args, **kwargs: None)

    def test_carry_uses_ballistic_simulator_when_launch_angle_present(self, monkeypatch):
        """A shot with a vertical launch angle should get carry from the physics sim."""
        self._patch_environment(monkeypatch)
        monkeypatch.setattr(server_module, "ballistics_enabled", True)

        captured = {}

        from openflight import flight_sim as ballistics_module

        real_simulate = ballistics_module.simulate

        def spying_simulate(conditions, *args, **kwargs):
            captured["conditions"] = conditions
            return real_simulate(conditions, *args, **kwargs)

        monkeypatch.setattr(flight_module, "simulate", spying_simulate)

        shot = Shot(
            ball_speed_mph=165.0,
            club_speed_mph=112.0,
            timestamp=datetime.now(),
            club=ClubType.DRIVER,
            launch_angle_vertical=11.0,
            launch_angle_vertical_source="radar",
            launch_angle_confidence=0.8,
            launch_angle_horizontal=0.5,
            launch_angle_horizontal_source="radar",
            spin_axis_deg=1.0,
            spin_rpm=2700,
            spin_confidence=0.85,
            angle_source="radar",
        )

        on_shot_detected(shot)
        _wait_for_shot_finalization_idle()

        assert "conditions" in captured, "simulate() should have been called"
        assert captured["conditions"].spin_source == "measured"
        assert shot.carry_spin_adjusted is not None
        assert 250 < shot.carry_spin_adjusted < 300

    def test_no_carry_when_resolve_returns_none(self, monkeypatch):
        """When resolve_launch returns None there is no carry: no table (decision 1)."""
        self._patch_environment(monkeypatch)

        monkeypatch.setattr(flight_module, "resolve_launch", lambda shot, **_kwargs: None)

        def fail_simulate(*args, **kwargs):
            raise AssertionError("simulate() must not be called when resolve_launch is None")

        monkeypatch.setattr(flight_module, "simulate", fail_simulate)

        shot = Shot(
            ball_speed_mph=150.0,
            club_speed_mph=105.0,
            timestamp=datetime.now(),
            club=ClubType.DRIVER,
            launch_angle_vertical=12.0,
            spin_rpm=2700,
            spin_confidence=0.85,
            angle_source="radar",
        )

        on_shot_detected(shot)
        _wait_for_shot_finalization_idle()

        assert shot.carry_spin_adjusted is None
        assert shot.carry_status == "not_measured"

    def test_carry_skips_ballistic_when_ballistics_disabled(self, monkeypatch):
        """When ballistics_enabled is False, the simulator must not run even
        if a valid launch angle is present, and nothing stands in for the carry.
        Operators can request this with `--no-ballistics`."""
        self._patch_environment(monkeypatch)
        monkeypatch.setattr(server_module, "ballistics_enabled", False)

        def fail_resolve(*args, **kwargs):
            raise AssertionError("resolve_launch must not run when ballistics disabled")

        def fail_simulate(*args, **kwargs):
            raise AssertionError("simulate() must not run when ballistics disabled")

        monkeypatch.setattr(flight_module, "resolve_launch", fail_resolve)
        monkeypatch.setattr(flight_module, "simulate", fail_simulate)

        shot = Shot(
            ball_speed_mph=165.0,
            club_speed_mph=112.0,
            timestamp=datetime.now(),
            club=ClubType.DRIVER,
            launch_angle_vertical=11.0,
            launch_angle_confidence=0.8,
            spin_rpm=2700,
            spin_confidence=0.85,
            angle_source="radar",
        )

        on_shot_detected(shot)
        _wait_for_shot_finalization_idle()

        assert shot.carry_spin_adjusted is None
        assert shot.carry_status == "not_measured"


def test_server_records_the_frame_of_every_horizontal_metric(monkeypatch):
    measurement = SimpleNamespace(
        accepted=True,
        angle_deg=18.5,
        horizontal_deg=0.8,
        horizontal_confidence=0.93,
        horizontal_status="hlcmf_v2_accepted",
        horizontal_frame="target_line",
        n_snapshots=18,
        n_frames=5,
        component_std_deg=1.4,
        to_dict=lambda: {},
    )
    club_path = SimpleNamespace(
        accepted=True,
        status="accepted",
        path_deg=-1.0,
        confidence=0.8,
        n_frames=5,
        candidate_path_deg=-1.0,
        candidate_path_status="candidate_available",
        candidate_attack_angle_deg=-4.0,
        attack_angle_status="candidate_available",
        club_path_frame="target_line",
        to_dict=lambda: {},
    )
    capture = SimpleNamespace(
        trigger_timestamp=100.01,
        path=None,
        raw=b"raw",
        dump_duration_s=4.5,
        error=None,
        valid=True,
        sequence=1,
    )
    runtime = SimpleNamespace(
        horizontal_phase_reference_rad=-0.5,
        process_shot=lambda **kwargs: SimpleNamespace(
            capture=capture, measurement=measurement, club_path=club_path
        ),
    )
    monkeypatch.setattr(server_module, "iwr6843_runtime", runtime)
    monkeypatch.setattr(server_module, "get_session_logger", lambda: None)
    monkeypatch.setattr(server_module, "horizontal_offset_deg", OFFSET_DEG)
    shot = _shot()

    server_module._process_iwr6843_angle(shot)
    shot_models._attach_collision_model(shot)
    payload = shot.to_dict()

    assert payload["launch_angle_horizontal_frame"] == "target_line"
    assert payload["experimental_club_path_frame"] == "target_line"
    assert payload["experimental_face_angle_frame"] == "target_line"

    server_module._apply_camera_horizontal_decision(
        shot, 1.2, 0.75, "camera_assisted_experimental", frame="target_line"
    )
    assert shot.to_dict()["launch_angle_horizontal_frame"] == "target_line"


def test_face_angle_from_two_frames_is_labelled_mixed():
    shot = _shot(
        launch_angle_horizontal=2.0,
        launch_angle_horizontal_source="camera_assisted_experimental",
        launch_angle_horizontal_frame="target_line",
        club_path_deg=-1.0,
        club_path_frame="unit_boresight",
    )

    shot_models._attach_collision_model(shot)

    assert shot.experimental_face_angle_deg is not None
    assert shot.experimental_face_angle_frame == "mixed"
    assert shot.experimental_face_angle_status.endswith("_mixed_frames")


def test_full_ball_speed_is_given_the_shot_s_range_and_its_uncertainty(monkeypatch):
    seen = {}

    def evaluate(**kwargs):
        seen.update(kwargs)
        return {"status": "not_measured", "reason": "test"}

    monkeypatch.setattr("openflight.full_speed.evaluate_full_ball_speed", evaluate)
    calibration = Calibration.identity()
    calibration.tee_range_m = None
    monkeypatch.setattr(server_module, "iwr6843_runtime", SimpleNamespace(calibration=calibration))
    shot = _range_shot()
    shot.ball_range_m = 2.11
    shot.ball_range_uncertainty_m = 0.42
    shot.ball_range_source = "camera_prior"

    shot_models._attach_full_speeds(shot)  # pylint: disable=protected-access

    assert seen["tee_range_m"] == 2.11
    assert seen["tee_range_uncertainty_m"] == 0.42
    assert seen["tee_range_source"] == "camera_prior"
