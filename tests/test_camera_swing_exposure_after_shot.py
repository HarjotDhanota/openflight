"""The swing exposure locks on a held ball reading, then re-meters only after shots or idle."""

import time

import pytest

from openflight.camera import capture_runtime, swing_exposure as sx
from openflight.camera.tester_capture_runtime import TesterCaptureRuntime
from tests import test_camera_swing_exposure_runtime as swing

IN_BAND = 129  # DN above black: the ball's peak at 0.55 of clip at 200 us x 8
BRIGHTER = 190  # peak 0.81 of clip: out of the hold band, neither clipped nor severe
CLIPPED = 240


def _runtime(tmp_path, **overrides):
    return swing._runtime(tmp_path, exposure_us=200, gain=8.0, **overrides)


def _counting_cycles(runtime, monkeypatch):
    calls = []
    original = runtime._run_auto_exposure_cycle

    def cycle(**kwargs):
        calls.append(kwargs.get("trigger", "searching"))
        return original(**kwargs)

    monkeypatch.setattr(runtime, "_run_auto_exposure_cycle", cycle)
    return calls


def _locked(runtime, now=1000.0):
    swing._add_frames(runtime, IN_BAND)
    decision = runtime._swing_exposure_step(now)
    assert decision is not None and decision.reading.passes and not decision.should_apply
    assert runtime.auto_exposure_status()["metering_state"] == "locked"
    return runtime


def _save_shot(runtime, signal, **frame_controls):
    """One real shot: the ring freezes, the clip is saved, and the kiosk claims it."""
    settings = runtime.settings
    swing._add_frames(runtime, signal, count=settings.pre_frames, **frame_controls)
    assert runtime.notify_trigger()
    swing._add_frames(runtime, signal, count=settings.post_frames, **frame_controls)
    runtime._ready.put(runtime._ring.pop_capture())
    runtime._ready.put(None)
    runtime._save_loop()
    capture = runtime.capture_for_shot(None, timeout_s=0.0)
    assert capture is not None and capture.valid
    return capture


def _triggers(runtime):
    return [item["trigger"] for item in runtime.auto_exposure_status()["recent_decisions"]]


def test_a_locked_exposure_is_not_metered_until_five_idle_minutes(tmp_path, monkeypatch):
    runtime = _locked(_runtime(tmp_path), now=1000.0)
    calls = _counting_cycles(runtime, monkeypatch)

    for second in range(1, 300):
        assert runtime._swing_exposure_step(1000.0 + second) is None
    assert calls == []

    decision = runtime._swing_exposure_step(1300.0)
    assert calls == ["idle_recheck"]
    assert decision.reading.passes

    for second in range(1, 300):
        runtime._swing_exposure_step(1300.0 + second)
    assert calls == ["idle_recheck"]
    runtime._swing_exposure_step(1600.0)
    assert calls == ["idle_recheck", "idle_recheck"]
    assert _triggers(runtime) == ["searching", "idle_recheck", "idle_recheck"]
    assert runtime.auto_exposure_status()["metering_state"] == "locked"


def test_until_locked_the_ball_is_metered_every_second(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path)
    calls = _counting_cycles(runtime, monkeypatch)
    swing._add_frames(runtime, BRIGHTER)

    runtime._swing_exposure_step(10.0)  # the first ball moves at once
    swing._add_frames(runtime, swing._light_at(runtime, BRIGHTER / (200 * 8.0)))
    runtime._swing_exposure_step(11.0)

    assert calls == ["searching", "searching"]
    assert runtime.auto_exposure_status()["metering_state"] == "locked"


def test_a_saved_shot_is_metered_once_from_its_own_resting_frames(tmp_path, monkeypatch):
    runtime = _locked(_runtime(tmp_path))
    calls = _counting_cycles(runtime, monkeypatch)
    # the sensor reports 199 us for the 200 requested: the clip's own controls are used
    _save_shot(runtime, CLIPPED, exposure_us=199)
    runtime.set_resting_ball_locator(swing._not_seen())  # the ball has flown
    swing._add_frames(runtime, 0)  # the live frames now show an empty tee

    decision = runtime._swing_exposure_step(1010.0)

    assert calls == []  # not a live cycle
    assert decision.reading.exposure_us == 199
    assert decision.reading.saturated
    assert decision.current == sx.Setting(200, 8.0)
    assert decision.should_apply
    assert runtime._camera.controls[-1] == {
        "ExposureTime": decision.target.exposure_us,
        "AnalogueGain": decision.target.gain,
    }
    assert _triggers(runtime) == ["searching", "after_shot"]
    assert runtime.auto_exposure_status()["metering_state"] == "searching"  # clipped: no pass
    runtime._swing_exposure_step(1011.0)
    assert _triggers(runtime).count("after_shot") == 1


