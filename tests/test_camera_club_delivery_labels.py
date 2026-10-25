"""Club delivery measures and labels; it refuses only where the maths has no input."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

import openflight.camera.club_delivery as club_delivery_module
from openflight.camera.club_delivery import (
    ApproachPairEstimate,
    CameraDeliveryGeometry,
    combine_approach_estimates,
    estimate_chained_delivery,
)
from openflight.camera.club_motion import ReferenceBall

GEOMETRY = CameraDeliveryGeometry(
    camera_height_m=0.2032,
    radar_height_m=0.1524,
    tee_range_m=1.524,
    ball_height_m=0.04,
    image_width_px=320,
    image_height_px=200,
)
BALL = ReferenceBall(160.0, 120.0, 14.0, 140)
ACCEPTED_RANGE = SimpleNamespace(status="accepted", track=None, geometry=None, impact_t_s=0.0)


def _run(monkeypatch, *, level=200, impact=None, trigger_index=40, ops=80.0, evidence=None):
    monkeypatch.setattr(club_delivery_module, "_detect_impact_index", lambda *_a, **_k: impact)
    monkeypatch.setattr(
        club_delivery_module, "_clubhead_pair_tracks", lambda *_a, **_k: (None, None)
    )
    return estimate_chained_delivery(
        np.full((60, 200, 320), level, dtype=np.uint8),
        np.arange(60, dtype=np.int64) * 2_000_000,
        trigger_index=trigger_index,
        range_evidence=evidence,
        geometry=GEOMETRY,
        ops_club_speed_mph=ops,
        reference_ball=BALL,
        reference_ball_selected=True,
    )


def test_no_departure_and_no_trigger_still_has_no_contact_time(monkeypatch):
    result = _run(monkeypatch, impact=None, trigger_index=None)
    assert result.status == "rejected_no_impact"


def test_a_dim_scene_is_a_note_not_a_refusal(monkeypatch):
    result = _run(monkeypatch, level=20, impact=30)
    assert result.status != "rejected_low_light"
    assert result.impact_frame == 30
    assert any(note.startswith("scene dim") for note in result.notes)


def test_without_ops_club_speed_the_radar_path_runs_and_says_speed_is_unchecked(monkeypatch):
    result = _run(monkeypatch, impact=30, ops=None, evidence=ACCEPTED_RANGE)
    assert result.status != "rejected_no_ops_speed"
    assert any("no OPS club speed" in note for note in result.notes)


def test_without_ops_club_speed_or_radar_range_the_depth_has_no_input(monkeypatch):
    # the camera + OPS fallback closes depth with the OPS club speed: it must refuse
    result = _run(monkeypatch, impact=30, ops=None, evidence=None)
    assert result.status == "rejected_no_ops_speed"


@pytest.mark.parametrize(
    ("window", "note"),
    [
        (ApproachPairEstimate(4.0, -30.0, 1.8, 25.0, 5), "outside plausible bounds"),
        (ApproachPairEstimate(2.0, -4.0, None, 3.0, 5), None),  # no OPS ratio
    ],
)
def test_out_of_bounds_or_unchecked_windows_keep_their_values_with_a_label(window, note):
    result = combine_approach_estimates(
        [window], attack_estimate=window, preferred_path_estimate=window, timing_plausible=True
    )
    assert result.club_path_deg == pytest.approx(window.path_deg)
    assert result.path_confidence_tier == "low"
    if note:
        assert result.attack_angle_deg == pytest.approx(window.attack_angle_deg)
        assert result.attack_confidence_tier == "low"
        assert any(note in item for item in result.notes)
