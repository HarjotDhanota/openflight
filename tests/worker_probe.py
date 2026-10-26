"""Runs inside a ball-search worker process: shows the test whether that process lives."""

from __future__ import annotations

import os
from multiprocessing.connection import Client
from pathlib import Path

_HELD = []


def hold_connection(address) -> int:
    """Connect back to the test and keep the connection open for the worker's life."""
    _HELD.append(Client(address))
    return os.getpid()


def thread_priorities() -> list[int]:
    """Observe the Linux worker's main and already-created native thread priorities."""
    priorities = []
    for thread in Path("/proc/self/task").iterdir():
        try:
            priorities.append(os.getpriority(os.PRIO_PROCESS, int(thread.name)))
        except ProcessLookupError:
            pass
    return priorities
