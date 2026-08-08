"""The only module that can power off the machine.

Isolated to exactly one function so tests can stub it without touching the
service, and so no test run can halt a development machine. The existing
"shutdown" path in server.py is os._exit(0), which stops the server process --
halting the machine is a different capability and does not inherit from it.
"""

from __future__ import annotations

import logging
import subprocess

logger = logging.getLogger(__name__)


def halt() -> bool:
    """Power the machine off. Returns False on failure; never raises.

    A failed halt is a visible degraded state, not something to retry: looping
    on a permissions error would spam the log and never succeed.
    """
    try:
        subprocess.run(["systemctl", "poweroff"], check=True, timeout=10)
        return True
    except Exception as error:  # pylint: disable=broad-exception-caught
        logger.error(
            "[POWER] Automatic shutdown failed (%s). The pack will continue to "
            "discharge; shut down manually.",
            error,
        )
        return False
