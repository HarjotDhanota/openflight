"""Worker scheduling preserves capture priority, inherited policies and cleanup."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from openflight.camera import worker_scheduling as scheduling


@pytest.fixture
def linux_threads(monkeypatch):
    priorities = {101: 0, 102: 0, 103: 15}
    writes = []
    cleanup = []
    monkeypatch.setattr(scheduling, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(scheduling.os, "getpid", lambda: 101)
    monkeypatch.setattr(scheduling.os, "PRIO_PROCESS", 0, raising=False)
    monkeypatch.setattr(
        scheduling.os, "getpriority", lambda _kind, tid: priorities[tid], raising=False
    )

    def set_priority(_kind, tid, value):
        priorities[tid] = value
        writes.append((tid, value))

    monkeypatch.setattr(scheduling.os, "setpriority", set_priority, raising=False)
    monkeypatch.setattr(Path, "iterdir", lambda _path: [Path(str(tid)) for tid in priorities])
    monkeypatch.setattr(scheduling, "exit_with_parent", lambda: cleanup.append(True))
    return priorities, writes, cleanup


def test_lowers_main_and_existing_native_threads_without_raising_priority(linux_threads):
    priorities, writes, cleanup = linux_threads
    scheduling.initialize_analysis_worker()
    assert priorities == {101: 10, 102: 10, 103: 15}
    assert writes == [(101, 10), (102, 10)]
    assert cleanup == [True]
    scheduling.initialize_analysis_worker()
    assert writes == [(101, 10), (102, 10)]


@pytest.mark.parametrize("error", [ProcessLookupError(), PermissionError("denied")])
def test_thread_exit_or_permission_failure_preserves_other_threads_and_cleanup(
    linux_threads, monkeypatch, caplog, error
):
    priorities, writes, cleanup = linux_threads
    original_getpriority = scheduling.os.getpriority

    def get_priority(kind, tid):
        if tid == 102:
            raise error
        return original_getpriority(kind, tid)

    monkeypatch.setattr(scheduling.os, "getpriority", get_priority)
    scheduling.initialize_analysis_worker()
    assert priorities == {101: 10, 102: 0, 103: 15}
    assert writes == [(101, 10)]
    assert cleanup == [True]
    assert bool(caplog.records) == isinstance(error, PermissionError)


def test_enumeration_failure_still_lowers_main_and_retains_cleanup(
    linux_threads, monkeypatch, caplog
):
    priorities, writes, cleanup = linux_threads

    def unavailable(_path):
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "iterdir", unavailable)
    scheduling.initialize_analysis_worker()
    assert priorities[101] == 10
    assert writes == [(101, 10)]
    assert cleanup == [True]
    assert "Cannot enumerate" in caplog.text


def test_setpriority_failure_is_reported_and_cleanup_still_runs(linux_threads, monkeypatch, caplog):
    priorities, writes, cleanup = linux_threads

    def denied(_kind, _tid, _value):
        raise PermissionError("denied")

    monkeypatch.setattr(scheduling.os, "setpriority", denied)
    scheduling.initialize_analysis_worker()
    assert priorities == {101: 0, 102: 0, 103: 15}
    assert not writes
    assert cleanup == [True]
    assert len(caplog.records) == 2


def test_other_platform_retains_cleanup_without_changing_priority(linux_threads, monkeypatch):
    priorities, writes, cleanup = linux_threads
    monkeypatch.setattr(scheduling, "sys", SimpleNamespace(platform="win32"))
    scheduling.initialize_analysis_worker()
    assert priorities == {101: 0, 102: 0, 103: 15}
    assert not writes
    assert cleanup == [True]
