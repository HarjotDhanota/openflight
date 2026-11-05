"""Tests for the live camera club-delivery estimator and fusion."""

import math
from dataclasses import replace

import numpy as np
import pytest

import openflight.camera.club_delivery as club_delivery_module
from openflight.camera.club_delivery import (
    ApproachPairEstimate,
    CameraDeliveryGeometry,
    CameraOpsReference,
    ChainedDelivery,
    _delivery_from_feature_pair,
    _detect_impact_index,
    _preferred_path_estimate,
    camera_ops_delivery_from_feature_pair,
    combine_approach_estimates,
    estimate_chained_delivery,
)
from openflight.camera.club_motion import ReferenceBall


class _Ball:
    x = 320.0
    y = 200.0
    diameter_px = 28.0


def test_impact_detection_ignores_late_ball_like_brightness_after_trigger():
    frames = np.zeros((60, 9, 9), dtype=np.uint8)
    yy, xx = np.mgrid[:9, :9]
    ball = _Ball()
    ball.x = 4.0
    ball.y = 4.0
    ball.diameter_px = 8.0
    core = (xx - ball.x) ** 2 + (yy - ball.y) ** 2 <= 3**2
    frames[:42, core] = 160
    # Empty background or late motion can accidentally resemble the teed ball.
    frames[59, core] = 160

    assert _detect_impact_index(frames, ball, trigger_index=44) == 41


def test_impact_detection_uses_first_departure_near_trigger():
    frames = np.zeros((60, 9, 9), dtype=np.uint8)
    yy, xx = np.mgrid[:9, :9]
    ball = _Ball()
    ball.x = 4.0
    ball.y = 4.0
    ball.diameter_px = 8.0
    core = (xx - ball.x) ** 2 + (yy - ball.y) ** 2 <= 3**2
    frames[:42, core] = 160
    frames[45, core] = 160  # club/halo briefly resembles the address frame

    assert _detect_impact_index(frames, ball, trigger_index=44) == 41


def test_impact_detection_accepts_camera_departure_after_early_gpio_trigger():
    frames = np.zeros((70, 9, 9), dtype=np.uint8)
    yy, xx = np.mgrid[:9, :9]
    ball = _Ball()
    ball.x = 4.0
    ball.y = 4.0
    ball.diameter_px = 8.0
    core = (xx - ball.x) ** 2 + (yy - ball.y) ** 2 <= 3**2
    frames[:53, core] = 160

    assert _detect_impact_index(frames, ball, trigger_index=44) == 52


def test_the_ball_stage_s_reference_ball_reaches_the_club_stage(monkeypatch):
    shared = ReferenceBall(160.0, 120.0, 14.0, 140)
    seen = {}

    def impact(_frames, ball, **_kwargs):
        seen["ball"] = ball
        return None

    monkeypatch.setattr(club_delivery_module, "_detect_impact_index", impact)
    result = estimate_chained_delivery(
        np.full((60, 200, 320), 200, dtype=np.uint8),
        np.arange(60, dtype=np.int64) * 2_000_000,
        trigger_index=40,
        range_evidence=None,
        geometry=CameraDeliveryGeometry(
            camera_height_m=0.2032,
            radar_height_m=0.1524,
            tee_range_m=1.524,
            ball_height_m=0.04,
            image_width_px=320,
            image_height_px=200,
        ),
        ops_club_speed_mph=80.0,
        reference_ball=shared,
        reference_ball_selected=True,
    )

    # no departure found: the trigger frame stands in, labelled
    assert result.impact_frame == 40
    assert any("contact from the trigger" in note for note in result.notes)
    assert seen["ball"] is shared


