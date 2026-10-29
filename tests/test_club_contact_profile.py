"""Contact algebra never supplies absent face origin, time or physical qualification."""

import numpy as np
import pytest

from openflight.camera import club_rigid_motion
from openflight.camera.club_contact_profile import (
    _gap,
    _roots,
    gap_coefficients,
    profile_face_contact,
)
from openflight.camera.club_sequence import ClubFrameIdentity


def synthetic_contact(motion, ball, face, interval):
    return profile_face_contact(
        motion, ball, face, interval, interval_kind="synthetic_first_touch_interval"
    )


def inputs():
    identity = ClubFrameIdentity(
        "a" * 64,
        10,
        "b" * 64,
        1_000_000_000,
        298,
        "recorded_start",
        "view",
        "mode",
        "controls",
        "optics",
        "club",
    )
    motion = {
        "identity": identity,
        "parameters": [0, 0, 0, 0, 0, np.log(1000)],
        "body_twist_model_s": [0, 0, 0, 0, 0, 1000],
        "model_unit_mm": 1,
        "coordinate_frame_id": "optical_RDF",
        "model_id": "synthetic-club",
        "pre_motion_support": [
            {"identity": identity, "used_interval_ns": [1_000_000_000, 1_000_298_000]}
        ],
    }
    ball = {
        "center_mm": [15, -8, 1031.335],
        "capture_sha256": "a" * 64,
        "coordinate_frame_id": "optical_RDF",
        "observation_ids": ["synthetic-ball"],
        "motion_model": "stationary_before_first_touch",
        "scope": "synthetic_control",
        "radius_mm": 21.335,
    }
    face = {
        "model_id": "synthetic-club",
        "face_frame_id": "synthetic-face",
        "scope": "synthetic_control",
        "origin_local_mm": [0, 0, 0],
        "basis_local": np.eye(3),
        "polygon_toe_up_mm": [[-50, -30], [50, -30], [50, 30], [-50, 30]],
    }
    return motion, ball, face


def test_off_center_contact_has_known_time_and_never_becomes_a_measurement():
    motion, ball, face = inputs()
    result = synthetic_contact(motion, ball, face, [1_000_000_000, 1_020_000_000])
    assert result["status"] == "conditional_face_contacts"
    first = result["conditional_contacts"][0]
    assert first["time_ns"] == pytest.approx(1_010_000_000, abs=1)
    assert first["conditional_geometry"]["toe_up_mm"] == pytest.approx([15, -8])
    assert first["normal_gap_velocity_mm_s"] == pytest.approx(-1000)
    assert first["precontact_plane_nonpenetration"]
    assert result["physical_pose"] is result["strike"] is None
    assert not result["eligible_as_independent_angle_factor"]


def test_tangent_face_origin_gauge_keeps_contact_but_changes_strike():
    motion, ball, face = inputs()
    original = synthetic_contact(motion, ball, face, [1_000_000_000, 1_020_000_000])
    shifted = synthetic_contact(
        motion, ball, {**face, "origin_local_mm": [10, 0, 0]}, [1_000_000_000, 1_020_000_000]
    )
    a, b = original["conditional_contacts"][0], shifted["conditional_contacts"][0]
    assert a["time_ns"] == pytest.approx(b["time_ns"])
    assert np.asarray(a["conditional_geometry"]["toe_up_mm"]) - b["conditional_geometry"][
        "toe_up_mm"
    ] == pytest.approx([10, 0])


def test_outside_finite_face_and_preexisting_penetration_are_rejected():
    motion, ball, face = inputs()
    outside = synthetic_contact(
        motion, {**ball, "center_mm": [60, 0, 1031.335]}, face, [1_000_000_000, 1_020_000_000]
    )
    assert outside["status"] == "no_admissible_face_plane_contact"
    assert outside["conditional_contacts"][0]["status"] == "outside_declared_finite_face"
    inside = synthetic_contact(motion, ball, face, [1_011_000_000, 1_020_000_000])
    assert (
        inside["started_behind_infinite_face_plane"]
        and inside["status"] == "no_admissible_face_plane_contact"
    )


@pytest.mark.parametrize(
    "changed,reason",
    [
        ("capture", "capture_mismatch"),
        ("frame", "frame_mismatch"),
        ("face", "face_registration"),
        ("scope", "cannot_grant_measurement"),
    ],
)
def test_missing_or_wrong_provenance_never_gets_a_contact(changed, reason):
    motion, ball, face = inputs()
    if changed == "capture":
        ball["capture_sha256"] = "c" * 64
    elif changed == "frame":
        ball["coordinate_frame_id"] = "other-frame"
    elif changed == "face":
        face.pop("face_frame_id")
    else:
        ball["scope"] = "measured"
    result = synthetic_contact(motion, ball, face, [1_000_000_000, 1_020_000_000])
    assert result["status"] == "unavailable" and reason in result["reason"]
    assert result["conditional_contacts"] == []


def test_analytic_gap_matches_rigid_transforms_for_rotating_translating_motion():
    rng = np.random.default_rng(29)
    for _ in range(10):
        ball = rng.normal(size=3) * 40
        twist = rng.normal(size=6) * [5, 5, 5, 100, 100, 100]
        origin = rng.normal(size=3)
        normal = rng.normal(size=3)
        normal /= np.linalg.norm(normal)
        coefficients = gap_coefficients(ball, twist, origin, normal, 21.335)
        for time in (-0.01, 0, 0.005, 0.02):
            transform = club_rigid_motion.se3_exp(twist * time)
            moved_ball = transform[:3, :3].T @ (ball - transform[:3, 3])
            assert _gap(coefficients, time) == pytest.approx(
                normal @ (moved_ball - origin) - 21.335, abs=1e-8
            )


