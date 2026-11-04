"""G2 C6b (4 Nov 2026): the low point from measured data.

The arc fit's low point needs curvature the 4-6 pre-contact frames rarely fix (Oct 27 shot 1:
269 mm data-only sigma), and with the prior discarded (C11f) mat shots had no low point at
all. Now: the low point DISTANCE comes from the pre-contact track's own turning point when
its sigma is within LOW_POINT_FIT_SIGMA_MAX_MM, else from the measured attack angle on an arc
whose radius is bounded (ARC_RADIUS_BOUNDS_M), labelled estimated with the range those
bounds give. The low point HEIGHT comes only from the measured heel heights (their turning
point, at most one frame past the data), never from the bounded radius; fat / thin is a
verdict of a measured height only (below the ball's bottom before the ball: fat).
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from openflight import club_arc
from openflight.camera.club_delivery import HEEL_ANCHOR_ABOVE_SOLE_M

FRAME_S = 1.0 / 288.0
BALL = {"lateral_m": 0.0, "up_m": 0.02135, "forward_m": 1.8}


def _arc(*, low_from_ball_m: float, sole_low_m: float, radius_m=1.2, speed=33.0, frames=6):
    """Heel positions on a vertical circle whose sole's lowest point is ``low_from_ball_m``
    past the ball at ``sole_low_m`` above the surface; one per 288 fps frame, the last 1.8 ms
    before the contact (the head at the ball's forward position). Returns positions, contact."""
    ball_f = BALL["forward_m"]
    low_f = ball_f + low_from_ball_m
    # arc angle at the ball, then the contact time is when the head reaches the ball
    phi_ball = math.asin((ball_f - low_f) / radius_m)
    contact_t = 100.0
    times = contact_t - 0.0018 - FRAME_S * np.arange(frames - 1, -1, -1)
    positions = []
    for t in times:
        phi = phi_ball + speed * (t - contact_t) / radius_m
        up = sole_low_m + radius_m * (1 - math.cos(phi)) + HEEL_ANCHOR_ABOVE_SOLE_M
        positions.append(
            {
                "t_s": float(t),
                "lateral_m": 0.0,
                "up_m": float(up),
                "forward_m": float(low_f + radius_m * math.sin(phi)),
            }
        )
    return positions, contact_t


def _estimate(positions, contact_t, *, attack=None, sigma=None, noise=0.001):
    return club_arc.low_point_estimate(
        positions,
        contact_t_s=contact_t,
        ball=BALL,
        attack_angle_deg=attack,
        attack_sigma_deg=sigma,
        noise_m=noise,
    )


def test_a_synthetic_arc_s_low_point_is_recovered():
    # 30 mm past the ball: 90 mm (under one frame at 33 m/s) past the last pre-contact frame
    positions, contact = _arc(low_from_ball_m=0.03, sole_low_m=-0.008)
    low = _estimate(positions, contact)
    distance, height = low["low_point_distance_mm"], low["low_point_height_mm"]
    assert distance["source"] == club_arc.LOW_POINT_FIT_SOURCE
    assert distance["value"] == pytest.approx(30.0, abs=5.0)
    assert distance["sigma_mm"] <= club_arc.LOW_POINT_FIT_SIGMA_MAX_MM
    assert height["status"] == "measured"
    assert height["value"] == pytest.approx(-8.0, abs=4.0)
    assert height["verdict"] is None  # after the ball: not fat


def test_a_low_point_before_the_ball_under_its_bottom_is_fat():
    positions, contact = _arc(low_from_ball_m=-0.09, sole_low_m=-0.012, frames=6)
    height = _estimate(positions, contact)["low_point_height_mm"]
    assert height["status"] == "measured"
    assert height["value"] < 0.0
    assert height["verdict"] == "fat"


def test_a_noisy_track_reads_its_low_point_from_the_attack_angle_on_a_bounded_radius():
    positions, contact = _arc(low_from_ball_m=0.10, sole_low_m=-0.005)
    low = _estimate(positions, contact, attack=-6.0, sigma=1.0, noise=0.02)
    distance = low["low_point_distance_mm"]
    assert distance["status"] == "estimated"
    assert distance["source"] == club_arc.LOW_POINT_ATTACK_SOURCE
    assert distance["note"].startswith("estimated (arc radius assumed 1.0-1.6 m)")
    last = positions[-1]["forward_m"]

    def from_ball(radius, attack_deg):
        return 1e3 * (last - radius * math.sin(math.radians(attack_deg)) - BALL["forward_m"])

    lo, hi = distance["range"]
    assert lo == pytest.approx(from_ball(1.0, -5.0), abs=0.5)
    assert hi == pytest.approx(from_ball(1.6, -7.0), abs=0.5)
    assert lo <= distance["value"] <= hi


def test_the_height_is_never_set_by_the_bounded_radius():
    # still falling steeply at the last frame: the turn is more than a frame past the data
    positions, contact = _arc(low_from_ball_m=0.35, sole_low_m=-0.005, radius_m=2.5)
    height = _estimate(positions, contact, attack=-8.0, sigma=1.0, noise=0.02)[
        "low_point_height_mm"
    ]
    assert height["value"] is None
    assert height["status"] == "not_measured"
    assert "more than one frame past" in height["reason"]
    assert height["verdict"] is None
    assert height["upper_bound_mm"] is not None


def test_oct27_shot1_reports_a_labelled_low_point_distance_and_a_height_status():
    from openflight.camera import club_delivery as cd  # noqa: PLC0415
    from openflight.clubs import ClubType  # noqa: PLC0415
    from tests.test_camera_club_contact_tangent import _shot1_club  # noqa: PLC0415

    case = _shot1_club()
    result = cd.estimate_chained_delivery(case["frames"], case["host"], **case["kwargs"])
    metrics = club_arc.club_delivery_payload(result, club=ClubType.IRON_7, handedness="RH")[
        "metrics"
    ]
    distance, height = metrics["low_point_distance_mm"], metrics["low_point_height_mm"]
    assert distance["value"] is not None
    assert distance["status"] == "estimated"
    assert "arc radius assumed 1.0-1.6 m" in distance["note"]
    assert distance["range"][0] < distance["value"] < distance["range"][1]
    assert height["status"] in ("measured", "estimated", "not_measured")
    assert "verdict" in height
    if height["value"] is None:
        assert height["reason"]