def _project_impact_tracks(
    *,
    path_deg: float,
    aoa_deg: float,
    speed_ms: float = 35.0,
    n_features: int = 12,
    camera_lateral_offset_m: float = 0.0,
):
    """Synthetic clubhead features viewed by the rear camera at impact."""
    geometry = CameraDeliveryGeometry(
        camera_height_m=0.20955,
        radar_height_m=0.15875,
        tee_range_m=1.524,
        ball_height_m=0.02135,
        camera_lateral_offset_m=camera_lateral_offset_m,
    )
    ball = _Ball()
    times = np.array([-0.0035, 0.0, 0.0035])
    path = math.radians(path_deg)
    aoa = math.radians(aoa_deg)
    forward = speed_ms / math.sqrt(1.0 + math.tan(path) ** 2 + math.tan(aoa) ** 2)
    velocity = np.array([forward * math.tan(path), forward * math.tan(aoa), forward])
    ball_forward = geometry.tee_range_m
    focal_px = (
        ball.diameter_px
        * math.sqrt(
            geometry.camera_lateral_offset_m**2
            + ball_forward**2
            + (geometry.ball_height_m - geometry.camera_height_m) ** 2
        )
        / geometry.ball_diameter_m
    )
    pitch = math.atan2(
        geometry.ball_height_m - geometry.camera_height_m,
        ball_forward,
    )

    tracks = []
    ranges = []
    for feature in range(n_features):
        lateral_offset = (feature - (n_features - 1) / 2) * 0.001
        height_offset = (feature % 3 - 1) * 0.001
        pixels = []
        feature_ranges = []
        for time_s in times:
            lateral = lateral_offset + velocity[0] * time_s
            height = geometry.ball_height_m + height_offset + velocity[1] * time_s
            forward_m = ball_forward + velocity[2] * time_s
            vertical_m = height - geometry.camera_height_m
            camera_forward = math.cos(pitch) * forward_m + math.sin(pitch) * vertical_m
            camera_vertical = -math.sin(pitch) * forward_m + math.cos(pitch) * vertical_m
            camera_lateral = lateral - geometry.camera_lateral_offset_m
            x_px = geometry.image_width_px / 2 + focal_px * camera_lateral / camera_forward
            y_px = geometry.image_height_px / 2 - focal_px * camera_vertical / camera_forward
            pixels.append((x_px, y_px))
            feature_ranges.append(math.hypot(forward_m, height - geometry.radar_height_m))
        tracks.append(pixels)
        ranges.append(feature_ranges)
    return np.asarray(tracks), times, np.median(np.asarray(ranges), axis=0), ball, geometry


