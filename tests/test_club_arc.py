"""E1: club path, attack angle, swing plane, swing direction and low point from an arc.

The synthetic arcs here are built from plain geometry (a circle of radius R in a
plane tilted ``plane`` from the horizon, its base line ``direction`` from the
target line, the golfer on the hub side), sampled at frame times before impact
with Gaussian noise. Truth for path and attack comes from a finite difference
of the generator itself, not from the fit's formulas.
"""

from __future__ import annotations

import dataclasses
import math

import numpy as np
import pytest

from openflight import club_arc
from openflight.club_arc import (
    CALCULATED,
    EXPERIMENTAL,
    MODEL_GEOMETRY,
    NOT_MEASURED,
    HeadPosition,
    club_delivery_payload,
    fit_club_arc,
    prior_for_club,
)
from openflight.clubs import ClubType

FRAME_S = 0.002  # the OV9281 clips' 2 ms frame interval


def _arc_point(t_s, *, plane_deg, direction_deg, radius_m, low_point, omega, t_low, handed):
    """A point on the arc at time t (x forward, y right, z up)."""
    psi = math.radians(direction_deg)
    theta = math.radians(plane_deg)
    side = 1.0 if handed == "RH" else -1.0
    e1 = np.array([math.cos(psi), math.sin(psi), 0.0])
    toward_golfer = side * np.array([math.sin(psi), -math.cos(psi), 0.0])
    e2 = math.cos(theta) * toward_golfer + np.array([0.0, 0.0, math.sin(theta)])
    phi = omega * (t_s - t_low)
    return (
        np.asarray(low_point, dtype=float)
        + radius_m * math.sin(phi) * e1
        + radius_m * (1.0 - math.cos(phi)) * e2
    )


def _synthetic_swing(
    *,
    plane_deg=57.0,
    direction_deg=1.5,
    radius_m=1.0,
    speed_ms=38.0,
    low_after_impact_s=0.002,
    n_points=6,
    noise_m=0.0005,
    handed="RH",
    seed=0,
    ball=(0.03, 1.45, 0.021),
    times=None,
):
    """Head positions before an impact at t=0, plus the truth at impact.

    Positions are returned in the unit frame of ``HeadPosition`` (lateral = y,
    forward = x, up = z); the low point sits ``low_after_impact_s`` after impact
    (positive: hitting down). Frames are 2 ms apart, the last one half a frame
    before contact (contact is midway between the last resting frame and the next).
    """
    omega = speed_ms / radius_m
    kwargs = dict(
        plane_deg=plane_deg,
        direction_deg=direction_deg,
        radius_m=radius_m,
        omega=omega,
        t_low=low_after_impact_s,
        handed=handed,
    )
    # Put the head at impact 40 mm behind the ball centre, at ball height.
    at_impact_from_low = _arc_point(0.0, low_point=np.zeros(3), **kwargs)
    impact = np.array([ball[1] - 0.04, ball[0], ball[2]])
    low_point = impact - at_impact_from_low
    rng = np.random.default_rng(seed)
    if times is None:
        times = -FRAME_S / 2.0 - FRAME_S * np.arange(n_points)[::-1]
    positions = []
    for t_s in times:
        point = _arc_point(float(t_s), low_point=low_point, **kwargs)
        point = point + rng.normal(0.0, noise_m, 3)
        positions.append(
            HeadPosition(
                t_s=float(t_s),
                lateral_m=float(point[1]),
                up_m=float(point[2]),
                forward_m=float(point[0]),
            )
        )
    dt = 1e-6
    before = _arc_point(-dt, low_point=low_point, **kwargs)
    after = _arc_point(dt, low_point=low_point, **kwargs)
    velocity = after - before
    truth = {
        "club_path_deg": math.degrees(math.atan2(velocity[1], velocity[0])),
        "attack_angle_deg": math.degrees(
            math.atan2(velocity[2], math.hypot(velocity[0], velocity[1]))
        ),
        "swing_plane_deg": plane_deg,
        "swing_direction_deg": direction_deg,
        # TrackMan's low point is relative to the head at impact (+ after, + up, + right)
        "low_point_distance_mm": (low_point[0] - impact[0]) * 1000.0,
        "low_point_height_mm": (low_point[2] - impact[2]) * 1000.0,
        "low_point_side_mm": (low_point[1] - impact[1]) * 1000.0,
        "radius_m": radius_m,
        "ball": ball,
    }
    return positions, truth


