"""Optional kiosk extensions: the one registration point for add-ons to the kiosk server.

An extension subclasses ``KioskExtension``, overrides the hooks it needs and calls
``register``; installed packages declare one under the ``openflight.kiosk_extensions``
entry-point group. Hooks run in registration order.
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

    Both None means the model decided there is no carry (a fusion unit).
    """

    source: Optional[str]
    table_reason: Optional[str]


class KioskExtension:  # pylint: disable=unused-argument
    """Base class for kiosk extensions: every hook does nothing."""

    name = "extension"

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

    def diagnostic_camera(self) -> bool:
        """True when a study page drives the camera and may exceed the armed profile."""
        return False

    def camera_runtime_class(self, base: type) -> Optional[type]:
        """A subclass of ``base`` (the camera runtime class) the kiosk runs instead, or None."""
        return None

    def trigger_evidence_provider(self) -> Optional[Callable[[float], dict]]:
        """A readiness reader the camera stamps on each trigger, or None."""
        return None

    def iwr_optional(self) -> Optional[str]:
        """Why the run may continue without a failed IWR6843, or None to exit."""
        return None

    def session_start_config(self, config: dict) -> None:
        """Add this extension's keys to the configuration recorded at session start."""

    def ready_light_states(self, states):
        """Return the ready light's sensor states adjusted for this extension (None: off)."""
        return states

    def setup_ready_causes(self, snapshot: dict) -> list:
        """Extra ready-light causes from the setup snapshot: ("red" or "amber", what it says)."""
        return []

    def before_setup_action(self, section: str, action: str, payload: dict) -> Optional[str]:
        """A reason to refuse this setup action (e.g. a run is in progress), or None."""
        return None

    def after_setup_action(self, section: str, action: str, result: dict) -> None:
        """A setup action finished and was applied."""

    def on_shot_detected(self, shot) -> None:
        """An OPS shot reached the kiosk, before any extension may refuse it."""

    def admit_shot(self, shot) -> bool:
        """False to refuse an OPS shot before it is numbered (the extension reports why)."""
        return True

    def on_shot_accepted(self, shot) -> None:
        """A shot was numbered and will be published."""

    def extend_shot_record(self, shot, record: dict) -> None:
        """Add this extension's keys to a shot's record (``Shot.to_dict``), from its extras."""

    def check_camera_capture(self, shot, camera_capture, session_uuid: str) -> None:
        """Raise ValueError when this camera clip must not be fused into the shot."""

    def on_camera_capture(self, shot, camera_capture) -> None:
        """A camera clip matched this shot (before it is logged)."""

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

        ``fusion``: a unit with an IWR6843 or camera (no table carry).
        """
        return None

    def after_shot_published(self, shot, *, update_kind: str) -> None:
        """A finalized shot went out ("first" or "late_update"); must not block the kiosk."""

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
    """Import every declared extension, logging and skipping failures; return their names."""
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


def setup_ready_causes(snapshot: dict) -> list:
    """Every extension's extra ready-light causes, in registration order."""
    causes: list = []
    for extension in registered():
        causes.extend(extension.setup_ready_causes(snapshot) or ())
    return causes


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
