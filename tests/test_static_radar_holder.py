"""The tester keeps one radar session alive across a setup's two captures."""

from __future__ import annotations

import io
import json
import queue
import threading
import time

import pytest

from openflight.camera.static_radar_holder import HeldStaticRadar


class FakeSessionProcess:
    """Stands in for static_range_session.py: answers requests on its stdout."""

    def __init__(self, command, *, usable=True, answer=True):
        self.command = command
        self.pid = 4242
        self.usable = usable
        self.answer = answer
        self.requests: list[dict] = []
        self._out: queue.Queue = queue.Queue()
        self.returncode = None
        self.terminated = False
        self.stdin = self._Stdin(self)
        self.stdout = iter(self._out.get, None)
        self._out.put(json.dumps({"event": "ready", "pid": self.pid}) + "\n")

    class _Stdin(io.StringIO):
        def __init__(self, owner):
            super().__init__()
            self.owner = owner

        def write(self, text):
            for line in text.splitlines():
                self.owner.handle(json.loads(line))
            return len(text)

        def flush(self):
            pass

        def close(self):
            self.owner.exit("stdin_closed")

    def handle(self, request):
        self.requests.append(request)
        if request["op"] == "close":
            self.exit("requested")
            return
        if not self.answer:
            return
        self._out.put(
            json.dumps(
                {
                    "event": "done",
                    "capture_id": request["capture_id"],
                    "usable": self.usable,
                    "radar_open": self.usable and not request.get("close_after"),
                }
            )
            + "\n"
        )
        if request.get("close_after"):
            self.exit("close_after")

    def exit(self, reason, code=0):
        if self.returncode is None:
            self._out.put(json.dumps({"event": "closed", "reason": reason}) + "\n")
            self.returncode = code
            self._out.put(None)

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        deadline = time.monotonic() + (timeout or 5.0)
        while self.returncode is None and time.monotonic() < deadline:
            time.sleep(0.01)
        if self.returncode is None:
            raise TimeoutError
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.exit("cancelled", code=-15)

    def kill(self):
        self.exit("killed", code=-9)


def _capture_command(tmp_path, capture_id, kind, *, port="/dev/iwr", config="radar.cfg"):
    return [
        "python",
        "/repo/scripts/iwr6843/capture_static_range.py",
        "--capture-id",
        capture_id,
        "--kind",
        kind,
        "--output-dir",
        str(tmp_path / "iwr"),
        "--config",
        config,
        "--firmware",
        "fw.bin",
        "--rig-geometry",
        "rig.json",
        "--calibration",
        "cal.json",
        "--port",
        port,
    ]


class Spawner:
    def __init__(self, **process_kwargs):
        self.processes: list[FakeSessionProcess] = []
        self.kwargs = process_kwargs

    def __call__(self, command, **_popen_kwargs):
        process = FakeSessionProcess(command, **self.kwargs)
        self.processes.append(process)
        return process


def _finished():
    done = threading.Event()
    calls = []

    def on_finish(action, code):
        calls.append((action, code))
        done.set()

    return calls, done, on_finish


def _holder(spawner, **kwargs):
    return HeldStaticRadar(
        popen=spawner, session_script="/repo/scripts/iwr6843/static_range_session.py", **kwargs
    )


def test_the_empty_and_ball_captures_use_one_session(tmp_path):
    spawner = Spawner()
    holder = _holder(spawner)
    calls, done, on_finish = _finished()

    holder.start(
        "tee_range", [_capture_command(tmp_path, "empty-1", "empty")], tmp_path / "e.log", on_finish
    )
    assert done.wait(2)
    done.clear()
    assert holder.holding
    holder.start(
        "tee_range",
        [_capture_command(tmp_path, "ball-2", "ball_present")],
        tmp_path / "b.log",
        on_finish,
    )
    assert done.wait(2)

    assert len(spawner.processes) == 1
    session = spawner.processes[0]
    assert session.command[1].endswith("static_range_session.py")
    assert "--capture-id" not in session.command
    assert session.command[session.command.index("--port") + 1] == "/dev/iwr"
    assert [request["kind"] for request in session.requests] == ["empty", "ball_present"]
    assert session.requests[0]["close_after"] is False
    assert session.requests[1]["close_after"] is True
    assert session.requests[0]["log_path"] == str(tmp_path / "e.log")
    assert calls == [("tee_range", 0), ("tee_range", 0)]


