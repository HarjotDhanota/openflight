"""Optional kiosk extensions: the one registration point for add-ons to the kiosk server.

A plain kiosk runs with none. An extension subclasses ``KioskExtension``, overrides
only the hooks it needs (every hook here is a no-op), and registers itself with
``register``. Installed packages can declare extensions under the
``openflight.kiosk_extensions`` entry-point group; the kiosk loads them at start-up
with ``load_installed``. Loading imports the module, which registers its extension.

Hooks are called in registration order. A hook that answers a question (a bool or
a callable) takes the first extension that gives a non-default answer.
"""

from __future__ import annotations

import contextlib
import importlib.metadata
import logging
import threading
from typing import Any, Callable, ContextManager, Iterator, NamedTuple, Optional

logger = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "openflight.kiosk_extensions"


class CarryOutcome(NamedTuple):
    """What a flight model did with a shot's carry.

    ``source`` names the carry it committed ("ballistic"), or None. With no carry,
    ``table_reason`` says why an OPS-only unit takes the table estimate instead;
    both None means the model decided there is no carry (a fusion unit, D17).
    """

    source: Optional[str]
    table_reason: Optional[str]


class KioskExtension:  # pylint: disable=unused-argument
    """Base class for kiosk extensions: every hook does nothing."""

    name = "extension"

    # --- start-up and shutdown -------------------------------------------------------

    def add_arguments(self, parser) -> None:
        """Add this extension's command-line flags to the kiosk's parser."""

    def configure(self, args, parser) -> None:
        """Validate and apply the parsed flags; call ``parser.error`` to refuse them."""

    def on_startup(self, args) -> None:
        """After session logging starts and before any sensor is opened."""

    def after_camera_setup(self) -> None:
        """After the high-speed camera was set up (or skipped), before any shot."""

    def before_monitor_start(self) -> None:
        """Before the OPS monitor's capture thread starts and the GATE is armed."""

    def on_shutdown(self) -> None:
        """During hardware cleanup at exit."""

    # --- questions the kiosk asks ----------------------------------------------------

    def diagnostic_camera(self) -> bool:
        """True when a study page drives the camera: raw frames are kept for it and
        its controls may exceed the armed exposure profile."""
        return False

    def trigger_evidence_provider(self) -> Optional[Callable[[float], dict]]:
        """A readiness reader the camera stamps on each trigger, or None."""
        return None

    def iwr_optional(self) -> Optional[str]:
        """Why the run continues without a requested IWR6843 that failed to start, or
        None (the kiosk then exits)."""
        return None

    # --- per-session and per-shot ------------------------------------------------------

    def session_start_config(self, config: dict) -> None:
        """Add this extension's keys to the configuration recorded at session start."""

    def ready_light_states(self, states):
        """Return the ready light's sensor states adjusted for this extension (None: off)."""
        return states

    def on_shot_detected(self, shot) -> None:
        """An OPS shot reached the kiosk, before any extension may refuse it."""

    def admit_shot(self, shot) -> bool:
        """False to refuse an OPS shot before it is numbered (the extension reports why)."""
        return True

    def on_shot_accepted(self, shot) -> None:
        """A shot was numbered and will be published."""

    def check_camera_capture(self, shot, camera_capture, session_uuid: str) -> None:
        """Raise ValueError when this camera clip must not be fused into the shot."""

    def on_camera_capture(self, shot, camera_capture) -> None:
        """A camera clip matched this shot (before it is logged)."""

    # --- shot models: what a model builds from the measured inputs --------------------

    def on_club_delivery(self, shot, delivery) -> None:
        """The camera's club delivery for this shot (a result, or a dict with its status)."""

    def after_enrichment(self, shot) -> None:
        """The shot's radar and camera enrichment is done (face, loft, spin axis go here)."""

    def model_spin(self, shot) -> Optional[tuple[float, str]]:
        """A model's spin (rpm, method) that --calculated-spin uses instead of its formula."""
        return None

    def after_speed_contract(self, shot) -> None:
        """The radial speeds are fixed; a model may add the full speeds."""

    def simulate_carry(self, shot, *, fusion: bool, enabled: bool) -> Optional["CarryOutcome"]:
        """Simulate the flight and commit the carry, or None without a flight model.

        ``fusion`` is a unit with an IWR6843 or camera (no table carry, D17);
        ``enabled`` is --ballistics.
        """
        return None

    def after_shot_published(self, shot, *, update_kind: str) -> None:
        """A finalized shot went out to the kiosk ("first" result or "late_update").

        Work started here must not hold up the kiosk; a result it finds later goes
        out with ``server.publish_shot_update``.
        """

    def observe_shot_emit(
        self, shot, event: str, payload: dict, *, final: bool, update_kind: str
    ) -> Optional[ContextManager]:
        """A context manager entered around one shot emit, or None."""
        return None

    def on_fusion_diagnostic(  # pylint: disable=too-many-arguments
        self,
        diagnostic_logger,
        session_uuid: Optional[str],
        shot,
        *,
        revision: int,
        phase: str,
        outcome: str,
        reason: Optional[str] = None,
    ) -> None:
        """One fusion lifecycle step of a shot (pending, terminal)."""