def _assert_recovers(arc, truth, *, angle_tol, plane_tol, direction_tol, low_point_tol_mm):
    assert arc.club_path_deg == pytest.approx(truth["club_path_deg"], abs=angle_tol)
    assert arc.attack_angle_deg == pytest.approx(truth["attack_angle_deg"], abs=angle_tol)
    assert arc.swing_plane_deg == pytest.approx(truth["swing_plane_deg"], abs=plane_tol)
    assert arc.swing_direction_deg == pytest.approx(truth["swing_direction_deg"], abs=direction_tol)
    for name in ("low_point_distance_mm", "low_point_height_mm", "low_point_side_mm"):
        assert getattr(arc, name) == pytest.approx(truth[name], abs=low_point_tol_mm), name


# ---------------------------------------------------------------------------
# Recovery from the shot's own points
# ---------------------------------------------------------------------------


def test_six_low_noise_points_recover_every_output_and_are_calculated():
    positions, truth = _synthetic_swing(
        plane_deg=52.0, radius_m=1.05, n_points=6, noise_m=0.0002, direction_deg=-2.0
    )

    arc = fit_club_arc(
        positions,
        impact_t_s=0.0,
        club=ClubType.IRON_7,
        position_noise_m=0.0002,
    )

    assert arc.status == CALCULATED, arc.reason
    # 52 deg is 5 deg off the 7-iron prior: the shot's own points carried it
    _assert_recovers(
        arc, truth, angle_tol=0.3, plane_tol=1.0, direction_tol=1.0, low_point_tol_mm=10.0
    )
    assert arc.radius_m == pytest.approx(1.05, abs=0.05)
    assert arc.residual_rms_mm < 1.0
    assert arc.n_positions == 6
    assert arc.prior is None


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_four_noisy_points_recover_path_and_attack_with_model_geometry(seed):
    positions, truth = _synthetic_swing(n_points=4, noise_m=0.002, seed=seed)

    arc = fit_club_arc(positions, impact_t_s=0.0, club=ClubType.IRON_7)

    # 4 points over 6 ms at 2 mm noise cannot fix the plane or radius alone
    assert arc.status == MODEL_GEOMETRY
    assert arc.prior is not None and "Kwon" in arc.prior["source"]
    _assert_recovers(
        arc, truth, angle_tol=1.5, plane_tol=3.0, direction_tol=3.0, low_point_tol_mm=40.0
    )


@pytest.mark.parametrize("seed", [4, 5, 6])
def test_three_noisy_points_recover_path_and_attack_with_model_geometry(seed):
    positions, truth = _synthetic_swing(n_points=3, noise_m=0.001, seed=seed)

    arc = fit_club_arc(positions, impact_t_s=0.0, club=ClubType.IRON_7)

    assert arc.status == MODEL_GEOMETRY
    _assert_recovers(
        arc, truth, angle_tol=1.5, plane_tol=3.0, direction_tol=3.0, low_point_tol_mm=40.0
    )


def test_three_points_over_a_wide_arc_are_calculated_from_the_shot_alone():
    """Three near-exact points 12 ms apart (~45 deg of arc) fix the circle: no prior."""
    positions, truth = _synthetic_swing(
        radius_m=1.1, plane_deg=50.0, noise_m=0.0001, times=(-0.0255, -0.0135, -0.0015)
    )

    arc = fit_club_arc(positions, impact_t_s=0.0, club=ClubType.IRON_7, position_noise_m=0.0002)

    assert arc.status == CALCULATED, arc.reason
    _assert_recovers(
        arc, truth, angle_tol=0.2, plane_tol=0.5, direction_tol=0.5, low_point_tol_mm=5.0
    )


# ---------------------------------------------------------------------------
# The prior case
# ---------------------------------------------------------------------------


