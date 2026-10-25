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
    estimate_camera_ball_flight,
    estimate_horizontal_launch,
    select_camera_assisted_horizontal,
)
from openflight.camera.club_motion import ReferenceBall
from openflight.iwr6843.lcmf import BallRangeEvidence, LCMFResult


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
    vertical = math.radians(20.0)
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


def test_horizontal_launch_uses_measured_vertical_and_tracked_centroids(monkeypatch):
    geometry, anchor, _model, candidates, candidate_times, _evidence, speed_ms = _synthetic_path()
    timestamps = np.concatenate(([0], candidate_times))
    frames = np.zeros(
        (len(timestamps), geometry.image_height_px, geometry.image_width_px), dtype=np.uint8
    )
    track = {index + 1: candidate for index, candidate in enumerate(candidates)}
    monkeypatch.setattr(ball_flight_module, "detect_impact_frame", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(ball_flight_module, "_track_launch_ball", lambda *_args, **_kwargs: track)

    result = estimate_horizontal_launch(
        frames,
        timestamps,
        trigger_ns=0,
        geometry=geometry,
        ops_ball_speed_mph=speed_ms * 2.23694,
        vertical_deg=20.0,
        vertical_source="radar",
        reference_ball=anchor,
    )

    assert result.status == "accepted_horizontal_only_experimental"
    assert result.confidence_tier == "experimental"
    assert result.horizontal_deg == pytest.approx(4.0, abs=0.25)
    assert result.support == len(candidates)
    assert result.depth_source == "ops_speed_iwr_vertical"


def _launch(monkeypatch, **synthetic):
    """``estimate_horizontal_launch`` over a synthetic tracked path, for a given geometry."""
    geometry, anchor, _model, candidates, candidate_times, _evidence, speed_ms = _synthetic_path(
        **synthetic
    )
    timestamps = np.concatenate(([0], candidate_times))
    frames = np.zeros(
        (len(timestamps), geometry.image_height_px, geometry.image_width_px), dtype=np.uint8
    )
    track = {index + 1: candidate for index, candidate in enumerate(candidates)}
    monkeypatch.setattr(ball_flight_module, "detect_impact_frame", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(ball_flight_module, "_track_launch_ball", lambda *_args, **_kwargs: track)

    def launch(geometry=geometry):
        return estimate_horizontal_launch(
            frames,
            timestamps,
            trigger_ns=0,
            geometry=geometry,
            ops_ball_speed_mph=speed_ms * 2.23694,
            vertical_deg=20.0,
            vertical_source="radar",
            reference_ball=anchor,
        )

    return geometry, launch


def test_horizontal_launch_reports_with_the_horizontal_offset(monkeypatch):
    _geometry, launch = _launch(monkeypatch, horizontal_offset_deg=1.0)

    result = launch()

    assert result.horizontal_deg == pytest.approx(5.0, abs=0.25)
    assert result.horizontal_frame == "target_line"


def test_horizontal_launch_withholds_non_radar_vertical_before_tracking(monkeypatch):
    monkeypatch.setattr(
        ball_flight_module,
        "_track_launch_ball",
        lambda *_args, **_kwargs: pytest.fail("estimated vertical must not launch CV tracking"),
    )

    result = estimate_horizontal_launch(
        np.zeros((20, 200, 320), dtype=np.uint8),
        np.arange(20, dtype=np.int64) * 2_000_000,
        trigger_ns=20_000_000,
        geometry=CameraBallGeometry(
            camera_height_m=0.2032,
            radar_height_m=0.1524,
            tee_range_m=1.524,
            ball_height_m=0.04,
            image_width_px=320,
            image_height_px=200,
        ),
        ops_ball_speed_mph=80.0,
        vertical_deg=30.0,
        vertical_source="estimated",
    )

    assert result.status == "withheld_vertical_not_radar"
    assert result.horizontal_deg is None


def test_path_estimate_recovers_known_horizontal_without_iwr_horizontal():
    geometry, _anchor, model, candidates, timestamps, evidence, speed_ms = _synthetic_path()

    result = _path_estimate(
        path=list(enumerate(candidates)),
        frame_indices=list(range(len(candidates))),
        timestamps_ns=timestamps,
        contact_ns=0,
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
        contact_ns=0,
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


def test_path_estimate_does_not_treat_lateral_ball_position_as_target_yaw():
    geometry, _anchor, model, candidates, timestamps, evidence, speed_ms = _synthetic_path(
        horizontal_deg=0.0,
        tee_x_m=-0.03,
    )

    result = _path_estimate(
        path=list(enumerate(candidates)),
        frame_indices=list(range(len(candidates))),
        timestamps_ns=timestamps,
        contact_ns=0,
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
        contact_ns=0,
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
        contact_ns=0,
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


def test_lcmf_range_evidence_carries_the_estimator_status(monkeypatch):
    """A rejected ball track keeps its evidence, marked rejected."""
    from openflight.iwr6843 import lcmf  # noqa: PLC0415

    monkeypatch.setattr(lcmf, "impact_time_s", lambda *_args, **_kwargs: 0.012)
    shot = SimpleNamespace(
        track=SimpleNamespace(
            speed_mph=100.0, rms_bins=0.2, n_inliers=12, t_first=0.0, t_last=0.02
        ),
        geometry=SimpleNamespace(range_res_m=0.046875),
        quality="reject",
        tdm_sign_used=1,
    )
    cal = SimpleNamespace(tee_range_m=1.5, range_bias_m=0.0)

    rejected = lcmf._result_from_track("rejected_track_quality", shot, cal=cal)
    accepted = lcmf._result_from_track("accepted", shot, cal=cal)

    assert rejected.range_evidence.status == "rejected_track_quality"
    assert accepted.range_evidence.status == "accepted"


def _fit(geometry, model, candidates, timestamps, evidence, speed_ms, contact_ns=0):
    result = _path_estimate(
        path=list(enumerate(candidates)),
        frame_indices=list(range(len(candidates))),
        timestamps_ns=timestamps,
        contact_ns=contact_ns,
        range_evidence=evidence,
        ops_ball_speed_mph=speed_ms * 2.23694,
        iwr_vertical_deg=20.0,
        model=model,
        geometry=geometry,
        thresholds=(100, 12, 5),
    )
    assert result is not None
    return result[1]


def _timed_flight(*, trigger_after_contact_s: float = 0.0026):
    """Radar track and frames on one clock: sensor 1.7 s ahead, IWR impact 12 ms in."""
    geometry, _anchor, model, candidates, _timestamps, _evidence, speed_ms = _synthetic_path(
        horizontal_deg=4.0
    )
    tee_forward = math.sqrt(
        geometry.tee_range_m**2 - (geometry.ball_height_m - geometry.radar_height_m) ** 2
    )
    tee = np.array([0.0, tee_forward, geometry.ball_height_m])
    horizontal, vertical = math.radians(4.0), math.radians(20.0)
    forward = speed_ms * math.cos(vertical) * math.cos(horizontal)
    velocity = np.array([forward * math.tan(horizontal), forward, speed_ms * math.sin(vertical)])
    since_contact = np.arange(8, dtype=float) * 0.0035 + 0.007
    radar = np.array([0.0, 0.0, geometry.radar_height_m])
    impact_t_s = 0.012

    class Track:
        def range_at(self, time_s, _resolution):
            flight = max(0.0, time_s - impact_t_s)
            return float(np.linalg.norm(tee + velocity * flight - radar))

    evidence = SimpleNamespace(
        track=Track(), geometry=SimpleNamespace(range_res_m=0.046875), impact_t_s=impact_t_s
    )
    clock_offset_ns = 1_700_000_000
    sensor_ns = np.asarray(since_contact * 1e9, dtype=np.int64) + clock_offset_ns
    contact_ns = float(clock_offset_ns)
    trigger_ns = contact_ns + trigger_after_contact_s * 1e9
    return geometry, model, candidates, sensor_ns, evidence, speed_ms, contact_ns, trigger_ns


def test_flight_timed_from_contact_recovers_the_launch():
    # contact 2.6 ms before the trigger
    *flight, contact_ns, trigger_ns = _timed_flight()

    from_contact = _fit(*flight, contact_ns=contact_ns)
    from_trigger = _fit(*flight, contact_ns=trigger_ns)

    assert from_contact.horizontal_deg == pytest.approx(4.0, abs=0.05)
    assert from_contact.vertical_deg == pytest.approx(20.0, abs=0.05)
    assert abs(from_trigger.vertical_deg - 20.0) > 0.1


def _departing_ball_clip(depart_after_frame: int, *, n_frames: int = 40):
    frames = np.full((n_frames, 200, 320), 40, dtype=np.uint8)
    yy, xx = np.mgrid[:200, :320]
    disk = (xx - 160) ** 2 + (yy - 150) ** 2 <= 7**2
    frames[: depart_after_frame + 1, disk] = 230
    return frames


def _stub_the_sweep(monkeypatch, seen):
    anchor = ReferenceBall(160.0, 150.0, 14.0, 150)
    monkeypatch.setattr(
        ball_flight_module,
        "_select_reference_ball",
        lambda *_args, **_kwargs: (anchor, {"selected_source": "test"}),
    )
    monkeypatch.setattr(
        ball_flight_module, "_camera_model", lambda *_args, **_kwargs: (480.0, 0.0, None)
    )
    candidate = BallCandidate(160.0, 140.0, 150, 14, 14, 0.8, 0.9, 220.0)
    monkeypatch.setattr(ball_flight_module, "_candidates", lambda *_a, **_k: [candidate])
    monkeypatch.setattr(
        ball_flight_module,
        "_pixel_paths",
        lambda nodes, *_a, **_k: [[(index, candidate) for index in range(4)]],
    )
    monkeypatch.setattr(ball_flight_module, "_clean_launch_path", lambda path, _frames: path)

    def path_estimate(**kwargs):
        seen.append(kwargs)
        return 1.0, ball_flight_module._PathEstimate(
            3.0, 20.0, 100.0, 0.0, 0.001, 0.5, 0.2, 6, 20, 25
        )

    monkeypatch.setattr(ball_flight_module, "_path_estimate", path_estimate)


_GEOMETRY_320 = CameraBallGeometry(
    camera_height_m=0.095,
    radar_height_m=0.051,
    tee_range_m=1.5,
    ball_height_m=0.02135,
    image_width_px=320,
    image_height_px=200,
)


def _evidence(**changes):
    return BallRangeEvidence(
        track=SimpleNamespace(),
        geometry=SimpleNamespace(range_res_m=0.046875),
        impact_t_s=0.012,
        **changes,
    )


def test_rejected_iwr_ball_track_is_not_used_as_camera_depth(monkeypatch):
    seen = []
    _stub_the_sweep(monkeypatch, seen)
    host = np.arange(40, dtype=np.int64) * 3_500_000

    estimate = estimate_camera_ball_flight(
        _departing_ball_clip(19),
        host,
        trigger_ns=int(host[20]),
        range_evidence=_evidence(status="rejected_track_quality"),
        geometry=_GEOMETRY_320,
        ops_ball_speed_mph=100.0,
    )

    assert not seen
    assert estimate.status == "rejected_no_iwr_range"
    assert estimate.range_evidence_status == "rejected_track_quality"


def test_ball_flight_times_the_radar_from_the_departure_frame_on_the_sensor_clock(monkeypatch):
    seen = []
    _stub_the_sweep(monkeypatch, seen)
    host = np.arange(40, dtype=np.int64) * 3_500_000 + 5_000_000_000
    sensor = np.arange(40, dtype=np.int64) * 3_500_000 + 1_000_000_000 - 700_000
    evidence = _evidence()

    estimate = estimate_camera_ball_flight(
        _departing_ball_clip(19),
        host,
        trigger_ns=int(host[20]),
        range_evidence=evidence,
        geometry=_GEOMETRY_320,
        ops_ball_speed_mph=100.0,
        sensor_timestamps_ns=sensor,
    )

    assert seen and all(call["range_evidence"] is evidence for call in seen)
    assert seen[0]["timestamps_ns"] == pytest.approx(sensor)
    assert seen[0]["contact_ns"] == pytest.approx((sensor[19] + sensor[20]) / 2.0)
    assert estimate.timing_anchor == "ball_departure"
    assert estimate.contact_frame == 19
    assert estimate.timestamp_source == "sensor_timestamp_ns"
    assert estimate.depth_source == "iwr_range"


def test_ball_flight_without_a_departure_does_not_time_the_radar_from_the_trigger(monkeypatch):
    seen = []
    _stub_the_sweep(monkeypatch, seen)
    host = np.arange(40, dtype=np.int64) * 3_500_000

    estimate = estimate_camera_ball_flight(
        _departing_ball_clip(39),  # the ball never leaves
        host,
        trigger_ns=int(host[20]),
        range_evidence=_evidence(),
        geometry=_GEOMETRY_320,
        ops_ball_speed_mph=100.0,
    )

    assert not seen
    assert estimate.status == "rejected_no_iwr_range"
    assert estimate.timing_anchor == "no_ball_departure"
    assert estimate.timestamp_source == "host_timestamp_ns"


def test_a_low_consensus_flight_is_measured_and_labelled_not_refused(monkeypatch):
    seen = []
    _stub_the_sweep(monkeypatch, seen)
    monkeypatch.setattr(
        ball_flight_module,
        "_path_estimate",
        lambda **_kwargs: (
            1.0,
            ball_flight_module._PathEstimate(3.0, 20.0, 100.0, 0.0, 0.001, 0.5, 4.0, 6, 20, 25),
        ),
    )
    host = np.arange(40, dtype=np.int64) * 3_500_000

    estimate = estimate_camera_ball_flight(
        _departing_ball_clip(19),
        host,
        trigger_ns=int(host[20]),
        range_evidence=_evidence(),
        geometry=_GEOMETRY_320,
        ops_ball_speed_mph=100.0,
    )

    assert estimate.status == "low_consensus"
    assert estimate.confidence_tier == "low"
    assert estimate.horizontal_deg == pytest.approx(3.0)
    assert estimate.vertical_deg == pytest.approx(20.0)


def test_a_low_consensus_camera_stands_in_labelled_only_when_there_is_no_radar():
    preferred = select_camera_assisted_horizontal(
        _estimate("low", 6.0), iwr_horizontal_deg=1.0, iwr_confidence=0.6
    )
    alone = select_camera_assisted_horizontal(
        _estimate("low", 6.0), iwr_horizontal_deg=None, iwr_confidence=None
    )

    assert preferred.selected_deg == 1.0 and preferred.source == "radar"
    assert preferred.camera_horizontal_deg == 6.0
    assert preferred.status == "camera_low_consensus_iwr_preferred"
    assert alone.selected_deg == 6.0
    assert alone.source == "camera_low_consensus"
    assert alone.status == "camera_low_consensus"


def test_a_side_offset_tee_is_fitted_from_where_it_sits(monkeypatch):
    import dataclasses  # noqa: PLC0415

    geometry, launch = _launch(monkeypatch, horizontal_deg=4.0, tee_x_m=0.30)

    told = launch(dataclasses.replace(geometry, ball_lateral_offset_m=0.30))
    untold = launch(dataclasses.replace(geometry, ball_lateral_offset_m=0.0))

    assert told.horizontal_deg == pytest.approx(4.0, abs=0.25)
    assert untold.horizontal_deg is None or abs(untold.horizontal_deg - 4.0) > 2 * abs(
        told.horizontal_deg - 4.0
    )


def test_a_camera_ball_straight_down_the_target_line_reports_zero():
    """Sign pinned: yaw +3 deg, hand-off flag -3, along the target line -> 0."""
    geometry, _anchor, model, candidates, timestamps, evidence, speed_ms = _synthetic_path(
        horizontal_deg=3.0,  # from the boresight: straight down a target line 3 deg right
        horizontal_offset_deg=-3.0,
    )

    fitted = _fit(geometry, model, candidates, timestamps, evidence, speed_ms)

    assert fitted.horizontal_deg == pytest.approx(0.0, abs=0.15)
