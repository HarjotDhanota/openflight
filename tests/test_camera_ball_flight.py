"""Tests for experimental camera-assisted horizontal ball flight."""

import math
from types import SimpleNamespace

import numpy as np
import pytest

import openflight.camera.ball_flight as ball_flight_module
from openflight.camera.ball_flight import (
    BallCandidate,
    CameraBallEstimate,
    CameraBallGeometry,
    _camera_model,
    _confidence_tier,
    _path_estimate,
    _rough_path_score,
    _vertical_confidence_tier,
    estimate_camera_ball_flight,
    select_camera_assisted_horizontal,
    select_camera_vertical_launch,
)
from openflight.camera.club_delivery import ReferenceBallTracker
from openflight.camera.club_motion import ReferenceBall
from openflight.iwr6843.lcmf import BallRangeEvidence, LCMFResult


def test_ball_flight_uses_established_anchor_when_detection_is_missing(monkeypatch):
    tracker = ReferenceBallTracker()
    for x in (159.0, 160.0, 161.0):
        tracker.resolve_stable(ReferenceBall(x, 100.0, 14.0, 140))
    monkeypatch.setattr(
        ball_flight_module,
        "detect_reference_ball",
        lambda _frames: (_ for _ in ()).throw(ValueError("not found")),
    )

    result = estimate_camera_ball_flight(
        np.zeros((20, 200, 320), dtype=np.uint8),
        np.arange(20, dtype=np.int64) * 2_000_000,
        trigger_ns=10_000_000,
        range_evidence=None,
        geometry=CameraBallGeometry(
            camera_height_m=0.2032,
            radar_height_m=0.1524,
            tee_range_m=1.524,
            ball_height_m=0.04,
            image_width_px=320,
            image_height_px=200,
        ),
        ops_ball_speed_mph=100.0,
        ball_tracker=tracker,
    )

    assert result.status != "rejected_reference_ball_not_found"


def _project_world_point(
    point: np.ndarray,
    *,
    camera_height_m: float,
    camera_lateral_offset_m: float = 0.0,
    focal_px: float,
    pitch_rad: float,
    width: int,
    height: int,
) -> tuple[float, float]:
    vector = point - np.array([camera_lateral_offset_m, 0.0, camera_height_m])
    camera_forward = math.cos(pitch_rad) * vector[1] + math.sin(pitch_rad) * vector[2]
    camera_vertical = -math.sin(pitch_rad) * vector[1] + math.cos(pitch_rad) * vector[2]
    return (
        width / 2 + focal_px * vector[0] / camera_forward,
        height / 2 - focal_px * camera_vertical / camera_forward,
    )