def test_model_geometry_carries_the_prior_bias_for_a_golfer_off_the_prior():
    """A 52 deg, 1.10 m golfer read through the 57 deg, 1.0 m 7-iron prior.

    The label says the plane is assumed; path and attack stay near truth because
    the shot's own points carry the direction.
    """
    positions, truth = _synthetic_swing(n_points=4, noise_m=0.002, plane_deg=52.0, radius_m=1.10)

    arc = fit_club_arc(positions, impact_t_s=0.0, club=ClubType.IRON_7)

    assert arc.status == MODEL_GEOMETRY
    assert arc.swing_plane_deg == pytest.approx(prior_for_club(ClubType.IRON_7).plane_deg, abs=1.0)
    assert arc.club_path_deg == pytest.approx(truth["club_path_deg"], abs=2.0)
    assert arc.attack_angle_deg == pytest.approx(truth["attack_angle_deg"], abs=2.5)


def test_two_points_use_the_published_prior_and_say_so():
    positions, truth = _synthetic_swing(n_points=2, noise_m=0.0, plane_deg=55.8, radius_m=1.0)

    arc = fit_club_arc(positions, impact_t_s=0.0, club=ClubType.IRON_5)

    assert arc.status == MODEL_GEOMETRY
    assert arc.prior["plane_deg"] == pytest.approx(55.8)
    assert "Kwon" in arc.prior["source"] and "Kra" in arc.prior["source"]
    assert arc.swing_plane_deg == pytest.approx(55.8, abs=0.5)
    assert arc.club_path_deg == pytest.approx(truth["club_path_deg"], abs=0.5)
    assert arc.attack_angle_deg == pytest.approx(truth["attack_angle_deg"], abs=0.5)
    assert any("prior" in reason for reason in arc.conditioning["reasons"])


def test_published_priors_by_club():
    driver = prior_for_club(ClubType.DRIVER)
    five = prior_for_club(ClubType.IRON_5)
    six = prior_for_club(ClubType.IRON_6)
    pw = prior_for_club(ClubType.PW)
    assert driver.plane_deg == pytest.approx(47.2)
    assert five.plane_deg == pytest.approx(55.8)
    assert pw.plane_deg == pytest.approx(59.1)
    assert driver.radius_m == pytest.approx(1.077, abs=0.001)
    assert six.radius_m == pytest.approx(1.002, abs=0.001)
    assert pw.radius_m == pytest.approx(0.993, abs=0.001)
    # steeper as the club shortens, never extrapolated past the anchors
    seven = prior_for_club(ClubType.IRON_7)
    assert five.plane_deg < seven.plane_deg < pw.plane_deg
    assert prior_for_club(ClubType.LW).plane_deg == pytest.approx(59.1)
    assert prior_for_club(ClubType.UNKNOWN) is None


def test_unknown_club_without_a_conditioned_fit_is_not_measured():
    positions, _truth = _synthetic_swing(n_points=2, noise_m=0.0)

    arc = fit_club_arc(positions, impact_t_s=0.0, club=ClubType.UNKNOWN)

    assert arc.status == NOT_MEASURED
    assert "prior" in arc.reason
    assert arc.club_path_deg is None


# ---------------------------------------------------------------------------
# Frame and sign
# ---------------------------------------------------------------------------


def test_right_handed_in_to_out_is_positive_path():
    """Hitting down on a square plane moves a right-hander's path in-to-out (+)."""
    positions, truth = _synthetic_swing(direction_deg=0.0, low_after_impact_s=0.003)
    assert truth["attack_angle_deg"] < 0.0
    assert truth["club_path_deg"] > 0.0

    arc = fit_club_arc(positions, impact_t_s=0.0, club=ClubType.IRON_7)

    assert arc.club_path_deg > 0.5
    assert arc.attack_angle_deg < -0.5
    assert arc.low_point_distance_mm > 0.0  # low point after the ball
    assert arc.handedness == "RH"


def test_left_handed_is_explicit_and_mirrors_the_path():
    positions, truth = _synthetic_swing(direction_deg=0.0, low_after_impact_s=0.003, handed="LH")
    assert truth["club_path_deg"] < 0.0  # a left-hander's in-to-out is left of target

    arc = fit_club_arc(positions, impact_t_s=0.0, club=ClubType.IRON_7, handedness="LH")
    default = fit_club_arc(positions, impact_t_s=0.0, club=ClubType.IRON_7)

    assert arc.handedness == "LH"
    assert arc.club_path_deg == pytest.approx(truth["club_path_deg"], abs=1.0)
    assert default.handedness == "RH"


