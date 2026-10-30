"""Full ball speed by measured projection, full club speed and full smash.

Synthetic geometry gives the radial reading the OPS would see from a known launch, OPS and ball
position; the module must recover the full speed from it.
"""

from __future__ import annotations

import json
import math
from types import SimpleNamespace

import numpy as np
import pytest

from openflight import full_speed
from openflight.full_speed import (
    BALL_SOURCE,
    CALCULATED,
    MIN_PROJECTION,
    NOT_MEASURED,
    evaluate_full_ball_speed,
    evaluate_full_club_speed,
    evaluate_full_smash,
    select_ball_reading,
)

MPH = 0.44704
WINDOW_MS = 128 / 30.0  # one 128-sample FFT window at 30 ksps
# camera image axes, mm: OPS 85 mm left, 47 mm below, 20 mm behind the lens;
# the IWR origin 50 mm below and 30 mm behind it
RIG = SimpleNamespace(ops_offset_mm=(-85.0, 47.0, -20.0), iwr_origin_mm=(0.0, 50.0, -30.0))
RADAR_HEIGHT = 0.052
BALL_HEIGHT = 0.02135
TEE_RANGE = 2.10
SIDE = 0.04


def _ops_position():
    # (forward, right, up) from the hitting surface below the IWR origin
    return np.array([-0.020 + 0.030, -0.085, RADAR_HEIGHT + (-0.047 + 0.050)])


def _ball_rest():
    vertical = BALL_HEIGHT - RADAR_HEIGHT
    forward = math.sqrt(TEE_RANGE**2 - vertical**2 - SIDE**2)
    return np.array([forward, SIDE, BALL_HEIGHT])


def _direction(vertical_deg, horizontal_boresight_deg):
    v, h = math.radians(vertical_deg), math.radians(horizontal_boresight_deg)
    return np.array([math.cos(v) * math.cos(h), math.cos(v) * math.sin(h), math.sin(v)])


def _radial_at(v0_mph, t_s, vertical_deg, horizontal_boresight_deg, *, drag=True):
    """The OPS's radial reading at ``t_s`` after separation, by a fine forward integration."""
    d = _direction(vertical_deg, horizontal_boresight_deg)
    v, s, steps = v0_mph * MPH, 0.0, 4000
    dt = t_s / steps
    for _ in range(steps):
        a = full_speed.drag_deceleration_mps2(v, vertical_deg) if drag else 0.0
        s += v * dt - 0.5 * a * dt * dt
        v -= a * dt
    p = _ball_rest() + d * s
    los = p - _ops_position()
    return v * float(np.dot(los / np.linalg.norm(los), d)) / MPH


def _track(v0_mph, vertical, horizontal_boresight, *, impact_ms=40.0, starts=None, **kw):
    starts = starts if starts is not None else [impact_ms + 0.5 + 1.0667 * i for i in range(20)]
    outbound = [
        [
            start,
            _radial_at(
                v0_mph, (start + WINDOW_MS / 2 - impact_ms) / 1000.0, vertical, horizontal_boresight
            ),
        ]
        for start in starts
    ]
    track = {
        "impact_ms": impact_ms,
        "impact_source": "ops_transition",
        "impact_uncertainty_ms": 0.6,
        "window_ms": WINDOW_MS,
        "canonical_radial_mph": max(speed for _, speed in outbound),
        "outbound": outbound,
    }
    track.update(kw)
    return track


def _evaluate(track, **overrides):
    kwargs = dict(
        ops_track=track,
        vertical_deg=16.0,
        vertical_source="radar",
        horizontal_deg=2.0,
        horizontal_source="radar",
        horizontal_status=None,
        horizontal_frame="unit_boresight",
        horizontal_offset_deg=None,
        rig=RIG,
        tee_range_m=TEE_RANGE,
        radar_height_m=RADAR_HEIGHT,
        ball_height_m=BALL_HEIGHT,
        tee_side_offset_m=SIDE,
    )
    kwargs.update(overrides)
    return evaluate_full_ball_speed(**kwargs)


