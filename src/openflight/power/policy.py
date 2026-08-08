"""Turning readings into health levels and decisions.

Level functions are pure. The reducer added in a later task wraps them with the
retained state that dwell, hysteresis and shutdown latching require.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from .config import PowerConfig
from .models import (
    PackLevel,
    PackReading,
    PendingShutdown,
    PowerSnapshot,
    RailLevel,
    RailReading,
    SourceState,
)
from .pmic import UNDERVOLTAGE_MASK


def rail_level(reading: RailReading, config: PowerConfig) -> RailLevel:
    """Classify 5V-rail health.

    Undervoltage flags outrank voltage: the firmware detecting a droop is a
    stronger signal than a spot reading taken between droops.
    """
    if reading.status != "ok":
        return "unknown"

    flags = reading.throttled or 0
    if flags & 0x1:
        return "red"

    volts = reading.ext5v_volts
    if volts is not None:
        if volts < config.rail_red_volts:
            return "red"
        if volts < config.rail_amber_volts:
            return "amber"

    if flags & UNDERVOLTAGE_MASK:
        # Sticky bit only: healthy now, but it has happened this boot.
        return "amber"
    return "green" if volts is not None else "unknown"


def pack_level(reading: PackReading, source_state: SourceState, config: PowerConfig) -> PackLevel:
    """Classify pack health from voltage.

    Bands are non-overlapping and exhaustive below ``pack_low_volts`` -- an
    earlier draft left 3.2-3.3 V with no level, so a nearly-dead pack fell
    through the table.

    On external power the reading is inflated by charge current, so voltage
    bands do not apply and mains is not a low-battery condition. On an unknown
    source it is evaluated as if on battery: a spurious warning costs a glance,
    a missed one costs the session.
    """
    if reading.status != "ok" or reading.volts is None:
        return "unknown"
    if source_state == "external":
        return "ok"
    if reading.volts >= config.pack_low_volts:
        return "ok"
    if reading.volts >= config.pack_critical_volts:
        return "low"
    return "critical"


def shutdown_eligible(reading: PackReading, config: PowerConfig) -> bool:
    """Whether voltage alone would permit a shutdown.

    Separate from the level so a pack below the shutdown threshold stays
    visibly critical whether or not automatic shutdown is enabled. This is one
    of five conditions; see the reducer.
    """
    if reading.status != "ok" or reading.volts is None:
        return False
    return reading.volts <= config.shutdown_volts


# --- reducer ---------------------------------------------------------------
#
# An earlier draft of the design claimed this module was "pure functions over a
# dataclass". That was wrong: dwell counting, hysteresis, runtime history and
# shutdown latching are all retained state. Making the state an explicit
# argument keeps determinism -- feed a list of snapshots, assert the decision
# sequence -- without a thread, a bus or a wall clock.

# Minimum on-battery history before a runtime estimate is offered.
RUNTIME_MIN_SECONDS = 600.0
# A jump larger than this between consecutive samples is a pack change, not
# discharge. Resets the slope history rather than producing a wild estimate.
RUNTIME_DISCONTINUITY_VOLTS = 0.15


@dataclass(frozen=True)
class PolicyState:
    """Everything the reducer remembers between samples."""

    pack_level: PackLevel = "unknown"
    rail_level: RailLevel = "unknown"
    pending_pack_level: PackLevel | None = None
    pack_dwell: int = 0
    pending_rail_level: RailLevel | None = None
    rail_dwell: int = 0
    shutdown_dwell: int = 0
    pending_shutdown: PendingShutdown | None = None
    shutdown_cancelled: bool = False
    last_source: SourceState = "unknown"
    last_pack_volts: float | None = None
    runtime_history: list[tuple[float, float]] = field(default_factory=list)


@dataclass(frozen=True)
class Decision:
    """What the service should do with this sample."""

    pack_level: PackLevel
    rail_level: RailLevel
    source: SourceState
    shutdown_eligible: bool
    runtime_minutes: int | None
    warnings: list[str]


def initial_state() -> PolicyState:
    """Fresh state for a newly started service."""
    return PolicyState()


def _debounce(current, proposed, pending, dwell, dwell_samples):
    """Advance a dwell counter, returning (level, pending, dwell)."""
    if proposed == current:
        return current, None, 0
    if proposed != pending:
        return current, proposed, 1
    dwell += 1
    if dwell >= dwell_samples:
        return proposed, None, 0
    return current, proposed, dwell


def _pack_level_with_deadband(reading, source_state, state, config):
    """Pack level, biased against leaving the current level too eagerly.

    Recovering from ``low`` needs voltage above the threshold *plus* the
    deadband; without it, a pack hovering on a boundary flaps every sample.
    """
    raw = pack_level(reading, source_state, config)
    if raw == "unknown" or state.pack_level == "unknown":
        return raw
    order = ["critical", "low", "ok"]
    if order.index(raw) <= order.index(state.pack_level):
        return raw
    # Improving: require the deadband before believing it.
    volts = reading.volts
    threshold = config.pack_low_volts if state.pack_level == "low" else config.pack_critical_volts
    if volts is not None and volts < threshold + config.deadband_volts:
        return state.pack_level
    return raw


def _update_runtime_history(state, snapshot, now_monotonic):
    """Append to the discharge history, resetting when it stops being valid."""
    pack, source = snapshot.pack, snapshot.source.state
    if source != "battery" or pack.status != "ok" or pack.volts is None:
        return []
    history = state.runtime_history
    if source != state.last_source:
        history = []
    elif (
        state.last_pack_volts is not None
        and abs(pack.volts - state.last_pack_volts) > RUNTIME_DISCONTINUITY_VOLTS
    ):
        history = []
    return [*history, (now_monotonic, pack.volts)]


def _runtime_minutes(history, config) -> int | None:
    """Minutes remaining, or None when the history cannot support an estimate."""
    if len(history) < 2:
        return None
    span = history[-1][0] - history[0][0]
    if span < RUNTIME_MIN_SECONDS:
        return None
    drop = history[0][1] - history[-1][1]
    if drop <= 0:
        return None  # flat or rising: not discharging in a way we can extrapolate
    volts_per_second = drop / span
    remaining = history[-1][1] - config.shutdown_volts
    if remaining <= 0:
        return 0
    return int(remaining / volts_per_second / 60)


def step(
    state: PolicyState,
    snapshot: PowerSnapshot,
    config: PowerConfig,
    now_monotonic: float,
) -> tuple[PolicyState, Decision]:
    """Fold one snapshot into the retained state and emit a decision.

    ``now_monotonic`` is a parameter rather than read internally so durations
    are exact under test and immune to the clock stepping at boot.
    """
    source = snapshot.source.state

    # A reader error must never accumulate toward a level change -- a
    # disconnected gauge could otherwise dwell its way into a shutdown.
    if snapshot.pack.status != "ok":
        pack_target, pack_pending, pack_dwell = state.pack_level, None, 0
    else:
        pack_target, pack_pending, pack_dwell = _debounce(
            state.pack_level,
            _pack_level_with_deadband(snapshot.pack, source, state, config),
            state.pending_pack_level,
            state.pack_dwell,
            config.dwell_samples,
        )
        if state.pack_level == "unknown":
            pack_target, pack_pending, pack_dwell = (
                pack_level(snapshot.pack, source, config),
                None,
                0,
            )

    rail_target, rail_pending, rail_dwell = _debounce(
        state.rail_level,
        rail_level(snapshot.rail, config),
        state.pending_rail_level,
        state.rail_dwell,
        config.dwell_samples,
    )
    if state.rail_level == "unknown":
        rail_target, rail_pending, rail_dwell = rail_level(snapshot.rail, config), None, 0

    history = _update_runtime_history(state, snapshot, now_monotonic)
    eligible = shutdown_eligible(snapshot.pack, config)

    warnings: list[str] = []
    if pack_target == "low":
        warnings.append("Battery low")
    elif pack_target == "critical":
        warnings.append("Battery critically low")
    if rail_target == "amber":
        warnings.append("Supply voltage marginal")
    elif rail_target == "red":
        warnings.append("Supply voltage too low - brownout risk")

    new_state = replace(
        state,
        pack_level=pack_target,
        rail_level=rail_target,
        pending_pack_level=pack_pending,
        pack_dwell=pack_dwell,
        pending_rail_level=rail_pending,
        rail_dwell=rail_dwell,
        last_source=source,
        last_pack_volts=snapshot.pack.volts,
        runtime_history=history,
    )
    decision = Decision(
        pack_level=pack_target,
        rail_level=rail_target,
        source=source,
        shutdown_eligible=eligible,
        runtime_minutes=_runtime_minutes(history, config),
        warnings=warnings,
    )
    return new_state, decision