@pytest.mark.parametrize("offset_deg", [0.0, 3.0, -2.5])
def test_horizontal_offset_moves_every_horizontal_output_into_the_target_line_frame(offset_deg):
    positions, truth = _synthetic_swing(n_points=6, noise_m=0.0002, plane_deg=52.0)

    boresight = fit_club_arc(
        positions, impact_t_s=0.0, club=ClubType.IRON_7, position_noise_m=0.0002
    )
    turned = fit_club_arc(
        positions,
        impact_t_s=0.0,
        club=ClubType.IRON_7,
        position_noise_m=0.0002,
        horizontal_offset_deg=offset_deg,
    )

    # an explicit 0.0 is a target line straight down the boresight (V-formulas C5)
    assert turned.frame == "target_line"
    assert boresight.frame == "unit_boresight"
    assert turned.club_path_deg == pytest.approx(boresight.club_path_deg + offset_deg, abs=0.05)
    assert turned.swing_direction_deg == pytest.approx(
        boresight.swing_direction_deg + offset_deg, abs=0.05
    )
    # vertical outputs do not depend on the yaw zero
    assert turned.attack_angle_deg == pytest.approx(boresight.attack_angle_deg, abs=0.05)
    assert turned.swing_plane_deg == pytest.approx(boresight.swing_plane_deg, abs=0.05)
    assert turned.low_point_height_mm == pytest.approx(boresight.low_point_height_mm, abs=0.5)
    horizontal = math.hypot(boresight.low_point_distance_mm, boresight.low_point_side_mm)
    assert math.hypot(turned.low_point_distance_mm, turned.low_point_side_mm) == pytest.approx(
        horizontal, abs=0.5
    )


# ---------------------------------------------------------------------------
# Not measured
# ---------------------------------------------------------------------------


def test_no_head_positions_is_not_measured_with_a_reason():
    arc = fit_club_arc([], impact_t_s=0.0, club=ClubType.IRON_7)

    assert arc.status == NOT_MEASURED
    assert arc.reason
    for name in (
        "club_path_deg",
        "attack_angle_deg",
        "swing_plane_deg",
        "swing_direction_deg",
        "low_point_distance_mm",
        "low_point_height_mm",
        "low_point_side_mm",
    ):
        assert getattr(arc, name) is None


def test_one_head_position_is_not_measured():
    positions, _truth = _synthetic_swing(n_points=1)

    arc = fit_club_arc(positions, impact_t_s=0.0, club=ClubType.IRON_7)

    assert arc.status == NOT_MEASURED
    assert "2" in arc.reason


def test_missing_impact_time_is_not_measured():
    positions, _truth = _synthetic_swing()

    arc = fit_club_arc(positions, impact_t_s=None, club=ClubType.IRON_7)

    assert arc.status == NOT_MEASURED
    assert "impact" in arc.reason


def test_post_impact_positions_are_left_out():
    positions, truth = _synthetic_swing(n_points=4, noise_m=0.0)
    after = HeadPosition(0.0015, 0.5, 0.5, 0.5)  # a launched-ball or deflected-head point

    arc = fit_club_arc([*positions, after], impact_t_s=0.0, club=ClubType.IRON_7)

    assert arc.n_positions == 4
    assert arc.club_path_deg == pytest.approx(truth["club_path_deg"], abs=0.5)


def test_positions_far_from_impact_are_not_measured():
    positions, _truth = _synthetic_swing(n_points=3)
    early = [HeadPosition(p.t_s - 0.05, p.lateral_m, p.up_m, p.forward_m) for p in positions]

    arc = fit_club_arc(early, impact_t_s=0.0, club=ClubType.IRON_7)

    assert arc.status == NOT_MEASURED
    assert "impact" in arc.reason


def test_times_on_a_large_camera_clock_fit_like_times_near_zero():
    positions, _truth = _synthetic_swing(n_points=4, noise_m=0.002)
    clock_s = 51234.5678
    shifted = [HeadPosition(p.t_s + clock_s, p.lateral_m, p.up_m, p.forward_m) for p in positions]

    near_zero = fit_club_arc(positions, impact_t_s=0.0, club=ClubType.IRON_7)
    on_clock = fit_club_arc(shifted, impact_t_s=clock_s, club=ClubType.IRON_7)

    assert on_clock.club_path_deg == pytest.approx(near_zero.club_path_deg, abs=0.01)
    assert on_clock.attack_angle_deg == pytest.approx(near_zero.attack_angle_deg, abs=0.01)