class TestChainedImpactDelivery:
    def test_recovers_known_impact_velocity(self):
        tracks, times, ranges, ball, geometry = _project_impact_tracks(
            path_deg=3.0,
            aoa_deg=-4.0,
        )

        result = _delivery_from_feature_pair(
            tracks[:, :2],
            times[:2],
            ranges[:2],
            ball=ball,
            geometry=geometry,
            ops_club_speed_mph=35.0 * 2.23694,
        )

        assert isinstance(result, ApproachPairEstimate)
        assert result.path_deg == pytest.approx(3.0, abs=0.15)
        assert result.attack_angle_deg == pytest.approx(-4.0, abs=0.15)
        assert result.n_features == 12

    def test_recovers_known_velocity_with_laterally_offset_camera(self):
        tracks, times, ranges, ball, geometry = _project_impact_tracks(
            path_deg=3.0,
            aoa_deg=-4.0,
            camera_lateral_offset_m=-0.08,
        )

        result = _delivery_from_feature_pair(
            tracks[:, :2],
            times[:2],
            ranges[:2],
            ball=ball,
            geometry=geometry,
            ops_club_speed_mph=35.0 * 2.23694,
        )

        assert result.path_deg == pytest.approx(3.0, abs=0.15)
        assert result.attack_angle_deg == pytest.approx(-4.0, abs=0.15)

    def test_mirrored_capture_preserves_physical_path_sign(self):
        tracks, times, ranges, ball, geometry = _project_impact_tracks(
            path_deg=3.0,
            aoa_deg=-4.0,
        )
        mirrored = tracks.copy()
        mirrored[:, :, 0] = geometry.image_width_px - mirrored[:, :, 0]

        result = _delivery_from_feature_pair(
            mirrored[:, :2],
            times[:2],
            ranges[:2],
            ball=ball,
            geometry=replace(geometry, horizontal_pixel_sign=-1.0),
            ops_club_speed_mph=35.0 * 2.23694,
        )

        assert result.path_deg == pytest.approx(3.0, abs=0.15)
        assert result.attack_angle_deg == pytest.approx(-4.0, abs=0.15)

    def test_approach_consensus_keeps_low_confidence_aoa_visible(self):
        path_windows = [
            ApproachPairEstimate(path, -4.0, 1.0, 1.0, 12) for path in (2.5, 3.0, 3.5, 4.0)
        ]
        implausible_aoa = ApproachPairEstimate(3.0, -22.0, 1.0, 1.0, 12)

        result = combine_approach_estimates(
            path_windows,
            attack_estimate=implausible_aoa,
            timing_plausible=True,
        )

        assert result.club_path_deg == pytest.approx(3.25)
        assert result.attack_angle_deg == pytest.approx(-22.0)
        assert result.path_confidence_tier == "high"
        assert result.attack_confidence_tier == "low"

    def test_impact_straddling_path_wins_over_broad_approach_consensus(self):
        path_windows = [
            ApproachPairEstimate(path, -4.0, 1.0, 1.0, 12) for path in (6.0, 7.0, 8.0, 9.0)
        ]
        impact_path = ApproachPairEstimate(3.0, -4.0, 1.0, 1.0, 12)

        result = combine_approach_estimates(
            path_windows,
            attack_estimate=impact_path,
            preferred_path_estimate=impact_path,
            timing_plausible=True,
        )

        assert result.club_path_deg == pytest.approx(3.0)

    def test_path_interval_priority_is_impact_then_final_approach(self):
        # G2 C3: the impact window ends at the last rest frame, never after contact
        impact = ApproachPairEstimate(3.0, -4.0, 1.0, 1.0, 12)
        final_approach = ApproachPairEstimate(7.0, -4.0, 1.0, 1.0, 12)

        assert _preferred_path_estimate({(-4, -1): final_approach}) is final_approach
        assert (
            _preferred_path_estimate(
                {
                    (-4, -1): final_approach,
                    (-3, 0): impact,
                }
            )
            is impact
        )
        assert _preferred_path_estimate({(-2, 1): impact}) is None

    def test_approach_consensus_keeps_unstable_path_visible_as_low_confidence(self):
        path_windows = [
            ApproachPairEstimate(path, -4.0, 1.0, 1.0, 12) for path in (-13.0, 2.5, 5.0, 14.0)
        ]
        attack = ApproachPairEstimate(3.0, -4.2, 1.0, 1.0, 12, attack_angle_sigma_deg=0.6)

        result = combine_approach_estimates(
            path_windows,
            attack_estimate=attack,
            timing_plausible=True,
        )

        assert result.club_path_deg == pytest.approx(3.75)
        assert result.attack_angle_deg == pytest.approx(-4.2)
        assert result.path_confidence_tier == "low"
        assert result.attack_confidence_tier == "high"

    def test_approach_consensus_assigns_medium_to_a_wider_attack_sigma(self):
        # G2 C2: the tier is the propagated sigma (1-2 deg: medium), not a range
        paths = [ApproachPairEstimate(path, -4.0, 1.0, 1.0, 12) for path in (2.5, 3.0, 3.5, 4.0)]
        near_miss = ApproachPairEstimate(3.0, -16.0, 1.31, 3.0, 12, attack_angle_sigma_deg=1.5)

        result = combine_approach_estimates(
            paths,
            attack_estimate=near_miss,
            timing_plausible=True,
        )

        assert result.attack_angle_deg == pytest.approx(-16.0)
        assert result.attack_confidence_tier == "medium"