def _synthetic_path(
    horizontal_deg: float = 4.0,
    tee_x_m: float = 0.0,
    horizontal_offset_deg: float = 0.0,
    camera_lateral_offset_m: float = 0.0,
    mirror_horizontal: bool = False,
    vertical_deg: float = 20.0,
):
    geometry = CameraBallGeometry(
        camera_height_m=0.20955,
        radar_height_m=0.15875,
        tee_range_m=1.524,
        ball_height_m=0.04,
        camera_lateral_offset_m=camera_lateral_offset_m,
        horizontal_offset_deg=horizontal_offset_deg,
        horizontal_pixel_sign=-1.0 if mirror_horizontal else 1.0,
        image_width_px=640,
        image_height_px=400,
    )
    focal_px = 480.0
    pitch_rad = 0.0
    tee_forward_m = math.sqrt(
        geometry.tee_range_m**2
        - (geometry.ball_height_m - geometry.radar_height_m) ** 2
        - tee_x_m**2
    )
    tee = np.array([tee_x_m, tee_forward_m, geometry.ball_height_m])
    anchor_x, anchor_y = _project_world_point(
        tee,
        camera_height_m=geometry.camera_height_m,
        camera_lateral_offset_m=geometry.camera_lateral_offset_m,
        focal_px=focal_px,
        pitch_rad=pitch_rad,
        width=geometry.image_width_px,
        height=geometry.image_height_px,
    )
    if mirror_horizontal:
        anchor_x = geometry.image_width_px - anchor_x
    camera_origin = np.array([geometry.camera_lateral_offset_m, 0.0, geometry.camera_height_m])
    camera_ball_range = np.linalg.norm(tee - camera_origin)
    anchor = ReferenceBall(
        x=anchor_x,
        y=anchor_y,
        diameter_px=focal_px * geometry.ball_diameter_m / camera_ball_range,
        area_px=140,
    )
    model = _camera_model(anchor, geometry)

    speed_ms = 45.0
    horizontal = math.radians(horizontal_deg)
    vertical = math.radians(vertical_deg)
    forward = speed_ms * math.cos(vertical) * math.cos(horizontal)
    velocity = np.array(
        [
            forward * math.tan(horizontal),
            forward,
            speed_ms * math.sin(vertical),
        ]
    )
    relative_times = np.arange(8, dtype=float) * 0.0035 + 0.007
    positions = np.asarray([tee + velocity * time_s for time_s in relative_times])
    timestamps_ns = np.asarray(relative_times * 1e9, dtype=np.int64)

    candidates = []
    for point in positions:
        x_px, y_px = _project_world_point(
            point,
            camera_height_m=geometry.camera_height_m,
            camera_lateral_offset_m=geometry.camera_lateral_offset_m,
            focal_px=focal_px,
            pitch_rad=pitch_rad,
            width=geometry.image_width_px,
            height=geometry.image_height_px,
        )
        if mirror_horizontal:
            x_px = geometry.image_width_px - x_px
        camera_range = np.linalg.norm(point - camera_origin)
        diameter = focal_px * geometry.ball_diameter_m / camera_range
        candidates.append(
            BallCandidate(
                x=x_px,
                y=y_px,
                area=max(5, round(math.pi * (diameter / 2) ** 2)),
                width=max(2, round(diameter)),
                height=max(2, round(diameter)),
                fill=0.75,
                circularity=0.9,
                mean_intensity=220.0,
            )
        )

    radar_origin = np.array([0.0, 0.0, geometry.radar_height_m])
    radar_ranges = np.linalg.norm(positions - radar_origin, axis=1)

    class SyntheticRangeTrack:
        def range_at(self, time_s, _range_resolution_m):
            return float(np.interp(time_s, relative_times, radar_ranges))

    evidence = SimpleNamespace(
        track=SyntheticRangeTrack(),
        geometry=SimpleNamespace(range_res_m=0.046875),
        impact_t_s=0.0,
    )
    return geometry, anchor, model, candidates, timestamps_ns, evidence, speed_ms


def test_path_estimate_recovers_known_horizontal_without_iwr_horizontal():
    geometry, _anchor, model, candidates, timestamps, evidence, speed_ms = _synthetic_path()

    result = _path_estimate(
        path=list(enumerate(candidates)),
        frame_indices=list(range(len(candidates))),
        timestamps_ns=timestamps,
        trigger_ns=0,
        range_evidence=evidence,
        ops_ball_speed_mph=speed_ms * 2.23694,
        iwr_vertical_deg=20.0,
        model=model,
        geometry=geometry,
        thresholds=(100, 12, 5),
    )

    assert result is not None
    _score, estimate = result
    assert estimate.horizontal_deg == pytest.approx(4.0, abs=0.15)
    assert estimate.vertical_deg == pytest.approx(20.0, abs=0.25)


