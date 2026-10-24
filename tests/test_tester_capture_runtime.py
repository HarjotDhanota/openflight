"""The tester's camera runtime: held and still controls, and the evidence frozen per trigger."""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from openflight.camera import capture_runtime, swing_exposure as sx
from openflight.camera.capture_runtime import CameraCaptureSettings, SavedCameraCapture
from openflight.camera.tester_capture_runtime import TesterCaptureRuntime
from tests import test_camera_swing_exposure_runtime as swing
from tests.test_camera_swing_exposure_runtime import LIMITS, _add_frames
from tests.test_camera_trigger_lock import _deliver


def _runtime(tmp_path, **overrides):
    return swing._runtime(tmp_path, runtime_class=TesterCaptureRuntime, **overrides)


def _lock_runtime(tmp_path, provider):
    """The trigger-lock tests' small runtime (tests/test_camera_trigger_lock.py), the tester's."""
    runtime = TesterCaptureRuntime(
        output_dir=tmp_path,
        settings=CameraCaptureSettings(width=3, height=2, fps=1000.0, pre_ms=2.0, post_ms=2.0),
        vertical_offset_path=tmp_path / "missing",
        trigger_evidence_provider=provider,
    )
    runtime._running = True
    return runtime


def test_a_held_rung_is_applied_live_without_restarting_the_ring(tmp_path):
    runtime = _runtime(tmp_path, auto_exposure=False, exposure_us=300, gain=2.0)
    _add_frames(runtime, 53)
    ring = runtime._ring

    applied = runtime.apply_held_controls(150, 4.0)

    assert applied == {"exposure_us": 150, "gain": 4.0}
    assert runtime._camera.controls[-1] == {"ExposureTime": 150, "AnalogueGain": 4.0}
    assert runtime._ring is ring  # no restart: the ring keeps running
    assert runtime.settings.auto_exposure is False


def test_a_held_rung_waits_for_a_clip_being_collected(tmp_path):
    runtime = _runtime(tmp_path, auto_exposure=False, exposure_us=300, gain=2.0)
    _add_frames(runtime, 53)
    assert runtime._ring.trigger()
    done = threading.Event()

    thread = threading.Thread(
        target=lambda: (runtime.apply_held_controls(150, 4.0, timeout_s=2.0), done.set())
    )
    thread.start()
    assert not done.wait(timeout=0.1)  # the clip's tail is still at the old controls
    _add_frames(runtime, 53, count=8)  # the post-trigger tail completes
    thread.join(timeout=2.0)

    assert done.is_set()
    assert runtime.settings.exposure_us == 150


def test_a_held_rung_refuses_while_the_automatic_exposure_runs(tmp_path):
    runtime = _runtime(tmp_path)

    with pytest.raises(RuntimeError, match="automatic"):
        runtime.apply_held_controls(150, 4.0)


def test_the_automatic_exposure_takes_over_seeded_from_the_light_index(tmp_path, monkeypatch):
    runtime = _runtime(
        tmp_path, auto_exposure=False, exposure_us=75, gain=8.0, light_index_dn_per_us_gain=0.1
    )
    monkeypatch.setattr(capture_runtime, "SWING_EXPOSURE_EVERY_S", 30.0)

    decision = runtime.begin_automatic_exposure()

    expected = sx.choose_setting(0.1, 0.1, runtime._swing_policy.envelope).setting
    assert runtime.settings.auto_exposure is True
    assert (runtime.settings.exposure_us, runtime.settings.gain) == (
        expected.exposure_us,
        expected.gain,
    )
    assert decision["status"] == "seeded"
    assert decision["reason"] == "setup_light_index"
    assert runtime.auto_exposure_status()["status"] == "seeded"
    assert runtime._swing_thread is not None and runtime._swing_thread.is_alive()
    runtime._auto_exposure_stop.set()
    runtime._swing_thread.join(timeout=1.0)


def test_starting_automatic_twice_is_harmless(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path, auto_exposure=False, light_index_dn_per_us_gain=0.1)
    monkeypatch.setattr(capture_runtime, "SWING_EXPOSURE_EVERY_S", 30.0)
    runtime.begin_automatic_exposure()
    thread = runtime._swing_thread

    again = runtime.begin_automatic_exposure()

    assert runtime._swing_thread is thread
    assert again["status"] == "seeded"
    runtime._auto_exposure_stop.set()
    thread.join(timeout=1.0)


def test_the_sensor_limits_are_readable_for_the_free_play_ladder(tmp_path):
    runtime = _runtime(tmp_path)

    assert runtime.sensor_limits == LIMITS