def test_ranges_bracket_the_values():
    positions, _truth = _synthetic_swing(n_points=4, noise_m=0.002, seed=9)

    arc = fit_club_arc(positions, impact_t_s=0.0, club=ClubType.IRON_7)

    for name, (low, high) in arc.ranges.items():
        value = getattr(arc, name)
        assert low <= value <= high, name


# ---------------------------------------------------------------------------
# Shot payload and the selection rule
# ---------------------------------------------------------------------------


def _delivery(positions, **overrides):
    delivery = {
        "status": "chained_high",
        "club_path_deg": 2.4,
        "attack_angle_deg": -4.1,
        "path_confidence_tier": "high",
        "attack_confidence_tier": "high",
        "club_path_frame": "unit_boresight",
        "contact_t_s": 0.0,
        "horizontal_offset_deg": None,
        "head_positions": [
            {"t_s": p.t_s, "lateral_m": p.lateral_m, "up_m": p.up_m, "forward_m": p.forward_m}
            for p in positions
        ],
    }
    delivery.update(overrides)
    return delivery


def test_payload_keeps_the_measured_chained_value_and_records_both():
    positions, _truth = _synthetic_swing(n_points=4, noise_m=0.002)

    payload = club_delivery_payload(_delivery(positions), club=ClubType.IRON_7, handedness="RH")

    path = payload["metrics"]["club_path_deg"]
    assert path["value"] == pytest.approx(2.4)
    assert path["status"] == "measured"
    assert path["source"] == "camera_iwr_chained"
    assert path["frame"] == "unit_boresight"
    assert payload["cross_check"]["arc"]["club_path_deg"] is not None
    assert payload["cross_check"]["chained"]["club_path_deg"] == pytest.approx(2.4)
    plane = payload["metrics"]["swing_plane_deg"]
    assert plane["status"] == MODEL_GEOMETRY
    assert plane["source"].startswith("club_arc")
    assert plane["frame"] is None
    assert payload["metrics"]["swing_direction_deg"]["frame"] == "unit_boresight"
    assert payload["metrics"]["low_point_distance_mm"]["frame"] == "unit_boresight"
    assert payload["selection_rule"]


def test_payload_uses_the_arc_when_the_chained_value_is_missing():
    positions, _truth = _synthetic_swing(n_points=4, noise_m=0.002)

    payload = club_delivery_payload(
        _delivery(positions, attack_angle_deg=None, attack_confidence_tier="withheld"),
        club=ClubType.IRON_7,
        handedness="RH",
    )

    attack = payload["metrics"]["attack_angle_deg"]
    assert attack["status"] == MODEL_GEOMETRY
    assert attack["source"].startswith("club_arc")
    assert attack["value"] == payload["cross_check"]["arc"]["attack_angle_deg"]


def test_payload_without_head_positions_is_not_measured_with_reasons():
    payload = club_delivery_payload(
        {"status": "rejected_no_stable_clubhead"}, club=ClubType.IRON_7, handedness=None
    )

    for name, metric in payload["metrics"].items():
        assert metric["value"] is None, name
        assert metric["status"] == NOT_MEASURED, name
        assert metric["reason"], name
    assert "rejected_no_stable_clubhead" in payload["metrics"]["swing_plane_deg"]["reason"]
    assert payload["handedness"] == "RH"


def test_payload_without_a_delivery_is_not_measured():
    payload = club_delivery_payload(None, club=ClubType.IRON_7, handedness="LH")

    assert payload["metrics"]["club_path_deg"]["status"] == NOT_MEASURED
    assert payload["handedness"] == "LH"


def test_label_rank_orders_measured_over_calculated_over_model_geometry():
    rank = club_arc.LABEL_RANK
    assert rank["measured"] > rank[CALCULATED] > rank[MODEL_GEOMETRY] > rank[NOT_MEASURED]


# ---- low point: the per-club offset --------------------------------------------


