"""Runs inside a ball-search worker process: shows the test whether that process lives."""

from __future__ import annotations

import os
from multiprocessing.connection import Client

_HELD = []


def hold_connection(address) -> int:
    """Connect back to the test and keep the connection open for the worker's life."""
    _HELD.append(Client(address))
    return os.getpid()
