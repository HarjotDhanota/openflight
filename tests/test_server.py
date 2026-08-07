"""Tests for server module."""

import json
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from openflight import server as server_module
from openflight.iwr6843 import Calibration
from openflight.kld7.types import KLD7Angle
from openflight.launch_monitor import ClubType, Shot
from openflight.ops243 import UART_BAUD_COMMANDS
from openflight.server import (
    MockLaunchMonitor,
    MockSwingSpeedMonitor,
    estimate_launch_angle,
    on_shot_detected,
    radar_launch_is_plausible,
    shot_to_dict,
    swing_speed_to_dict,
    swing_speed_to_shot_dict,
)
from openflight.swing_speed import SwingSpeedEvent


class TestShutdownCleanup:
    """Tests for UI/server shutdown hardware cleanup."""

    def test_shutdown_cleanup_continues_if_kld7_stop_fails(self, monkeypatch):
        """One hardware cleanup failure must not skip OPS rolling-buffer cleanup."""
        calls = []

        class FailingKLD7:
            def stop(self):
                calls.append("kld7_vertical.stop")
                raise RuntimeError("stale kld7 stream")

        class GoodKLD7:
            def stop(self):
                calls.append("kld7_horizontal.stop")

        monkeypatch.setattr(server_module, "kld7_vertical", FailingKLD7())
        monkeypatch.setattr(server_module, "kld7_horizontal", GoodKLD7())
        monkeypatch.setattr(server_module, "iwr6843_runtime", None)
        monkeypatch.setattr(server_module, "shutdown_cleanup_started", False)
        monkeypatch.setattr(
            server_module, "stop_camera_thread", lambda: calls.append("camera_thread")
        )
        monkeypatch.setattr(server_module, "camera", None)
        monkeypatch.setattr(server_module, "stop_monitor", lambda: calls.append("stop_monitor"))

        server_module._cleanup_hardware_for_shutdown()

        assert calls == [
            "kld7_vertical.stop",
            "kld7_horizontal.stop",
            "camera_thread",
            "stop_monitor",
        ]

    def test_shutdown_cleanup_is_idempotent(self, monkeypatch):
        """Duplicate shutdown requests must not stop hardware twice."""
        calls = []

        monkeypatch.setattr(server_module, "kld7_vertical", None)
        monkeypatch.setattr(server_module, "kld7_horizontal", None)
        monkeypatch.setattr(server_module, "iwr6843_runtime", None)
        monkeypatch.setattr(server_module, "shutdown_cleanup_started", False)
        monkeypatch.setattr(server_module, "stop_camera_thread", lambda: calls.append("camera"))
        monkeypatch.setattr(server_module, "camera", None)
        monkeypatch.setattr(server_module, "stop_monitor", lambda: calls.append("monitor"))

        server_module._cleanup_hardware_for_shutdown()
        server_module._cleanup_hardware_for_shutdown()

        assert calls == ["camera", "monitor"]

    def test_shutdown_stops_iwr6843_before_ops_monitor(self, monkeypatch):
        calls = []
        monkeypatch.setattr(server_module, "kld7_vertical", None)
        monkeypatch.setattr(server_module, "kld7_horizontal", None)
        monkeypatch.setattr(
            server_module,
            "iwr6843_runtime",
            SimpleNamespace(stop=lambda: calls.append("iwr6843")),
        )
        monkeypatch.setattr(server_module, "shutdown_cleanup_started", False)
        monkeypatch.setattr(server_module, "camera", None)
        monkeypatch.setattr(server_module, "stop_camera_thread", lambda: None)
        monkeypatch.setattr(server_module, "stop_monitor", lambda: calls.append("ops243"))

        server_module._cleanup_hardware_for_shutdown()

        assert calls == ["iwr6843", "ops243"]


class TestIWR6843ShotIntegration:
    """TI angle processing must enrich, never suppress, an OPS shot."""

    def test_init_iwr6843_has_no_host_freeze_delay(self, monkeypatch, tmp_path):
        """Production capture must always request the firmware-frozen boundary ring immediately."""
        captured = {}
        calibration = Calibration.identity()

        class FakeCaptureMonitor:
            def __init__(self, **kwargs):
                captured.update(kwargs)
                self.port = "/dev/ttyUSB0"

            def start(self, *, armed=True):
                captured["armed"] = armed
                return None

            def stop(self):
                return None

        monkeypatch.setattr(Calibration, "load", lambda _path: calibration)
        monkeypatch.setattr(
            "openflight.iwr6843.monitor.IWR6843CaptureMonitor",
            FakeCaptureMonitor,
        )
        monkeypatch.setattr(
            "openflight.iwr6843.monitor.tx_order_from_config",
            lambda _path: "normal",
        )

        assert server_module.init_iwr6843(
            port="/dev/ttyUSB0",
            config_path="snapshot.cfg",
            calibration_path="cal.json",
            output_dir=tmp_path,
            trigger_pin=17,
            tee_range_m=1.575,
            net_range_m=4.6,
            tx_order="auto",
            capture_timeout_s=12.0,
        )

        assert "freeze_delay_s" not in captured
        assert captured["armed"] is False
        server_module.iwr6843_runtime = None

    def test_init_iwr6843_wires_azimuth_offset_into_runtime(self, monkeypatch, tmp_path):
        """--iwr6843-azimuth-offset-deg must reach IWR6843Runtime, not just be parsed.

        A flag that parses but never reaches the runtime silently reports every
        club path relative to boresight instead of the target line.
        """
        calibration = Calibration.identity()

        class FakeCaptureMonitor:
            def __init__(self, **kwargs):
                self.port = "/dev/ttyUSB0"

            def start(self, *, armed=True):
                return None

            def stop(self):
                return None

        monkeypatch.setattr(Calibration, "load", lambda _path: calibration)
        monkeypatch.setattr(
            "openflight.iwr6843.monitor.IWR6843CaptureMonitor",
            FakeCaptureMonitor,
        )
        monkeypatch.setattr(
            "openflight.iwr6843.monitor.tx_order_from_config",
            lambda _path: "normal",
        )

        assert server_module.init_iwr6843(
            port="/dev/ttyUSB0",
            config_path="snapshot.cfg",
            calibration_path="cal.json",
            output_dir=tmp_path,
            trigger_pin=17,
            tee_range_m=1.575,
            net_range_m=4.6,
            tx_order="auto",
            capture_timeout_s=12.0,
            azimuth_offset_deg=1.5,
        )

        assert server_module.iwr6843_runtime.azimuth_offset_deg == 1.5
        assert server_module.iwr6843_runtime_config["azimuth_offset_deg"] == 1.5
        server_module.iwr6843_runtime = None

    def test_accepted_lcmf_angle_is_applied_to_existing_shot_contract(self, monkeypatch):
        measurement = SimpleNamespace(
            accepted=True,
            angle_deg=17.42,
            n_snapshots=20,
            n_frames=6,
            component_std_deg=1.1,
            to_dict=lambda: {"estimator": "lcmf_v1", "launch_angle_deg": 17.42},
        )
        capture = SimpleNamespace(
            trigger_timestamp=100.01,
            path=Path("/tmp/test.l3dump"),
            raw=b"raw",
            dump_duration_s=7.5,
            error=None,
            valid=True,
            sequence=1,
        )
        runtime = SimpleNamespace(
            process_shot=lambda **kwargs: SimpleNamespace(
                capture=capture,
                measurement=measurement,
            )
        )
        logged = []
        session = SimpleNamespace(
            stats={"shots_detected": 2},
            log_iwr6843_capture=lambda **kwargs: logged.append(kwargs),
        )
        monkeypatch.setattr(server_module, "iwr6843_runtime", runtime)
        monkeypatch.setattr(server_module, "get_session_logger", lambda: session)

        shot = Shot(
            ball_speed_mph=100.0,
            club_speed_mph=80.0,
            timestamp=datetime.now(),
            impact_timestamp=100.0,
            club=ClubType.IRON_9,
        )
        elapsed = server_module._process_iwr6843_angle(shot)

        assert elapsed is not None
        assert shot.launch_angle_vertical == pytest.approx(17.42)
        assert shot.launch_angle_vertical_source == "radar"
        assert shot.angle_source == "radar"
        assert shot.launch_angle_horizontal is None
        assert logged[0]["shot_number"] == 3
        assert logged[0]["ball_speed_mph"] == 100.0
        assert logged[0]["measurement"]["estimator"] == "lcmf_v1"

    def test_iwr6843_horizontal_confidence_derived_from_coherence(self, monkeypatch):
        measurement = SimpleNamespace(
            accepted=True,
            angle_deg=18.5,
            horizontal_deg=2.25,
            horizontal_confidence=0.63,
            horizontal_status="hlcmf_v0_accepted",
            n_snapshots=18,
            n_frames=5,
            component_std_deg=1.4,
            to_dict=lambda: {
                "estimator": "lcmf_v1",
                "launch_angle_deg": 18.5,
                "horizontal_deg": 2.25,
                "horizontal_confidence": 0.63,
            },
        )
        capture = SimpleNamespace(
            trigger_timestamp=100.01,
            path=Path("/tmp/test.l3dump"),
            raw=b"raw",
            dump_duration_s=4.5,
            error=None,
            valid=True,
            sequence=1,
        )
        runtime = SimpleNamespace(
            process_shot=lambda **kwargs: SimpleNamespace(
                capture=capture,
                measurement=measurement,
            )
        )
        monkeypatch.setattr(server_module, "iwr6843_runtime", runtime)
        monkeypatch.setattr(server_module, "get_session_logger", lambda: None)

        shot = Shot(
            ball_speed_mph=100.0,
            club_speed_mph=80.0,
            timestamp=datetime.now(),
            impact_timestamp=100.0,
            club=ClubType.IRON_9,
        )

        server_module._process_iwr6843_angle(shot)

        assert shot.launch_angle_vertical == pytest.approx(18.5)
        assert shot.launch_angle_vertical_source == "radar"
        assert shot.launch_angle_horizontal == pytest.approx(2.25)
        assert shot.launch_angle_horizontal_source == "radar"
        # Confidence is now derived from HLCMF-v0 coherence (0.63 here), not
        # a hardcoded 0.95 -- see openflight.server.horizontal_confidence_from.
        assert shot.launch_angle_horizontal_confidence == pytest.approx(0.63)

    def test_horizontal_fallback_does_not_invent_confidence_for_lcmf_angle(self):
        shot = Shot(
            ball_speed_mph=100.0,
            club_speed_mph=80.0,
            timestamp=datetime.now(),
            club=ClubType.IRON_9,
            launch_angle_vertical=17.42,
            launch_angle_vertical_source="radar",
            angle_source="radar",
        )

        server_module._ensure_user_facing_launch_angles(shot)

        assert shot.launch_angle_vertical == pytest.approx(17.42)
        assert shot.launch_angle_vertical_source == "radar"
        assert shot.launch_angle_confidence is None
        assert shot.launch_angle_vertical_confidence is None
        assert shot.launch_angle_horizontal == 0.0
        assert shot.launch_angle_horizontal_source == "estimated"
        assert shot.launch_angle_horizontal_confidence == pytest.approx(0.35)

    def test_missing_ti_capture_preserves_ops_shot(self, monkeypatch):
        runtime = SimpleNamespace(
            process_shot=lambda **kwargs: SimpleNamespace(capture=None, measurement=None)
        )
        monkeypatch.setattr(server_module, "iwr6843_runtime", runtime)
        monkeypatch.setattr(server_module, "get_session_logger", lambda: None)
        shot = Shot(
            ball_speed_mph=100.0,
            timestamp=datetime.now(),
            club=ClubType.IRON_9,
        )

        server_module._process_iwr6843_angle(shot)

        assert shot.ball_speed_mph == 100.0
        assert shot.launch_angle_vertical is None


class TestSessionErrorLogging:
    """Session JSONL should record shot-pipeline failures, not only Python logs."""

    def test_on_shot_detected_logs_kld7_processing_error(self, monkeypatch):
        logged_errors = []

        class FailingTracker:
            orientation = "vertical"

            def snapshot_buffer(self, include_radc_payload=False):
                raise RuntimeError("snapshot failed")

            def get_angle_for_shot(self, **kwargs):
                return None

            def get_club_angle(self, **kwargs):
                return None

            def reset(self):
                return None

        monkeypatch.setattr(server_module, "kld7_vertical", FailingTracker())
        monkeypatch.setattr(server_module, "kld7_horizontal", None)
        monkeypatch.setattr(server_module, "camera_tracker", None)
        monkeypatch.setattr(server_module, "camera_enabled", False)
        monkeypatch.setattr(server_module, "monitor", None)
        monkeypatch.setattr(server_module, "debug_mode", False)
        monkeypatch.setattr(server_module, "get_session_logger", lambda: None)
        monkeypatch.setattr(
            server_module,
            "log_session_error",
            lambda error, **kwargs: logged_errors.append((error, kwargs)),
        )
        monkeypatch.setattr(server_module.socketio, "emit", lambda *args, **kwargs: None)

        shot = Shot(
            ball_speed_mph=150.0,
            club_speed_mph=100.0,
            timestamp=datetime.now(),
            club=ClubType.DRIVER,
        )
        on_shot_detected(shot)

        assert logged_errors
        assert logged_errors[0][0] == "Angle/spin-axis post-processing failed"
        assert logged_errors[0][1]["component"] == "server"
        assert logged_errors[0][1]["context"]["stage"] == "angle_postprocessing"
        assert logged_errors[0][1]["exc"].__class__.__name__ == "RuntimeError"

    def test_set_radar_config_logs_failure_to_session(self, monkeypatch):
        logged_errors = []
        emitted = []

        class FailingRadar:
            def set_min_speed_filter(self, _value):
                raise ValueError("invalid speed")

        class StubMonitor:
            radar = FailingRadar()

        monkeypatch.setattr(server_module, "monitor", StubMonitor())
        monkeypatch.setattr(server_module, "mock_mode", False)
        monkeypatch.setattr(server_module, "radar_config", {"min_speed": 10})
        monkeypatch.setattr(
            server_module,
            "log_session_error",
            lambda error, **kwargs: logged_errors.append((error, kwargs)),
        )
        monkeypatch.setattr(
            server_module.socketio,
            "emit",
            lambda event, payload: emitted.append((event, payload)),
        )
        monkeypatch.setattr(server_module, "get_session_logger", lambda: None)

        server_module.handle_set_radar_config({"min_speed": 99})

        assert logged_errors
        assert logged_errors[0][0] == "Radar config update failed"
        assert logged_errors[0][1]["context"]["stage"] == "set_radar_config"
        assert emitted[-1][0] == "radar_config_error"

    def test_set_radar_config_logs_not_connected_to_session(self, monkeypatch):
        logged_errors = []

        monkeypatch.setattr(server_module, "monitor", None)
        monkeypatch.setattr(server_module, "mock_mode", True)
        monkeypatch.setattr(
            server_module,
            "log_session_error",
            lambda error, **kwargs: logged_errors.append((error, kwargs)),
        )
        monkeypatch.setattr(server_module.socketio, "emit", lambda *args, **kwargs: None)

        server_module.handle_set_radar_config({"min_speed": 99})

        assert logged_errors
        assert "not connected" in logged_errors[0][0]


