"""The capture runtime's swing exposure: the resting ball meters, before every swing.

The runtime reads the sensor's limits at configure, seeds from the setup's light
index, then re-checks the resting ball between clips for the next swing.
"""

import json
import threading
import time

import numpy as np
import pytest

from openflight.camera import capture_runtime, swing_exposure as sx
from openflight.camera.capture_runtime import CameraCaptureRuntime, CameraCaptureSettings
from openflight.camera.triggered_buffer import CameraFrame

LIMITS = sx.SensorLimits(exposure_min_us=9, exposure_max_us=8_000, gain_min=1.0, gain_max=15.9375)
CAP = 15.9375
BALL = (60.0, 60.0, 20.0)


class FakeCamera:
    def __init__(self, controls=None):
        self.controls = []
        self.camera_controls = controls if controls is not None else {}

    def set_controls(self, controls):
        self.controls.append(controls)


def _runtime(
    tmp_path, *, limits=LIMITS, locator=True, runtime_class=CameraCaptureRuntime, **overrides
):
    settings = {
        "width": 1280,
        "height": 800,
        "fps": 120.0,
        "pre_ms": 30.0,
        "post_ms": 10.0,
        "exposure_us": 300,
        "gain": 12.0,
        "sensor_limits_path": tmp_path / "camera-sensor-limits.json",
        **overrides,
    }
    runtime = runtime_class(output_dir=tmp_path, settings=CameraCaptureSettings(**settings))
    runtime._camera = FakeCamera()
    runtime._running = True
    runtime.configure_swing_exposure(limits)
    if locator:
        runtime.set_resting_ball_locator(
            lambda: {
                "state": "in_box",
                "center_px": [BALL[0], BALL[1]],
                "diameter_px": BALL[2],
                "observed_at": time.time() + 1.0,  # newer than any change in the test
            }
        )
    return runtime


def _ball_image(signal_dn, *, black=16.0, background=20):
    image = np.full((120, 120), background, dtype=np.uint8)
    yy, xx = np.indices(image.shape)
    image[np.hypot(xx - BALL[0], yy - BALL[1]) <= BALL[2] / 2.0] = int(
        min(255, round(black + signal_dn))
    )
    return image


def _add_frames(runtime, signal_dn, *, count=4, exposure_us=None, gain=None):
    exposure = runtime.settings.exposure_us if exposure_us is None else exposure_us
    gain = runtime.settings.gain if gain is None else gain
    for _ in range(count):
        index = runtime._frame_counter = getattr(runtime, "_frame_counter", 0) + 1
        runtime._ring.add_frame(
            CameraFrame(
                image=_ball_image(signal_dn),
                sensor_timestamp_ns=index * 8_333_333,
                host_timestamp_ns=index * 8_333_333 + 100,
                exposure_us=exposure,
                analogue_gain=gain,
            )
        )


def _light_at(runtime, index):
    """Ball DN above black for a light index (DN per us x gain) at the controls in force."""
    return index * runtime.settings.exposure_us * runtime.settings.gain


def test_the_sensor_limits_are_read_from_the_camera_and_recorded(tmp_path):
    runtime = CameraCaptureRuntime(
        output_dir=tmp_path,
        settings=CameraCaptureSettings(
            width=640,
            height=400,
            fps=288.0,
            sensor_limits_path=tmp_path / "limits.json",
            lens={"lens_id": "6mm"},
        ),
    )
    camera = FakeCamera({"ExposureTime": (4, 3_000, 300), "AnalogueGain": (1.0, 15.9375, 1.0)})

    runtime.configure_swing_exposure(capture_runtime.read_sensor_limits(camera))

    recorded = sx.recorded_sensor_limits(tmp_path / "limits.json", 640, 400)
    assert recorded["exposure_min_us"] == 4
    assert recorded["lens_id"] == "6mm"
    status = runtime.auto_exposure_status()
    assert status["sensor_limits"]["exposure_min_us"] == 4
    assert status["blur_ceiling_us"] == 294


def test_a_camera_without_exposure_limits_holds_its_controls_and_says_so(tmp_path):
    runtime = _runtime(tmp_path, limits=None)

    runtime._seed_swing_exposure()
    _add_frames(runtime, 53)
    decision = runtime._run_auto_exposure_cycle()

    assert decision is None
    assert runtime._camera.controls == []
    status = runtime.auto_exposure_status()
    assert status["status"] == "unavailable"
    assert "limits" in status["message"]