def test_path_estimate_recovers_physical_sign_from_mirrored_capture():
    geometry, _anchor, model, candidates, timestamps, evidence, speed_ms = _synthetic_path(
        horizontal_deg=4.0,
        mirror_horizontal=True,
    )

    result = _path_estimate(
        path=list(enumerate(candidates)),
        frame_indices=list(range(len(candidates))),
        timestamps_ns=timestamps,
        trigger_ns=0,
        range_evidence=evidence,
        ops_ball_speed_mph=speed_ms * 2.23694,
        iwr_vertical_deg=20.0,
        model=model,
        geometry=geometry,
        thresholds=(100, 12, 5),
    )

    assert result is not None
    _score, estimate = result
    assert estimate.horizontal_deg == pytest.approx(4.0, abs=0.15)


def test_rough_path_scoring_avoids_numpy_in_beam_search(monkeypatch):
    path = [
        (0, BallCandidate(10, 20, 10, 3, 4, 0.7, 0.8, 220)),
        (1, BallCandidate(12, 17, 10, 3, 4, 0.7, 0.8, 220)),
        (2, BallCandidate(14, 14, 10, 3, 4, 0.7, 0.8, 220)),
    ]

    def reject_numpy_median(*_args, **_kwargs):
        raise AssertionError("beam-search scoring must remain scalar")

    monkeypatch.setattr(np, "median", reject_numpy_median)
    assert _rough_path_score(path) == pytest.approx(60.0)


def test_path_estimate_recovers_horizontal_from_apparent_ball_size_without_iwr_range():
    geometry, _anchor, model, candidates, timestamps, _evidence, speed_ms = _synthetic_path()

    result = _path_estimate(
        path=list(enumerate(candidates)),
        frame_indices=list(range(len(candidates))),
        timestamps_ns=timestamps,
        trigger_ns=0,
        range_evidence=None,
        ops_ball_speed_mph=speed_ms * 2.23694,
        iwr_vertical_deg=20.0,
        model=model,
        geometry=geometry,
        thresholds=(100, 12, 5),
    )

    assert result is not None
    _score, estimate = result
    assert estimate.horizontal_deg == pytest.approx(4.0, abs=0.75)
    assert estimate.vertical_deg == pytest.approx(20.0, abs=1.5)


def test_path_estimate_does_not_treat_lateral_ball_position_as_target_yaw():
    geometry, _anchor, model, candidates, timestamps, evidence, speed_ms = _synthetic_path(
        horizontal_deg=0.0,
        tee_x_m=-0.03,
    )

    result = _path_estimate(
        path=list(enumerate(candidates)),
        frame_indices=list(range(len(candidates))),
        timestamps_ns=timestamps,
        trigger_ns=0,
        range_evidence=evidence,
        ops_ball_speed_mph=speed_ms * 2.23694,
        iwr_vertical_deg=20.0,
        model=model,
        geometry=geometry,
        thresholds=(100, 12, 5),
    )

    assert result is not None
    _score, estimate = result
    assert estimate.horizontal_deg == pytest.approx(0.0, abs=0.15)


def test_path_estimate_accounts_for_camera_lateral_translation():
    geometry, _anchor, model, candidates, timestamps, evidence, speed_ms = _synthetic_path(
        horizontal_deg=4.0,
        camera_lateral_offset_m=0.08,
    )

    result = _path_estimate(
        path=list(enumerate(candidates)),
        frame_indices=list(range(len(candidates))),
        timestamps_ns=timestamps,
        trigger_ns=0,
        range_evidence=evidence,
        ops_ball_speed_mph=speed_ms * 2.23694,
        iwr_vertical_deg=20.0,
        model=model,
        geometry=geometry,
        thresholds=(100, 12, 5),
    )

    assert result is not None
    _score, estimate = result
    assert estimate.horizontal_deg == pytest.approx(4.0, abs=0.15)
    assert estimate.vertical_deg == pytest.approx(20.0, abs=0.25)


