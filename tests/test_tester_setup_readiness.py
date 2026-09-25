"""Runtime-side mandatory tester setup checks."""

from types import SimpleNamespace

from openflight import server
from openflight.camera import capture_runtime
from openflight.rig_geometry import geometry_fingerprint
from openflight.session_logger import SessionLogger


def _ready(monkeypatch, *, camera_age=0.1, pitch_deg=0.0, x_g=0.0, z_g=1.0):
    rig_hash = "a" * 64
    config_hash = geometry_fingerprint(
        {
            "rig_geometry_sha256": rig_hash,
            "inclinometer": {
                "i2c_bus": 1,
                "i2c_address": "0x18",
                "zero_offset_deg": 0.0,
            },
        }
    )
    monkeypatch.setattr(server, "study_mode_enabled", True)
    monkeypatch.setattr(server, "tester_config_hash", config_hash)
    monkeypatch.setattr(server, "rig_geometry_config", {"snapshot": {"sha256": rig_hash}})
    alive = SimpleNamespace(is_alive=lambda: True)
    monkeypatch.setattr(
        server,
        "monitor",
        SimpleNamespace(
            _running=True,
            _capture_thread=alive,
            radar=SimpleNamespace(serial=SimpleNamespace(is_open=True)),
        ),
    )
    monkeypatch.setattr(server, "mock_mode", False)
    monkeypatch.setattr(
        server,
        "camera_capture_runtime",
        SimpleNamespace(
            settings=SimpleNamespace(auto_exposure=False, width=320, height=200),
            _startup_capture_mode={
                "camera_properties": {"Model": "ov9281"},
                "resolved_config": {"raw": {"size": [320, 200]}},
            },
            status=lambda: {
                "running": True,
                "armed": True,
                "latest_frame_age_s": camera_age,
            },
        ),
    )
    monkeypatch.setattr(
        server,
        "iwr6843_runtime",
        SimpleNamespace(
            capture_monitor=SimpleNamespace(
                _running=True,
                _armed=True,
                _worker=alive,
                radar=SimpleNamespace(ser=SimpleNamespace(is_open=True)),
            )
        ),
    )
    monkeypatch.setattr(server, "iwr6843_runtime_config", {"enabled": True})
    monkeypatch.setattr(
        server,
        "inclinometer_service",
        SimpleNamespace(
            snapshot_for_impact=lambda _timestamp: SimpleNamespace(
                status="stable",
                snapshot=SimpleNamespace(
                    calibrated_pitch_deg=pitch_deg,
                    x_g=x_g,
                    y_g=0.0,
                    z_g=z_g,
                ),
            )
        ),
    )
    monkeypatch.setattr(
        server,
        "inclinometer_runtime_config",
        {
            "enabled": True,
            "i2c_bus": 1,
            "i2c_address": "0x18",
            "zero_offset_deg": 0.0,
        },
    )
    monkeypatch.setattr(
        server,
        "get_session_logger",
        lambda: SimpleNamespace(log_dir=server.Path("logs"), active_session_uuid="session-uuid"),
    )
    return config_hash


def test_study_readiness_reports_required_runtime_checks(monkeypatch):
    config_hash = _ready(monkeypatch)

    response = server.app.test_client().get("/api/camera/study/readiness")

    payload = response.get_json()
    assert response.status_code == 200
    assert payload["ready"] is True
    assert payload["config_hash"] == config_hash
    assert {check["id"] for check in payload["checks"]} == {
        "geometry",
        "ops",
        "camera",
        "iwr6843",
        "lis3dh",
        "logging",
    }


def test_stale_camera_blocks_readiness(monkeypatch):
    _ready(monkeypatch, camera_age=2.1)

    payload = server.app.test_client().get("/api/camera/study/readiness").get_json()

    assert payload["ready"] is False
    assert [blocker["id"] for blocker in payload["blockers"]] == ["camera"]