@pytest.mark.parametrize(
    ("vertical", "expected"),
    [
        (16.0, CALCULATED),
        (np.float32(16.0), CALCULATED),
        (np.int64(16), CALCULATED),
        (True, NOT_MEASURED),
        (np.bool_(True), NOT_MEASURED),
        (math.nan, NOT_MEASURED),
        (math.inf, NOT_MEASURED),
        (10**400, NOT_MEASURED),
    ],
)
def test_full_speed_admission_accepts_finite_real_scalars(vertical, expected):
    track = {
        "impact_ms": 40.0,
        "impact_source": "ops_transition",
        "impact_uncertainty_ms": 0.6,
        "window_ms": WINDOW_MS,
        "outbound": [[44.0, 115.0]],
        "canonical_radial_mph": 115.0,
    }

    result = _evaluate(track, vertical_deg=vertical)

    assert result["status"] == expected
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize(
    "v0, vertical, horizontal",
    [(120.0, 16.0, 2.0), (165.0, 11.0, -3.0), (95.0, 24.0, 4.0), (140.0, 8.0, 0.0)],
)
def test_synthetic_radial_recovers_the_full_speed_at_separation(v0, vertical, horizontal):
    track = _track(v0, vertical, horizontal)

    result = _evaluate(track, vertical_deg=vertical, horizontal_deg=horizontal)

    assert result["status"] == CALCULATED
    assert result["source"] == BALL_SOURCE
    assert result["referred_to"] == "separation"
    assert result["value_mph"] == pytest.approx(v0, abs=0.01)
    # the radial read low, by the projection, and the value is never the radial
    assert result["reading"]["radial_mph"] < result["value_mph"]
    assert MIN_PROJECTION <= result["projection"] < 1.0


def test_without_drag_the_full_speed_is_radial_over_the_projection():
    """At the reading itself: full = radial / (LOS . dir)."""
    v0, vertical, horizontal = 120.0, 16.0, 2.0
    impact = 40.0
    start = impact + 1.0
    t = (start + WINDOW_MS / 2 - impact) / 1000.0
    radial = _radial_at(v0, t, vertical, horizontal)
    track = _track(v0, vertical, horizontal, starts=[start])

    result = _evaluate(track, vertical_deg=vertical, horizontal_deg=horizontal)

    assert result["speed_at_reading_mph"] == pytest.approx(radial / result["projection"])
    # drag over ~3 ms is small, and it is added back, never subtracted
    assert 0.0 < result["drag_referral_mph"] < 0.3
    assert result["value_mph"] == pytest.approx(
        result["speed_at_reading_mph"] + result["drag_referral_mph"]
    )


def test_the_drag_term_is_published_and_small():
    """Ferguson et al. 2022 Cd over the stated spin band, ISA air: ~0.02-0.04 mph per ms."""
    v = 120.0 * MPH
    per_ms = full_speed.drag_deceleration_mps2(v, 16.0) / MPH / 1000.0
    assert 0.02 < per_ms < 0.05
    assert "Ferguson" in full_speed.DRAG_SOURCE


def test_the_range_brackets_the_value_and_names_each_input():
    result = _evaluate(_track(120.0, 16.0, 2.0, starts=[42.0]))

    low, high = result["range_mph"]
    assert low < result["value_mph"] < high
    budget = result["uncertainty_mph"]
    for name in (
        "radial_resolution",
        "fft_window",
        "vertical_launch",
        "horizontal_launch",
        "tee_range",
        "tee_side_offset",
        "impact_time",
        "ops_position",
        "drag",
    ):
        assert budget[name] >= 0.0
    total = math.sqrt(sum(value**2 for value in budget.values()))
    assert high - result["value_mph"] == pytest.approx(total)


RIG_WITH_MIC = SimpleNamespace(
    ops_offset_mm=(-85.0, 47.0, -20.0),
    iwr_origin_mm=(0.0, 50.0, -30.0),
    mic_offset_mm=(-80.0, 0.0, 0.0),
)


def test_a_sound_trigger_impact_widens_the_range():
    precise = _evaluate(_track(120.0, 16.0, 2.0, starts=[42.0]))
    loose = _evaluate(
        _track(120.0, 16.0, 2.0, impact_source="sound_trigger", impact_uncertainty_ms=4.0),
        rig=RIG_WITH_MIC,
    )
    assert loose["uncertainty_mph"]["impact_time"] > precise["uncertainty_mph"]["impact_time"]


def test_a_sound_trigger_impact_is_walked_back_by_the_sound_s_flight():
    """The trigger fires when the sound reaches the mic: impact = trigger - distance / c."""
    trigger = 46.0
    # the mic: 80 mm left of the lens, level with it, in its forward plane
    mic = (0.030, -0.080, RADAR_HEIGHT + 0.050)
    walk_back = 1000.0 * math.dist(_ball_rest(), mic) / 343.0
    impact = trigger - walk_back
    track = _track(
        120.0, 16.0, 2.0, impact_ms=impact, impact_source="sound_trigger", impact_uncertainty_ms=3.0
    )
    track["impact_ms"] = trigger

    result = _evaluate(track, rig=RIG_WITH_MIC)

    assert result["reading"]["impact_walk_back_ms"] == pytest.approx(walk_back)
    assert result["value_mph"] == pytest.approx(120.0, abs=0.01)


