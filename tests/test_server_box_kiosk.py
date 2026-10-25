"""The kiosk's side of the setup contract: the lens id, the record's tee side offset,
and the target-line and ball-at-address routes."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest

from openflight import server
from openflight.camera import ball_at_address
from openflight.iwr6843.calibration import Calibration
from openflight.iwr6843.runtime import IWR6843Runtime
from tests.test_ball_at_address import BOX, SETUP, _scene
from tests.test_iwr6843_runtime_club import FakeMonitor

RIG = Path(__file__).resolve().parents[1] / "config" / "enclosure_v3_rig_geometry.json"


@pytest.fixture(name="kiosk_state")
def _kiosk_state(monkeypatch):
    for name in (
        "rig_geometry",
        "rig_geometry_config",
        "setup_handoff_config",
        "ball_at_address_monitor",
    ):
        monkeypatch.setattr(server, name, getattr(server, name))


def test_the_selected_lens_scales_the_rig_s_focal_length_and_is_recorded(kiosk_state):
    server.init_rig_geometry(RIG)
    stock_focal = server.rig_geometry.focal_px
    params = server.rig_geometry_config["rig_geometry_params_sha256"]

    server.init_camera_lens("6mm")

    assert server.rig_geometry.focal_px == pytest.approx(stock_focal * 6.0 / 2.8)
    lens = server.rig_geometry_config["lens"]
    assert lens["lens_id"] == "6mm"
    assert lens["intrinsics"] == "nominal"
    assert "nominal intrinsics" in lens["note"]
    # the setup's binding stays the rig file's own parameters
    assert server.rig_geometry_config["rig_geometry_params_sha256"] == params
    with pytest.raises(ValueError):
        server.init_camera_lens("12mm")


def test_without_a_lens_flag_the_rig_file_s_lens_is_recorded_as_such(kiosk_state):
    server.init_rig_geometry(RIG)

    server.init_camera_lens(None)

    assert server.rig_geometry_config["lens"]["lens_id"] is None
    assert server.rig_geometry_config["lens"]["effective_lens_id"] == "stock-2.8mm"


def test_the_target_line_endpoint(kiosk_state):
    client = server.app.test_client()
    server.setup_handoff_config = {}

    unset = client.get("/api/target-line").get_json()
    server.setup_handoff_config = {
        "target_line": {
            "status": "set",
            "yaw_deg": 2.4,
            "total_uncertainty_deg": 0.9,
            "source": "aim_point",
        }
    }
    shown = client.get("/api/target-line").get_json()

    assert unset == dict.fromkeys(("yaw_deg", "total_uncertainty_deg", "source"), None) | {
        "status": "not_set"
    }
    assert shown == server.setup_handoff_config["target_line"]


def test_the_ball_at_address_endpoint(kiosk_state):
    client = server.app.test_client()
    server.ball_at_address_monitor = None
    unknown = client.get("/api/ball-at-address").get_json()

    monitor = ball_at_address.BallAtAddress(box_px=BOX, setup_ball=SETUP, setup_range_m=2.2)
    monitor.observe(_scene([(641.0, 451.0, 20.0)]), at=5.0)
    server.ball_at_address_monitor = monitor
    seen = client.get("/api/ball-at-address").get_json()

    assert unknown["state"] == "unknown"
    assert seen["state"] == "in_box"
    assert seen["range_m"] == pytest.approx(2.2)


def test_a_shot_takes_its_own_re_located_range_without_changing_the_setup(kiosk_state):
    calibration = Calibration.identity()
    calibration.tee_range_m = 2.2
    runtime = IWR6843Runtime(
        capture_monitor=FakeMonitor(), calibration=calibration, net_range_m=4.6
    )
    seen = {}

    def fake_lcmf(_raw, shot_calibration, **_kwargs):
        seen["tee"] = shot_calibration.tee_range_m
        return None

    with (
        patch(
            "openflight.iwr6843.runtime.prepare_lcmf_capture",
            return_value=SimpleNamespace(vertical=object()),
        ),
        patch("openflight.iwr6843.runtime.estimate_lcmf_v1", side_effect=fake_lcmf),
    ):
        runtime.process_shot(impact_timestamp=1.0, ball_speed_mph=100.0, club="7i", tee_range_m=2.6)

    assert seen["tee"] == pytest.approx(2.6)
    assert calibration.tee_range_m == pytest.approx(2.2)
    snapshot = runtime.replay_config_snapshot(
        ball_speed_mph=100.0, tee_range_m=2.6, ball_at_address={"state": "in_box"}
    )
    assert snapshot["per_shot_inputs"]["tee_range_m"] == 2.6
    assert snapshot["per_shot_inputs"]["ball_at_address"] == {"state": "in_box"}


def test_the_shot_s_range_comes_from_the_last_look_before_it(kiosk_state):
    monitor = ball_at_address.BallAtAddress(box_px=BOX, setup_ball=SETUP, setup_range_m=2.2)
    monitor.observe(_scene([(700.0, 420.0, 16.0)]), at=50.0)
    server.ball_at_address_monitor = monitor

    look = server.shot_ball_at_address(55.0)
    missing = server.shot_ball_at_address(40.0)

    assert look["state"] == "in_box"
    assert look["range_m"] == pytest.approx(2.75, rel=0.1)
    assert missing is None


def test_the_monitor_looks_at_the_camera_s_latest_frame_only_when_idle(kiosk_state):
    monitor = ball_at_address.BallAtAddress(box_px=BOX, setup_ball=SETUP, setup_range_m=2.2)

    class Frame:
        image = _scene([(641.0, 451.0, 20.0)])

    class Runtime:
        def __init__(self, busy):
            self.busy = busy

        def status(self):
            return {"collecting_tail": self.busy, "saving": False, "pending_saves": 0}

        def recent_frames(self, count, timeout_s=2.0):
            return [Frame()]

    assert server.ball_at_address_look(monitor, Runtime(busy=True), now=1.0) is None
    look = server.ball_at_address_look(monitor, Runtime(busy=False), now=2.0)
    assert look["state"] == "in_box"
    assert isinstance(Frame.image, np.ndarray)


class _Started(Exception):
    pass


def _start_iwr_kiosk(monkeypatch, tmp_path, setup_file) -> dict:
    """main() with --iwr6843 and a stand-in radar, up to the monitor's start (after the
    setup's apply); the arguments the radar was started with."""
    seen: dict = {}
    calibration = Calibration.identity()
    calibration.meta["radar_height_m"] = 0.0522

    def init_iwr6843(**kwargs):
        seen.update(kwargs)
        server.iwr6843_runtime = IWR6843Runtime(
            capture_monitor=None, calibration=calibration, net_range_m=4.6
        )
        server.iwr6843_runtime_config = {"enabled": True}
        return True

    def started(**_kwargs):
        raise _Started

    monkeypatch.setattr(server, "init_iwr6843", init_iwr6843)
    monkeypatch.setattr(server, "start_monitor", started)
    monkeypatch.setattr(server, "_cleanup_hardware_for_shutdown", lambda: None)
    monkeypatch.setattr(server, "init_session_logger", lambda **_kwargs: None)
    monkeypatch.setattr(server, "ball_speed_correction_enabled", None)
    monkeypatch.setattr(server, "profile_store", None)
    for name in (
        "rig_geometry",
        "rig_geometry_config",
        "setup_handoff_config",
        "iwr6843_runtime",
        "iwr6843_runtime_config",
        # a record's aim moves the target line; it must not outlive the test
        "horizontal_offset_deg",
        "_target_line_revision",
        "_target_line_since",
    ):
        monkeypatch.setattr(server, name, getattr(server, name))
    monkeypatch.setattr(
        server.sys,
        "argv",
        [
            "openflight-server",
            "--iwr6843",
            "--no-logging",
            "--profiles-path",
            str(tmp_path / "profiles.json"),
            "--rig-geometry",
            str(RIG),
            "--setup-file",
            str(setup_file),
        ],
    )
    with pytest.raises(_Started):
        server.main()
    return seen


