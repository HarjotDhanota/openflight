"""LIS3DH transport, pitch math, filtering, and snapshot selection tests."""

import math

import pytest

from openflight.inclinometer import (
    LIS3DH,
    AccelerationSample,
    InclinometerService,
    LIS3DHIdentityError,
)
from openflight.inclinometer.service import pitch_degrees, roll_degrees


def _gravity(pitch_deg: float, roll_deg: float) -> tuple[float, float, float]:
    """The 1 g vector a level-mounted LIS3DH reads at this pitch and roll.

    Pitch tilts the forward (+Y) axis up, roll drops the target-right side,
    which moves the sensed up-vector onto -X. The exact inverse of
    `pitch_degrees` / `roll_degrees`, so the tests below pin both formulas
    rather than restating one of them.
    """
    pitch = math.radians(pitch_deg)
    roll = math.radians(roll_deg)
    return (
        -math.sin(roll) * math.cos(pitch),
        math.sin(pitch),
        math.cos(roll) * math.cos(pitch),
    )


def _axis_bytes(value_g: float) -> list[int]:
    counts = round(value_g / 0.001)
    raw = (counts << 4) & 0xFFFF
    return [raw & 0xFF, raw >> 8]


class FakeBus:
    def __init__(self, *, identity=0x33, axes=(0.0, 0.0, 1.0)):
        self.identity = identity
        self.axes = axes
        self.writes = []
        self.closed = False

    def read_byte_data(self, address, register):
        assert address == 0x18
        assert register == LIS3DH.WHO_AM_I
        return self.identity

    def write_byte_data(self, address, register, value):
        self.writes.append((address, register, value))

    def read_i2c_block_data(self, address, register, length):
        assert (address, register, length) == (0x18, 0xA8, 6)
        return sum((_axis_bytes(axis) for axis in self.axes), [])

    def close(self):
        self.closed = True


def test_lis3dh_initializes_high_resolution_xyz_mode_and_reads_signed_axes():
    bus = FakeBus(axes=(-0.125, 0.25, 0.975))
    sensor = LIS3DH(bus=bus)

    sensor.initialize()
    sample = sensor.read(timestamp=123.0)
    sensor.close()

    assert bus.writes == [(0x18, 0x20, 0x57), (0x18, 0x23, 0x88)]
    assert sample == AccelerationSample(123.0, -0.125, 0.25, 0.975)
    assert bus.closed


def test_lis3dh_rejects_wrong_identity():
    sensor = LIS3DH(bus=FakeBus(identity=0x00))

    with pytest.raises(LIS3DHIdentityError, match="expected 0x33, got 0x00"):
        sensor.initialize()


def test_pitch_is_positive_when_sensor_y_tilts_back():
    angle = math.radians(12.0)
    sample = AccelerationSample(1.0, 0.0, math.sin(angle), math.cos(angle))

    assert pitch_degrees(sample) == pytest.approx(12.0)


def test_pitch_and_roll_read_zero_on_a_level_enclosure():
    sample = AccelerationSample(1.0, *_gravity(0.0, 0.0))

    assert pitch_degrees(sample) == pytest.approx(0.0, abs=1e-6)
    assert roll_degrees(sample) == pytest.approx(0.0, abs=1e-6)


def test_ten_degrees_of_tilt_back_is_pitch_only():
    sample = AccelerationSample(1.0, *_gravity(10.0, 0.0))

    assert sample.y_g == pytest.approx(math.sin(math.radians(10.0)), abs=1e-12)
    assert pitch_degrees(sample) == pytest.approx(10.0, abs=1e-6)
    assert roll_degrees(sample) == pytest.approx(0.0, abs=1e-6)


def test_roll_is_positive_when_the_target_right_side_drops():
    sample = AccelerationSample(1.0, *_gravity(0.0, 5.0))

    # Right side down puts the sensed up-vector on -X, which is the sign the
    # roll formula's negation exists for.
    assert sample.x_g < 0.0
    assert roll_degrees(sample) == pytest.approx(5.0, abs=1e-6)
    assert pitch_degrees(sample) == pytest.approx(0.0, abs=1e-6)
    assert roll_degrees(AccelerationSample(1.0, *_gravity(0.0, -5.0))) == pytest.approx(
        -5.0, abs=1e-6
    )


