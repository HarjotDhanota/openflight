"""The club-delivery head track on real Oct 27 outdoor swings (fork only: field frames).

Harjot-outdoor-test-2 (OV9281, 640x400, ~3.6 ms frames, sunlit sky saturating the top
of the view), shots 4 and 3, built by scripts/analysis/club_delivery_head_fixture.py
from each shot's logged camera_fusion_context. Before the ball-side head selection the
tracker followed the shaft's glint 100-150 px above the ball (and, on shot 3's early
windows, sky between the golfer's legs): shot 4 read attack angle +9.2 deg with head
positions 0.23-0.36 m above the floor. Shot 3's context has no IWR club range or OPS
reference, so only its tracking is checked.

The heel points below are where the shaft's axis leaves the club at the heel's sole,
rendered and checked by eye (research/head-track-fix-2026-10-31). The angles are not
validated against any reference: Oct 27 has no TrackMan.
"""

import json
from pathlib import Path

import numpy as np
import pytest

from openflight.camera import fusion_processing as fp
from openflight.camera.club_delivery import (
    APPROACH_MEDIUM_AOA_RANGE_DEG,
    APPROACH_PATH_OFFSETS,
    GOLF_BALL_DIAMETER_M,
    HEAD_ABOVE_BALL_MAX_M,
    CameraDeliveryGeometry,
    _clubhead_pair_tracks,
    estimate_chained_delivery,
)
from openflight.camera.club_motion import ReferenceBall

FIXTURE = Path(__file__).parent / "fixtures" / "club_delivery" / "outdoor-test-2.npz"
BACKGROUND_FRAMES = 15
# shot: {frame offset from impact: heel's sole px, checked on renders}
HEELS = {
    4: {-3: (371.9, 163.4), -2: (384.8, 189.0), -1: (389.5, 202.6), 0: (390.7, 207.6)},
    3: {-3: (368.7, 154.7), -2: (390.3, 189.2), -1: (401.0, 208.8), 0: (402.8, 206.6)},
}


@pytest.fixture(scope="module")
def archive():
    with np.load(FIXTURE) as data:
        return {key: data[key] for key in data.files}


def _shot(archive, shot: int) -> dict:
    """The club stage's inputs, with the window behind fifteen background frames."""
    prefix = f"s{shot}_"
    inputs = json.loads(str(archive[prefix + "inputs_json"]))
    window = archive[prefix + "frames"]
    background = archive[prefix + "background"]
    first = int(archive[prefix + "first_frame"])
    shift = BACKGROUND_FRAMES - first
    frames = np.concatenate([np.repeat(background[None], BACKGROUND_FRAMES, axis=0), window])

    def clock(stamps):
        stamps = stamps.astype(np.int64)
        step = int(np.median(np.diff(stamps)))
        lead = stamps[0] - step * np.arange(BACKGROUND_FRAMES, 0, -1)
        return np.concatenate([lead, stamps])

    return {
        "frames": frames,
        "host": clock(archive[prefix + "host_timestamp_ns"]),
        "sensor": clock(archive[prefix + "sensor_timestamp_ns"]),
        "trigger": int(archive[prefix + "trigger_index"]) + shift,
        "impact": int(archive[prefix + "impact_frame"]) + shift,
        "ball": ReferenceBall(**inputs["reference_ball"]),
        "geometry": CameraDeliveryGeometry(**inputs["geometry"]),
        "range_evidence": fp._restore_range(inputs["club_range_evidence"], "club"),
        "ops_club_speed_mph": inputs["ops_club_speed_mph"],
    }


def _pair_tracks(case: dict) -> dict:
    background = np.median(case["frames"][:BACKGROUND_FRAMES], axis=0).astype(np.uint8)
    tracks = {}
    for offsets in APPROACH_PATH_OFFSETS:
        first, second = (case["impact"] + offset for offset in offsets)
        tracks[offsets], _thickness = _clubhead_pair_tracks(
            case["frames"],
            background,
            case["ball"],
            first_idx=first,
            second_idx=second,
            bright_now=255.0,
            dark_bg=230.0,
        )
    return tracks


def test_the_fixture_is_small():
    assert FIXTURE.stat().st_size < 2_000_000


@pytest.mark.parametrize("shot", [4, 3])
def test_every_tracked_point_is_on_the_head_near_the_ball(archive, shot):
    case = _shot(archive, shot)
    ball = case["ball"]
    px_per_m = ball.diameter_px / GOLF_BALL_DIAMETER_M
    tracks = _pair_tracks(case)

    tracked = {offsets: value for offsets, value in tracks.items() if value is not None}
    assert len(tracked) >= 4, sorted(tracks)
    for (seed_offset, _end), points in tracked.items():
        start = points[:, 0]
        above = ball.y - start[:, 1]
        assert above.max() <= HEAD_ABOVE_BALL_MAX_M * px_per_m
        if seed_offset >= -2:  # the last pre-impact frames: within 25 px of the ball's top
            assert above.max() <= ball.diameter_px / 2 + 25.0, (seed_offset, start)
        # never on the golfer's legs (image left, 160-250 px from the ball on these shots)
        assert np.all(np.abs(start[:, 0] - ball.x) <= 0.2 * px_per_m), start
        heel = HEELS[shot][seed_offset]
        assert np.linalg.norm(start[0] - heel) <= 6.0, (seed_offset, start[0], heel)


def test_shot_4_attack_angle_and_heights_come_from_the_head(archive):
    case = _shot(archive, 4)
    result = estimate_chained_delivery(
        case["frames"],
        case["host"],
        trigger_index=case["trigger"],
        range_evidence=case["range_evidence"],
        geometry=case["geometry"],
        ops_club_speed_mph=case["ops_club_speed_mph"],
        reference_ball=case["ball"],
        reference_ball_selected=True,
        sensor_timestamp_ns=case["sensor"],
    )

    assert result.impact_frame == case["impact"]
    # impact-4: the club is still high; refused by name, not tracked up the shaft
    assert any("(-4, -1)" in note and "above the ball" in note for note in result.notes)
    assert result.attack_angle_deg is not None and result.club_path_deg is not None
    # Owner: below about +5 deg; the low side stays inside the estimator's medium bound.
    assert APPROACH_MEDIUM_AOA_RANGE_DEG[0] <= result.attack_angle_deg < 5.0
    geometry = case["geometry"]
    floor_m = geometry.ball_height_m - geometry.ball_diameter_m / 2.0
    times = case["sensor"].astype(float) / 1e9
    late = times[case["impact"] - 2] - 1e-6
    heights = [position.up_m - floor_m for position in result.head_positions]
    assert heights and max(heights) <= HEAD_ABOVE_BALL_MAX_M
    assert all(
        abs(position.up_m - floor_m) <= 0.08
        for position in result.head_positions
        if position.t_s >= late
    ), heights
