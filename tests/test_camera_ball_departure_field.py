"""The ball-departure detector on real dusk swings (fork only: it loads field frames).

The fixture is six real dusk swings at 1280x800 (64x64 px crops, hand-marked ball
centres). The ball's lit cap and shaded body sit 5-10 DN either side of the mat, far
under a fixed 30 DN threshold, and cancel in a plain mean.
"""

from pathlib import Path

import numpy as np
import pytest

from openflight.camera.club_delivery import _detect_impact_index, camera_contact_time, frame_clock
from openflight.camera.club_motion import ReferenceBall

FIXTURE = Path(__file__).parent / "fixtures" / "ball_departure" / "outdoors-test-5-dusk.npz"

# The ball leaves after frame 18; in swings 3-6 the head already covers its core
# in frame 18, so the last undisturbed frame is 17.
EXPECTED_LAST_UNDISTURBED = {1: 18, 2: 18, 3: 17, 4: 17, 5: 17, 6: 17}


def _swing(archive, swing: int):
    frames = archive[f"s{swing}_frames"]
    x0, y0 = archive[f"s{swing}_crop_offset_xy"]
    bx, by = archive[f"s{swing}_ball_xy"]
    diameter = float(archive["ball_diameter_px"])
    ball = ReferenceBall(
        x=float(bx - x0),
        y=float(by - y0),
        diameter_px=diameter,
        area_px=int(round(np.pi * diameter * diameter / 4)),
    )
    return frames, ball


@pytest.fixture(scope="module")
def archive():
    with np.load(FIXTURE) as data:
        return {key: data[key] for key in data.files}


def test_the_fixture_is_small_and_holds_only_the_ball_crops(archive):
    assert FIXTURE.stat().st_size < 1_000_000
    assert int(archive["swing_count"]) == 6
    for swing in range(1, 7):
        assert archive[f"s{swing}_frames"].shape == (24, 64, 64)
        assert int(archive[f"s{swing}_pre_trigger_count"]) == 18


@pytest.mark.parametrize("swing", range(1, 7))
def test_the_departure_is_found_on_every_dusk_swing(archive, swing):
    frames, ball = _swing(archive, swing)
    trigger_index = int(archive[f"s{swing}_pre_trigger_count"]) - 1

    found = _detect_impact_index(frames, ball, trigger_index=trigger_index)

    assert found == EXPECTED_LAST_UNDISTURBED[swing]


@pytest.mark.parametrize("swing", range(1, 7))
def test_the_departure_needs_no_trigger_hint(archive, swing):
    frames, ball = _swing(archive, swing)

    assert _detect_impact_index(frames, ball) == EXPECTED_LAST_UNDISTURBED[swing]


def test_the_contact_time_comes_back_on_a_dusk_swing(archive):
    """The contact time is found on a dusk swing."""
    frames, ball = _swing(archive, 1)
    clock, source = frame_clock(archive["s1_host_timestamp_ns"], archive["s1_sensor_timestamp_ns"])

    contact = camera_contact_time(frames, clock, ball, trigger_index=17, timestamp_source=source)

    assert contact is not None
    assert source == "sensor_timestamp_ns"
    assert contact.impact_frame == 18
    sensor = archive["s1_sensor_timestamp_ns"]
    assert contact.contact_ns == pytest.approx((int(sensor[18]) + int(sensor[19])) / 2)