def test_the_setup_light_index_seeds_the_first_setting(tmp_path):
    runtime = _runtime(tmp_path, light_index_dn_per_us_gain=0.1)

    runtime._seed_swing_exposure()

    expected = sx.choose_setting(0.1, 0.1, runtime._swing_policy.envelope).setting
    assert (runtime.settings.exposure_us, runtime.settings.gain) == (
        expected.exposure_us,
        expected.gain,
    )
    status = runtime.auto_exposure_status()
    assert status["status"] == "seeded"
    assert status["reason"] == "setup_light_index"


def test_640x400_seeds_from_twice_the_setup_light(tmp_path):
    full = _runtime(tmp_path / "a", light_index_dn_per_us_gain=0.1)
    half = _runtime(
        tmp_path / "b", width=640, height=400, fps=288.0, light_index_dn_per_us_gain=0.1
    )

    full._seed_swing_exposure()
    half._seed_swing_exposure()

    product_full = full.settings.exposure_us * full.settings.gain
    product_half = half.settings.exposure_us * half.settings.gain
    assert product_half == pytest.approx(product_full / 2.0, rel=0.03)
    assert half.auto_exposure_status()["light_index"]["median"] == pytest.approx(0.2)


def test_without_a_light_index_a_seed_past_the_ceiling_moves_inside_it(tmp_path):
    runtime = _runtime(tmp_path, exposure_us=1000, gain=4.0)

    runtime._seed_swing_exposure()

    assert (runtime.settings.exposure_us, runtime.settings.gain) == (294, 13.61)


def test_a_remembered_light_index_seeds_the_next_start(tmp_path):
    state = tmp_path / "camera-exposure.json"
    first = _runtime(tmp_path, auto_exposure_state_path=state)
    _add_frames(first, 53)
    first._run_auto_exposure_cycle()
    saved = json.loads(state.read_text(encoding="utf-8"))
    assert saved["version"] == 2
    assert saved["light_index_median"] == pytest.approx(53 / (300 * 12.0), rel=0.02)

    second = _runtime(tmp_path, auto_exposure_state_path=state, exposure_us=1000, gain=4.0)
    second._seed_swing_exposure()

    assert second.auto_exposure_status()["reason"] == "remembered_light_index"
    assert (second.settings.exposure_us, second.settings.gain) == (294, CAP)


def test_today_s_room_moves_to_the_blur_ceiling_and_the_gain_cap(tmp_path):
    runtime = _runtime(tmp_path)
    _add_frames(runtime, 53)

    decision = runtime._run_auto_exposure_cycle()

    assert decision.status == "ball_driven"
    assert runtime._camera.controls[-1] == {"ExposureTime": 294, "AnalogueGain": CAP}
    status = runtime.auto_exposure_status()
    assert status["exposure_us"] == 294
    assert status["analysis_eligible"] is True
    assert status["blur_ceiling_us"] == 294
    assert status["reading"]["signal_dn"] == pytest.approx(53.0)


def test_a_recheck_before_each_swing_changes_the_next_shot_s_controls(tmp_path):
    runtime = _runtime(tmp_path, exposure_us=104, gain=8.0)
    room = 0.12  # DN above black per us x gain on the ball's peak
    _add_frames(runtime, _light_at(runtime, room))
    runtime._run_auto_exposure_cycle()
    _add_frames(runtime, _light_at(runtime, room))
    assert runtime.notify_trigger()
    first_shot = runtime._trigger_auto_exposure.get_nowait()
    runtime._ring.pop_capture()
    runtime._ring = type(runtime._ring)(runtime.settings.pre_frames, runtime.settings.post_frames)

    # the sun comes out between swings: the ball clips at the old setting
    _add_frames(runtime, 240)
    decision = runtime._run_auto_exposure_cycle()
    _add_frames(runtime, 240)
    assert runtime.notify_trigger()
    second_shot = runtime._trigger_auto_exposure.get_nowait()

    assert decision.should_apply
    assert second_shot["exposure_us"] * second_shot["gain"] < (
        first_shot["exposure_us"] * first_shot["gain"]
    )
    assert second_shot["exposure_us"] == decision.target.exposure_us


def test_too_bright_at_the_floor_labels_the_shot_and_never_blocks(tmp_path):
    runtime = _runtime(tmp_path, exposure_us=9, gain=1.0)
    _add_frames(runtime, 240)

    decision = runtime._run_auto_exposure_cycle()

    assert decision.status == "too_bright"
    assert runtime._camera.controls == []
    status = runtime.auto_exposure_status()
    assert status["analysis_eligible"] is False
    assert status["frame_label"] == sx.TOO_BRIGHT_LABEL
    assert status["analysis_eligibility"]["reason"] == sx.TOO_BRIGHT_LABEL


