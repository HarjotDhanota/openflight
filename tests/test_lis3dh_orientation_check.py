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


def reading(x, y, z, status="stable"):
    return {"status": status, "x_g": x, "y_g": y, "z_g": z}


ALL_MOUNTS = [
    oc.Mount(yaw, flip) for yaw, flip in itertools.product((0, 90, 180, 270), (False, True))
]


class TestMount:
    @pytest.mark.parametrize(
        ("mount", "raw", "reads"),
        [
            (oc.Mount(), (0.1, 0.2, 0.97), (0.1, 0.2, 0.97)),
            (oc.Mount(180.0), (0.1, 0.2, 0.97), (-0.1, -0.2, 0.97)),  # turned round
            (oc.Mount(0.0, True), (0.1, 0.2, -0.97), (-0.1, 0.2, 0.97)),  # upside down
            (oc.Mount(90.0), (0.1, 0.0, 0.99), (0.0, 0.1, 0.99)),  # board X to enclosure Y
        ],
    )
    def test_a_mount_reads_the_board_in_the_enclosures_axes(self, mount, raw, reads):
        assert mount.apply(*raw) == pytest.approx(reads, abs=1e-12)

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


class TestRollVerified:
    """The roll is applied only on a unit whose file says the roll step passed."""

    def test_a_roll_pass_is_saved_after_the_front_pass(self, tmp_path):
        path = tmp_path / "lis3dh-mount.json"
        store = oc.MountStore(path, rig_yaw_deg=180.0)
        store.record_pass({"front_up_deg": 6.0})
        assert store.roll_verified() is False

        store.record_roll({"left_up_deg": 6.0, "roll_change_deg": -6.0}, verified=True)

        again = oc.MountStore(path, rig_yaw_deg=180.0)
        assert again.roll_verified() is True
        assert again.record()["roll_verified"] is True
        # verified but not zeroed: the roll is still not applied
        assert again.roll_calibration() == {
            "verified": True,
            "zero_deg": None,
            "residual_deg": None,
            "applied": False,
        }
        document = json.loads(path.read_text(encoding="utf-8"))
        assert document["roll_verified"] is True
        assert document["check"]["roll"]["state"] == "passed"

    def test_a_reversed_roll_is_recorded_unverified(self, tmp_path):
        store = oc.MountStore(tmp_path / "m.json", rig_yaw_deg=180.0)
        store.record_pass({"front_up_deg": 6.0})
        store.record_roll({"left_up_deg": 6.0}, verified=False)
        assert store.roll_verified() is False
        assert store.record()["check"]["roll"]["state"] == "reversed"

    def test_a_correction_or_a_new_front_pass_voids_the_roll(self, tmp_path):
        store = oc.MountStore(tmp_path / "m.json", rig_yaw_deg=180.0)
        store.record_pass({"front_up_deg": 6.0})
        store.record_roll({}, verified=True)
        store.record_pass({"front_up_deg": 6.1})
        assert store.roll_verified() is False
        store.record_roll({}, verified=True)
        store.apply_correction("turned_around")
        assert store.roll_verified() is False

    def test_no_roll_without_a_front_pass(self, tmp_path):
        store = oc.MountStore(tmp_path / "m.json", rig_yaw_deg=180.0)
        with pytest.raises(ValueError):
            store.record_roll({}, verified=True)
        assert oc.MountStore(None, rig_yaw_deg=0.0).roll_verified() is False
        assert oc.effective_mount(tmp_path / "none.json", 180.0)[2]["roll_verified"] is False


def _surface_reading(surface_roll_deg, facing_deg, *, seat_roll_deg, offset_x_g):
    """What a board seated ``seat_roll_deg`` with an X zero-g offset reads, in the
    enclosure's axes, on a surface rolled ``surface_roll_deg`` (target-right up,
    for the unit facing forward), the unit turned ``facing_deg`` about the vertical."""
    unit_roll = math.radians(surface_roll_deg) * math.cos(math.radians(facing_deg))
    roll = unit_roll + math.radians(seat_roll_deg)
    return (math.sin(roll) + offset_x_g, 0.0, math.cos(roll))