_lock = threading.Lock()
_extensions: list[KioskExtension] = []


def register(extension: KioskExtension) -> KioskExtension:
    """Add an extension once; registering it again changes nothing."""
    with _lock:
        if extension not in _extensions:
            _extensions.append(extension)
    return extension


def unregister(extension: KioskExtension) -> None:
    """Remove an extension (tests, or an extension that turns itself off)."""
    with _lock:
        if extension in _extensions:
            _extensions.remove(extension)


def registered() -> tuple[KioskExtension, ...]:
    """The registered extensions, in registration order."""
    with _lock:
        return tuple(_extensions)


def load_installed() -> list[str]:
    """Import every extension declared in the entry-point group; return their names.

    An extension that fails to import is logged and skipped: the kiosk starts
    without it rather than not at all.
    """
    loaded = []
    for entry_point in importlib.metadata.entry_points(group=ENTRY_POINT_GROUP):
        try:
            extension = entry_point.load()
        except Exception:  # pylint: disable=broad-exception-caught
            logger.error("[EXTENSIONS] %s failed to load", entry_point.value, exc_info=True)
            continue
        if isinstance(extension, KioskExtension):
            register(extension)
        loaded.append(entry_point.name)
    return loaded


def call_all(hook: str, *args: Any, **kwargs: Any) -> None:
    """Call one hook on every extension, in order."""
    for extension in registered():
        getattr(extension, hook)(*args, **kwargs)


def any_true(hook: str, *args: Any, **kwargs: Any) -> bool:
    """True when any extension answers True."""
    return any(getattr(extension, hook)(*args, **kwargs) for extension in registered())


def first(hook: str, *args: Any, **kwargs: Any) -> Any:
    """The first answer that is not None, or None."""
    for extension in registered():
        answer = getattr(extension, hook)(*args, **kwargs)
        if answer is not None:
            return answer
    return None


def all_admit(shot) -> bool:
    """False as soon as one extension refuses the shot (later ones are not asked)."""
    return all(extension.admit_shot(shot) for extension in registered())


def adjust_ready_light(states):
    """The ready light's states after every extension adjusted them."""
    for extension in registered():
        if states is None:
            break
        states = extension.ready_light_states(states)
    return states


@contextlib.contextmanager
def observing_shot_emit(
    shot, event: str, payload: dict, *, final: bool, update_kind: str
) -> Iterator[None]:
    """Enter every extension's emit observer around one shot emit."""
    with contextlib.ExitStack() as stack:
        for extension in registered():
            observer = extension.observe_shot_emit(
                shot, event, payload, final=final, update_kind=update_kind
            )
            if observer is not None:
                stack.enter_context(observer)
        yield
