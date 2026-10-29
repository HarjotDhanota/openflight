"""Guarded face-plane contact profiling of conditional pre-impact rigid motion."""

from __future__ import annotations

import math

import cv2
import numpy as np
from scipy.optimize import brentq

from ..constants import BALL_DIAMETER_MM
from . import club_face_geometry, club_rigid_motion
from .club_sequence import ClubFrameIdentity

# pylint: disable=no-member


def gap_coefficients(ball_in_body_mm, body_twist_mm_s, face_origin_mm, face_normal, radius_mm):
    """Return a*(cos(w*t)-1)+b*sin(w*t)+c*t+g0 for a stationary ball."""
    ball, twist = np.asarray(ball_in_body_mm, float), np.asarray(body_twist_mm_s, float)
    origin, normal = np.asarray(face_origin_mm, float), np.asarray(face_normal, float)
    if (
        ball.shape != (3,)
        or twist.shape != (6,)
        or origin.shape != (3,)
        or normal.shape != (3,)
        or not np.isfinite(np.r_[ball, twist, origin, normal, radius_mm]).all()
        or radius_mm <= 0
        or not np.isclose(np.linalg.norm(normal), 1)
    ):
        raise ValueError("finite contact vectors, unit face normal and positive radius required")
    speed = float(np.linalg.norm(twist[:3]))
    if speed < 1e-10:
        return np.array([0, 0, -normal @ twist[3:], normal @ (ball - origin) - radius_mm, 0])
    axis = twist[:3] / speed
    parallel = axis * (axis @ ball)
    equilibrium = np.cross(axis, twist[3:]) / speed
    perpendicular = ball - parallel - equilibrium
    return np.array(
        [
            normal @ perpendicular,
            -normal @ np.cross(axis, perpendicular),
            -(normal @ axis) * (axis @ twist[3:]),
            normal @ (ball - origin) - radius_mm,
            speed,
        ]
    )


def _gap(coefficients, time_s):
    a, b, c, d, w = coefficients
    return float(-2 * a * np.sin(w * time_s / 2) ** 2 + b * np.sin(w * time_s) + c * time_s + d)


def _rate(coefficients, time_s):
    a, b, c, _, w = coefficients
    return float(-a * w * np.sin(w * time_s) + b * w * np.cos(w * time_s) + c)


def _roots(coefficients, interval_s, tolerance):
    low, high = interval_s
    a, b, c, _, w = coefficients
    amplitude = w * np.hypot(a, b)
    points = [low, high]
    if amplitude > 0 and abs(c) <= amplitude:
        phase = math.atan2(a, b)
        angle = math.acos(float(np.clip(-c / amplitude, -1, 1)))
        if w * (high - low) / np.pi > 4096:
            raise ValueError("contact_profile_rotation_has_too_many_stationary_intervals")
        for sign in (-1, 1):
            first = math.ceil((w * low + phase - sign * angle) / (2 * np.pi))
            last = math.floor((w * high + phase - sign * angle) / (2 * np.pi))
            points.extend(
                (sign * angle - phase + 2 * np.pi * turn) / w for turn in range(first, last + 1)
            )
    points = sorted(set(points))
    candidates = [time for time in points if abs(_gap(coefficients, time)) <= tolerance]
    for first, second in zip(points, points[1:]):
        if _gap(coefficients, first) * _gap(coefficients, second) < 0:
            candidates.append(brentq(lambda t: _gap(coefficients, t), first, second, xtol=1e-13))
    roots = []
    for time in sorted(candidates):
        if not roots or abs(time - roots[-1]) > 1e-10:
            roots.append(time)
    return roots, points