def _project_camera_ops_pair(
    *,
    path_deg: float,
    aoa_deg: float,
    speed_ms: float = 35.0,
    camera_lateral_offset_m: float = -0.060325,
    camera_yaw_deg: float = 3.0,
    camera_forward_offset_m: float = 0.0,
):
    """Project impact-adjacent club features without supplying radar range."""
    camera_height_m = 0.2032
    radar_height_m = 0.1524
    ball_height_m = 0.04
    ball_forward_m = 1.52
    geometry = CameraDeliveryGeometry(
        camera_height_m=camera_height_m,
        radar_height_m=radar_height_m,
        tee_range_m=math.hypot(ball_forward_m, ball_height_m - radar_height_m),
        ball_height_m=ball_height_m,
        camera_lateral_offset_m=camera_lateral_offset_m,
        camera_forward_offset_m=camera_forward_offset_m,
        image_width_px=640,
        image_height_px=400,
    )
    focal_px = 980.0
    yaw = math.radians(camera_yaw_deg)
    path = math.radians(path_deg)
    aoa = math.radians(aoa_deg)
    forward_speed = speed_ms / math.sqrt(1.0 + math.tan(path) ** 2 + math.tan(aoa) ** 2)
    velocity = np.array(
        [
            forward_speed * math.tan(path),
            forward_speed,
            forward_speed * math.tan(aoa),
        ]
    )
    times = np.array([-0.002, 0.0])

    def project(world_xyz):
        delta = world_xyz - np.array(
            [camera_lateral_offset_m, camera_forward_offset_m, camera_height_m]
        )
        # Inverse of camera-heading rotation: world -> camera coordinates.
        camera_x = math.cos(yaw) * delta[0] - math.sin(yaw) * delta[1]
        camera_y = math.sin(yaw) * delta[0] + math.cos(yaw) * delta[1]
        camera_z = delta[2]
        return np.array(
            [
                geometry.image_width_px / 2 + focal_px * camera_x / camera_y,
                geometry.image_height_px / 2 - focal_px * camera_z / camera_y,
            ]
        )

    contact = np.array([0.0, ball_forward_m, ball_height_m])
    ball_px = project(contact)
    camera_ball_range = np.linalg.norm(
        contact - np.array([camera_lateral_offset_m, camera_forward_offset_m, camera_height_m])
    )
    ball = ReferenceBall(
        x=float(ball_px[0]),
        y=float(ball_px[1]),
        diameter_px=focal_px * geometry.ball_diameter_m / camera_ball_range,
        area_px=400,
    )
    tracks = []
    for index in range(12):
        feature_offset = np.array([0.001 * (index - 5.5), 0.0, 0.001 * ((index % 3) - 1)])
        tracks.append([project(contact + feature_offset + velocity * time_s) for time_s in times])
    head_relative = contact - geometry.camera_origin
    rotation = np.array(
        [[math.cos(yaw), -math.sin(yaw), 0.0], [math.sin(yaw), math.cos(yaw), 0.0], [0.0, 0.0, 1.0]]
    )
    head = geometry.camera_origin + rotation @ head_relative
    ops = geometry.camera_origin + np.array([0.05, 0.0, -0.04])
    los = (head - ops) / np.linalg.norm(head - ops)
    radial = float(los @ (rotation @ velocity)) * 2.23694
    reference = CameraOpsReference(tuple(ops), tuple(head), float(times[-1]), "test_camera")
    geometry = replace(geometry, setup_pitch_deg=0.0, setup_focal_px=focal_px)
    return np.asarray(tracks), times, ball, geometry, radial, reference