def test_a_sound_trigger_impact_without_a_mic_offset_is_refused():
    result = _evaluate(_track(120.0, 16.0, 2.0, impact_source="sound_trigger"))
    assert result["status"] == NOT_MEASURED
    assert "mic" in result["reason"]


def test_the_reading_is_the_first_whole_window_after_impact():
    impact = 40.0
    track = _track(120.0, 16.0, 2.0, impact_ms=impact)
    # a window that straddles impact (club inside it) and the club after impact
    track["outbound"] = [[impact - 2.0, 90.0], [impact + 0.5, 60.0]] + track["outbound"]

    result = _evaluate(track)

    assert result["reading"]["window_start_ms"] == pytest.approx(impact + 0.5)
    assert result["reading"]["radial_mph"] > 100.0
    assert result["reading"]["time_after_impact_ms"] == pytest.approx(0.5 + WINDOW_MS / 2)


def test_readings_beyond_the_referral_limit_are_not_used():
    impact = 40.0
    late = impact + full_speed.MAX_REFERRAL_MS + 1.0
    track = _track(120.0, 16.0, 2.0, impact_ms=impact, starts=[late])

    result = _evaluate(track)

    assert result["status"] == NOT_MEASURED
    assert "no OPS ball reading" in result["reason"]


def test_select_ball_reading_takes_the_fastest_ball_peak_in_the_earliest_window():
    track = {
        "impact_ms": 10.0,
        "canonical_radial_mph": 120.0,
        "window_ms": WINDOW_MS,
        "outbound": [[11.0, 70.0], [11.0, 112.0], [12.0, 113.0]],
    }
    readings = select_ball_reading(track)
    assert readings[0] == (11.0, 112.0)
    assert readings[1] == (12.0, 113.0)


def test_refused_when_ill_conditioned_at_a_grazing_angle():
    """A steep launch with the OPS beside a near tee: LOS . dir < cos 30 deg everywhere."""
    near = 0.45
    rig = SimpleNamespace(ops_offset_mm=(-300.0, 47.0, -20.0), iwr_origin_mm=(0.0, 50.0, -30.0))
    track = {
        "impact_ms": 40.0,
        "impact_source": "ops_transition",
        "impact_uncertainty_ms": 0.6,
        "window_ms": WINDOW_MS,
        "canonical_radial_mph": 80.0,
        "outbound": [[40.5 + i, 75.0] for i in range(5)],
    }

    result = _evaluate(
        track,
        rig=rig,
        tee_range_m=near,
        tee_side_offset_m=0.0,
        vertical_deg=50.0,
        horizontal_deg=25.0,
    )

    assert result["status"] == NOT_MEASURED
    assert result["value_mph"] is None
    assert "ill-conditioned" in result["reason"]
    assert "0.866" in result["reason"]
    assert result["projection"] < MIN_PROJECTION


def test_the_threshold_is_cos_30_degrees():
    assert MIN_PROJECTION == pytest.approx(math.cos(math.radians(30.0)))


def test_a_ball_moving_toward_the_ops_is_refused():
    track = _track(120.0, 16.0, 2.0)
    result = _evaluate(track, horizontal_deg=179.0)
    assert result["status"] == NOT_MEASURED
    assert result["value_mph"] is None


@pytest.mark.parametrize(
    "override, words",
    [
        ({"vertical_deg": None}, "vertical launch"),
        ({"vertical_source": "estimated"}, "vertical launch"),
        ({"vertical_deg": float("nan")}, "vertical launch"),
        ({"horizontal_deg": None}, "horizontal launch"),
        ({"horizontal_source": "estimated"}, "horizontal launch"),
        ({"horizontal_source": "kld7"}, "horizontal launch"),
        ({"horizontal_frame": "mixed"}, "frame"),
        ({"horizontal_frame": None}, "frame"),
        ({"rig": None}, "rig"),
        ({"rig": SimpleNamespace(ops_offset_mm=None, iwr_origin_mm=(0.0, 50.0, -30.0))}, "OPS"),
        ({"tee_range_m": None}, "tee range"),
        ({"tee_side_offset_m": None}, "side offset"),
        ({"radar_height_m": None}, "height"),
        ({"ops_track": None}, "OPS"),
        ({"tee_range_m": 0.01}, "tee range"),
    ],
)
def test_not_measured_when_an_input_is_missing(override, words):
    result = _evaluate(_track(120.0, 16.0, 2.0), **override)

    assert result["status"] == NOT_MEASURED
    assert result["value_mph"] is None
    assert result["range_mph"] is None
    assert words in result["reason"]


