"""The tester page's LIS3DH orientation check: level and lift verdicts, the guided check."""

from __future__ import annotations

import math

import pytest

from openflight.inclinometer import guided_check as gc, orientation_check as oc
from tests.test_lis3dh_orientation_check import (
    ALL_MOUNTS,
    LIFT_DEG,
    board_reads,
    enclosure_gravity,
    reading,
)


class TestLevelVerdict:
    def test_upright_passes(self):
        assert gc.level_verdict(reading(0.01, 0.02, 0.99))["state"] == "ok"

    def test_upside_down_asks_for_the_upside_down_correction(self):
        verdict = gc.level_verdict(reading(0.01, 0.02, -0.99))
        assert verdict["state"] == "upside_down"
        assert verdict["suggestion"] == "upside_down"

    def test_on_its_side_cannot_be_judged(self):
        assert gc.level_verdict(reading(0.99, 0.02, 0.05))["state"] == "not_flat"

    def test_no_stable_reading_is_not_judged(self):
        assert gc.level_verdict({"status": "moving"})["state"] == "waiting"
        assert gc.level_verdict({"status": "off", "error": "no I2C"})["state"] == "waiting"


class TestLiftVerdict:
    LEVEL = reading(*enclosure_gravity(0.0))

    def lifted(self, mount_seen: oc.Mount, degrees=LIFT_DEG):
        return reading(*mount_seen.apply(*enclosure_gravity(degrees)))

    def test_front_up_on_plus_y_passes(self):
        verdict = gc.lift_verdict(self.LEVEL, reading(*enclosure_gravity(LIFT_DEG)))
        assert verdict["state"] == "pass"
        assert verdict["front_up_deg"] == pytest.approx(LIFT_DEG, abs=0.05)

    @pytest.mark.parametrize(
        ("seen", "suggestion"),
        [
            ((0.0, -1.0), "turned_around"),
            ((1.0, 0.0), "quarter_left"),
            ((-1.0, 0.0), "quarter_right"),
        ],
    )
    def test_front_up_on_another_axis_names_its_correction(self, seen, suggestion):
        s = math.sin(math.radians(LIFT_DEG))
        lifted = reading(seen[0] * s, seen[1] * s, math.cos(math.radians(LIFT_DEG)))
        verdict = gc.lift_verdict(self.LEVEL, lifted)
        assert verdict["state"] == "needs_correction"
        assert verdict["suggestion"] == suggestion

    def test_a_small_lift_keeps_waiting(self):
        assert gc.lift_verdict(self.LEVEL, reading(*enclosure_gravity(1.0)))["state"] == "waiting"

    def test_a_corner_lift_is_unclear(self):
        s = math.sin(math.radians(LIFT_DEG)) / math.sqrt(2)
        verdict = gc.lift_verdict(self.LEVEL, reading(s, s, 0.99))
        assert verdict["state"] == "unclear"

    def test_a_moving_reading_keeps_waiting(self):
        assert gc.lift_verdict(self.LEVEL, {"status": "moving"})["state"] == "waiting"