class TestKLD7Initialization:
    """Tests for K-LD7 startup wiring."""

    def _radc_args(self, enabled: bool) -> SimpleNamespace:
        return SimpleNamespace(
            experimental_kld7_radc_tuning=enabled,
            experimental_kld7_speed_tolerance=8.0,
            experimental_kld7_centroid_floor=0.65,
            experimental_kld7_spectrum_source="sum12",
            experimental_kld7_ops_bin_tol=12,
            experimental_kld7_ops_bin_penalty=4.0,
            experimental_kld7_ops_anchored_min_snr=2.5,
            experimental_kld7_vertical_impact_energy=2.5,
            experimental_kld7_horizontal_impact_energy=1.4,
            experimental_kld7_horizontal_retry_impact_energy=0.35,
            experimental_kld7_horizontal_angle_limit=30.0,
        )

    def test_radc_tuning_args_ignored_without_experimental_gate(self):
        """Experimental RADC values must not affect startup unless gated."""
        kwargs = server_module._kld7_radc_tuning_kwargs(self._radc_args(enabled=False))

        assert kwargs == server_module._DEFAULT_KLD7_RADC_TUNING

    def test_radc_tuning_args_used_with_experimental_gate(self):
        """The gated path should pass replay-discovered parameters through."""
        kwargs = server_module._kld7_radc_tuning_kwargs(self._radc_args(enabled=True))

        assert kwargs == {
            "radc_speed_tolerance_mph": 8.0,
            "radc_centroid_floor_frac": 0.65,
            "radc_spectrum_source": "sum12",
            "radc_ops_bin_outlier_tol": 12,
            "radc_ops_bin_outlier_penalty": 4.0,
            "radc_ops_anchored_peak_min_snr": 2.5,
            "radc_vertical_impact_energy_threshold": 2.5,
            "radc_horizontal_impact_energy_threshold": 1.4,
            "radc_horizontal_retry_impact_energy_threshold": 0.35,
            "radc_horizontal_angle_limit_deg": 30.0,
        }

    @pytest.mark.parametrize(
        ("raw_logging_enabled", "radc_tuning_enabled", "expected"),
        [
            (False, False, False),
            (True, False, True),
            (False, True, True),
            (True, True, True),
        ],
    )
    def test_raw_radc_logging_enabled_for_any_kld7_experiment(
        self,
        monkeypatch,
        raw_logging_enabled,
        radc_tuning_enabled,
        expected,
    ):
        """Any K-LD7 experiment path should preserve raw RADC for replay."""
        monkeypatch.setattr(
            server_module,
            "experimental_kld7_raw_radc_logging",
            raw_logging_enabled,
        )
        monkeypatch.setattr(server_module, "experimental_kld7_radc_tuning", radc_tuning_enabled)

        assert server_module._experimental_kld7_raw_radc_logging_enabled() is expected

    def test_session_start_config_records_kld7_experiment_provenance(self, monkeypatch):
        """Session logs should preserve exact experiment settings for replay."""
        tuning = {
            "radc_speed_tolerance_mph": 8.0,
            "radc_centroid_floor_frac": 0.25,
            "radc_spectrum_source": "sum12",
            "radc_ops_bin_outlier_tol": 12,
            "radc_ops_bin_outlier_penalty": 4.0,
            "radc_ops_anchored_peak_min_snr": 2.5,
            "radc_vertical_impact_energy_threshold": 2.5,
            "radc_horizontal_impact_energy_threshold": 1.4,
            "radc_horizontal_retry_impact_energy_threshold": 0.35,
            "radc_horizontal_angle_limit_deg": 30.0,
        }
        monkeypatch.setattr(server_module, "experimental_kld7_raw_radc_logging", True)
        monkeypatch.setattr(server_module, "experimental_kld7_radc_tuning", True)
        monkeypatch.setattr(server_module, "active_kld7_radc_tuning", tuning)

        config = server_module._session_start_config()

        assert config["min_speed"] == server_module.radar_config["min_speed"]
        assert config["kld7_experiments"] == {
            "trackman_calibration_enabled": False,
            "trackman_calibration_model": None,
            "raw_radc_payload_logging_enabled": True,
            "raw_radc_payload_logging_requested": True,
            "radc_tuning_enabled": True,
            "radc_tuning_params": tuning,
        }

    def test_start_monitor_writes_kld7_experiment_provenance(self, monkeypatch):
        """The session_start row should include K-LD7 experiment settings."""
        started = {}

        class FakeSessionLogger:
            def start_session(self, **kwargs):
                started.update(kwargs)

            def end_session(self):
                pass

        tuning = {
            "radc_speed_tolerance_mph": 8.0,
            "radc_centroid_floor_frac": 0.25,
            "radc_spectrum_source": "sum12",
            "radc_ops_bin_outlier_tol": 12,
            "radc_ops_bin_outlier_penalty": 4.0,
            "radc_ops_anchored_peak_min_snr": 2.5,
            "radc_vertical_impact_energy_threshold": 2.5,
            "radc_horizontal_impact_energy_threshold": 1.4,
            "radc_horizontal_retry_impact_energy_threshold": 0.35,
            "radc_horizontal_angle_limit_deg": 30.0,
        }
        monkeypatch.setattr(server_module, "monitor", None)
        monkeypatch.setattr(server_module, "camera", None)
        monkeypatch.setattr(server_module, "camera_tracker", None)
        monkeypatch.setattr(server_module, "get_session_logger", lambda: FakeSessionLogger())
        monkeypatch.setattr(server_module, "experimental_kld7_raw_radc_logging", True)
        monkeypatch.setattr(server_module, "experimental_kld7_radc_tuning", True)
        monkeypatch.setattr(server_module, "active_kld7_radc_tuning", tuning)

        server_module.start_monitor(mock=True, trigger_type="sound")

        assert started["config"]["kld7_experiments"]["trackman_calibration_enabled"] is False
        assert started["config"]["kld7_experiments"]["raw_radc_payload_logging_enabled"] is True
        assert started["config"]["kld7_experiments"]["raw_radc_payload_logging_requested"] is True
        assert started["config"]["kld7_experiments"]["radc_tuning_params"] == tuning
        server_module.stop_monitor()

    def test_init_kld7_passes_radc_tuning_parameters(self, monkeypatch):
        """Server startup should forward experimental replay knobs to KLD7Tracker."""
        import openflight.kld7 as kld7_package

        created = []

        class FakeKLD7Tracker:
            def __init__(self, **kwargs):
                self.port = kwargs["port"]
                self.kwargs = kwargs
                self.started = False
                created.append(self)

            def connect(self):
                return True

            def start(self):
                self.started = True

        monkeypatch.setattr(kld7_package, "KLD7Tracker", FakeKLD7Tracker)
        monkeypatch.setattr(server_module, "get_session_logger", lambda: None)
        monkeypatch.setattr(server_module, "kld7_vertical", None)
        monkeypatch.setattr(server_module, "kld7_horizontal", None)

        ok = server_module.init_kld7(
            port="/dev/test-kld7",
            orientation="horizontal",
            angle_offset_deg=1.5,
            base_freq=2,
            radc_speed_tolerance_mph=8.0,
            radc_centroid_floor_frac=0.65,
            radc_spectrum_source="sum12",
            radc_ops_bin_outlier_tol=12,
            radc_ops_bin_outlier_penalty=4.0,
            radc_ops_anchored_peak_min_snr=2.5,
            radc_vertical_impact_energy_threshold=2.5,
            radc_horizontal_impact_energy_threshold=1.4,
            radc_horizontal_retry_impact_energy_threshold=0.35,
            radc_horizontal_angle_limit_deg=30.0,
        )

        assert ok is True
        assert created[0].started is True
        assert server_module.kld7_horizontal is created[0]
        assert created[0].kwargs == {
            "port": "/dev/test-kld7",
            "orientation": "horizontal",
            "angle_offset_deg": 1.5,
            "base_freq": 2,
            "buffer_seconds": 6.0,
            "radc_speed_tolerance_mph": 8.0,
            "radc_centroid_floor_frac": 0.65,
            "radc_spectrum_source": "sum12",
            "radc_ops_bin_outlier_tol": 12,
            "radc_ops_bin_outlier_penalty": 4.0,
            "radc_ops_anchored_peak_min_snr": 2.5,
            "radc_vertical_impact_energy_threshold": 2.5,
            "radc_horizontal_impact_energy_threshold": 1.4,
            "radc_horizontal_retry_impact_energy_threshold": 0.35,
            "radc_horizontal_angle_limit_deg": 30.0,
            "vertical_estimator": "naive",
            "mount_tilt_deg": 18.0,
            "ball_distance_ft": 5.5,
            "vertical_flight_window_net_distance_ft": 10.0,
        }

    def test_init_kld7_defaults_to_legacy_vertical_estimator(self, monkeypatch):
        """Plain --kld7 should use the legacy bearing-average path unless opted in."""
        import openflight.kld7 as kld7_package

        created = []

        class FakeKLD7Tracker:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
                created.append(self)

            def connect(self):
                return True

            def start(self):
                pass

        monkeypatch.setattr(kld7_package, "KLD7Tracker", FakeKLD7Tracker)
        monkeypatch.setattr(server_module, "get_session_logger", lambda: None)
        monkeypatch.setattr(server_module, "kld7_vertical", None)
        monkeypatch.setattr(server_module, "kld7_horizontal", None)

        assert server_module.init_kld7(port="/dev/test-kld7") is True

        assert created[0].kwargs["vertical_estimator"] == "naive"


class TestStaticRoutes:
    """Tests for frontend static routes."""

    def test_display_route_serves_react_app(self):
        """Direct refresh of /display should return the React app."""
        client = server_module.app.test_client()

        response = client.get("/display")

        assert response.status_code == 200
        assert b'<div id="root"></div>' in response.data

    def test_display_route_accepts_trailing_slash(self):
        """TV browsers may preserve a trailing slash on /display/."""
        client = server_module.app.test_client()

        response = client.get("/display/")

        assert response.status_code == 200
        assert b'<div id="root"></div>' in response.data

    def test_display_route_falls_back_when_dist_missing(self, monkeypatch, tmp_path):
        """Clean checkouts without ui/dist should still serve the React shell."""
        monkeypatch.setattr(server_module, "FRONTEND_DIST_DIR", tmp_path / "missing-dist")
        monkeypatch.setattr(server_module.app, "static_folder", str(tmp_path / "missing-dist"))

        client = server_module.app.test_client()
        response = client.get("/display")

        assert response.status_code == 200
        assert b'<div id="root"></div>' in response.data


class TestShotToDict:
    """Tests for shot_to_dict conversion."""

    def test_basic_conversion(self):
        """Convert a basic shot to dict."""
        shot = Shot(
            ball_speed_mph=150.5,
            club_speed_mph=103.2,
            timestamp=datetime(2024, 1, 15, 10, 30, 0),
            club=ClubType.DRIVER,
        )

        result = shot_to_dict(shot)

        assert result["ball_speed_mph"] == 150.5
        assert result["club_speed_mph"] == 103.2
        assert result["club"] == "driver"
        assert result["player_name"] == "Player 1"
        assert result["timestamp"] == "2024-01-15T10:30:00"
        assert "estimated_carry_yards" in result
        assert "carry_range" in result
        assert len(result["carry_range"]) == 2

    def test_null_club_speed(self):
        """Shot without club speed should have null in dict."""
        shot = Shot(
            ball_speed_mph=150.0,
            timestamp=datetime.now(),
        )

        result = shot_to_dict(shot)

        assert result["club_speed_mph"] is None
        assert result["smash_factor"] is None

    def test_rounding(self):
        """Values should be rounded appropriately."""
        shot = Shot(
            ball_speed_mph=150.456,
            club_speed_mph=103.789,
            timestamp=datetime.now(),
        )

        result = shot_to_dict(shot)

        assert result["ball_speed_mph"] == 150.5  # 1 decimal
        assert result["club_speed_mph"] == 103.8  # 1 decimal
        assert result["smash_factor"] == 1.45  # 2 decimals

    def test_angle_source_field(self):
        """shot_to_dict should include angle_source."""
        shot = Shot(
            ball_speed_mph=150.0,
            timestamp=datetime.now(),
            launch_angle_vertical=12.5,
            launch_angle_confidence=0.8,
            launch_angle_vertical_confidence=0.8,
            launch_angle_vertical_source="radar",
            angle_source="radar",
        )
        result = shot_to_dict(shot)
        assert result["angle_source"] == "radar"
        assert result["launch_angle_vertical_confidence"] == 0.8
        assert result["launch_angle_vertical_source"] == "radar"
        assert result["launch_angle_horizontal_confidence"] is None
        assert result["launch_angle_horizontal_source"] is None

    def test_angle_source_none_by_default(self):
        """Shot without angle source should have None."""
        shot = Shot(
            ball_speed_mph=150.0,
            timestamp=datetime.now(),
        )
        result = shot_to_dict(shot)
        assert result["angle_source"] is None
        assert result["launch_angle_vertical_source"] is None
        assert result["launch_angle_horizontal_source"] is None

    def test_spin_diagnostics_included(self):
        """Rejected spin diagnostics should be present in UI payloads."""
        shot = Shot(
            ball_speed_mph=120.0,
            timestamp=datetime.now(),
            spin_snr=2.96,
            spin_peak_freq_hz=95.21484375,
            spin_candidates=[{"rank": 1, "rpm": 5713, "selected": True}],
            spin_phase_method="phase_residual",
            spin_phase_rpm=5713,
            spin_phase_snr=3.2,
            spin_phase_agreement_pct=2.1,
            spin_phase_confirmed=True,
            spin_rejection_reason="SNR too low (2.96, need 3.0)",
        )

        result = shot_to_dict(shot)

        assert result["spin_rpm"] is None
        assert result["spin_snr"] == 2.96
        assert result["spin_candidate_rpm"] == 5713
        assert result["spin_candidates"][0]["rpm"] == 5713
        assert result["spin_phase_method"] == "phase_residual"
        assert result["spin_phase_rpm"] == 5713
        assert result["spin_phase_snr"] == 3.2
        assert result["spin_phase_agreement_pct"] == 2.1
        assert result["spin_phase_confirmed"] is True
        assert result["spin_rejection_reason"] == "SNR too low (2.96, need 3.0)"


class TestSwingSpeedMode:
    """Tests for swing speed training server helpers."""

    def test_swing_speed_to_dict(self):
        """Swing speed event payloads should be rounded and UI-friendly."""
        event = SwingSpeedEvent(
            peak_speed_mph=101.44,
            timestamp=datetime(2024, 1, 15, 10, 30, 0),
            duration_ms=347.8,
            reading_count=9,
            trigger_speed_mph=32.25,
            peak_magnitude=42,
        )

        result = swing_speed_to_dict(event)

        assert result == {
            "peak_speed_mph": 101.4,
            "timestamp": "2024-01-15T10:30:00",
            "duration_ms": 348,
            "reading_count": 9,
            "trigger_speed_mph": 32.2,
            "peak_magnitude": 42,
            "training_implement": "driver",
            "training_implement_label": "Driver",
            "player_name": "Player 1",
            "unit": "mph",
            "mode": "swing-speed",
        }

    def test_swing_speed_to_shot_dict_supports_existing_ui(self):
        """Swing speed events should also map to the normal shot event shape."""
        event = SwingSpeedEvent(
            peak_speed_mph=101.44,
            timestamp=datetime(2024, 1, 15, 10, 30, 0),
            duration_ms=347.8,
            reading_count=9,
            trigger_speed_mph=32.25,
            peak_magnitude=42,
        )

        result = swing_speed_to_shot_dict(event)

        assert result["ball_speed_mph"] == 101.4
        assert result["club_speed_mph"] == 101.4
        assert result["club"] == "Driver"
        assert result["estimated_carry_yards"] == 0
        assert result["carry_range"] == [0, 0]
        assert result["mode"] == "swing-speed"
        assert result["swing_speed_reading_count"] == 9
        assert result["swing_speed_trigger_mph"] == 32.2
        assert result["training_implement"] == "driver"
        assert result["training_implement_label"] == "Driver"
        assert result["player_name"] == "Player 1"

    def test_set_player_updates_future_swing_speed_payloads(self, monkeypatch):
        """Selected UI player should be stamped on subsequent swing speed reps."""
        emitted = []
        monkeypatch.setattr(server_module, "current_player_name", "Player 1")
        monkeypatch.setattr(
            server_module.socketio, "emit", lambda *args, **kwargs: emitted.append(args)
        )

        server_module.handle_set_player({"player_name": "David"})
        event = SwingSpeedEvent(
            peak_speed_mph=101.44,
            timestamp=datetime(2024, 1, 15, 10, 30, 0),
            duration_ms=347.8,
            reading_count=9,
            trigger_speed_mph=32.25,
        )
        server_module.on_swing_speed_detected(event)

        assert server_module.current_player_name == "David"
        shot_payload = next(payload for name, payload in emitted if name == "shot")
        assert shot_payload["shot"]["player_name"] == "David"

    def test_start_monitor_uses_swing_speed_monitor(self, monkeypatch):
        """Swing speed mode should start a club-only monitor and callback."""
        started = {}
        session = {}

        class FakeSwingSpeedMonitor:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
                self.radar = SimpleNamespace(baud=57600)

            def connect(self):
                started["connected"] = True

            def get_radar_info(self):
                return {"Version": "test"}

            def start(self, event_callback=None, live_callback=None):
                started["event_callback"] = event_callback
                started["live_callback"] = live_callback

            def stop(self):
                started["stopped"] = True

            def disconnect(self):
                started["disconnected"] = True

        class FakeSessionLogger:
            def start_session(self, **kwargs):
                session.update(kwargs)

            def end_session(self):
                pass

            def log_connection(self, **kwargs):
                session["connection"] = kwargs

            def log_clock_sync(self, **kwargs):
                session["clock_sync"] = kwargs

        monkeypatch.setattr(server_module, "monitor", None)
        monkeypatch.setattr(server_module, "camera", None)
        monkeypatch.setattr(server_module, "camera_tracker", None)
        monkeypatch.setattr(server_module, "get_session_logger", lambda: FakeSessionLogger())
        monkeypatch.setattr(
            "openflight.swing_speed.SwingSpeedMonitor",
            FakeSwingSpeedMonitor,
        )

        server_module.start_monitor(
            port="/dev/ops",
            swing_speed_mode=True,
            swing_speed_kwargs={
                "trigger_threshold_mph": 35.0,
                "max_speed_mph": 125.0,
                "min_readings": 4,
                "single_reading_peak_mph": 65.0,
                "num_reports": 8,
                "rejected_cooldown_ms": 50.0,
            },
        )

        assert server_module.monitor.kwargs == {
            "port": "/dev/ops",
            "trigger_threshold_mph": 35.0,
            "max_speed_mph": 125.0,
            "min_readings": 4,
            "single_reading_peak_mph": 65.0,
            "num_reports": 8,
            "rejected_cooldown_ms": 50.0,
        }
        assert started["connected"] is True
        assert started["event_callback"] is server_module.on_swing_speed_detected
        assert started["live_callback"] is server_module.on_live_reading
        assert session["mode"] == "swing-speed"
        assert session["trigger_type"] is None

        server_module.stop_monitor()

    def test_start_monitor_uses_mock_swing_speed_monitor(self, monkeypatch):
        """Mock swing speed mode should exercise the swing speed UI without hardware."""
        session = {}

        class FakeSessionLogger:
            def start_session(self, **kwargs):
                session.update(kwargs)

            def end_session(self):
                pass

        monkeypatch.setattr(server_module, "monitor", None)
        monkeypatch.setattr(server_module, "camera", None)
        monkeypatch.setattr(server_module, "camera_tracker", None)
        monkeypatch.setattr(server_module, "get_session_logger", lambda: FakeSessionLogger())

        server_module.start_monitor(
            mock=True,
            swing_speed_mode=True,
            swing_speed_kwargs={
                "trigger_threshold_mph": 70.0,
                "max_speed_mph": 125.0,
                "min_readings": 5,
            },
        )

        assert isinstance(server_module.monitor, MockSwingSpeedMonitor)
        assert server_module.mock_mode is True
        assert server_module.mock_swing_speed_mode is True
        assert server_module.monitor.trigger_threshold_mph == 70.0
        assert server_module.monitor.max_speed_mph == 125.0
        assert server_module.monitor.min_readings == 5
        assert session["mode"] == "swing-speed"
        assert session["trigger_type"] is None

        server_module.stop_monitor()

    def test_mock_swing_speed_simulates_bounded_event(self):
        """Mock swing speed reps should respect configured lower and upper gates."""
        emitted = []
        monitor = MockSwingSpeedMonitor(
            trigger_threshold_mph=75.0,
            max_speed_mph=110.0,
            min_readings=5,
        )

        monitor.start(event_callback=emitted.append)
        event = monitor.simulate_shot()

        assert emitted == [event]
        assert 75.0 <= event.peak_speed_mph <= 110.0
        assert event.reading_count >= 5
        assert monitor.get_session_stats()["shot_count"] == 1

    def test_mock_swing_speed_stamps_training_implement(self):
        """Mock reps should use the selected training implement metadata."""
        monitor = MockSwingSpeedMonitor()

        assert (
            server_module.TRAINING_IMPLEMENT_LABELS["rypstick-3w-cw"]
            == "Rypstick 3 Weights + Counterweight"
        )

        monitor.set_training_implement("rypstick-3w-cw", "Rypstick 3 Weights + Counterweight")
        event = monitor.simulate_shot(peak_speed=95.0)
        shot = swing_speed_to_shot_dict(event)

        assert event.training_implement == "rypstick-3w-cw"
        assert event.training_implement_label == "Rypstick 3 Weights + Counterweight"
        assert shot["club"] == "Rypstick 3 Weights + Counterweight"

    def test_delete_session_row_removes_mock_swing_speed_event(self, monkeypatch):
        """Deleting a swing-speed UI row should remove the matching event."""
        monitor = MockSwingSpeedMonitor(
            trigger_threshold_mph=75.0,
            max_speed_mph=110.0,
            min_readings=5,
        )
        first = monitor.simulate_shot(peak_speed=95.0)
        second = monitor.simulate_shot(peak_speed=101.0)

        monkeypatch.setattr(server_module, "monitor", monitor)

        assert server_module._delete_session_row(first.timestamp.isoformat()) is True
        remaining = server_module._session_shots()

        assert len(remaining) == 1
        assert remaining[0]["timestamp"] == second.timestamp.isoformat()
        assert remaining[0]["club"] == "Driver"

    def test_set_radar_config_updates_swing_speed_gates(self, monkeypatch):
        """UI tuning should update live swing-speed lower and upper gates."""
        calls = []
        emitted = []

        class StubRadar:
            def set_min_speed_filter(self, value):
                calls.append(("min", value))

            def set_max_speed_filter(self, value):
                calls.append(("max", value))

        class StubSwingSpeedMonitor:
            radar = StubRadar()
            trigger_threshold_mph = 70.0
            max_speed_mph = 125.0

        monkeypatch.setattr(server_module, "monitor", StubSwingSpeedMonitor())
        monkeypatch.setattr(server_module, "mock_mode", False)
        monkeypatch.setattr(server_module, "radar_config", {"min_speed": 70, "max_speed": 125})
        monkeypatch.setattr(server_module, "get_session_logger", lambda: None)
        monkeypatch.setattr(
            server_module.socketio,
            "emit",
            lambda event, payload: emitted.append((event, payload)),
        )
        monkeypatch.setattr(
            server_module,
            "log_session_error",
            lambda *args, **kwargs: None,
        )
        monkeypatch.setattr(
            "openflight.swing_speed.SwingSpeedMonitor",
            StubSwingSpeedMonitor,
        )

        server_module.handle_set_radar_config({"min_speed": 55, "max_speed": 115})

        assert calls == [("min", 55), ("max", 115)]
        assert server_module.monitor.trigger_threshold_mph == 55.0
        assert server_module.monitor.max_speed_mph == 115.0
        assert emitted[-1] == ("radar_config", {"min_speed": 55, "max_speed": 115})

    def test_set_radar_config_forwards_zero_max_speed_to_clear_the_filter(self, monkeypatch):
        """max_speed 0 must still reach the radar on the default launch path.

        AN-010-AD (p10) defines "R<0 resets to no limit", so 0 is how the UI
        clears a previously-set ceiling -- the default DebugPanel slider allows
        it (min=0). Skipping the command leaves the old ceiling active on the
        radar while radar_config reports 0, silently dropping fast shots.
        """
        calls = []

        class StubRadar:
            def set_min_speed_filter(self, value):
                calls.append(("min", value))

            def set_max_speed_filter(self, value):
                calls.append(("max", value))

        class StubMonitor:
            radar = StubRadar()

        monkeypatch.setattr(server_module, "monitor", StubMonitor())
        monkeypatch.setattr(server_module, "mock_mode", False)
        monkeypatch.setattr(server_module, "mock_swing_speed_mode", False)
        monkeypatch.setattr(server_module, "radar_config", {"min_speed": 10, "max_speed": 150})
        monkeypatch.setattr(server_module, "get_session_logger", lambda: None)
        monkeypatch.setattr(server_module.socketio, "emit", lambda *_a, **_kw: None)
        monkeypatch.setattr(server_module, "log_session_error", lambda *_a, **_kw: None)

        server_module.handle_set_radar_config({"max_speed": 0})

        assert ("max", 0) in calls, (
            "R<0 is the documented reset-to-no-limit; suppressing it leaves the "
            f"previous ceiling active on the radar. Calls: {calls}"
        )
        assert server_module.radar_config["max_speed"] == 0


