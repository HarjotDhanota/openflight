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
    enclosure_gravity_left_up,
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


class TestLeftLiftVerdict:
    LEVEL = reading(*enclosure_gravity(0.0))

    def test_the_left_side_up_reads_the_roll_down_and_passes(self):
        verdict = gc.left_lift_verdict(self.LEVEL, reading(*enclosure_gravity_left_up(LIFT_DEG)))
        assert verdict["state"] == "pass"
        assert verdict["left_up_deg"] == pytest.approx(LIFT_DEG, abs=0.05)
        assert verdict["roll_change_deg"] == pytest.approx(-LIFT_DEG, abs=0.05)

    def test_a_roll_that_reads_the_other_way_is_reversed(self):
        x, y, z = enclosure_gravity_left_up(LIFT_DEG)
        assert gc.left_lift_verdict(self.LEVEL, reading(-x, y, z))["state"] == "reversed"

    def test_the_front_still_up_or_a_small_lift_keeps_waiting(self):
        front = reading(*enclosure_gravity(LIFT_DEG))
        assert gc.left_lift_verdict(self.LEVEL, front)["state"] == "waiting"
        small = reading(*enclosure_gravity_left_up(1.0))
        assert gc.left_lift_verdict(self.LEVEL, small)["state"] == "waiting"
        assert gc.left_lift_verdict(self.LEVEL, {"status": "moving"})["state"] == "waiting"


SURFACE_ROLL_DEG = 1.5
SEAT_ROLL_DEG = 0.8


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class TestGuidedCheck:
    """The tester page's check: start, lift, correct, again, pass."""

    def make(self, tmp_path, true_mount, rig_yaw=180.0, mirrored_x=False):
        # on a floor rolled 1.5 deg (target-right up), the board seated 0.8 deg rolled
        state = {"front_up": 0.0, "left_up": 0.0, "turned": False, "status": "stable"}
        store = oc.MountStore(tmp_path / "lis3dh-mount.json", rig_yaw_deg=rig_yaw)

        def read():
            if state["status"] != "stable":
                return {"status": state["status"]}
            floor = math.radians(SURFACE_ROLL_DEG) * (-1.0 if state["turned"] else 1.0)
            roll = floor + math.radians(SEAT_ROLL_DEG) - math.radians(state["left_up"])
            front = math.radians(state["front_up"])
            gravity = (
                math.sin(roll) * math.cos(front),
                math.sin(front),
                math.cos(roll) * math.cos(front),
            )
            x, y, z = store.mount().apply(*board_reads(true_mount, gravity))
            return reading(-x if mirrored_x else x, y, z)

        clock = FakeClock()
        check = gc.GuidedCheck(store, read, clock=clock)
        return check, state, store, clock

    def lift_front_then_left(self, check, state):
        state["front_up"] = LIFT_DEG
        after_front = check.status()
        state["front_up"] = 0.0
        down = check.status()
        state["left_up"] = LIFT_DEG
        return after_front, down, check.status()

    def reverse(self, check, state):
        """Put the left side down, turn the unit round and back, tapping after each."""
        state["left_up"] = 0.0
        assert check.status()["state"] == "turn_round"
        state["turned"] = True
        assert check.turned()["state"] == "turn_back"
        state["turned"] = False
        return check.turned()

    def test_a_board_that_matches_passes_the_front_the_left_and_the_zero(self, tmp_path):
        check, state, store, _clock = self.make(tmp_path, oc.Mount(180.0))
        assert check.status()["state"] == "not_run"
        assert check.start()["state"] == "lift"
        assert check.status()["state"] == "lift"
        after_front, down, status = self.lift_front_then_left(check, state)
        assert after_front["state"] == "lift_left"
        assert after_front["front_up_deg"] == pytest.approx(LIFT_DEG, abs=0.1)
        assert after_front["passed"] is True and after_front["roll_verified"] is False
        assert down["state"] == "lift_left"
        # the direction is verified; the roll waits for its zero
        assert status["state"] == "turn_round"
        assert status["roll_verified"] is True and status["roll_applied"] is False
        assert status["left_up_deg"] == pytest.approx(LIFT_DEG, abs=0.1)

        status = self.reverse(check, state)

        assert status["state"] == "passed"
        assert status["roll_applied"] is True
        # the floor's 1.5 deg cancels: the zero is the board's own 0.8 deg
        assert status["roll_zero_deg"] == pytest.approx(SEAT_ROLL_DEG, abs=0.02)
        assert store.roll_calibration()["applied"] is True

    def test_a_reversal_that_did_not_sit_the_same_way_is_tried_again(self, tmp_path):
        check, state, store, _clock = self.make(tmp_path, oc.Mount(180.0))
        check.start()
        self.lift_front_then_left(check, state)
        state["left_up"] = 0.0
        check.status()
        state["turned"] = True
        check.turned()
        state["left_up"] = 2.0  # turned back, but propped up on something
        state["turned"] = False
        status = check.turned()
        assert status["state"] == "zero_unrepeatable"
        assert store.roll_calibration()["applied"] is False
        assert store.roll_verified() is True

    def test_a_tap_outside_the_reversal_is_refused(self, tmp_path):
        check, _state, _store, _clock = self.make(tmp_path, oc.Mount(180.0))
        with pytest.raises(ValueError):
            check.turned()

    def test_a_roll_that_reads_the_wrong_way_is_not_verified(self, tmp_path):
        check, state, store, _clock = self.make(tmp_path, oc.Mount(180.0), mirrored_x=True)
        check.start()
        _front, _down, status = self.lift_front_then_left(check, state)
        assert status["state"] == "roll_reversed"
        assert status["roll_applied"] is False
        assert status["roll_verified"] is False
        assert store.passed() is True and store.roll_verified() is False

    def test_no_left_lift_within_the_time_says_so(self, tmp_path):
        check, state, store, clock = self.make(tmp_path, oc.Mount(180.0))
        check.start()
        state["front_up"] = LIFT_DEG
        check.status()
        clock.now += gc.LIFT_TIMEOUT_S + 1
        assert check.status()["state"] == "no_left_lift"
        assert store.roll_verified() is False

    def test_checking_again_needs_the_roll_again(self, tmp_path):
        check, state, store, _clock = self.make(tmp_path, oc.Mount(180.0))
        check.start()
        self.lift_front_then_left(check, state)
        self.reverse(check, state)
        assert store.roll_calibration()["applied"] is True
        check.start()
        state["front_up"] = LIFT_DEG
        assert check.status()["state"] == "lift_left"
        assert store.roll_verified() is False
        assert store.roll_calibration()["zero_deg"] is None

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
        assert check.status()["state"] == "lift_left"
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
        assert status["roll_verified"] is False
