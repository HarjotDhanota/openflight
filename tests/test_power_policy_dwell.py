from openflight.power.config import PowerConfig
from openflight.power.models import PackReading, PowerSnapshot, RailReading, SourceReading
from openflight.power.policy import initial_state, step

CONFIG = PowerConfig(dwell_samples=3, deadband_volts=0.05)


def snap(volts, t=0.0, source="battery", pack_status="ok"):
    return PowerSnapshot(
        timestamp=t,
        pack=PackReading(status=pack_status, timestamp=t, volts=volts, percent=50.0),
        rail=RailReading(status="ok", timestamp=t, ext5v_volts=5.21, throttled=0),
        source=SourceReading(status="ok", timestamp=t, state=source),
    )


def run(voltages, config=CONFIG, **kw):
    state, decisions = initial_state(), []
    for index, volts in enumerate(voltages):
        state, decision = step(state, snap(volts, t=float(index), **kw), config, float(index))
        decisions.append(decision)
    return decisions


def test_first_reading_establishes_the_level_with_no_dwell():
    # Startup must show the truth straight away, not 30 seconds of "unknown".
    # Every other test in this file has to seed a baseline because of this.
    assert run([3.5])[0].pack_level == "low"
    assert run([3.9])[0].pack_level == "ok"


def test_level_change_requires_dwell():
    levels = [d.pack_level for d in run([3.9, 3.5, 3.5, 3.5])]
    assert levels == ["ok", "ok", "ok", "low"]  # changes only on the 3rd low read


def test_transient_sag_does_not_change_level():
    levels = [d.pack_level for d in run([3.9, 3.5, 3.9, 3.9])]
    assert levels == ["ok", "ok", "ok", "ok"]


def test_flapping_across_a_threshold_does_not_oscillate():
    levels = [d.pack_level for d in run([3.9, 3.59, 3.61, 3.59, 3.61, 3.59])]
    assert set(levels) == {"ok"}


def test_leaving_a_level_requires_the_deadband():
    # Settle into low, then recover. 3.61 is above 3.6 but inside the 0.05
    # deadband, so it must not clear; 3.66 must.
    decisions = run([3.5, 3.5, 3.5, 3.61, 3.61, 3.61, 3.66, 3.66, 3.66])
    levels = [d.pack_level for d in decisions]
    assert levels[2] == "low"
    assert levels[5] == "low"  # inside deadband, still low
    assert levels[8] == "ok"


def test_reader_error_resets_dwell():
    # The first valid reading establishes a level with no dwell, so the
    # indicator is right at startup rather than 30 seconds later. That means
    # this test must seed a healthy baseline first -- starting at 3.5 V would
    # make "low" the baseline and there would be no transition to observe.
    state, config = initial_state(), CONFIG  # dwell_samples=3
    voltages = [
        (3.9, "ok"),  # 0: baseline established immediately -> "ok"
        (3.5, "ok"),  # 1: dwell 1 toward "low"
        (3.5, "ok"),  # 2: dwell 2
        (None, "error"),  # 3: gauge drops out -> dwell resets to 0
        (3.5, "ok"),  # 4: dwell 1 again (would have been 3 without the reset)
        (3.5, "ok"),  # 5: dwell 2 -- still short of 3
    ]
    last = None
    for index, (volts, status) in enumerate(voltages):
        state, last = step(
            state, snap(volts, t=float(index), pack_status=status), config, float(index)
        )
    # A disconnected gauge must not accumulate its way toward a level change,
    # because at the bottom of the scale that path ends in a shutdown.
    assert last.pack_level == "ok"


def test_runtime_none_before_enough_history():
    assert run([3.9, 3.88, 3.86])[-1].runtime_minutes is None


def test_runtime_history_resets_on_source_change():
    state, config = initial_state(), PowerConfig(dwell_samples=1)
    state, _ = step(state, snap(3.9, t=0.0, source="battery"), config, 0.0)
    state, _ = step(state, snap(3.88, t=1.0, source="battery"), config, 1.0)
    state, _ = step(state, snap(3.86, t=2.0, source="external"), config, 2.0)
    assert state.runtime_history == []


def test_runtime_history_resets_on_voltage_discontinuity():
    state, config = initial_state(), PowerConfig(dwell_samples=1)
    state, _ = step(state, snap(3.90, t=0.0), config, 0.0)
    state, _ = step(state, snap(3.88, t=1.0), config, 1.0)
    state, _ = step(state, snap(4.15, t=2.0), config, 2.0)  # hot-swapped pack
    assert len(state.runtime_history) == 1
