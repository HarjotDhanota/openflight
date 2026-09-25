"""Process-wide ownership for the IWR6843 single-UART transport."""

from __future__ import annotations

import hashlib
import os
import tempfile
import time
from pathlib import Path
from typing import BinaryIO

_LOCK_FILE_BYTES = 256
_OWNER_BYTES = _LOCK_FILE_BYTES - 1


class IWR6843DeviceBusyError(RuntimeError):
    """The IWR6843 serial device is already owned by another process."""

    def __init__(self, port: str, owner: str | None = None):
        detail = f" ({owner})" if owner else ""
        super().__init__(
            f"IWR6843 serial device {port!r} is busy; another process is using it{detail}"
        )
        self.port = port
        self.owner = owner


def _default_lock_root() -> Path:
    user = str(os.getuid()) if hasattr(os, "getuid") else os.environ.get("USERNAME", "user")
    user_key = hashlib.sha256(user.encode("utf-8")).hexdigest()[:12]
    return Path(tempfile.gettempdir()) / f"openflight-iwr6843-{user_key}"


def _device_identity(port: str) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(port)))


def _try_lock(handle: BinaryIO) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt  # pylint: disable=import-outside-toplevel,import-error

        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl  # pylint: disable=import-outside-toplevel,import-error

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(handle: BinaryIO) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt  # pylint: disable=import-outside-toplevel,import-error

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl  # pylint: disable=import-outside-toplevel,import-error

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class IWR6843DeviceLock:
    """Exclusive interprocess lock keyed by the resolved serial device path."""

    def __init__(
        self,
        port: str,
        *,
        timeout_s: float = 0.0,
        lock_root: str | Path | None = None,
    ):
        if not isinstance(port, str) or not port.strip():
            raise ValueError("IWR6843 port must be a non-empty string")
        if timeout_s < 0.0:
            raise ValueError("IWR6843 lock timeout cannot be negative")
        self.port = port
        self.timeout_s = float(timeout_s)
        identity = _device_identity(port)
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
        root = Path(lock_root) if lock_root is not None else _default_lock_root()
        self.path = root / f"device-{digest}.lock"
        self._handle: BinaryIO | None = None

    def acquire(self) -> "IWR6843DeviceLock":
        """Acquire ownership or raise a clear busy error after the timeout."""
        if self._handle is not None:
            return self
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        handle = os.fdopen(descriptor, "r+b")
        if self.path.stat().st_size < _LOCK_FILE_BYTES:
            handle.seek(0)
            handle.write(b"\0" * _LOCK_FILE_BYTES)
            handle.flush()
        deadline = time.monotonic() + self.timeout_s
        while True:
            try:
                _try_lock(handle)
                break
            except (OSError, BlockingIOError):
                if time.monotonic() >= deadline:
                    owner = self._read_owner(handle)
                    handle.close()
                    raise IWR6843DeviceBusyError(self.port, owner) from None
                time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
        self._handle = handle
        owner = f"pid={os.getpid()} port={self.port}".encode("utf-8")[:_OWNER_BYTES]
        handle.seek(1)
        handle.write(owner.ljust(_OWNER_BYTES, b"\0"))
        handle.flush()
        return self

    @staticmethod
    def _read_owner(handle: BinaryIO) -> str | None:
        try:
            handle.seek(1)
            value = handle.read(_OWNER_BYTES).decode("utf-8", errors="replace").strip("\0\r\n ")
            return value or None
        except OSError:
            return None

    def release(self) -> None:
        """Release ownership; repeated calls are harmless."""
        handle = self._handle
        if handle is None:
            return
        self._handle = None
        try:
            handle.seek(1)
            handle.write(b"\0" * _OWNER_BYTES)
            handle.flush()
            _unlock(handle)
        finally:
            handle.close()

    def __enter__(self) -> "IWR6843DeviceLock":
        return self.acquire()

    def __exit__(self, *_exc) -> None:
        self.release()


__all__ = ["IWR6843DeviceBusyError", "IWR6843DeviceLock"]
