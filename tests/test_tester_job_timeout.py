"""Idle diagnostic sessions outlive reassembly; finite hardware jobs still expire."""

import threading

import pytest

from openflight.camera import tester_server as ts


@pytest.fixture
def virtual_job(monkeypatch):
    timers = []

    class Timer:
        def __init__(self, seconds, callback):
            self.seconds, self.callback = seconds, callback
            self.started = self.cancelled = False
            timers.append(self)

        def start(self):
            self.started = True

        def cancel(self):
            self.cancelled = True

    class Process:
        stdout = None

        def __init__(self):
            self.stopped = threading.Event()

        def wait(self):
            assert self.stopped.wait(5)
            return -15

        def terminate(self):
            self.stopped.set()

    monkeypatch.setattr(ts.threading, "Timer", Timer)
    monkeypatch.setattr(
        ts.TesterJobManager, "_request_process_stop", staticmethod(lambda p: p.terminate())
    )
    process = Process()
    manager = ts.TesterJobManager(popen=lambda *_args, **_kwargs: process)

    def advance(seconds):
        for timer in timers:
            if timer.started and not timer.cancelled and timer.seconds <= seconds:
                timer.callback()

    yield manager, process, timers, advance
    manager.cancel()
    process.stopped.set()
    if manager._thread is not None:
        manager._thread.join(2)


def test_internal_sync_waits_beyond_one_hour_until_manual_stop(tmp_path, virtual_job):
    manager, process, timers, advance = virtual_job
    manager.start("internal_sync", [["diagnostic"]], tmp_path / "session.log", output_to_log=True)
    advance(24 * 3600)
    assert not process.stopped.is_set()
    assert manager.status()["state"] == "running"
    assert manager.cancel_requested is False
    assert timers == []
    assert manager.status()["timeout_s"] is None
    assert manager.cancel() is True
    manager._thread.join(2)
    assert process.stopped.is_set()
    assert manager.status()["state"] == "stopped"


@pytest.mark.parametrize(
    "action,seconds", [("preflight", 120.0), ("gain", 900.0), ("tee_range", 120.0)]
)
def test_other_hardware_job_deadlines_remain_enforced(tmp_path, virtual_job, action, seconds):
    manager, process, timers, advance = virtual_job
    manager.start(action, [["hardware-step"]], tmp_path / "job.log", output_to_log=True)
    assert manager.status()["timeout_s"] == seconds
    assert len(timers) == 1 and timers[0].seconds == seconds
    advance(seconds - 0.01)
    assert not process.stopped.is_set()
    advance(seconds)
    manager._thread.join(2)
    assert process.stopped.is_set()
    assert manager.cancel_requested is True
    assert manager.status()["state"] == "stopped"


def test_idle_job_has_no_lifetime_deadline(virtual_job):
    manager, _process, _timers, _advance = virtual_job
    assert manager.status()["state"] == "idle"
    assert manager.status()["timeout_s"] is None
