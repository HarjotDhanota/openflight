"""Initialize ball-search workers to yield CPU time to capture on Linux."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from .worker_lifetime import exit_with_parent

logger = logging.getLogger(__name__)
ANALYSIS_NICE = 10


def _lower_thread_priority(thread_id: int) -> None:
    # Linux-only os members; the caller guards this on other platforms.
    # pylint: disable=no-member
    try:
        current = os.getpriority(os.PRIO_PROCESS, thread_id)
        if current < ANALYSIS_NICE:
            os.setpriority(os.PRIO_PROCESS, thread_id, ANALYSIS_NICE)
    except ProcessLookupError:
        pass
    except OSError as error:
        logger.warning("Cannot lower analysis thread %s priority: %s", thread_id, error)


def initialize_analysis_worker() -> None:
    """Pool initializer: lower own Linux thread priorities and retain parent cleanup."""
    if sys.platform == "linux":
        # Linux nice is per-thread; numpy may create native threads before this initializer.
        main_thread = os.getpid()
        _lower_thread_priority(main_thread)
        try:
            thread_ids = [int(thread.name) for thread in Path("/proc/self/task").iterdir()]
        except OSError as error:
            logger.warning("Cannot enumerate analysis thread priorities: %s", error)
        else:
            for thread_id in thread_ids:
                if thread_id != main_thread:
                    _lower_thread_priority(thread_id)
    exit_with_parent()
