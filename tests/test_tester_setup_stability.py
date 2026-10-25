"""The jumpy setup of 24 Oct (outdoor test 1), server side.

The owner: "I would do a step, and then it would keep resetting or the page would
jump ... It would find the ball, I'd click accept, and then it would cancel again
and start over." Steps 3 and 4 reset whenever one LIS3DH reading crossed 0.5 deg
of a single unsettled snapshot, or was momentarily not still; the page's GETs then
answered ``setup_not_confirmed``. These tests hold the server to the fix: a settled
baseline, a move latched only when it persists, polls that never revoke on a
moment's motion, read-only GETs that never queue behind a Save, a box drawn at its
confirmed tilt, and a ball confirmed only on a fresh look.
"""

# the shared fixture ``inputs`` is imported, then named as each test's parameter
# ruff: noqa: F811

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from openflight.camera import tester_server as ts
from openflight.camera.setup_eligibility import SetupEligibility
from tests.test_tester_box_contract import inputs  # noqa: F401  (fixture)
from tests.test_tester_tee_range_flow import app_for, box_state, confirm_box, post

STILL = {
    "status": "stable",
    "pitch_deg": 0.2,
    "camera_pitch_deg": 0.2,
    "roll_deg": -1.8,
    "x_g": 0.0,
    "y_g": 0.0,
    "z_g": 1.0,
}


class ScriptedTilt:
    """An enclosure whose readings the test sets; the last one repeats."""

    bus = 1
    address = 0x18
    zero_offset_deg = 0.0

    def __init__(self, *readings):
        self.readings = [dict(item) for item in (readings or (STILL,))]
        self.calls = 0

    def reading(self):
        self.calls += 1
        if len(self.readings) > 1:
            return dict(self.readings.pop(0))
        return dict(self.readings[0])

    def set(self, reading):
        self.readings = [dict(reading)]

    def start(self):
        return None

    def stop(self):
        return None


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


# --- the confirmation's baseline -------------------------------------------------


def test_the_baseline_is_the_median_of_still_readings_over_its_window():
    clock = Clock()
    rolls = iter([-2.25, -1.79, -1.81, -1.77, -1.80, -1.83, -1.78] + [-1.80] * 40)
    readings = []

    def read():
        roll = next(rolls)
        reading = {**STILL, "roll_deg": roll}
        readings.append(reading)
        return reading

    baseline = ts.median_reading(read, window_s=2.5, interval_s=0.2, sleep=clock.sleep, clock=clock)

    assert baseline["status"] == "stable"
    # the one snapshot of the unit being set down no longer is the baseline
    assert baseline["roll_deg"] == pytest.approx(-1.80, abs=0.01)
    assert baseline["baseline_samples"] == len(readings)
    assert baseline["baseline_window_s"] == pytest.approx(2.5, abs=0.21)


def test_the_baseline_skips_readings_that_are_not_still_and_falls_back_to_the_last():
    clock = Clock()
    script = iter([{"status": "moving"}, {**STILL, "roll_deg": -1.7}, {"status": "moving"}] * 20)
    baseline = ts.median_reading(
        lambda: next(script), window_s=1.0, interval_s=0.2, sleep=clock.sleep, clock=clock
    )
    assert baseline["status"] == "stable"
    assert baseline["roll_deg"] == pytest.approx(-1.7)

    clock = Clock()
    never = ts.median_reading(
        lambda: {"status": "moving"}, window_s=1.0, interval_s=0.2, sleep=clock.sleep, clock=clock
    )
    assert never == {"status": "moving"}