@pytest.mark.parametrize(
    ("club", "offset"),
    [(ClubType.IRON_7, 2.3), (ClubType.DRIVER, -28.3), (ClubType.PW, 5.5)],
)
def test_the_low_point_distance_adds_the_clubs_fitted_offset_to_the_arc(club, offset):
    positions, _truth = _synthetic_swing(n_points=4, noise_m=0.002)

    payload = club_delivery_payload(_delivery(positions), club=club, handedness="RH")

    entry = payload["metrics"]["low_point_distance_mm"]
    geometry = payload["arc"]["low_point_distance_mm"]
    assert entry["geometry_value"] == pytest.approx(geometry)
    assert entry["offset_mm"] == offset
    assert entry["value"] == pytest.approx(geometry + offset, abs=0.01)
    low, high = entry["range"]
    arc_low, arc_high = payload["arc"]["ranges"]["low_point_distance_mm"]
    assert (low, high) == pytest.approx((arc_low + offset, arc_high + offset), abs=0.01)
    assert f"{offset:+.1f} mm" in entry["note"]
    assert payload["metrics"]["low_point_height_mm"]["value"] == pytest.approx(
        payload["arc"]["low_point_height_mm"]
    )


@pytest.mark.parametrize("club", [ClubType.IRON_5, ClubType.WOOD_3, ClubType.SW, ClubType.UNKNOWN])
def test_a_club_without_a_fitted_offset_keeps_the_arc_and_says_so(club):
    positions, _truth = _synthetic_swing(n_points=6, noise_m=0.0005)

    payload = club_delivery_payload(_delivery(positions), club=club, handedness="RH")

    entry = payload["metrics"]["low_point_distance_mm"]
    if entry["value"] is None:
        return
    assert entry["value"] == pytest.approx(payload["arc"]["low_point_distance_mm"])
    assert entry["offset_mm"] is None
    assert entry["note"] == club_arc.NOTE_NO_LOW_POINT_OFFSET


def test_a_missing_low_point_gets_no_offset():
    payload = club_delivery_payload(None, club=ClubType.DRIVER, handedness="RH")

    entry = payload["metrics"]["low_point_distance_mm"]
    assert entry["value"] is None
    assert entry["status"] == NOT_MEASURED


# ---- how much of each output the prior sets ----------------------------------------

# The head track's frames at 120 fps: two or three hosel points, the last one a few ms
# before contact, each to about a centimetre (one hosel pixel at the head's depth).
HEAD_TRACK_TIMES = (-0.0226, -0.0143, -0.0060)
HEAD_TRACK_SIGMA_M = 0.012


def test_a_model_geometry_fit_reports_the_prior_s_share_of_each_output():
    positions, _truth = _synthetic_swing(n_points=2, noise_m=0.0, plane_deg=57.0)

    arc = fit_club_arc(positions, impact_t_s=0.0, club=ClubType.IRON_7)

    assert arc.status == MODEL_GEOMETRY
    assert set(arc.prior_share) >= {*club_arc._OUTPUTS, "radius_m"}
    assert all(0.0 <= share <= 1.0 for share in arc.prior_share.values())
    # two points cannot tilt the plane: it is the prior's
    assert arc.prior_share["swing_plane_deg"] > 0.95
    assert arc.prior_share["radius_m"] > 0.95


def test_a_calculated_fit_has_no_prior_share():
    positions, _truth = _synthetic_swing(n_points=6, noise_m=0.0002, plane_deg=52.0)

    arc = fit_club_arc(positions, impact_t_s=0.0, club=ClubType.IRON_7, position_noise_m=0.0002)

    assert arc.status == CALCULATED
    assert arc.prior_share == {}


def _head_track_delivery(positions, **overrides):
    delivery = _delivery(
        positions,
        status="camera_head_track",
        club_path_deg=2.0,
        attack_angle_deg=-4.0,
        path_confidence_tier="experimental",
        attack_confidence_tier="experimental",
        head_track={"label": "experimental", "position_sigma_m": HEAD_TRACK_SIGMA_M},
    )
    delivery.update(overrides)
    return delivery