def test_not_measured_without_an_impact_time():
    track = _track(120.0, 16.0, 2.0)
    track["impact_ms"] = None
    result = _evaluate(track)
    assert result["status"] == NOT_MEASURED
    assert "impact" in result["reason"]


def test_a_target_line_angle_is_turned_into_the_unit_frame_by_the_offset():
    """Reported = boresight + offset (horizontal_frame), so boresight = reported - offset."""
    track = _track(120.0, 16.0, 1.0)
    in_boresight = _evaluate(track, horizontal_deg=1.0, horizontal_frame="unit_boresight")
    in_target_line = _evaluate(
        track, horizontal_deg=4.0, horizontal_frame="target_line", horizontal_offset_deg=3.0
    )
    assert in_target_line["value_mph"] == pytest.approx(in_boresight["value_mph"])
    assert in_target_line["value_mph"] == pytest.approx(120.0, abs=0.01)
    assert in_target_line["inputs"]["horizontal_boresight_deg"] == pytest.approx(1.0)


def test_a_target_line_frame_without_an_offset_is_refused():
    result = _evaluate(
        _track(120.0, 16.0, 1.0), horizontal_frame="target_line", horizontal_offset_deg=None
    )
    assert result["status"] == NOT_MEASURED
    assert "offset" in result["reason"]


def test_sign_a_ball_launched_right_leans_into_the_ops_line_of_sight():
    """The OPS sits left of the ball (-y), so its line of sight points right (+y):
    a ball launched right shares more of it and needs a smaller correction."""
    radial_track = _track(120.0, 16.0, 0.0)
    right = _evaluate(radial_track, horizontal_deg=+4.0)
    left = _evaluate(radial_track, horizontal_deg=-4.0)
    assert right["projection"] > left["projection"]
    assert right["value_mph"] < left["value_mph"]


def test_sign_a_higher_launch_needs_a_larger_correction():
    track = _track(120.0, 16.0, 0.0)
    low = _evaluate(track, vertical_deg=10.0, horizontal_deg=0.0)
    high = _evaluate(track, vertical_deg=20.0, horizontal_deg=0.0)
    assert high["value_mph"] > low["value_mph"]


def test_the_canonical_radial_is_never_changed_or_reused():
    track = _track(120.0, 16.0, 2.0)
    result = _evaluate(track)
    assert result["canonical_speed_contract"] == "ops_radial_unchanged"
    assert result["method"] == full_speed.METHOD
    assert "legacy" not in result["method"]


def test_full_club_speed_recovers_a_synthetic_head():
    ops = _ops_position()
    head = _ball_rest() - np.array([0.10, 0.02, -0.01])
    direction = _direction(-4.0, 3.0)
    v = 95.0
    los = head - ops
    radial = v * float(np.dot(los / np.linalg.norm(los), direction))

    result = evaluate_full_club_speed(
        radial_mph=radial,
        head_position_m=head,
        direction=direction,
        ops_position_m=ops,
        direction_source="camera_arc",
    )

    assert result["status"] == CALCULATED
    assert result["value_mph"] == pytest.approx(v)


def test_full_club_speed_is_not_measured_without_a_single_reading():
    result = evaluate_full_club_speed(
        radial_mph=None,
        head_position_m=None,
        direction=None,
        ops_position_m=None,
        direction_source=None,
        reason="the OPS club speed is not one FFT-window reading",
    )
    assert result["status"] == NOT_MEASURED
    assert result["value_mph"] is None
    assert "FFT-window" in result["reason"]


def test_full_club_speed_refuses_a_grazing_direction():
    ops = _ops_position()
    head = _ball_rest()
    result = evaluate_full_club_speed(
        radial_mph=40.0,
        head_position_m=head,
        direction=_direction(0.0, 60.0),
        ops_position_m=ops,
        direction_source="camera_arc",
    )
    assert result["status"] == NOT_MEASURED
    assert "ill-conditioned" in result["reason"]


def _full(value, *, club=False):
    return {
        "status": CALCULATED,
        "value_mph": value,
        "referred_to": "impact" if club else "separation",
        "reference_point": "clubhead" if club else "ball",
    }


def _not(reason):
    return {"status": NOT_MEASURED, "value_mph": None, "reason": reason}