def test_too_dark_at_the_ceiling_labels_the_shot(tmp_path):
    runtime = _runtime(tmp_path, exposure_us=294, gain=CAP)
    _add_frames(runtime, 5)

    decision = runtime._run_auto_exposure_cycle()

    assert decision.status == "too_dark"
    assert runtime.auto_exposure_status()["frame_label"] == sx.TOO_DARK_LABEL


def test_no_ball_holds_the_setting_and_says_why(tmp_path):
    runtime = _runtime(tmp_path, locator=False)
    runtime.set_resting_ball_locator(
        lambda: {"state": "not_seen", "reason": "no ball in the box", "observed_at": time.time()}
    )
    _add_frames(runtime, 53)

    decision = runtime._run_auto_exposure_cycle()

    assert decision.status == "holding_no_ball"
    assert "no ball in the box" in decision.message
    assert runtime._camera.controls == []


def test_a_stale_look_is_not_a_ball(tmp_path):
    runtime = _runtime(tmp_path, locator=False)
    runtime.set_resting_ball_locator(
        lambda: {
            "state": "in_box",
            "center_px": [BALL[0], BALL[1]],
            "diameter_px": BALL[2],
            "observed_at": time.time() - 60.0,
        }
    )
    _add_frames(runtime, 53)

    decision = runtime._run_auto_exposure_cycle()

    assert decision.status == "holding_no_ball"
    assert "old" in decision.message


def test_without_a_box_the_setup_ball_spot_is_checked(tmp_path, monkeypatch):
    runtime = _runtime(
        tmp_path,
        locator=False,
        setup_ball={"x": BALL[0], "y": BALL[1], "diameter_px": BALL[2]},
    )
    seen = {}

    def ball_light(frames, black_floor, expected_ball):
        seen["expected"] = dict(expected_ball)
        return {"x": BALL[0], "y": BALL[1], "diameter_px": BALL[2], "found_by": "detector"}

    monkeypatch.setattr(capture_runtime.light_judging, "ball_light", ball_light)
    _add_frames(runtime, 53)

    decision = runtime._run_auto_exposure_cycle()

    assert seen["expected"]["x"] == BALL[0]
    assert decision.reading.source == "setup_ball_detector"
    assert decision.status == "ball_driven"


def test_frames_from_before_a_control_change_are_not_judged(tmp_path):
    runtime = _runtime(tmp_path)
    _add_frames(runtime, 53, exposure_us=150, gain=2.0)  # still the old controls

    decision = runtime._run_auto_exposure_cycle()

    assert decision is None
    assert runtime._camera.controls == []


def test_the_recheck_waits_while_a_clip_is_being_collected(tmp_path):
    runtime = _runtime(tmp_path)
    _add_frames(runtime, 53)
    assert runtime._ring.trigger()

    decision = runtime._run_auto_exposure_cycle()

    assert decision is None
    assert runtime._camera.controls == []
    assert runtime.auto_exposure_status()["capture_deferred"] is True


def test_the_armed_profile_caps_the_policy(tmp_path):
    armed = {"sha256": "a" * 64, "qualified": True, "exposure_ceiling_us": 200, "gain_ceiling": 8.0}
    runtime = _runtime(tmp_path, armed_profile=armed, exposure_us=150, gain=4.0)
    _add_frames(runtime, 10)

    runtime._run_auto_exposure_cycle()

    assert runtime.settings.exposure_us <= 200
    assert runtime.settings.gain <= 8.0


def test_each_decision_is_kept_with_its_inputs(tmp_path):
    runtime = _runtime(tmp_path)
    _add_frames(runtime, 53)
    runtime._run_auto_exposure_cycle()
    _add_frames(runtime, _light_at(runtime, 53 / 3600.0))
    runtime._run_auto_exposure_cycle()

    decisions = runtime.auto_exposure_status()["recent_decisions"]

    assert len(decisions) == 2
    assert decisions[0]["reading"]["exposure_us"] == 300
    assert decisions[0]["target"] == {"exposure_us": 294, "gain": CAP}
    assert decisions[0]["light_index"]["peak"] > 0


