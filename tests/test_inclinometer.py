"""LIS3DH transport, pitch math, filtering, and snapshot selection tests."""

import math

import pytest

from openflight.inclinometer import (
    LIS3DH,
    AccelerationSample,
    InclinometerService,
    LIS3DHIdentityError,
    MountedAccelerometer,
)
from openflight.inclinometer.service import pitch_degrees


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


class _Board:
    """A sensor that reads one fixed sample and remembers what was asked of it."""

    def __init__(self, sample):
        self.sample = sample
        self.calls = []

    def initialize(self):
        self.calls.append("initialize")

    def read(self, *, timestamp=None):
        self.calls.append(("read", timestamp))
        return self.sample

    def close(self):
        self.calls.append("close")


def test_a_board_turned_round_reads_the_front_tipped_up_as_up():
    # the enclosure's front tipped up 3.3 deg, read by a board whose +Y
    # arrow points back: the board sees its Y negative
    angle = math.radians(3.3)
    board = _Board(AccelerationSample(1.0, 0.05, -math.sin(angle), math.cos(angle)))

    mounted = MountedAccelerometer(board, 180.0)
    sample = mounted.read(timestamp=2.0)

    assert pitch_degrees(sample) == pytest.approx(3.3, abs=0.01)
    assert sample.x_g == pytest.approx(-0.05)
    assert sample.z_g == pytest.approx(math.cos(angle))
    assert board.calls == [("read", 2.0)]


def test_a_quarter_turn_moves_the_boards_x_into_the_enclosures_y():
    board = _Board(AccelerationSample(0.0, 0.1, 0.0, 0.99))

    sample = MountedAccelerometer(board, 90.0).read()

    assert sample.x_g == pytest.approx(0.0, abs=1e-12)
    assert sample.y_g == pytest.approx(0.1)


def test_the_mounting_passes_start_and_stop_through():
    board = _Board(AccelerationSample(0.0, 0.0, 0.0, 1.0))
    mounted = MountedAccelerometer(board, 180.0)

    mounted.initialize()
    mounted.close()

    assert board.calls == ["initialize", "close"]


# A shot's reading: the newest stable one before the swing; an older one, or one the
# swing shook, stands only when the next stable reading after impact agrees with it.


def _tilted(timestamp, pitch_deg=2.0, roll_deg=0.0):
    pitch, roll = math.radians(pitch_deg), math.radians(roll_deg)
    return AccelerationSample(
        timestamp,
        math.sin(roll),
        math.sin(pitch) * math.cos(roll),
        math.cos(pitch) * math.cos(roll),
    )


def _shake(timestamp):
    return AccelerationSample(timestamp, 0.4, 0.3, 1.3)


def _service():
    return InclinometerService(sensor=None, window_samples=4, max_snapshot_age_s=2.0)


def _feed(service, samples):
    for sample in samples:
        service.add_sample(sample)


def _steady(start, stop, **orientation):
    count = round((stop - start) / 0.1) + 1
    return [_tilted(round(start + 0.1 * index, 3), **orientation) for index in range(count)]


def test_a_snapshot_records_its_roll_and_window():
    service = _service()
    _feed(service, _steady(1.0, 1.3, pitch_deg=1.0, roll_deg=-2.5))

    snapshot = service.snapshot_for_impact(1.4).snapshot

    assert snapshot.roll_deg == pytest.approx(-2.5)
    assert snapshot.window_start_timestamp == 1.0
    assert snapshot.to_dict()["roll_deg"] == pytest.approx(-2.5)


def test_a_shot_takes_the_recent_steady_reading_at_once():
    service = _service()
    _feed(service, _steady(1.0, 1.9))

    selection = service.snapshot_for_shot(2.2, wait_s=0.0)

    assert selection.status == "stable"
    assert selection.rule == "recent_stable"
    assert selection.snapshot.timestamp == 1.9
    assert selection.to_dict()["rule"] == "recent_stable"


def test_swing_vibration_does_not_cost_the_correction():
    service = _service()
    _feed(service, _steady(1.0, 1.9))
    _feed(service, [_shake(t) for t in (2.0, 2.1, 2.2, 2.3)])
    _feed(service, _steady(2.4, 3.0))

    selection = service.snapshot_for_shot(2.2, wait_s=0.0)

    assert selection.status == "stable"
    assert selection.rule == "confirmed_after_impact"
    assert selection.snapshot.timestamp == 1.9
    assert selection.confirmation["timestamp"] == 2.7
    assert selection.confirmation["pitch_change_deg"] == pytest.approx(0.0, abs=1e-6)


def test_a_reading_up_to_a_minute_old_stands_once_confirmed():
    service = _service()
    _feed(service, _steady(1.0, 1.3))
    _feed(service, _steady(50.1, 50.4, pitch_deg=2.2, roll_deg=0.2))

    selection = service.snapshot_for_shot(50.0, wait_s=0.0)

    assert selection.status == "stable"
    assert selection.rule == "confirmed_after_impact"
    assert selection.age_s == pytest.approx(48.7)