class TestCameraOpsFallback:
    @pytest.mark.parametrize("forward_offset", [0.0, 0.03, -0.03])
    def test_recovers_known_delivery_without_iwr_range(self, forward_offset):
        tracks, times, ball, geometry, radial, reference = _project_camera_ops_pair(
            path_deg=3.0,
            aoa_deg=-5.0,
            camera_yaw_deg=0.0,
            camera_forward_offset_m=forward_offset,
        )

        result = camera_ops_delivery_from_feature_pair(
            tracks,
            times,
            ball=ball,
            geometry=geometry,
            ops_club_speed_mph=radial,
            ops_reference=reference,
            timestamp_source="test_camera",
        )

        assert result is not None
        assert result.path_deg == pytest.approx(3.0, abs=0.25)
        assert result.attack_angle_deg == pytest.approx(-5.0, abs=0.25)

    def test_rejects_missing_or_nonphysical_ops_speed(self):
        tracks, times, ball, geometry, _radial, _reference = _project_camera_ops_pair(
            path_deg=3.0,
            aoa_deg=-5.0,
        )

        assert (
            camera_ops_delivery_from_feature_pair(
                tracks,
                times,
                ball=ball,
                geometry=geometry,
                ops_club_speed_mph=0.0,
            )
            is None
        )

    def test_yawed_unit_reports_path_from_its_own_boresight(self):
        # the teed ball does not set the zero: a unit aimed 3 deg right reads 3 deg less path
        tracks, times, ball, geometry, radial, reference = _project_camera_ops_pair(
            path_deg=3.0,
            aoa_deg=-5.0,
            camera_yaw_deg=3.0,
        )

        result = camera_ops_delivery_from_feature_pair(
            tracks,
            times,
            ball=ball,
            geometry=geometry,
            ops_club_speed_mph=radial,
            ops_reference=reference,
            timestamp_source="test_camera",
        )

        assert result is not None
        assert result.path_deg == pytest.approx(0.0, abs=0.25)
        assert result.attack_angle_deg == pytest.approx(-5.0, abs=0.25)

    def test_mirrored_capture_preserves_fallback_path_sign(self):
        tracks, times, ball, geometry, radial, reference = _project_camera_ops_pair(
            path_deg=3.0,
            aoa_deg=-5.0,
            camera_yaw_deg=0.0,
        )
        mirrored_tracks = tracks.copy()
        mirrored_tracks[:, :, 0] = geometry.image_width_px - mirrored_tracks[:, :, 0]
        mirrored_ball = replace(ball, x=geometry.image_width_px - ball.x)

        result = camera_ops_delivery_from_feature_pair(
            mirrored_tracks,
            times,
            ball=mirrored_ball,
            geometry=replace(geometry, horizontal_pixel_sign=-1.0),
            ops_club_speed_mph=radial,
            ops_reference=reference,
            timestamp_source="test_camera",
        )

        assert result is not None
        assert result.path_deg == pytest.approx(3.0, abs=0.25)
        assert result.attack_angle_deg == pytest.approx(-5.0, abs=0.25)

    def test_live_estimator_uses_low_confidence_fallback_without_iwr_range(self, monkeypatch):
        frames = np.full((60, 20, 20), 150, dtype=np.uint8)
        timestamps = np.arange(60, dtype=np.int64) * 2_000_000
        ball = ReferenceBall(10.0, 10.0, 12.0, 120)
        estimate = ApproachPairEstimate(3.0, -5.0, 1.0, 1.0, 12)
        monkeypatch.setattr(
            club_delivery_module, "detect_reference_ball", lambda _frames, **_kwargs: ball
        )
        monkeypatch.setattr(
            club_delivery_module,
            "_detect_impact_index",
            lambda _frames, _ball, trigger_index: 40,
        )
        monkeypatch.setattr(
            club_delivery_module,
            "_clubhead_pair_tracks",
            lambda *_args, **_kwargs: (np.zeros((12, 2, 2)), 5.0),
        )
        monkeypatch.setattr(
            club_delivery_module,
            "camera_ops_delivery_from_feature_pair",
            lambda *_args, **_kwargs: estimate,
        )
        geometry = CameraDeliveryGeometry(
            camera_height_m=0.2032,
            radar_height_m=0.1524,
            tee_range_m=1.524,
            ball_height_m=0.04,
            image_width_px=20,
            image_height_px=20,
        )

        result = estimate_chained_delivery(
            frames,
            timestamps,
            trigger_index=40,
            range_evidence=None,
            geometry=geometry,
            ops_club_speed_mph=80.0,
            ops_reference=CameraOpsReference((0.0, 0.0, 0.0), (0.0, 1.5, 0.04), 0.08, "host"),
        )

        assert result.status == "camera_ops_fallback"
        assert result.attack_angle_deg == pytest.approx(-5.0)
        assert result.club_path_deg == pytest.approx(3.0)
        assert result.attack_confidence_tier == "low"
        assert result.path_confidence_tier == "low"


