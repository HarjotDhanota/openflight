"""The kiosk's side of the 13 Oct tester contract.

``--camera-lens-id`` puts the selected lens's focal length under every camera
metric and records it; ``--camera-ball-box`` re-locates the resting ball before
each swing and gives the shot its own range; ``--tee-side-offset-m`` reaches the
radar launch fit; ``GET /api/target-line`` and ``GET /api/ball-at-address`` serve
the tester page and the kiosk's Live tab.
"""

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

    assert unset == {
        "status": "not_set",
        "yaw_deg": None,
        "total_uncertainty_deg": None,
        "source": None,
    }
    assert shown == {
        "status": "set",
        "yaw_deg": 2.4,
        "total_uncertainty_deg": 0.9,
        "source": "aim_point",
    }


def _parser():
    import argparse  # noqa: PLC0415

    parser = argparse.ArgumentParser()
    server._add_box_kiosk_arguments(parser)  # noqa: SLF001
    return parser


def test_the_target_line_flags_are_recorded_in_the_hand_off():
    args = _parser().parse_args(
        [
            "--target-line-yaw-deg",
            "2.4",
            "--target-line-uncertainty-deg",
            "0.9",
            "--target-line-source",
            "aim_point",
        ]
    )

    assert server.target_line_config(args) == {
        "status": "set",
        "yaw_deg": 2.4,
        "total_uncertainty_deg": 0.9,
        "source": "aim_point",
    }
    skipped = _parser().parse_args(["--target-line-status", "skipped"])
    assert server.target_line_config(skipped)["status"] == "skipped"
    assert server.target_line_config(_parser().parse_args([]))["status"] == "not_set"
    boxed = _parser().parse_args(
        ["--camera-ball-box", "480,380,800,520", "--camera-lens-id", "6mm"]
    )
    assert boxed.camera_ball_box == "480,380,800,520"
    assert boxed.camera_lens_id == "6mm"


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


def test_the_measured_side_offset_reaches_the_radar_launch_fit(monkeypatch, tmp_path):
    seen: dict = {}

    class Stop(Exception):
        pass

    def stop(**kwargs):
        seen.update(kwargs)
        raise Stop

    monkeypatch.setattr(server, "init_iwr6843", stop)
    monkeypatch.setattr(server, "init_session_logger", lambda **_kwargs: None)
    monkeypatch.setattr(server, "ball_speed_correction_enabled", None)
    monkeypatch.setattr(server, "profile_store", None)
    for name in ("rig_geometry", "rig_geometry_config", "setup_handoff_config"):
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
            "--iwr6843-tee-m",
            "2.2",
            "--tee-side-offset-m",
            "-0.14",
        ],
    )

    with pytest.raises(Stop):
        server.main()

    assert seen["lateral_tee_offset_m"] == pytest.approx(-0.14)
