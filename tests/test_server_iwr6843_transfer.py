"""The kiosk's IWR transfer setting: per-session defaults, a live switch, and the shot logs.

Two-round ("Fast") for normal play, the full dump for tester and research sessions; the
operator switches between shots without a restart, and every capture record says which
mode the shot used. The radar decides whether two-round is available at all.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from openflight import server as server_module
from openflight.clubs import ClubType
from openflight.iwr6843.calibration import Calibration
from openflight.iwr6843.runtime import IWR6843Runtime
from openflight.launch_monitor import Shot


class _Monitor:
    def __init__(self, supported: bool, tmp_path: Path | None = None):
        self.two_round_support = {
            "supported": supported,
            "reason": None if supported else "the radar firmware has no fast-transfer commands",
        }
        self.saved = []
        self.save_dumps = tmp_path is not None
        self.output_dir = tmp_path

    def capture_for_shot(self, _impact, timeout_s=0.0):  # noqa: ARG002
        return None

    def save_sparse_capture(self, sequence, trigger_timestamp, data):
        path = self.output_dir / f"iwr6843_x_{sequence:03d}.l3sparse"
        path.write_bytes(data)
        self.saved.append(path)
        return path


def _runtime(supported: bool, transfer: str = "full", tmp_path=None) -> IWR6843Runtime:
    return IWR6843Runtime(
        capture_monitor=_Monitor(supported, tmp_path),
        calibration=Calibration.identity(),
        net_range_m=4.0,
        transfer=transfer,
    )


@pytest.fixture(name="emitted")
def _emitted(monkeypatch):
    events = []
    monkeypatch.setattr(
        server_module.socketio,
        "emit",
        lambda event, payload=None, **_kw: events.append((event, payload)),
    )
    return events


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        ([], ("two_round", "kiosk")),
        (["--iwr6843-transfer", "full"], ("full", "kiosk")),
        (["--study-mode"], ("full", "research")),
        (["--tester-setup-required"], ("full", "tester")),
        (["--tester-free-play"], ("full", "tester")),
        (["--tester-setup-required", "--iwr6843-transfer", "two_round"], ("two_round", "tester")),
    ],
)
def test_the_session_kind_picks_the_initial_transfer(argv, expected):
    args = server_module.build_parser().parse_args(argv)
    assert server_module.initial_iwr6843_transfer(args) == expected


def test_the_switch_reports_unavailable_with_a_reason(monkeypatch, emitted):
    monkeypatch.setattr(server_module, "iwr6843_runtime", _runtime(False, "two_round"))
    server_module.handle_get_iwr6843_transfer()
    status = emitted[-1][1]
    assert emitted[-1][0] == "iwr6843_transfer"
    assert status["requested"] == "two_round" and status["effective"] == "full"
    assert status["available"] is False and "fast-transfer" in status["reason"]
    assert status["enabled"] is True


def _from(address: str):
    """A request context from ``address``, as a Socket.IO event handler runs in."""
    return server_module.app.test_request_context(environ_base={"REMOTE_ADDR": address})


def test_the_switch_changes_the_next_shots_mode_without_a_restart(monkeypatch, emitted):
    runtime = _runtime(True, "full")
    monkeypatch.setattr(server_module, "iwr6843_runtime", runtime)
    monkeypatch.setattr(server_module, "iwr6843_runtime_config", {"transfer": "full"})
    with _from("127.0.0.1"):
        server_module.handle_set_iwr6843_transfer({"mode": "two_round"})
    assert runtime.transfer_mode.effective() == "two_round"
    assert emitted[-1] == ("iwr6843_transfer", server_module.iwr6843_transfer_status())
    assert server_module.iwr6843_runtime_config["transfer"] == "two_round"
    with _from("::1"):
        server_module.handle_set_iwr6843_transfer({"mode": "half"})
    assert emitted[-1][0] == "iwr6843_transfer_error"
    assert runtime.transfer_mode.effective() == "two_round"


def test_without_a_radar_the_switch_says_so(monkeypatch, emitted):
    monkeypatch.setattr(server_module, "iwr6843_runtime", None)
    server_module.handle_get_iwr6843_transfer()
    status = emitted[-1][1]
    assert status["enabled"] is False and status["effective"] == "full"
    assert "no IWR radar" in status["reason"]


def test_the_http_switch_is_local_only(monkeypatch):
    runtime = _runtime(True, "full")
    monkeypatch.setattr(server_module, "iwr6843_runtime", runtime)
    monkeypatch.setattr(server_module, "iwr6843_runtime_config", {})
    monkeypatch.setattr(server_module.socketio, "emit", lambda *_a, **_k: None)
    client = server_module.app.test_client()
    assert client.get("/api/iwr6843/transfer").get_json()["requested"] == "full"
    refused = client.post(
        "/api/iwr6843/transfer",
        json={"mode": "two_round"},
        environ_base={"REMOTE_ADDR": "192.0.2.7"},
    )
    assert refused.status_code == 403 and runtime.transfer == "full"
    accepted = client.post("/api/iwr6843/transfer", json={"mode": "two_round"})
    assert accepted.status_code == 200 and accepted.get_json()["effective"] == "two_round"
    bad = client.post("/api/iwr6843/transfer", json={"mode": "x"})
    assert bad.status_code == 400


def test_session_start_records_the_transfer_setting(monkeypatch):
    monkeypatch.setattr(server_module, "iwr6843_runtime", _runtime(False, "two_round"))
    config = server_module._session_start_config()
    assert config["iwr6843_transfer"]["requested"] == "two_round"
    assert config["iwr6843_transfer"]["effective"] == "full"


def test_a_two_round_shot_is_saved_and_logged_with_its_mode(monkeypatch, tmp_path):
    runtime = _runtime(True, "two_round", tmp_path)
    capture = SimpleNamespace(
        trigger_timestamp=100.01,
        path=None,
        raw=None,
        dump_duration_s=None,
        error=None,
        valid=True,
        sequence=7,
        gate_event=None,
    )
    result = SimpleNamespace(
        capture=capture,
        measurement=None,
        transfer={"session_id": 3, "cells_per_round": [40]},
        transfer_mode={"requested": "two_round", "used": "two_round", "fallback_reason": None},
        sparse_capture=b"OFL3SPARSE\nexample",
    )
    runtime.process_shot = lambda **_kwargs: result
    logged = []
    session = SimpleNamespace(
        stats={"shots_detected": 2}, log_iwr6843_capture=lambda **kwargs: logged.append(kwargs)
    )
    monkeypatch.setattr(server_module, "iwr6843_runtime", runtime)
    monkeypatch.setattr(server_module, "get_session_logger", lambda: session)
    monkeypatch.setattr(server_module.socketio, "emit", lambda *_args, **_kwargs: None)
    shot = Shot(
        ball_speed_mph=100.0,
        club_speed_mph=80.0,
        timestamp=datetime.now(),
        impact_timestamp=100.0,
        club=ClubType.IRON_9,
    )

    server_module._process_iwr6843_angle(shot)

    saved = runtime.capture_monitor.saved
    assert len(saved) == 1 and saved[0].read_bytes() == result.sparse_capture
    assert logged[0]["capture_path"] == str(saved[0])
    assert logged[0]["capture_bytes"] == len(result.sparse_capture)
    assert logged[0]["transfer_mode"] == result.transfer_mode
    assert logged[0]["transfer"] == result.transfer


def test_the_socket_switch_refuses_another_machine(monkeypatch, emitted):
    runtime = _runtime(True, "full")
    monkeypatch.setattr(server_module, "iwr6843_runtime", runtime)
    monkeypatch.setattr(server_module, "iwr6843_runtime_config", {"transfer": "full"})
    with _from("192.0.2.7"):
        server_module.handle_set_iwr6843_transfer({"mode": "two_round"})
    assert emitted[-1] == (
        "iwr6843_transfer_error",
        {"error": "the IWR transfer is set from the kiosk itself"},
    )
    assert runtime.transfer == "full" and runtime.transfer_mode.effective() == "full"
    assert server_module.iwr6843_runtime_config["transfer"] == "full"


def test_a_local_socket_client_sets_the_mode(monkeypatch):
    runtime = _runtime(True, "full")
    monkeypatch.setattr(server_module, "iwr6843_runtime", None)
    monkeypatch.setattr(server_module, "iwr6843_runtime_config", {"transfer": "full"})
    monkeypatch.setattr(server_module, "get_session_logger", lambda: None)
    # Flask's test client gives the connection the kiosk's own address, 127.0.0.1
    client = server_module.socketio.test_client(
        server_module.app, flask_test_client=server_module.app.test_client()
    )
    try:
        client.get_received()
        monkeypatch.setattr(server_module, "iwr6843_runtime", runtime)
        client.emit("set_iwr6843_transfer", {"mode": "two_round"})
        names = [message["name"] for message in client.get_received()]
    finally:
        client.disconnect()
    assert "iwr6843_transfer" in names and "iwr6843_transfer_error" not in names
    assert runtime.transfer_mode.effective() == "two_round"


def test_a_socket_client_without_an_address_is_refused(monkeypatch):
    runtime = _runtime(True, "full")
    monkeypatch.setattr(server_module, "iwr6843_runtime", None)
    monkeypatch.setattr(server_module, "iwr6843_runtime_config", {"transfer": "full"})
    client = server_module.socketio.test_client(server_module.app)  # no REMOTE_ADDR at all
    try:
        client.get_received()
        monkeypatch.setattr(server_module, "iwr6843_runtime", runtime)
        client.emit("set_iwr6843_transfer", {"mode": "two_round"})
        names = [message["name"] for message in client.get_received()]
    finally:
        client.disconnect()
    assert names == ["iwr6843_transfer_error"] and runtime.transfer == "full"
