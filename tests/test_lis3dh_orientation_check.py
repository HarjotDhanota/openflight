"""The LIS3DH orientation check: the board's mount, found by gravity and one lift."""

from __future__ import annotations

import itertools
import json
import math

import pytest

from openflight.inclinometer import (
    AccelerationSample,
    MountedAccelerometer,
    orientation_check as oc,
)

LIFT_DEG = 6.0  # about 2 cm under the front of a 20 cm deep unit


def enclosure_gravity(front_up_deg: float = 0.0) -> tuple[float, float, float]:
    """What a sensor in the enclosure's own axes reads, the front raised this much."""
    angle = math.radians(front_up_deg)
    return (0.0, math.sin(angle), math.cos(angle))


def board_reads(true_mount: oc.Mount, enclosure: tuple[float, float, float]):
    """The raw board reading for a board mounted ``true_mount`` in the enclosure."""
    return true_mount.inverse_apply(*enclosure)


def reading(x, y, z, status="stable"):
    return {"status": status, "x_g": x, "y_g": y, "z_g": z}


ALL_MOUNTS = [
    oc.Mount(yaw, flip) for yaw, flip in itertools.product((0, 90, 180, 270), (False, True))
]


class TestMount:
    def test_the_identity_mount_reads_the_board_as_it_is(self):
        assert oc.Mount().apply(0.1, 0.2, 0.97) == pytest.approx((0.1, 0.2, 0.97))

    def test_a_turned_round_board_reads_x_and_y_reversed(self):
        assert oc.Mount(180.0).apply(0.1, 0.2, 0.97) == pytest.approx((-0.1, -0.2, 0.97))

    def test_upside_down_turns_gravity_back_up(self):
        assert oc.Mount(0.0, True).apply(0.1, 0.2, -0.97) == pytest.approx((-0.1, 0.2, 0.97))

    @pytest.mark.parametrize("mount", ALL_MOUNTS, ids=str)
    def test_inverse_apply_undoes_apply(self, mount):
        assert mount.apply(*mount.inverse_apply(0.3, -0.2, 0.9)) == pytest.approx((0.3, -0.2, 0.9))

    @pytest.mark.parametrize("mount", ALL_MOUNTS, ids=str)
    def test_the_accelerometer_wrapper_reads_what_the_mount_says(self, mount):
        class Board:
            def read(self, *, timestamp=None):
                return AccelerationSample(1.0, 0.3, -0.2, 0.9)

        sample = MountedAccelerometer(Board(), mount.yaw_deg, upside_down=mount.upside_down).read()

        assert (sample.x_g, sample.y_g, sample.z_g) == pytest.approx(mount.apply(0.3, -0.2, 0.9))

    @pytest.mark.parametrize("mount", ALL_MOUNTS, ids=str)
    @pytest.mark.parametrize("correction", sorted(oc.CORRECTIONS))
    def test_a_correction_composes_after_the_mount(self, mount, correction):
        corrected = mount.corrected(correction)
        step = oc.CORRECTION_TRANSFORMS[correction]
        raw = (0.3, -0.2, 0.9)

        assert corrected.apply(*raw) == pytest.approx(step.apply(*mount.apply(*raw)))

    def test_the_yaw_stays_within_a_turn(self):
        assert oc.Mount(270.0).corrected("quarter_left").yaw_deg == 0.0
        assert oc.Mount(0.0).corrected("quarter_right").yaw_deg == 270.0

    def test_unknown_corrections_are_refused(self):
        with pytest.raises(ValueError):
            oc.Mount().corrected("sideways")


class TestLevelVerdict:
    def test_upright_passes(self):
        assert oc.level_verdict(reading(0.01, 0.02, 0.99))["state"] == "ok"

    def test_upside_down_asks_for_the_upside_down_correction(self):
        verdict = oc.level_verdict(reading(0.01, 0.02, -0.99))
        assert verdict["state"] == "upside_down"
        assert verdict["suggestion"] == "upside_down"

    def test_on_its_side_cannot_be_judged(self):
        assert oc.level_verdict(reading(0.99, 0.02, 0.05))["state"] == "not_flat"

    def test_no_stable_reading_is_not_judged(self):
        assert oc.level_verdict({"status": "moving"})["state"] == "waiting"
        assert oc.level_verdict({"status": "off", "error": "no I2C"})["state"] == "waiting"


class TestLiftVerdict:
    LEVEL = reading(*enclosure_gravity(0.0))

    def lifted(self, mount_seen: oc.Mount, degrees=LIFT_DEG):
        return reading(*mount_seen.apply(*enclosure_gravity(degrees)))

    def test_front_up_on_plus_y_passes(self):
        verdict = oc.lift_verdict(self.LEVEL, reading(*enclosure_gravity(LIFT_DEG)))
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
        verdict = oc.lift_verdict(self.LEVEL, lifted)
        assert verdict["state"] == "needs_correction"
        assert verdict["suggestion"] == suggestion

    def test_a_small_lift_keeps_waiting(self):
        assert oc.lift_verdict(self.LEVEL, reading(*enclosure_gravity(1.0)))["state"] == "waiting"

    def test_a_corner_lift_is_unclear(self):
        s = math.sin(math.radians(LIFT_DEG)) / math.sqrt(2)
        verdict = oc.lift_verdict(self.LEVEL, reading(s, s, 0.99))
        assert verdict["state"] == "unclear"

    def test_a_moving_reading_keeps_waiting(self):
        assert oc.lift_verdict(self.LEVEL, {"status": "moving"})["state"] == "waiting"