def _pre_motion_support(motion, identity):
    support = motion.get("pre_motion_support")
    if not support:
        raise ValueError("contact_missing_pre_motion_exposure_support")
    result = []
    for row in support:
        bound = row["identity"]
        if isinstance(bound, dict):
            bound = ClubFrameIdentity(**bound)
        if not isinstance(bound, ClubFrameIdentity) or bound.context_key != identity.context_key:
            raise ValueError("contact_motion_support_context_mismatch")
        full = club_rigid_motion.exposure_interval_ns(
            bound.sensor_timestamp_ns, bound.exposure_us, bound.timestamp_semantics
        )
        used = np.asarray(row["used_interval_ns"], float)
        split = row.get("split_contact_hypothesis_ns")
        expected = full if split is None else [full[0], split]
        if (
            used.shape != (2,)
            or not np.isfinite(used).all()
            or used[0] >= used[1]
            or not np.array_equal(used, expected)
            or used[1] > full[1]
        ):
            raise ValueError("contact_invalid_pre_motion_exposure_support")
        result.append((float(used[1]), split))
    return result


def profile_face_contact(
    motion, ball, face, contact_interval_ns, *, gap_tolerance_mm=1e-7, interval_kind=None
):
    """Inspect all plane-contact roots without producing a qualified strike.

    The finite surface and face registration are declared inputs. A successful
    root does not establish actual-club geometry, ball depth, clock qualification
    or first collision with some other part of the club. Contact-derived geometry
    is never an independent angle observation for the estimator that produced it.
    """
    result = {
        "status": "unavailable",
        "conditional_contacts": [],
        "physical_pose": None,
        "strike": None,
        "eligible_as_independent_angle_factor": False,
        "contact_time_is_measured": False,
        "interval_kind": interval_kind,
        "strike_uncertainty_mm": None,
    }
    try:
        if interval_kind is None:
            raise ValueError("contact_missing_first_touch_interval_semantics")
        if interval_kind == "ball_departure_interval":
            raise ValueError("contact_departure_interval_does_not_bound_first_touch")
        if interval_kind not in {
            "synthetic_first_touch_interval",
            "conditional_first_touch_search",
            "independently_bounded_first_touch_interval",
        }:
            raise ValueError("contact_unsupported_first_touch_interval_semantics")
        identity = motion["identity"]
        if not isinstance(identity, ClubFrameIdentity):
            raise ValueError("contact_missing_bound_frame_identity")
        support = _pre_motion_support(motion, identity)
        if ball["capture_sha256"] != identity.capture_sha256:
            raise ValueError("contact_ball_capture_mismatch")
        if (
            not motion["coordinate_frame_id"]
            or ball["coordinate_frame_id"] != motion["coordinate_frame_id"]
        ):
            raise ValueError("contact_coordinate_frame_mismatch")
        if (
            not ball.get("observation_ids")
            or ball.get("motion_model") != "stationary_before_first_touch"
        ):
            raise ValueError("contact_missing_stationary_own_ball_evidence")
        if ball.get("scope") not in {"synthetic_control", "conditional_derived_geometry"}:
            raise ValueError("contact_ball_scope_cannot_grant_measurement")
        if not face.get("face_frame_id") or face.get("scope") not in {
            "synthetic_control",
            "conditional_CAD",
            "independently_registered",
        }:
            raise ValueError("contact_missing_explicit_face_registration")
        if face.get("model_id") != motion.get("model_id"):
            raise ValueError("contact_static_model_face_mismatch")
        low, high = contact_interval_ns
        if (
            not np.isfinite([low, high, gap_tolerance_mm]).all()
            or low > high
            or gap_tolerance_mm <= 0
        ):
            raise ValueError("contact_invalid_time_interval_or_numerical_tolerance")
        unit = float(motion["model_unit_mm"])
        if not np.isfinite(unit) or unit <= 0:
            raise ValueError("contact_missing_declared_model_units")
        transform = club_rigid_motion.from_parameters(motion["parameters"])
        transform[:3, 3] *= unit
        twist = np.asarray(motion["body_twist_model_s"], float).copy()
        if twist.shape != (6,) or not np.isfinite(twist).all():
            raise ValueError("contact_missing_finite_body_twist")
        twist[3:] *= unit
        center = np.asarray(ball["center_mm"], float)
        origin, basis = (
            np.asarray(face["origin_local_mm"], float),
            np.asarray(face["basis_local"], float),
        )
        radius = ball.get("radius_mm", BALL_DIAMETER_MM / 2)
        initial_geometry = club_face_geometry.face_contact_geometry(
            center,
            transform[:3, :3] @ origin + transform[:3, 3],
            transform[:3, :3] @ basis,
            ball_radius_mm=radius,
        )
        polygon = np.asarray(face["polygon_toe_up_mm"], np.float32)
        if (
            polygon.ndim != 2
            or polygon.shape[1] != 2
            or len(polygon) < 3
            or not np.isfinite(polygon).all()
            or not cv2.isContourConvex(polygon)
            or cv2.contourArea(polygon) <= 0
        ):
            raise ValueError("contact_requires_declared_finite_convex_face_support")
        ball_body = transform[:3, :3].T @ (center - transform[:3, 3])
        coefficients = gap_coefficients(ball_body, twist, origin, basis[:, 2], radius)
        interval = (np.asarray([low, high]) - identity.sensor_timestamp_ns) * 1e-9
        roots, critical = _roots(coefficients, interval, gap_tolerance_mm)
        started_inside = _gap(coefficients, interval[0]) < -gap_tolerance_mm
        contacts = []
        for time in roots:
            root_ns = float(identity.sensor_timestamp_ns + time * 1e9)
            phase_supported = time >= -1e-9 and all(
                end <= root_ns + 1 and (split is None or abs(split - root_ns) <= 1)
                for end, split in support
            )
            at = transform @ club_rigid_motion.se3_exp(twist * time)
            world_origin = at[:3, :3] @ origin + at[:3, 3]
            world_basis = at[:3, :3] @ basis
            geometry = club_face_geometry.face_contact_geometry(
                center, world_origin, world_basis, ball_radius_mm=radius
            )
            toe_up = geometry["toe_up_mm"]
            finite_face = cv2.pointPolygonTest(polygon, tuple(toe_up), False) >= 0
            closing = _rate(coefficients, time) < -1e-8
            earlier_penetration = any(
                _gap(coefficients, point) < -gap_tolerance_mm for point in critical if point < time
            )
            valid = finite_face and closing and not started_inside and not earlier_penetration
            reason = (
                "phase_contaminated_motion"
                if valid and not phase_supported
                else "conditional_face_plane_contact"
                if valid
                else (
                    "outside_declared_finite_face"
                    if not finite_face
                    else "not_closing_face_contact"
                    if not closing
                    else "precontact_plane_penetration"
                )
            )
            contacts.append(
                {
                    "time_ns": root_ns,
                    "offset_from_anchor_s": float(time),
                    "normal_gap_velocity_mm_s": _rate(coefficients, time),
                    "finite_face_support": bool(finite_face),
                    "precontact_plane_nonpenetration": not started_inside
                    and not earlier_penetration,
                    "conditional_plane_admissible": bool(valid),
                    "pre_motion_exposure_supported": phase_supported,
                    "status": reason,
                    "conditional_geometry": geometry,
                }
            )
        available = any(
            contact["status"] == "conditional_face_plane_contact" for contact in contacts
        )
        return {
            **result,
            "status": "conditional_face_contacts"
            if available
            else "phase_contaminated_motion"
            if any(row["status"] == "phase_contaminated_motion" for row in contacts)
            else "no_admissible_face_plane_contact",
            "conditional_contacts": contacts,
            "initial_geometry": initial_geometry,
            "interval_start_gap_mm": _gap(coefficients, interval[0]),
            "started_behind_infinite_face_plane": bool(started_inside),
            "gap_coefficients": coefficients.tolist(),
            "stationary_interval_count": len(critical) - 1,
            "face_frame_id": face["face_frame_id"],
            "face_scope": face["scope"],
            "model_id": motion["model_id"],
            "capture_sha256": identity.capture_sha256,
            "coordinate_frame_id": motion["coordinate_frame_id"],
            "ball_observation_ids": list(ball["observation_ids"]),
            "scope": "conditional plane tangency; not actual-club first touch or physical strike",
        }
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        return {**result, "reason": str(exc)}
