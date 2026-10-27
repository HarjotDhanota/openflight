"""Enclosure geometry file <-> live server: the only placement source, and the tilt arithmetic."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
from types import SimpleNamespace

import pytest

from openflight import server
from openflight.rig_geometry import RigGeometry

V3 = "config/enclosure_v3_rig_geometry.json"


@pytest.fixture(autouse=True)
def _restore_module_globals():
    saved = (server.rig_geometry, dict(server.rig_geometry_config))
    yield
    server.rig_geometry, server.rig_geometry_config = saved[0], saved[1]


def _fake_iwr_hardware(monkeypatch, tmp_path):
    """A capture monitor that opens nothing, and a radar config file; returns its path."""

    class FakeCaptureMonitor:
        def __init__(self, **kwargs):
            self.port = "/dev/ttyUSB0"

        def start(self, *, armed=True):
            return None

        def stop(self):
            return None

    monkeypatch.setattr("openflight.iwr6843.monitor.IWR6843CaptureMonitor", FakeCaptureMonitor)
    monkeypatch.setattr("openflight.iwr6843.monitor.tx_order_from_config", lambda _path: "normal")
    config_path = tmp_path / "snapshot.cfg"
    config_path.write_text("profileCfg 0\n", encoding="utf-8")
    return config_path


class TestTheGeometryReplacesTheFlags:
    def test_without_a_file_nothing_is_claimed(self):
        server.init_rig_geometry(None)
        assert server.rig_geometry is None
        assert server.rig_geometry_config == {"enabled": False}

    def test_the_session_records_the_file_and_what_it_derived(self):
        server.init_rig_geometry(V3)
        config = server._session_start_config()

        block = config["rig_geometry"]
        assert block["enabled"] is True
        assert block["path"].endswith("enclosure_v3_rig_geometry.json")
        # first_look reads this block; the phase centre is 7.85 mm above the RX row
        assert block["derived"]["radar_height_m"] == pytest.approx(0.0522, abs=5e-4)
        assert block["derived"]["radar_rx_row_height_m"] == pytest.approx(0.0444, abs=5e-4)
        assert block["derived"]["provenance"]

    def test_an_absent_file_is_an_error_not_a_silent_default(self):
        with pytest.raises(FileNotFoundError):
            server.init_rig_geometry("config/no_such_enclosure.json")


class TestTheExpectedOrientation:
    def test_without_a_file_the_expectation_is_named_absent(self):
        server.init_rig_geometry(None)
        expected = server._expected_inclinometer_orientation()

        assert expected["pitch_deg"] is None
        assert expected["missing"] == ["rig_geometry"]

    def test_the_v3_enclosure_expects_to_read_level(self):
        server.init_rig_geometry(V3)
        expected = server._expected_inclinometer_orientation()

        assert expected["pitch_deg"] == pytest.approx(0.0)
        assert expected["roll_deg"] == pytest.approx(0.0)


class TestTheFileTravelsWithItsProvenance:
    def test_session_preserves_loaded_geometry_after_source_file_changes(self, tmp_path):
        path = tmp_path / "rig.json"
        original = RigGeometry.from_json(V3)
        original.to_json(path)
        server.init_rig_geometry(path)
        dataclasses.replace(original, focal_px=999.0).to_json(path)

        block = server._session_start_config()["rig_geometry"]
        snapshot = block["snapshot"]
        payload = json.dumps(snapshot["parameters"], sort_keys=True, separators=(",", ":"))
        assert snapshot["sha256"] == hashlib.sha256(payload.encode("utf-8")).hexdigest()
        assert json.loads(payload) == json.loads(json.dumps(dataclasses.asdict(original)))
        path.unlink()
        assert server._session_start_config()["rig_geometry"]["snapshot"] == snapshot

    def test_session_config_cannot_mutate_the_loaded_snapshot(self):
        server.init_rig_geometry(V3)
        first = server._session_start_config()["rig_geometry"]
        first["snapshot"]["parameters"]["focal_px"] = 999.0

        second = server._session_start_config()["rig_geometry"]
        assert second["snapshot"]["parameters"]["focal_px"] == server.rig_geometry.focal_px

    def test_a_rig_without_heights_names_the_gap_instead_of_guessing(self):
        rig = dataclasses.replace(RigGeometry.from_json(V3), lens_height_above_floor_mm=None)
        setup = rig.enclosure_setup()
        assert "lens_height_above_floor_mm" in setup.missing
        assert setup.camera_mount_height_m is None
        assert setup.radar_height_m is None


class TestTheSwingServerNeedsTheRigFile:
    """No swing server run falls back to a built-in geometry."""

    @staticmethod
    def _main(monkeypatch, tmp_path, argv):
        class Started(Exception):
            pass

        received = {}

        def fake_init_iwr6843(**kwargs):
            received.update(kwargs)
            raise Started

        def started(*_args, **_kwargs):
            raise Started

        monkeypatch.setattr(server, "init_iwr6843", fake_init_iwr6843)
        monkeypatch.setattr(server, "init_camera_capture", started)
        monkeypatch.setattr(server, "start_monitor", started)
        monkeypatch.setattr(server.socketio, "run", started)
        monkeypatch.setattr(server, "init_session_logger", lambda **kwargs: None)
        monkeypatch.setattr(server, "ball_speed_correction_enabled", None)
        monkeypatch.setattr(server, "profile_store", None)
        monkeypatch.setattr(
            "sys.argv",
            [
                "openflight-server",
                "--no-logging",
                "--profiles-path",
                str(tmp_path / "profiles.json"),
                *argv,
            ],
        )
        try:
            server.main()
        except Started:
            return received
        raise AssertionError("the server should have reached the IWR start")

    @pytest.mark.parametrize("sensor", ["--iwr6843", "--camera-capture"])
    def test_the_iwr_and_camera_are_refused_without_a_rig_file(
        self, monkeypatch, tmp_path, capsys, sensor
    ):
        with pytest.raises(SystemExit):
            self._main(monkeypatch, tmp_path, [sensor])
        assert "--rig-geometry" in capsys.readouterr().err

    def test_a_rig_file_that_cannot_place_the_radar_is_refused(self, monkeypatch, tmp_path, capsys):
        path = tmp_path / "rig.json"
        dataclasses.replace(RigGeometry.from_json(V3), iwr_offset_mm=None).to_json(path)
        with pytest.raises(SystemExit):
            self._main(monkeypatch, tmp_path, ["--iwr6843", "--rig-geometry", str(path)])
        assert "iwr_offset_mm" in capsys.readouterr().err

    def test_the_radar_height_tilt_and_board_rotation_come_from_the_rig_file(
        self, monkeypatch, tmp_path
    ):
        received = self._main(monkeypatch, tmp_path, ["--iwr6843", "--rig-geometry", V3])
        assert received["radar_height_m"] == pytest.approx(0.05225, abs=1e-5)
        assert received["tilt_deg"] == pytest.approx(10.0)
        assert received["board_rotation_deg"] == 90.0

    def test_the_setup_record_s_solved_lens_height_moves_the_antennas_with_it(
        self, monkeypatch, tmp_path
    ):
        from tests.setup_files import write_setup  # noqa: PLC0415

        class Started(Exception):
            pass

        def started(*_args, **_kwargs):
            raise Started

        config_path = _fake_iwr_hardware(monkeypatch, tmp_path)
        for name in ("iwr6843_runtime", "iwr6843_runtime_config", "setup_handoff_config"):
            monkeypatch.setattr(server, name, getattr(server, name))
        monkeypatch.setattr(server, "start_monitor", started)
        monkeypatch.setattr(server, "_cleanup_hardware_for_shutdown", lambda: None)
        monkeypatch.setattr(server, "init_session_logger", lambda **kwargs: None)
        monkeypatch.setattr(server, "profile_store", None)
        setup_file = tmp_path / "setup.json"
        write_setup(setup_file, scene_lens_height_m=0.195, scene_uncertainty_m=0.01)
        monkeypatch.setattr(
            "sys.argv",
            [
                "openflight-server",
                "--no-logging",
                "--profiles-path",
                str(tmp_path / "profiles.json"),
                "--iwr6843",
                "--iwr6843-config",
                str(config_path),
                "--rig-geometry",
                V3,
                "--setup-file",
                str(setup_file),
            ],
        )

        with pytest.raises(Started):
            server.main()

        layout = server.iwr6843_runtime.calibration.antennas
        # the RX row stays the rig's 50.6 mm below the lens, now 195 mm up
        assert layout.rx_row_height_m == pytest.approx(0.195 - 0.0506, abs=1e-6)
        scene = server.setup_handoff_config["scene"]
        assert scene["lens_height_used_m"] == pytest.approx(0.195)
        assert scene["lens_height_used_source"] == "radar_solved_height"
        assert scene["lens_height_solved_uncertainty_m"] == pytest.approx(0.01)


class TestTheBoardCalibrationCarriesNoInstallation:
    """The corner-reflector calibration JSON's mount is not the rig."""

    def test_its_radar_height_never_reaches_the_calibration(self):
        from openflight.iwr6843.calibration import Calibration  # noqa: PLC0415

        calibration = Calibration.load("config/iwr6843_calibration_reference.json")
        assert "radar_height_m" not in calibration.meta
        with pytest.raises(ValueError, match="rig"):
            _ = calibration.radar_height_m

    @staticmethod
    def _init_v3(monkeypatch, tmp_path, **overrides):
        config_path = _fake_iwr_hardware(monkeypatch, tmp_path)
        monkeypatch.setattr(server, "iwr6843_runtime", None)
        setup = RigGeometry.from_json(V3).enclosure_setup()
        kwargs = {
            "port": "/dev/ttyUSB0",
            "config_path": str(config_path),
            "calibration_path": "config/iwr6843_calibration_reference.json",
            "output_dir": tmp_path,
            "trigger_pin": 17,
            "tee_range_m": 1.3,
            "net_range_m": 4.6,
            "tx_order": "auto",
            "capture_timeout_s": 12.0,
            "tilt_deg": setup.iwr_tilt_deg,
            "radar_height_m": setup.radar_height_m,
            "board_rotation_deg": setup.iwr_board_rotation_deg,
            **overrides,
        }
        assert server.init_iwr6843(**kwargs)
        runtime = server.iwr6843_runtime
        server.iwr6843_runtime = None
        return runtime

    def test_the_server_installs_the_rigs_height_tilt_and_antenna_layout(
        self, monkeypatch, tmp_path
    ):
        from openflight.iwr6843 import antennas, lcmf  # noqa: PLC0415

        runtime = self._init_v3(monkeypatch, tmp_path)
        assert math.degrees(runtime.calibration.tilt_rad) == pytest.approx(10.0)
        layout = runtime.calibration.antennas
        assert layout.basis == antennas.RIG_BASIS
        assert layout.board_rotation_deg == 90.0
        assert layout.rx_row_height_m == pytest.approx(0.0444)
        assert runtime.calibration.radar_height_m == pytest.approx(0.05225, abs=1e-5)
        effective = runtime.calibration_provenance["effective"]
        assert effective["iwr_board_rotation_deg"] == 90.0
        assert effective["antenna_layout"] == layout.as_dict()
        assert server.iwr6843_runtime_config["estimator"] == lcmf.NAME
        assert server.iwr6843_runtime_config["antenna_layout"] == layout.as_dict()

    def test_a_rig_without_the_rotation_reads_its_height_as_the_rx_row(self, monkeypatch, tmp_path):
        """Such a rig's radar height is the RX row and the board is taken as turned +90 deg."""
        from openflight.iwr6843 import antennas  # noqa: PLC0415

        runtime = self._init_v3(
            monkeypatch, tmp_path, radar_height_m=0.051, board_rotation_deg=None
        )
        layout = runtime.calibration.antennas
        assert layout == antennas.legacy_layout(0.051)
        assert runtime.calibration_provenance["effective"]["iwr_board_rotation_deg"] is None

    def test_the_server_will_not_start_the_iwr_without_them(self, monkeypatch, tmp_path):
        def no_hardware(**_kwargs):
            raise AssertionError("the IWR must not be opened without its rig geometry")

        monkeypatch.setattr("openflight.iwr6843.monitor.IWR6843CaptureMonitor", no_hardware)
        with pytest.raises(TypeError):
            server.init_iwr6843(  # pylint: disable=missing-kwoa
                port=None,
                config_path="config/iwr6843_static_range_24f3ms_53bin_iq16.cfg",
                calibration_path="config/iwr6843_calibration_reference.json",
                output_dir=tmp_path,
                trigger_pin=17,
                tee_range_m=None,
                net_range_m=4.6,
                tx_order="auto",
                capture_timeout_s=12.0,
            )


