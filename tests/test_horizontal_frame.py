"""D36 item 2: one horizontal-angle zero model for every horizontal angle.

Two layers: each sensor's own boresight zero (the IWR's horizontal phase
reference, the camera's optical axis), then ONE unit-to-target yaw. The yaw is
positive when the target line lies to the right of the unit boresight, seen
from behind the unit, and every reported horizontal angle is its angle from
the boresight minus that yaw, so + still means right of the target line.
"""

from __future__ import annotations

import sys
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from openflight import server as server_module
from openflight.clubs import ClubType
from openflight.horizontal_frame import (
    TARGET_LINE,
    UNIT_BORESIGHT,
    frame_for,
    resolve_target_yaw,
    to_boresight_deg,
    to_reported_deg,
)
from openflight.iwr6843.lcmf import LCMFResult
from openflight.iwr6843.runtime import IWR6843Runtime
from openflight.launch_monitor import Shot

YAW_DEG = 2.5  # the target line lies 2.5 deg right of the unit boresight


def test_frame_is_the_boresight_until_a_yaw_is_set():
    assert frame_for(None) == UNIT_BORESIGHT == "unit_boresight"
    assert frame_for(0.0) == UNIT_BORESIGHT
    assert frame_for(YAW_DEG) == TARGET_LINE == "target_line"
    assert frame_for(-YAW_DEG) == TARGET_LINE


def test_reported_angle_is_the_boresight_angle_minus_the_yaw():
    # A ball flying straight down the target line reads +yaw from the boresight
    assert to_reported_deg(YAW_DEG, YAW_DEG) == pytest.approx(0.0)
    assert to_reported_deg(4.0, YAW_DEG) == pytest.approx(1.5)
    assert to_reported_deg(4.0, None) == pytest.approx(4.0)
    assert to_reported_deg(179.0, -2.0) == pytest.approx(-179.0)
    assert to_boresight_deg(to_reported_deg(4.0, YAW_DEG), YAW_DEG) == pytest.approx(4.0)


def test_the_two_flags_must_agree_when_both_are_given():
    assert resolve_target_yaw(camera_deg=None, iwr_deg=None) is None
    assert resolve_target_yaw(camera_deg=YAW_DEG, iwr_deg=None) == YAW_DEG
    assert resolve_target_yaw(camera_deg=None, iwr_deg=YAW_DEG) == YAW_DEG
    assert resolve_target_yaw(camera_deg=YAW_DEG, iwr_deg=YAW_DEG) == YAW_DEG
    with pytest.raises(ValueError, match="same value and sign"):
        resolve_target_yaw(camera_deg=YAW_DEG, iwr_deg=-YAW_DEG)


def _stub_main_runtime(monkeypatch):
    monkeypatch.setattr(server_module, "start_monitor", lambda **kwargs: None)
    monkeypatch.setattr(server_module, "load_sim_config", lambda: [])
    monkeypatch.setattr(server_module, "build_connectors", lambda *args, **kwargs: [])
    monkeypatch.setattr(server_module, "init_session_logger", lambda **kwargs: None)
    monkeypatch.setattr(server_module, "_cleanup_hardware_for_shutdown", lambda: None)
    monkeypatch.setattr(server_module.socketio, "run", lambda *args, **kwargs: None)


def test_kiosk_refuses_two_different_yaws(monkeypatch, capsys):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "openflight-server",
            "--mock",
            "--no-logging",
            "--camera-capture-horizontal-offset-deg",
            "2.5",
            "--iwr6843-azimuth-offset-deg",
            "-2.5",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        server_module.main()

    assert exc_info.value.code == 2
    error = capsys.readouterr().err
    assert "--camera-capture-horizontal-offset-deg" in error
    assert "--iwr6843-azimuth-offset-deg" in error