class TestEstimateLaunchAngle:
    """Tests for launch angle estimation from club type and ball speed."""

    def test_driver_average_speed(self):
        """Driver at average speed should return baseline launch angle."""
        angle, confidence = estimate_launch_angle(ClubType.DRIVER, 143)
        assert angle == 11.0
        assert confidence == 0.2

    def test_driver_fast_lowers_launch(self):
        """Faster than average ball speed should produce lower launch."""
        angle, _ = estimate_launch_angle(ClubType.DRIVER, 160)
        assert angle < 11.0

    def test_driver_slow_raises_launch(self):
        """Slower than average ball speed should produce higher launch."""
        angle, _ = estimate_launch_angle(ClubType.DRIVER, 120)
        assert angle > 11.0

    def test_wedge_high_launch(self):
        """Wedges should have high baseline launch angle."""
        angle, _ = estimate_launch_angle(ClubType.LW, 70)
        assert angle >= 30.0

    def test_floor_at_5_degrees(self):
        """Launch angle should never go below 5 degrees."""
        angle, _ = estimate_launch_angle(ClubType.DRIVER, 300)
        assert angle >= 5.0

    def test_unknown_club(self):
        """Unknown club should still return a reasonable estimate."""
        angle, confidence = estimate_launch_angle(ClubType.UNKNOWN, 120)
        assert 5.0 <= angle <= 40.0
        assert confidence == 0.2

    def test_low_smash_lowers_launch(self):
        """Low smash factor (thin hit) should lower launch angle, clamped."""
        baseline, _ = estimate_launch_angle(ClubType.DRIVER, 143)
        angle, _ = estimate_launch_angle(ClubType.DRIVER, 143, club_speed_mph=110)
        # smash = 143/110 = 1.30, well below optimal 1.48
        # Adjustment clamped to -3.0 degrees, so angle â‰ˆ 11.0 - 3.0 = 8.0
        assert angle < baseline
        assert 7.0 <= angle <= 9.0

    def test_optimal_smash_no_change(self):
        """Optimal smash factor should not shift launch angle."""
        angle, _ = estimate_launch_angle(ClubType.DRIVER, 143, club_speed_mph=96.6)
        # smash = 143/96.6 â‰ˆ 1.48 (optimal for driver)
        assert angle == 11.0

    def test_smash_raises_confidence(self):
        """Providing club speed should raise confidence from 0.2 to 0.35."""
        _, conf = estimate_launch_angle(ClubType.DRIVER, 143, club_speed_mph=96.6)
        assert conf == 0.35

    def test_high_smash_raises_launch(self):
        """High smash factor should slightly raise launch angle."""
        baseline, _ = estimate_launch_angle(ClubType.DRIVER, 143)
        # smash = 143/90 â‰ˆ 1.59, above optimal 1.48
        angle, _ = estimate_launch_angle(ClubType.DRIVER, 143, club_speed_mph=90)
        assert angle > baseline
        assert angle <= baseline + 2.0  # capped at +2.0 degrees

    def test_iron_smash_adjustment(self):
        """Iron smash factor adjustment should lower angle for thin hit."""
        baseline, _ = estimate_launch_angle(ClubType.IRON_7, 100)
        # Low smash for 7-iron: smash = 100/80 = 1.25, below optimal ~1.34
        angle, _ = estimate_launch_angle(ClubType.IRON_7, 100, club_speed_mph=80)
        assert angle < baseline
        assert angle >= baseline - 3.0  # clamped

    def test_no_club_speed_unchanged(self):
        """Without club speed, behavior should be identical to current."""
        angle, conf = estimate_launch_angle(ClubType.DRIVER, 143)
        assert angle == 11.0
        assert conf == 0.2

    def test_zero_club_speed_ignored(self):
        """Zero club speed should be treated as no club speed."""
        angle, conf = estimate_launch_angle(ClubType.DRIVER, 143, club_speed_mph=0)
        assert angle == 11.0
        assert conf == 0.2

    def test_high_spin_raises_launch(self):
        """High spin should nudge launch angle up."""
        baseline, _ = estimate_launch_angle(ClubType.DRIVER, 143)
        angle, _ = estimate_launch_angle(ClubType.DRIVER, 143, spin_rpm=4000)
        # 4000 rpm is above optimal ~2500 for driver at 143 mph
        assert angle > baseline

    def test_low_spin_lowers_launch(self):
        """Low spin should nudge launch angle down."""
        baseline, _ = estimate_launch_angle(ClubType.DRIVER, 143)
        angle, _ = estimate_launch_angle(ClubType.DRIVER, 143, spin_rpm=1000)
        assert angle < baseline

    def test_spin_with_smash_raises_confidence(self):
        """Providing both club speed and spin should raise confidence to 0.5."""
        _, conf = estimate_launch_angle(ClubType.DRIVER, 143, club_speed_mph=96.6, spin_rpm=2500)
        assert conf == 0.5

    def test_spin_alone_confidence(self):
        """Spin without club speed should raise confidence to 0.35."""
        _, conf = estimate_launch_angle(ClubType.DRIVER, 143, spin_rpm=2500)
        assert conf == 0.35


class TestMockLaunchMonitor:
    """Tests for MockLaunchMonitor."""

    def test_initial_state(self):
        """New mock monitor should have empty state."""
        monitor = MockLaunchMonitor()

        assert monitor._shots == []
        assert monitor._current_club == ClubType.DRIVER
        assert not monitor._running

    def test_connect_disconnect(self):
        """Connect and disconnect should work."""
        monitor = MockLaunchMonitor()

        assert monitor.connect() is True
        monitor.disconnect()
        assert not monitor._running

    def test_simulate_shot(self):
        """Simulating a shot should create a shot record."""
        monitor = MockLaunchMonitor()
        monitor.connect()
        monitor.start()

        shot = monitor.simulate_shot(ball_speed=150.0)

        assert len(monitor._shots) == 1
        assert 140.0 <= shot.ball_speed_mph <= 160.0  # Â±10 variance
        assert shot.club == ClubType.DRIVER
        assert shot.mode == "mock"
        assert shot.spin_rpm is not None and shot.spin_rpm >= 1000
        assert shot.launch_angle_vertical is not None and shot.launch_angle_vertical >= 5.0
        assert shot.launch_angle_horizontal is not None
        assert shot.launch_angle_confidence is not None

    def test_simulate_shot_with_callback(self):
        """Callback should be called when shot is simulated."""
        monitor = MockLaunchMonitor()
        received_shots = []

        def callback(shot):
            received_shots.append(shot)

        monitor.connect()
        monitor.start(shot_callback=callback)
        monitor.simulate_shot()

        assert len(received_shots) == 1

    def test_set_club(self):
        """Set club should affect future shots."""
        monitor = MockLaunchMonitor()
        monitor.connect()
        monitor.start()

        monitor.set_club(ClubType.IRON_7)
        shot = monitor.simulate_shot()

        assert shot.club == ClubType.IRON_7

    def test_get_shots(self):
        """Get shots should return copy of shots list."""
        monitor = MockLaunchMonitor()
        monitor.connect()
        monitor.start()
        monitor.simulate_shot()
        monitor.simulate_shot()

        shots = monitor.get_shots()

        assert len(shots) == 2
        # Verify it's a copy
        shots.append(None)
        assert len(monitor._shots) == 2

    def test_session_stats_empty(self):
        """Empty session should return zero stats."""
        monitor = MockLaunchMonitor()

        stats = monitor.get_session_stats()

        assert stats["shot_count"] == 0
        assert stats["avg_ball_speed"] == 0

    def test_session_stats_with_shots(self):
        """Session stats should reflect shots taken."""
        monitor = MockLaunchMonitor()
        monitor.connect()
        monitor.start()
        monitor.simulate_shot(ball_speed=140.0)
        monitor.simulate_shot(ball_speed=150.0)
        monitor.simulate_shot(ball_speed=160.0)

        stats = monitor.get_session_stats()

        assert stats["shot_count"] == 3
        # Averages will vary due to Â±10 variance, but should be in range
        assert 140 <= stats["avg_ball_speed"] <= 160
        assert stats["avg_club_speed"] is not None
        assert stats["avg_smash_factor"] is not None

    def test_clear_session(self):
        """Clear session should reset all shots."""
        monitor = MockLaunchMonitor()
        monitor.connect()
        monitor.start()
        monitor.simulate_shot()
        monitor.simulate_shot()

        monitor.clear_session()

        assert monitor._shots == []
        assert monitor.get_session_stats()["shot_count"] == 0


class TestRadarLaunchGuard:
    """Tests for club-and-speed sanity checks on radar launch angles."""

    SESSION_LOG_PATH = (
        Path(__file__).parent.parent / "session_logs" / "session_20260402_121507_range.jsonl"
    )

    def test_rejects_implausible_7iron_launch(self):
        """An obviously impossible 7-iron launch angle should be rejected."""
        plausible, details = radar_launch_is_plausible(
            radar_angle_deg=79.4,
            club=ClubType.IRON_7,
            ball_speed_mph=100.0,
        )

        assert plausible is False
        assert details["expected_launch_deg"] == pytest.approx(20.5)
        assert details["delta_deg"] > details["allowed_delta_deg"]

    def test_accepts_plausible_driver_launch(self):
        """A realistic driver launch angle should pass the sanity guard."""
        plausible, details = radar_launch_is_plausible(
            radar_angle_deg=17.8,
            club=ClubType.DRIVER,
            ball_speed_mph=97.9,
            club_speed_mph=66.0,
        )

        assert plausible is True
        assert details["delta_deg"] < details["allowed_delta_deg"]

    def test_accepts_low_iron_launch(self):
        """Thin/low iron shots are real and should not be replaced by estimates."""
        plausible, details = radar_launch_is_plausible(
            radar_angle_deg=6.9,
            club=ClubType.IRON_9,
            ball_speed_mph=54.8,
        )

        assert plausible is True
        assert details["delta_deg"] > details["allowed_delta_deg"]

    def test_flags_known_outliers_in_real_session_log(self):
        """Historic backyard session log should surface the same three driver outliers."""
        if not self.SESSION_LOG_PATH.exists():
            pytest.skip(f"Session log not found: {self.SESSION_LOG_PATH}")

        implausible_shots = []
        total_shots = 0

        with self.SESSION_LOG_PATH.open() as f:
            for line in f:
                entry = json.loads(line)
                if entry.get("type") != "shot_detected":
                    continue

                total_shots += 1
                plausible, _ = radar_launch_is_plausible(
                    radar_angle_deg=entry["launch_angle_vertical"],
                    club=ClubType(entry["club"]),
                    ball_speed_mph=entry["ball_speed_mph"],
                    club_speed_mph=entry.get("club_speed_mph"),
                    spin_rpm=entry.get("spin_rpm"),
                )
                if not plausible:
                    implausible_shots.append(entry["shot_number"])

        assert total_shots == 11
        assert implausible_shots == [3, 9, 11]


class TestKLD7BufferUnderfillWarning:
    """The buffer-underfill warning surfaces stream-rate problems in
    production logs without requiring a replay.
    """

    def test_full_buffer_does_not_warn(self, caplog):
        import logging

        from openflight.server import _warn_if_kld7_buffer_underfilled

        with caplog.at_level(logging.WARNING, logger="openflight.server"):
            # Expected ~204; full buffer should not warn.
            _warn_if_kld7_buffer_underfilled("vertical", 200)
        warns = [r for r in caplog.records if "underfilled" in r.message]
        assert not warns

    def test_underfilled_buffer_warns(self, caplog):
        import logging

        from openflight.server import _warn_if_kld7_buffer_underfilled

        with caplog.at_level(logging.WARNING, logger="openflight.server"):
            _warn_if_kld7_buffer_underfilled("vertical", 50)  # ~25%
        warns = [r for r in caplog.records if "underfilled" in r.message]
        assert warns, "Expected underfill WARNING but got none"
        assert "vertical" in warns[0].message
        assert "50/204" in warns[0].message or "50/" in warns[0].message

    def test_empty_buffer_does_not_warn(self, caplog):
        # frame_count=0 means snapshot wasn't taken or stream hadn't
        # started; not the underfill case we care about.
        import logging

        from openflight.server import _warn_if_kld7_buffer_underfilled

        with caplog.at_level(logging.WARNING, logger="openflight.server"):
            _warn_if_kld7_buffer_underfilled("horizontal", 0)
        warns = [r for r in caplog.records if "underfilled" in r.message]
        assert not warns