def test_large_stable_tilt_warns_without_blocking_runtime(monkeypatch):
    _ready(monkeypatch, pitch_deg=5.0, x_g=-0.1)
    payload = server.app.test_client().get("/api/camera/study/readiness").get_json()
    lis = next(check for check in payload["checks"] if check["id"] == "lis3dh")
    assert payload["ready"] is True
    assert lis["status"] == "warn"
    assert payload["warnings"][0]["id"] == "lis3dh"
    guard = payload["observations"]["lis3dh"]["placement_guard"]
    assert guard["within_threshold"] is False
    assert guard["accuracy_qualified"] is False


def test_camera_runtime_gets_trigger_provider_only_when_tester_gate_required(monkeypatch, tmp_path):
    providers = []
    # init_camera_capture assigns these module globals directly. Register their
    # original values with monkeypatch so this test cannot leak its fake runtime
    # into server tests collected later in the same process.
    for name in (
        "camera_capture_runtime",
        "camera_capture_config",
        "camera_replay_manager",
        "camera_reference_ball_tracker",
        "camera_ball_flight_reference_tracker",
    ):
        monkeypatch.setattr(server, name, getattr(server, name))

    class Runtime:
        def __init__(self, *, settings, trigger_evidence_provider=None, **_kwargs):
            self.settings = settings
            providers.append(trigger_evidence_provider)

        def start(self):
            return None

    monkeypatch.setattr(capture_runtime, "CameraCaptureRuntime", Runtime)

    def initialize(required):
        monkeypatch.setattr(server, "tester_setup_required", required)
        assert server.init_camera_capture(
            output_dir=tmp_path / str(required),
            gpio_pin=17,
            width=320,
            height=200,
            fps=450,
            pre_ms=150,
            post_ms=50,
            exposure_us=300,
            gain=4.0,
            stream="raw",
            rotate_180=False,
            mirror_horizontal=False,
            roll_correction_deg=0.0,
            scaler_crop=None,
            mount_height_m=0.095,
            lateral_offset_m=0.0,
            horizontal_offset_deg=0.0,
            use_gpio_trigger=False,
            auto_exposure=False,
        )

    initialize(False)
    initialize(True)

    assert providers[0] is None
    assert callable(providers[1])


def _trigger_evidence(tmp_path, **overrides):
    evidence = {
        "schema_version": 1,
        "required": True,
        "ready": True,
        "config_hash": "expected",
        "observations": {
            "runtime": {
                "run_dir": str(tmp_path.resolve()),
                "session_uuid": "active-session",
            }
        },
    }
    evidence.update(overrides)
    return evidence


def _active_logger(monkeypatch, tmp_path, session_uuid="active-session"):
    monkeypatch.setattr(server, "tester_config_hash", "expected")
    monkeypatch.setattr(
        server,
        "get_session_logger",
        lambda: SimpleNamespace(log_dir=tmp_path, active_session_uuid=session_uuid),
    )


def test_trigger_evidence_requires_explicit_required_flag(monkeypatch, tmp_path):
    _active_logger(monkeypatch, tmp_path)
    problem = server._tester_trigger_evidence_problem(_trigger_evidence(tmp_path, required=None))
    assert "does not mark" in problem


def test_trigger_evidence_requires_active_setup_hash(monkeypatch, tmp_path):
    _active_logger(monkeypatch, tmp_path)
    problem = server._tester_trigger_evidence_problem(
        _trigger_evidence(tmp_path, config_hash="old")
    )
    assert "setup hash" in problem


def test_trigger_evidence_cannot_cross_logger_session_restart(monkeypatch, tmp_path):
    _active_logger(monkeypatch, tmp_path, session_uuid="new-session")
    problem = server._tester_trigger_evidence_problem(_trigger_evidence(tmp_path))
    assert "different or inactive logging session" in problem


def test_readiness_uses_real_session_logger_uuid_property(monkeypatch, tmp_path):
    _ready(monkeypatch)
    logger = SessionLogger(
        tmp_path,
        provenance_collector=lambda _path, _uuid: {"status": "test"},
    )
    logger.start_session()
    monkeypatch.setattr(server, "get_session_logger", lambda: logger)
    try:
        payload = server._tester_setup_readiness()
    finally:
        logger.end_session()

    assert payload["ready"] is True
    assert payload["observations"]["runtime"]["session_uuid"]