def test_an_unchanged_light_after_a_shot_holds_and_restarts_the_idle_wait(tmp_path, monkeypatch):
    runtime = _locked(_runtime(tmp_path), now=1000.0)
    _save_shot(runtime, IN_BAND)
    runtime._swing_exposure_step(1200.0)
    calls = _counting_cycles(runtime, monkeypatch)

    for second in range(1, 300):
        runtime._swing_exposure_step(1200.0 + second)
    assert calls == []
    runtime._swing_exposure_step(1500.0)

    assert calls == ["idle_recheck"]
    assert runtime._camera.controls == []
    assert runtime.auto_exposure_status()["metering_state"] == "locked"


def test_an_after_shot_change_waits_while_the_next_clip_is_collected(tmp_path):
    runtime = _locked(_runtime(tmp_path))
    _save_shot(runtime, CLIPPED)
    swing._add_frames(runtime, IN_BAND, count=runtime.settings.pre_frames)
    assert runtime._ring.trigger()  # the next swing's clip is being collected

    assert runtime._swing_exposure_step(1010.0) is None
    assert runtime._camera.controls == []
    assert runtime.auto_exposure_status()["capture_deferred"] is True

    swing._add_frames(runtime, IN_BAND, count=runtime.settings.post_frames)
    runtime._ring.pop_capture()
    decision = runtime._swing_exposure_step(1011.0)

    assert decision.should_apply
    assert runtime._camera.controls[-1]["ExposureTime"] == decision.target.exposure_us
    assert _triggers(runtime).count("after_shot") == 1


def test_one_change_after_a_shot_stays_locked_but_a_further_change_searches(tmp_path):
    runtime = _locked(_runtime(tmp_path))
    _save_shot(runtime, BRIGHTER)
    waiting = runtime._swing_exposure_step(1010.0)
    assert waiting.target is None and waiting.hysteresis["pending_direction"] == "darker"
    assert runtime.auto_exposure_status()["metering_state"] == "locked"

    _save_shot(runtime, BRIGHTER)
    applied = runtime._swing_exposure_step(1050.0)
    assert applied.should_apply
    assert runtime.auto_exposure_status()["metering_state"] == "locked"

    _save_shot(runtime, BRIGHTER)  # still too bright at the new setting
    runtime._swing_exposure_step(1090.0)
    assert runtime.auto_exposure_status()["metering_state"] == "searching"


def test_a_lost_ball_after_a_shot_returns_to_the_one_second_search(tmp_path, monkeypatch):
    runtime = _locked(_runtime(tmp_path))
    runtime.set_resting_ball_locator(swing._not_seen())
    _save_shot(runtime, IN_BAND)

    decision = runtime._swing_exposure_step(1010.0)

    assert decision.reading is None
    assert runtime.auto_exposure_status()["metering_state"] == "searching"
    calls = _counting_cycles(runtime, monkeypatch)
    runtime._swing_exposure_step(1011.0)
    runtime._swing_exposure_step(1012.0)
    assert calls == ["searching", "searching"]


def test_a_restarted_search_returns_to_the_one_second_cycle(tmp_path, monkeypatch):
    runtime = _locked(_runtime(tmp_path))
    calls = _counting_cycles(runtime, monkeypatch)

    runtime.restart_ball_search("the setup measured a new light index")
    runtime.set_resting_ball_locator(swing._not_seen())
    runtime._swing_exposure_step(1001.0)
    runtime._swing_exposure_step(1002.0)

    assert calls == ["searching", "searching"]
    assert runtime.auto_exposure_status()["metering_state"] == "searching"


