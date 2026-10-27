"""E3's second routes: the OPS total-speed projection, full club speed and full smash.

- The OPS total-speed projection (``evaluate_total_speed_projection``) divides the
  canonical OPS radial ball speed by the peak share of the ball's velocity along the OPS
  line of sight (``speed_correction.radial_speed_factor``, the model that held to sd 0.5
  mph on the 12 Oct TrackMan pairs at 1.83 m). It needs a vertical launch and the
  OPS-to-ball geometry, not the horizontal launch; it is the full ball speed's second
  route, used when the launch-based projection refuses.
- The full club speed (``evaluate_full_club_speed_head_track``) projects the one OPS club
  reading the club pick read onto the head's direction at that reading's time, from the
  camera head track: experimental like the track.
- The full smash is the ratio of two full speeds under the weaker one's label.
"""

from __future__ import annotations

import math
from types import SimpleNamespace

import numpy as np
import pytest

from openflight import full_speed
from openflight.camera.head_track import estimate_head_track
from openflight.full_speed import (
    CALCULATED,
    ESTIMATED,
    EXPERIMENTAL,
    NOT_MEASURED,
    evaluate_full_club_speed_head_track,
    evaluate_full_smash,
    evaluate_total_speed_projection,
)
from openflight.speed_correction import DRAG_MPH_PER_MS, MPH_TO_FTS
from tests.test_camera_head_track import (
    BALL_LUF,
    CAMERA_HEIGHT_M,
    CONTACT_NS,
    Swing,
    _hosel,
    _inputs,
)
from tests.test_full_speed import BALL_HEIGHT, RADAR_HEIGHT, RIG, SIDE, TEE_RANGE, _ball_rest

FT = 0.3048


def _ops_position():
    # (forward, right, up) from the hitting surface below the IWR origin
    return np.array([-0.020 + 0.030, -0.085, RADAR_HEIGHT + (-0.047 + 0.050)])


def _peak_radial(v0_mph: float, vertical_deg: float, window_ms: float = 70.0) -> float:
    """The largest OPS radial reading over the window: the ball flies straight along the
    boresight at the launch angle and loses the historical drag rate (the model's own
    physics, written out independently)."""
    ball = _ball_rest()
    ops = _ops_position()
    la = math.radians(vertical_deg)
    direction = np.array([math.cos(la), 0.0, math.sin(la)])
    best = 0.0
    for t_ms in np.arange(0.0, window_ms + 1e-9, 0.5):
        speed = v0_mph - DRAG_MPH_PER_MS * t_ms
        travelled = (v0_mph * MPH_TO_FTS * FT) * t_ms / 1000.0
        los = ball + direction * travelled - ops
        best = max(best, speed * float(direction @ los) / float(np.linalg.norm(los)))
    return best


def _total(**overrides):
    kwargs = dict(
        ball_radial_mph=_peak_radial(120.0, 16.0),
        vertical_deg=16.0,
        vertical_source="radar",
        rig=RIG,
        tee_range_m=TEE_RANGE,
        radar_height_m=RADAR_HEIGHT,
        ball_height_m=BALL_HEIGHT,
        tee_side_offset_m=SIDE,
        tee_range_source="iwr_post_impact",
        tee_side_offset_source="setup",
    )
    kwargs.update(overrides)
    return evaluate_total_speed_projection(**kwargs)


# ---------------------------------------------------------------- OPS total projection


@pytest.mark.parametrize(("v0", "vertical"), [(120.0, 16.0), (95.0, 22.0), (150.0, 11.0)])
def test_the_total_projection_recovers_the_launch_speed(v0, vertical):
    result = _total(ball_radial_mph=_peak_radial(v0, vertical), vertical_deg=vertical)

    assert result["status"] == CALCULATED, result["reason"]
    # the model samples every 2 ms, the synthetic every 0.5 ms
    assert result["value_mph"] == pytest.approx(v0, abs=0.3)
    assert result["value_mph"] > result["radial_mph"]
    assert result["route"] == "ops_total_projection"


def test_the_total_projection_range_names_each_input_and_the_validation_scatter():
    result = _total()

    low, high = result["range_mph"]
    assert low < result["value_mph"] < high
    terms = result["uncertainty_mph"]
    assert set(terms) >= {"vertical_launch", "tee_range", "tee_side_offset", "model_scatter"}
    assert terms["model_scatter"] == pytest.approx(full_speed.TOTAL_MODEL_SCATTER_MPH)
    assert "TrackMan" in result["validation"]
    # the horizontal launch is not an input: noted, not a refusal
    assert any("horizontal" in item for item in result["assumptions"])


