from openflight.power.config import PowerConfig
from openflight.power.models import PackReading, PowerSnapshot, RailReading, SourceReading
from openflight.power.policy import cancel_shutdown, initial_state, step

ARMED = PowerConfig(dwell_samples=2, auto_shutdown_enabled=True, shutdown_grace_seconds=60)


def snap(volts, t=0.0, source="battery"):
    return PowerSnapshot(
        timestamp=t,
        pack=PackReading(status="ok", timestamp=t, volts=volts, percent=5.0),
        rail=RailReading(status="ok", timestamp=t, ext5v_volts=5.2, throttled=0),
        source=SourceReading(status="ok", timestamp=t, state=source),
    )


def drive(voltages, config=ARMED, source="battery", state=None):
    state = state or initial_state()
    decision = None
    for index, volts in enumerate(voltages):
        state, decision = step(state, snap(volts, float(index), source), config, float(index))
    return state, decision


def test_arms_after_dwell_when_all_conditions_hold():
    # dwell_samples=2, so the 2nd consecutive eligible sample arms it. Drive
    # exactly that many: a 3rd sample would leave the last decision as "none",
    # since arming happens once and the pending shutdown then just persists.
    state, decision = drive([3.1, 3.1])
    assert decision.shutdown_action == "arm"
    assert state.pending_shutdown is not None
    # Armed at the 2nd sample (now=1.0), not the last.
    assert state.pending_shutdown.deadline_monotonic == 1.0 + 60


def test_arming_is_not_repeated_while_pending():
    state, decision = drive([3.1, 3.1, 3.1, 3.1])
    assert decision.shutdown_action == "none"
    assert state.pending_shutdown is not None
    assert state.pending_shutdown.deadline_monotonic == 1.0 + 60  # unchanged


def test_disabled_never_arms():
    config = PowerConfig(dwell_samples=2, auto_shutdown_enabled=False)
    state, decision = drive([3.1, 3.1, 3.1], config=config)
    assert decision.shutdown_action == "none"
    assert state.pending_shutdown is None
    # ...but the pack is still visibly critical.
    assert decision.pack_level == "critical"


def test_external_power_never_arms():
    state, _ = drive([3.1, 3.1, 3.1], source="external")
    assert state.pending_shutdown is None


def test_unknown_source_never_arms():
    state, _ = drive([3.1, 3.1, 3.1], source="unknown")
    assert state.pending_shutdown is None


def test_above_threshold_never_arms():
    state, _ = drive([3.25, 3.25, 3.25])
    assert state.pending_shutdown is None


def test_single_low_sample_does_not_arm():
    state, _ = drive([3.9, 3.1, 3.9])
    assert state.pending_shutdown is None


def test_executes_at_deadline():
    state, _ = drive([3.1, 3.1, 3.1])
    state, decision = step(state, snap(3.1, 99.0), ARMED, 99.0)
    assert decision.shutdown_action == "execute"


def test_cancel_clears_pending_and_latches_for_the_process():
    state, _ = drive([3.1, 3.1, 3.1])
    state = cancel_shutdown(state, state.pending_shutdown.id)
    assert state.pending_shutdown is None
    assert state.shutdown_cancelled is True
    # No re-arming afterwards, at any voltage.
    state, decision = drive([3.0, 3.0, 3.0, 3.0], state=state)
    assert state.pending_shutdown is None
    assert decision.shutdown_action == "none"
    # The warning stays visible.
    assert decision.pack_level == "critical"


def test_stale_cancel_id_is_ignored():
    state, _ = drive([3.1, 3.1, 3.1])
    unchanged = cancel_shutdown(state, "not-the-current-id")
    assert unchanged.pending_shutdown is not None
    assert unchanged.shutdown_cancelled is False


def test_recovering_above_threshold_disarms_without_latching():
    state, _ = drive([3.1, 3.1, 3.1])
    assert state.pending_shutdown is not None
    state, _ = drive([3.9, 3.9, 3.9], state=state)
    assert state.pending_shutdown is None
    assert state.shutdown_cancelled is False  # not a user decision, so no latch