def test_the_loop_keeps_checking_after_the_first_decision(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path)
    calls = []
    monkeypatch.setattr(capture_runtime, "SWING_EXPOSURE_EVERY_S", 0.01)
    monkeypatch.setattr(runtime, "_run_auto_exposure_cycle", lambda: calls.append(1))

    thread = threading.Thread(target=runtime._auto_exposure_loop)
    thread.start()
    deadline = time.monotonic() + 2.0
    while len(calls) < 3 and time.monotonic() < deadline:
        time.sleep(0.01)
    runtime._auto_exposure_stop.set()
    thread.join(timeout=1.0)

    assert len(calls) >= 3


def test_a_trigger_waits_for_a_control_change_in_progress(tmp_path):
    controls_started = threading.Event()
    allow_controls = threading.Event()
    trigger_finished = threading.Event()

    class BlockingCamera:
        camera_controls = {}

        @staticmethod
        def set_controls(_controls):
            controls_started.set()
            assert allow_controls.wait(timeout=1.0)

    runtime = _runtime(tmp_path)
    runtime._camera = BlockingCamera()
    _add_frames(runtime, 53)
    exposure_thread = threading.Thread(target=runtime._run_auto_exposure_cycle)
    trigger_thread = threading.Thread(
        target=lambda: (runtime.notify_trigger(), trigger_finished.set()),
    )

    exposure_thread.start()
    assert controls_started.wait(timeout=1.0)
    trigger_thread.start()
    assert not trigger_finished.wait(timeout=0.05)
    allow_controls.set()
    exposure_thread.join(timeout=1.0)
    trigger_thread.join(timeout=1.0)

    assert trigger_finished.is_set()
    trigger_state = runtime._trigger_auto_exposure.get_nowait()
    assert trigger_state["exposure_us"] == runtime.settings.exposure_us == 294


def test_manual_controls_are_not_labelled_by_the_policy(tmp_path):
    runtime = _runtime(tmp_path, auto_exposure=False)
    _add_frames(runtime, 53)

    status = runtime.auto_exposure_status()

    assert status["status"] == "manual"
    assert "analysis_eligibility" not in status
    assert "frame_label" not in status


def test_a_clip_keeps_its_own_decision_not_the_log(tmp_path):
    runtime = _runtime(tmp_path)
    _add_frames(runtime, 53)
    runtime._run_auto_exposure_cycle()
    _add_frames(runtime, _light_at(runtime, 53 / 3600.0))

    assert runtime.notify_trigger()
    recorded = runtime._trigger_auto_exposure.get_nowait()

    assert recorded["recent_decisions"] is None
    assert recorded["status"] == "ball_driven"
    assert recorded["exposure_us"] == 294
    assert runtime.auto_exposure_status()["recent_decisions"]


def _not_seen(box=(40, 40, 80, 80)):
    return lambda: {
        "state": "not_seen",
        "reason": "no ball in the box",
        "observed_at": time.time() + 1.0,  # newer than any change in the test
        "box_px": list(box),
    }


def _look_cycle(runtime, signal=5):
    _add_frames(runtime, signal)
    return runtime._run_auto_exposure_cycle()


def test_three_looks_without_a_ball_start_a_sweep_inside_the_envelope(tmp_path):
    runtime = _runtime(tmp_path, locator=False, exposure_us=104, gain=8.0)
    runtime.set_resting_ball_locator(_not_seen())

    first = _look_cycle(runtime)
    second = _look_cycle(runtime)
    third = _look_cycle(runtime)

    assert first.status == second.status == "holding_no_ball"
    assert third.status == "searching_for_ball"
    assert runtime._camera.controls  # the sweep moved the controls
    applied = runtime._camera.controls[-1]
    envelope = runtime._swing_policy.envelope
    assert envelope.ladder[0] <= applied["ExposureTime"] <= envelope.exposure_ceiling_us
    assert applied["AnalogueGain"] <= envelope.gain_cap
    assert runtime.auto_exposure_status()["status"] == "searching_for_ball"


def test_a_clip_in_progress_pauses_the_sweep(tmp_path):
    runtime = _runtime(tmp_path, locator=False, exposure_us=104, gain=8.0)
    runtime.set_resting_ball_locator(_not_seen())
    for _ in range(3):
        _look_cycle(runtime)
    step = runtime._swing_policy._sweep_index
    controls = list(runtime._camera.controls)
    _add_frames(runtime, 5)
    assert runtime._ring.trigger()

    paused = runtime._run_auto_exposure_cycle()

    assert paused is None
    assert runtime._camera.controls == controls
    assert runtime._swing_policy._sweep_index == step
    assert runtime.auto_exposure_status()["capture_deferred"] is True


