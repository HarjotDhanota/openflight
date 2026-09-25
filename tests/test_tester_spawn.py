"""Accepting an analysis means its process exists; failures to start are reported, not hidden."""

from __future__ import annotations

import threading
import time

import pytest

from openflight.camera import tester_server as ts
from tests.session_fixtures import TESTER, capture_tree


class _Process:
    def __init__(self):
        self.pid = 4242
        self.stdout = None
        self.stopped = threading.Event()

    def terminate(self):
        self.stopped.set()

    def kill(self):
        self.stopped.set()

    def wait(self):
        self.stopped.wait(10)
        return -15


def _raising(*_args, **_kwargs):
    raise FileNotFoundError("python: not found")


def _start(manager, tmp_path):
    manager.start("analyze", [["worker"]], tmp_path / "analysis.log", output_to_log=True)


def _settled(manager, deadline_s=5.0):
    deadline = time.monotonic() + deadline_s
    while manager.status()["state"] == "running" and time.monotonic() < deadline:
        time.sleep(0.01)
    return manager.status()


def test_a_process_that_cannot_be_created_is_an_error_not_an_acceptance(tmp_path):
    manager = ts.TesterJobManager(popen=_raising)
    with pytest.raises(ts.SpawnError, match="could not start: python: not found"):
        _start(manager, tmp_path)
    assert _settled(manager)["state"] == "error"
    assert "python: not found" in (tmp_path / "analysis.log").read_text(encoding="utf-8")


def test_a_spawn_that_does_not_finish_in_time_is_refused_and_stopped_when_it_appears(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(ts, "SPAWN_WAIT_S", 0.2)
    release = threading.Event()
    process = _Process()

    def slow(*_args, **_kwargs):
        release.wait(10)
        return process

    manager = ts.TesterJobManager(popen=slow)
    with pytest.raises(ts.SpawnError, match="did not start within 0.2 s"):
        _start(manager, tmp_path)
    release.set()
    assert process.stopped.wait(5), "a late process must be stopped, not left running"
    assert _settled(manager)["state"] == "stopped"


def test_success_is_reported_only_once_the_process_is_held(tmp_path):
    process = _Process()
    manager = ts.TesterJobManager(popen=lambda *_args, **_kwargs: process)
    try:
        _start(manager, tmp_path)
        assert manager._process is process  # pylint: disable=protected-access
    finally:
        manager.cancel()
        process.stopped.set()


@pytest.mark.parametrize("failure", ["raise", "timeout"])
def test_the_service_answers_503_when_the_worker_does_not_start(tmp_path, monkeypatch, failure):
    root = capture_tree(tmp_path / "sessions")
    release = threading.Event()
    if failure == "timeout":
        monkeypatch.setattr(ts, "SPAWN_WAIT_S", 0.2)
        process = _Process()

        def popen(*_args, **_kwargs):
            release.wait(10)
            return process
    else:
        popen = _raising
    manager = ts.TesterJobManager(popen=popen)
    client = ts.create_app(
        sessions_root=root, rig_geometry=ts.DEFAULT_RIG_GEOMETRY, manager=manager
    ).test_client()
    try:
        response = client.post("/api/tester/analysis", json={"tester_id": TESTER})
        assert response.status_code == 503
        assert "analyze process" in response.get_json()["error"]
    finally:
        release.set()
        _settled(manager)