def test_the_confirm_route_stores_the_settled_baseline(tmp_path, monkeypatch):
    monkeypatch.setattr(ts, "CONFIRM_BASELINE_WINDOW_S", 0.3)
    monkeypatch.setattr(ts, "CONFIRM_BASELINE_INTERVAL_S", 0.05)
    tilt = ScriptedTilt(STILL)
    client = ts.create_app(
        sessions_root=tmp_path, rig_geometry=ts.DEFAULT_RIG_GEOMETRY, tilt=tilt
    ).test_client()
    tester = "stability-1"
    config_hash = client.get(
        "/api/tester/setup-eligibility", query_string={"tester_id": tester}
    ).get_json()["config_hash"]
    # the unit is still being set down when Confirm is pressed, then settles
    tilt.readings = [{**STILL, "roll_deg": -2.25}, *[{**STILL, "roll_deg": -1.80}] * 3]

    confirmed = client.post(
        "/api/tester/setup-eligibility",
        json={
            "tester_id": tester,
            "action": "confirm",
            "config_hash": config_hash,
            "physical_rig_confirmed": True,
        },
    )

    assert confirmed.status_code == 200, confirmed.get_json()
    stored = json.loads((tmp_path / tester / "setup-confirmation.json").read_text("utf-8"))
    assert stored["reading"]["roll_deg"] == pytest.approx(-1.80, abs=0.01)


# --- the polls' reading ----------------------------------------------------------


def test_a_moment_of_motion_is_held_over_from_the_last_still_reading():
    clock = Clock()
    tilt = ScriptedTilt(STILL)
    steady = ts.SteadyReading(tilt.reading, hold_s=10.0, window_s=5.0, clock=clock)
    assert steady.reading() == STILL

    clock.now += 1.0
    tilt.set({"status": "moving", "error": None})
    held = steady.reading()
    assert held["status"] == "stable"
    assert held["roll_deg"] == STILL["roll_deg"]
    assert held["held_over"] == "moving"
    assert held["held_s"] == pytest.approx(1.0)

    # held only so long: a unit that stays in motion is in motion
    clock.now += 10.5
    assert steady.reading()["status"] == "moving"


@pytest.mark.parametrize("status", ["off", "sensor_error"])
def test_a_sensor_that_is_off_or_failing_is_never_held_over(status):
    clock = Clock()
    tilt = ScriptedTilt(STILL)
    steady = ts.SteadyReading(tilt.reading, clock=clock)
    steady.reading()
    tilt.set({"status": status, "error": "gone"})
    assert steady.reading()["status"] == status


def test_the_steady_reading_is_the_median_of_the_recent_still_readings():
    clock = Clock()
    tilt = ScriptedTilt(STILL)
    steady = ts.SteadyReading(tilt.reading, window_s=5.0, clock=clock)
    for pitch in (0.10, 0.30, 0.20, 0.25, 0.15):
        tilt.set({**STILL, "pitch_deg": pitch, "camera_pitch_deg": pitch + 1.0})
        steady.reading()
        clock.now += 0.5
    tilt.set({**STILL, "pitch_deg": 0.29, "camera_pitch_deg": 1.29})
    value = steady.steady()
    assert value["pitch_deg"] == pytest.approx(0.225, abs=0.01)
    assert value["camera_pitch_deg"] == pytest.approx(1.225, abs=0.01)
    assert value["steady_samples"] == 6


def confirmed_setup(tmp_path, tilt, clock=None):
    """A real setup policy, confirmed on the tilt's current reading."""
    policy = SetupEligibility(
        Path(ts.DEFAULT_RIG_GEOMETRY),
        tmp_path / "sessions",
        inclinometer_bus=1,
        inclinometer_address=0x18,
        inclinometer_zero_offset_deg=0.0,
        **({"clock": clock} if clock is not None else {}),
    )
    return policy


def test_a_moving_reading_never_takes_the_box_or_the_aim_back_to_step_2(
    tmp_path,
    inputs,
    monkeypatch,
):
    tilt = ScriptedTilt(STILL)
    setup = confirmed_setup(tmp_path, tilt)
    app, tester = app_for(tmp_path, inputs, monkeypatch, setup_policy=setup, tilt=tilt)
    client = app.test_client()
    config_hash = setup.evaluate(tester, STILL)["config_hash"]
    setup.confirm(tester, config_hash, True, STILL)
    assert box_state(client, tester)["state"] == "needs_confirmation"
    assert confirm_box(client, tester, "box").status_code == 200

    tilt.set({"status": "moving", "error": None})
    for _ in range(5):
        assert box_state(client, tester)["state"] == "confirmed"
        aim = client.get("/api/tester/aim-point", query_string={"tester_id": tester}).get_json()
        assert aim["state"] != "setup_not_confirmed"
        eligibility = client.get(
            "/api/tester/setup-eligibility", query_string={"tester_id": tester}
        ).get_json()
        assert eligibility["eligible"], eligibility["blockers"]
        assert eligibility["operator_confirmation"]["confirmation_id"]