def test_a_look_from_before_the_last_change_is_not_judged(tmp_path):
    runtime = _runtime(tmp_path, locator=False, exposure_us=104, gain=8.0)
    runtime.set_resting_ball_locator(_not_seen())
    for _ in range(3):
        _look_cycle(runtime)
    step = runtime._swing_policy._sweep_index
    old = time.time() - 0.5
    runtime.set_resting_ball_locator(
        lambda: {"state": "not_seen", "reason": "no ball in the box", "observed_at": old}
    )

    stale = _look_cycle(runtime)

    assert stale is None
    assert runtime._swing_policy._sweep_index == step


def test_a_moved_box_restarts_the_search_after_a_failed_sweep(tmp_path):
    runtime = _runtime(tmp_path, locator=False, exposure_us=104, gain=8.0)
    runtime.set_resting_ball_locator(_not_seen())
    decision = None
    for _ in range(40):
        decision = _look_cycle(runtime)
        if decision is not None and decision.reason == "no_ball_searched":
            break
    assert decision.reason == "no_ball_searched"
    assert sx.NO_BALL_FOUND_MESSAGE in decision.message
    cooling = _look_cycle(runtime)
    assert cooling.reason == "no_ball_searched" and cooling.target is None

    runtime.set_resting_ball_locator(_not_seen(box=(50, 50, 90, 90)))
    restarted = _look_cycle(runtime)

    assert restarted.status == "searching_for_ball"


def test_manual_exposure_never_sweeps(tmp_path):
    runtime = _runtime(tmp_path, locator=False, auto_exposure=False, exposure_us=104, gain=8.0)
    runtime.set_resting_ball_locator(_not_seen())

    for _ in range(5):
        assert _look_cycle(runtime) is None

    assert runtime._camera.controls == []


def test_a_new_light_index_restarts_the_search_past_the_cool_down(tmp_path):
    runtime = _runtime(tmp_path, locator=False, exposure_us=104, gain=8.0)
    runtime.set_resting_ball_locator(_not_seen())
    for _ in range(40):
        decision = _look_cycle(runtime)
        if decision is not None and decision.reason == "no_ball_searched":
            break

    runtime.restart_ball_search("the setup measured a new light index")
    restarted = _look_cycle(runtime)

    assert restarted.status == "searching_for_ball"


class _StartCamera(FakeCamera):
    """A Picamera2 stand-in that records the order of configure, controls and start."""

    calls: list = []

    def __init__(self):
        super().__init__({"ExposureTime": (9, 8_000, 300), "AnalogueGain": (1.0, 15.9375, 1.0)})
        self.configured = None

    def create_video_configuration(self, **kwargs):
        return kwargs

    def configure(self, config):
        self.configured = config
        _StartCamera.calls.append(("configure", config["controls"]["ExposureTime"]))

    def set_controls(self, controls):
        super().set_controls(controls)
        _StartCamera.calls.append(("set_controls", controls.get("ExposureTime")))

    def start(self):
        _StartCamera.calls.append(("start", None))

    def stop(self):
        pass

    def close(self):
        pass


def test_automatic_startup_applies_the_seed_before_the_first_frame(tmp_path, monkeypatch):
    import sys
    import types

    _StartCamera.calls = []
    fake = types.ModuleType("picamera2")
    fake.Picamera2 = _StartCamera
    monkeypatch.setitem(sys.modules, "picamera2", fake)
    monkeypatch.setattr(capture_runtime, "ensure_picamera2_import_path", lambda: None)
    monkeypatch.setattr(capture_runtime, "resolved_camera_config", lambda _camera: {})
    monkeypatch.setattr(capture_runtime, "SWING_EXPOSURE_EVERY_S", 30.0)
    runtime = CameraCaptureRuntime(
        output_dir=tmp_path,
        settings=CameraCaptureSettings(
            width=1280,
            height=800,
            fps=120.0,
            exposure_us=8,  # near-black
            gain=1.0,
            light_index_dn_per_us_gain=0.1,
        ),
        use_gpio_trigger=False,
    )
    monkeypatch.setattr(runtime, "_wait_for_prebuffer", lambda: None)
    monkeypatch.setattr(runtime, "_capture_mode_startup_snapshot", lambda: {})

    runtime.start()
    try:
        names = [name for name, _value in _StartCamera.calls]
        seeded = runtime.settings.exposure_us
        assert seeded > 8
        # the seed reaches the sensor before streaming starts: no frame at 8 us x 1
        assert names.index("set_controls") < names.index("start")
        assert ("set_controls", seeded) in _StartCamera.calls
    finally:
        runtime.stop()