def test_the_record_s_measured_side_offset_reaches_the_radar_launch_fit(monkeypatch, tmp_path):
    from tests.setup_files import write_setup  # noqa: PLC0415

    setup_file = tmp_path / "setup.json"
    write_setup(
        setup_file, ball_range={"range_m": 2.2, "uncertainty_m": 0.1, "side_offset_m": -0.14}
    )

    seen = _start_iwr_kiosk(monkeypatch, tmp_path, setup_file)

    # the radar starts on the rig's camera axis; the record's side offset replaces it
    assert seen["lateral_tee_offset_m"] == pytest.approx(server._rig_tee_lateral_offset_m())  # noqa: SLF001
    assert server.iwr6843_runtime.calibration.lateral_tee_offset_m == pytest.approx(-0.14)
    assert server.iwr6843_runtime.range_prior.range_m == pytest.approx(2.2)
    assert server.setup_handoff_config["scene"]["tee_side_offset_m"] == pytest.approx(-0.14)


@pytest.mark.parametrize("measured", [False, True])
def test_only_an_assumed_net_range_is_warned_at_start(monkeypatch, tmp_path, caplog, measured):
    from tests.setup_files import write_setup  # noqa: PLC0415

    caplog.set_level("WARNING", logger="openflight.server")
    setup_file = tmp_path / "setup.json"
    if measured:
        write_setup(setup_file, aim_yaw_deg=1.0, net_range_m=3.9)

    _start_iwr_kiosk(monkeypatch, tmp_path, setup_file)

    net = server.setup_handoff_config["net_range"]
    warned = any("Net range 4.60 m is ASSUMED" in r.getMessage() for r in caplog.records)
    assert (net["assumed"], warned) == (not measured, not measured)
    if measured:
        assert net["net_range_m"] == pytest.approx(3.9)
        assert not any("ASSUMED" in r.getMessage() for r in caplog.records)


def test_the_camera_gate_follows_the_re_located_ball_not_the_setup_ball(kiosk_state, monkeypatch):
    """A ball teed elsewhere in the box is found where it now rests."""
    monitor = ball_at_address.BallAtAddress(box_px=BOX, setup_ball=SETUP, setup_range_m=2.2)
    monitor.observe(_scene([(700.0, 420.0, 16.0)]), at=50.0)
    server.ball_at_address_monitor = monitor
    monkeypatch.setattr(server, "camera_capture_config", {"setup_ball": dict(SETUP)})

    moved = server.shot_reference_ball(55.0)
    before = server.shot_reference_ball(10.0)

    assert moved["x"] == pytest.approx(700.0, abs=1.5)
    assert moved["diameter_px"] == pytest.approx(16.0, abs=2.0)
    assert moved["source"] == "ball_at_address"
    assert before == {**SETUP, "source": "setup_ball"}
