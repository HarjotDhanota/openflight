"""The session's hitting-surface height and the heights every consumer is handed."""

from __future__ import annotations

import math

import numpy as np
import pytest

from openflight import surface_height as sh

LENS_M = 0.095
RADAR_M = LENS_M - 0.04275
RADIUS = 0.02135


def _refused(code="range_label_not_measured", number=1):
    return {
        "status": "refused",
        "reason_code": code,
        "reason": f"refused: {code}",
        "shot_number": number,
    }


# --- the session aggregate --------------------------------------------------------------


def _contribution(height, random_sigma=0.002, systematic_sigma=0.01, number=1):
    return {
        "status": "solved",
        "surface_height_m": height,
        "random_sigma_m": random_sigma,
        "systematic_sigma_m": systematic_sigma,
        "sigma_m": math.hypot(random_sigma, systematic_sigma),
        "shot_number": number,
        "source": sh.PER_SHOT_SOURCE,
    }


def test_no_accepted_solve_leaves_no_aggregate_but_keeps_the_reasons():
    refused = _refused(number=1)

    combined = sh.aggregate([refused])

    assert combined["status"] == "none"
    assert combined["n"] == 0
    assert combined["surface_height_m"] is None
    assert combined["reasons"] == [
        {"shot_number": 1, "reason_code": "range_label_not_measured", "reason": refused["reason"]}
    ]


def test_the_aggregate_rejects_an_outlier_and_averages_the_rest():
    solves = [_contribution(h, number=i) for i, h in enumerate((0.066, 0.070, 0.068, 0.20), 1)]

    combined = sh.aggregate(solves)

    assert combined["status"] == "solved"
    assert combined["n"] == 3
    assert combined["n_rejected"] == 1
    assert combined["surface_height_m"] == pytest.approx(0.068, abs=1e-6)
    states = {item["shot_number"]: item["state"] for item in combined["contributions"]}
    assert states == {1: "used", 2: "used", 3: "used", 4: "outlier"}


def test_the_camera_s_shared_sigma_does_not_average_down():
    solves = [
        _contribution(0.068, random_sigma=0.004, systematic_sigma=0.01, number=i)
        for i in range(1, 17)
    ]

    combined = sh.aggregate(solves)

    assert combined["random_sigma_m"] == pytest.approx(0.004 / 4.0)
    assert combined["systematic_sigma_m"] == pytest.approx(0.01)
    assert combined["sigma_m"] == pytest.approx(math.hypot(0.001, 0.01))


def test_inverse_variance_weights_the_tighter_shots():
    combined = sh.aggregate(
        [
            _contribution(0.060, random_sigma=0.002, number=1),
            _contribution(0.080, random_sigma=0.006, number=2),
        ]
    )

    weights = np.array([1 / 0.002**2, 1 / 0.006**2])
    assert combined["surface_height_m"] == pytest.approx(
        float(np.dot(weights, [0.060, 0.080]) / weights.sum())
    )


# --- the heights in force -------------------------------------------------------------


def _in_force(combined, own=None):
    return sh.heights_in_force(
        combined,
        rig_lens_height_m=LENS_M,
        rig_radar_height_m=RADAR_M,
        ball_radius_m=RADIUS,
        shot_number=own,
    )


def test_without_a_solve_the_rig_s_heights_stand_labelled_with_the_reasons():
    combined = sh.aggregate([_refused(number=3)])

    heights = _in_force(combined, own=3)

    assert heights["source"] == sh.RIG_NOMINAL
    assert heights["surface_height_above_base_m"] == 0.0
    assert heights["lens_height_m"] == pytest.approx(LENS_M)
    assert heights["radar_height_m"] == pytest.approx(RADAR_M)
    assert heights["ball_height_m"] == pytest.approx(RADIUS)
    assert heights["reflecting_plane_depth_m"] == 0.0
    assert "range_label_not_measured" in heights["reason"]


def test_a_precise_solve_is_used_and_moves_every_height():
    combined = sh.aggregate([_contribution(0.068, random_sigma=0.002, systematic_sigma=0.008)])

    heights = _in_force(combined, own=1)

    assert heights["source"] == sh.SOLVED_PER_SHOT
    assert heights["surface_height_above_base_m"] == pytest.approx(0.068)
    assert heights["lens_height_m"] == pytest.approx(LENS_M - 0.068)
    # the radar may be below the hitting surface: heights are signed
    assert heights["radar_height_m"] == pytest.approx(RADAR_M - 0.068)
    assert heights["radar_height_m"] < 0.0
    # the ball rests on the hitting surface, one radius up
    assert heights["ball_height_m"] == pytest.approx(RADIUS)
    # the radar's floor bounce comes from the base in front of the unit, below it
    assert heights["reflecting_plane_depth_m"] == pytest.approx(0.068)


def test_later_shots_use_the_session_s_aggregate():
    combined = sh.aggregate(
        [
            _contribution(0.066, number=1, systematic_sigma=0.008),
            _contribution(0.070, number=2, systematic_sigma=0.008),
        ]
    )

    assert _in_force(combined, own=2)["source"] == sh.SOLVED_SESSION


def test_a_loose_solve_consistent_with_the_rig_keeps_the_rig_s_heights():
    # 20 mm +- 30 mm: neither precise nor a clear departure from the base
    combined = sh.aggregate([_contribution(0.020, random_sigma=0.005, systematic_sigma=0.030)])

    heights = _in_force(combined, own=1)

    assert heights["source"] == sh.RIG_NOMINAL
    assert "15 mm" in heights["reason"]


def test_a_loose_solve_that_rules_out_the_rig_is_used():
    # 300 mm +- 30 mm: a unit on a box (the old gross-error case), with no 60 mm floor
    combined = sh.aggregate([_contribution(-0.30, random_sigma=0.005, systematic_sigma=0.030)])

    heights = _in_force(combined, own=1)

    assert heights["source"] == sh.SOLVED_PER_SHOT
    assert heights["lens_height_m"] == pytest.approx(LENS_M + 0.30)
    # the surface is below the base: the radar reflects from the hitting surface itself
    assert heights["reflecting_plane_depth_m"] == 0.0


@pytest.mark.parametrize(
    ("offset", "sigma", "used"),
    [
        (0.010, 0.015, True),  # precise: used however small
        (0.010, 0.016, False),  # loose and consistent with the rig
        (0.045, 0.020, True),  # loose but more than two sigma from the rig
    ],
)
def test_the_use_rule(offset, sigma, used):
    assert sh.use_solved(offset, sigma)[0] is used


def test_the_named_bounds():
    assert sh.SURFACE_HEIGHT_MAX_SIGMA_M == pytest.approx(0.015)
    assert sh.NOMINAL_REJECTION_SIGMAS == pytest.approx(2.0)
