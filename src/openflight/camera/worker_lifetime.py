"""Worker processes that end with the process that started them.

A ``spawn`` worker waits on its task queue and outlives a parent that was killed
rather than shut down: Stop on Windows, SIGKILL on the Pi, or a crash. Every test
run that started a tester server left its two ball-search workers behind
(29 Sept). This module is imported by the worker, so it stays small.
"""

from __future__ import annotations

import multiprocessing
import os
import threading
from multiprocessing.connection import wait


def exit_with_parent() -> None:
    """Pool initializer: end this worker as soon as its parent process ends."""
    parent = multiprocessing.parent_process()
    if parent is None:
        return

    def watch() -> None:
        wait([parent.sentinel])
        os._exit(0)  # the parent is gone: nothing is left to hand results to

    threading.Thread(target=watch, daemon=True, name="exit-with-parent").start()
