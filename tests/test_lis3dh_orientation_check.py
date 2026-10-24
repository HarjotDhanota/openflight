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


def enclosure_gravity_left_up(left_up_deg: float) -> tuple[float, float, float]:
    """The enclosure's own reading with its LEFT (target-left) side raised this much."""
    angle = math.radians(left_up_deg)
    return (-math.sin(angle), 0.0, math.cos(angle))


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