@pytest.mark.parametrize(
    ("override", "words"),
    [
        ({"vertical_source": "camera_fallback"}, "camera fallback"),
        ({"tee_range_source": "camera_prior"}, "camera prior"),
        ({"tee_range_source": "camera_ball_size"}, "resting ball's own size"),
        ({"tee_side_offset_m": None, "tee_side_offset_source": None}, "assumed dead ahead"),
        ({"tee_side_offset_source": "camera_resting_ball"}, "camera bearing"),
    ],
)
def test_a_weak_input_makes_the_total_projection_estimated_and_says_why(override, words):
    result = _total(**override)

    assert result["status"] == ESTIMATED
    assert result["value_mph"] is not None
    assert any(words in item for item in result["weak_inputs"]), result["weak_inputs"]
    assert words in result["note"]


def test_an_assumed_side_offset_uses_the_ball_dead_ahead():
    assumed = _total(tee_side_offset_m=None, tee_side_offset_source=None)
    dead_ahead = _total(tee_side_offset_m=0.0)

    assert assumed["value_mph"] == pytest.approx(dead_ahead["value_mph"])
    assert assumed["inputs"]["tee_side_offset_m"] == 0.0


@pytest.mark.parametrize(
    ("override", "words"),
    [
        ({"vertical_deg": None}, "vertical launch is not measured"),
        ({"vertical_source": "estimated"}, "not a measured vertical launch"),
        ({"ball_radial_mph": None}, "no OPS radial ball speed"),
        ({"ball_radial_mph": 0.0}, "no OPS radial ball speed"),
        ({"rig": None}, "rig"),
        ({"rig": SimpleNamespace(ops_offset_mm=None, iwr_origin_mm=(0, 0, 0))}, "OPS offset"),
        ({"tee_range_m": None}, "range"),
    ],
)
def test_the_total_projection_refuses_a_missing_input(override, words):
    result = _total(**override)

    assert result["status"] == NOT_MEASURED
    assert result["value_mph"] is None
    assert words in result["reason"]


# ---------------------------------------------------------------- full club speed

MPH_PER_MPS = 2.23694
WINDOW_MS = 128 / 30.0
# the OPS 60 mm right of, 40 mm below and 10 mm behind the lens (camera image axes, mm)
CLUB_RIG = SimpleNamespace(ops_offset_mm=(60.0, 40.0, -10.0), mic_offset_mm=(0.0, 30.0, 0.0))


def _ops_camera_relative() -> np.ndarray:
    right, down, forward = (value / 1000.0 for value in CLUB_RIG.ops_offset_mm)
    return np.array([right, -down, forward])


def _head_record(swing: Swing) -> dict:
    track = estimate_head_track(_inputs(swing))
    assert track.status == "ok", track.notes
    return track.to_dict()


def _radial_at(swing: Swing, t_ns: float) -> float:
    """The OPS's line-of-sight speed of the hosel at ``t_ns`` (camera-relative frame)."""
    dt = 1e3
    velocity = (_hosel(swing, t_ns + dt) - _hosel(swing, t_ns - dt)) / (2 * dt / 1e9)
    camera = np.array([0.0, CAMERA_HEIGHT_M, 0.0])
    los = _hosel(swing, t_ns) - camera - _ops_camera_relative()
    return float(velocity @ los / np.linalg.norm(los)) * MPH_PER_MPS


def _club_track(swing: Swing, before_contact_ms: float, **changes) -> dict:
    impact_ms = 50.0
    start = impact_ms - before_contact_ms - WINDOW_MS / 2.0
    t_ns = CONTACT_NS - before_contact_ms * 1e6
    track = {
        "clock": "ops_capture",
        "impact_ms": impact_ms,
        "impact_source": "ops_transition",
        "impact_uncertainty_ms": 0.6,
        "window_ms": WINDOW_MS,
        "radial_resolution_mph": 0.05,
        "club_reading": {"window_start_ms": start, "radial_mph": _radial_at(swing, t_ns)},
    }
    track.update(changes)
    return track


def _club(swing: Swing = Swing(), before_contact_ms: float = 8.0, **overrides):
    kwargs = dict(
        ops_track=_club_track(swing, before_contact_ms),
        head_track=_head_record(swing),
        rig=CLUB_RIG,
    )
    kwargs.update(overrides)
    return evaluate_full_club_speed_head_track(**kwargs)


@pytest.mark.parametrize("before_contact_ms", [3.0, 6.0, 9.0])
def test_full_club_speed_recovers_the_head_speed_from_one_reading(before_contact_ms):
    swing = Swing()
    result = _club(swing, before_contact_ms)

    assert result["status"] == EXPERIMENTAL, result["reason"]
    assert result["value_mph"] == pytest.approx(swing.speed_mps * MPH_PER_MPS, rel=0.02)
    assert result["value_mph"] >= result["reading"]["radial_mph"]
    assert result["reading"]["time_before_contact_ms"] == pytest.approx(before_contact_ms)
    assert result["direction_source"] == "camera_head_track"
    assert "experimental" in result["note"]