def test_one_reading_past_the_tolerance_keeps_the_box_until_the_move_persists(
    tmp_path,
    inputs,
    monkeypatch,
):
    clock = Clock()
    tilt = ScriptedTilt(STILL)
    setup = confirmed_setup(tmp_path, tilt, clock)
    app, tester = app_for(tmp_path, inputs, monkeypatch, setup_policy=setup, tilt=tilt)
    client = app.test_client()
    setup.confirm(tester, setup.evaluate(tester, STILL)["config_hash"], True, STILL)
    assert confirm_box(client, tester, "box").status_code == 200

    knocked = {**STILL, "roll_deg": STILL["roll_deg"] + 2.0}
    tilt.set(knocked)
    for _ in range(3):
        shown = box_state(client, tester)
        assert shown["state"] == "confirmed"
        clock.now += 1.0
    tilt.set(STILL)
    # back where it was: nothing was latched, nothing resets
    assert box_state(client, tester)["state"] == "confirmed"

    tilt.set(knocked)
    for _ in range(4):
        box_state(client, tester)
        clock.now += 1.0
    # a move that lasted is a move: the box asks for the setup again
    assert box_state(client, tester)["state"] == "setup_not_confirmed"


# --- the read-only GETs never queue behind an action -----------------------------


def test_the_box_and_aim_polls_answer_while_a_range_action_holds_the_lock(
    tmp_path,
    inputs,
    monkeypatch,
):
    """Field 24 Oct: the box and aim GETs queued 5.6-5.8 s behind a Save, past the
    page's 5 s timeout; they are read-only and take no part of the range's lock."""
    app, tester = app_for(tmp_path, inputs, monkeypatch)
    client = app.test_client()
    assert post(client, tester, "start", "start").status_code == 200
    entered, release = threading.Event(), threading.Event()
    original = ts.FlowStore.start

    def slow_start(self, *args, **kwargs):
        entered.set()
        release.wait(10.0)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(ts.FlowStore, "start", slow_start)
    worker = threading.Thread(
        target=lambda: app.test_client().post(
            "/api/tester/tee-range",
            json={"tester_id": tester, "action": "start_over", "request_id": "slow"},
        ),
        daemon=True,
    )
    worker.start()
    try:
        assert entered.wait(10.0), "the range action never reached its store"
        started = time.monotonic()
        box = client.get("/api/tester/placement-box", query_string={"tester_id": tester})
        aim = client.get("/api/tester/aim-point", query_string={"tester_id": tester})
        assert box.status_code == 200
        assert aim.status_code == 200
        assert time.monotonic() - started < 2.0
    finally:
        release.set()
        worker.join(10.0)


# --- the confirmed box is drawn at its confirmed tilt ----------------------------


def test_a_confirmed_box_is_drawn_from_its_record_not_the_live_tilt(
    tmp_path,
    inputs,
    monkeypatch,
):
    """Field 24 Oct: the outline was projected again on every GET from the live
    pitch, and moved about 4 px with the LIS3DH's noise."""
    tilt = ScriptedTilt(STILL)
    app, tester = app_for(tmp_path, inputs, monkeypatch, tilt=tilt)

    def tilted(arm, _rig, reading, *_args):
        return ts.BallPlaneCamera.nominal(
            focal_px=933.0 if arm.width == 1280 else 466.5,
            image_width_px=arm.width,
            image_height_px=arm.height,
            pitch_deg=float(reading.get("camera_pitch_deg") or 0.0),
            roll_correction_deg=0.0,
            mirror_horizontal=False,
            camera_origin_lfu=(0.0, 0.0, 0.095),
            radar_origin_lfu=(0.0, -0.03, 0.051),
            angular_uncertainty_deg=0.1,
            focal_relative_uncertainty=0.01,
        )

    monkeypatch.setattr(ts, "_reference_ball_camera", tilted)
    client = app.test_client()
    assert confirm_box(client, tester, "box").status_code == 200
    first = box_state(client, tester)

    tilt.set({**STILL, "camera_pitch_deg": STILL["camera_pitch_deg"] + 0.6})
    for _ in range(8):
        again = box_state(client, tester)
        assert again["state"] == "confirmed"
        assert again["box_outline_px"] == first["box_outline_px"]
        assert again["box_outline_px"] == first["box"]["box_outline_px"]
    # and in 640x400, the same record halved
    halved = client.get(
        "/api/tester/placement-box", query_string={"tester_id": tester, "arm_id": "arm6"}
    ).get_json()
    flat = lambda points: [value for point in points for value in point]  # noqa: E731
    assert flat(halved["box_outline_px"]) == pytest.approx(
        [value / 2.0 for value in flat(first["box_outline_px"])], abs=0.1
    )