# ---------------------------------------------------------------------------
# Per-club AoA offsets
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Fusion math
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Trace estimation on synthetic captures
# ---------------------------------------------------------------------------

HEIGHT, WIDTH = 400, 640  # production capture geometry
BALL_XY = (400, 200)
FPS = 288.0


class TestVelocityAngleProjection:
    """Attack angle is the elevation of a 3D velocity, not a 2D ratio.

    ``_velocity_angles`` returned ``atan2(vertical, forward)``, which is the
    elevation only when the club path is zero. The exact elevation divides by
    the full horizontal speed, so the error scales as ``cos(path)``.

    Measured on session 20260825_181734 this is +0.052 deg mean and 0.347 deg
    max at the observed fused club paths -- small, but a systematic,
    path-correlated bias, and it grows to 0.60 deg at a 30 deg path.
    """

    def test_attack_angle_uses_total_horizontal_speed(self):
        """A velocity with lateral motion must not inflate the attack angle."""
        # 10 m/s lateral, 10 m/s vertical, 10 m/s forward.
        # forward-only ratio  -> atan2(10, 10)            = 45 deg
        # true elevation      -> atan2(10, hypot(10, 10)) = 35.264 deg
        velocity = np.array([10.0, 10.0, 10.0])

        path_deg, attack_deg = club_delivery_module._velocity_angles(velocity)

        assert path_deg == pytest.approx(45.0, abs=1e-9)
        assert attack_deg == pytest.approx(math.degrees(math.atan2(10.0, math.hypot(10.0, 10.0))))
        assert attack_deg == pytest.approx(35.26438968, abs=1e-6)

    def test_zero_path_is_unchanged(self):
        """With no lateral motion the two formulations must agree exactly."""
        velocity = np.array([0.0, -3.0, 40.0])

        _path_deg, attack_deg = club_delivery_module._velocity_angles(velocity)

        assert attack_deg == pytest.approx(math.degrees(math.atan2(-3.0, 40.0)))

    def test_club_path_still_uses_forward_speed(self):
        """Path is an azimuth in the horizontal plane and must not change."""
        velocity = np.array([5.0, 99.0, 20.0])

        path_deg, _attack_deg = club_delivery_module._velocity_angles(velocity)

        assert path_deg == pytest.approx(math.degrees(math.atan2(5.0, 20.0)))


def _camera_ops_only(monkeypatch, *, range_evidence=None, **geometry):
    """A chained delivery whose only path estimate is the camera+OPS pair (3.0, -5.0)."""
    ball = ReferenceBall(10.0, 10.0, 12.0, 120)
    monkeypatch.setattr(
        club_delivery_module, "detect_reference_ball", lambda _frames, **_kwargs: ball
    )
    monkeypatch.setattr(
        club_delivery_module, "_detect_impact_index", lambda _frames, _ball, trigger_index: 40
    )
    monkeypatch.setattr(
        club_delivery_module,
        "_clubhead_pair_tracks",
        lambda *_args, **_kwargs: (np.zeros((12, 2, 2)), 5.0),
    )
    monkeypatch.setattr(
        club_delivery_module,
        "camera_ops_delivery_from_feature_pair",
        lambda *_args, **_kwargs: ApproachPairEstimate(3.0, -5.0, 1.0, 1.0, 12),
    )
    return estimate_chained_delivery(
        np.full((60, 20, 20), 150, dtype=np.uint8),
        np.arange(60, dtype=np.int64) * 2_000_000,
        trigger_index=40,
        range_evidence=range_evidence,
        geometry=CameraDeliveryGeometry(
            camera_height_m=0.2032,
            radar_height_m=0.1524,
            tee_range_m=1.524,
            ball_height_m=0.04,
            image_width_px=20,
            image_height_px=20,
            **geometry,
        ),
        ops_club_speed_mph=80.0,
        ops_reference=CameraOpsReference((0.0, 0.0, 0.0), (0.0, 1.5, 0.04), 0.08, "host"),
    )