def test_near_zero_rotation_does_not_lose_the_plane_gap_to_cancellation():
    ball, twist = np.array([0, 0, 31.335123]), np.array([1e-8, 0, 0, 0, 1000, 0])
    coefficients = gap_coefficients(ball, twist, np.zeros(3), np.array([0, 0, 1]), 21.335)
    assert _gap(coefficients, 0) == pytest.approx(10.000123, abs=1e-9)


def test_all_roots_are_found_between_analytic_stationary_points():
    coefficients = gap_coefficients([40, 0, 0], [0, 0, 1000, 0, 0, 0], [0, 0, 0], [1, 0, 0], 20)
    roots, _ = _roots(coefficients, [0, 0.02], 1e-8)
    expected = sorted(
        (sign * np.pi / 3 + 2 * np.pi * turn) / 1000
        for sign in (-1, 1)
        for turn in range(4)
        if 0 < (sign * np.pi / 3 + 2 * np.pi * turn) / 1000 < 0.02
    )
    assert roots == pytest.approx(expected, abs=1e-11)


def test_stationary_tangency_is_not_closing_impact():
    motion, ball, face = inputs()
    motion["body_twist_model_s"] = np.zeros(6)
    ball["center_mm"] = [0, 0, 1021.335]
    result = synthetic_contact(motion, ball, face, [1_000_000_000, 1_020_000_000])
    assert result["status"] == "no_admissible_face_plane_contact"
    assert result["conditional_contacts"]
    assert all(
        row["status"] == "not_closing_face_contact" for row in result["conditional_contacts"]
    )


def test_missing_origin_or_invalid_polygon_never_gets_default_geometry():
    motion, ball, face = inputs()
    face.pop("origin_local_mm")
    assert (
        synthetic_contact(motion, ball, face, [1_000_000_000, 1_020_000_000])["status"]
        == "unavailable"
    )


def test_untyped_time_tuple_cannot_silently_become_a_first_touch_interval():
    motion, ball, face = inputs()
    result = profile_face_contact(motion, ball, face, [1_000_000_000, 1_020_000_000])
    assert result["status"] == "unavailable"
    assert result["reason"] == "contact_missing_first_touch_interval_semantics"


def test_departure_event_cannot_be_relabelled_as_first_touch():
    motion, ball, face = inputs()
    result = profile_face_contact(
        motion, ball, face, [1_000_000_000, 1_020_000_000], interval_kind="ball_departure_interval"
    )
    assert result["status"] == "unavailable"
    assert result["reason"] == "contact_departure_interval_does_not_bound_first_touch"


def test_root_before_used_motion_exposure_is_retained_but_phase_contaminated():
    from dataclasses import replace

    motion, ball, face = inputs()
    late = replace(motion["identity"], frame_index=11, sensor_timestamp_ns=1_012_000_000)
    motion["pre_motion_support"].append(
        {"identity": late, "used_interval_ns": [1_012_000_000, 1_012_298_000]}
    )
    result = synthetic_contact(motion, ball, face, [1_000_000_000, 1_020_000_000])
    assert result["status"] == "phase_contaminated_motion"
    root = result["conditional_contacts"][0]
    assert root["time_ns"] == pytest.approx(1_010_000_000, abs=1)
    assert root["conditional_plane_admissible"]
    assert root["status"] == "phase_contaminated_motion"
    assert result["strike"] is None


def test_missing_motion_support_cannot_be_treated_as_wholly_preimpact():
    motion, ball, face = inputs()
    motion.pop("pre_motion_support")
    result = synthetic_contact(motion, ball, face, [1_000_000_000, 1_020_000_000])
    assert result["status"] == "unavailable"
    assert result["reason"] == "contact_missing_pre_motion_exposure_support"


def test_split_exposure_is_only_pre_motion_support_at_its_declared_contact_root():
    from dataclasses import replace

    motion, ball, face = inputs()
    touch = 1_010_000_000
    split_frame = replace(motion["identity"], frame_index=11, sensor_timestamp_ns=touch - 100_000)
    support = {
        "identity": split_frame,
        "used_interval_ns": [touch - 100_000, touch],
        "split_contact_hypothesis_ns": touch,
    }
    motion["pre_motion_support"].append(support)
    assert (
        synthetic_contact(motion, ball, face, [1_000_000_000, 1_020_000_000])["status"]
        == "conditional_face_contacts"
    )
    support["split_contact_hypothesis_ns"] = touch - 50_000
    support["used_interval_ns"][1] = touch - 50_000
    assert (
        synthetic_contact(motion, ball, face, [1_000_000_000, 1_020_000_000])["status"]
        == "phase_contaminated_motion"
    )


@pytest.mark.parametrize("change", ["foreign_capture", "truncated_exposure", "unknown_time"])
def test_pre_motion_support_must_be_bound_and_cannot_be_arbitrarily_truncated(change):
    from dataclasses import replace

    motion, ball, face = inputs()
    support = motion["pre_motion_support"][0]
    if change == "foreign_capture":
        support["identity"] = replace(support["identity"], capture_sha256="c" * 64)
    elif change == "unknown_time":
        support["identity"] = replace(support["identity"], timestamp_semantics="unknown")
    else:
        support["used_interval_ns"][1] -= 1000
    assert (
        synthetic_contact(motion, ball, face, [1_000_000_000, 1_020_000_000])["status"]
        == "unavailable"
    )