@pytest.mark.parametrize("true_mount", ALL_MOUNTS, ids=str)
@pytest.mark.parametrize("assumed", [oc.Mount(), oc.Mount(180.0)], ids=["as-designed", "rig-180"])
def test_the_guided_check_finds_every_flat_mount(true_mount, assumed):
    """Gravity, then one lift, then tap the named correction: it always converges."""
    mount = assumed
    corrections = []
    for _ in range(4):
        level = reading(*mount.apply(*board_reads(true_mount, enclosure_gravity(0.0))))
        verdict = gc.level_verdict(level)
        if verdict["state"] == "upside_down":
            corrections.append("upside_down")
            mount = mount.corrected("upside_down")
            continue
        assert verdict["state"] == "ok"
        lifted = reading(*mount.apply(*board_reads(true_mount, enclosure_gravity(LIFT_DEG))))
        verdict = gc.lift_verdict(level, lifted)
        if verdict["state"] == "pass":
            break
        assert verdict["state"] == "needs_correction"
        corrections.append(verdict["suggestion"])
        mount = mount.corrected(verdict["suggestion"])
    else:
        pytest.fail(f"no pass after {corrections}")
    assert len(corrections) <= 2
    # the found mount reads any enclosure tilt as the enclosure does
    probe = (0.2, -0.1, 0.97)
    assert mount.apply(*board_reads(true_mount, probe)) == pytest.approx(probe)


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class TestGuidedCheck:
    """The tester page's check: start, lift, correct, again, pass."""

    def make(self, tmp_path, true_mount, rig_yaw=180.0):
        state = {"front_up": 0.0, "status": "stable"}
        store = oc.MountStore(tmp_path / "lis3dh-mount.json", rig_yaw_deg=rig_yaw)

        def read():
            if state["status"] != "stable":
                return {"status": state["status"]}
            raw = board_reads(true_mount, enclosure_gravity(state["front_up"]))
            return reading(*store.mount().apply(*raw))

        clock = FakeClock()
        check = gc.GuidedCheck(store, read, clock=clock)
        return check, state, store, clock

    def test_a_board_that_matches_passes_after_one_lift(self, tmp_path):
        check, state, store, _clock = self.make(tmp_path, oc.Mount(180.0))
        assert check.status()["state"] == "not_run"
        assert check.start()["state"] == "lift"
        assert check.status()["state"] == "lift"
        state["front_up"] = LIFT_DEG
        status = check.status()
        assert status["state"] == "passed"
        assert status["front_up_deg"] == pytest.approx(LIFT_DEG, abs=0.05)
        assert store.passed() is True

    def test_a_quarter_turned_board_is_named_then_passes(self, tmp_path):
        check, state, store, _clock = self.make(tmp_path, oc.Mount(90.0))
        check.start()
        state["front_up"] = LIFT_DEG
        status = check.status()
        assert status["state"] == "needs_correction"
        assert status["suggestion"] == "turned_around" or status["suggestion"].startswith("quarter")
        state["front_up"] = 0.0
        after = check.correct(status["suggestion"])
        assert after["state"] == "lift"
        state["front_up"] = LIFT_DEG
        assert check.status()["state"] == "passed"
        assert store.mount() == oc.Mount(90.0)

    def test_an_upside_down_board_is_caught_before_the_lift(self, tmp_path):
        check, state, store, _clock = self.make(tmp_path, oc.Mount(180.0, True))
        status = check.start()
        assert status["state"] == "upside_down"
        assert status["suggestion"] == "upside_down"
        assert check.correct("upside_down")["state"] in {"lift", "needs_correction"}

    def test_no_lift_within_the_time_says_so(self, tmp_path):
        check, _state, _store, clock = self.make(tmp_path, oc.Mount(180.0))
        check.start()
        clock.now += gc.LIFT_TIMEOUT_S + 1
        assert check.status()["state"] == "no_lift"

    def test_no_stable_reading_at_the_start_waits_then_says_so(self, tmp_path):
        check, state, _store, clock = self.make(tmp_path, oc.Mount(180.0))
        state["status"] = "moving"
        assert check.start()["state"] == "level"
        clock.now += gc.LEVEL_TIMEOUT_S + 1
        assert check.status()["state"] == "no_reading"

    def test_a_correction_needs_a_known_name(self, tmp_path):
        check, _state, _store, _clock = self.make(tmp_path, oc.Mount(180.0))
        with pytest.raises(ValueError):
            check.correct("sideways")

    def test_a_correction_hook_runs_with_the_new_mount(self, tmp_path):
        seen = []
        store = oc.MountStore(tmp_path / "m.json", rig_yaw_deg=0.0)
        check = gc.GuidedCheck(
            store,
            lambda: reading(0.0, 0.0, 1.0),
            on_mount_change=seen.append,
            clock=FakeClock(),
        )
        check.correct("turned_around")
        assert seen == [oc.Mount(180.0)]

    def test_the_status_carries_the_mount_and_the_thresholds(self, tmp_path):
        check, _state, _store, _clock = self.make(tmp_path, oc.Mount(180.0))
        status = check.status()
        assert status["mount"] == {"yaw_deg": 180.0, "upside_down": False}
        assert status["mount_source"] == "rig_file"
        assert status["thresholds"]["lift_min_deg"] == gc.LIFT_MIN_DEG
        assert status["passed"] is False
