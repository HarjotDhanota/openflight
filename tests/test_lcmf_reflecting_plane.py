"""The IWR's floor bounce off the plane below a raised hitting surface (30 Oct).

Heights come above the hitting surface; ``reflecting_plane_depth_m`` says how far below it
the plane the radar's multipath reflects from lies (the base in front of the unit). The
Oct 27 replay chose this arm: research/surface-height-2026-10-30/REPORT.txt section 3.
"""

from __future__ import annotations

import json
import math

import numpy as np
import pytest

from openflight.iwr6843 import antennas, lcmf
from openflight.iwr6843.calibration import Calibration
from openflight.iwr6843.replay import build_replay_calibration

RADAR = 0.05224817918569254
RADIUS = 0.02135
TILT = 10.0


def _calibration(radar_m=RADAR, ball_m=RADIUS, depth_m=0.0):
    calibration = Calibration.identity()
    calibration.meta["radar_height_m"] = radar_m
    calibration.tee_ball_height_m = ball_m
    calibration.tilt_rad = math.radians(TILT)
    calibration.tee_range_m = 1.6
    calibration.antennas = antennas.layout_from_phase_centre(
        phase_centre_height_m=radar_m, board_rotation_deg=90.0, boresight_pitch_deg=TILT
    )
    calibration.reflecting_plane_depth_m = depth_m
    return calibration


# --- LCMF: the floor bounce off the plane below the hitting surface --------------------


def test_lcmf_models_the_bounce_from_the_base_when_the_surface_is_raised():
    # surface 68 mm above the base: the radar is 16 mm below it, the ball one radius up;
    # the reflecting plane (the base in front of the unit) is 68 mm below the surface
    raised = lcmf._model_geometry(
        _calibration(RADAR - 0.068, RADIUS, 0.068),
        ball_speed_mph=100.0,
        tx_order="normal",
        tdm_tau_s=1e-4,
    )
    # the same scene measured from the base: the radar at its rig height, the ball raised
    from_base = lcmf._model_geometry(
        _calibration(RADAR, RADIUS + 0.068, 0.0),
        ball_speed_mph=100.0,
        tx_order="normal",
        tdm_tau_s=1e-4,
    )

    for key in ("phase_centre_height_m", "ball_height_m", "tee_x_m"):
        assert raised[key] == pytest.approx(from_base[key], abs=1e-9)
    np.testing.assert_allclose(raised["tx_positions_m"], from_base["tx_positions_m"], atol=1e-9)
    np.testing.assert_allclose(raised["rx_positions_m"], from_base["rx_positions_m"], atol=1e-9)
    assert raised["reflecting_plane_depth_m"] == pytest.approx(0.068)


def test_lcmf_refuses_a_radar_below_its_reflecting_plane():
    # no plane below the radar is known: a mirror above it is not a model of anything
    with pytest.raises(ValueError, match="radar_below_the_hitting_surface"):
        lcmf._model_geometry(
            _calibration(RADAR - 0.068, RADIUS, 0.0),
            ball_speed_mph=100.0,
            tx_order="normal",
            tdm_tau_s=1e-4,
        )


def test_a_calibration_without_a_plane_depth_keeps_the_old_geometry():
    assert Calibration.identity().reflecting_plane_depth_m == 0.0


def test_a_replay_rebuilds_the_plane_depth_the_shot_recorded(tmp_path):
    path = tmp_path / "calibration.json"
    path.write_text(
        json.dumps(
            {
                "elem_phase_rad": [0.0] * 8,
                "elem_gain": [1.0] * 8,
                "tilt_deg": 0.0,
                "range_bias_const_m": 0.0,
            }
        ),
        encoding="utf-8",
    )

    calibration = build_replay_calibration(
        path,
        tee_range_m=1.6,
        tilt_deg=TILT,
        radar_height_m=RADAR - 0.068,
        ball_height_m=RADIUS,
        board_rotation_deg=90.0,
        effective_snapshot={"reflecting_plane_depth_m": 0.068},
    )

    assert calibration.reflecting_plane_depth_m == pytest.approx(0.068)
