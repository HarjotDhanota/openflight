"""Keep one IWR6843 session open across a range setup's empty and ball captures.

The tester used to run ``capture_static_range.py`` once per capture, so each
capture paid Python start-up, port search and a port close. On the Pi the close
costs 5 s after every capture (the CP2105 purge-on-close times out), so this
holder runs ``static_range_session.py`` once and sends it both captures.

It takes the same ``start(action, commands, log_path, on_finish)`` call the job
manager takes for ``tee_range``, and splits the capture command into the
session's shared inputs and a per-capture request. The ball capture asks the
session to close afterwards; ``release()`` closes an idle session before any
other job needs the radar.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable, Sequence

logger = logging.getLogger(__name__)

ACTION = "tee_range"
# Flags that belong to one capture; everything else is shared by the session.
_PER_CAPTURE = {"--capture-id": "capture_id", "--kind": "kind", "--output-dir": "output_dir"}


def split_capture_command(command: Sequence[str]) -> tuple[tuple[str, ...], dict]:
    """(shared session arguments, per-capture request) from a capture command."""
    args = list(command)
    script = next(
        (index for index, arg in enumerate(args) if arg.endswith("capture_static_range.py")), None
    )
    if script is None:
        raise ValueError("not a capture_static_range.py command")
    rest = args[script + 1 :]
    shared: list[str] = []
    request: dict = {}
    index = 0
    while index < len(rest):
        flag = rest[index]
        if flag in _PER_CAPTURE and index + 1 < len(rest):
            request[_PER_CAPTURE[flag]] = rest[index + 1]
            index += 2
            continue
        shared.append(flag)
        index += 1
    missing = [key for key in _PER_CAPTURE.values() if key not in request]
    if missing:
        raise ValueError(f"capture command lacks {', '.join(missing)}")
    return tuple(shared), request


class HeldStaticRadar:  # pylint: disable=too-many-instance-attributes
    """Run setup captures through one long-lived radar session."""

    def __init__(
        self,
        *,
        session_script: str | Path,
        popen: Callable[..., subprocess.Popen] = subprocess.Popen,
        python: str = sys.executable,
        cwd: str | Path | None = None,
        timeout_s: float = 120.0,
        release_wait_s: float = 10.0,
        kill_grace_s: float = 5.0,
    ):
        self._session_script = str(session_script)
        self._popen = popen
        self._python = python
        self._cwd = cwd
        self._timeout_s = timeout_s
        self._release_wait_s = release_wait_s
        self._kill_grace_s = kill_grace_s
        self._lock = threading.Lock()
        self._process = None
        self._base: tuple[str, ...] | None = None
        self._reusable = False
        self._pending: tuple[str, str, Callable[[str, int], None] | None] | None = None
        self._timer: threading.Timer | None = None
        self._stderr = None

    # -- queries ---------------------------------------------------------------
    def _alive_locked(self) -> bool:
        return self._process is not None and self._process.poll() is None

    @property
    def holding(self) -> bool:
        """Whether a session process (and so the radar port) is alive."""
        with self._lock:
            return self._alive_locked()

    def status(self) -> dict[str, object]:
        """Job-manager-shaped status: running only while a capture is in flight."""
        with self._lock:
            running = self._pending is not None
            return {
                "state": "running" if running else "idle",
                "action": ACTION if running else None,
                "message": "Capturing radar setup evidence" if running else "Ready",
                "holding": self._alive_locked(),
            }

    # -- captures --------------------------------------------------------------
    def start(
        self,
        action: str,
        commands: Sequence[Sequence[str]],
        log_path: Path,
        on_finish: Callable[[str, int], None] | None = None,
        *,
        output_to_log: bool = False,  # pylint: disable=unused-argument
    ) -> None:
        """Send one setup capture to the session, starting the session if needed."""
        if action != ACTION:
            raise ValueError(f"the static radar holder only runs {ACTION} captures")
        base, request = split_capture_command(commands[0])
        request = {
            "op": "capture",
            **request,
            "log_path": str(log_path),
            # the setup needs the radar again only after a new empty capture
            "close_after": request["kind"] == "ball_present",
        }
        with self._lock:
            if self._pending is not None:
                raise RuntimeError("another action is already running")
            old = None
            if self._alive_locked() and (self._base != base or not self._reusable):
                old = self._request_close_locked()
        if old is not None:
            self._await_exit(old)
        with self._lock:
            if self._pending is not None:
                raise RuntimeError("another action is already running")
            if not self._alive_locked():
                self._spawn_locked(base, Path(log_path))
            self._pending = (request["capture_id"], action, on_finish)
            self._reusable = not request["close_after"]
            try:
                self._send_locked(request)
            except (OSError, ValueError) as error:
                self._pending = None
                process = self._process
                self._process = None
                if process is not None:
                    self._stop(process)
                raise RuntimeError(
                    f"the radar session did not take the capture: {error}"
                ) from error
            self._timer = threading.Timer(self._timeout_s, self._timed_out)
            self._timer.daemon = True
            self._timer.start()

    def cancel(self) -> bool:
        """Stop a capture in flight; its result is then missing and the setup retries."""
        with self._lock:
            if self._pending is None or self._process is None:
                return False
            process = self._process
            self._reusable = False
        self._stop(process)
        return True

    def release(self) -> None:
        """Close an idle session so another job can own the radar."""
        with self._lock:
            if self._pending is not None:
                raise RuntimeError("the radar setup capture owns the hardware")
            old = self._request_close_locked() if self._alive_locked() else None
        if old is not None:
            self._await_exit(old)

    # -- internals -------------------------------------------------------------
    def _spawn_locked(self, base: tuple[str, ...], log_path: Path) -> None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        if self._stderr is not None:
            self._stderr.close()
        self._stderr = (log_path.parent / "static-radar-session.log").open("a", encoding="utf-8")
        try:
            process = self._popen(
                [self._python, self._session_script, *base],
                cwd=self._cwd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=self._stderr,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                start_new_session=True,
            )
        except OSError as error:
            raise RuntimeError(f"the radar session could not start: {error}") from error
        self._process = process
        self._base = base
        self._reusable = True
        threading.Thread(
            target=self._read_events, args=(process,), daemon=True, name="static-radar-events"
        ).start()

    def _send_locked(self, request: dict) -> None:
        stdin = self._process.stdin
        stdin.write(json.dumps(request) + "\n")
        stdin.flush()

    def _request_close_locked(self):
        """Ask the current session to close; the caller waits outside the lock."""
        process = self._process
        self._process = None
        self._reusable = False
        try:
            process.stdin.write(json.dumps({"op": "close"}) + "\n")
            process.stdin.flush()
            process.stdin.close()
        except (OSError, ValueError):
            pass
        return process

    def _await_exit(self, process) -> None:
        try:
            process.wait(timeout=self._release_wait_s)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.warning(
                "Radar session did not close in %.0f s; killing it", self._release_wait_s
            )
            try:
                process.kill()
            except OSError:
                pass

    def _stop(self, process) -> None:
        try:
            process.terminate()
        except OSError:
            return
        ender = threading.Timer(self._kill_grace_s, self._kill_if_alive, args=(process,))
        ender.daemon = True
        ender.start()

    @staticmethod
    def _kill_if_alive(process) -> None:
        if process.poll() is None:
            try:
                process.kill()
            except OSError:
                pass

    def _timed_out(self) -> None:
        logger.warning("Radar setup capture overran %.0f s; stopping it", self._timeout_s)
        self.cancel()

    def _read_events(self, process) -> None:
        for line in process.stdout:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict) and event.get("event") == "done":
                self._finish(process, event.get("capture_id"), 0 if event.get("usable") else 1)
        try:
            code = process.wait()
        except Exception:  # pylint: disable=broad-exception-caught
            code = -1
        # a session that ended without answering failed that capture
        self._finish(process, None, code or -1)

    def _finish(self, process, capture_id, code: int) -> None:
        with self._lock:
            pending = self._pending
            if pending is None:
                return
            if capture_id is not None and capture_id != pending[0]:
                return
            if capture_id is None and process is not self._process and self._process is not None:
                return
            self._pending = None
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
        _capture_id, action, on_finish = pending
        if on_finish is not None:
            try:
                on_finish(action, code)
            except Exception:  # pylint: disable=broad-exception-caught
                logger.exception("Radar setup capture completion failed")


__all__ = ["ACTION", "HeldStaticRadar", "split_capture_command"]
