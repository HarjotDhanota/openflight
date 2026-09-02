"""Tests for the D-plane inversion and the policy that decides what may be shown.

The arithmetic here is a published model, so it can be checked exactly. What
CANNOT be checked here -- or anywhere in this repository -- is whether the
angles it produces are the golfer's. On the development session the inversion
faithfully reported implausible lofts because the launch angle feeding it was
low, and it is the withholding policy, not the arithmetic, that keeps that from
reaching a display.
"""

from __future__ import annotations

import math

import pytest

from openflight.club_metrics import (
    K_HORIZONTAL_DEFAULT,
    K_VERTICAL_DEFAULT,
    DPlaneResult,
    dplane_from_sources,
    dynamic_loft,
    face_angle,
    face_to_path,
    spin_loft,
    spin_loft_3d,
)


def test_dynamic_loft_inverts_the_published_relation() -> None:
    """DL = A + (V - A) / k_v, and the coefficient used is reported with it."""
    result = dynamic_loft(vertical_launch_deg=24.0, attack_angle_deg=-5.0)

    assert result.deg == pytest.approx(-5.0 + 29.0 / 0.81)
    assert result.coefficient == pytest.approx(K_VERTICAL_DEFAULT)


def test_spin_loft_reduces_to_dynamic_loft_less_attack_when_face_matches_path() -> None:
    """At zero face-to-path, SL = DL - A = (V - A) / k_v exactly."""
    result = spin_loft(vertical_launch_deg=24.0, attack_angle_deg=-5.0)

    assert result.deg == pytest.approx(29.0 / 0.81)
    assert result.coefficient == pytest.approx(K_VERTICAL_DEFAULT)
    loft = dynamic_loft(vertical_launch_deg=24.0, attack_angle_deg=-5.0)
    assert result.deg == pytest.approx(loft.deg - (-5.0))


def test_face_angle_inverts_the_horizontal_relation() -> None:
    """F = P + (H - P) / k_h, positive right of the target line."""
    result = face_angle(horizontal_launch_deg=0.0, club_path_deg=1.5)

    assert result.deg == pytest.approx(1.5 + (0.0 - 1.5) / 0.69)
    assert result.coefficient == pytest.approx(K_HORIZONTAL_DEFAULT)


def test_face_to_path_is_the_face_less_the_path() -> None:
    result = face_to_path(horizontal_launch_deg=0.0, club_path_deg=1.5)
    face = face_angle(horizontal_launch_deg=0.0, club_path_deg=1.5)

    assert result.deg == pytest.approx(face.deg - 1.5)


@pytest.mark.parametrize(
    ("vertical", "attack", "horizontal", "path"),
    [(24.0, -5.0, 0.0, 1.5), (18.0, -4.0, -1.0, 3.0), (32.0, 2.0, 2.0, -2.0)],
)
def test_a_coefficient_of_one_makes_launch_equal_delivery(
    vertical: float, attack: float, horizontal: float, path: float
) -> None:
    """With no gear or friction transfer the ball leaves along the face."""
    assert dynamic_loft(vertical, attack, k_v=1.0).deg == pytest.approx(vertical)
    assert face_angle(horizontal, path, k_h=1.0).deg == pytest.approx(horizontal)


def test_the_launch_angle_carries_more_weight_than_the_attack_angle() -> None:
    """DL = A(1 - 1/k_v) + V/k_v, so V dominates by 1/k_v against -(1-k_v)/k_v.

    This is why an implausible dynamic loft is a statement about the launch
    angle rather than about the attack angle, and it is pinned here so that
    reasoning cannot quietly stop being true.
    """
    base = dynamic_loft(24.0, -5.0).deg
    per_launch = dynamic_loft(25.0, -5.0).deg - base
    per_attack = dynamic_loft(24.0, -4.0).deg - base

    assert per_launch == pytest.approx(1.0 / K_VERTICAL_DEFAULT)
    assert per_attack == pytest.approx(1.0 - 1.0 / K_VERTICAL_DEFAULT)
    assert abs(per_launch) > 4.0 * abs(per_attack)


@pytest.mark.parametrize("k_v", [0.0, -0.1, 1.01, math.nan])
def test_an_impossible_vertical_coefficient_is_refused(k_v: float) -> None:
    with pytest.raises(ValueError):
        dynamic_loft(24.0, -5.0, k_v=k_v)


@pytest.mark.parametrize("k_h", [0.0, -0.1, 1.01, math.nan])
def test_an_impossible_horizontal_coefficient_is_refused(k_h: float) -> None:
    with pytest.raises(ValueError):
        face_angle(0.0, 1.5, k_h=k_h)


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_a_non_finite_angle_is_refused(bad: float) -> None:
    with pytest.raises(ValueError):
        dynamic_loft(bad, -5.0)
    with pytest.raises(ValueError):
        face_angle(0.0, bad)


# --------------------------------------------------------------------------
# The policy layer: what may be shown, and why the rest may not.
# --------------------------------------------------------------------------


def _sources(**overrides):
    defaults = {
        "camera_vertical_launch_deg": 24.0,
        "horizontal_launch_deg": 0.0,
        "attack_angle_deg": -5.0,
        "club_path_deg": 1.5,
        "vertical_launch_source": "camera_ball_flight",
        "horizontal_launch_source": "shot_launch_angle_horizontal",
        "attack_angle_source": "head_track",
        "club_path_source": "head_track",
    }
    return {**defaults, **overrides}