def test_path_estimate_applies_setup_level_horizontal_offset():
    geometry, _anchor, model, candidates, timestamps, evidence, speed_ms = _synthetic_path(
        horizontal_deg=4.0,
        horizontal_offset_deg=-0.45,
    )

    result = _path_estimate(
        path=list(enumerate(candidates)),
        frame_indices=list(range(len(candidates))),
        timestamps_ns=timestamps,
        trigger_ns=0,
        range_evidence=evidence,
        ops_ball_speed_mph=speed_ms * 2.23694,
        iwr_vertical_deg=20.0,
        model=model,
        geometry=geometry,
        thresholds=(100, 12, 5),
    )

    assert result is not None
    _score, estimate = result
    assert estimate.horizontal_deg == pytest.approx(3.55, abs=0.15)


def test_high_confidence_requires_tight_local_trajectory_stability():
    assert _confidence_tier(20, 0.2, 0.49) == "high"
    assert _confidence_tier(20, 0.2, 0.51) == "experimental"
    assert _confidence_tier(20, 1.1, 0.2) == "withheld"


def _estimate(tier: str, angle: float | None) -> CameraBallEstimate:
    return CameraBallEstimate(
        status="accepted" if angle is not None else "rejected_no_stable_path",
        confidence_tier=tier,
        horizontal_deg=angle,
    )


def test_high_camera_estimate_replaces_iwr_but_preserves_both_values():
    decision = select_camera_assisted_horizontal(
        _estimate("high", 0.6),
        iwr_horizontal_deg=17.9,
        iwr_confidence=0.8,
    )

    assert decision.selected_deg == pytest.approx(0.6)
    assert decision.source == "camera_assisted_experimental"
    assert decision.iwr_horizontal_deg == pytest.approx(17.9)
    assert decision.camera_iwr_delta_deg == pytest.approx(-17.3)
    assert decision.status == "camera_assisted_high"


def test_experimental_camera_disagreement_keeps_measured_camera_trajectory():
    decision = select_camera_assisted_horizontal(
        _estimate("experimental", 5.0),
        iwr_horizontal_deg=-2.0,
        iwr_confidence=0.7,
    )

    assert decision.selected_deg == pytest.approx(5.0)
    assert decision.source == "camera_assisted_experimental"
    assert decision.confidence == pytest.approx(0.3)
    assert decision.camera_horizontal_deg == pytest.approx(5.0)
    assert decision.status == "camera_experimental_disagreement"


def test_experimental_camera_agreement_is_selected():
    decision = select_camera_assisted_horizontal(
        _estimate("experimental", 1.0),
        iwr_horizontal_deg=-1.0,
        iwr_confidence=0.7,
    )

    assert decision.selected_deg == pytest.approx(1.0)
    assert decision.source == "camera_assisted_experimental"
    assert decision.confidence == pytest.approx(0.45)
    assert decision.status == "camera_assisted_experimental_agreement"


def test_experimental_camera_is_retained_when_iwr_is_unavailable():
    decision = select_camera_assisted_horizontal(
        _estimate("experimental", 5.0),
        iwr_horizontal_deg=None,
        iwr_confidence=None,
    )

    assert decision.selected_deg == pytest.approx(5.0)
    assert decision.source == "camera_assisted_experimental"
    assert decision.confidence == pytest.approx(0.3)
    assert decision.status == "camera_experimental_no_iwr"


def test_camera_size_depth_is_labeled_as_camera_only_fallback():
    estimate = CameraBallEstimate(
        status="accepted_camera_only",
        confidence_tier="experimental",
        horizontal_deg=3.0,
        depth_source="camera_size",
    )

    decision = select_camera_assisted_horizontal(
        estimate,
        iwr_horizontal_deg=None,
        iwr_confidence=None,
    )

    assert decision.selected_deg == pytest.approx(3.0)
    assert decision.source == "camera_only_experimental"
    assert decision.confidence == pytest.approx(0.3)
    assert decision.status == "camera_only_experimental"


