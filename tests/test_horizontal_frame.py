"""D36 item 2: one horizontal-angle zero model for every horizontal angle.

Two layers: each sensor's own boresight zero (the IWR's horizontal phase
reference, the camera's optical axis), then ONE horizontal offset, added once to
every angle. The offset is -(target yaw): the target yaw is + when the target
line lies right of the unit boresight, seen from behind the unit, so the offset
is + when the boresight points right of the target line (the flags' existing
meaning). A ball hit straight down the target line reports 0.
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
    offset_from_target_yaw,
    resolve_horizontal_offset,
    to_boresight_deg,
    to_reported_deg,
)
from openflight.iwr6843.lcmf import LCMFResult
from openflight.iwr6843.runtime import IWR6843Runtime
from openflight.launch_monitor import Shot

YAW_DEG = 3.0  # the target line lies 3 deg right of the unit boresight
OFFSET_DEG = -3.0  # what the D34 hand-off passes to both flags for that yaw


def test_the_hand_off_value_is_minus_the_target_yaw():
    assert offset_from_target_yaw(YAW_DEG) == OFFSET_DEG


def test_frame_is_the_boresight_until_an_offset_is_set():
    assert frame_for(None) == UNIT_BORESIGHT == "unit_boresight"
    assert frame_for(0.0) == UNIT_BORESIGHT
    assert frame_for(OFFSET_DEG) == TARGET_LINE == "target_line"
    assert frame_for(-OFFSET_DEG) == TARGET_LINE


def test_reported_angle_is_the_boresight_angle_plus_the_offset():
    # a ball straight down the target line reads +yaw from the boresight: it reports 0
    assert to_reported_deg(YAW_DEG, OFFSET_DEG) == pytest.approx(0.0)
    assert to_reported_deg(4.0, OFFSET_DEG) == pytest.approx(1.0)
    assert to_reported_deg(4.0, None) == pytest.approx(4.0)
    assert to_reported_deg(-179.0, -2.0) == pytest.approx(179.0)
    assert to_boresight_deg(to_reported_deg(4.0, OFFSET_DEG), OFFSET_DEG) == pytest.approx(4.0)


def test_the_two_flags_must_agree_when_both_are_given():
    assert resolve_horizontal_offset(camera_deg=None, iwr_deg=None) is None
    assert resolve_horizontal_offset(camera_deg=OFFSET_DEG, iwr_deg=None) == OFFSET_DEG
    assert resolve_horizontal_offset(camera_deg=None, iwr_deg=OFFSET_DEG) == OFFSET_DEG
    assert resolve_horizontal_offset(camera_deg=OFFSET_DEG, iwr_deg=OFFSET_DEG) == OFFSET_DEG
    with pytest.raises(ValueError, match="must be equal"):
        resolve_horizontal_offset(camera_deg=OFFSET_DEG, iwr_deg=-OFFSET_DEG)


def _stub_main_runtime(monkeypatch):
    monkeypatch.setattr(server_module, "start_monitor", lambda **kwargs: None)
    monkeypatch.setattr(server_module, "load_sim_config", lambda: [])
    monkeypatch.setattr(server_module, "build_connectors", lambda *args, **kwargs: [])
    monkeypatch.setattr(server_module, "init_session_logger", lambda **kwargs: None)
    monkeypatch.setattr(server_module, "_cleanup_hardware_for_shutdown", lambda: None)
    monkeypatch.setattr(server_module.socketio, "run", lambda *args, **kwargs: None)


def test_kiosk_refuses_two_different_offsets(monkeypatch, capsys):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "openflight-server",
            "--mock",
            "--no-logging",
            "--camera-capture-horizontal-offset-deg",
            "-3",
            "--iwr6843-azimuth-offset-deg",
            "3",
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
def test_one_flag_sets_the_offset_for_every_sensor(monkeypatch, flag):
    _stub_main_runtime(monkeypatch)
    monkeypatch.setattr(server_module, "horizontal_offset_deg", None)
    monkeypatch.setattr(sys, "argv", ["openflight-server", "--mock", "--no-logging", flag, "-3"])

    server_module.main()

    assert server_module.horizontal_offset_deg == pytest.approx(OFFSET_DEG)


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


def _iwr_horizontal(boresight_deg, offset_deg):
    measurement = LCMFResult(
        status="accepted", angle_deg=18.0, horizontal_deg=boresight_deg, horizontal_status="ok"
    )
    with (
        patch(
            "openflight.iwr6843.runtime.prepare_lcmf_capture",
            return_value=SimpleNamespace(vertical=object()),
        ),
        patch("openflight.iwr6843.runtime.estimate_lcmf_v1", return_value=measurement),
    ):
        return (
            _runtime(azimuth_offset_deg=offset_deg)
            .process_shot(impact_timestamp=1.0, ball_speed_mph=100.0, club="9i")
            .measurement
        )


def test_a_ball_straight_down_the_target_line_reports_zero():
    """The sign, pinned: yaw +3 deg, hand-off flag -3, a ball along the target line reads 0."""
    flag = offset_from_target_yaw(YAW_DEG)

    measured = _iwr_horizontal(YAW_DEG, flag)  # the IWR sees it 3 deg right of boresight

    assert flag == -3.0
    assert measured.horizontal_deg == pytest.approx(0.0)
    assert measured.horizontal_frame == "target_line"


@pytest.mark.parametrize(
    ("offset", "frame"), [(0.0, "unit_boresight"), (OFFSET_DEG, "target_line")]
)
def test_iwr_horizontal_launch_adds_the_offset_and_names_its_frame(offset, frame):
    measured = _iwr_horizontal(3.3, offset)

    assert measured.horizontal_deg == pytest.approx(3.3 + offset)
    assert measured.horizontal_raw_deg == pytest.approx(3.3)
    assert measured.horizontal_frame == frame
    assert measured.to_dict()["horizontal_frame"] == frame


def test_iwr_club_path_gets_the_same_offset_as_the_ball():
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
        _runtime(azimuth_offset_deg=OFFSET_DEG).process_shot(
            impact_timestamp=1.0, ball_speed_mph=100.0, club="7i", club_speed_mph=74.0
        )

    assert seen["aim_offset_deg"] == OFFSET_DEG


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
    monkeypatch.setattr(server_module, "horizontal_offset_deg", OFFSET_DEG)
    shot = _shot()

    server_module._process_iwr6843_angle(shot)
    server_module._attach_collision_model(shot)
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

    server_module._attach_collision_model(shot)

    assert shot.experimental_face_angle_deg is not None
    assert shot.experimental_face_angle_frame == "mixed"
    assert shot.experimental_face_angle_status.endswith("_mixed_frames")


def test_kld7_angles_take_the_same_yaw(monkeypatch):
    """The K-LD7's own offset is its sensor zero; the horizontal offset is the one model's."""
    monkeypatch.setattr(server_module, "horizontal_offset_deg", OFFSET_DEG)

    assert server_module._reported_horizontal_deg(4.0) == pytest.approx(1.0)
    assert server_module._horizontal_frame() == "target_line"
    monkeypatch.setattr(server_module, "horizontal_offset_deg", None)
    assert server_module._reported_horizontal_deg(4.0) == pytest.approx(4.0)
    assert server_module._horizontal_frame() == "unit_boresight"