class TestKLD7RawPayloadWarning:
    """TrackMan experiments should warn when replay payloads are missing."""

    def test_raw_payload_warning_disabled_when_not_expected(self, caplog):
        import logging

        from openflight.server import _warn_if_kld7_raw_payload_missing

        with caplog.at_level(logging.WARNING, logger="openflight.server"):
            _warn_if_kld7_raw_payload_missing(
                "vertical",
                [{"timestamp": 1.0, "has_radc": True}],
                raw_payload_expected=False,
            )

        warns = [r for r in caplog.records if "raw RADC replay payload" in r.message]
        assert not warns

    def test_missing_raw_payload_warns_when_expected(self, caplog):
        import logging

        from openflight.server import _warn_if_kld7_raw_payload_missing

        with caplog.at_level(logging.WARNING, logger="openflight.server"):
            _warn_if_kld7_raw_payload_missing(
                "vertical",
                [{"timestamp": 1.0, "has_radc": True}],
                raw_payload_expected=True,
            )

        warns = [r for r in caplog.records if "raw RADC replay payload missing" in r.message]
        assert warns
        assert "0/1 RADC frames have radc_b64" in warns[0].message

    def test_partial_raw_payload_warns_when_expected(self, caplog):
        import logging

        from openflight.server import _warn_if_kld7_raw_payload_missing

        with caplog.at_level(logging.WARNING, logger="openflight.server"):
            _warn_if_kld7_raw_payload_missing(
                "horizontal",
                [
                    {"timestamp": 1.0, "radc_b64": "AQID"},
                    {"timestamp": 2.0, "has_radc": True},
                ],
                raw_payload_expected=True,
            )

        warns = [r for r in caplog.records if "raw RADC replay payload incomplete" in r.message]
        assert warns
        assert "1/2 RADC frames have radc_b64" in warns[0].message

    def test_complete_raw_payload_ignores_non_radc_frames(self, caplog):
        import logging

        from openflight.server import _warn_if_kld7_raw_payload_missing

        with caplog.at_level(logging.WARNING, logger="openflight.server"):
            _warn_if_kld7_raw_payload_missing(
                "vertical",
                [
                    {"timestamp": 1.0},
                    {"timestamp": 2.0, "has_radc": True, "radc_b64": "AQID"},
                ],
                raw_payload_expected=True,
            )

        warns = [r for r in caplog.records if "raw RADC replay payload" in r.message]
        assert not warns

    def test_wrong_size_raw_payload_warns_when_expected(self, caplog):
        import logging

        from openflight.server import _warn_if_kld7_raw_payload_missing

        with caplog.at_level(logging.WARNING, logger="openflight.server"):
            _warn_if_kld7_raw_payload_missing(
                "vertical",
                [
                    {
                        "timestamp": 1.0,
                        "has_radc": True,
                        "radc_b64": "AQID",
                        "radc_payload_bytes": 3,
                        "radc_payload_valid": False,
                    },
                ],
                raw_payload_expected=True,
            )

        warns = [r for r in caplog.records if "raw RADC replay payload invalid" in r.message]
        assert warns
        assert "1/1 payloads" in warns[0].message

    def test_no_radc_frames_warns_when_raw_payload_expected(self, caplog):
        import logging

        from openflight.server import _warn_if_kld7_raw_payload_missing

        with caplog.at_level(logging.WARNING, logger="openflight.server"):
            _warn_if_kld7_raw_payload_missing(
                "horizontal",
                [{"timestamp": 1.0}],
                raw_payload_expected=True,
            )

        warns = [r for r in caplog.records if "buffer has no RADC frames" in r.message]
        assert warns


class TestKLD7PostShotSnapshotWarning:
    """TrackMan replay snapshots should include frames after the OPS impact timestamp."""

    def test_no_post_shot_frames_warns_when_expected(self, caplog):
        import logging

        from openflight.server import _warn_if_kld7_snapshot_lacks_post_shot_frames

        with caplog.at_level(logging.WARNING, logger="openflight.server"):
            _warn_if_kld7_snapshot_lacks_post_shot_frames(
                "vertical",
                [{"timestamp": 99.9, "has_radc": True}, {"timestamp": 100.0, "has_radc": True}],
                100.0,
                raw_payload_expected=True,
            )

        warns = [r for r in caplog.records if "no frames after shot timestamp" in r.message]
        assert warns

    def test_post_shot_frames_do_not_warn(self, caplog):
        import logging

        from openflight.server import _warn_if_kld7_snapshot_lacks_post_shot_frames

        with caplog.at_level(logging.WARNING, logger="openflight.server"):
            _warn_if_kld7_snapshot_lacks_post_shot_frames(
                "vertical",
                [{"timestamp": 99.9, "has_radc": True}, {"timestamp": 100.1, "has_radc": True}],
                100.0,
                raw_payload_expected=True,
            )

        warns = [r for r in caplog.records if "no frames after shot timestamp" in r.message]
        assert not warns


class TestKLD7PostShotCaptureDelay:
    """Live K-LD7 extraction should include post-impact frames."""

    def test_waits_until_post_shot_capture_time(self, monkeypatch):
        sleeps = []
        monkeypatch.setattr(server_module.time, "time", lambda: 1000.0)
        monkeypatch.setattr(server_module.time, "sleep", lambda delay: sleeps.append(delay))

        server_module._maybe_wait_for_kld7_post_shot_frames(1000.0)

        assert sleeps == [pytest.approx(0.18)]

    def test_does_not_wait_when_processing_is_already_past_capture_time(self, monkeypatch):
        sleeps = []
        monkeypatch.setattr(server_module.time, "time", lambda: 1000.2)
        monkeypatch.setattr(server_module.time, "sleep", lambda delay: sleeps.append(delay))

        server_module._maybe_wait_for_kld7_post_shot_frames(1000.0)

        assert sleeps == []