class TestReversalZero:
    """Turned 180 deg on the same spot, the surface's tilt changes sign; the bias does not."""

    @pytest.mark.parametrize("surface_roll_deg", [-2.4, 0.0, 1.7])
    def test_the_mean_of_the_two_facings_is_the_bias_whatever_the_surface(self, surface_roll_deg):
        from openflight.inclinometer.models import gravity_roll_deg  # noqa: PLC0415

        bias = {"seat_roll_deg": -1.2, "offset_x_g": 0.03}
        first, turned = (
            gravity_roll_deg(*_surface_reading(surface_roll_deg, facing, **bias))
            for facing in (0.0, 180.0)
        )
        zero = oc.reversal_roll_zero(first, turned, first + 0.04, expected_roll_deg=0.0)

        expected_bias = gravity_roll_deg(*_surface_reading(0.0, 0.0, **bias))
        assert zero["roll_zero_deg"] == pytest.approx(expected_bias, abs=0.02)
        assert zero["surface_roll_deg"] == pytest.approx(surface_roll_deg, abs=0.05)
        assert zero["repeatability_deg"] == pytest.approx(0.04)
        # the residual pad is the zero's own repeatability, not a fixed figure
        assert zero["residual_deg"] == pytest.approx(
            0.04 if 0.04 > oc.ROLL_ZERO_MIN_RESIDUAL_DEG else oc.ROLL_ZERO_MIN_RESIDUAL_DEG
        )
        assert zero["repeatable"] is True
        # later, on another surface: reading - zero is the unit's own roll
        later = gravity_roll_deg(*_surface_reading(2.5, 0.0, **bias))
        assert later - zero["roll_zero_deg"] == pytest.approx(2.5, abs=0.05)

    def test_the_zero_is_the_bias_beyond_the_rigs_expected_mount_roll(self):
        zero = oc.reversal_roll_zero(1.5, 0.5, 1.5, expected_roll_deg=0.4)
        assert zero["level_roll_deg"] == pytest.approx(1.0)
        assert zero["roll_zero_deg"] == pytest.approx(0.6)

    def test_a_unit_that_did_not_sit_the_same_way_twice_is_not_zeroed(self):
        zero = oc.reversal_roll_zero(1.0, 0.0, 1.0 + oc.ROLL_ZERO_MAX_REPEATABILITY_DEG + 0.1)
        assert zero["repeatable"] is False


class TestRollZeroStore:
    def verified(self, tmp_path):
        store = oc.MountStore(tmp_path / "m.json", rig_yaw_deg=180.0)
        store.record_pass({"front_up_deg": 6.0})
        store.record_roll({"left_up_deg": 6.0}, verified=True)
        return store

    def test_the_zero_is_saved_and_the_roll_then_applies(self, tmp_path):
        store = self.verified(tmp_path)
        store.record_roll_zero(oc.reversal_roll_zero(-2.0, -3.6, -2.1))

        again = oc.MountStore(tmp_path / "m.json", rig_yaw_deg=180.0)
        calibration = again.roll_calibration()
        assert calibration["applied"] is True
        assert calibration["zero_deg"] == pytest.approx(-2.825)
        assert calibration["residual_deg"] == pytest.approx(0.1)
        document = json.loads((tmp_path / "m.json").read_text(encoding="utf-8"))
        assert document["roll_zero_deg"] == pytest.approx(-2.825)
        assert again.record()["roll_calibration"] == calibration

    def test_no_zero_without_a_verified_roll_or_from_an_unrepeatable_reversal(self, tmp_path):
        store = oc.MountStore(tmp_path / "m.json", rig_yaw_deg=180.0)
        store.record_pass({"front_up_deg": 6.0})
        with pytest.raises(ValueError):
            store.record_roll_zero(oc.reversal_roll_zero(0.0, 0.0, 0.0))
        store = self.verified(tmp_path)
        with pytest.raises(ValueError):
            store.record_roll_zero(oc.reversal_roll_zero(0.0, 0.0, 2.0))

    def test_a_new_check_or_a_correction_voids_the_zero(self, tmp_path):
        store = self.verified(tmp_path)
        store.record_roll_zero(oc.reversal_roll_zero(0.2, 0.0, 0.2))
        store.record_pass({"front_up_deg": 6.0})
        assert store.roll_calibration()["zero_deg"] is None
        store.record_roll({}, verified=True)
        assert store.roll_calibration()["applied"] is False
        store.record_roll_zero(oc.reversal_roll_zero(0.2, 0.0, 0.2))
        store.apply_correction("turned_around")
        assert store.roll_calibration() == {
            "verified": False,
            "zero_deg": None,
            "residual_deg": None,
            "applied": False,
        }