def test_head_track_positions_make_every_arc_output_experimental_or_the_prior_s():
    """Labels inherit the positions': experimental, or model geometry where the prior sets it."""
    positions, _truth = _synthetic_swing(times=HEAD_TRACK_TIMES, noise_m=0.0)

    payload = club_delivery_payload(
        _head_track_delivery(positions), club=ClubType.IRON_7, handedness="RH"
    )

    arc = payload["arc"]
    assert arc["status"] == MODEL_GEOMETRY
    assert arc["conditioning"]["position_noise_floor_m"] == pytest.approx(HEAD_TRACK_SIGMA_M)
    metrics = payload["metrics"]
    plane = metrics["swing_plane_deg"]
    assert plane["status"] == MODEL_GEOMETRY
    assert "prior" in plane["note"] and "%" in plane["note"]
    for name in ("swing_direction_deg", "low_point_distance_mm", "low_point_height_mm"):
        entry = metrics[name]
        assert entry["value"] is not None, name
        share = arc["prior_share"][name]
        expected = MODEL_GEOMETRY if share >= club_arc.PRIOR_SET_SHARE else EXPERIMENTAL
        assert entry["status"] == expected, (name, share)
        assert "head track" in entry["note"], name
    # the head track's own path and attack stay: the arc reads the same points
    assert metrics["club_path_deg"]["value"] == pytest.approx(2.0)
    assert metrics["club_path_deg"]["status"] == EXPERIMENTAL
    assert metrics["club_path_deg"]["source"] == "camera_head_track"
    assert metrics["attack_angle_deg"]["value"] == pytest.approx(-4.0)


def test_head_track_positions_fill_a_withheld_angle_only_experimental():
    positions, _truth = _synthetic_swing(times=HEAD_TRACK_TIMES, noise_m=0.0)

    payload = club_delivery_payload(
        _head_track_delivery(positions, attack_angle_deg=None, attack_confidence_tier="withheld"),
        club=ClubType.IRON_7,
        handedness="RH",
    )

    attack = payload["metrics"]["attack_angle_deg"]
    assert attack["value"] == payload["cross_check"]["arc"]["attack_angle_deg"]
    assert attack["status"] in (EXPERIMENTAL, MODEL_GEOMETRY)


def test_label_rank_follows_the_owner_order():
    rank = club_arc.LABEL_RANK
    assert (
        rank["measured"]
        > rank[CALCULATED]
        > rank[MODEL_GEOMETRY]
        > rank["estimated"]
        > rank[EXPERIMENTAL]
        > rank[NOT_MEASURED]
    )


def test_the_prior_sensitivity_predicts_a_refit_on_a_moved_prior(monkeypatch):
    """The linearized move per degree of the assumed plane matches refitting with it moved."""
    positions, _truth = _synthetic_swing(times=HEAD_TRACK_TIMES, noise_m=0.0)
    arc = fit_club_arc(
        positions, impact_t_s=0.0, club=ClubType.IRON_7, position_noise_m=HEAD_TRACK_SIGMA_M
    )
    prior = prior_for_club(ClubType.IRON_7)
    moved = dataclasses.replace(prior, plane_deg=prior.plane_deg + 1.0)
    monkeypatch.setattr(club_arc, "prior_for_club", lambda _club: moved)
    refit = fit_club_arc(
        positions, impact_t_s=0.0, club=ClubType.IRON_7, position_noise_m=HEAD_TRACK_SIGMA_M
    )

    for name in ("swing_plane_deg", "swing_direction_deg", "low_point_distance_mm"):
        predicted = arc.prior_sensitivity[name]["per_plane_deg"]
        actual = getattr(refit, name) - getattr(arc, name)
        assert actual == pytest.approx(predicted, abs=0.05 + 0.15 * abs(predicted)), name


def test_the_payload_keeps_the_head_track_state_for_the_full_club_speed():
    positions, _truth = _synthetic_swing(times=HEAD_TRACK_TIMES, noise_m=0.0)
    turn = {"t_ns": 1.0, "direction": [0.0, 0.0, 1.0], "axis": [1.0, 0.0, 0.0]}
    delivery = _head_track_delivery(
        positions,
        head_track={"status": "ok", "turn": turn, "position_sigma_m": 0.01, "track": [1, 2]},
    )

    state = club_delivery_payload(delivery, club=ClubType.IRON_7, handedness="RH")["head_track"]

    assert state["status"] == "ok"
    assert state["turn"] == turn
    assert "track" not in state  # the pixels stay with the camera record
    assert club_delivery_payload(_delivery(positions), club=ClubType.IRON_7, handedness="RH")[
        "head_track"
    ] is None