class TestOnShotDetected:
    """Tests for live shot processing in the server."""

    def test_kld7_uses_shot_impact_timestamp(self, monkeypatch):
        """K-LD7 selection should be anchored to the OPS243 impact timestamp."""
        calls = []

        class StubTracker:
            orientation = "vertical"

            def snapshot_buffer(self, include_radc_payload=False):
                return []

            def get_angle_for_shot(
                self, shot_timestamp=None, ball_speed_mph=None, impact_timestamp=None, **kwargs
            ):
                calls.append(("ball", shot_timestamp))
                return KLD7Angle(vertical_deg=12.0, confidence=0.8, num_frames=2)

            def get_club_angle(self, club_speed_mph=None, shot_timestamp=None):
                calls.append(("club", shot_timestamp))
                return None

            def reset(self):
                calls.append(("reset", None))

        emitted = []
        monkeypatch.setattr(server_module, "kld7_vertical", StubTracker())
        monkeypatch.setattr(server_module, "camera_tracker", None)
        monkeypatch.setattr(server_module, "camera_enabled", False)
        monkeypatch.setattr(server_module, "monitor", None)
        monkeypatch.setattr(server_module, "debug_mode", False)
        monkeypatch.setattr(server_module, "get_session_logger", lambda: None)
        monkeypatch.setattr(
            server_module.socketio, "emit", lambda *args, **kwargs: emitted.append((args, kwargs))
        )

        shot = Shot(
            ball_speed_mph=150.0,
            club_speed_mph=100.0,
            timestamp=datetime.now(),
            impact_timestamp=1234.5,
            club=ClubType.DRIVER,
        )

        on_shot_detected(shot)

        assert ("ball", 1234.5) in calls
        assert ("club", 1234.5) in calls
        assert emitted

    def test_radc_tuning_logs_raw_kld7_payloads_without_calibration(self, monkeypatch):
        """Tuning-only experiments still need raw RADC buffers for replay."""
        snapshot_calls = []

        class StubTracker:
            orientation = "vertical"

            def snapshot_buffer(self, include_radc_payload=False):
                snapshot_calls.append(include_radc_payload)
                return [{"timestamp": 1000.0, "has_radc": True, "radc_b64": "AQID"}]

            def get_angle_for_shot(
                self, shot_timestamp=None, ball_speed_mph=None, impact_timestamp=None, **kwargs
            ):
                return KLD7Angle(vertical_deg=12.0, confidence=0.8, num_frames=2)

            def get_club_angle(self, club_speed_mph=None, shot_timestamp=None):
                return None

            def reset(self):
                return None

        logged_buffers = []

        class StubSessionLogger:
            @property
            def stats(self):
                return {"shots_detected": 0}

            def log_kld7_buffer(self, **kwargs):
                logged_buffers.append(kwargs)

        monkeypatch.setattr(server_module, "kld7_vertical", StubTracker())
        monkeypatch.setattr(server_module, "kld7_horizontal", None)
        monkeypatch.setattr(server_module, "camera_tracker", None)
        monkeypatch.setattr(server_module, "camera_enabled", False)
        monkeypatch.setattr(server_module, "monitor", None)
        monkeypatch.setattr(server_module, "debug_mode", False)
        monkeypatch.setattr(server_module, "experimental_kld7_radc_tuning", True)
        monkeypatch.setattr(server_module, "get_session_logger", lambda: StubSessionLogger())
        monkeypatch.setattr(server_module.socketio, "emit", lambda *args, **kwargs: None)

        shot = Shot(
            ball_speed_mph=150.0,
            club_speed_mph=100.0,
            timestamp=datetime.now(),
            impact_timestamp=1234.5,
            club=ClubType.DRIVER,
        )

        on_shot_detected(shot)

        assert snapshot_calls == [True]
        assert logged_buffers[0]["buffer_frames"][0]["radc_b64"] == "AQID"
        assert logged_buffers[0]["raw_payload_expected"] is True

    def test_experiment_warns_when_snapshot_lacks_raw_payloads(self, monkeypatch, caplog):
        """A TrackMan run should warn immediately if future replay will fail."""
        import logging

        class StubTracker:
            orientation = "vertical"

            def snapshot_buffer(self, include_radc_payload=False):
                assert include_radc_payload is True
                return [{"timestamp": 1000.0, "has_radc": True}]

            def get_angle_for_shot(
                self, shot_timestamp=None, ball_speed_mph=None, impact_timestamp=None, **kwargs
            ):
                return KLD7Angle(vertical_deg=12.0, confidence=0.8, num_frames=2)

            def get_club_angle(self, club_speed_mph=None, shot_timestamp=None):
                return None

            def reset(self):
                return None

        class StubSessionLogger:
            @property
            def stats(self):
                return {"shots_detected": 0}

            def log_kld7_buffer(self, **kwargs):
                return None

        monkeypatch.setattr(server_module, "kld7_vertical", StubTracker())
        monkeypatch.setattr(server_module, "kld7_horizontal", None)
        monkeypatch.setattr(server_module, "camera_tracker", None)
        monkeypatch.setattr(server_module, "camera_enabled", False)
        monkeypatch.setattr(server_module, "monitor", None)
        monkeypatch.setattr(server_module, "debug_mode", False)
        monkeypatch.setattr(server_module, "experimental_kld7_raw_radc_logging", True)
        monkeypatch.setattr(server_module, "experimental_kld7_radc_tuning", False)
        monkeypatch.setattr(server_module, "get_session_logger", lambda: StubSessionLogger())
        monkeypatch.setattr(server_module.socketio, "emit", lambda *args, **kwargs: None)

        shot = Shot(
            ball_speed_mph=150.0,
            club_speed_mph=100.0,
            timestamp=datetime.now(),
            impact_timestamp=1234.5,
            club=ClubType.DRIVER,
        )

        with caplog.at_level(logging.WARNING, logger="openflight.server"):
            on_shot_detected(shot)

        assert any("raw RADC replay payload missing" in r.message for r in caplog.records)

    def test_implausible_kld7_angle_falls_back_to_estimate(self, monkeypatch):
        """Radar angles that conflict with club+speed should not override the estimate."""

        class StubTracker:
            orientation = "vertical"

            def snapshot_buffer(self, include_radc_payload=False):
                return []

            def get_angle_for_shot(
                self, shot_timestamp=None, ball_speed_mph=None, impact_timestamp=None, **kwargs
            ):
                return KLD7Angle(vertical_deg=79.4, confidence=0.58, num_frames=1)

            def reset(self):
                return None

        monkeypatch.setattr(server_module, "kld7_vertical", StubTracker())
        monkeypatch.setattr(server_module, "camera_tracker", None)
        monkeypatch.setattr(server_module, "camera_enabled", False)
        monkeypatch.setattr(server_module, "monitor", None)
        monkeypatch.setattr(server_module, "debug_mode", False)
        monkeypatch.setattr(server_module, "get_session_logger", lambda: None)
        monkeypatch.setattr(server_module.socketio, "emit", lambda *args, **kwargs: None)

        shot = Shot(
            ball_speed_mph=100.0,
            timestamp=datetime.now(),
            club=ClubType.IRON_7,
        )

        on_shot_detected(shot)

        assert shot.angle_source == "estimated"
        assert shot.launch_angle_vertical == pytest.approx(20.5)
        assert shot.launch_angle_horizontal == pytest.approx(0.0)

    def test_low_valid_vertical_kld7_angle_beats_high_estimate(self, monkeypatch):
        """A low measured iron launch should not be replaced by a high fallback estimate."""

        class StubTracker:
            orientation = "vertical"

            def snapshot_buffer(self):
                return []

            def get_angle_for_shot(
                self, shot_timestamp=None, ball_speed_mph=None, impact_timestamp=None, **kwargs
            ):
                return KLD7Angle(vertical_deg=10.7, confidence=0.89, num_frames=6)

            def get_club_angle(self, club_speed_mph=None, shot_timestamp=None):
                return None

            def reset(self):
                return None

        monkeypatch.setattr(server_module, "kld7_vertical", StubTracker())
        monkeypatch.setattr(server_module, "kld7_horizontal", None)
        monkeypatch.setattr(server_module, "camera_tracker", None)
        monkeypatch.setattr(server_module, "camera_enabled", False)
        monkeypatch.setattr(server_module, "monitor", None)
        monkeypatch.setattr(server_module, "debug_mode", False)
        monkeypatch.setattr(server_module, "get_session_logger", lambda: None)
        monkeypatch.setattr(server_module.socketio, "emit", lambda *args, **kwargs: None)

        shot = Shot(
            ball_speed_mph=52.94928729492188,
            club_speed_mph=40.32291878613282,
            timestamp=datetime.now(),
            club=ClubType.IRON_9,
        )

        on_shot_detected(shot)

        assert shot.launch_angle_vertical == pytest.approx(10.7)
        assert shot.launch_angle_vertical_source == "radar"
        assert shot.launch_angle_confidence == pytest.approx(0.89)
        assert shot.angle_source == "radar"

    def test_lane_disagreement_vertical_radar_shown_as_marginal_confidence(self, monkeypatch):
        """Weak vertical radar candidates should not override the launch model."""

        class StubTracker:
            orientation = "vertical"

            def snapshot_buffer(self):
                return []

            def get_angle_for_shot(
                self, shot_timestamp=None, ball_speed_mph=None, impact_timestamp=None, **kwargs
            ):
                return KLD7Angle(vertical_deg=10.7, confidence=0.72, num_frames=6)

            def get_club_angle(self, club_speed_mph=None, shot_timestamp=None):
                return None

            def reset(self):
                return None

        monkeypatch.setattr(server_module, "kld7_vertical", StubTracker())
        monkeypatch.setattr(server_module, "kld7_horizontal", None)
        monkeypatch.setattr(server_module, "camera_tracker", None)
        monkeypatch.setattr(server_module, "camera_enabled", False)
        monkeypatch.setattr(server_module, "monitor", None)
        monkeypatch.setattr(server_module, "debug_mode", False)
        monkeypatch.setattr(server_module, "get_session_logger", lambda: None)
        monkeypatch.setattr(server_module.socketio, "emit", lambda *args, **kwargs: None)

        shot = Shot(
            ball_speed_mph=52.94928729492188,
            club_speed_mph=40.32291878613282,
            timestamp=datetime.now(),
            club=ClubType.IRON_9,
        )

        on_shot_detected(shot)

        # Lane disagreement no longer silently replaces the measurement:
        # shown as radar with single-dot (marginal) confidence
        assert shot.launch_angle_vertical_source == "radar"
        assert shot.launch_angle_vertical == pytest.approx(10.7)
        assert shot.launch_angle_vertical_confidence < 0.4

    def test_low_confidence_vertical_kld7_angle_soft_accepts_when_estimator_aligned(
        self, monkeypatch
    ):
        """A marginal vertical radar candidate can win when it agrees with the shot model."""

        class StubTracker:
            orientation = "vertical"

            def snapshot_buffer(self):
                return [{"timestamp": 1234.5, "has_radc": True}]

            def get_angle_for_shot(
                self,
                shot_timestamp=None,
                ball_speed_mph=None,
                impact_timestamp=None,
                **kwargs,
            ):
                return KLD7Angle(
                    vertical_deg=19.9,
                    confidence=0.69,
                    num_frames=10,
                    radc_selection={
                        "estimator": "geometry",
                        "selection_path": "geometry_primary",
                        "selected_frame_indices": [39, 40],
                        "selected_t_ms": [21.1, 56.3],
                        "selected_bin_errors": [19, 2],
                        "geom_fit_rmse_deg": 0.64,
                    },
                )

            def get_club_angle(self, club_speed_mph=None, shot_timestamp=None):
                return None

            def reset(self):
                return None

        logged_buffers = []

        class StubSessionLogger:
            @property
            def stats(self):
                return {"shots_detected": 0}

            def log_kld7_buffer(self, **kwargs):
                logged_buffers.append(kwargs)

            def log_shot(self, **kwargs):
                return None

        monkeypatch.setattr(server_module, "kld7_vertical", StubTracker())
        monkeypatch.setattr(server_module, "kld7_horizontal", None)
        monkeypatch.setattr(server_module, "camera_tracker", None)
        monkeypatch.setattr(server_module, "camera_enabled", False)
        monkeypatch.setattr(server_module, "monitor", None)
        monkeypatch.setattr(server_module, "debug_mode", False)
        monkeypatch.setattr(server_module, "get_session_logger", lambda: StubSessionLogger())
        monkeypatch.setattr(server_module.socketio, "emit", lambda *args, **kwargs: None)

        shot = Shot(
            ball_speed_mph=107.8,
            club_speed_mph=76.2,
            timestamp=datetime.now(),
            club=ClubType.IRON_7,
        )

        on_shot_detected(shot)

        assert shot.launch_angle_vertical == pytest.approx(19.9)
        assert shot.launch_angle_vertical_source == "radar"
        assert shot.launch_angle_confidence == pytest.approx(0.69)
        assert shot.angle_source == "radar"
        assert logged_buffers[0]["ball_angle"]["selection_reason"] == "soft_accept"
        assert logged_buffers[0]["ball_angle"]["radc_selection"] == {
            "estimator": "geometry",
            "selection_path": "geometry_primary",
            "selected_frame_indices": [39, 40],
            "selected_t_ms": [21.1, 56.3],
            "selected_bin_errors": [19, 2],
            "geom_fit_rmse_deg": 0.64,
        }

    def test_near_threshold_vertical_kld7_angle_displays_as_low_confidence_radar(self, monkeypatch):
        """A plausible near-threshold radar candidate should show instead of estimate."""

        class StubTracker:
            orientation = "vertical"

            def snapshot_buffer(self):
                return [{"timestamp": 1234.5, "has_radc": True}]

            def get_angle_for_shot(
                self,
                shot_timestamp=None,
                ball_speed_mph=None,
                impact_timestamp=None,
                **kwargs,
            ):
                return KLD7Angle(
                    vertical_deg=19.9,
                    confidence=0.67,
                    num_frames=1,
                    radc_selection={
                        "estimator": "geometry_single_frame",
                        "selection_path": "geometry_single_frame",
                        "selected_frame_indices": [40],
                        "selected_t_ms": [79.4],
                        "selected_bin_errors": [5],
                    },
                )

            def get_club_angle(self, club_speed_mph=None, shot_timestamp=None):
                return None

            def reset(self):
                return None

        logged_buffers = []
        logged_shots = []

        class StubSessionLogger:
            @property
            def stats(self):
                return {"shots_detected": 0}

            def log_kld7_buffer(self, **kwargs):
                logged_buffers.append(kwargs)

            def log_shot(self, **kwargs):
                logged_shots.append(kwargs)

        monkeypatch.setattr(server_module, "kld7_vertical", StubTracker())
        monkeypatch.setattr(server_module, "kld7_horizontal", None)
        monkeypatch.setattr(server_module, "camera_tracker", None)
        monkeypatch.setattr(server_module, "camera_enabled", False)
        monkeypatch.setattr(server_module, "monitor", None)
        monkeypatch.setattr(server_module, "debug_mode", False)
        monkeypatch.setattr(server_module, "get_session_logger", lambda: StubSessionLogger())
        monkeypatch.setattr(server_module.socketio, "emit", lambda *args, **kwargs: None)

        shot = Shot(
            ball_speed_mph=100.9,
            club_speed_mph=67.7,
            timestamp=datetime.now(),
            club=ClubType.IRON_7,
        )

        on_shot_detected(shot)

        assert shot.launch_angle_vertical == pytest.approx(19.9)
        assert shot.launch_angle_vertical_source == "radar"
        assert shot.launch_angle_confidence == pytest.approx(0.67)
        assert shot.angle_source == "radar"
        assert logged_shots[0]["launch_angle_vertical"] == pytest.approx(19.9)
        assert logged_shots[0]["launch_angle_vertical_source"] == "radar"
        assert logged_shots[0]["angle_source"] == "radar"
        assert logged_buffers[0]["ball_angle"]["selection_reason"] == "low_confidence_accept"
        assert logged_buffers[0]["ball_angle"]["acceptance_path"] == "low_confidence"
        assert logged_buffers[0]["ball_angle"]["radc_selection"] == {
            "estimator": "geometry_single_frame",
            "selection_path": "geometry_single_frame",
            "selected_frame_indices": [40],
            "selected_t_ms": [79.4],
            "selected_bin_errors": [5],
        }

    def test_low_confidence_vertical_kld7_angle_rejects_estimator_outlier(self, monkeypatch):
        """Soft acceptance should not admit high-angle lane picks from the same session."""

        class StubTracker:
            orientation = "vertical"

            def snapshot_buffer(self):
                return [{"timestamp": 1234.5, "has_radc": True}]

            def get_angle_for_shot(self, shot_timestamp=None, ball_speed_mph=None, **kwargs):
                return KLD7Angle(vertical_deg=27.8, confidence=0.75, num_frames=32)

            def get_club_angle(self, club_speed_mph=None, shot_timestamp=None):
                return None

            def reset(self):
                return None

        logged_buffers = []

        class StubSessionLogger:
            @property
            def stats(self):
                return {"shots_detected": 0}

            def log_kld7_buffer(self, **kwargs):
                logged_buffers.append(kwargs)

            def log_shot(self, **kwargs):
                return None

        monkeypatch.setattr(server_module, "kld7_vertical", StubTracker())
        monkeypatch.setattr(server_module, "kld7_horizontal", None)
        monkeypatch.setattr(server_module, "camera_tracker", None)
        monkeypatch.setattr(server_module, "camera_enabled", False)
        monkeypatch.setattr(server_module, "monitor", None)
        monkeypatch.setattr(server_module, "debug_mode", False)
        monkeypatch.setattr(server_module, "get_session_logger", lambda: StubSessionLogger())
        monkeypatch.setattr(server_module.socketio, "emit", lambda *args, **kwargs: None)

        shot = Shot(
            ball_speed_mph=117.2,
            club_speed_mph=87.0,
            timestamp=datetime.now(),
            club=ClubType.IRON_7,
        )
        expected_launch, _ = estimate_launch_angle(
            shot.club,
            shot.ball_speed_mph,
            club_speed_mph=shot.club_speed_mph,
        )

        on_shot_detected(shot)

        # Marginal accept: shown as radar with single-dot confidence
        # instead of silently replaced by the club estimate
        assert shot.launch_angle_vertical_source == "radar"
        assert shot.launch_angle_vertical != pytest.approx(expected_launch)
        assert shot.launch_angle_vertical_confidence < 0.4
        assert (
            logged_buffers[0]["ball_angle"]["selection_reason"]
            == "marginal_accept:estimator_delta_too_large"
        )

    def test_vertical_estimate_preserves_radar_horizontal(self, monkeypatch):
        """Vertical fallback should not erase a horizontal radar measurement."""

        class StubHorizontalTracker:
            orientation = "horizontal"

            def snapshot_buffer(self, include_radc_payload=False):
                return []

            def get_angle_for_shot(
                self, shot_timestamp=None, ball_speed_mph=None, impact_timestamp=None, **kwargs
            ):
                return KLD7Angle(horizontal_deg=1.5, confidence=0.68, num_frames=3)

            def get_club_angle(self, club_speed_mph=None, shot_timestamp=None):
                return None

            def reset(self):
                return None

        monkeypatch.setattr(server_module, "kld7_vertical", None)
        monkeypatch.setattr(server_module, "kld7_horizontal", StubHorizontalTracker())
        monkeypatch.setattr(server_module, "camera_tracker", None)
        monkeypatch.setattr(server_module, "camera_enabled", False)
        monkeypatch.setattr(server_module, "monitor", None)
        monkeypatch.setattr(server_module, "debug_mode", False)
        monkeypatch.setattr(server_module, "get_session_logger", lambda: None)
        monkeypatch.setattr(server_module.socketio, "emit", lambda *args, **kwargs: None)

        shot = Shot(
            ball_speed_mph=100.0,
            timestamp=datetime.now(),
            club=ClubType.IRON_7,
        )

        on_shot_detected(shot)

        assert shot.angle_source == "estimated"
        assert shot.launch_angle_vertical == pytest.approx(20.5)
        assert shot.launch_angle_horizontal == pytest.approx(1.5)
        assert shot.launch_angle_vertical_source == "estimated"
        assert shot.launch_angle_horizontal_source == "radar"

    def test_radc_tuning_horizontal_limit_accepts_wider_trackman_angle(self, monkeypatch):
        """Experimental RADC tuning can widen the server-side horizontal guard."""

        class StubHorizontalTracker:
            orientation = "horizontal"

            def snapshot_buffer(self, include_radc_payload=False):
                return []

            def get_angle_for_shot(
                self, shot_timestamp=None, ball_speed_mph=None, impact_timestamp=None, **kwargs
            ):
                return KLD7Angle(horizontal_deg=16.1, confidence=0.68, num_frames=3)

            def get_club_angle(self, club_speed_mph=None, shot_timestamp=None):
                return None

            def reset(self):
                return None

        tuning = dict(server_module._DEFAULT_KLD7_RADC_TUNING)
        tuning["radc_horizontal_angle_limit_deg"] = 30.0
        monkeypatch.setattr(server_module, "kld7_vertical", None)
        monkeypatch.setattr(server_module, "kld7_horizontal", StubHorizontalTracker())
        monkeypatch.setattr(server_module, "camera_tracker", None)
        monkeypatch.setattr(server_module, "camera_enabled", False)
        monkeypatch.setattr(server_module, "monitor", None)
        monkeypatch.setattr(server_module, "debug_mode", False)
        monkeypatch.setattr(server_module, "experimental_kld7_radc_tuning", True)
        monkeypatch.setattr(server_module, "active_kld7_radc_tuning", tuning)
        monkeypatch.setattr(server_module, "get_session_logger", lambda: None)
        monkeypatch.setattr(server_module.socketio, "emit", lambda *args, **kwargs: None)

        shot = Shot(
            ball_speed_mph=100.0,
            timestamp=datetime.now(),
            club=ClubType.IRON_7,
        )

        on_shot_detected(shot)

        assert shot.launch_angle_horizontal == pytest.approx(16.1)
        assert shot.launch_angle_horizontal_source == "radar"

    def test_low_confidence_horizontal_radar_falls_back_to_neutral(self, monkeypatch):
        """Very low-confidence horizontal K-LD7 angles should not overwrite neutral fallback."""

        class StubHorizontalTracker:
            orientation = "horizontal"

            def snapshot_buffer(self):
                return []

            def get_angle_for_shot(
                self, shot_timestamp=None, ball_speed_mph=None, impact_timestamp=None, **kwargs
            ):
                return KLD7Angle(horizontal_deg=-8.1, confidence=0.31, num_frames=19)

            def get_club_angle(self, club_speed_mph=None, shot_timestamp=None):
                return None

            def reset(self):
                return None

        monkeypatch.setattr(server_module, "kld7_vertical", None)
        monkeypatch.setattr(server_module, "kld7_horizontal", StubHorizontalTracker())
        monkeypatch.setattr(server_module, "camera_tracker", None)
        monkeypatch.setattr(server_module, "camera_enabled", False)
        monkeypatch.setattr(server_module, "monitor", None)
        monkeypatch.setattr(server_module, "debug_mode", False)
        monkeypatch.setattr(server_module, "get_session_logger", lambda: None)
        monkeypatch.setattr(server_module.socketio, "emit", lambda *args, **kwargs: None)

        shot = Shot(
            ball_speed_mph=95.0,
            timestamp=datetime.now(),
            club=ClubType.IRON_9,
        )

        on_shot_detected(shot)

        assert shot.launch_angle_horizontal == pytest.approx(0.0)
        assert shot.launch_angle_horizontal_source == "estimated"
        assert shot.angle_source == "estimated"

    def test_low_confidence_horizontal_radar_soft_accepts_near_target_line(self, monkeypatch):
        """Marginal horizontal candidates can win when they stay near centerline."""

        class StubHorizontalTracker:
            orientation = "horizontal"

            def snapshot_buffer(self):
                return [{"timestamp": 1234.5, "has_radc": True}]

            def get_angle_for_shot(self, shot_timestamp=None, ball_speed_mph=None, **kwargs):
                return KLD7Angle(horizontal_deg=-2.2, confidence=0.34, num_frames=8)

            def get_club_angle(self, club_speed_mph=None, shot_timestamp=None):
                return None

            def reset(self):
                return None

        logged_buffers = []

        class StubSessionLogger:
            @property
            def stats(self):
                return {"shots_detected": 0}

            def log_kld7_buffer(self, **kwargs):
                logged_buffers.append(kwargs)

            def log_shot(self, **kwargs):
                return None

        monkeypatch.setattr(server_module, "kld7_vertical", None)
        monkeypatch.setattr(server_module, "kld7_horizontal", StubHorizontalTracker())
        monkeypatch.setattr(server_module, "camera_tracker", None)
        monkeypatch.setattr(server_module, "camera_enabled", False)
        monkeypatch.setattr(server_module, "monitor", None)
        monkeypatch.setattr(server_module, "debug_mode", False)
        monkeypatch.setattr(server_module, "get_session_logger", lambda: StubSessionLogger())
        monkeypatch.setattr(server_module.socketio, "emit", lambda *args, **kwargs: None)

        shot = Shot(
            ball_speed_mph=95.0,
            timestamp=datetime.now(),
            club=ClubType.IRON_9,
        )

        on_shot_detected(shot)

        assert shot.launch_angle_horizontal == pytest.approx(-2.2)
        assert shot.launch_angle_horizontal_source == "radar"
        assert shot.launch_angle_horizontal_confidence == pytest.approx(0.34)
        assert logged_buffers[0]["ball_angle"]["selection_reason"] == "soft_accept"

    def test_low_confidence_horizontal_radar_rejects_wide_soft_lane(self, monkeypatch):
        """Soft horizontal acceptance should not admit wider marginal candidates."""

        class StubHorizontalTracker:
            orientation = "horizontal"

            def snapshot_buffer(self):
                return [{"timestamp": 1234.5, "has_radc": True}]

            def get_angle_for_shot(self, shot_timestamp=None, ball_speed_mph=None, **kwargs):
                return KLD7Angle(horizontal_deg=-8.1, confidence=0.34, num_frames=8)

            def get_club_angle(self, club_speed_mph=None, shot_timestamp=None):
                return None

            def reset(self):
                return None

        logged_buffers = []

        class StubSessionLogger:
            @property
            def stats(self):
                return {"shots_detected": 0}

            def log_kld7_buffer(self, **kwargs):
                logged_buffers.append(kwargs)

            def log_shot(self, **kwargs):
                return None

        monkeypatch.setattr(server_module, "kld7_vertical", None)
        monkeypatch.setattr(server_module, "kld7_horizontal", StubHorizontalTracker())
        monkeypatch.setattr(server_module, "camera_tracker", None)
        monkeypatch.setattr(server_module, "camera_enabled", False)
        monkeypatch.setattr(server_module, "monitor", None)
        monkeypatch.setattr(server_module, "debug_mode", False)
        monkeypatch.setattr(server_module, "get_session_logger", lambda: StubSessionLogger())
        monkeypatch.setattr(server_module.socketio, "emit", lambda *args, **kwargs: None)

        shot = Shot(
            ball_speed_mph=95.0,
            timestamp=datetime.now(),
            club=ClubType.IRON_9,
        )

        on_shot_detected(shot)

        assert shot.launch_angle_horizontal == pytest.approx(0.0)
        assert shot.launch_angle_horizontal_source == "estimated"
        assert shot.angle_source == "estimated"
        assert logged_buffers[0]["ball_angle"]["selection_reason"] == "outside_soft_lane"

    def test_weak_near_limit_horizontal_radar_falls_back_to_neutral(self, monkeypatch):
        """Near-wall horizontal readings need stronger evidence than centerline readings."""

        class StubHorizontalTracker:
            orientation = "horizontal"

            def snapshot_buffer(self):
                return [{"timestamp": 1234.5, "has_radc": True}]

            def get_angle_for_shot(self, shot_timestamp=None, ball_speed_mph=None, **kwargs):
                return KLD7Angle(horizontal_deg=13.9, confidence=0.66, num_frames=2)

            def get_club_angle(self, club_speed_mph=None, shot_timestamp=None):
                return None

            def reset(self):
                return None

        logged_buffers = []

        class StubSessionLogger:
            @property
            def stats(self):
                return {"shots_detected": 0}

            def log_kld7_buffer(self, **kwargs):
                logged_buffers.append(kwargs)

            def log_shot(self, **kwargs):
                return None

        monkeypatch.setattr(server_module, "kld7_vertical", None)
        monkeypatch.setattr(server_module, "kld7_horizontal", StubHorizontalTracker())
        monkeypatch.setattr(server_module, "camera_tracker", None)
        monkeypatch.setattr(server_module, "camera_enabled", False)
        monkeypatch.setattr(server_module, "monitor", None)
        monkeypatch.setattr(server_module, "debug_mode", False)
        monkeypatch.setattr(server_module, "get_session_logger", lambda: StubSessionLogger())
        monkeypatch.setattr(server_module.socketio, "emit", lambda *args, **kwargs: None)

        shot = Shot(
            ball_speed_mph=108.0,
            timestamp=datetime.now(),
            club=ClubType.IRON_7,
        )

        on_shot_detected(shot)

        assert shot.launch_angle_horizontal == pytest.approx(0.0)
        assert shot.launch_angle_horizontal_source == "estimated"
        assert logged_buffers[0]["ball_angle"]["selection_reason"] == "weak_near_limit"

    def test_vertical_radar_gets_neutral_horizontal_fallback(self, monkeypatch):
        """A good vertical radar angle should still emit a horizontal value."""

        class StubVerticalTracker:
            orientation = "vertical"

            def snapshot_buffer(self):
                return []

            def get_angle_for_shot(
                self, shot_timestamp=None, ball_speed_mph=None, impact_timestamp=None, **kwargs
            ):
                return KLD7Angle(vertical_deg=18.7, confidence=0.8, num_frames=2)

            def get_club_angle(self, club_speed_mph=None, shot_timestamp=None):
                return None

            def reset(self):
                return None

        monkeypatch.setattr(server_module, "kld7_vertical", StubVerticalTracker())
        monkeypatch.setattr(server_module, "kld7_horizontal", None)
        monkeypatch.setattr(server_module, "camera_tracker", None)
        monkeypatch.setattr(server_module, "camera_enabled", False)
        monkeypatch.setattr(server_module, "monitor", None)
        monkeypatch.setattr(server_module, "debug_mode", False)
        monkeypatch.setattr(server_module, "get_session_logger", lambda: None)
        monkeypatch.setattr(server_module.socketio, "emit", lambda *args, **kwargs: None)

        shot = Shot(
            ball_speed_mph=82.5,
            club_speed_mph=57.0,
            timestamp=datetime.now(),
            club=ClubType.DRIVER,
        )

        on_shot_detected(shot)

        assert shot.angle_source == "radar"
        assert shot.launch_angle_vertical == pytest.approx(18.7)
        assert shot.launch_angle_horizontal == pytest.approx(0.0)
        assert shot.launch_angle_vertical_source == "radar"
        assert shot.launch_angle_horizontal_source == "estimated"

    def test_mock_shot_missing_angles_gets_fallback_values(self, monkeypatch):
        """Even malformed/manual mock shots should emit user-facing angles."""
        monkeypatch.setattr(server_module, "monitor", None)
        monkeypatch.setattr(server_module, "debug_mode", False)
        monkeypatch.setattr(server_module, "get_session_logger", lambda: None)
        monkeypatch.setattr(server_module.socketio, "emit", lambda *args, **kwargs: None)

        shot = Shot(
            ball_speed_mph=100.0,
            timestamp=datetime.now(),
            club=ClubType.IRON_7,
            mode="mock",
        )

        on_shot_detected(shot)

        assert shot.angle_source == "estimated"
        assert shot.launch_angle_vertical == pytest.approx(20.5)
        assert shot.launch_angle_horizontal == pytest.approx(0.0)

    def test_implausible_club_aoa_is_rejected(self, monkeypatch):
        """A +31Â° club AoA is physically impossible and should be discarded."""

        class StubTracker:
            orientation = "vertical"

            def snapshot_buffer(self):
                return []

            def get_angle_for_shot(
                self, shot_timestamp=None, ball_speed_mph=None, impact_timestamp=None, **kwargs
            ):
                return KLD7Angle(vertical_deg=15.0, confidence=0.7, num_frames=2)

            def get_club_angle(self, club_speed_mph=None, shot_timestamp=None):
                # Radar reports -31Â° vertical â†’ server negates to +31Â° AoA
                return KLD7Angle(vertical_deg=-31.0, confidence=0.7, num_frames=2)

            def reset(self):
                return None

        monkeypatch.setattr(server_module, "kld7_vertical", StubTracker())
        monkeypatch.setattr(server_module, "camera_tracker", None)
        monkeypatch.setattr(server_module, "camera_enabled", False)
        monkeypatch.setattr(server_module, "monitor", None)
        monkeypatch.setattr(server_module, "debug_mode", False)
        monkeypatch.setattr(server_module, "get_session_logger", lambda: None)
        monkeypatch.setattr(server_module.socketio, "emit", lambda *args, **kwargs: None)

        shot = Shot(
            ball_speed_mph=115.0,
            club_speed_mph=80.0,
            timestamp=datetime.now(),
            club=ClubType.IRON_7,
        )

        on_shot_detected(shot)

        assert shot.club_angle_deg is None, (
            f"AoA of +31Â° should be rejected, got {shot.club_angle_deg}"
        )

    def test_plausible_kld7_angle_remains_radar_source(self, monkeypatch):
        """Plausible radar angles should continue to override the estimate."""

        class StubTracker:
            orientation = "vertical"

            def snapshot_buffer(self):
                return []

            def get_angle_for_shot(
                self, shot_timestamp=None, ball_speed_mph=None, impact_timestamp=None, **kwargs
            ):
                return KLD7Angle(vertical_deg=18.7, confidence=0.8, num_frames=2)

            def reset(self):
                return None

        monkeypatch.setattr(server_module, "kld7_vertical", StubTracker())
        monkeypatch.setattr(server_module, "camera_tracker", None)
        monkeypatch.setattr(server_module, "camera_enabled", False)
        monkeypatch.setattr(server_module, "monitor", None)
        monkeypatch.setattr(server_module, "debug_mode", False)
        monkeypatch.setattr(server_module, "get_session_logger", lambda: None)
        monkeypatch.setattr(server_module.socketio, "emit", lambda *args, **kwargs: None)

        shot = Shot(
            ball_speed_mph=82.5,
            club_speed_mph=57.0,
            timestamp=datetime.now(),
            club=ClubType.DRIVER,
        )

        on_shot_detected(shot)

        assert shot.angle_source == "radar"
        assert shot.launch_angle_vertical == pytest.approx(18.7)
        assert shot.launch_angle_horizontal == pytest.approx(0.0)

    def _spin_axis_shot(self, *, horizontal_confidence):
        """A shot with launch angle and club path already resolved, isolating
        the spin-axis gate itself rather than whatever radar path fed it."""
        return Shot(
            ball_speed_mph=150.0,
            club_speed_mph=100.0,
            timestamp=datetime.now(),
            impact_timestamp=1234.5,
            club=ClubType.DRIVER,
            launch_angle_horizontal=3.2,
            launch_angle_horizontal_confidence=horizontal_confidence,
            club_path_deg=-1.5,
        )

    def _run_with_no_radar_hardware(self, monkeypatch, shot):
        monkeypatch.setattr(server_module, "iwr6843_runtime", None)
        monkeypatch.setattr(server_module, "kld7_vertical", None)
        monkeypatch.setattr(server_module, "kld7_horizontal", None)
        monkeypatch.setattr(server_module, "camera_tracker", None)
        monkeypatch.setattr(server_module, "camera_enabled", False)
        monkeypatch.setattr(server_module, "monitor", None)
        monkeypatch.setattr(server_module, "debug_mode", False)
        monkeypatch.setattr(server_module, "get_session_logger", lambda: None)
        monkeypatch.setattr(server_module.socketio, "emit", lambda *args, **kwargs: None)
        on_shot_detected(shot)

    def test_spin_axis_emitted_when_horizontal_confidence_clears_gate(self, monkeypatch):
        shot = self._spin_axis_shot(horizontal_confidence=server_module.SPIN_AXIS_MIN_CONFIDENCE)

        self._run_with_no_radar_hardware(monkeypatch, shot)

        assert shot.spin_axis_deg == pytest.approx(3.2 - (-1.5))

    def test_spin_axis_withheld_when_horizontal_confidence_below_gate(self, monkeypatch):
        """Regression guard: spin axis must not appear the moment club path
        is non-null -- only once the horizontal leg is trustworthy enough."""
        shot = self._spin_axis_shot(
            horizontal_confidence=server_module.SPIN_AXIS_MIN_CONFIDENCE - 0.01
        )

        self._run_with_no_radar_hardware(monkeypatch, shot)

        assert shot.spin_axis_deg is None


