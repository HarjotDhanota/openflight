"""LCMF on the exact per-antenna simulator: launch recovery and board orientation.

Only the four-path channel is held to 0.2 deg; the fused angle also averages ``two8``,
which lacks the DG/GD bounces. ``amp=100`` keeps IQ16 snapshots (clipped at 32767 per
I and Q) unclipped at both floor gains.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from openflight.constants import BALL_DIAMETER_MM
from openflight.iwr6843 import Calibration, antennas, estimate_lcmf_v1
from openflight.rig_geometry import RigGeometry
from tests.test_iwr6843_pipeline import range_snapshot_dump, synth_shot

V3_RIG = "config/enclosure_v3_rig_geometry.json"
SPEED_MS = 45.0
MPH_PER_MS = 2.23694
AMP = 100.0
BALL_HEIGHT_M = BALL_DIAMETER_MM / 2000.0  # one radius: the ball rests on the surface
RANGE_RES_M = 6.0 / 128
CHANNEL = "channel_four4_path_tdm_deg"


def _v3():
    setup = RigGeometry.from_json(V3_RIG).enclosure_setup()
    layout = antennas.layout_from_phase_centre(
        phase_centre_height_m=setup.radar_height_m,
        board_rotation_deg=setup.iwr_board_rotation_deg,
        boresight_pitch_deg=setup.iwr_tilt_deg,
    )
    return setup, layout


def _cal(layout, *, tilt_deg, tee_m, ball_height_m, lateral_m) -> Calibration:
    cal = Calibration.identity()
    cal.tilt_rad = math.radians(tilt_deg)
    cal.tee_range_m = tee_m
    cal.tee_ball_height_m = ball_height_m
    cal.lateral_tee_offset_m = lateral_m
    cal.meta["radar_height_m"] = layout.phase_centre_height_m(cal.tilt_rad)
    cal.antennas = layout
    return cal


def _shot(layout, *, launch_deg, tee_m, tilt_deg, ball_height_m, lateral_m, image_gain, seed=0):
    raw = synth_shot(
        speed_ms=SPEED_MS,
        launch_deg=launch_deg,
        tee_m=tee_m,
        tilt_deg=tilt_deg,
        layout=layout,
        ball_height_m=ball_height_m,
        lateral_m=lateral_m,
        image_gain=image_gain,
        noise=4.0,
        amp=AMP,
        seed=seed,
    )
    start_bin = max(0, int(tee_m / RANGE_RES_M) - 8)
    return range_snapshot_dump(raw, start_bin=start_bin, n_bins=80)


@pytest.mark.parametrize("image_gain", [0.35, 0.6])
def test_per_antenna_channel_recovers_launch_on_the_v3_rig(image_gain):
    """0.35 is the LCMF tests' floor gain, 0.6 the two-ray tests'."""
    setup, layout = _v3()
    tilt = setup.iwr_tilt_deg
    lateral = setup.tee_lateral_offset_m
    misses = []
    for tee_m in (1.0, 1.5, 2.0):
        cal = _cal(
            layout, tilt_deg=tilt, tee_m=tee_m, ball_height_m=BALL_HEIGHT_M, lateral_m=lateral
        )
        for launch_deg in (5.0, 10.0, 15.0, 20.0, 30.0):
            for seed in (0, 1, 2):
                raw = _shot(
                    layout,
                    launch_deg=launch_deg,
                    tee_m=tee_m,
                    tilt_deg=tilt,
                    ball_height_m=BALL_HEIGHT_M,
                    lateral_m=lateral,
                    image_gain=image_gain,
                    seed=seed,
                )
                result = estimate_lcmf_v1(raw, cal, ball_speed_mph=SPEED_MS * MPH_PER_MS, club="7i")
                channel = result.components_deg.get(CHANNEL)
                if not result.accepted or channel is None or abs(channel - launch_deg) > 0.2:
                    misses.append((tee_m, launch_deg, seed, result.status, channel))
    assert not misses


def _level_board(rotation_deg: float) -> antennas.AntennaLayout:
    return antennas.AntennaLayout(rotation_deg, 0.30, "test_level")


@pytest.mark.parametrize("image_gain", [0.0, 0.35])
def test_a_mirrored_board_flips_the_fitted_launch_sign(image_gain):
    """A launch from the array's own height reads as descending on an upside-down board."""
    board = _level_board(90.0)
    centre = board.phase_centre_height_m(0.0)
    raw = _shot(
        board,
        launch_deg=3.0,
        tee_m=1.5,
        tilt_deg=0.0,
        ball_height_m=centre,
        lateral_m=0.0,
        image_gain=image_gain,
    )
    fits = {}
    for rotation in (90.0, -90.0):
        cal = _cal(
            _level_board(rotation), tilt_deg=0.0, tee_m=1.5, ball_height_m=centre, lateral_m=0.0
        )
        result = estimate_lcmf_v1(raw, cal, ball_speed_mph=SPEED_MS * MPH_PER_MS, club="7i")
        assert result.accepted, result.status
        fits[rotation] = result.angle_deg
    assert fits[90.0] == pytest.approx(3.0, abs=0.2)
    assert fits[-90.0] < -1.5


def test_a_mirrored_board_misreads_the_v3_shot():
    """A mirrored array reflects angles about the boresight, not the horizon."""
    setup, layout = _v3()
    mirrored = antennas.AntennaLayout(-90.0, layout.rx_row_height_m, "test_mirrored")
    for launch_deg in (5.0, 30.0):
        raw = _shot(
            layout,
            launch_deg=launch_deg,
            tee_m=1.5,
            tilt_deg=setup.iwr_tilt_deg,
            ball_height_m=BALL_HEIGHT_M,
            lateral_m=setup.tee_lateral_offset_m,
            image_gain=0.35,
        )
        readings = []
        for board in (layout, mirrored):
            cal = _cal(
                board,
                tilt_deg=setup.iwr_tilt_deg,
                tee_m=1.5,
                ball_height_m=BALL_HEIGHT_M,
                lateral_m=setup.tee_lateral_offset_m,
            )
            result = estimate_lcmf_v1(raw, cal, ball_speed_mph=SPEED_MS * MPH_PER_MS, club="7i")
            readings.append(result.components_deg[CHANNEL])
        right, wrong = readings
        assert right == pytest.approx(launch_deg, abs=0.2)
        assert abs(wrong - launch_deg) > 10.0


def test_the_v3_tee_starts_at_the_phase_centre():
    """The simulator and the model put the resting ball at the same point."""
    from tests.test_iwr6843_pipeline import synth_ball_position  # noqa: PLC0415

    setup, layout = _v3()
    ball = synth_ball_position(
        0.0,
        speed_ms=SPEED_MS,
        launch_deg=12.0,
        tee_m=1.5,
        layout=layout,
        tilt_deg=setup.iwr_tilt_deg,
        ball_height_m=BALL_HEIGHT_M,
        lateral_m=setup.tee_lateral_offset_m,
    )
    centre = np.array([0.0, 0.0, layout.phase_centre_height_m(math.radians(setup.iwr_tilt_deg))])
    assert np.linalg.norm(ball - centre) == pytest.approx(1.5)
    assert ball[2] == pytest.approx(BALL_HEIGHT_M)