def test_camera_size_depth_remains_selected_when_iwr_is_available():
    estimate = CameraBallEstimate(
        status="accepted_camera_only",
        confidence_tier="experimental",
        horizontal_deg=3.0,
        depth_source="camera_size",
    )

    decision = select_camera_assisted_horizontal(
        estimate,
        iwr_horizontal_deg=-8.0,
        iwr_confidence=0.9,
    )

    assert decision.selected_deg == pytest.approx(3.0)
    assert decision.source == "camera_only_experimental"
    assert decision.confidence == pytest.approx(0.3)
    assert decision.status == "camera_only_experimental"


def test_withheld_camera_falls_back_to_unchanged_iwr():
    decision = select_camera_assisted_horizontal(
        _estimate("withheld", None),
        iwr_horizontal_deg=-2.0,
        iwr_confidence=0.7,
    )

    assert decision.selected_deg == pytest.approx(-2.0)
    assert decision.source == "radar"
    assert decision.confidence == pytest.approx(0.7)
    assert decision.status == "camera_withheld_fallback_iwr"


def test_withheld_camera_rejects_implausible_iwr_fallback():
    decision = select_camera_assisted_horizontal(
        _estimate("withheld", None),
        iwr_horizontal_deg=38.0,
        iwr_confidence=0.95,
    )

    assert decision.selected_deg is None
    assert decision.source is None
    assert decision.confidence is None
    assert decision.iwr_horizontal_deg == pytest.approx(38.0)
    assert decision.status == "camera_withheld_iwr_implausible"


def test_lcmf_ball_range_evidence_is_transient():
    evidence = BallRangeEvidence(
        track=SimpleNamespace(name="ball-track"),
        geometry=SimpleNamespace(range_res_m=0.046875),
        impact_t_s=0.012,
    )
    result = LCMFResult(
        status="accepted",
        angle_deg=20.0,
        range_evidence=evidence,
    )

    assert result.range_evidence is evidence
    assert "range_evidence" not in result.to_dict()


# ---------------------------------------------------------------------------
# Camera-derived vertical launch, experimentally.
#
# UNVALIDATED AGAINST A REFERENCE INSTRUMENT. What these tests establish is
# that the reconstruction returns the launch angle it was given and that the
# status paths withhold when their evidence is missing. Whether the
# reconstruction is closer to the truth than the shipped LCMF launch angle is
# NOT settled here and has never been settled against a launch monitor.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("vertical_deg", [16.0, 20.0, 24.0, 27.0, 32.0])
def test_vertical_launch_is_recovered_across_the_iron_band(vertical_deg):
    """The ball's camera rays plus the IWR range walk return the launch we set."""
    geometry, _anchor, model, candidates, timestamps, evidence, speed_ms = _synthetic_path(
        vertical_deg=vertical_deg
    )

    result = _path_estimate(
        path=list(enumerate(candidates)),
        frame_indices=list(range(len(candidates))),
        timestamps_ns=timestamps,
        trigger_ns=0,
        range_evidence=evidence,
        ops_ball_speed_mph=speed_ms * 2.23694,
        iwr_vertical_deg=None,
        model=model,
        geometry=geometry,
        thresholds=(100, 12, 5),
    )

    assert result is not None
    _score, estimate = result
    assert estimate.vertical_deg == pytest.approx(vertical_deg, abs=0.3)


def test_vertical_launch_does_not_lean_on_the_radar_prior():
    """Handing the scorer a wrong LCMF prior must not move the reconstruction.

    The prior only ranks candidate paths; it must never be blended into the
    answer, or the camera would stop being independent evidence about the
    launch angle it is meant to cross-check.
    """
    geometry, _anchor, model, candidates, timestamps, evidence, speed_ms = _synthetic_path(
        vertical_deg=27.0
    )
    common = {
        "path": list(enumerate(candidates)),
        "frame_indices": list(range(len(candidates))),
        "timestamps_ns": timestamps,
        "trigger_ns": 0,
        "range_evidence": evidence,
        "ops_ball_speed_mph": speed_ms * 2.23694,
        "model": model,
        "geometry": geometry,
        "thresholds": (100, 12, 5),
    }
    honest = _path_estimate(iwr_vertical_deg=27.0, **common)
    misled = _path_estimate(iwr_vertical_deg=18.0, **common)

    assert honest is not None and misled is not None
    assert misled[1].vertical_deg == pytest.approx(honest[1].vertical_deg, abs=1e-9)


