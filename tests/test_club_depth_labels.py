"""G2 C4 (4 Nov 2026): honest labels and gates for the camera club delivery.

Without an accepted IWR club walk the head's depth is ASSUMED: range(t) = the located tee +
the OPS radial club speed x (t - contact) (fusion_processing._club_depth). Path and attack
angle are then sideways / vertical pixel motion over an assumed forward speed, so they are
estimated, with the reason, until a measured depth exists. Under that depth the OPS speed
ratio is >= 1 by construction (the forward speed IS the OPS speed), so it tests nothing and
no longer gates the path; it still gates a measured (IWR) depth. The velocity MAD over the
three hosel points of one rigid anchor tests no identity and is gone.
"""

from __future__ import annotations

import pytest

from openflight import source_labels as sl
from openflight.camera import club_delivery as cd
from openflight.camera.ball_flight import located_tee_range_evidence
from openflight.camera.club_delivery import ApproachPairEstimate, combine_approach_estimates


def _windows(ratio: float, mad: float = 1.0):
    return [ApproachPairEstimate(p, -4.0, ratio, mad, 3) for p in (3.0, 3.2, 3.4)]


def test_an_assumed_depth_does_not_gate_the_path_on_its_own_speed_ratio():
    windows = _windows(ratio=1.45)
    assumed = combine_approach_estimates(
        windows,
        attack_estimate=None,
        preferred_path_estimate=windows[0],
        timing_plausible=True,
        speed_ratio_tests_depth=False,
    )
    measured = combine_approach_estimates(
        windows,
        attack_estimate=None,
        preferred_path_estimate=windows[0],
        timing_plausible=True,
        speed_ratio_tests_depth=True,
    )
    assert assumed.path_confidence_tier == "high"
    assert measured.path_confidence_tier == "low"  # an IWR depth that disagrees with the OPS


def test_the_hosel_points_velocity_mad_gates_nothing():
    windows = _windows(ratio=1.0, mad=40.0)
    result = combine_approach_estimates(
        windows, attack_estimate=None, preferred_path_estimate=windows[0], timing_plausible=True
    )
    assert result.path_confidence_tier == "high"
    assert not hasattr(cd, "CHAINED_VELOCITY_MAD_MAX_MPH")


def test_the_located_tee_depth_is_named_assumed():
    evidence = located_tee_range_evidence(1.8, 72.0)
    assert evidence.status in sl.ASSUMED_DEPTH_STATUSES
    assert "accepted" not in sl.ASSUMED_DEPTH_STATUSES  # an IWR walk is measured


@pytest.mark.parametrize(
    "tier,lens,expected",
    [
        ("high", "calibrated", "estimated"),  # measured on a measured depth
        ("high", "nominal", "estimated"),
        ("low", "calibrated", "experimental"),  # the weakest input labels it
    ],
)
def test_a_camera_club_value_on_an_assumed_depth_is_at_best_estimated(tier, lens, expected):
    status = "accepted_located_tee"
    assert sl.camera_club_label(tier, lens, depth_status=status) == expected
    assert sl.ASSUMED_DEPTH_NOTE in sl.camera_club_note(tier, lens, depth_status=status)
    assert sl.ASSUMED_DEPTH_NOTE == "depth from tee range + OPS club speed (not measured)"


def test_a_measured_depth_keeps_the_lens_rule():
    assert sl.camera_club_label("high", "calibrated", depth_status="accepted") == "measured"
    assert sl.camera_club_note("high", "calibrated", depth_status="accepted") is None
