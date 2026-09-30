"""The tester page's ready light (P7-14, decision D13).

The kiosk computes the light from its sensors and serves it at
``/api/ready-light``. This server adds what only it knows: whether a kiosk is
running at all, a kiosk restarting between ladder settings, and the ladder's
own phase (its light check, a face photo owed, stopped or finished). The
ladder's phase is pushed to the kiosk as a hold, so its own screen shows it
too, and laid over the kiosk's light here until the kiosk has taken it.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Mapping

from openflight.ready_light import WORDS

logger = logging.getLogger(__name__)

# the actions whose job is a running kiosk
KIOSK_ACTIONS = {"ladder", "swings"}
RELAY_PERIOD_S = 0.5


def page_causes(job: Mapping, runner) -> tuple[str | None, str | None]:
    """(why the kiosk cannot be asked, the ladder's red cause to hold it at).

    ``runner`` is the ladder's LadderRunner, or None. A plain swings kiosk has
    no ladder, so it is never held.
    """
    running = job.get("state") == "running" and job.get("action") in KIOSK_ACTIONS
    ladder = job.get("action") == "ladder" and runner is not None
    hold = runner.ready_hold() if ladder else None
    if running:
        return None, hold
    if hold is not None:
        return hold, hold  # restarting between settings, or the ladder stopped or finished
    return "kiosk not running", None


def _red(cause: str) -> dict:
    return {
        "state": "red",
        "word": WORDS["red"],
        "cause": cause,
        "time_left_s": None,
    }


def compose(kiosk: Mapping | None, *, down: str | None, hold: str | None) -> dict:
    """The light the page shows: the tester's reason when the kiosk is down,
    else the kiosk's light, held red here until the kiosk has taken the hold."""
    now = time.time()
    if down is not None or kiosk is None:
        cause = down or "kiosk restarting"
        return {
            "schema_version": 1,
            **_red(cause),
            "causes": [{"cause": cause, "time_left_s": None}],
            "since": None,
            "checked_at": now,
            "hold": hold,
            "swing": None,
            "source": "tester",
        }
    light = {**kiosk, "source": "kiosk"}
    if hold and kiosk.get("hold") != hold:
        light.update(_red(hold))
        light["causes"] = [{"cause": hold, "time_left_s": None}, *(kiosk.get("causes") or [])]
        light["hold"] = hold
    return light


class ReadyLightRelay:
    """Keeps the kiosk's hold in step with the ladder, and answers the page.

    ``tester_side_for(tester_id)`` returns ``page_causes``'s pair for that tester
    (or for whoever runs the kiosk, when None). The kiosk client's calls return
    None when the kiosk does not answer.
    """

    def __init__(self, client, tester_side_for: Callable[[str | None], tuple]):
        self._client = client
        self._tester_side_for = tester_side_for
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def sync(self, tester_id: str | None = None) -> dict:
        down, hold = self._tester_side_for(tester_id)
        kiosk = None
        if down is None:
            kiosk = self._client.ready_light()
            if kiosk is not None and kiosk.get("hold") != hold:
                kiosk = self._client.set_ready_hold(hold) or kiosk
        return compose(kiosk, down=down, hold=hold)

    def _loop(self) -> None:
        while not self._stop.wait(RELAY_PERIOD_S):
            try:
                self.sync()
            except Exception:  # pylint: disable=broad-exception-caught
                logger.warning("Ready light relay failed", exc_info=True)

    def start(self) -> None:
        """Keep the kiosk's screen in step while no page is polling (a phone asleep)."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="ready-light-relay", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