def test_vertical_launch_is_accepted_on_the_iwr_range_walk():
    """A stable consensus on radar depth is offered, with its own provenance."""
    estimate = CameraBallEstimate(
        status="accepted",
        confidence_tier="high",
        horizontal_deg=1.2,
        vertical_deg=24.4,
        support=18,
        vertical_parameter_mad_deg=0.2,
        depth_source="iwr_range",
    )
    decision = select_camera_vertical_launch(estimate, lcmf_vertical_deg=19.6)

    assert decision.status == "accepted"
    assert decision.vertical_deg == pytest.approx(24.4)
    assert decision.confidence_tier in {"high", "experimental"}
    assert decision.depth_source == "iwr_range"
    assert decision.lcmf_vertical_deg == pytest.approx(19.6)
    assert decision.lcmf_delta_deg == pytest.approx(4.8)


def test_vertical_launch_is_withheld_without_the_iwr_range_walk():
    """Apparent ball size is not depth enough for an elevation angle.

    The horizontal estimator accepts a camera-only fallback because bearing
    barely depends on depth. Elevation does, so the value is computed, kept and
    labelled -- never offered.
    """
    estimate = CameraBallEstimate(
        status="accepted_camera_only",
        confidence_tier="experimental",
        horizontal_deg=1.2,
        vertical_deg=24.4,
        support=18,
        vertical_parameter_mad_deg=0.2,
        depth_source="camera_size",
    )
    decision = select_camera_vertical_launch(estimate, lcmf_vertical_deg=19.6)

    assert decision.status == "withheld_no_iwr_range_walk"
    assert decision.vertical_deg is None
    assert decision.computed_vertical_deg == pytest.approx(24.4)
    assert decision.confidence_tier == "withheld"


def test_vertical_launch_is_withheld_when_the_sweep_disagrees_with_itself():
    """A vertical angle that moves with the detector thresholds is not a measurement."""
    estimate = CameraBallEstimate(
        status="accepted",
        confidence_tier="high",
        horizontal_deg=1.2,
        vertical_deg=24.4,
        support=18,
        vertical_parameter_mad_deg=4.0,
        depth_source="iwr_range",
    )
    decision = select_camera_vertical_launch(estimate, lcmf_vertical_deg=19.6)

    assert decision.status == "withheld_unstable_vertical_consensus"
    assert decision.vertical_deg is None
    assert decision.computed_vertical_deg == pytest.approx(24.4)


def test_vertical_launch_is_rejected_when_no_ball_path_was_found():
    """No path, no angle, and the reason is the estimator's own."""
    decision = select_camera_vertical_launch(
        CameraBallEstimate(status="rejected_no_stable_path"),
        lcmf_vertical_deg=19.6,
    )

    assert decision.status == "rejected_no_stable_path"
    assert decision.vertical_deg is None
    assert decision.computed_vertical_deg is None


def test_vertical_launch_reports_no_delta_without_an_lcmf_value():
    """The comparison is optional; its absence is not a rejection."""
    estimate = CameraBallEstimate(
        status="accepted",
        confidence_tier="high",
        horizontal_deg=1.2,
        vertical_deg=24.4,
        support=18,
        vertical_parameter_mad_deg=0.2,
        depth_source="iwr_range",
    )
    decision = select_camera_vertical_launch(estimate, lcmf_vertical_deg=None)

    assert decision.status == "accepted"
    assert decision.vertical_deg == pytest.approx(24.4)
    assert decision.lcmf_delta_deg is None