def test_a_complete_set_of_sources_produces_a_loft_and_records_its_provenance() -> None:
    """Dynamic and spin loft are offered when the CAMERA launch angle fed them."""
    result = dplane_from_sources(**_sources())

    assert isinstance(result, DPlaneResult)
    assert result.dynamic_loft_status == "experimental"
    assert result.dynamic_loft_deg == pytest.approx(-5.0 + 29.0 / 0.81)
    assert result.spin_loft_status == "experimental"
    # The 3-D spin loft (2026-09-01 audit): the computed face-to-path -- a
    # geometry input, never an offered face -- adds its horizontal component.
    # Derived, not tuned: DL = A + (V-A)/k_v, F-P = (H-P)/k_h.
    assert result.spin_loft_deg == pytest.approx(
        spin_loft_3d(-5.0 + 29.0 / 0.81, -5.0, (0.0 - 1.5) / 0.69)
    )
    assert result.sources["vertical_launch"] == "camera_ball_flight"
    assert result.sources["attack_angle"] == "head_track"
    assert result.k_vertical == pytest.approx(K_VERTICAL_DEFAULT)
    assert result.k_horizontal == pytest.approx(K_HORIZONTAL_DEFAULT)


def test_without_the_camera_launch_angle_the_loft_is_withheld_not_substituted() -> None:
    """The shipped launch angle is NOT an acceptable substitute here.

    The whole D-plane loft is 1.23x the vertical launch angle less 0.23x the
    attack angle, so feeding it the launch angle whose bias motivated this work
    would produce a number that looks like a measurement and is not one.
    """
    result = dplane_from_sources(**_sources(camera_vertical_launch_deg=None))

    assert result.dynamic_loft_status == "withheld_lcmf_launch_bias"
    assert result.dynamic_loft_deg is None
    assert result.spin_loft_status == "withheld_lcmf_launch_bias"
    assert result.spin_loft_deg is None
    assert result.computed_dynamic_loft_deg is None


def test_the_face_angle_is_computed_and_stored_but_never_offered() -> None:
    """The horizontal launch offset is uncalibrated, so the value is not shown.

    Centring the session's face angle on a plausible mean needed +2.3 deg on
    the horizontal launch angle or -7.6 deg on the club path -- the same
    unexplained discrepancy read off either instrument. Until something
    calibrates it, the number is recorded for study and withheld from display.
    """
    result = dplane_from_sources(**_sources())

    assert result.face_angle_status == "withheld_launch_direction_offset"
    assert result.face_angle_deg is None
    assert result.computed_face_angle_deg == pytest.approx(1.5 + (0.0 - 1.5) / 0.69)
    assert result.face_to_path_status == "withheld_launch_direction_offset"
    assert result.face_to_path_deg is None
    assert result.computed_face_to_path_deg == pytest.approx(result.computed_face_angle_deg - 1.5)


def test_a_missing_attack_angle_withholds_the_loft_with_its_own_reason() -> None:
    result = dplane_from_sources(**_sources(attack_angle_deg=None))

    assert result.dynamic_loft_status == "withheld_no_attack_angle"
    assert result.dynamic_loft_deg is None
    assert result.spin_loft_status == "withheld_no_attack_angle"


def test_a_missing_club_path_withholds_the_face_angle_with_its_own_reason() -> None:
    result = dplane_from_sources(**_sources(club_path_deg=None))

    assert result.face_angle_status == "withheld_no_club_path"
    assert result.computed_face_angle_deg is None
    assert result.face_to_path_status == "withheld_no_club_path"


def test_a_missing_horizontal_launch_withholds_the_face_angle() -> None:
    result = dplane_from_sources(**_sources(horizontal_launch_deg=None))

    assert result.face_angle_status == "withheld_no_launch_direction"
    assert result.computed_face_angle_deg is None


def test_the_coefficients_actually_used_are_the_ones_reported() -> None:
    """A caller that sweeps the coefficient bracket must be able to say which ran."""
    result = dplane_from_sources(**_sources(), k_v=0.76, k_h=0.72)

    assert result.k_vertical == pytest.approx(0.76)
    assert result.k_horizontal == pytest.approx(0.72)
    assert result.dynamic_loft_deg == pytest.approx(-5.0 + 29.0 / 0.76)
    assert result.computed_face_angle_deg == pytest.approx(1.5 + (0.0 - 1.5) / 0.72)


def test_a_non_finite_input_is_treated_as_absent_rather_than_raising() -> None:
    """The policy layer takes sensor output, which is allowed to be NaN."""
    result = dplane_from_sources(**_sources(camera_vertical_launch_deg=math.nan))

    assert result.dynamic_loft_status == "withheld_lcmf_launch_bias"
    assert result.dynamic_loft_deg is None


def test_spin_loft_is_the_three_d_face_to_velocity_angle() -> None:
    """The 2026-09-01 audit counterexample: DL 30, A -5, F-P 20 is 39.904 deg,
    where the old 2-D subtraction said 35.0. And the zero-separation case
    reduces exactly to DL - A."""
    assert spin_loft_3d(30.0, -5.0, 20.0) == pytest.approx(39.904, abs=0.01)
    assert spin_loft_3d(30.0, -5.0, 0.0) == pytest.approx(35.0, abs=1e-9)