def test_status_reads_running_only_while_a_capture_is_in_flight(tmp_path):
    spawner = Spawner(answer=False)
    holder = _holder(spawner)
    calls, _done, on_finish = _finished()

    holder.start(
        "tee_range", [_capture_command(tmp_path, "empty-1", "empty")], tmp_path / "e.log", on_finish
    )

    assert holder.status()["state"] == "running"
    assert holder.status()["action"] == "tee_range"
    with pytest.raises(RuntimeError, match="already running"):
        holder.start(
            "tee_range",
            [_capture_command(tmp_path, "empty-2", "empty")],
            tmp_path / "e2.log",
            on_finish,
        )
    assert calls == []


def test_an_unusable_capture_reports_a_failure_code(tmp_path):
    holder = _holder(Spawner(usable=False))
    calls, done, on_finish = _finished()

    holder.start(
        "tee_range", [_capture_command(tmp_path, "empty-1", "empty")], tmp_path / "e.log", on_finish
    )

    assert done.wait(2)
    assert calls == [("tee_range", 1)]
    assert holder.status()["state"] == "idle"


def test_a_session_that_dies_mid_capture_still_finishes_the_capture(tmp_path):
    spawner = Spawner(answer=False)
    holder = _holder(spawner)
    calls, done, on_finish = _finished()

    holder.start(
        "tee_range", [_capture_command(tmp_path, "empty-1", "empty")], tmp_path / "e.log", on_finish
    )
    spawner.processes[0].exit("crashed", code=1)

    assert done.wait(2)
    assert calls == [("tee_range", 1)]
    assert not holder.holding


def test_cancel_stops_a_capture_in_flight(tmp_path):
    spawner = Spawner(answer=False)
    holder = _holder(spawner)
    calls, done, on_finish = _finished()
    holder.start(
        "tee_range", [_capture_command(tmp_path, "empty-1", "empty")], tmp_path / "e.log", on_finish
    )

    assert holder.cancel() is True
    assert done.wait(2)
    assert spawner.processes[0].terminated
    assert calls[0][1] != 0
    assert holder.cancel() is False


def test_a_capture_that_overruns_its_timeout_is_stopped(tmp_path):
    spawner = Spawner(answer=False)
    holder = _holder(spawner, timeout_s=0.05)
    calls, done, on_finish = _finished()

    holder.start(
        "tee_range", [_capture_command(tmp_path, "empty-1", "empty")], tmp_path / "e.log", on_finish
    )

    assert done.wait(2)
    assert spawner.processes[0].terminated
    assert calls[0][1] != 0


def test_release_closes_an_idle_session_for_other_hardware(tmp_path):
    spawner = Spawner()
    holder = _holder(spawner)
    _calls, done, on_finish = _finished()
    holder.start(
        "tee_range", [_capture_command(tmp_path, "empty-1", "empty")], tmp_path / "e.log", on_finish
    )
    assert done.wait(2)

    holder.release()

    assert not holder.holding
    assert spawner.processes[0].requests[-1] == {"op": "close"}


def test_release_refuses_while_a_capture_is_in_flight(tmp_path):
    holder = _holder(Spawner(answer=False))
    _calls, _done, on_finish = _finished()
    holder.start(
        "tee_range", [_capture_command(tmp_path, "empty-1", "empty")], tmp_path / "e.log", on_finish
    )

    with pytest.raises(RuntimeError, match="owns the hardware"):
        holder.release()


def test_changed_inputs_start_a_fresh_session(tmp_path):
    spawner = Spawner()
    holder = _holder(spawner)
    _calls, done, on_finish = _finished()
    holder.start(
        "tee_range", [_capture_command(tmp_path, "empty-1", "empty")], tmp_path / "e.log", on_finish
    )
    assert done.wait(2)
    done.clear()

    holder.start(
        "tee_range",
        [_capture_command(tmp_path, "empty-2", "empty", config="other.cfg")],
        tmp_path / "e2.log",
        on_finish,
    )
    assert done.wait(2)

    assert len(spawner.processes) == 2
    assert spawner.processes[0].requests[-1] == {"op": "close"}


def test_a_session_that_ended_is_replaced(tmp_path):
    spawner = Spawner()
    holder = _holder(spawner)
    _calls, done, on_finish = _finished()
    holder.start(
        "tee_range",
        [_capture_command(tmp_path, "b-1", "ball_present")],
        tmp_path / "b.log",
        on_finish,
    )
    assert done.wait(2)
    spawner.processes[0].wait(2)
    done.clear()

    holder.start(
        "tee_range", [_capture_command(tmp_path, "empty-2", "empty")], tmp_path / "e.log", on_finish
    )

    assert done.wait(2)
    assert len(spawner.processes) == 2


def test_only_the_setup_capture_action_is_accepted(tmp_path):
    holder = _holder(Spawner())

    with pytest.raises(ValueError, match="tee_range"):
        holder.start("swings", [["python", "x.py"]], tmp_path / "s.log")