@pytest.mark.parametrize("miss", ["not_found", "lis3dh_moving"])
def test_the_step_6_streak_survives_a_missed_look(monkeypatch, miss):
    """Field 24 Oct: the ball range's stable streak started over on any look that
    did not select the ball, or any moment the LIS3DH was not still."""
    from tests.test_tester_server import (  # pylint: disable=import-outside-toplevel
        _guided_camera,
        _guided_patch,
        _guided_result,
    )

    current = {"result": _guided_result()}
    monkeypatch.setattr(ts, "estimate_patch_ball", lambda *_args, **_kwargs: current["result"])
    still = {"status": "stable", "camera_pitch_deg": 0.0, "roll_deg": 0.0}
    orientation = dict(still)
    analyzer = ts.GuidedRangeAnalyzer(
        _guided_camera(), dict(still), lambda: orientation, _guided_patch()
    )
    frames = np.full((5, 200, 320), 70, dtype=np.uint8)
    for observation_id, at in enumerate((1.0, 1.5, 2.0), start=1):
        ready = analyzer.observe(frames, observation_id, observed_at=at)
    assert ready["save_eligible"] is True

    def missing():
        if miss == "not_found":
            current["result"] = _guided_result(status="not_found")
        else:
            orientation.clear()
            orientation.update({"status": "moving"})

    def back():
        current["result"] = _guided_result()
        orientation.clear()
        orientation.update(still)

    missing()
    missed = analyzer.observe(frames, 4, observed_at=2.5)
    assert missed["save_eligible"] is False
    assert missed["stable_count"] == 3
    back()
    again = analyzer.observe(frames, 5, observed_at=3.0)
    assert again["save_eligible"] is True, "the streak was kept through one miss"
    assert again["stable_count"] == 4

    missing()
    for observation_id in (6, 7, 8):
        lost = analyzer.observe(frames, observation_id, observed_at=3.0 + observation_id / 2)
    assert lost["stable_count"] == 0, "three misses in a row end the streak"
    back()
    assert analyzer.observe(frames, 9, observed_at=8.0)["stable_count"] == 1


def test_a_range_action_waits_for_a_still_reading_outside_the_lock(
    tmp_path,
    inputs,
    monkeypatch,
):
    """The wait for a still LIS3DH reading (up to 3 s) held the range's lock, and the
    page's tee-range poll queued behind it."""
    app, tester = app_for(tmp_path, inputs, monkeypatch)
    client = app.test_client()
    assert post(client, tester, "start", "start").status_code == 200
    entered, release = threading.Event(), threading.Event()
    settle = ts.settle_reading

    def slow_settle(read, **kwargs):
        entered.set()
        release.wait(10.0)
        return settle(read, **kwargs)

    monkeypatch.setattr(ts, "settle_reading", slow_settle)
    worker = threading.Thread(
        target=lambda: app.test_client().post(
            "/api/tester/tee-range",
            json={"tester_id": tester, "action": "capture_empty", "request_id": "empty"},
        ),
        daemon=True,
    )
    worker.start()
    try:
        assert entered.wait(10.0)
        started = time.monotonic()
        polled = client.get("/api/tester/tee-range", query_string={"tester_id": tester})
        assert polled.status_code == 200
        assert time.monotonic() - started < 2.0
    finally:
        release.set()
        worker.join(10.0)
