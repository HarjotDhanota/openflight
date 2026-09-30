"""Enclosure geometry file <-> live server: flag overrides and the tilt arithmetic."""

from __future__ import annotations

import dataclasses
import hashlib
import json
from types import SimpleNamespace

import pytest

from openflight import server
from openflight.rig_geometry import RigGeometry

V3 = "config/enclosure_v3_rig_geometry.json"


@pytest.fixture(autouse=True)
def _restore_module_globals():
    """Each test loads geometry into module state; put it back afterwards."""
    saved = (server.rig_geometry, dict(server.rig_geometry_config))
    yield
    server.rig_geometry, server.rig_geometry_config = saved[0], saved[1]


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
        # first_look decides whether numbers may be attributed to sensors by
        # the presence of exactly this block, so the derived values ride in it.
        assert block["derived"]["radar_height_m"] == pytest.approx(0.051, abs=5e-4)
        assert block["derived"]["provenance"]

    def test_a_measured_value_beats_a_typed_flag(self):
        assert server._rig_override("radar height", 0.1524, 0.051) == 0.051

    def test_a_flag_survives_where_the_rig_is_silent(self):
        assert server._rig_override("radar height", 0.1524, None) == 0.1524

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


class TestTheTiltArithmetic:
    """effective = configured + (measured - expected); the raw sum doubles on a leaning housing."""

    @staticmethod
    def _effective(configured_deg: float, measured_deg: float, expected_deg: float) -> float:
        return configured_deg + (measured_deg - expected_deg)

    def test_a_level_enclosure_on_a_level_floor_keeps_its_mount_angle(self):
        # v3: shell level, radar on a +10 deg mount, LIS3DH reads 0.
        assert self._effective(10.0, 0.0, 0.0) == pytest.approx(10.0)

    def test_a_level_enclosure_on_a_sloping_floor_is_corrected(self):
        # The floor drops the nose 2 deg; the radar really points 8 deg up.
        assert self._effective(10.0, -2.0, 0.0) == pytest.approx(8.0)

    def test_a_leaning_housing_is_not_double_counted(self):
        # v42: housing leans 10 deg and the LIS3DH, parallel to it, reads +10.
        # Summing would hand the LCMF 20 deg -- roughly 20 deg of launch angle
        # at the ~2 deg per degree of tilt the estimator carries.
        assert self._effective(10.0, 10.0, 10.0) == pytest.approx(10.0)
        naive = 10.0 + 10.0
        assert naive == pytest.approx(20.0), "the defect this test exists to prevent"

    def test_a_leaning_housing_on_a_sloping_floor_is_still_corrected(self):
        assert self._effective(10.0, 12.5, 10.0) == pytest.approx(12.5)

    def test_an_unknown_expectation_falls_back_to_the_raw_reading(self):
        # No geometry file: expected is None, the server reads it as 0.0, and
        # the behaviour is the old one rather than a crash.
        server.init_rig_geometry(None)
        expected = server._expected_inclinometer_orientation().get("pitch_deg") or 0.0
        assert self._effective(10.0, 1.5, expected) == pytest.approx(11.5)


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

    def test_the_v3_file_names_the_frame_and_what_it_is_not(self):
        text = json.loads(open(V3, encoding="utf-8").read())["provenance"]
        assert "camera image axes" in text
        assert "4-RX" in text
        assert "20260825" in text, "must say which session it does NOT describe"
        assert "PENDING checkerboard" in text, "focal_px is nominal until calibrated"

    def test_a_rig_without_heights_names_the_gap_instead_of_guessing(self):
        rig = dataclasses.replace(RigGeometry.from_json(V3), lens_height_above_floor_mm=None)
        setup = rig.enclosure_setup()
        assert "lens_height_above_floor_mm" in setup.missing
        assert setup.camera_mount_height_m is None
        assert setup.radar_height_m is None


