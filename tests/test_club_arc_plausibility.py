"""G2 follow-up: an implausible camera attack angle or path flags what is built on it.

The club stage's ``plausibility`` record (club_delivery.flag_implausible) reaches the shot
record: the metric carries ``plausibility: "implausible"`` with its bound, is labelled no
better than experimental (its tier is low), and the low point read from that attack angle
and that track inherits the flag and the cap; the collision model's camera input says why.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from openflight import club_arc, shot_models
from openflight.camera import club_delivery as cd
from openflight.clubs import ClubType
from tests.test_camera_club_contact_tangent import _shot1_club


@pytest.fixture(scope="module")
def shot1():
    case = _shot1_club()
    return cd.estimate_chained_delivery(case["frames"], case["host"], **case["kwargs"])


def _implausible(result):
    """Shot 1 as if its attack angle had read -20 deg with a high-tier sigma."""
    return cd.flag_implausible(
        replace(result, attack_angle_deg=-20.0, attack_confidence_tier="high")
    )


def test_the_shot_record_flags_the_attack_and_its_low_point(shot1):
    metrics = club_arc.club_delivery_payload(
        _implausible(shot1), club=ClubType.IRON_7, handedness="RH"
    )["metrics"]
    attack = metrics["attack_angle_deg"]
    assert attack["value"] == pytest.approx(-20.0)
    assert attack["plausibility"] == "implausible"
    assert attack["status"] == "experimental"
    for name in ("low_point_distance_mm", "low_point_height_mm"):
        assert metrics[name]["plausibility"] == "implausible", name
        assert metrics[name]["status"] in ("experimental", "not_measured"), name
    assert metrics["low_point_distance_mm"]["value"] is not None  # never parked
    assert "plausibility" not in metrics["club_path_deg"]


def test_shot1_s_own_record_carries_no_flag(shot1):
    metrics = club_arc.club_delivery_payload(shot1, club=ClubType.IRON_7, handedness="RH")[
        "metrics"
    ]
    assert all("plausibility" not in entry for entry in metrics.values())


def test_the_collision_input_says_the_attack_is_implausible(shot1):
    from tests.test_label_policy import _shot  # noqa: PLC0415

    stage = _implausible(shot1)
    shot = _shot(
        camera_lens_intrinsics="calibrated",
        experimental_fused_status=stage.status,
        experimental_fused_attack_angle_deg=stage.attack_angle_deg,
        experimental_fused_attack_angle_confidence=stage.attack_confidence_tier,
        camera_fusion_processing={"club_delivery": {"plausibility": stage.plausibility}},
    )
    label, note = shot_models.camera_fused_label(shot, "attack_angle", "camera_fused_chained")
    assert label == "experimental"
    assert "implausible" in note
