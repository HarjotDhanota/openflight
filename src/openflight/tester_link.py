"""The kiosk's Tester button: where the tester page is, while the tester answers.

A kiosk the tester starts carries ``--tester-setup-required`` and the tester's port
(``--tester-control-port``, 8765 by default). ``GET /api/tester-page`` answers::

    {"available": bool, "url": str | None, "reason": str | None, "final": bool}

``available`` only while that tester server answers its ping on this unit, and, for a
request from another device (a phone), only when the tester listens beyond loopback.
``url`` is reached the way the request reached the kiosk, so the unit's own screen goes
to ``localhost`` (an origin its Electron shell allows) and a phone to the unit's address.
``final`` says nothing will change for this kiosk (no tester started it): stop asking.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.request
from typing import Callable

from flask import jsonify, request

PING_PATH = "/api/tester/ping"
# the kiosk UI polls every few seconds; one probe serves them all for this long
CACHE_S = 2.0
PING_TIMEOUT_S = 1.0
LOOPBACK_ADDRESSES = {"127.0.0.1", "::1", "localhost"}
NOT_ANSWERING = "the tester page is not answering"
SCREEN_ONLY = "the tester page opens only on the unit's own screen"


def fetch_json(url: str, timeout_s: float):
    """GET ``url`` and decode its JSON body."""
    with urllib.request.urlopen(url, timeout=timeout_s) as response:
        return json.loads(response.read())


def _host_name(host: str) -> str:
    """``host`` without its port, keeping an IPv6 address's brackets."""
    if host.startswith("["):
        return host[: host.index("]") + 1] if "]" in host else host
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def _is_loopback(address: str | None) -> bool:
    return bool(address) and (address in LOOPBACK_ADDRESSES or address.startswith("127."))


class TesterLink:
    """The tester this kiosk links back to, and a short cache of its ping."""

    def __init__(
        self,
        port: int | None,
        *,
        fetch: Callable[[str, float], object] = fetch_json,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._fetch = fetch
        self._clock = clock
        self._lock = threading.Lock()
        self.port: int | None = None
        self._cached: tuple[float, dict | None] | None = None
        self.configure(port)

    def configure(self, port: int | None) -> None:
        """Link to the tester on ``port``, or to none."""
        with self._lock:
            self.port = int(port) if port is not None else None
            self._cached = None

    def _ping(self, port: int) -> dict | None:
        with self._lock:
            if self._cached is not None and self._clock() - self._cached[0] < CACHE_S:
                return self._cached[1]
        try:
            answer = self._fetch(f"http://127.0.0.1:{port}{PING_PATH}", PING_TIMEOUT_S)
        except (OSError, ValueError):
            answer = None
        if not isinstance(answer, dict) or answer.get("tester") is not True:
            answer = None
        with self._lock:
            self._cached = (self._clock(), answer)
        return answer

    def answer(self, host: str, remote_addr: str | None) -> dict:
        """What ``/api/tester-page`` says to a request for ``host`` from ``remote_addr``."""
        port = self.port
        if port is None:
            return {
                "available": False,
                "url": None,
                "reason": "no tester started this kiosk",
                "final": True,
            }
        ping = self._ping(port)
        if ping is None:
            return {"available": False, "url": None, "reason": NOT_ANSWERING, "final": False}
        if not _is_loopback(remote_addr) and not ping.get("lan"):
            return {"available": False, "url": None, "reason": SCREEN_ONLY, "final": False}
        name = _host_name(host or "localhost") or "localhost"
        return {"available": True, "url": f"http://{name}:{port}/", "reason": None, "final": False}


def register(app, link: TesterLink) -> None:
    """Expose ``GET /api/tester-page`` on the kiosk."""

    @app.get("/api/tester-page")
    def tester_page():
        return jsonify(link.answer(request.host, request.remote_addr))