class TestCarryComputation:
    """Tests for the ballistic carry path in on_shot_detected."""

    def _patch_environment(self, monkeypatch):
        monkeypatch.setattr(server_module, "kld7_vertical", None)
        monkeypatch.setattr(server_module, "kld7_horizontal", None)
        monkeypatch.setattr(server_module, "camera_tracker", None)
        monkeypatch.setattr(server_module, "camera_enabled", False)
        monkeypatch.setattr(server_module, "monitor", None)
        monkeypatch.setattr(server_module, "debug_mode", False)
        monkeypatch.setattr(server_module, "get_session_logger", lambda: None)
        monkeypatch.setattr(server_module.socketio, "emit", lambda *args, **kwargs: None)

    def test_carry_uses_ballistic_simulator_when_launch_angle_present(self, monkeypatch):
        """A shot with a vertical launch angle should get carry from the physics sim."""
        self._patch_environment(monkeypatch)
        monkeypatch.setattr(server_module, "ballistics_enabled", True)

        captured = {}

        from openflight import ballistics as ballistics_module

        real_simulate = ballistics_module.simulate

        def spying_simulate(conditions, *args, **kwargs):
            captured["conditions"] = conditions
            return real_simulate(conditions, *args, **kwargs)

        monkeypatch.setattr(server_module, "simulate", spying_simulate)

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

        assert "conditions" in captured, "simulate() should have been called"
        assert captured["conditions"].spin_source == "measured"
        assert shot.carry_spin_adjusted is not None
        assert 250 < shot.carry_spin_adjusted < 300

    def test_carry_falls_back_to_table_when_resolve_returns_none(self, monkeypatch):
        """When resolve_launch returns None, the table path should compute carry."""
        self._patch_environment(monkeypatch)

        monkeypatch.setattr(server_module, "resolve_launch", lambda shot: None)

        def fail_simulate(*args, **kwargs):
            raise AssertionError("simulate() must not be called when resolve_launch is None")

        monkeypatch.setattr(server_module, "simulate", fail_simulate)

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

        assert shot.carry_spin_adjusted is not None
        assert shot.carry_spin_adjusted > 0

    def test_carry_skips_ballistic_when_ballistics_disabled(self, monkeypatch):
        """When ballistics_enabled is False, the simulator must not run even
        if a valid launch angle is present â€” carry falls through to the
        table estimator. This is the default; `--ballistics` opts in."""
        self._patch_environment(monkeypatch)
        monkeypatch.setattr(server_module, "ballistics_enabled", False)

        def fail_resolve(*args, **kwargs):
            raise AssertionError("resolve_launch must not run when ballistics disabled")

        def fail_simulate(*args, **kwargs):
            raise AssertionError("simulate() must not run when ballistics disabled")

        monkeypatch.setattr(server_module, "resolve_launch", fail_resolve)
        monkeypatch.setattr(server_module, "simulate", fail_simulate)

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

        assert shot.carry_spin_adjusted is not None
        assert shot.carry_spin_adjusted > 0


class TestApplyCalculatedSpin:
    """Tests for the --calculated-spin shot rewrite."""

    def _shot(self, la=18.0, la_source="radar", ball_speed=115.0, spin=6800.0):
        return Shot(
            ball_speed_mph=ball_speed,
            timestamp=datetime.now(),
            club=ClubType.IRON_7,
            launch_angle_vertical=la,
            launch_angle_vertical_source=la_source,
            spin_rpm=spin,
            spin_confidence=0.3,
            spin_rejection_reason="SNR too low",
        )

    def test_rewrites_spin_when_launch_angle_measured(self):
        shot = self._shot()
        assert server_module._apply_calculated_spin(shot) is True
        # 170 * 115 * sin(18deg)^1.2 ~= 4800 rpm
        assert 4500 < shot.spin_rpm < 5100
        assert shot.spin_rpm_measured == 6800.0
        assert shot.spin_source == "calculated"
        assert shot.spin_confidence == pytest.approx(0.7)
        assert shot.spin_rejection_reason is None

    def test_untouched_when_launch_angle_estimated(self):
        shot = self._shot(la_source="estimated")
        assert server_module._apply_calculated_spin(shot) is False
        assert shot.spin_rpm == 6800.0
        assert shot.spin_source is None

    def test_untouched_when_no_launch_angle(self):
        shot = self._shot(la=None)
        assert server_module._apply_calculated_spin(shot) is False
        assert shot.spin_rpm == 6800.0

    def test_untouched_when_launch_angle_outside_model_range(self):
        shot = self._shot(la=1.0)
        assert server_module._apply_calculated_spin(shot) is False
        assert shot.spin_rpm == 6800.0

    def test_camera_launch_angle_accepted(self):
        shot = self._shot(la_source="camera")
        assert server_module._apply_calculated_spin(shot) is True
        assert shot.spin_source == "calculated"


class TestVerticalGateBypass:
    """--kld7-vertical-raw: show the radar angle for every candidate."""

    def _shot(self):
        return SimpleNamespace(
            club=ClubType.IRON_7, ball_speed_mph=110.0, club_speed_mph=86.0, spin_rpm=None
        )

    def test_default_marginal_accepts_out_of_lane_reading(self):
        # 0.6 deg for a 7-iron is outside the soft lane. It clears the hard
        # physics guard, so it is shown as a low-confidence (marginal) radar
        # reading rather than silently replaced by the club estimate.
        angle = KLD7Angle(vertical_deg=0.6, confidence=0.65, num_frames=1)
        accepted, details = server_module._select_vertical_radar_launch(angle, self._shot())
        assert accepted is True
        assert details["selection_reason"] == "marginal_accept:outside_soft_lane"
        assert details["acceptance_path"] == "marginal"

    def test_bypass_accepts_anything_with_a_candidate(self, monkeypatch):
        monkeypatch.setattr(server_module, "_VERTICAL_RADAR_GATE_BYPASS", True)
        angle = KLD7Angle(vertical_deg=0.6, confidence=0.65, num_frames=1)
        accepted, details = server_module._select_vertical_radar_launch(angle, self._shot())
        assert accepted is True
        assert details["selection_reason"] == "gate_bypassed"
        assert details["acceptance_path"] == "bypass"

    def test_bypass_still_needs_a_candidate(self, monkeypatch):
        monkeypatch.setattr(server_module, "_VERTICAL_RADAR_GATE_BYPASS", True)
        assert server_module._select_vertical_radar_launch(None, self._shot())[0] is False
        no_angle = KLD7Angle(vertical_deg=None, confidence=0.9, num_frames=2)
        assert server_module._select_vertical_radar_launch(no_angle, self._shot())[0] is False