def test_a_shot_from_a_moved_box_restarts_the_search(tmp_path):
    runtime = _runtime(tmp_path, locator=False)
    look = {
        "state": "in_box",
        "center_px": [swing.BALL[0], swing.BALL[1]],
        "diameter_px": swing.BALL[2],
        "box_px": [40, 40, 80, 80],
    }
    runtime.set_resting_ball_locator(lambda: {**look, "observed_at": time.time() + 1.0})
    _locked(runtime)
    look["box_px"] = [50, 50, 90, 90]
    restarts = []
    runtime._swing_policy.restart_search = restarts.append

    _save_shot(runtime, IN_BAND)
    runtime._swing_exposure_step(1010.0)

    assert restarts == ["the box moved to [50, 50, 90, 90]"]


def test_the_no_ball_sweep_still_runs_while_searching(tmp_path):
    runtime = _runtime(tmp_path, locator=False)
    runtime.set_resting_ball_locator(swing._not_seen())

    decisions = []
    for second in range(3):
        swing._add_frames(runtime, 5)
        decisions.append(runtime._swing_exposure_step(float(second)))

    assert [item.status for item in decisions] == [
        "holding_no_ball",
        "holding_no_ball",
        "searching_for_ball",
    ]
    assert runtime.auto_exposure_status()["metering_state"] == "searching"


def test_the_seed_is_recorded_as_the_seed(tmp_path):
    runtime = _runtime(tmp_path, light_index_dn_per_us_gain=0.1)

    runtime._seed_swing_exposure()

    status = runtime.auto_exposure_status()
    assert (status["status"], status["metering_trigger"]) == ("seeded", "seed")
    assert status["metering_state"] == "searching"
    assert _triggers(runtime) == ["seed"]


def test_a_shot_taken_at_older_controls_is_not_metered(tmp_path):
    runtime = _runtime(tmp_path)
    _save_shot(runtime, CLIPPED, exposure_us=120, gain=4.0)  # a sweep moved on since

    runtime._swing_exposure_step(10.0)

    assert "after_shot" not in _triggers(runtime)
    assert runtime._camera.controls == []


@pytest.mark.parametrize("held", ["manual", "still_photo"])
def test_held_controls_are_never_metered_after_a_shot(tmp_path, held):
    if held == "manual":
        runtime = _runtime(tmp_path, auto_exposure=False)
    else:
        runtime = swing._runtime(
            tmp_path, runtime_class=TesterCaptureRuntime, exposure_us=200, gain=8.0
        )
        runtime._still_hold = (200, 8.0, "capture")
    _save_shot(runtime, CLIPPED)

    assert runtime._swing_exposure_step(10.0) is None
    assert runtime._camera.controls == []
    status = runtime.auto_exposure_status()
    assert status["recent_decisions"] == []
    if held == "manual":
        assert "metering_state" not in status
        assert runtime._resting_balls == {}  # no clip frames kept while the ladder holds


def test_an_unclaimed_clip_is_not_metered(tmp_path):
    """A clip no OPS shot claims (a false trigger) never moves the controls."""
    runtime = _locked(_runtime(tmp_path))
    swing._add_frames(runtime, CLIPPED, count=runtime.settings.pre_frames)
    assert runtime.notify_trigger()
    swing._add_frames(runtime, CLIPPED, count=runtime.settings.post_frames)
    runtime._ready.put(runtime._ring.pop_capture())
    runtime._ready.put(None)
    runtime._save_loop()

    assert runtime._swing_exposure_step(1010.0) is None
    assert runtime._camera.controls == []


def test_unclaimed_clips_keep_only_a_few_resting_frame_sets(tmp_path):
    runtime = _runtime(tmp_path)
    for _ in range(capture_runtime.MAX_PENDING_SAVES + 2):
        swing._add_frames(runtime, IN_BAND, count=runtime.settings.pre_frames)
        assert runtime.notify_trigger()
        swing._add_frames(runtime, IN_BAND, count=runtime.settings.post_frames)
        runtime._ready.put(runtime._ring.pop_capture())
        runtime._ready.put(None)
        runtime._save_loop()

    assert len(runtime._resting_balls) == capture_runtime.MAX_PENDING_SAVES
    newest = max(runtime._resting_balls)
    assert runtime.capture_for_shot(None, timeout_s=0.0).sequence == 1  # evicted: not metered
    assert runtime._after_shot is None
    assert newest in runtime._resting_balls


def test_the_idle_wait_is_the_owner_s_five_minutes():
    assert capture_runtime.SWING_EXPOSURE_IDLE_RECHECK_S == 300.0