@pytest.mark.parametrize(
    "flag", ["--camera-capture-horizontal-offset-deg", "--iwr6843-azimuth-offset-deg"]
)
def test_one_flag_sets_the_yaw_for_every_sensor(monkeypatch, flag):
    _stub_main_runtime(monkeypatch)
    monkeypatch.setattr(server_module, "horizontal_target_yaw_deg", None)
    monkeypatch.setattr(sys, "argv", ["openflight-server", "--mock", "--no-logging", flag, "2.5"])

    server_module.main()

    assert server_module.horizontal_target_yaw_deg == pytest.approx(YAW_DEG)


def _runtime(**kwargs):
    return IWR6843Runtime(
        capture_monitor=SimpleNamespace(
            capture_for_shot=lambda _ts, timeout_s: SimpleNamespace(
                valid=True, raw=b"x" * 32, path=None, error=None
            )
        ),
        calibration=object(),
        net_range_m=4.064,
        **kwargs,
    )


@pytest.mark.parametrize(("yaw", "frame"), [(0.0, "unit_boresight"), (YAW_DEG, "target_line")])
def test_iwr_horizontal_launch_subtracts_the_yaw_and_names_its_frame(yaw, frame):
    measurement = LCMFResult(
        status="accepted", angle_deg=18.0, horizontal_deg=3.3, horizontal_status="ok"
    )
    with (
        patch(
            "openflight.iwr6843.runtime.prepare_lcmf_capture",
            return_value=SimpleNamespace(vertical=object()),
        ),
        patch("openflight.iwr6843.runtime.estimate_lcmf_v1", return_value=measurement),
    ):
        result = _runtime(azimuth_offset_deg=yaw).process_shot(
            impact_timestamp=1.0, ball_speed_mph=100.0, club="9i"
        )

    assert result.measurement.horizontal_deg == pytest.approx(3.3 - yaw)
    assert result.measurement.horizontal_raw_deg == pytest.approx(3.3)
    assert result.measurement.horizontal_frame == frame
    assert result.measurement.to_dict()["horizontal_frame"] == frame


def test_iwr_club_path_gets_the_same_yaw_as_the_ball():
    seen = {}

    def fake_club(_raw, _cal, **kwargs):
        seen.update(kwargs)
        return SimpleNamespace(status="accepted")

    class Measurement:
        tdm_sign_used = 1

    with (
        patch(
            "openflight.iwr6843.runtime.prepare_lcmf_capture",
            return_value=SimpleNamespace(vertical=object()),
        ),
        patch("openflight.iwr6843.runtime.estimate_lcmf_v1", return_value=Measurement()),
        patch("openflight.iwr6843.runtime.estimate_club_path", side_effect=fake_club),
    ):
        _runtime(azimuth_offset_deg=YAW_DEG).process_shot(
            impact_timestamp=1.0, ball_speed_mph=100.0, club="7i", club_speed_mph=74.0
        )

    assert seen["target_yaw_deg"] == YAW_DEG


def _shot(**kwargs) -> Shot:
    return Shot(
        ball_speed_mph=100.0,
        club_speed_mph=80.0,
        timestamp=datetime.now(),
        impact_timestamp=100.0,
        club=ClubType.IRON_7,
        **kwargs,
    )


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
    monkeypatch.setattr(server_module, "horizontal_target_yaw_deg", YAW_DEG)
    shot = _shot()

    server_module._process_iwr6843_angle(shot)
    server_module._attach_experimental_face_angle(shot)
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

    server_module._attach_experimental_face_angle(shot)

    assert shot.experimental_face_angle_deg is not None
    assert shot.experimental_face_angle_frame == "mixed"
    assert shot.experimental_face_angle_status.endswith("_mixed_frames")


def test_kld7_angles_take_the_same_yaw(monkeypatch):
    """The K-LD7's own offset is its sensor zero; the target yaw is still the one model's."""
    monkeypatch.setattr(server_module, "horizontal_target_yaw_deg", YAW_DEG)

    assert server_module._reported_horizontal_deg(4.0) == pytest.approx(1.5)
    assert server_module._horizontal_frame() == "target_line"
    monkeypatch.setattr(server_module, "horizontal_target_yaw_deg", None)
    assert server_module._reported_horizontal_deg(4.0) == pytest.approx(4.0)
    assert server_module._horizontal_frame() == "unit_boresight"