class TestClubPathOwnershipGuard:
    """Two producers can write shot.club_path_deg (IWR6843 and the deprecated
    horizontal K-LD7). _process_iwr6843_angle runs first in on_shot_detected,
    so if both hardware paths were allowed to start, the deprecated radar
    would silently overwrite the IWR6843 value with no error and no log.
    The CLI must make that combination impossible to start, matching the
    existing --iwr6843/--kld7 (vertical) guard."""

    def test_iwr6843_and_kld7_horizontal_cannot_both_own_club_path(self, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", ["openflight-server", "--iwr6843", "--kld7-horizontal"])

        with pytest.raises(SystemExit) as exc_info:
            server_module.main()

        # code=2 pins this to argparse's parser.error(), not the unrelated
        # SystemExit(1) that main() raises further down when IWR6843
        # hardware init fails in a test environment -- that failure would
        # otherwise make this test pass whether or not the guard exists.
        # The message pins it to *this* guard, not one of the other
        # parser.error() calls in the same validation block.
        assert exc_info.value.code == 2
        assert "cannot both own club path" in capsys.readouterr().err

    def test_iwr6843_and_kld7_vertical_cannot_both_own_launch_angle(self, monkeypatch, capsys):
        """Existing guard this one is modeled on -- pinned so a refactor of
        the argparse validation block can't quietly drop either check."""
        monkeypatch.setattr(
            sys,
            "argv",
            ["openflight-server", "--iwr6843", "--kld7", "--kld7-mount-tilt", "0"],
        )

        with pytest.raises(SystemExit) as exc_info:
            server_module.main()

        assert exc_info.value.code == 2
        assert "cannot both own launch angle" in capsys.readouterr().err


class TestOpsBaudValidation:
    """The radar can only move to a rate it has an ``In`` API command for, so an
    unsupported --ops-baud is refused by the hardware and leaves the link at
    whatever answered. That presents as an unresponsive app -- a 40KB dump takes
    ~21s at 19,200 against ~1.8s at 230,400 -- rather than as a bad flag, and 0
    or a negative value reaches pyserial directly. Its sibling geometry flags
    already validate via parser.error; this one did not."""

    @pytest.mark.parametrize("bad", ["0", "-1", "250000", "9601"])
    def test_unsupported_ops_baud_is_refused_at_the_cli(self, monkeypatch, capsys, bad):
        monkeypatch.setattr(sys, "argv", ["openflight-server", "--ops-baud", bad])

        with pytest.raises(SystemExit) as exc_info:
            server_module.main()

        # code=2 pins this to parser.error() rather than a later SystemExit(1)
        # from hardware init failing in a test environment, which would make
        # this pass whether or not the guard exists.
        assert exc_info.value.code == 2
        err = capsys.readouterr().err
        assert "--ops-baud must be one of" in err
        # The message must name the valid rates; "invalid value" alone leaves
        # the operator guessing which of five the radar accepts.
        assert "230400" in err and "9600" in err

    @pytest.mark.parametrize("good", [9600, 19200, 57600, 115200, 230400])
    def test_every_api_supported_baud_is_accepted(self, good):
        """The guard must admit exactly the rates the radar has a command for --
        a stricter check would reject a legitimate fallback to 115200, which the
        flag's own help text tells operators to use."""
        assert good in UART_BAUD_COMMANDS


class TestWeatherRefresh:
    """The "Detect location" button.

    All of it runs off the shot path, on user action only. The contract is
    that a failed lookup produces an error in the UI and changes nothing --
    never an exception, and never a half-written config.
    """

    @pytest.fixture
    def weather(self, monkeypatch, tmp_path):
        """Isolate the provider and capture emits. Never touches the real config."""
        from openflight.environment.config import WeatherConfig
        from openflight.environment.provider import EnvironmentProvider

        emitted = []
        provider = EnvironmentProvider(WeatherConfig())
        monkeypatch.setattr(server_module, "environment_provider", provider)
        monkeypatch.setattr(
            server_module.socketio, "emit", lambda *args, **kwargs: emitted.append(args)
        )
        saved = []
        monkeypatch.setattr(server_module, "save_weather_config", lambda cfg: saved.append(cfg))
        return SimpleNamespace(provider=provider, emitted=emitted, saved=saved)

    def _events(self, weather):
        return [event for event, *_ in weather.emitted]

    def _payload(self, weather, name):
        return next(payload for event, payload in weather.emitted if event == name)

    def test_fetched_conditions_become_the_active_source(self, weather, monkeypatch):
        from openflight.environment.openmeteo import FetchedWeather

        weather.provider.config.latitude = 38.58
        weather.provider.config.longitude = -121.49
        monkeypatch.setattr(
            server_module,
            "fetch_current_weather",
            lambda *a, **k: FetchedWeather(36.1, 1010.2, 25.0),
        )

        server_module.refresh_weather_now()

        reading = weather.provider.current()
        assert reading.source == "open-meteo"
        assert reading.temp_c == pytest.approx(36.1)
        # 97 F at a sea-level venue: the Sacramento case from the design doc.
        assert reading.air_density_kg_m3 == pytest.approx(1.1316, abs=0.001)

    def test_successful_fetch_is_persisted(self, weather, monkeypatch):
        from openflight.environment.openmeteo import FetchedWeather

        weather.provider.config.latitude = 38.58
        weather.provider.config.longitude = -121.49
        monkeypatch.setattr(
            server_module,
            "fetch_current_weather",
            lambda *a, **k: FetchedWeather(36.1, 1010.2, 25.0),
        )

        server_module.refresh_weather_now()

        assert weather.saved, "a fetch the user waited for must survive a restart"
        assert "environment" in self._events(weather)

    def test_the_users_elevation_is_sent_to_the_api(self, weather, monkeypatch):
        """Open-Meteo reports pressure at ITS terrain height unless told the
        real one; ~12 Pa/m means a 100 m error is ~0.9 yd on a driver."""
        from openflight.environment.openmeteo import FetchedWeather

        seen = {}
        weather.provider.config.latitude = 38.58
        weather.provider.config.longitude = -121.49
        weather.provider.config.elevation_m = 9.0

        def spy(latitude, longitude, elevation_m=None, **kwargs):
            seen["elevation_m"] = elevation_m
            return FetchedWeather(36.1, 1010.2, 25.0)

        monkeypatch.setattr(server_module, "fetch_current_weather", spy)

        server_module.refresh_weather_now()

        assert seen["elevation_m"] == 9.0

    def test_failed_fetch_reports_an_error_and_changes_nothing(self, weather, monkeypatch):
        weather.provider.config.latitude = 38.58
        weather.provider.config.longitude = -121.49
        monkeypatch.setattr(server_module, "fetch_current_weather", lambda *a, **k: None)

        server_module.refresh_weather_now()

        assert "weather_error" in self._events(weather)
        assert weather.provider.current().source == "default"
        assert not weather.saved, "a failed fetch must not rewrite the config"

    def test_location_is_looked_up_when_none_is_configured(self, weather, monkeypatch):
        from openflight.environment.openmeteo import FetchedWeather, Location

        weather.provider.config.location_consent = True
        monkeypatch.setattr(
            server_module,
            "lookup_location",
            lambda **k: Location(38.58, -121.49, "Sacramento, California"),
        )
        monkeypatch.setattr(
            server_module,
            "fetch_current_weather",
            lambda *a, **k: FetchedWeather(36.1, 1010.2, 25.0),
        )

        server_module.refresh_weather_now()

        assert weather.provider.config.latitude == pytest.approx(38.58)
        assert weather.provider.config.location_label == "Sacramento, California"

    def test_lookup_requires_consent(self, weather, monkeypatch):
        """An IP geolocation call is a privacy decision, so it is opt-in."""
        called = []
        monkeypatch.setattr(
            server_module, "lookup_location", lambda **k: called.append(True) or None
        )

        server_module.refresh_weather_now()

        assert not called
        assert "weather_error" in self._events(weather)

    def test_failed_location_lookup_reports_an_error(self, weather, monkeypatch):
        weather.provider.config.location_consent = True
        monkeypatch.setattr(server_module, "lookup_location", lambda **k: None)

        server_module.refresh_weather_now()

        assert "weather_error" in self._events(weather)
        assert weather.provider.config.latitude is None

    def test_refresh_never_raises_into_the_caller(self, weather, monkeypatch):
        """The button lives next to a person hitting balls. Whatever the
        network does, the server keeps running."""
        weather.provider.config.latitude = 38.58
        weather.provider.config.longitude = -121.49

        def boom(*a, **k):
            raise RuntimeError("something nobody predicted")

        monkeypatch.setattr(server_module, "fetch_current_weather", boom)

        server_module.refresh_weather_now()

        assert "weather_error" in self._events(weather)


class TestWeatherRegression:
    """The guarantee for everyone who never opens the settings screen.

    With no sensor, no config and no flags, every number this repo produced
    before the weather subsystem existed must come out bit-for-bit the same.
    That is the whole reason `_apply_environment` returns early on "default"
    rather than stamping ISA values onto the shot.
    """

    @pytest.fixture
    def unconfigured(self, monkeypatch):
        from openflight.environment.config import WeatherConfig
        from openflight.environment.provider import EnvironmentProvider

        provider = EnvironmentProvider(WeatherConfig())
        monkeypatch.setattr(server_module, "environment_provider", provider)
        return provider

    def test_unconfigured_shot_carries_no_environment_fields(self, unconfigured):
        shot = Shot(ball_speed_mph=150.0, timestamp=datetime.now(), club=ClubType.DRIVER)

        server_module._apply_environment(shot)

        assert shot.air_density_kg_m3 is None
        assert shot.air_density_source is None
        assert shot.air_temp_c is None
        assert shot.air_pressure_hpa is None
        assert shot.humidity_pct is None

    def test_unconfigured_shot_dict_has_null_environment(self, unconfigured):
        shot = Shot(ball_speed_mph=150.0, timestamp=datetime.now(), club=ClubType.DRIVER)
        server_module._apply_environment(shot)

        payload = shot_to_dict(shot)

        assert payload["air_density_kg_m3"] is None
        assert payload["air_density_source"] is None
        assert payload["carry_standard_yards"] is None

    def test_table_carry_is_identical_to_the_uncorrected_estimate(self, unconfigured, monkeypatch):
        """Asserts the carry number itself, not just that density is None.

        The previous version of this test only checked `air_density_kg_m3 is
        None` and never computed a carry, so the table path could have been
        multiplied by anything and it would still have passed.
        """
        from openflight.rolling_buffer.monitor import (
            estimate_carry_with_spin,
            get_optimal_spin_for_ball_speed,
        )

        monkeypatch.setattr(server_module, "kld7_vertical", None)
        monkeypatch.setattr(server_module, "kld7_horizontal", None)
        monkeypatch.setattr(server_module, "camera_tracker", None)
        monkeypatch.setattr(server_module, "camera_enabled", False)
        monkeypatch.setattr(server_module, "monitor", None)
        monkeypatch.setattr(server_module, "debug_mode", False)
        monkeypatch.setattr(server_module, "get_session_logger", lambda: None)
        monkeypatch.setattr(server_module.socketio, "emit", lambda *a, **k: None)
        monkeypatch.setattr(server_module, "ballistics_enabled", False)

        shot = Shot(
            ball_speed_mph=150.0,
            club_speed_mph=103.0,
            timestamp=datetime.now(),
            club=ClubType.DRIVER,
        )
        expected = estimate_carry_with_spin(
            150.0,
            get_optimal_spin_for_ball_speed(150.0, ClubType.DRIVER),
            ClubType.DRIVER,
            club_speed_mph=103.0,
        )

        on_shot_detected(shot)

        assert shot.air_density_kg_m3 is None
        assert shot.carry_spin_adjusted == pytest.approx(expected)

    def test_the_server_passes_isa_to_simulate_when_density_is_unknown(
        self, unconfigured, monkeypatch
    ):
        """Captures what the SERVER passes, not what a Python expression
        evaluates to. The previous version asserted `None or AIR_DENSITY_STD`,
        which is true of the language regardless of the call site."""
        from openflight.ballistics import AIR_DENSITY_STD

        seen = {}
        real_simulate = server_module.simulate

        def spy(conditions, air_density=AIR_DENSITY_STD, **kwargs):
            seen["air_density"] = air_density
            return real_simulate(conditions, air_density=air_density, **kwargs)

        monkeypatch.setattr(server_module, "simulate", spy)
        monkeypatch.setattr(server_module, "kld7_vertical", None)
        monkeypatch.setattr(server_module, "kld7_horizontal", None)
        monkeypatch.setattr(server_module, "camera_tracker", None)
        monkeypatch.setattr(server_module, "camera_enabled", False)
        monkeypatch.setattr(server_module, "monitor", None)
        monkeypatch.setattr(server_module, "debug_mode", False)
        monkeypatch.setattr(server_module, "get_session_logger", lambda: None)
        monkeypatch.setattr(server_module.socketio, "emit", lambda *a, **k: None)
        monkeypatch.setattr(server_module, "ballistics_enabled", True)

        on_shot_detected(
            Shot(
                ball_speed_mph=150.0,
                timestamp=datetime.now(),
                club=ClubType.DRIVER,
                launch_angle_vertical=12.0,
                spin_rpm=2700.0,
                spin_confidence=0.9,
            )
        )

        assert seen["air_density"] == AIR_DENSITY_STD

    def test_standard_carry_is_skipped_without_a_density(self, unconfigured):
        """No density means no correction to compare against, so the second
        integration must not run -- it would cost 50-85 ms for nothing."""
        from openflight.ballistics import LaunchConditions

        shot = Shot(ball_speed_mph=150.0, timestamp=datetime.now(), club=ClubType.DRIVER)
        conditions = LaunchConditions(150.0, 12.0, 0.0, 2700.0, 0.0, "measured")

        server_module._apply_standard_carry(shot, conditions)

        assert shot.carry_standard_yards is None


class TestStandardCarryThreshold:
    """The second integration is lazy: it only runs when it would change the
    displayed yardage."""

    @pytest.fixture
    def provider(self, monkeypatch):
        from openflight.environment.config import WeatherConfig
        from openflight.environment.provider import EnvironmentProvider

        provider = EnvironmentProvider(WeatherConfig())
        monkeypatch.setattr(server_module, "environment_provider", provider)
        return provider

    def _conditions(self):
        from openflight.ballistics import LaunchConditions

        return LaunchConditions(165.0, 12.5, 0.0, 2600.0, 0.0, "measured")

    def test_disabled_by_the_user_means_no_second_figure(self, provider):
        provider.config.show_standard = False
        shot = Shot(ball_speed_mph=165.0, timestamp=datetime.now(), club=ClubType.DRIVER)
        shot.air_density_kg_m3 = 0.97  # Denver: a huge deviation

        server_module._apply_standard_carry(shot, self._conditions())

        assert shot.carry_standard_yards is None

    def test_below_half_a_percent_deviation_is_skipped(self, provider):
        """Both figures would round to the same yardage, so the extra RK4 pass
        and the extra line of UI are pure noise."""
        shot = Shot(ball_speed_mph=165.0, timestamp=datetime.now(), club=ClubType.DRIVER)
        shot.air_density_kg_m3 = provider.standard_density() * 1.004

        server_module._apply_standard_carry(shot, self._conditions())

        assert shot.carry_standard_yards is None

    def test_above_half_a_percent_deviation_is_computed(self, provider):
        shot = Shot(ball_speed_mph=165.0, timestamp=datetime.now(), club=ClubType.DRIVER)
        shot.air_density_kg_m3 = provider.standard_density() * 1.02

        server_module._apply_standard_carry(shot, self._conditions())

        assert shot.carry_standard_yards is not None

    def test_the_standard_figure_uses_reference_air_not_todays(self, provider):
        """A hot day must move the main carry and leave this one alone -- that
        is the entire point of showing it."""
        from openflight.ballistics import simulate

        shot = Shot(ball_speed_mph=165.0, timestamp=datetime.now(), club=ClubType.DRIVER)
        shot.air_density_kg_m3 = 1.1316  # Sacramento at 97 F

        server_module._apply_standard_carry(shot, self._conditions())

        expected = simulate(self._conditions(), air_density=provider.standard_density())
        assert shot.carry_standard_yards == pytest.approx(expected.carry_yards)

    def test_thin_air_reads_longer_than_the_standard_figure(self, provider):
        """Sanity on the direction: Denver air must make today's carry the
        bigger of the two numbers, or the labels are backwards in the UI."""
        from openflight.ballistics import simulate

        shot = Shot(ball_speed_mph=165.0, timestamp=datetime.now(), club=ClubType.DRIVER)
        shot.air_density_kg_m3 = 0.97

        server_module._apply_standard_carry(shot, self._conditions())
        today = simulate(self._conditions(), air_density=0.97).carry_yards

        assert today > shot.carry_standard_yards


class TestWeatherCliFlags:
    """Session-only overrides for headless and bench use. Never written to disk."""

    @pytest.fixture
    def provider(self, monkeypatch):
        from openflight.environment.config import WeatherConfig
        from openflight.environment.provider import EnvironmentProvider

        provider = EnvironmentProvider(WeatherConfig())
        monkeypatch.setattr(server_module, "environment_provider", provider)
        return provider

    def _args(self, **overrides):
        defaults = dict(
            weather_density=None,
            weather_temp_c=None,
            weather_pressure_hpa=None,
            weather_elevation_m=0.0,
            weather_humidity=None,
        )
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    def test_no_flags_leaves_the_provider_untouched(self, provider):
        server_module._resolve_cli_environment(self._args())

        assert provider.current().source == "default"

    def test_explicit_density_is_used_verbatim(self, provider):
        server_module._resolve_cli_environment(self._args(weather_density=1.0500))

        assert provider.current().air_density_kg_m3 == pytest.approx(1.05)

    def test_temp_and_pressure_resolve_through_the_psychrometric_model(self, provider):
        server_module._resolve_cli_environment(
            self._args(weather_temp_c=36.0, weather_pressure_hpa=1010.2, weather_humidity=25.0)
        )

        from openflight.environment import air_density

        assert provider.current().air_density_kg_m3 == pytest.approx(
            air_density(36.0, 101020.0, 25.0)
        )

    def test_temp_and_elevation_estimate_pressure(self, provider):
        """The bench case: 36 C at 9 m, with humidity defaulting to 50%.

        1.1279, not the 1.1338 an earlier draft of the checklist quoted --
        that figure is the same conditions at 25% RH. Humidity is the smallest
        term but it is not nothing: 25 points of it is 0.5% of density.
        """
        server_module._resolve_cli_environment(
            self._args(weather_temp_c=36.0, weather_elevation_m=9.0)
        )

        assert provider.current().air_density_kg_m3 == pytest.approx(1.1279, abs=0.001)

    def test_the_same_conditions_at_lower_humidity_are_denser(self, provider):
        server_module._resolve_cli_environment(
            self._args(weather_temp_c=36.0, weather_elevation_m=9.0, weather_humidity=25.0)
        )

        assert provider.current().air_density_kg_m3 == pytest.approx(1.1338, abs=0.001)

    def test_humidity_defaults_to_fifty_percent(self, provider):
        server_module._resolve_cli_environment(
            self._args(weather_temp_c=36.0, weather_elevation_m=9.0)
        )

        assert provider.current().humidity_pct == pytest.approx(50.0)

    def test_a_flag_override_outranks_the_saved_config(self, provider):
        """An operator flag is for this session; the settings file is the
        user's standing preference. The flag must win."""
        provider.config.cached = {
            "temp_c": 5.0,
            "pressure_hpa": 1030.0,
            "humidity_pct": 80.0,
            "fetched_at": 1.0,
        }

        server_module._resolve_cli_environment(self._args(weather_density=1.0))

        assert provider.current().air_density_kg_m3 == pytest.approx(1.0)


class TestSetWeatherSettings:
    @pytest.fixture
    def weather(self, monkeypatch):
        from openflight.environment.config import WeatherConfig
        from openflight.environment.provider import EnvironmentProvider

        emitted = []
        saved = []
        provider = EnvironmentProvider(WeatherConfig())
        monkeypatch.setattr(server_module, "environment_provider", provider)
        monkeypatch.setattr(
            server_module.socketio, "emit", lambda *args, **kwargs: emitted.append(args)
        )
        monkeypatch.setattr(server_module, "save_weather_config", lambda cfg: saved.append(cfg))
        return SimpleNamespace(provider=provider, emitted=emitted, saved=saved)

    def test_settings_are_persisted(self, weather):
        server_module.handle_set_weather_settings({"mode": "manual", "manual_temp_c": 30.0})

        assert weather.saved
        assert weather.provider.config.mode == "manual"

    def test_the_new_resolution_is_broadcast(self, weather):
        server_module.handle_set_weather_settings(
            {"mode": "manual", "manual_temp_c": 36.0, "manual_pressure_hpa": 1010.2}
        )

        events = [event for event, *_ in weather.emitted]
        assert "environment" in events
        assert "weather_settings" in events

    def test_an_unknown_mode_is_ignored_rather_than_stored(self, weather):
        server_module.handle_set_weather_settings({"mode": "supersonic"})

        assert weather.provider.config.mode == "auto"

    def test_a_partial_payload_leaves_unmentioned_fields_alone(self, weather):
        """The UI sends only what the user just changed, never the whole form.

        It used to send its entire local draft on every keystroke, which raced
        with any in-flight fetch: tap a checkbox while "Detect location" is
        still running and the draft's pre-detection nulls land after the
        coordinates, wiping them. Sending a patch removes the race outright,
        but only while this handler keeps merging rather than replacing.
        """
        config = weather.provider.config
        config.latitude = 38.58
        config.longitude = -121.49
        config.location_label = "Sacramento, California"
        config.elevation_m = 9.0

        server_module.handle_set_weather_settings({"show_standard": False})

        assert config.latitude == 38.58
        assert config.longitude == -121.49
        assert config.location_label == "Sacramento, California"
        assert config.elevation_m == 9.0
        assert config.show_standard is False

    @pytest.mark.parametrize("payload", [None, [], "manual", 42, ["mode", "manual"]])
    def test_a_payload_that_is_not_a_mapping_is_ignored(self, weather, payload):
        """Socket.IO hands the handler whatever the client emitted. `.get()` on
        a list or None raises inside the event loop, so a malformed or hostile
        client could take down the connection every other tab shares."""
        from openflight.environment.config import WeatherConfig

        server_module.handle_set_weather_settings(payload)

        assert weather.provider.config == WeatherConfig()
        assert not weather.saved

    def test_a_failed_save_still_applies_for_this_session(self, weather, monkeypatch):
        def cannot_write(_config):
            raise OSError("read-only filesystem")

        monkeypatch.setattr(server_module, "save_weather_config", cannot_write)

        server_module.handle_set_weather_settings({"mode": "manual", "manual_temp_c": 36.0})

        assert weather.provider.config.manual_temp_c == 36.0
        assert "weather_error" in [event for event, *_ in weather.emitted]

    def test_settings_payload_carries_both_namespaces(self, weather):
        """Manual entry and local weather are separate set-ups, so the payload
        has to carry both rather than one shared field per value."""
        payload = server_module._weather_settings_payload()

        for key in ("elevation_m", "manual_elevation_m", "indoor_temp_c", "manual_temp_c"):
            assert key in payload

    def test_an_implausible_elevation_is_stored_but_warned_about(self, weather, caplog):
        """Someone might genuinely be in Leadville, so it is never silently
        corrected -- but it is far more often the R10-in-E6 fudge."""
        import logging

        with caplog.at_level(logging.WARNING):
            server_module.handle_set_weather_settings({"elevation_m": 3048.0})

        assert weather.provider.config.elevation_m == 3048.0
        assert "spin_source" in caplog.text


class TestStandardCarryOnTheTablePath:
    """Shots without a measured launch angle get the density correction, so
    they must get the comparable reference figure too -- otherwise the second
    number appears and disappears depending on whether the angle radar
    happened to see that shot."""

    @pytest.fixture
    def provider(self, monkeypatch):
        from openflight.environment.config import WeatherConfig
        from openflight.environment.provider import EnvironmentProvider

        provider = EnvironmentProvider(WeatherConfig())
        monkeypatch.setattr(server_module, "environment_provider", provider)
        return provider

    def test_table_carry_gets_a_standard_figure(self, provider):
        shot = Shot(ball_speed_mph=150.0, timestamp=datetime.now(), club=ClubType.DRIVER)
        shot.air_density_kg_m3 = 0.97  # Denver
        shot.carry_spin_adjusted = 260.0

        server_module._apply_standard_carry(shot)

        assert shot.carry_standard_yards is not None

    def test_the_rescale_is_exact_for_the_table_estimator(self, provider):
        """base * f(today) / f(today) * f(std) == base * f(std), with no second
        integration and no re-derivation of the base estimate."""
        from openflight.ballistics import density_carry_factor

        base = 250.0
        today = 0.97
        shot = Shot(ball_speed_mph=150.0, timestamp=datetime.now(), club=ClubType.DRIVER)
        shot.air_density_kg_m3 = today
        shot.carry_spin_adjusted = base * density_carry_factor(today)

        server_module._apply_standard_carry(shot)

        expected = base * density_carry_factor(provider.standard_density())
        assert shot.carry_standard_yards == pytest.approx(expected)

    def test_thin_air_still_reads_longer_than_the_reference(self, provider):
        shot = Shot(ball_speed_mph=150.0, timestamp=datetime.now(), club=ClubType.DRIVER)
        shot.air_density_kg_m3 = 0.97
        shot.carry_spin_adjusted = 270.0

        server_module._apply_standard_carry(shot)

        assert shot.carry_spin_adjusted > shot.carry_standard_yards

    def test_no_second_integration_is_attempted_without_a_carry(self, provider):
        """Nothing to rescale means nothing to report -- and crucially no crash
        from passing None into simulate()."""
        shot = Shot(ball_speed_mph=150.0, timestamp=datetime.now(), club=ClubType.DRIVER)
        shot.air_density_kg_m3 = 0.97

        server_module._apply_standard_carry(shot)

        assert shot.carry_standard_yards is None

    def test_the_threshold_still_applies(self, provider):
        shot = Shot(ball_speed_mph=150.0, timestamp=datetime.now(), club=ClubType.DRIVER)
        shot.air_density_kg_m3 = provider.standard_density() * 1.002
        shot.carry_spin_adjusted = 250.0

        server_module._apply_standard_carry(shot)

        assert shot.carry_standard_yards is None


class TestMockModeShowsTheCorrection:
    """Mock is the mode this feature gets demoed and reviewed in.

    0b4d7f1 deliberately kept mock shots out of carry *modelling* ("calculate
    on real world data"), and that stands -- mock still uses the table estimate
    rather than being handed to the integrator. But applying the measured air
    to that estimate is presentation, not modelling. Without it the Conditions
    badge reads live while the carry beneath it is silently uncorrected, which
    is worse than showing nothing.
    """

    @pytest.fixture
    def harness(self, monkeypatch):
        from openflight.environment.config import WeatherConfig
        from openflight.environment.provider import EnvironmentProvider

        provider = EnvironmentProvider(WeatherConfig())
        monkeypatch.setattr(server_module, "environment_provider", provider)
        monkeypatch.setattr(server_module, "kld7_vertical", None)
        monkeypatch.setattr(server_module, "kld7_horizontal", None)
        monkeypatch.setattr(server_module, "camera_tracker", None)
        monkeypatch.setattr(server_module, "camera_enabled", False)
        monkeypatch.setattr(server_module, "monitor", None)
        monkeypatch.setattr(server_module, "debug_mode", False)
        monkeypatch.setattr(server_module, "get_session_logger", lambda: None)
        monkeypatch.setattr(server_module.socketio, "emit", lambda *a, **k: None)
        return provider

    def _mock_shot(self):
        return Shot(
            ball_speed_mph=143.0,
            club_speed_mph=99.0,
            timestamp=datetime.now(),
            club=ClubType.DRIVER,
            launch_angle_vertical=11.0,
            spin_rpm=2700.0,
            mode="mock",
        )

    def test_thin_air_lengthens_a_mock_carry(self, harness):
        harness.set_cli_override(
            server_module.EnvironmentReading(0.97, "manual")  # Denver
        )
        shot = self._mock_shot()

        on_shot_detected(shot)

        assert shot.carry_spin_adjusted is not None
        assert shot.carry_spin_adjusted > shot.estimated_carry_yards

    def test_mock_gets_the_standard_figure_too(self, harness):
        harness.set_cli_override(server_module.EnvironmentReading(0.97, "manual"))
        shot = self._mock_shot()

        on_shot_detected(shot)

        assert shot.carry_standard_yards is not None
        assert shot.carry_standard_yards < shot.carry_spin_adjusted

    def test_mock_carry_is_untouched_when_no_weather_is_configured(self, harness):
        """The regression guarantee holds in mock as well: no config, no change."""
        shot = self._mock_shot()

        on_shot_detected(shot)

        assert shot.carry_spin_adjusted is None
        assert shot.carry_standard_yards is None

    def test_mock_is_still_not_handed_to_the_integrator(self, harness, monkeypatch):
        """The maintainer's decision stands: mock keeps the table estimate and
        never triggers an RK4 flight."""
        simulated = []
        monkeypatch.setattr(
            server_module,
            "simulate",
            lambda *a, **k: simulated.append(a) or (_ for _ in ()).throw(AssertionError()),
        )
        harness.set_cli_override(server_module.EnvironmentReading(0.97, "manual"))

        on_shot_detected(self._mock_shot())

        assert not simulated


class TestLocationSearch:
    """Typed search is the primary way a location gets set.

    IP detection stays as a suggestion, but it cannot be the only path: behind
    a VPN it returns the exit node, and the weather that follows is wrong in a
    way that looks entirely plausible.
    """

    @pytest.fixture
    def weather(self, monkeypatch):
        from openflight.environment.config import WeatherConfig
        from openflight.environment.provider import EnvironmentProvider

        emitted = []
        provider = EnvironmentProvider(WeatherConfig())
        monkeypatch.setattr(server_module, "environment_provider", provider)
        monkeypatch.setattr(
            server_module.socketio, "emit", lambda *args, **kwargs: emitted.append(args)
        )
        monkeypatch.setattr(server_module, "save_weather_config", lambda cfg: None)
        return SimpleNamespace(provider=provider, emitted=emitted)

    def _payload(self, weather, name):
        return next(payload for event, payload in weather.emitted if event == name)

    def test_matches_are_sent_to_the_ui(self, weather, monkeypatch):
        from openflight.environment.openmeteo import LocationResult

        monkeypatch.setattr(
            server_module,
            "search_locations",
            lambda q, **k: [LocationResult("Sacramento", 38.58, -121.49, 9.0, "US", "California")],
        )

        server_module.search_locations_now("Sacram")

        payload = self._payload(weather, "location_results")
        assert payload["results"][0]["label"] == "Sacramento, California, US"
        assert payload["results"][0]["elevation_m"] == 9.0

    def test_the_query_comes_back_with_the_results(self, weather, monkeypatch):
        """Typing is faster than the network, so a late reply must be
        attributable to the query that asked for it."""
        monkeypatch.setattr(server_module, "search_locations", lambda q, **k: [])

        server_module.search_locations_now("Sacram")

        assert self._payload(weather, "location_results")["query"] == "Sacram"

    def test_no_matches_sends_an_empty_list_not_an_error(self, weather, monkeypatch):
        monkeypatch.setattr(server_module, "search_locations", lambda q, **k: [])

        server_module.search_locations_now("zzzzzz")

        assert self._payload(weather, "location_results")["results"] == []

    def test_a_search_that_blows_up_still_answers(self, weather, monkeypatch):
        """Otherwise the UI spins forever on a stuck request."""

        def boom(*a, **k):
            raise RuntimeError("nobody predicted this")

        monkeypatch.setattr(server_module, "search_locations", boom)

        server_module.search_locations_now("Sacram")

        assert self._payload(weather, "location_results")["results"] == []

    def test_choosing_a_result_adopts_its_coordinates(self, weather, monkeypatch):
        monkeypatch.setattr(server_module, "fetch_current_weather", lambda *a, **k: None)

        server_module.select_location_now(38.58, -121.49, "Sacramento, California, US", 9.0)

        assert weather.provider.config.latitude == pytest.approx(38.58)
        assert weather.provider.config.location_label == "Sacramento, California, US"

    def test_choosing_a_result_takes_its_elevation(self, weather, monkeypatch):
        """The search already knows it, and it is the term the user is least
        able to supply -- 100 m out is about 0.9 yd on a driver."""
        monkeypatch.setattr(server_module, "fetch_current_weather", lambda *a, **k: None)

        server_module.select_location_now(38.58, -121.49, "Sacramento", 9.0)

        assert weather.provider.config.elevation_m == 9.0

    def test_an_unknown_elevation_clears_the_previous_venues(self, weather, monkeypatch):
        """This test previously asserted the opposite, and was wrong.

        Keeping Denver's 1,609 m while moving to a result with no elevation
        makes Open-Meteo compute the new city's surface pressure at Denver's
        terrain -- wrong by most of a mile, with nothing on screen to say so.
        Unknown has to mean unknown.
        """
        weather.provider.config.elevation_m = 1609.0
        monkeypatch.setattr(server_module, "fetch_current_weather", lambda *a, **k: None)

        server_module.select_location_now(38.58, -121.49, "Somewhere", None)

        assert weather.provider.config.elevation_m is None

    def test_choosing_a_location_drops_the_old_ones_cached_weather(self, weather, monkeypatch):
        """Otherwise a failed fetch leaves the previous city's temperature and
        pressure being applied to shots under the new location's name."""
        weather.provider.config.cached = {
            "temp_c": -10.0,
            "pressure_hpa": 1030.0,
            "fetched_at": 1.0,
        }
        monkeypatch.setattr(server_module, "fetch_current_weather", lambda *a, **k: None)

        server_module.select_location_now(38.58, -121.49, "Sacramento", 9.0)

        assert weather.provider.config.cached == {}
        assert weather.provider.current().source == "default"

    def test_choosing_a_result_fetches_its_weather_immediately(self, weather, monkeypatch):
        from openflight.environment.openmeteo import FetchedWeather

        monkeypatch.setattr(
            server_module,
            "fetch_current_weather",
            lambda *a, **k: FetchedWeather(36.1, 1010.2, 25.0),
        )

        server_module.select_location_now(38.58, -121.49, "Sacramento", 9.0)

        assert weather.provider.current().source == "open-meteo"

    def test_the_chosen_elevation_is_sent_to_the_forecast(self, weather, monkeypatch):
        from openflight.environment.openmeteo import FetchedWeather

        seen = {}

        def spy(latitude, longitude, elevation_m=None, **kwargs):
            seen["elevation_m"] = elevation_m
            return FetchedWeather(36.1, 1010.2, 25.0)

        monkeypatch.setattr(server_module, "fetch_current_weather", spy)

        server_module.select_location_now(38.58, -121.49, "Sacramento", 9.0)

        assert seen["elevation_m"] == 9.0

    def test_a_result_without_coordinates_is_refused(self, weather):
        server_module.handle_select_location({"label": "nowhere"})

        assert "weather_error" in [event for event, *_ in weather.emitted]
        assert weather.provider.config.latitude is None


class TestDetectCurrentLocation:
    """Re-detecting is a different action from refreshing.

    Refresh re-fetches weather for the location already chosen. Once someone
    has searched for a city there was otherwise no way back to "where am I
    now" short of editing the config by hand -- which matters for a unit that
    moves between a home bay and a range.
    """

    @pytest.fixture
    def weather(self, monkeypatch):
        from openflight.environment.config import WeatherConfig
        from openflight.environment.provider import EnvironmentProvider

        emitted = []
        provider = EnvironmentProvider(WeatherConfig(location_consent=True))
        provider.config.latitude = 51.5
        provider.config.longitude = -0.12
        provider.config.location_label = "London, England, GB"
        provider.config.elevation_m = 11.0
        monkeypatch.setattr(server_module, "environment_provider", provider)
        monkeypatch.setattr(
            server_module.socketio, "emit", lambda *args, **kwargs: emitted.append(args)
        )
        monkeypatch.setattr(server_module, "save_weather_config", lambda cfg: None)
        return SimpleNamespace(provider=provider, emitted=emitted)

    def test_it_replaces_a_chosen_city(self, weather, monkeypatch):
        from openflight.environment.openmeteo import FetchedWeather, Location

        monkeypatch.setattr(
            server_module, "lookup_location", lambda **k: Location(38.58, -121.49, "Sacramento")
        )
        monkeypatch.setattr(
            server_module,
            "fetch_current_weather",
            lambda *a, **k: FetchedWeather(36.1, 1010.2, 25.0),
        )

        server_module.detect_location_now()

        assert weather.provider.config.location_label == "Sacramento"
        assert weather.provider.config.latitude == pytest.approx(38.58)

    def test_it_drops_the_old_venue_elevation(self, weather, monkeypatch):
        """Keeping London's 11 m while detecting Denver would apply the wrong
        terrain to the new place, and nothing on screen would say so."""
        from openflight.environment.openmeteo import Location

        monkeypatch.setattr(
            server_module, "lookup_location", lambda **k: Location(39.74, -104.98, "Denver")
        )
        monkeypatch.setattr(server_module, "fetch_current_weather", lambda *a, **k: None)

        server_module.detect_location_now()

        assert weather.provider.config.elevation_m is None

    def test_it_still_needs_consent(self, weather, monkeypatch):
        called = []
        weather.provider.config.location_consent = False
        monkeypatch.setattr(
            server_module, "lookup_location", lambda **k: called.append(True) or None
        )

        server_module.detect_location_now()

        assert not called
        assert "weather_error" in [event for event, *_ in weather.emitted]

    def test_refresh_leaves_the_chosen_city_alone(self, weather, monkeypatch):
        """The distinction being drawn: refresh must NOT re-detect."""
        from openflight.environment.openmeteo import FetchedWeather

        monkeypatch.setattr(
            server_module, "lookup_location", lambda **k: pytest.fail("must not re-detect")
        )
        monkeypatch.setattr(
            server_module,
            "fetch_current_weather",
            lambda *a, **k: FetchedWeather(12.0, 1005.0, 70.0),
        )

        server_module.refresh_weather_now()

        assert weather.provider.config.location_label == "London, England, GB"
        assert weather.provider.config.elevation_m == 11.0


class TestAutoRefreshLoop:
    """The background re-fetch.

    The design doc argued for no polling; this is a deliberate, opt-in
    reversal, so the guards around it matter more than the loop itself.
    """

    @pytest.fixture
    def weather(self, monkeypatch):
        from openflight.environment.config import WeatherConfig
        from openflight.environment.provider import EnvironmentProvider

        provider = EnvironmentProvider(
            WeatherConfig(
                latitude=38.58,
                longitude=-121.49,
                location_consent=True,
                auto_refresh_minutes=30,
                cached={"temp_c": 20.0, "pressure_hpa": 1013.0, "fetched_at": 1000.0},
            )
        )
        monkeypatch.setattr(server_module, "environment_provider", provider)
        monkeypatch.setattr(server_module.socketio, "emit", lambda *a, **k: None)
        monkeypatch.setattr(server_module, "save_weather_config", lambda cfg: None)
        return SimpleNamespace(provider=provider)

    def test_a_background_failure_is_not_announced(self, weather, monkeypatch):
        """A scheduled fetch failing is not news: the cached reading and its
        age are still on screen. Popping an error nobody asked for, every
        interval, for as long as the Wi-Fi is down, would be worse."""
        emitted = []
        monkeypatch.setattr(
            server_module.socketio, "emit", lambda *args, **kwargs: emitted.append(args)
        )
        monkeypatch.setattr(server_module, "fetch_current_weather", lambda *a, **k: None)

        server_module.refresh_weather_now(announce_errors=False)

        assert "weather_error" not in [event for event, *_ in emitted]

    def test_a_user_requested_failure_is_announced(self, weather, monkeypatch):
        emitted = []
        monkeypatch.setattr(
            server_module.socketio, "emit", lambda *args, **kwargs: emitted.append(args)
        )
        monkeypatch.setattr(server_module, "fetch_current_weather", lambda *a, **k: None)

        server_module.refresh_weather_now()

        assert "weather_error" in [event for event, *_ in emitted]

    def _run_loop(self, now_value, seconds=0.1):
        """Drive the loop with an injected tick and clock.

        Never patches the global `time` module: threading itself waits on it,
        so patching it hangs the very thread under test. Daemon plus a finally
        so a failing assertion cannot strand a live thread and wedge the run.
        """
        stop = threading.Event()
        thread = threading.Thread(
            target=server_module._auto_refresh_loop,
            args=(stop, 0.01, lambda: now_value),
            daemon=True,
        )
        thread.start()
        try:
            time.sleep(seconds)
        finally:
            stop.set()
            thread.join(timeout=2)
        assert not thread.is_alive(), "the loop should stop promptly when asked"

    def test_the_tick_refreshes_when_due(self, weather, monkeypatch):
        calls = []
        monkeypatch.setattr(server_module, "refresh_weather_now", lambda **k: calls.append(k))

        self._run_loop(1000.0 + 31 * 60)

        assert calls, "a due refresh should have fired"
        assert calls[0] == {"announce_errors": False}

    def test_the_tick_does_nothing_when_not_due(self, weather, monkeypatch):
        calls = []
        monkeypatch.setattr(server_module, "refresh_weather_now", lambda **k: calls.append(k))

        self._run_loop(1000.0 + 60)

        assert calls == []

    def test_the_loop_survives_a_refresh_that_throws(self, weather, monkeypatch):
        """This thread outlives every individual failure -- one exception must
        not silently end scheduled refreshes for the rest of the session."""
        calls = []

        def boom(**kwargs):
            calls.append(kwargs)
            raise RuntimeError("nobody predicted this")

        monkeypatch.setattr(server_module, "refresh_weather_now", boom)

        self._run_loop(1000.0 + 31 * 60, seconds=0.15)

        assert len(calls) > 1, "the loop should have kept ticking after the failure"

    def test_the_interval_is_only_accepted_from_the_offered_set(self, weather):
        """An unbounded value here becomes a poll rate against someone else's
        API, so a malformed client cannot set one."""
        server_module.handle_set_weather_settings({"auto_refresh_minutes": 1})

        assert weather.provider.config.auto_refresh_minutes == 30

    @pytest.mark.parametrize("minutes", [0, 15, 30, 60])
    def test_every_offered_interval_is_accepted(self, weather, minutes):
        server_module.handle_set_weather_settings({"auto_refresh_minutes": minutes})

        assert weather.provider.config.auto_refresh_minutes == minutes
