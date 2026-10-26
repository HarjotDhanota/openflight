"""Observe a sound GATE once and carry its evidence unchanged to every consumer."""

from __future__ import annotations

import functools
import importlib.metadata
import time
import uuid
from dataclasses import asdict, dataclass
from typing import Callable

from openflight.clock_mapping import QUALIFIED_BACKENDS, BracketedPair, EdgeObservation
from openflight.numeric import finite as _finite


@functools.lru_cache(maxsize=None)
def _package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def warm_edge_reading_cache() -> None:
    """Keep package metadata lookups out of the GPIO callback."""
    for name in ("gpiozero", "lgpio"):
        _package_version(name)


def read_edge_tick(button) -> dict:
    """Bracket gpiozero's active age; only LGPIO forwards a kernel event tick."""
    before = time.monotonic_ns()
    try:
        age_s = button.active_time if button is not None else None
    except Exception:  # pylint: disable=broad-exception-caught
        age_s = None
    after = time.monotonic_ns()
    factory = getattr(button, "pin_factory", None)
    return {
        "mono_before_ns": before,
        "age_ns": round(float(age_s) * 1e9) if _finite(age_s) else None,
        "mono_after_ns": after,
        "backend": type(factory).__name__ if factory is not None else None,
        "gpiozero_version": _package_version("gpiozero"),
        "lgpio_version": _package_version("lgpio"),
    }


def edge_trace_fields(reader: Callable[[], dict] | None, observer_wall_s: float) -> dict:
    """Capture raw GPIO evidence and a bracketed wall/monotonic clock pair."""
    reading = {}
    reading_error = None
    try:
        reading = reader() if reader is not None else {}
    except Exception as error:  # pylint: disable=broad-exception-caught
        reading_error = type(error).__name__
    low, high = BracketedPair.now().wall_minus_mono_bounds_ns()
    return {
        "mono_before_ns": reading.get("mono_before_ns"),
        "age_ns": reading.get("age_ns"),
        "mono_after_ns": reading.get("mono_after_ns"),
        "wall_minus_mono_low_ns": low,
        "wall_minus_mono_high_ns": high,
        "backend": reading.get("backend"),
        "gpiozero_version": reading.get("gpiozero_version"),
        "lgpio_version": reading.get("lgpio_version"),
        "observer_wall_s": float(observer_wall_s),
        "reading_error": reading_error,
    }


@dataclass(frozen=True)
class GateEvent:
    """Shared kernel-tick evidence, without a claim about physical edge accuracy."""

    event_id: str
    observation: EdgeObservation
    gpiozero_version: str | None = None
    lgpio_version: str | None = None
    reading_error: str | None = None

    @classmethod
    def observe(
        cls,
        reader: Callable[[], dict] | None,
        observer_wall_s: float,
        *,
        previous_callback_mono_ns: int | None = None,
    ) -> GateEvent:
        """Read before queueing or notifying any consumer, independent of tracing."""
        fields = edge_trace_fields(reader, observer_wall_s)
        observation = EdgeObservation(
            edge_seq=None,
            mono_before_ns=fields["mono_before_ns"],
            mono_after_ns=fields["mono_after_ns"],
            edge_age_ns=fields["age_ns"],
            wall_minus_mono_low_ns=fields["wall_minus_mono_low_ns"],
            wall_minus_mono_high_ns=fields["wall_minus_mono_high_ns"],
            backend=fields["backend"],
            previous_callback_mono_ns=previous_callback_mono_ns,
            observer_wall_s=fields["observer_wall_s"],
        )
        return cls(
            uuid.uuid4().hex,
            observation,
            fields["gpiozero_version"],
            fields["lgpio_version"],
            fields["reading_error"],
        )

    @property
    def kernel_mono_interval_ns(self) -> tuple[int, int] | None:
        """A usable kernel tick interval, never a substituted callback timestamp."""
        edge = self.observation
        if self.reading_error is not None or edge.backend not in QUALIFIED_BACKENDS:
            return None
        interval = edge.kernel_mono_interval_ns()
        if interval is None or interval[0] < 0:
            return None
        if (
            edge.previous_callback_mono_ns is not None
            and interval[0] <= edge.previous_callback_mono_ns
        ):
            return None
        return interval

    def trace_fields(self) -> dict:
        """Original raw fields for existing trace readers, with a shared event ID."""
        fields = asdict(self.observation)
        fields.pop("edge_seq")
        fields["age_ns"] = fields.pop("edge_age_ns")
        return {
            **fields,
            "gate_event_id": self.event_id,
            "gpiozero_version": self.gpiozero_version,
            "lgpio_version": self.lgpio_version,
            "reading_error": self.reading_error,
        }

    def to_dict(self) -> dict:
        """JSON evidence keeps the interval and the unmeasured IRQ uncertainty."""
        fields = self.trace_fields()
        fields["event_id"] = fields.pop("gate_event_id")
        interval = self.kernel_mono_interval_ns
        return {
            **fields,
            "source": "sound_gate",
            "kernel_mono_interval_ns": list(interval) if interval is not None else None,
            "status": "observed_kernel_tick" if interval is not None else "unobserved",
            "physical_edge_uncertainty_ns": None,
        }
