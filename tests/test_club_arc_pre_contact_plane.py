"""Swing plane and direction from the shot's own pre-contact head track (C18c).

When the shot's head positions do not fix the full arc (club_arc CONDITIONING), the plane
the pre-contact track lies in still says something: a total-least-squares plane through the
positions before contact (never after: on a mat the heel rides the surface and every
through-impact fit reads ~90 deg), its inclination to the horizon and its ground line's
heading, each with a Monte Carlo sigma from the position noise plus the earliest anchor's
ambiguity, labelled experimental. The published prior stays a gate, never the value. The
low point stays not measured, saying whether the track's own arc turns beyond the tracked
span or under the surface.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from openflight import club_arc
from openflight.club_arc import club_delivery_payload
from openflight.clubs import ClubType
from tests.test_club_arc_no_prior_values import SHOT1

F39 = {"t_s": 4027.366941, "lateral_m": -0.2541, "up_m": 0.1865, "forward_m": 1.2339}


def _plane_track(plane_deg=60.0, direction_deg=3.0, radius=1.2, n=6, noise=0.0, seed=1):
    """Positions on a circle of ``radius`` in a plane tilted ``plane_deg``, its ground line at
    ``direction_deg``; the last a quarter frame before contact at t=0."""
    theta, psi = math.radians(plane_deg), math.radians(direction_deg)
    e1 = np.array([math.sin(psi), 0.0, math.cos(psi)])  # lateral, up, forward
    horizontal = np.array([-math.cos(psi), 0.0, math.sin(psi)])  # toward the golfer (left)
    e2 = math.cos(theta) * horizontal + math.sin(theta) * np.array([0.0, 1.0, 0.0])
    rng = np.random.default_rng(seed)
    positions = []
    for k in range(n):
        t = -0.0018 - 0.0036 * (n - 1 - k)
        phi = 33.0 * t / radius - 0.05
        p = (
            np.array([0.0, 0.02, 1.8])
            + radius * math.sin(phi) * e1
            + radius * (1 - math.cos(phi)) * e2
        )
        p = p + rng.normal(0.0, noise, 3)
        positions.append({"t_s": t, "lateral_m": p[0], "up_m": p[1], "forward_m": p[2]})
    return positions


def _delivery(positions, **changes):
    return {
        "status": "approach_high",
        "attack_angle_deg": -5.0,
        "club_path_deg": 3.0,
        "path_confidence_tier": "high",
        "attack_confidence_tier": "high",
        "club_path_frame": "unit_boresight",
        "contact_t_s": 0.0,
        "horizontal_offset_deg": None,
        "head_positions": positions,
        **changes,
    }


def test_a_known_plane_comes_back_from_the_pre_contact_track():
    plane = club_arc.pre_contact_plane(_plane_track(), impact_t_s=0.0)
    assert plane["swing_plane_deg"] == pytest.approx(60.0, abs=0.5)
    assert plane["swing_direction_deg"] == pytest.approx(3.0, abs=0.5)
    assert plane["swing_plane_sigma_deg"] > 0 and plane["swing_direction_sigma_deg"] > 0
    assert "anchor_ambiguity" in plane["sigma_terms"]


def test_positions_after_contact_never_enter_the_plane():
    track = _plane_track()
    after = {"t_s": 0.004, "lateral_m": 0.05, "up_m": 0.0, "forward_m": 1.95}  # on the mat
    assert club_arc.pre_contact_plane(
        track + [after], impact_t_s=0.0
    ) == club_arc.pre_contact_plane(track, impact_t_s=0.0)


def test_oct27_shot1_reads_a_steep_experimental_plane_and_no_low_point():
    shot1 = {**SHOT1, "head_positions": [F39, *SHOT1["head_positions"]]}
    payload = club_delivery_payload(shot1, club=ClubType.IRON_7, handedness="RH")
    plane, direction = (
        payload["metrics"]["swing_plane_deg"],
        payload["metrics"]["swing_direction_deg"],
    )

    assert plane["status"] == "experimental" and direction["status"] == "experimental"
    assert plane["source"] == club_arc.PRE_CONTACT_PLANE_SOURCE
    assert 60.0 < plane["value"] < 85.0
    # +4.9 in the measurement frame, turned by the +2.07 deg target line
    assert direction["value"] == pytest.approx(4.9 + 2.0666, abs=1.0)
    assert direction["frame"] == "target_line"
    low, high = plane["range"]
    assert high - low > 10.0  # an honest +- sigma, not a prior's
    low_point = payload["metrics"]["low_point_distance_mm"]
    assert low_point["value"] is None
    assert (
        "beyond the tracked span" in low_point["reason"]
        or "under the surface" in low_point["reason"]
    )


def test_fewer_than_four_pre_contact_positions_give_no_plane():
    assert club_arc.pre_contact_plane(_plane_track(n=3), impact_t_s=0.0) is None