class TestTheSwingServerNeedsTheRigFile:
    """C1 (decision D6): no swing server run falls back to July or August geometry."""

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
        # nothing past argument handling may reach hardware or serve
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

    def test_the_iwr_is_refused_without_a_rig_file(self, monkeypatch, tmp_path, capsys):
        with pytest.raises(SystemExit):
            self._main(monkeypatch, tmp_path, ["--iwr6843"])
        assert "--rig-geometry" in capsys.readouterr().err

    def test_a_rig_file_that_cannot_place_the_radar_is_refused(self, monkeypatch, tmp_path, capsys):
        path = tmp_path / "rig.json"
        dataclasses.replace(RigGeometry.from_json(V3), iwr_offset_mm=None).to_json(path)
        with pytest.raises(SystemExit):
            self._main(monkeypatch, tmp_path, ["--iwr6843", "--rig-geometry", str(path)])
        assert "iwr_offset_mm" in capsys.readouterr().err

    def test_the_radar_height_and_tilt_come_from_the_rig_file(self, monkeypatch, tmp_path):
        received = self._main(monkeypatch, tmp_path, ["--iwr6843", "--rig-geometry", V3])
        assert received["radar_height_m"] == pytest.approx(0.051)
        assert received["tilt_deg"] == pytest.approx(10.0)

    def test_the_camera_has_no_default_height(self, monkeypatch, tmp_path, capsys):
        # the 0.20955 m (8.25 in) July height is gone: no rig, no height, no start
        with pytest.raises(SystemExit):
            self._main(monkeypatch, tmp_path, ["--camera-capture"])
        assert "--rig-geometry" in capsys.readouterr().err


class TestTheBoardCalibrationCarriesNoInstallation:
    """C1: the corner-reflector JSON's July mount (0.1524 m, 10.4 deg) is not the rig."""

    def test_its_radar_height_never_reaches_the_calibration(self):
        from openflight.iwr6843.calibration import Calibration  # noqa: PLC0415

        calibration = Calibration.load("config/iwr6843_calibration_reference.json")
        assert "radar_height_m" not in calibration.meta
        with pytest.raises(ValueError, match="rig"):
            _ = calibration.radar_height_m

    def test_the_server_installs_the_rigs_height_and_tilt(self, monkeypatch, tmp_path):
        import math  # noqa: PLC0415

        class FakeCaptureMonitor:
            def __init__(self, **kwargs):
                self.port = "/dev/ttyUSB0"

            def start(self, *, armed=True):
                return None

            def stop(self):
                return None

        monkeypatch.setattr("openflight.iwr6843.monitor.IWR6843CaptureMonitor", FakeCaptureMonitor)
        monkeypatch.setattr(
            "openflight.iwr6843.monitor.tx_order_from_config", lambda _path: "normal"
        )
        monkeypatch.setattr(server, "iwr6843_runtime", None)
        config_path = tmp_path / "snapshot.cfg"
        config_path.write_text("profileCfg 0\n", encoding="utf-8")
        setup = RigGeometry.from_json(V3).enclosure_setup()

        assert server.init_iwr6843(
            port="/dev/ttyUSB0",
            config_path=str(config_path),
            calibration_path="config/iwr6843_calibration_reference.json",
            output_dir=tmp_path,
            trigger_pin=17,
            tee_range_m=1.3,
            net_range_m=4.6,
            tx_order="auto",
            capture_timeout_s=12.0,
            tilt_deg=setup.iwr_tilt_deg,
            radar_height_m=setup.radar_height_m,
        )
        calibration = server.iwr6843_runtime.calibration
        assert calibration.radar_height_m == pytest.approx(0.051)
        assert math.degrees(calibration.tilt_rad) == pytest.approx(10.0)
        server.iwr6843_runtime = None

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
    """C12: 320x200 is a movable crop whose strip offset the camera models do not apply."""

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


def test_the_tester_refuses_320x200_for_measurement():
    from openflight.camera import tester_server as ts  # noqa: PLC0415

    with pytest.raises(ValueError, match="320x200"):
        ts._reference_ball_camera(  # pylint: disable=protected-access
            ts.ARMS["arm1"], V3, {"camera_pitch_deg": 0.0}, None, None
        )