@pytest.mark.parametrize("true_mount", ALL_MOUNTS, ids=str)
@pytest.mark.parametrize("assumed", [oc.Mount(), oc.Mount(180.0)], ids=["as-designed", "rig-180"])
def test_the_guided_check_finds_every_flat_mount(true_mount, assumed):
    """Gravity, then one lift, then tap the named correction: it always converges."""
    mount = assumed
    corrections = []
    for _ in range(4):
        level = reading(*mount.apply(*board_reads(true_mount, enclosure_gravity(0.0))))
        verdict = oc.level_verdict(level)
        if verdict["state"] == "upside_down":
            corrections.append("upside_down")
            mount = mount.corrected("upside_down")
            continue
        assert verdict["state"] == "ok"
        lifted = reading(*mount.apply(*board_reads(true_mount, enclosure_gravity(LIFT_DEG))))
        verdict = oc.lift_verdict(level, lifted)
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


class TestMountStore:
    def test_without_a_file_the_rig_files_mount_stands(self, tmp_path):
        store = oc.MountStore(tmp_path / "lis3dh-mount.json", rig_yaw_deg=180.0)
        assert store.mount() == oc.Mount(180.0)
        assert store.source() == "rig_file"
        assert store.passed() is False

    def test_a_correction_is_saved_and_voids_the_last_pass(self, tmp_path):
        path = tmp_path / "unit" / "lis3dh-mount.json"
        store = oc.MountStore(path, rig_yaw_deg=180.0)
        store.record_pass({"front_up_deg": 6.0})
        assert store.passed() is True

        store.apply_correction("quarter_left")

        again = oc.MountStore(path, rig_yaw_deg=180.0)
        assert again.mount() == oc.Mount(270.0)
        assert again.source() == "unit_file"
        assert again.passed() is False
        document = json.loads(path.read_text(encoding="utf-8"))
        assert document["schema"] == oc.SCHEMA
        assert document["corrections"][-1]["correction"] == "quarter_left"
        assert document["corrections"][-1]["from"] == {"yaw_deg": 180.0, "upside_down": False}

    def test_a_pass_persists_with_the_mount_it_passed_under(self, tmp_path):
        path = tmp_path / "lis3dh-mount.json"
        oc.MountStore(path, rig_yaw_deg=0.0).record_pass({"front_up_deg": 5.5})

        again = oc.MountStore(path, rig_yaw_deg=0.0)
        assert again.passed() is True
        assert again.record()["check"]["mount"] == {"yaw_deg": 0.0, "upside_down": False}

    def test_a_file_from_elsewhere_is_ignored(self, tmp_path):
        path = tmp_path / "lis3dh-mount.json"
        path.write_text('{"schema": "something else", "mount": {"yaw_deg": 90}}', encoding="utf-8")
        assert oc.MountStore(path, rig_yaw_deg=180.0).mount() == oc.Mount(180.0)

    def test_memory_only_without_a_path(self):
        store = oc.MountStore(None, rig_yaw_deg=0.0)
        store.apply_correction("turned_around")
        assert store.mount() == oc.Mount(180.0)

    def test_the_effective_mount_reads_the_unit_file_first(self, tmp_path):
        path = tmp_path / "lis3dh-mount.json"
        oc.MountStore(path, rig_yaw_deg=180.0).apply_correction("upside_down")
        mount, source, record = oc.effective_mount(path, 180.0)
        assert mount == oc.Mount(180.0).corrected("upside_down")
        assert source == "unit_file"
        assert record["check"] is None


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
        check = oc.GuidedCheck(store, read, clock=clock)
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
        clock.now += oc.LIFT_TIMEOUT_S + 1
        assert check.status()["state"] == "no_lift"

    def test_no_stable_reading_at_the_start_waits_then_says_so(self, tmp_path):
        check, state, _store, clock = self.make(tmp_path, oc.Mount(180.0))
        state["status"] = "moving"
        assert check.start()["state"] == "level"
        clock.now += oc.LEVEL_TIMEOUT_S + 1
        assert check.status()["state"] == "no_reading"

    def test_a_correction_needs_a_known_name(self, tmp_path):
        check, _state, _store, _clock = self.make(tmp_path, oc.Mount(180.0))
        with pytest.raises(ValueError):
            check.correct("sideways")

    def test_a_correction_hook_runs_with_the_new_mount(self, tmp_path):
        seen = []
        store = oc.MountStore(tmp_path / "m.json", rig_yaw_deg=0.0)
        check = oc.GuidedCheck(
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
        assert status["thresholds"]["lift_min_deg"] == oc.LIFT_MIN_DEG
        assert status["passed"] is False