class TestThe320x200StripIsRefusedForMeasurement:
    """320x200 is a movable crop whose strip offset the camera models do not apply."""

    @staticmethod
    def _capture(*offsets, status="uniform"):
        contexts = [
            {"startup": {"driver": {"strip_y_offset": {"value_px": value}}}} for value in offsets
        ]
        return SimpleNamespace(
            valid=True,
            metadata={"capture_mode": {"context_status": status, "contexts": contexts}},
        )

    @pytest.fixture
    def strip_mode(self, monkeypatch):
        monkeypatch.setattr(server, "camera_capture_config", {"width": 320, "height": 200})

    def test_a_centred_strip_is_measured(self, strip_mode):
        assert server._strip_offset_refusal(self._capture(0)) is None

    @pytest.mark.parametrize("offsets", [(30,), (-70,), (None,), ()])
    def test_a_moved_or_unrecorded_strip_is_refused(self, strip_mode, offsets):
        reason = server._strip_offset_refusal(self._capture(*offsets))
        assert reason is not None and "strip" in reason

    def test_other_modes_have_no_strip(self, monkeypatch):
        monkeypatch.setattr(server, "camera_capture_config", {"width": 640, "height": 400})
        assert server._strip_offset_refusal(self._capture(30)) is None

    def test_the_shot_keeps_its_radar_values_and_says_why(self, strip_mode, monkeypatch):
        from datetime import datetime  # noqa: PLC0415

        from openflight.launch_monitor import Shot  # noqa: PLC0415

        monkeypatch.setattr(
            server,
            "_load_camera_capture_archive",
            lambda _capture: pytest.fail("a refused mode should not be decoded"),
        )
        shot = Shot(ball_speed_mph=110.0, timestamp=datetime.now())

        server._fuse_camera_measurements(shot, self._capture(40))

        assert shot.experimental_fused_status == "rejected_strip_offset_not_modelled"
        assert shot.ball_speed_mph == 110.0
