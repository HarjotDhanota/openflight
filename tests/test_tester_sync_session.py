"""Stopped operator waits do not start RF or expire during enclosure reassembly."""

import importlib
import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def session_tools(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / "scripts/hardware-test"))
    module = importlib.import_module("tester_sync_session")
    clock = SimpleNamespace(now=0.0, tick=lambda: None)

    def sleep(_seconds):
        clock.now += 901
        clock.tick()

    monkeypatch.setattr(
        module,
        "time",
        SimpleNamespace(
            monotonic=lambda: clock.now,
            monotonic_ns=lambda: round(clock.now * 1e9),
            sleep=sleep,
        ),
    )
    return module, clock


def _command(files, command, token=None):
    with (files.folder / "commands.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(
            json.dumps({"command": command, "phase_token": token or files.state["phase_token"]})
            + "\n"
        )


@pytest.mark.parametrize("late_command", ["next", "end"])
@pytest.mark.parametrize("stale_command", ["next", "stop"])
def test_stopped_session_waits_past_900_seconds_for_owned_next_or_end(
    tmp_path, monkeypatch, session_tools, late_command, stale_command
):
    tools, clock = session_tools
    attempts, events = [], []

    @contextmanager
    def stopped_runtime(_package, files, **_kwargs):
        files.publish("loading")
        events.append("fresh stopped runtime")

        def operator():
            assert files.state["phase"] == "next"
            assert len(attempts) == (1 if clock.now > 1802 and late_command == "next" else 0)
            if clock.now == 901:
                _command(files, stale_command, "previous-phase-token")
            else:
                _command(files, late_command if clock.now == 1802 else "end")

        clock.tick = operator
        try:
            yield SimpleNamespace()
        finally:
            events.append("runtime closed")

    def acquire(_collector, files, folder):
        assert clock.now == 1802
        attempts.append(folder.name)
        files.publish("saving")

    monkeypatch.setattr(tools, "open_runtime", stopped_runtime)
    monkeypatch.setattr(tools, "attempt", acquire)
    tools.run(tmp_path / "package", tmp_path / "session")
    status = json.loads((tmp_path / "session/status.json").read_text())
    assert status["phase"] == "stopped"
    assert attempts == (["attempt-001"] if late_command == "next" else [])
    assert events == ["fresh stopped runtime", "runtime closed"]
    assert not list((tmp_path / "session").glob("attempt-*"))


@pytest.mark.parametrize("confirmation", ["switches_set", "reset_pressed"])
def test_physical_confirmation_still_expires_after_900_seconds(
    tmp_path, session_tools, confirmation
):
    tools, clock = session_tools
    files = tools.SessionFiles(tmp_path)
    files.publish("switches" if confirmation == "switches_set" else "reset")
    clock.tick = lambda: _command(files, confirmation)
    with pytest.raises(TimeoutError, match="operator confirmation expired"):
        files.wait(confirmation)
    assert clock.now == 901
    assert files.offset == 0


@pytest.mark.parametrize("error", [RuntimeError("uncertain transfer"), InterruptedError("stop")])
def test_attempt_failure_does_not_enter_another_idle_wait(
    tmp_path, monkeypatch, session_tools, error
):
    tools, clock = session_tools
    attempts = []

    @contextmanager
    def stopped_runtime(_package, files, **_kwargs):
        clock.tick = lambda: _command(files, "next")
        try:
            yield SimpleNamespace()
        except BaseException:
            files.publish("failed")
            raise

    def acquire(_collector, files, folder):
        attempts.append(folder.name)
        files.publish("armed")
        raise error

    monkeypatch.setattr(tools, "open_runtime", stopped_runtime)
    monkeypatch.setattr(tools, "attempt", acquire)
    if isinstance(error, InterruptedError):
        tools.run(tmp_path / "package", tmp_path / "session")
    else:
        with pytest.raises(RuntimeError, match="uncertain transfer"):
            tools.run(tmp_path / "package", tmp_path / "session")
    status = json.loads((tmp_path / "session/status.json").read_text())
    assert status["phase"] == ("stopped" if isinstance(error, InterruptedError) else "failed")
    assert attempts == ["attempt-001"]
    assert clock.now == 901