def test_full_club_speed_range_names_each_input():
    result = _club()

    low, high = result["range_mph"]
    assert low <= result["value_mph"] <= high
    assert set(result["uncertainty_mph"]) >= {
        "radial_resolution",
        "reading_time",
        "fft_window",
        "club_path",
        "attack_angle",
        "head_depth",
        "ops_position",
    }


def test_a_sound_trigger_impact_is_walked_back_by_the_sound_s_flight():
    swing = Swing()
    head = _head_record(swing)
    track = _club_track(swing, 8.0, impact_source="sound_trigger")
    ball = np.asarray(head["turn"]["ball_m"])
    right, down, forward = (value / 1000.0 for value in CLUB_RIG.mic_offset_mm)
    walk_back_ms = 1000.0 * float(np.linalg.norm(ball - np.array([right, -down, forward])))
    walk_back_ms /= full_speed.SPEED_OF_SOUND_M_S
    # the trigger heard contact this much after it happened
    track["impact_ms"] += walk_back_ms

    result = _club(swing, head_track=head, ops_track=track)

    assert result["status"] == EXPERIMENTAL
    assert result["reading"]["impact_walk_back_ms"] == pytest.approx(walk_back_ms)
    assert result["reading"]["time_before_contact_ms"] == pytest.approx(8.0)


@pytest.mark.parametrize(
    ("overrides", "words"),
    [
        ({"head_track": None}, "no camera head track"),
        ({"head_track": {"status": "rejected_no_shaft"}}, "no camera head track"),
        ({"ops_track": None}, "no OPS speed track"),
        ({"rig": None}, "rig"),
        ({"rig": SimpleNamespace(ops_offset_mm=None, mic_offset_mm=None)}, "OPS offset"),
    ],
)
def test_full_club_speed_refuses_a_missing_input(overrides, words):
    result = _club(**overrides)

    assert result["status"] == NOT_MEASURED
    assert result["value_mph"] is None
    assert words in result["reason"]


def test_full_club_speed_refuses_without_a_timed_club_reading():
    swing = Swing()
    result = _club(swing, ops_track=_club_track(swing, 8.0, club_reading=None))

    assert result["status"] == NOT_MEASURED
    assert "one OPS club reading" in result["reason"]


def test_full_club_speed_refuses_a_reading_after_contact_or_far_before_it():
    swing = Swing()
    after = _club(swing, ops_track=_club_track(swing, -3.0))
    early_track = _club_track(swing, 60.0)
    early_track["club_reading"]["radial_mph"] = 60.0
    early = _club(swing, ops_track=early_track)

    assert after["status"] == NOT_MEASURED and "after contact" in after["reason"]
    assert early["status"] == NOT_MEASURED and "before contact" in early["reason"]


def test_a_reading_where_the_head_crosses_the_ops_line_of_sight_is_ill_conditioned():
    """14 ms before contact the head is high and toward the golfer: LOS . dir < cos 30."""
    result = _club(Swing(), 14.0)

    assert result["status"] == NOT_MEASURED
    assert "ill-conditioned" in result["reason"]
    assert result["reading"]["time_before_contact_ms"] == pytest.approx(14.0)


def test_full_club_speed_refuses_a_flagged_head_track():
    swing = Swing()
    head = _head_record(swing)
    head["attack_angle_flag"] = "attack angle -18.0 deg is beyond +-15 deg: flagged, not shown"

    result = _club(swing, head_track=head)

    assert result["status"] == NOT_MEASURED
    assert "flagged" in result["reason"]


# ---------------------------------------------------------------- full smash


def _speed(status, value):
    return {"status": status, "value_mph": value}


@pytest.mark.parametrize(
    ("ball", "club", "label"),
    [
        (CALCULATED, CALCULATED, CALCULATED),
        (CALCULATED, EXPERIMENTAL, EXPERIMENTAL),
        (ESTIMATED, EXPERIMENTAL, EXPERIMENTAL),
        (ESTIMATED, CALCULATED, ESTIMATED),
    ],
)
def test_full_smash_takes_the_weaker_speed_s_label(ball, club, label):
    result = evaluate_full_smash(_speed(ball, 120.0), _speed(club, 88.0))

    assert result["status"] == label
    assert result["value"] == pytest.approx(120.0 / 88.0)


def test_full_smash_is_not_measured_when_either_speed_is_missing():
    missing = {"status": NOT_MEASURED, "value_mph": None}
    assert evaluate_full_smash(_speed(CALCULATED, 120.0), missing)["status"] == NOT_MEASURED
    assert evaluate_full_smash(missing, _speed(EXPERIMENTAL, 88.0))["status"] == NOT_MEASURED


def test_the_resting_ball_is_where_the_synthetic_puts_it():
    """A guard on the fixture: the head track's ball is the scene's ball."""
    ball = np.asarray(_head_record(Swing())["turn"]["ball_m"])
    assert np.linalg.norm(ball - (BALL_LUF - np.array([0.0, CAMERA_HEIGHT_M, 0.0]))) < 0.005
