"""One radar connection held across the empty and ball setup captures."""

from __future__ import annotations

import json
import queue
import threading

from openflight.iwr6843 import static_session
from openflight.iwr6843.static_session import SessionInputs, serve


class HeldRadar:
    def __init__(self):
        self.closed = 0


def _inputs(tmp_path) -> SessionInputs:
    return SessionInputs(
        config_path=tmp_path / "radar.cfg",
        firmware_path=tmp_path / "firmware.bin",
        rig_geometry_path=tmp_path / "rig.json",
        calibration_path=tmp_path / "calibration.json",
        port="/dev/test-iwr",
        settle_s=1.0,
    )


def _request(tmp_path, capture_id, kind, **extra):
    return json.dumps(
        {
            "op": "capture",
            "capture_id": capture_id,
            "kind": kind,
            "output_dir": str(tmp_path / "iwr"),
            "log_path": str(tmp_path / "iwr" / f"{capture_id}.log"),
            **extra,
        }
    )


def _run(tmp_path, lines, *, usable=True, idle_timeout_s=5.0, cancel=None):
    requests: queue.Queue = queue.Queue()
    for line in lines:
        requests.put(line)
    events = []
    opened = []
    captures = []

    def factory(**kwargs):
        radar = HeldRadar()
        opened.append((radar, kwargs))
        return radar

    def capture(inputs, *, radar_factory, cancel_event, close_radar):
        radar = radar_factory(port=inputs.port)
        captures.append((inputs, radar, close_radar))
        inputs.output_dir.mkdir(parents=True, exist_ok=True)
        result = {
            "capture_id": inputs.capture_id,
            "status": "usable" if usable else "error",
            "usable": usable,
            "radar_left_open": usable and not close_radar,
        }
        (inputs.output_dir / f"{inputs.capture_id}.json").write_text(json.dumps(result))
        return result

    def close(radar):
        radar.closed += 1

    code = serve(
        requests,
        events.append,
        _inputs(tmp_path),
        radar_factory=factory,
        capture=capture,
        close=close,
        idle_timeout_s=idle_timeout_s,
        cancel_event=cancel or threading.Event(),
    )
    return code, events, opened, captures


def test_both_setup_captures_share_one_radar_connection(tmp_path):
    code, events, opened, captures = _run(
        tmp_path,
        [
            _request(tmp_path, "empty-000001", "empty"),
            _request(tmp_path, "ball_present-000002", "ball_present", close_after=True),
        ],
    )

    assert code == 0
    assert len(opened) == 1
    assert opened[0][1] == {"port": "/dev/test-iwr"}
    assert [inputs.capture_kind for inputs, _radar, _close in captures] == [
        "empty",
        "ball_present",
    ]
    assert captures[0][1] is captures[1][1]
    assert all(close_radar is False for _inputs, _radar, close_radar in captures)
    assert opened[0][0].closed == 1
    done = [event for event in events if event["event"] == "done"]
    assert [event["capture_id"] for event in done] == ["empty-000001", "ball_present-000002"]
    assert done[0]["radar_open"] is True
    assert events[-1]["event"] == "closed"
    assert events[-1]["reason"] == "close_after"


def test_done_is_reported_before_the_slow_close(tmp_path):
    _code, events, _opened, _captures = _run(
        tmp_path, [_request(tmp_path, "ball_present-000002", "ball_present", close_after=True)]
    )

    kinds = [event["event"] for event in events]
    assert kinds.index("done") < kinds.index("closed")


def test_the_capture_log_gets_the_result(tmp_path):
    _run(tmp_path, [_request(tmp_path, "empty-000001", "empty"), None])

    logged = json.loads((tmp_path / "iwr" / "empty-000001.log").read_text())
    assert logged["capture_id"] == "empty-000001"


def test_an_unusable_capture_drops_the_connection_and_the_next_one_reopens(tmp_path):
    _code, events, opened, _captures = _run(
        tmp_path,
        [_request(tmp_path, "empty-000001", "empty"), None],
        usable=False,
    )

    assert len(opened) == 1
    done = [event for event in events if event["event"] == "done"]
    assert done[0]["radar_open"] is False


def test_stdin_closing_releases_the_radar(tmp_path):
    _code, events, opened, _captures = _run(
        tmp_path, [_request(tmp_path, "empty-000001", "empty"), None]
    )

    assert opened[0][0].closed == 1
    assert events[-1] == {**events[-1], "event": "closed", "reason": "stdin_closed"}


def test_an_idle_session_releases_the_radar(tmp_path):
    _code, events, opened, _captures = _run(
        tmp_path, [_request(tmp_path, "empty-000001", "empty")], idle_timeout_s=0.05
    )

    assert opened[0][0].closed == 1
    assert events[-1]["reason"] == "idle"


def test_a_bad_request_is_reported_and_the_session_continues(tmp_path):
    _code, events, opened, _captures = _run(
        tmp_path,
        ["not json", json.dumps({"op": "dance"}), _request(tmp_path, "e-1", "empty"), None],
    )

    errors = [event for event in events if event["event"] == "error"]
    assert len(errors) == 2
    assert len(opened) == 1


def test_a_refused_capture_is_reported_as_done_without_a_result(tmp_path, monkeypatch):
    def refuse(inputs, **_kwargs):
        raise FileExistsError(f"capture ID {inputs.capture_id!r} is already reserved")

    requests: queue.Queue = queue.Queue()
    requests.put(_request(tmp_path, "empty-000001", "empty"))
    requests.put(None)
    events = []
    serve(
        requests,
        events.append,
        _inputs(tmp_path),
        radar_factory=lambda **_kwargs: HeldRadar(),
        capture=refuse,
        close=lambda radar: None,
        idle_timeout_s=5.0,
        cancel_event=threading.Event(),
    )

    done = [event for event in events if event["event"] == "done"][0]
    assert done["usable"] is False
    assert "already reserved" in done["refused"]


def test_the_session_module_names_its_events():
    assert static_session.EVENTS == ("ready", "done", "error", "closed")