def test_smash_is_calculated_when_both_speeds_are_full():
    result = evaluate_full_smash(_full(130.0), _full(92.0, club=True))
    assert result["status"] == CALCULATED
    assert result["value"] == pytest.approx(130.0 / 92.0)
    assert "smash_factor" in result["source"]


def test_smash_is_not_measured_when_the_club_speed_is_radial():
    result = evaluate_full_smash(_full(130.0), _not("club radial"))
    assert result["status"] == NOT_MEASURED
    assert result["value"] is None
    assert "club speed is not a full speed" in result["reason"]


def test_smash_is_not_measured_when_the_ball_speed_is_radial():
    result = evaluate_full_smash(_not("x"), _full(92.0, club=True))
    assert result["status"] == NOT_MEASURED
    assert "ball speed is not a full speed" in result["reason"]


def test_smash_is_not_measured_when_neither_is_full():
    result = evaluate_full_smash(None, None)
    assert result["status"] == NOT_MEASURED
    assert result["value"] is None


def test_an_unbounded_range_is_none_never_infinite(monkeypatch):
    monkeypatch.setitem(full_speed.TOLERANCES, "tee_range_m", 10.0)

    result = _evaluate(_track(120.0, 16.0, 2.0))

    assert result["status"] == CALCULATED
    assert result["range_mph"] is None
    assert result["uncertainty_mph"]["tee_range"] is None
    assert result["range_basis"].startswith("unbounded")


@pytest.mark.parametrize(
    "override, words, label",
    [
        # a nominal lens: the camera's launch is experimental, and so is what rests on it
        (
            {"vertical_source": "camera_fallback"},
            "vertical launch from camera fallback",
            "experimental",
        ),
        (
            {"horizontal_source": "camera_fallback"},
            "horizontal launch from camera fallback",
            "experimental",
        ),
        (
            {"horizontal_source": "camera_assisted_experimental"},
            "camera horizontal",
            "experimental",
        ),
        # Q3: a horizontal that failed its own gate is experimental, and feeds on
        ({"horizontal_source": "radar_low_coherence"}, "low-coherence", "experimental"),
        ({"horizontal_source": "camera_low_consensus"}, "low-consensus", "experimental"),
        # an estimated horizontal resolves, estimated (fix 5)
        (
            {"horizontal_source": "radar", "horizontal_status": "azimuth_uncalibrated"},
            "azimuth uncalibrated",
            "estimated",
        ),
        ({"tee_range_source": "camera_ball_size"}, "resting ball's own size", "experimental"),
        ({"tee_range_source": "camera_prior"}, "camera prior", "estimated"),
        (
            {"tee_range_source": "iwr_post_impact", "tee_range_label": "iwr_partial"},
            "partial IWR dump",
            "estimated",
        ),
        (
            {"tee_range_source": "iwr_post_impact", "tee_range_label": "estimated"},
            "two IWR tracks compete",
            "estimated",
        ),
    ],
)
def test_a_weaker_input_gives_the_full_speed_its_label(override, words, label):
    """The value is projected, labelled by its weakest input: no simulator flies it (D31)."""
    result = _evaluate(_track(120.0, 16.0, 2.0), **override)

    assert result["status"] == label
    assert result["value_mph"] is not None
    assert words in result["note"]
    assert any(words in item for item in result["weak_inputs"])


def test_the_caller_s_policy_labels_decide_the_full_speed_label():
    """The kiosk hands its own labels in (source_labels.shot_launch_label): a calibrated
    lens lifts the camera fallback to estimated; a configured tilt lowers the radar's."""
    calibrated = _evaluate(
        _track(120.0, 16.0, 2.0),
        horizontal_source="camera_fallback",
        horizontal_label="estimated",
        horizontal_note="camera fallback through a calibrated lens",
    )
    tilt = _evaluate(
        _track(120.0, 16.0, 2.0),
        vertical_label="estimated",
        vertical_note="tilt is the configured value, not measured",
    )

    assert calibrated["status"] == "estimated"
    assert tilt["status"] == "estimated"
    assert "tilt is the configured value" in tilt["note"]


def test_the_weakest_input_wins_experimental_over_estimated():
    result = _evaluate(
        _track(120.0, 16.0, 2.0),
        horizontal_source="radar_low_coherence",
        tee_range_source="camera_prior",
    )

    assert result["status"] == "experimental"


def test_a_measured_launch_on_the_shot_s_own_range_stays_calculated():
    result = _evaluate(_track(120.0, 16.0, 2.0), tee_range_source="iwr_post_impact")

    assert result["status"] == CALCULATED
    assert result["weak_inputs"] == []
