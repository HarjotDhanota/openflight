"""Interprocess ownership tests for the IWR6843 serial device."""

from __future__ import annotations

import subprocess
import sys

import pytest

from openflight.iwr6843.device_lock import (
    IWR6843DeviceBusyError,
    IWR6843DeviceLock,
)


def test_second_owner_gets_clear_busy_error(tmp_path):
    first = IWR6843DeviceLock("/dev/test-iwr", lock_root=tmp_path)
    second = IWR6843DeviceLock("/dev/test-iwr", lock_root=tmp_path)

    first.acquire()
    try:
        with pytest.raises(IWR6843DeviceBusyError, match=r"/dev/test-iwr.*another process.*pid="):
            second.acquire()
    finally:
        first.release()

    second.acquire()
    second.release()


def test_lock_contends_with_another_process(tmp_path):
    code = """
import sys
from pathlib import Path
from openflight.iwr6843.device_lock import IWR6843DeviceLock
lock = IWR6843DeviceLock('/dev/cross-process-iwr', lock_root=Path(sys.argv[1]))
lock.acquire()
print('ready', flush=True)
sys.stdin.readline()
lock.release()
"""
    child = subprocess.Popen(
        [sys.executable, "-c", code, str(tmp_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "ready"
        contender = IWR6843DeviceLock("/dev/cross-process-iwr", lock_root=tmp_path)
        with pytest.raises(IWR6843DeviceBusyError):
            contender.acquire()
    finally:
        if child.stdin is not None:
            child.stdin.write("stop\n")
            child.stdin.flush()
        child.wait(timeout=5)

    assert child.returncode == 0, child.stderr.read() if child.stderr else ""