def test_still_controls_hold_off_the_automatic_exposure_until_released(tmp_path):
    runtime = _runtime(tmp_path, exposure_us=300, gain=12.0)

    held = runtime.hold_still_controls(4000, 2.0)

    assert held == {"exposure_us": 4000, "gain": 2.0}
    assert runtime._camera.controls[-1] == {"ExposureTime": 4000, "AnalogueGain": 2.0}
    assert runtime.auto_exposure_status()["controls_purpose"] == "still_photo"
    _add_frames(runtime, 250)
    assert runtime._run_auto_exposure_cycle() is None  # the photo's light is never metered
    assert runtime._camera.controls[-1] == {"ExposureTime": 4000, "AnalogueGain": 2.0}

    restored = runtime.release_still_controls()

    assert restored == {"exposure_us": 300, "gain": 12.0}
    assert runtime._camera.controls[-1] == {"ExposureTime": 300, "AnalogueGain": 12.0}
    assert runtime.auto_exposure_status()["controls_purpose"] == "capture"
    assert runtime.release_still_controls() is None


def test_holding_twice_restores_the_swing_controls_not_the_first_photo(tmp_path):
    runtime = _runtime(tmp_path, auto_exposure=False, exposure_us=150, gain=4.0)
    runtime.hold_still_controls(4000, 2.0)
    runtime.hold_still_controls(3000, 1.5)
    assert runtime.release_still_controls() == {"exposure_us": 150, "gain": 4.0}


def test_still_controls_wait_for_a_clip_being_collected(tmp_path):
    runtime = _runtime(tmp_path, auto_exposure=False, exposure_us=300, gain=2.0)
    _add_frames(runtime, 53)
    assert runtime._ring.trigger()
    with pytest.raises(RuntimeError, match="clip"):
        runtime.hold_still_controls(4000, 2.0, timeout_s=0.05)
    assert runtime.settings.exposure_us == 300
    assert runtime.release_still_controls() is None


def test_the_admission_copy_is_independent_of_the_queued_evidence(tmp_path):
    runtime = _lock_runtime(tmp_path, lambda _timestamp: {"warnings": [{"id": "lis3dh"}]})
    _deliver(runtime, 1, 2)

    assert runtime.notify_trigger(10.0) is True
    queued = runtime._trigger_evidence.get_nowait()
    queued["warnings"].clear()

    assert runtime.trigger_evidence_for_shot(10.0) == {"warnings": [{"id": "lis3dh"}]}


@pytest.mark.parametrize(
    ("timestamps", "impact", "selected"),
    [((99.0, 100.0, 100.4), 100.4, 100.4), ((100.0, 100.4), 100.3, 100.4)],
)
def test_capture_and_evidence_choose_the_same_nearest_trigger(
    tmp_path, timestamps, impact, selected
):
    runtime = TesterCaptureRuntime(output_dir=tmp_path)
    runtime._captures = [
        SavedCameraCapture(i, stamp, stamp + 0.1, tmp_path, {"trigger": stamp})
        for i, stamp in enumerate(timestamps)
    ]
    runtime._admission_evidence = [(stamp, {"trigger": stamp}) for stamp in timestamps]

    capture = runtime.capture_for_shot(impact, timeout_s=0.0)
    evidence = runtime.trigger_evidence_for_shot(impact)

    assert capture.trigger_timestamp == selected
    assert evidence == capture.metadata
    assert runtime.capture_for_shot(100.0, timeout_s=0.0).trigger_timestamp == 100.0


def test_shot_admission_uses_frozen_trigger_evidence_after_provider_recovers(tmp_path):
    current = {"ready": False, "blockers": [{"id": "lis3dh", "reason": "moving"}]}
    runtime = TesterCaptureRuntime(
        output_dir=tmp_path, trigger_evidence_provider=lambda _timestamp: current
    )
    runtime._running = True
    runtime._ring = SimpleNamespace(trigger=lambda _timestamp: True)

    runtime.notify_trigger(10.0)
    current["ready"] = True

    assert runtime.trigger_evidence_for_shot(10.0)["ready"] is False


def test_equal_distance_triggers_are_not_consumed_as_evidence(tmp_path):
    runtime = TesterCaptureRuntime(output_dir=tmp_path)
    runtime._admission_evidence = [(stamp, {"trigger": stamp}) for stamp in (100.0, 100.5)]

    assert runtime.trigger_evidence_for_shot(100.25) is None
    assert runtime.trigger_evidence_for_shot(100.5) == {"trigger": 100.5}


def test_a_refused_trigger_freezes_no_evidence(tmp_path):
    runtime = _lock_runtime(tmp_path, lambda _timestamp: {"ready": True})
    _deliver(runtime, 1, 1)  # the pre-trigger ring is not full yet

    assert runtime.notify_trigger(10.0) is False
    assert runtime._admission_evidence == []