def test_pitch_and_roll_stay_separable_when_the_enclosure_has_both():
    sample = AccelerationSample(1.0, *_gravity(10.0, 5.0))

    assert math.hypot(sample.x_g, sample.y_g, sample.z_g) == pytest.approx(1.0, abs=1e-12)
    assert pitch_degrees(sample) == pytest.approx(10.0, abs=1e-6)
    assert roll_degrees(sample) == pytest.approx(5.0, abs=1e-6)


def test_service_reports_roll_with_its_own_zero_offset():
    service = InclinometerService(
        sensor=None,
        zero_offset_deg=1.5,
        roll_zero_offset_deg=-0.25,
        window_samples=3,
    )
    axes = _gravity(10.0, 5.0)

    for timestamp in (1.0, 1.1, 1.2):
        snapshot = service.add_sample(AccelerationSample(timestamp, *axes))

    assert snapshot.raw_pitch_deg == pytest.approx(10.0, abs=1e-6)
    assert snapshot.calibrated_pitch_deg == pytest.approx(11.5, abs=1e-6)
    assert snapshot.raw_roll_deg == pytest.approx(5.0, abs=1e-6)
    assert snapshot.calibrated_roll_deg == pytest.approx(4.75, abs=1e-6)
    assert snapshot.to_dict()["calibrated_roll_deg"] == 4.75


def test_roll_defaults_to_the_raw_reading_when_no_offset_is_configured():
    service = InclinometerService(sensor=None, window_samples=3)
    axes = _gravity(0.0, -3.0)

    for timestamp in (1.0, 1.1, 1.2):
        snapshot = service.add_sample(AccelerationSample(timestamp, *axes))

    assert snapshot.raw_roll_deg == pytest.approx(-3.0, abs=1e-6)
    assert snapshot.calibrated_roll_deg == pytest.approx(snapshot.raw_roll_deg, abs=1e-12)


def test_service_applies_zero_offset_and_selects_preimpact_snapshot():
    service = InclinometerService(
        sensor=None,
        zero_offset_deg=1.5,
        window_samples=4,
        max_snapshot_age_s=2.0,
    )
    angle = math.radians(3.0)
    for timestamp in (9.0, 9.1, 9.2, 9.3, 10.1):
        service.add_sample(AccelerationSample(timestamp, 0.0, math.sin(angle), math.cos(angle)))

    selection = service.snapshot_for_impact(10.0)

    assert selection.status == "stable"
    assert selection.age_s == pytest.approx(0.7)
    assert selection.snapshot.timestamp == 9.3
    assert selection.snapshot.raw_pitch_deg == pytest.approx(3.0)
    assert selection.snapshot.calibrated_pitch_deg == pytest.approx(4.5)


def test_service_rejects_moving_window_and_stale_snapshot():
    service = InclinometerService(
        sensor=None,
        window_samples=4,
        max_pitch_std_deg=0.2,
        max_snapshot_age_s=1.0,
    )
    for timestamp, angle_deg in zip((1.0, 1.1, 1.2, 1.3), (0.0, 3.0, -2.0, 4.0)):
        angle = math.radians(angle_deg)
        assert (
            service.add_sample(AccelerationSample(timestamp, 0.0, math.sin(angle), math.cos(angle)))
            is None
        )
    assert service.snapshot_for_impact(1.4).status == "no_stable_preimpact_reading"

    for timestamp in (2.0, 2.1, 2.2, 2.3):
        service.add_sample(AccelerationSample(timestamp, 0.0, 0.0, 1.0))
    selection = service.snapshot_for_impact(4.0)
    assert selection.status == "stale"
    assert selection.snapshot is None
    assert selection.age_s == pytest.approx(1.7)


def test_service_rejects_non_gravity_acceleration():
    service = InclinometerService(sensor=None, window_samples=3)

    for timestamp in (1.0, 1.1, 1.2):
        assert service.add_sample(AccelerationSample(timestamp, 0.0, 0.0, 1.5)) is None

    assert service.snapshot_for_impact(1.3).status == "no_stable_preimpact_reading"


def test_service_does_not_reuse_old_position_after_movement():
    service = InclinometerService(sensor=None, window_samples=3, max_pitch_std_deg=0.2)
    for timestamp in (1.0, 1.1, 1.2):
        service.add_sample(AccelerationSample(timestamp, 0.0, 0.0, 1.0))
    assert service.snapshot_for_impact(1.25).status == "stable"

    moved = math.radians(8.0)
    service.add_sample(AccelerationSample(1.3, 0.0, math.sin(moved), math.cos(moved)))

    selection = service.snapshot_for_impact(1.35)
    assert selection.status == "moving"
    assert selection.snapshot is None