@pytest.mark.parametrize("moved", [{"pitch_deg": 2.4}, {"roll_deg": 0.4}])
def test_a_unit_that_moved_gets_no_correction(moved):
    service = _service()
    _feed(service, _steady(1.0, 1.9))
    _feed(service, [_shake(t) for t in (2.0, 2.1, 2.2, 2.3)])
    _feed(service, _steady(2.4, 2.7, **{"pitch_deg": 2.0, **moved}))

    selection = service.snapshot_for_shot(2.2, wait_s=0.0)

    assert selection.status == "moved"
    assert selection.snapshot is None
    assert selection.reason == "the unit moved"
    record = selection.to_dict()
    assert record["reason"] == "the unit moved"
    assert "calibrated_pitch_deg" not in record


def test_without_a_reading_after_impact_an_unsteady_one_is_refused():
    service = _service()
    _feed(service, _steady(1.0, 1.9))
    _feed(service, [_shake(t) for t in (2.0, 2.1, 2.2, 2.3)])

    selection = service.snapshot_for_shot(2.2, wait_s=0.0)

    assert selection.status == "unconfirmed"
    assert selection.snapshot is None


def test_a_window_that_spans_impact_does_not_confirm():
    service = _service()
    _feed(service, _steady(1.0, 1.9))
    _feed(service, [_shake(2.0)])
    # one stable window, 2.1-2.4: it began before the 2.15 impact
    _feed(service, _steady(2.1, 2.4))

    assert service.snapshot_for_shot(2.15, wait_s=0.0).status == "unconfirmed"
    _feed(service, _steady(2.5, 2.5))
    assert service.snapshot_for_shot(2.15, wait_s=0.0).rule == "confirmed_after_impact"


def test_a_reading_older_than_a_minute_is_stale():
    service = _service()
    _feed(service, _steady(1.0, 1.3))
    _feed(service, _steady(70.1, 70.5))

    assert service.snapshot_for_shot(70.0, wait_s=0.0).status == "stale"


def test_the_shot_waits_briefly_for_the_reading_after_impact():
    service = _service()
    _feed(service, _steady(1.0, 1.9))
    _feed(service, [_shake(t) for t in (2.0, 2.1, 2.2, 2.3)])
    waited = []

    def sleep(seconds):
        waited.append(seconds)
        _feed(service, _steady(2.4, 2.7))

    selection = service.snapshot_for_shot(2.2, wait_s=1.0, sleep=sleep)

    assert selection.rule == "confirmed_after_impact"
    assert len(waited) == 1


# A still unit is never "moved": the confirming window must be quiet, and the move
# tolerance grows with the two windows' own scatter (the reviewer's heavy-shake case).


def _shaken_shot(seed, *, shake_before, shake_after, moved_deg=0.0, latency=0.1):
    import random  # noqa: PLC0415

    rng = random.Random(seed)
    service = InclinometerService(sensor=None)
    impact = 1000.0
    clock = {"t": impact - 30.0 + rng.random() * 0.1}

    def feed_one():
        t = clock["t"]
        shaky = impact - shake_before <= t <= impact + shake_after
        noise = rng.gauss(0, 2.0) if shaky else rng.gauss(0, 0.02)
        pitch = math.radians(noise + (moved_deg if t > impact else 0.0))
        service.add_sample(AccelerationSample(t, 0.0, math.sin(pitch), math.cos(pitch)))
        clock["t"] += 0.1

    while clock["t"] <= impact + latency:
        feed_one()

    def sleep(_seconds):
        if clock["t"] > impact + latency + 1.0:
            raise TimeoutError
        feed_one()

    try:
        return service.snapshot_for_shot(impact, wait_s=5.0, sleep=sleep).status
    except TimeoutError:
        return "unconfirmed"


@pytest.mark.parametrize("shake", [(0.3, 0.6), (1.0, 1.0)])
def test_heavy_shake_on_a_still_unit_is_never_read_as_moved(shake):
    before, after = shake
    outcomes = {_shaken_shot(seed, shake_before=before, shake_after=after) for seed in range(20)}
    assert "moved" not in outcomes
    # seed 12 was the reviewer's false "moved"
    assert _shaken_shot(12, shake_before=before, shake_after=after) != "moved"


def test_a_real_half_degree_move_after_a_shake_is_still_caught():
    assert _shaken_shot(3, shake_before=0.3, shake_after=0.1, moved_deg=0.5) == "moved"


def test_a_unit_rocking_side_to_side_gives_no_stable_reading():
    service = _service()
    for index in range(12):
        service.add_sample(_tilted(1.0 + 0.1 * index, roll_deg=1.0 if index % 2 else -1.0))
    assert service.snapshot_for_impact(2.2).status == "no_stable_preimpact_reading"
