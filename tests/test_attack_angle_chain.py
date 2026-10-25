"""The attack-angle chain, with the IWR candidate as an estimated route (owner decision 3).

Routes, best first: the canonical radar club track; the camera + IWR (or OPS) fused
delivery; an accepted IWR attack; the camera head track (experimental); the IWR
attack-angle candidate (estimated); the E1 arc. The head track goes before the
candidate: it carries a per-shot sigma from its own error budget (2.8-10.1 deg on
Harjot-outdoor-test-1), while the candidate
has none and the last radar candidate checked against TrackMan ran 9-22 deg off by
club. Each route's refusal is kept in ``Shot.input_sources["attack_angle"]``.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from openflight import collision_model, shot_metrics, shot_models
from openflight.clubs import ClubType
from openflight.launch_monitor import Shot
from openflight.sim.resolver import resolve_shot
from openflight.sim.types import PlayerState, UnmeasuredShotError
from tests.every_metric import arc_positions, full_ball_speed

ROUTES = [
    "canonical",
    "camera_fused",
    "iwr_accepted",
    "camera_head_track",
    "iwr_candidate",
    "club_arc",
]


def _shot(**overrides) -> Shot:
    values = {
        "ball_speed_mph": 120.0,
        "club_speed_mph": 85.0,
        "timestamp": datetime(2026, 10, 25, 9, 0, 0),
        "club": ClubType.IRON_7,
        "launch_angle_vertical": 18.0,
        "launch_angle_vertical_source": "radar",
        "launch_angle_horizontal": 1.0,
        "launch_angle_horizontal_source": "radar",
        "launch_angle_horizontal_confidence": 0.9,
        "launch_angle_horizontal_frame": "target_line",
        "club_path_deg": -2.0,
        "club_path_frame": "target_line",
        "mode": "rolling-buffer",
    }
    values.update(overrides)
    return Shot(**values)


def _iwr_candidate(status="candidate_available", value=-6.5) -> dict:
    return {"experimental_attack_angle_deg": value, "experimental_attack_angle_status": status}


def _head_track() -> dict:
    return {
        "experimental_fused_status": "camera_head_track",
        "experimental_fused_attack_angle_deg": -4.2,
        "experimental_fused_attack_angle_confidence": "experimental",
    }


def _fused() -> dict:
    return {
        "experimental_fused_status": "chained_high",
        "experimental_fused_attack_angle_deg": -3.1,
        "experimental_fused_attack_angle_confidence": "medium",
    }


def _arc(shot: Shot) -> Shot:
    shot_models._attach_club_arc(  # pylint: disable=protected-access
        shot,
        {
            "status": "rejected_no_ball",
            "head_positions": arc_positions(),
            "contact_t_s": 0.0,
            "horizontal_offset_deg": 0.0,
        },
    )
    return shot


def _attack(shot: Shot) -> dict:
    shot_models._attach_collision_model(shot)  # pylint: disable=protected-access
    return shot.collision_model["inputs"]["attack_angle"]


@pytest.mark.parametrize(
    "fields, route, value, label",
    [
        ({"club_angle_deg": -2.5, **_fused(), **_iwr_candidate()}, "canonical", -2.5, "measured"),
        ({**_fused(), **_iwr_candidate()}, "camera_fused", -3.1, "measured"),
        (
            {**_head_track(), **_iwr_candidate("accepted", -5.0)},
            "iwr_accepted",
            -5.0,
            "measured",
        ),
        ({**_head_track(), **_iwr_candidate()}, "camera_head_track", -4.2, "experimental"),
        (_iwr_candidate(), "iwr_candidate", -6.5, "estimated"),
    ],
    ids=["canonical", "fused", "accepted-iwr", "head-track", "iwr-candidate"],
)
def test_each_route_wins_only_when_every_route_before_it_gave_nothing(fields, route, value, label):
    shot = _shot(**fields)

    resolution = shot_models.attack_angle_resolution(shot)

    assert resolution.route == route
    assert resolution.value == pytest.approx(value)
    assert resolution.label == label
    assert [name for name, _why in resolution.tried] == ROUTES[: ROUTES.index(route)]


def test_the_arc_is_the_last_route():
    shot = _arc(_shot())

    resolution = shot_models.attack_angle_resolution(shot)

    assert resolution.route == "club_arc"
    assert resolution.label in ("calculated", "model_geometry")
    assert [name for name, _why in resolution.tried] == ROUTES[:-1]


@pytest.mark.parametrize(
    "status",
    [
        "candidate_out_of_bounds",
        "candidate_noisy_fit",
        "candidate_club_speed_mismatch",
        "candidate_impact_contact_mismatch",
        "rejected_insufficient_vertical_points",
    ],
)
def test_a_flagged_iwr_candidate_is_refused_with_its_status(status):
    shot = _shot(**_iwr_candidate(status))

    resolution = shot_models.attack_angle_resolution(shot)

    assert not resolution.present
    assert status in dict(resolution.tried)["iwr_candidate"]


def test_the_iwr_candidate_enters_the_collision_model_estimated_with_its_reason():
    shot = _shot(**_iwr_candidate())

    attack = _attack(shot)

    assert attack["value"] == pytest.approx(-6.5)
    assert attack["label"] == collision_model.LABEL_ESTIMATED
    assert attack["source"] == shot_models.IWR_CANDIDATE_SOURCE
    assert attack["reason"] == shot_models.IWR_CANDIDATE_REASON
    assert shot.input_sources["attack_angle"]["route"] == "iwr_candidate"
    assert shot.input_sources["attack_angle"]["label"] == "estimated"


def test_the_iwr_candidate_record_and_its_outputs_are_estimated_with_the_reason():
    shot = _shot(**_iwr_candidate())
    shot.ball_speed_full = full_ball_speed()
    shot.ball_speed_full_mph = shot.ball_speed_full["value_mph"]
    shot_models._attach_collision_model(shot)  # pylint: disable=protected-access

    metrics = shot_metrics.records(shot.to_dict())

    attack = metrics["attack_angle"]
    assert attack["status"] == "estimated"
    assert attack["source"] == "IWR attack-angle candidate (estimated)"
    assert shot_models.IWR_CANDIDATE_REASON in attack["note"]
    for name in ("dynamic_loft", "spin_loft"):
        assert metrics[name]["value"] is not None, name
        assert metrics[name]["status"] == "estimated", name


def test_the_head_track_outranks_the_iwr_candidate_but_not_an_accepted_iwr_attack():
    head = shot_models.attack_angle_resolution(_shot(**_head_track(), **_iwr_candidate()))
    accepted = shot_models.attack_angle_resolution(
        _shot(**_head_track(), **_iwr_candidate("accepted_tx2", -5.0))
    )

    assert head.route == "camera_head_track"
    assert accepted.route == "iwr_accepted"


def test_a_rejected_or_withheld_fused_attack_gives_way_to_the_iwr_candidate():
    rejected = {**_fused(), "experimental_fused_status": "rejected_no_impact"}
    withheld = {**_fused(), "experimental_fused_attack_angle_confidence": "withheld"}

    for fields in (rejected, withheld):
        resolution = shot_models.attack_angle_resolution(_shot(**fields, **_iwr_candidate()))
        assert resolution.route == "iwr_candidate"


def test_displayed_attack_angle_mirrors_the_chain_before_the_arc():
    assert shot_models.displayed_attack_angle(_shot(**_iwr_candidate())) == (
        -6.5,
        shot_models.IWR_CANDIDATE_SOURCE,
    )
    assert shot_models.displayed_attack_angle(_arc(_shot())) == (None, None)


def test_with_nothing_the_attack_is_not_measured_with_every_reason():
    shot = _shot()

    attack = _attack(shot)

    assert attack["value"] is None
    for name in ROUTES:
        assert f"{name}:" in attack["reason"]


def test_simulators_get_no_axis_built_on_the_estimated_candidate_by_default():
    shot = _shot(**_iwr_candidate(), club_path_deg=None, club_path_frame=None)
    shot.experimental_club_path_deg = -2.0
    shot.experimental_club_path_status = "accepted"
    shot.spin_rpm, shot.spin_confidence = 6800.0, 0.9
    shot_models._attach_collision_model(shot)  # pylint: disable=protected-access

    if shot.spin_axis_deg is None:
        pytest.skip("no axis built on this geometry")
    with pytest.raises(UnmeasuredShotError) as refused:
        resolve_shot(shot, PlayerState())
    assert "spin axis" in refused.value.missing