def test_rejected_iwr_club_track_is_not_used_as_camera_depth(monkeypatch):
    from types import SimpleNamespace  # noqa: PLC0415

    from openflight.iwr6843.club import ClubRangeEvidence  # noqa: PLC0415

    def iwr_pair(*_args, **_kwargs):
        raise AssertionError("a rejected IWR club track must not supply camera depth")

    monkeypatch.setattr(club_delivery_module, "_delivery_from_feature_pair", iwr_pair)
    rejected = ClubRangeEvidence(
        track=SimpleNamespace(range_at=lambda *_args: 1.2),
        geometry=SimpleNamespace(range_res_m=0.046875),
        impact_t_s=0.02,
        status="rejected_club_speed_mismatch",
    )

    result = _camera_ops_only(monkeypatch, range_evidence=rejected)

    assert result.status == "camera_ops_fallback"
    assert result.range_evidence_status == "rejected_club_speed_mismatch"


def _off_centre_ball_scene(*, path_deg: float, aoa_deg: float, ball_offset_px: float = 67.0):
    """A level, unyawed camera above the radar; the ball ``ball_offset_px`` right of centre."""
    camera_height_m, radar_height_m, ball_height_m = 0.095, 0.051, 0.02135
    focal_px, width, height = 933.33, 640, 400
    tee_range_m = 1.30
    lateral_m = ball_offset_px * 1.3 / focal_px
    forward_m = math.sqrt(tee_range_m**2 - lateral_m**2 - (ball_height_m - radar_height_m) ** 2)
    geometry = CameraDeliveryGeometry(
        camera_height_m=camera_height_m,
        radar_height_m=radar_height_m,
        tee_range_m=tee_range_m,
        ball_height_m=ball_height_m,
        image_width_px=width,
        image_height_px=height,
    )
    camera = np.array([0.0, 0.0, camera_height_m])

    def project(point):
        delta = point - camera
        return np.array(
            [
                width / 2 + focal_px * delta[0] / delta[1],
                height / 2 - focal_px * delta[2] / delta[1],
            ]
        )

    contact = np.array([lateral_m, forward_m, ball_height_m])
    ball_px = project(contact)
    ball = ReferenceBall(
        x=float(ball_px[0]),
        y=float(ball_px[1]),
        diameter_px=focal_px * geometry.ball_diameter_m / float(np.linalg.norm(contact - camera)),
        area_px=400,
    )
    path, aoa, speed_ms = math.radians(path_deg), math.radians(aoa_deg), 35.0
    forward_speed = speed_ms / math.sqrt(1.0 + math.tan(path) ** 2 + math.tan(aoa) ** 2)
    velocity = np.array(
        [forward_speed * math.tan(path), forward_speed, forward_speed * math.tan(aoa)]
    )
    times = np.array([-0.001, 0.0])
    tracks, ranges = [], []
    radar = np.array([0.0, 0.0, radar_height_m])
    for index in range(12):
        offset = np.array([0.001 * (index - 5.5), 0.0, 0.001 * ((index % 3) - 1)])
        points = [contact + offset + velocity * time_s for time_s in times]
        tracks.append([project(point) for point in points])
        ranges.append([float(np.linalg.norm(point - radar)) for point in points])
    return np.asarray(tracks), times, np.median(np.asarray(ranges), axis=0), ball, geometry


