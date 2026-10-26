"""The camera head track as the club-delivery chain's last fallback.

When the chained estimator rejects (no stable clubhead, no ball), the head track runs on
the same frames and is published as ``camera_head_track``, labelled experimental, never
fed to the arc or the collision model.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from openflight.camera import club_delivery
from openflight.camera.club_delivery import CameraDeliveryGeometry, estimate_chained_delivery
from openflight.camera.head_track import SOURCE, HeadTrackContext, head_track_context
from openflight.clubs import ClubType
from tests.test_camera_head_track import (
    BALL_LUF,
    CAMERA_HEIGHT_M,
    CONTACT_NS,
    FOCAL,
    HEIGHT,
    WIDTH,
    Swing,
    _hosel,
    _project,
    _render,
    _truth,
)

YAW_OFFSET_DEG = -2.2


def test_contact_window_uses_whole_sensor_clock_gate_interval_not_callback_midpoint():
    archive = {
        "sensor_timestamp_ns": np.arange(4),
        "trigger_boottime_ns": np.int64(130_000_000),
        "gate_boottime_interval_ns": np.array([100_000_000, 101_000_000]),
    }
    context = head_track_context(archive, gate_latency=30_000_000)
    assert context.contact_window_ns == (96_000_000, 100_500_000)


@pytest.mark.parametrize("interval", [[101, 100], [float("nan"), 100], [100]])
def test_invalid_recorded_gate_interval_cannot_fall_back_to_a_callback_guess(interval):
    archive = {
        "sensor_timestamp_ns": np.arange(4),
        "trigger_boottime_ns": np.int64(130_000_000),
        "gate_boottime_interval_ns": np.array(interval),
    }
    assert head_track_context(archive).contact_window_ns is None


def _geometry(**changes) -> CameraDeliveryGeometry:
    camera_ball = float(np.linalg.norm(BALL_LUF - np.array([0.0, CAMERA_HEIGHT_M, 0.0])))
    values = {
        "camera_height_m": CAMERA_HEIGHT_M,
        "radar_height_m": CAMERA_HEIGHT_M,
        "tee_range_m": camera_ball,
        "ball_height_m": float(BALL_LUF[1]),
        "image_width_px": WIDTH,
        "image_height_px": HEIGHT,
        "setup_pitch_deg": 0.0,
        "setup_focal_px": FOCAL,
        "horizontal_offset_deg": YAW_OFFSET_DEG,
    }
    values.update(changes)
    return CameraDeliveryGeometry(**values)


def _context(swing: Swing, **changes) -> HeadTrackContext:
    values = {
        "contact_window_ns": (CONTACT_NS - 1e6, CONTACT_NS + 1e6),
        "exposure_us": np.full(24, swing.exposure_us),
        "handedness": swing.handed,
        "club": ClubType.IRON_7,
    }
    values.update(changes)
    return HeadTrackContext(**values)


@pytest.fixture
def no_stable_clubhead(monkeypatch):
    """The chained estimator's own clubhead tracker finds nothing."""
    monkeypatch.setattr(club_delivery, "_clubhead_pair_tracks", lambda *a, **k: (None, None))


def _run(swing: Swing, *, head_track, **kwargs):
    frames, times = _render(swing)
    _path, _attack, ops_mph = _truth(swing)
    return estimate_chained_delivery(
        frames,
        times,
        trigger_index=19,
        range_evidence=None,
        geometry=kwargs.pop("geometry", _geometry()),
        ops_club_speed_mph=ops_mph,
        sensor_timestamp_ns=times,
        head_track=head_track,
        **kwargs,
    )


def test_no_stable_clubhead_falls_back_to_the_head_track(no_stable_clubhead):
    swing = Swing()
    truth_path, truth_attack, _ops = _truth(swing)
    delivery = _run(swing, head_track=_context(swing))

    assert delivery.status == SOURCE
    assert delivery.confidence_tier == "experimental"
    assert delivery.path_confidence_tier == "experimental"
    assert delivery.attack_confidence_tier == "experimental"
    # the target-line offset turns the published path like every other horizontal angle
    assert delivery.club_path_deg == pytest.approx(truth_path + YAW_OFFSET_DEG, abs=1.0)
    assert delivery.club_path_frame == "target_line"
    assert delivery.attack_angle_deg == pytest.approx(truth_attack, abs=1.5)
    assert delivery.head_track["label"] == "experimental"
    assert delivery.head_track["club_path_sigma_deg"] > 0
    assert any("rejected_no_stable_clubhead" in note for note in delivery.notes)


