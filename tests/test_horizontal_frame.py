"""One horizontal-angle zero model: each sensor's boresight zero, then one offset.

The offset is -(target yaw); target yaw is + when the target line lies right of
the unit boresight, seen from behind. A ball down the target line reports 0.
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
    to_boresight_deg,
    to_reported_deg,
)
from openflight.iwr6843.lcmf import LCMFResult
from openflight.iwr6843.runtime import IWR6843Runtime
from openflight.launch_monitor import Shot

YAW_DEG = 3.0  # the target line lies 3 deg right of the unit boresight
OFFSET_DEG = -3.0  # the one horizontal offset for that yaw


def test_frame_is_the_boresight_until_an_offset_is_set():
    assert frame_for(None) == UNIT_BORESIGHT == "unit_boresight"
    # an explicit 0.0 is a target line straight down the boresight
    assert frame_for(0.0) == TARGET_LINE
    assert frame_for(OFFSET_DEG) == TARGET_LINE == "target_line"
    assert frame_for(-OFFSET_DEG) == TARGET_LINE


def test_reported_angle_is_the_boresight_angle_plus_the_offset():
    # a ball straight down the target line reads +yaw from the boresight: it reports 0
    assert to_reported_deg(YAW_DEG, OFFSET_DEG) == pytest.approx(0.0)
    assert to_reported_deg(4.0, OFFSET_DEG) == pytest.approx(1.0)
    assert to_reported_deg(4.0, None) == pytest.approx(4.0)
    assert to_reported_deg(-179.0, -2.0) == pytest.approx(179.0)
    assert to_boresight_deg(to_reported_deg(4.0, OFFSET_DEG), OFFSET_DEG) == pytest.approx(4.0)


def _stub_main_runtime(monkeypatch):
    monkeypatch.setattr(server_module, "start_monitor", lambda **kwargs: None)
    monkeypatch.setattr(server_module, "load_sim_config", lambda: [])
    monkeypatch.setattr(server_module, "build_connectors", lambda *args, **kwargs: [])
    monkeypatch.setattr(server_module, "init_session_logger", lambda **kwargs: None)
    monkeypatch.setattr(server_module, "_cleanup_hardware_for_shutdown", lambda: None)
    monkeypatch.setattr(server_module.socketio, "run", lambda *args, **kwargs: None)


def test_the_setup_record_s_aim_sets_the_offset_for_every_sensor(monkeypatch, tmp_path):
    from tests.setup_files import write_setup  # noqa: PLC0415

    _stub_main_runtime(monkeypatch)
    for name in ("horizontal_offset_deg", "rig_geometry", "rig_geometry_config"):
        monkeypatch.setattr(server_module, name, getattr(server_module, name))
    monkeypatch.setattr(server_module, "horizontal_offset_deg", None)
    monkeypatch.setattr(server_module, "setup_handoff_config", {})
    setup_file = tmp_path / "setup.json"
    # with --mock and no rig file the box is stamped for no lens and no rig
    write_setup(setup_file, lens_id=None, rig="no_rig_geometry", aim_yaw_deg=YAW_DEG)
    monkeypatch.setattr(
        sys,
        "argv",
        ["openflight-server", "--mock", "--no-logging", "--setup-file", str(setup_file)],
    )

    server_module.main()

    assert server_module.horizontal_offset_deg == pytest.approx(OFFSET_DEG)
    assert server_module.target_line_in_force()["frame"] == TARGET_LINE


def test_without_an_aim_every_sensor_stays_in_the_boresight_frame(monkeypatch, tmp_path):
    _stub_main_runtime(monkeypatch)
    monkeypatch.setattr(server_module, "horizontal_offset_deg", None)
    monkeypatch.setattr(server_module, "setup_handoff_config", {})
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "openflight-server",
            "--mock",
            "--no-logging",
            "--setup-file",
            str(tmp_path / "none.json"),
        ],
    )

    server_module.main()

    assert server_module.horizontal_offset_deg is None
    assert server_module.target_line_in_force()["frame"] == UNIT_BORESIGHT


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
    ("offset", "frame"),
    [(None, "unit_boresight"), (0.0, "target_line"), (OFFSET_DEG, "target_line")],
)
def test_iwr_horizontal_launch_adds_the_offset_and_names_its_frame(offset, frame):
    measured = _iwr_horizontal(3.3, offset)

    assert measured.horizontal_deg == pytest.approx(3.3 + (offset or 0.0))
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


def test_kld7_angles_take_the_same_yaw(monkeypatch):
    """The K-LD7's own offset is its sensor zero, separate from the horizontal offset."""
    monkeypatch.setattr(server_module, "horizontal_offset_deg", OFFSET_DEG)

    assert server_module._reported_horizontal_deg(4.0) == pytest.approx(1.0)
    assert server_module._horizontal_frame() == "target_line"
    monkeypatch.setattr(server_module, "horizontal_offset_deg", None)
    assert server_module._reported_horizontal_deg(4.0) == pytest.approx(4.0)
    assert server_module._horizontal_frame() == "unit_boresight"