def test_vertical_confidence_needs_both_support_and_agreement():
    """Neither a wide sweep nor a tight one alone earns the top tier."""
    assert _vertical_confidence_tier(9, 0.2) == "high"
    assert _vertical_confidence_tier(9, 0.9) == "experimental"
    assert _vertical_confidence_tier(1, 0.2) == "withheld"
    assert _vertical_confidence_tier(9, 3.0) == "withheld"


def _render_ball_flight(geometry, anchor, candidates):
    """Frames carrying a bright disc at each synthetic ball position."""
    cv2 = pytest.importorskip("cv2")
    frames = np.full(
        (len(candidates), geometry.image_height_px, geometry.image_width_px), 40, np.uint8
    )
    for index, candidate in enumerate(candidates):
        cv2.circle(
            frames[index],
            (int(round(candidate.x)), int(round(candidate.y))),
            max(2, int(round(math.sqrt(4.0 * candidate.area / math.pi) / 2.0))),
            235,
            -1,
        )
        # The teed ball has to remain findable as the size and pose anchor.
        cv2.circle(
            frames[index],
            (int(round(anchor.x)), int(round(anchor.y))),
            max(2, int(round(anchor.diameter_px / 2.0))),
            235,
            -1,
        )
    return frames


def test_estimate_recovers_a_rendered_vertical_launch_and_publishes_its_spread():
    """Synthetic ballistic frames, end to end, through the whole sweep.

    UNVALIDATED AGAINST A REFERENCE INSTRUMENT. This shows the reconstruction
    returns the launch angle it was given, not that it is right on real pixels.
    """
    geometry, anchor, _model, candidates, timestamps, evidence, speed_ms = _synthetic_path(
        vertical_deg=24.0
    )
    frames = _render_ball_flight(geometry, anchor, candidates)

    estimate = estimate_camera_ball_flight(
        frames,
        timestamps,
        trigger_ns=0,
        range_evidence=evidence,
        geometry=geometry,
        ops_ball_speed_mph=speed_ms * 2.23694,
        # The anchor's apparent SIZE sets the focal scale, so a rasterised disc
        # measured back off its own antialiased edge would set the scale from a
        # rendering artefact rather than from the geometry under test. The ball
        # detector has its own tests; here the anchor is pinned to truth.
        # (`resolve` is supplied too: the estimator's `getattr` default is
        # evaluated eagerly, so a tracker without it raises even when
        # `resolve_stable` is present.)
        ball_tracker=SimpleNamespace(
            resolve_stable=lambda _candidate: (anchor, "test_anchor"),
            resolve=lambda _candidate: (anchor, "test_anchor"),
        ),
    )

    assert estimate.vertical_deg == pytest.approx(24.0, abs=0.3)
    assert estimate.vertical_parameter_mad_deg is not None
    assert estimate.depth_source == "iwr_range"

    decision = select_camera_vertical_launch(estimate, lcmf_vertical_deg=19.5)
    assert decision.computed_vertical_deg == pytest.approx(24.0, abs=0.3)
    assert decision.lcmf_delta_deg == pytest.approx(4.5, abs=0.3)


def test_estimate_publishes_no_vertical_spread_when_it_found_no_ball():
    """The field exists on every result; a failed capture leaves it empty."""
    geometry, anchor, _model, _candidates, timestamps, evidence, speed_ms = _synthetic_path(
        vertical_deg=24.0
    )
    frames = np.zeros(
        (len(timestamps), geometry.image_height_px, geometry.image_width_px), np.uint8
    )
    estimate = estimate_camera_ball_flight(
        frames,
        timestamps,
        trigger_ns=0,
        range_evidence=evidence,
        geometry=geometry,
        ops_ball_speed_mph=speed_ms * 2.23694,
        ball_tracker=SimpleNamespace(fallback=lambda: anchor),
    )

    assert estimate.vertical_parameter_mad_deg is None
    assert estimate.vertical_deg is None
    assert select_camera_vertical_launch(estimate, lcmf_vertical_deg=19.5).vertical_deg is None