@pytest.mark.parametrize(("path_deg", "offset_px"), [(3.0, 67.0), (-4.0, -67.0), (0.0, 67.0)])
def test_both_club_path_branches_share_the_unit_boresight_zero(path_deg, offset_px):
    tracks, times, ranges, ball, geometry = _off_centre_ball_scene(
        path_deg=path_deg, aoa_deg=-4.0, ball_offset_px=offset_px
    )
    assert abs(ball.x - geometry.image_width_px / 2) == pytest.approx(abs(offset_px), abs=1.0)

    chained = club_delivery_module._delivery_from_feature_pair(
        tracks,
        times,
        ranges,
        ball=ball,
        geometry=geometry,
        ops_club_speed_mph=35.0 * 2.23694,
    )
    lateral = offset_px * 1.3 / 933.33
    forward = math.sqrt(
        1.3**2 - lateral**2 - (geometry.ball_height_m - geometry.radar_height_m) ** 2
    )
    head = np.array([lateral, forward, geometry.ball_height_m])
    ops = np.array([0.0, 0.0, geometry.radar_height_m])
    direction = np.array([math.tan(math.radians(path_deg)), 1.0, math.tan(math.radians(-4.0))])
    velocity = 35.0 * direction / np.linalg.norm(direction)
    radial = float((head - ops) @ velocity / np.linalg.norm(head - ops)) * 2.23694
    camera_ops = camera_ops_delivery_from_feature_pair(
        tracks,
        times,
        ball=ball,
        geometry=replace(geometry, setup_focal_px=933.33, setup_pitch_deg=0.0),
        ops_club_speed_mph=radial,
        ops_reference=CameraOpsReference(tuple(ops), tuple(head), float(times[-1]), "test_camera"),
        timestamp_source="test_camera",
    )

    assert chained is not None and camera_ops is not None
    assert chained.path_deg == pytest.approx(path_deg, abs=0.2)
    assert camera_ops.path_deg == pytest.approx(chained.path_deg, abs=0.2)


def test_every_camera_club_path_names_its_frame():
    assert ChainedDelivery(status="chained_high").club_path_frame == "unit_boresight"


@pytest.mark.parametrize(
    ("offset_deg", "frame"),
    [
        (None, "unit_boresight"),
        # an explicit 0.0 is a target line straight down the boresight
        (0.0, "target_line"),
        (2.0, "target_line"),
        (-3.0, "target_line"),
    ],
)
def test_horizontal_offset_turns_club_path_like_launch(monkeypatch, offset_deg, frame):
    result = _camera_ops_only(monkeypatch, horizontal_offset_deg=offset_deg)

    assert result.club_path_deg == pytest.approx(3.0 + (offset_deg or 0.0))
    assert result.attack_angle_deg == pytest.approx(-5.0)
    assert result.club_path_frame == frame


def test_camera_contact_is_midway_between_the_last_resting_and_first_departed_frames():
    from openflight.camera.club_delivery import camera_contact_time, frame_clock  # noqa: PLC0415

    frames = np.zeros((60, 9, 9), dtype=np.uint8)
    yy, xx = np.mgrid[:9, :9]
    ball = _Ball()
    ball.x, ball.y, ball.diameter_px = 4.0, 4.0, 8.0
    frames[:42, (xx - ball.x) ** 2 + (yy - ball.y) ** 2 <= 3**2] = 160
    host = np.arange(60, dtype=np.int64) * 3_500_000 + 9_000_000
    sensor = np.arange(60, dtype=np.int64) * 3_500_000 + 2_000

    clock, source = frame_clock(host, sensor)
    contact = camera_contact_time(frames, clock, ball, trigger_index=44, timestamp_source=source)

    assert source == "sensor_timestamp_ns"
    assert contact.impact_frame == 41
    assert contact.contact_ns == pytest.approx((sensor[41] + sensor[42]) / 2.0)
    assert contact.contact_vs_trigger_frame_ms == pytest.approx(-8.75)
    assert frame_clock(host, None)[1] == "host_timestamp_ns"
    assert frame_clock(host, sensor[::-1].copy())[1] == "host_timestamp_ns"
