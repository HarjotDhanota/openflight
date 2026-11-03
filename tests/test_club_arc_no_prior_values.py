"""No published prior is ever shown as a club-delivery value (C11f, owner 3 Nov 2026).

When the shot's own head positions do not fix the arc (club_arc CONDITIONING), the fit
falls back to the published per-club prior on the radius and swing plane (model
geometry). Those values are the population's, not the shot's: swing plane 57.1, swing
direction, low point. The shot record and the kiosk now show every arc output of such a
fit as not measured, with the shot's own conditioning in the reason; the prior stays in
the record only as the named gate it was fitted against. A fit the points fix
(calculated) is unchanged.
"""

from __future__ import annotations

import json

import pytest

from openflight.club_arc import (
    ARC_OUTPUTS,
    CALCULATED,
    PRE_CONTACT_PLANE_SOURCE,
    club_delivery_payload,
)
from openflight.clubs import ClubType
from tests.test_club_arc import (
    HEAD_TRACK_TIMES,
    _delivery,
    _head_track_delivery,
    _synthetic_swing,
)

PRIOR_VALUES = ("57.1", "1.62", "57.98")

# Oct 27 shot 1 at 6dc0018e: the club stage's head positions f40-f44 (camera clock, s)
SHOT1 = {
    "status": "approach_high",
    "attack_angle_deg": -10.29,
    "club_path_deg": 10.97,
    "path_confidence_tier": "high",
    "attack_confidence_tier": "high",
    "club_path_frame": "target_line",
    "contact_t_s": 4027.3832145,
    "horizontal_offset_deg": 2.0666,
    "ball_position": {"lateral_m": -0.0635, "up_m": 0.02135, "forward_m": 1.8129},
    "head_positions": [
        {"t_s": 4027.370558, "lateral_m": -0.2258, "up_m": 0.1338, "forward_m": 1.3622},
        {"t_s": 4027.374175, "lateral_m": -0.2052, "up_m": 0.0948, "forward_m": 1.4849},
        {"t_s": 4027.37779, "lateral_m": -0.1861, "up_m": 0.0646, "forward_m": 1.6052},
        {"t_s": 4027.381406, "lateral_m": -0.1658, "up_m": 0.0363, "forward_m": 1.7243},
        {"t_s": 4027.385023, "lateral_m": -0.1491, "up_m": 0.0312, "forward_m": 1.8427},
    ],
}


def _assert_no_prior_values(payload: dict) -> None:
    metrics = payload["metrics"]
    for name in (
        "swing_plane_deg",
        "swing_direction_deg",
        "low_point_distance_mm",
        "low_point_height_mm",
        "low_point_side_mm",
        "radius_m",
    ):
        entry = metrics.get(name)
        if entry is None:
            continue
        if entry.get("source") == PRE_CONTACT_PLANE_SOURCE:
            # the pre-contact track's own plane (C18c): experimental, never the prior's
            assert name in ("swing_plane_deg", "swing_direction_deg")
            assert entry["status"] == "experimental"
            continue
        assert entry["value"] is None, name
        assert entry["status"] == "not_measured", name
        assert entry["source"] is None, name
        assert "prior" in entry["reason"] and "gate" in entry["reason"], name
    arc = payload["arc"]
    assert arc["status"] == "not_measured"
    assert all(arc.get(name) is None for name in ARC_OUTPUTS)
    assert arc["prior"] is not None  # named, as the gate it is
    assert payload["cross_check"]["arc"]["club_path_deg"] is None


def test_shot1_shows_no_swing_plane_direction_or_low_point_from_the_prior():
    payload = club_delivery_payload(SHOT1, club=ClubType.IRON_7, handedness="RH")

    _assert_no_prior_values(payload)
    reason = payload["metrics"]["low_point_distance_mm"]["reason"]
    assert "swing_plane_deg uncertainty" in reason  # the shot's own conditioning, in numbers
    # the shot's own path and attack stand
    assert payload["metrics"]["attack_angle_deg"]["value"] == pytest.approx(-10.29)
    assert payload["metrics"]["club_path_deg"]["value"] == pytest.approx(10.97)
    assert not any(value in json.dumps(payload["metrics"]) for value in PRIOR_VALUES)


def test_an_unconditioned_arc_never_fills_a_withheld_angle_with_the_prior_s():
    positions, _truth = _synthetic_swing(n_points=4, noise_m=0.002)
    payload = club_delivery_payload(
        _delivery(positions, attack_angle_deg=None, attack_confidence_tier="withheld"),
        club=ClubType.IRON_7,
        handedness="RH",
    )

    _assert_no_prior_values(payload)
    attack = payload["metrics"]["attack_angle_deg"]
    assert attack["value"] is None and attack["status"] == "not_measured"


def test_head_track_positions_the_prior_would_set_are_not_measured():
    positions, _truth = _synthetic_swing(times=HEAD_TRACK_TIMES, noise_m=0.0)
    payload = club_delivery_payload(
        _head_track_delivery(positions), club=ClubType.IRON_7, handedness="RH"
    )

    _assert_no_prior_values(payload)
    assert payload["metrics"]["attack_angle_deg"]["value"] == pytest.approx(-4.0)


def test_a_fit_the_shot_s_points_fix_is_kept_as_it_is():
    from openflight.club_arc import _without_prior_values, fit_club_arc  # noqa: PLC0415

    positions, _truth = _synthetic_swing(n_points=6, noise_m=0.0002, plane_deg=52.0)
    arc = fit_club_arc(positions, impact_t_s=0.0, club=ClubType.IRON_7, position_noise_m=0.0002)

    assert arc.status == CALCULATED
    assert _without_prior_values(arc) is arc