def test_the_head_track_hands_its_hosel_positions_to_the_arc(no_stable_clubhead):
    """Each tracked frame's hosel, in the radar-centred frame the arc and the ball share."""
    swing = Swing()
    delivery = _run(swing, head_track=_context(swing))

    assert delivery.status == SOURCE
    assert len(delivery.head_positions) == len(delivery.head_track["positions"]) >= 2
    for position in delivery.head_positions:
        truth = _hosel(swing, position.t_s * 1e9)  # lateral, up, forward from the foot
        found = np.array([position.lateral_m, position.up_m, position.forward_m])
        assert np.linalg.norm(found - truth) < 0.05
        assert position.t_s < delivery.contact_t_s
    assert delivery.contact_t_s == pytest.approx(CONTACT_NS / 1e9)
    assert delivery.horizontal_offset_deg == pytest.approx(YAW_OFFSET_DEG)


def test_without_a_head_track_context_the_rejection_stands(no_stable_clubhead):
    delivery = _run(Swing(), head_track=None)

    assert delivery.status == "rejected_no_stable_clubhead"
    assert delivery.head_track is None


def test_a_failed_head_track_keeps_the_rejection_and_says_why(no_stable_clubhead):
    swing = Swing()
    delivery = _run(swing, head_track=_context(swing, contact_window_ns=None))

    assert delivery.status == "rejected_no_stable_clubhead"
    assert delivery.club_path_deg is None and delivery.attack_angle_deg is None
    assert delivery.head_track["status"] == "rejected_no_contact_time"
    assert any("camera head track" in note for note in delivery.notes)


def test_a_flagged_angle_is_withheld_with_its_reason(no_stable_clubhead):
    swing = Swing(phi_contact_deg=-22.0)
    delivery = _run(swing, head_track=_context(swing))

    assert delivery.status == SOURCE
    assert delivery.attack_angle_deg is None
    assert delivery.attack_confidence_tier == "withheld"
    assert delivery.head_track["attack_angle_deg"] < -15.0
    assert any("flagged" in note for note in delivery.notes)
    # a flagged angle withholds the positions too: the arc must not show it another way
    assert delivery.head_positions == ()


def test_no_ball_found_uses_the_setup_ball_and_the_lens_focal_length():
    swing = Swing()
    truth_path, truth_attack, _ops = _truth(swing)
    bx, by = _project(swing, BALL_LUF)
    delivery = _run(
        swing,
        head_track=_context(
            swing, setup_ball={"x": bx, "y": by, "diameter_px": 70.0}, lens_focal_px=FOCAL
        ),
        geometry=_geometry(setup_pitch_deg=None, setup_focal_px=None),
        reference_ball=None,
        reference_ball_selected=True,
    )

    assert delivery.status == SOURCE
    assert any("rejected_no_ball" in note for note in delivery.notes)
    assert delivery.club_path_deg == pytest.approx(truth_path + YAW_OFFSET_DEG, abs=1.5)
    assert delivery.attack_angle_deg == pytest.approx(truth_attack, abs=2.0)


def test_no_ball_and_no_setup_ball_keeps_the_rejection():
    swing = Swing()
    delivery = _run(
        swing,
        head_track=_context(swing),
        reference_ball=None,
        reference_ball_selected=True,
    )

    assert delivery.status == "rejected_no_ball"
    assert delivery.head_track["status"] == "rejected_no_ball_position"


def test_context_takes_the_window_from_the_sensor_clock_trigger():
    archive = {
        "sensor_timestamp_ns": np.arange(24, dtype=np.int64) * 8_333_333,
        "trigger_boottime_ns": np.int64(160_000_000),
        "exposure_us": np.full(24, 145, dtype=np.int32),
    }
    context = head_track_context(archive, gate_latency=800_000.0, handedness="LH")

    lo, hi = context.contact_window_ns
    assert lo == pytest.approx(160_000_000 - 800_000 - 4e6)
    assert hi == pytest.approx(160_000_000 - 800_000 - 0.5e6)
    assert context.handedness == "LH"
    assert context.exposure_us.tolist() == [145] * 24
    assert math.isclose(lo, 155_200_000)


def test_context_without_sensor_timestamps_has_no_window():
    context = head_track_context({"trigger_boottime_ns": np.int64(1)})

    assert context.contact_window_ns is None
