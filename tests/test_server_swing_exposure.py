"""The kiosk wires the swing exposure: the setup's light seeds it, the box look meters it."""

from pathlib import Path

import pytest

from openflight import server
from openflight.camera import capture_runtime


def _run_kiosk_until_camera_init(monkeypatch, tmp_path, *extra):
    received = {}

    class StopAfterCameraInit(Exception):
        pass

    def fake_init_camera_capture(**kwargs):
        received.update(kwargs)
        raise StopAfterCameraInit

    monkeypatch.setattr(server, "init_camera_capture", fake_init_camera_capture)
    monkeypatch.setattr(server, "init_session_logger", lambda **kwargs: None)
    monkeypatch.setattr(server, "profile_store", None)
    monkeypatch.setattr(
        "sys.argv",
        [
            "openflight-server",
            "--camera-capture",
            "--rig-geometry",
            "config/enclosure_v3_rig_geometry.json",
            "--no-logging",
            "--profiles-path",
            str(tmp_path / "profiles.json"),
            *extra,
        ],
    )
    with pytest.raises((StopAfterCameraInit, SystemExit)):
        server.main()
    return received


def test_the_setup_light_index_reaches_the_camera(monkeypatch, tmp_path):
    received = _run_kiosk_until_camera_init(monkeypatch, tmp_path, "--camera-light-index", "0.0148")

    assert received["light_index_dn_per_us_gain"] == pytest.approx(0.0148)


def test_without_a_light_index_none_is_passed(monkeypatch, tmp_path):
    received = _run_kiosk_until_camera_init(monkeypatch, tmp_path)

    assert received["light_index_dn_per_us_gain"] is None


class _Monitor:
    def __init__(self, look):
        self._look = look

    def latest(self):
        return self._look


def test_the_resting_ball_look_is_the_box_monitor_s_latest(monkeypatch):
    monkeypatch.setattr(server, "ball_at_address_monitor", None)
    assert server.resting_ball_look() is None

    monkeypatch.setattr(server, "ball_at_address_monitor", _Monitor(None))
    assert server.resting_ball_look()["state"] == "unknown"

    look = {"state": "in_box", "center_px": [640.0, 600.0], "diameter_px": 40.0}
    monkeypatch.setattr(server, "ball_at_address_monitor", _Monitor(look))
    assert server.resting_ball_look() == look


def test_the_camera_gets_the_locator_the_light_and_the_limits_file(monkeypatch, tmp_path):
    for name in ("camera_capture_runtime", "camera_capture_config", "camera_replay_manager"):
        monkeypatch.setattr(server, name, getattr(server, name))
    made = {}

    class Runtime:
        def __init__(self, *, settings, **_kwargs):
            self.settings = settings
            made["runtime"] = self

        def set_resting_ball_locator(self, locator):
            made["locator"] = locator

        def start(self):
            return None

    monkeypatch.setattr(capture_runtime, "CameraCaptureRuntime", Runtime)

    assert server.init_camera_capture(
        output_dir=tmp_path,
        gpio_pin=17,
        width=1280,
        height=800,
        fps=120.0,
        pre_ms=150,
        post_ms=50,
        exposure_us=300,
        gain=4.0,
        stream="raw",
        rotate_180=False,
        mirror_horizontal=False,
        scaler_crop=None,
        mount_height_m=0.095,
        lateral_offset_m=0,
        horizontal_offset_deg=0,
        use_gpio_trigger=False,
        light_index_dn_per_us_gain=0.02,
    )

    settings = made["runtime"].settings
    assert made["locator"] is server.resting_ball_look
    assert settings.light_index_dn_per_us_gain == 0.02
    assert Path(settings.sensor_limits_path).name == "camera-sensor-limits.json"
