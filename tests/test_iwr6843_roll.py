"""The unit's roll turns the IWR about its boresight: the angles are un-mixed before use.

Sign: the LIS3DH roll is + when the unit's target-right side is up (``camera_roll``).
The LEVM is turned +90 deg, so its RX row is vertical and TX2 sits half a wavelength
target-right of the TX1/TX3 midpoint: its horizontal baseline. A + roll lifts that
baseline's right end, so a rising ball reads to the right unless the roll is removed.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from openflight.inclinometer.models import gravity_roll_deg
from openflight.iwr6843 import antennas, doa, lcmf
from openflight.iwr6843.calibration import Calibration
from openflight.iwr6843.music import LAM

PITCH_DEG = 10.0
ROLL_DEG = 3.0  # target-right side up
LAYOUT = antennas.AntennaLayout(board_rotation_deg=90.0, rx_row_height_m=0.0444, basis="test")


def _lis3dh_roll_rad(roll_deg: float) -> float:
    """The roll the kiosk reads for a unit rolled this much (enclosure axes, +X right)."""
    roll = math.radians(roll_deg)
    return math.radians(gravity_roll_deg(math.sin(roll), 0.0, math.cos(roll)))


def test_a_target_right_side_up_roll_turns_the_board_counter_clockwise_from_behind():
    tilt = math.radians(PITCH_DEG)
    level = LAYOUT.positions_m(tilt)
    rolled = LAYOUT.positions_m(tilt, _lis3dh_roll_rad(ROLL_DEG))

    def baseline(positions):
        return positions["TX2"] - 0.5 * (positions["TX1"] + positions["TX3"])

    # TX2 is target-right of the TX1/TX3 midpoint; a + roll raises it
    assert baseline(level)[1] > 0.0
    assert baseline(level)[2] == pytest.approx(0.0, abs=1e-9)
    assert baseline(rolled)[2] == pytest.approx(
        baseline(level)[1] * math.sin(math.radians(ROLL_DEG)), rel=1e-6
    )
    # the vertical RX row's top leans target-left
    lean = rolled["RX4"] - rolled["RX1"]
    assert lean[1] < 0.0
    assert math.degrees(math.atan2(-lean[1], lean[2])) == pytest.approx(ROLL_DEG, abs=1e-6)
    # no roll is the layout as it was
    for name, position in LAYOUT.positions_m(tilt, 0.0).items():
        np.testing.assert_allclose(position, level[name], atol=1e-12)


def test_the_world_lateral_takes_the_height_out_of_a_rolled_reading():
    roll = math.radians(ROLL_DEG)
    lateral, height = 0.12, 0.35
    board = lateral * math.cos(roll) + height * math.sin(roll)

    assert antennas.world_lateral_m(board, height, roll) == pytest.approx(lateral)
    assert antennas.world_lateral_m(board, height, 0.0) == pytest.approx(board)


def _synthetic_bearings(roll_rad: float, launch_deg: float = 20.0):
    """TX2 phases a board rolled ``roll_rad`` measures for a ball straight down the line."""
    tilt = math.radians(PITCH_DEG)
    positions = LAYOUT.positions_m(tilt, roll_rad)
    centre = np.array([0.0, 0.0, LAYOUT.phase_centre_height_m(tilt, roll_rad)])
    reference = 0.5 * (positions["TX1"] + positions["TX3"])
    tee = np.array([1.5, 0.0, 0.04])
    times = np.linspace(0.0, 0.03, 8)
    direction = np.array(
        [math.cos(math.radians(launch_deg)), 0.0, math.sin(math.radians(launch_deg))]
    )
    balls = tee[None, :] + 60.0 * times[:, None] * direction[None, :]
    phases = (
        2.0
        * math.pi
        / LAM
        * (
            np.linalg.norm(balls - positions["TX2"], axis=1)
            - np.linalg.norm(balls - reference, axis=1)
        )
    )
    bearings = -doa.tx2_phase_to_axis_angle_rad(phases)
    ranges = np.linalg.norm(balls - centre, axis=1)
    tee_position = (float(tee[0]), 0.0, float(tee[2] - centre[2]))
    return times, ranges, bearings, tee_position


def test_a_rolled_unit_reads_a_straight_rising_ball_straight_once_un_rolled():
    roll = _lis3dh_roll_rad(ROLL_DEG)
    times, ranges, bearings, tee = _synthetic_bearings(roll)

    unrolled = lcmf.launch_direction_deg(times, ranges, bearings, tee, 20.0, roll_rad=roll)
    ignored = lcmf.launch_direction_deg(times, ranges, bearings, tee, 20.0, roll_rad=0.0)

    assert unrolled == pytest.approx(0.0, abs=0.1)
    # left in, the roll mixes the rise into the lateral: the ball reads right
    assert ignored > 0.8
    # the other way round, it reads left
    times, ranges, bearings, tee = _synthetic_bearings(-roll)
    assert lcmf.launch_direction_deg(times, ranges, bearings, tee, 20.0, roll_rad=0.0) < -0.8
    assert lcmf.launch_direction_deg(
        times, ranges, bearings, tee, 20.0, roll_rad=-roll
    ) == pytest.approx(0.0, abs=0.1)


def test_the_vertical_model_places_the_antennas_of_the_rolled_board():
    calibration = Calibration.identity()
    calibration.tilt_rad = math.radians(PITCH_DEG)
    calibration.tee_range_m = 1.6
    calibration.antennas = LAYOUT
    level = lcmf._model_geometry(  # pylint: disable=protected-access
        calibration, ball_speed_mph=100.0, tx_order="normal", tdm_tau_s=doa.TDM_TAU_S
    )
    calibration.roll_rad = math.radians(ROLL_DEG)
    rolled = lcmf._model_geometry(  # pylint: disable=protected-access
        calibration, ball_speed_mph=100.0, tx_order="normal", tdm_tau_s=doa.TDM_TAU_S
    )

    expected = LAYOUT.positions_m(calibration.tilt_rad, calibration.roll_rad)
    tx, _rx = antennas.channel_positions_m(expected, "normal")
    np.testing.assert_allclose(rolled["tx_positions_m"], tx, atol=1e-12)
    assert not np.allclose(rolled["rx_positions_m"], level["rx_positions_m"])
