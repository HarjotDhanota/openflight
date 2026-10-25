"""The kiosk's swing exposure policy: the resting ball is the light meter.

Shortest sensor exposure at gain 1, gain before exposure, never past the blur ceiling;
re-checked before every swing, with hysteresis; labelled, never blocked.
"""

import json
import math

import numpy as np
import pytest

from openflight.camera import swing_exposure as sx

BLACK = 16.0
CLIP_RANGE = 250.0 - BLACK  # 234 DN between black and clip
# A plausible OV9281 1280x800 control table, as picamera2 reports it: (min, max, default).
CONTROLS_1280 = {
    "ExposureTime": (9, 8_000, 300),
    "AnalogueGain": (1.0, 15.9375, 1.0),
    "FrameDurationLimits": (8_333, 1_000_000, 8_333),
}


def limits(minimum=9, gain_max=15.9375):
    return sx.SensorLimits(
        exposure_min_us=minimum,
        exposure_max_us=8_000,
        gain_min=1.0,
        gain_max=gain_max,
        source="camera_controls",
    )


def envelope(minimum=9, ceiling=294, fps=120.0, gain_max=15.9375, armed=None):
    return sx.build_envelope(
        limits(minimum, gain_max), blur_ceiling_us=ceiling, fps=fps, armed_profile=armed
    )


def reading(*, signal, peak, exposure_us, gain, clipped_pct=0.0, black=BLACK):
    """A resting-ball reading: core median and p95 given as DN above black."""
    return sx.BallReading(
        median_dn=black + signal,
        peak_dn=black + peak,
        clipped_pct=clipped_pct,
        exposure_us=exposure_us,
        gain=gain,
        black_floor_dn=black,
        source="ball_at_address",
        center_px=(640.0, 600.0),
        diameter_px=40.0,
    )


def test_the_blur_ceiling_is_derived_from_the_mm_budget_and_matches_the_config():
    config = sx.load_config()

    # floor(min(4 mm / 13.6 m/s, 1 mm / 3.3 m/s) x 1000) = floor(min(294.1, 303.0))
    assert sx.blur_ceiling_us(config, "arm5") == 294
    assert sx.blur_ceiling_us(config, "arm6") == 294  # mm, not px: the mode doesn't change it
    for arm_id in ("arm5", "arm6"):
        assert config["modes"][arm_id]["blur_ceiling_us"] == sx.blur_ceiling_us(config, arm_id)
    assert "derivation" in config


def test_a_config_whose_stated_ceiling_disagrees_with_its_derivation_is_refused(tmp_path):
    config = sx.load_config()
    config["modes"]["arm5"]["blur_ceiling_us"] = 500
    path = tmp_path / "swing_exposure.json"
    path.write_text(json.dumps(config), encoding="utf-8")

    with pytest.raises(ValueError, match="blur ceiling"):
        sx.load_config(path)


def test_modes_are_found_by_their_size():
    assert sx.mode_for_size(1280, 800) == "arm5"
    assert sx.mode_for_size(640, 400) == "arm6"
    assert sx.mode_for_size(320, 200) is None


def test_sensor_limits_are_read_from_the_camera_controls():
    read = sx.sensor_limits_from_controls(CONTROLS_1280)

    assert read == sx.SensorLimits(
        exposure_min_us=9,
        exposure_max_us=8_000,
        gain_min=1.0,
        gain_max=15.9375,
        source="camera_controls",
    )
    assert read.to_dict()["exposure_min_us"] == 9


def test_a_fractional_minimum_is_rounded_up_to_a_whole_microsecond():
    read = sx.sensor_limits_from_controls({**CONTROLS_1280, "ExposureTime": (3.4, 3_000, 100)})

    assert read.exposure_min_us == 4


@pytest.mark.parametrize(
    "controls",
    [
        {},
        {"AnalogueGain": (1.0, 15.9, 1.0)},
        {"ExposureTime": "bad", "AnalogueGain": (1.0, 15.9, 1.0)},
        {"ExposureTime": (0, 100, 10), "AnalogueGain": (1.0, 15.9, 1.0)},
        {"ExposureTime": (50, 10, 20), "AnalogueGain": (1.0, 15.9, 1.0)},
    ],
)
def test_missing_or_nonsense_exposure_limits_read_as_none(controls):
    assert sx.sensor_limits_from_controls(controls) is None


def test_without_a_gain_limit_the_driver_cap_stands_in_and_says_so():
    read = sx.sensor_limits_from_controls({"ExposureTime": (9, 8_000, 300)})

    assert read.gain_max == pytest.approx(sx.DRIVER_GAIN_CAP)
    assert read.gain_source == "ov9282_driver_register_cap"


def test_the_ladder_runs_from_the_sensor_minimum_to_the_blur_ceiling_in_root_two_steps():
    ladder = sx.exposure_ladder(9, 294)

    assert ladder[0] == 9
    assert ladder[-1] == 294
    assert list(ladder) == sorted(set(ladder))
    ratios = [b / a for a, b in zip(ladder, ladder[1:])]
    assert all(1.2 <= ratio <= 1.6 for ratio in ratios[1:])
    assert ratios[0] <= 1.6


def test_the_envelope_never_passes_the_frame_period_or_an_armed_ceiling():
    tight = envelope(ceiling=294, fps=4000.0)
    assert tight.exposure_ceiling_us < 250
    armed = envelope(armed={"exposure_ceiling_us": 150, "gain_ceiling": 8.0})
    assert armed.exposure_ceiling_us == 150
    assert armed.gain_cap == 8.0
    assert max(armed.ladder) == 150


def test_the_gain_cap_is_the_sensor_s_not_an_impossible_eighteen_or_twenty():
    env = envelope(gain_max=15.9375)

    assert env.gain_cap == pytest.approx(15.9375)
    assert env.to_dict()["gain_cap"] == pytest.approx(15.9375)


def test_bright_indoor_picks_the_shortest_exposure_a_capped_gain_can_reach():
    env = envelope()
    peak_index = 0.15  # DN above black per us x gain
    wanted = sx.TARGET_PEAK_FRACTION * CLIP_RANGE / peak_index

    choice = sx.choose_setting(peak_index, peak_index * 0.7, env)

    shortest = min(e for e in env.ladder if wanted / e <= env.gain_cap)
    assert choice.setting.exposure_us == shortest
    assert choice.setting.gain == pytest.approx(wanted / shortest, abs=0.01)
    assert choice.status == "ball_driven"
    assert choice.predicted_peak_fraction == pytest.approx(sx.TARGET_PEAK_FRACTION, abs=0.01)


def test_gain_fills_first_then_exposure_lengthens_only_at_the_gain_cap():
    env = envelope()
    for peak_index in (2.0, 0.5, 0.15, 0.05, 0.03):
        choice = sx.choose_setting(peak_index, peak_index, env)
        index = env.ladder.index(choice.setting.exposure_us)
        if index > 0:
            shorter = env.ladder[index - 1]
            assert choice.wanted_product / shorter > env.gain_cap


def test_the_choice_never_passes_the_blur_ceiling_or_the_gain_cap():
    env = envelope()

    choice = sx.choose_setting(1e-4, 1e-4, env)

    assert choice.setting == sx.Setting(294, env.gain_cap)
    assert choice.status == "too_dark"


def test_full_sun_clipped_at_the_sensor_floor_holds_there_and_says_shade_or_nd():
    policy = sx.SwingExposurePolicy(envelope(minimum=9))
    clipped = reading(signal=234, peak=234, exposure_us=9, gain=1.0, clipped_pct=60.0)

    decision = policy.decide(clipped, sx.Setting(9, 1.0))

    assert decision.status == "too_bright"
    assert decision.target is None
    assert decision.analysis_eligible is False
    assert decision.frame_label == "too bright: shade the ball area (or an ND filter)"


def test_full_sun_walks_down_to_the_floor_from_a_long_setting():
    policy = sx.SwingExposurePolicy(envelope(minimum=9))
    current = sx.Setting(294, 15.9375)
    # full sun: about 20 DN per us x gain at 1280x800; 640x400 doubles it
    sun = 40.0
    for _ in range(10):
        product = current.exposure_us * current.gain
        peak = min(sun * product, CLIP_RANGE)
        clipped = 60.0 if sun * product >= CLIP_RANGE else 0.0
        decision = policy.decide(
            reading(
                signal=peak * 0.8,
                peak=peak,
                exposure_us=current.exposure_us,
                gain=current.gain,
                clipped_pct=clipped,
            ),
            current,
        )
        if decision.target is None:
            break
        current = decision.target

    assert current == sx.Setting(9, 1.0)
    assert decision.status == "too_bright"


def test_full_sun_unclipped_at_the_floor_is_ball_driven():
    policy = sx.SwingExposurePolicy(envelope(minimum=7))
    # 7 us x 1 puts a sunlit ball near 0.6 of clip
    sunny = reading(signal=110, peak=140, exposure_us=7, gain=1.0)

    decision = policy.decide(sunny, sx.Setting(7, 1.0))

    assert decision.status == "ball_driven"
    assert decision.target is None
    assert decision.analysis_eligible is True
    assert decision.frame_label is None


def test_today_s_room_at_1_32_m_goes_to_the_blur_ceiling_and_the_gain_cap():
    """6 mm lens: the ball reads 53 DN (peak 0.372 of clip) at 298 us x 12."""
    policy = sx.SwingExposurePolicy(envelope())
    today = reading(signal=53, peak=0.372 * CLIP_RANGE, exposure_us=298, gain=12.0)

    decision = policy.decide(today, sx.Setting(300, 12.0))

    assert decision.target == sx.Setting(294, 15.9375)
    assert decision.status == "ball_driven"
    assert decision.analysis_eligible is True
    # 53 x (294 x 15.9375) / (298 x 12) = 69.5 DN; the peak lands near 0.49 of clip
    assert decision.predicted_signal_dn == pytest.approx(69.5, abs=0.5)
    assert decision.predicted_peak_fraction == pytest.approx(0.487, abs=0.01)


def test_today_s_room_at_2_14_m_is_under_target_but_above_the_gate():
    policy = sx.SwingExposurePolicy(envelope())
    far = reading(signal=26, peak=0.145 * CLIP_RANGE, exposure_us=298, gain=12.0)

    decision = policy.decide(far, sx.Setting(300, 12.0))

    assert decision.target == sx.Setting(294, 15.9375)
    assert decision.status == "ball_driven"
    assert decision.predicted_signal_dn == pytest.approx(34.1, abs=0.5)
    assert decision.frame_label is None
    assert "blur ceiling" in decision.message


def test_dark_room_holds_the_ceiling_and_cap_and_says_add_light():
    policy = sx.SwingExposurePolicy(envelope())
    dark = reading(signal=4, peak=6, exposure_us=294, gain=15.9375)

    decision = policy.decide(dark, sx.Setting(294, 15.9375))

    assert decision.status == "too_dark"
    assert decision.target is None
    assert decision.analysis_eligible is False
    assert decision.frame_label == "too dark: add light"


def test_a_dim_ball_below_the_ceiling_is_raised_at_once():
    policy = sx.SwingExposurePolicy(envelope())
    dim = reading(signal=8, peak=12, exposure_us=37, gain=4.0)

    decision = policy.decide(dim, sx.Setting(37, 4.0))

    assert decision.target is not None
    assert decision.target.exposure_us * decision.target.gain > 37 * 4.0
    assert decision.analysis_eligible is False


def test_no_ball_keeps_the_last_setting_and_says_why():
    policy = sx.SwingExposurePolicy(envelope())
    policy.decide(reading(signal=60, peak=128, exposure_us=104, gain=8.0), sx.Setting(104, 8.0))

    decision = policy.decide(None, sx.Setting(104, 8.0), no_ball_reason="no ball in the box")

    assert decision.status == "holding_no_ball"
    assert decision.target is None
    assert "no ball in the box" in decision.message
    assert "104 us" in decision.message
    assert decision.analysis_eligible is True


def test_no_ball_before_any_ball_is_not_eligible():
    policy = sx.SwingExposurePolicy(envelope())

    decision = policy.decide(None, sx.Setting(104, 8.0), no_ball_reason="no look yet")

    assert decision.status == "holding_no_ball"
    assert decision.analysis_eligible is False


def test_arm6_takes_half_the_exposure_for_the_same_light():
    config = sx.load_config()
    arm5 = sx.SwingExposurePolicy(envelope(fps=120.0))
    arm6 = sx.SwingExposurePolicy(envelope(fps=288.0))
    light = 0.1  # the setup's 1280x800 index, DN above black per us x gain

    full = arm5.seed(light * sx.mode_signal_factor(config, "arm5"), sx.Setting(300, 4.0))
    half = arm6.seed(light * sx.mode_signal_factor(config, "arm6"), sx.Setting(300, 4.0))

    assert sx.mode_signal_factor(config, "arm6") == 2.0
    product_full = full.target.exposure_us * full.target.gain
    product_half = half.target.exposure_us * half.target.gain
    assert product_half == pytest.approx(product_full / 2.0, rel=0.03)
    assert half.target.exposure_us < full.target.exposure_us


def test_the_setup_light_index_picks_the_first_setting():
    policy = sx.SwingExposurePolicy(envelope())
    light = 0.15

    decision = policy.seed(light, sx.Setting(1000, 4.0))

    assert decision.status == "seeded"
    assert decision.reason == "setup_light_index"
    assert decision.target == sx.choose_setting(light, light, envelope()).setting
    assert decision.analysis_eligible is False


def test_without_a_light_index_the_startup_seed_is_clamped_into_the_envelope():
    policy = sx.SwingExposurePolicy(envelope())

    decision = policy.seed(None, sx.Setting(1000, 4.0))

    assert decision.target == sx.Setting(294, 13.61)
    assert decision.reason == "startup_controls_clamped"


def test_a_seed_already_inside_the_envelope_is_kept():
    policy = sx.SwingExposurePolicy(envelope())

    decision = policy.seed(None, sx.Setting(104, 8.0))

    assert decision.target is None
    assert decision.current == sx.Setting(104, 8.0)


def test_a_ball_inside_the_band_holds_even_off_target():
    policy = sx.SwingExposurePolicy(envelope())
    near = reading(signal=110, peak=0.70 * CLIP_RANGE, exposure_us=104, gain=8.0)

    decision = policy.decide(near, sx.Setting(104, 8.0))

    assert decision.target is None
    assert decision.status == "ball_driven"


def test_a_mild_change_needs_two_looks_in_a_row():
    policy = sx.SwingExposurePolicy(envelope(), confirm_looks=2)
    good = reading(signal=90, peak=0.55 * CLIP_RANGE, exposure_us=104, gain=8.0)
    dimmer = reading(signal=50, peak=0.30 * CLIP_RANGE, exposure_us=104, gain=8.0)
    current = sx.Setting(104, 8.0)
    policy.decide(good, current)

    first = policy.decide(dimmer, current)
    flicker_back = policy.decide(good, current)
    again = policy.decide(dimmer, current)
    confirmed = policy.decide(dimmer, current)

    assert first.target is None and first.hysteresis["pending_count"] == 1
    assert flicker_back.target is None and flicker_back.hysteresis["pending_count"] == 0
    assert again.target is None
    assert confirmed.target is not None
    assert confirmed.target.exposure_us * confirmed.target.gain > 104 * 8.0


def test_alternating_looks_never_oscillate():
    policy = sx.SwingExposurePolicy(envelope(), confirm_looks=2)
    current = sx.Setting(104, 8.0)
    changes = 0
    for index in range(20):
        fraction = 0.38 if index % 2 else 0.77
        decision = policy.decide(
            reading(
                signal=fraction * CLIP_RANGE * 0.7,
                peak=fraction * CLIP_RANGE,
                exposure_us=current.exposure_us,
                gain=current.gain,
            ),
            current,
        )
        if decision.target is not None:
            changes += 1
            current = decision.target

    assert changes <= 1


def test_a_settled_change_lands_inside_the_band():
    policy = sx.SwingExposurePolicy(envelope())
    current = sx.Setting(37, 2.0)
    true_peak_index = 0.2
    for _ in range(6):
        peak = min(true_peak_index * current.exposure_us * current.gain, CLIP_RANGE)
        decision = policy.decide(
            reading(
                signal=peak * 0.7, peak=peak, exposure_us=current.exposure_us, gain=current.gain
            ),
            current,
        )
        if decision.target is not None:
            current = decision.target

    fraction = true_peak_index * current.exposure_us * current.gain / CLIP_RANGE
    low, high = sx.HOLD_BAND
    assert low <= fraction <= high


def test_the_ball_reading_measures_the_core_from_frames():
    image = np.full((100, 100), 20, dtype=np.uint8)
    yy, xx = np.indices(image.shape)
    image[np.hypot(xx - 50, yy - 50) <= 10] = 120
    frames = [image, image, image]

    read = sx.read_ball(
        frames, x=50, y=50, diameter_px=20, black_floor_dn=BLACK, exposure_us=104, gain=8.0
    )

    assert read.median_dn == 120
    assert read.peak_dn == 120
    assert read.clipped_pct == 0.0
    assert read.signal_dn == pytest.approx(104.0)
    assert read.peak_index == pytest.approx(104.0 / (104 * 8.0))


def test_a_decision_serialises_with_its_inputs():
    policy = sx.SwingExposurePolicy(envelope())
    decision = policy.decide(
        reading(signal=53, peak=87, exposure_us=298, gain=12.0), sx.Setting(300, 12.0)
    )

    payload = decision.to_dict()

    assert payload["schema"] == sx.DECISION_SCHEMA
    assert payload["reading"]["exposure_us"] == 298
    assert payload["light_index"]["peak"] == pytest.approx(87 / (298 * 12.0), rel=1e-5)
    assert payload["envelope"]["blur_ceiling_us"] == 294
    assert payload["envelope"]["sensor_min_us"] == 9
    assert payload["target"] == {"exposure_us": 294, "gain": 15.9375}
    assert payload["current"] == {"exposure_us": 300, "gain": 12.0}
    json.dumps(payload, allow_nan=False)
    assert math.isfinite(payload["predicted"]["signal_dn"])


def test_the_ball_gate_is_the_one_every_other_judge_uses():
    from openflight.camera import light_judging

    assert sx.BALL_GATE_DN == light_judging.BALL_MIN_SIGNAL_DN
    assert sx.BALL_CLIPPED_MAX_PCT == light_judging.CLIPPED_MAX_PCT
    assert sx.BLACK_LEVEL_DN == light_judging.SENSOR_BLACK_LEVEL_DN


def test_a_size_outside_the_modes_takes_the_shared_mm_budget():
    assert sx.blur_ceiling_us(sx.load_config(), None) == 294


def _in_envelope(setting, env):
    return (
        env.ladder[0] <= setting.exposure_us <= env.exposure_ceiling_us
        and env.gain_min <= setting.gain <= env.gain_cap
    )


def _look(world, setting):
    """What a look sees at ``setting``: a reading if the ball is visible there, else None."""
    peak_index, visible = world
    product = setting.exposure_us * setting.gain
    peak = peak_index * product
    if not visible(peak):
        return None
    return reading(
        signal=min(peak * 0.8, CLIP_RANGE),
        peak=min(peak, CLIP_RANGE),
        exposure_us=setting.exposure_us,
        gain=setting.gain,
        clipped_pct=60.0 if peak >= CLIP_RANGE else 0.0,
    )


def _walk(policy, world, start, looks=40, now=0.0):
    current, seen, decisions = start, [], []
    for index in range(looks):
        decision = policy.decide(
            _look(world, current), current, no_ball_reason="no ball in the box", now=now + index
        )
        decisions.append(decision)
        if decision.target is not None:
            current = decision.target
            seen.append(current)
        if decision.reading is not None:
            break
    return current, seen, decisions


def test_the_sweep_waits_for_three_looks_without_a_ball():
    policy = sx.SwingExposurePolicy(envelope())
    current = sx.Setting(104, 8.0)

    first = policy.decide(None, current, no_ball_reason="no ball in the box", now=0.0)
    second = policy.decide(None, current, no_ball_reason="no ball in the box", now=1.0)
    third = policy.decide(None, current, no_ball_reason="no ball in the box", now=2.0)

    assert first.target is None and second.target is None
    assert first.status == second.status == "holding_no_ball"
    assert third.status == "searching_for_ball"
    assert third.target is not None


def test_the_sweep_alternates_brighter_and_darker_from_the_current_setting():
    env = envelope()
    current = sx.Setting(104, 8.0)

    steps = sx.sweep_settings(env, current)

    assert steps[0].product > current.product
    assert steps[1].product < current.product
    assert steps[2].product > steps[0].product
    assert steps[3].product < steps[1].product
    assert all(_in_envelope(step, env) for step in steps)
    assert steps[-1] in (env.top_setting, env.floor_setting) or any(
        step.same_as(env.top_setting) for step in steps
    )
    assert any(step.same_as(env.floor_setting) for step in steps)


def test_a_dark_seed_in_sun_finds_the_ball_by_stepping_darker():
    """Sun at 1280x800 (20 DN per us x gain): the dark seed washes the box out."""
    env = envelope(minimum=9)
    policy = sx.SwingExposurePolicy(env)
    sun = (20.0, lambda peak: peak < CLIP_RANGE)

    found, seen, decisions = _walk(policy, sun, env.top_setting)

    assert decisions[-1].reading is not None
    assert found.product * 20.0 < CLIP_RANGE
    assert all(_in_envelope(step, env) for step in seen)
    products = [step.product for step in seen]
    assert products == sorted(products, reverse=True)
    assert any(d.status == "searching_for_ball" for d in decisions)


def test_a_bright_seed_in_a_dark_room_finds_the_ball_by_stepping_brighter():
    """A dim room: the box look needs about 20 DN on the ball's peak to see it."""
    env = envelope(minimum=9)
    policy = sx.SwingExposurePolicy(env)
    dim = (0.01, lambda peak: peak >= 20.0)

    found, seen, decisions = _walk(policy, dim, env.floor_setting)

    assert decisions[-1].reading is not None
    assert found.product * 0.01 >= 20.0
    assert all(_in_envelope(step, env) for step in seen)
    products = [step.product for step in seen]
    assert products == sorted(products)


def test_the_found_ball_hands_over_to_normal_metering():
    env = envelope(minimum=9)
    policy = sx.SwingExposurePolicy(env)
    dim = (0.01, lambda peak: peak >= 20.0)
    found, _seen, decisions = _walk(policy, dim, env.floor_setting)

    after = decisions[-1]

    assert after.status in ("ball_driven", "too_dark")
    assert after.reason == "resting_ball"
    assert after.setting.same_as(env.top_setting)


def test_no_ball_anywhere_holds_the_last_good_setting_and_says_it_searched():
    env = envelope()
    policy = sx.SwingExposurePolicy(env)
    good = sx.Setting(104, 8.0)
    policy.decide(reading(signal=60, peak=128, exposure_us=104, gain=8.0), good)
    nowhere = (0.1, lambda _peak: False)

    current, seen, decisions = _walk(policy, nowhere, good, looks=30)

    final = next(d for d in decisions if d.reason == "no_ball_searched")
    assert final.status == "holding_no_ball"
    assert sx.NO_BALL_FOUND_MESSAGE in final.message
    assert final.setting.same_as(good)
    assert current.same_as(good)
    assert all(_in_envelope(step, env) for step in seen)
    assert len(seen) >= len(sx.sweep_settings(env, good))


def test_without_a_good_setting_the_hold_is_the_seed():
    env = envelope()
    policy = sx.SwingExposurePolicy(env)
    seed = policy.seed(0.15, sx.Setting(1000, 4.0)).setting
    nowhere = (0.1, lambda _peak: False)

    current, _seen, decisions = _walk(policy, nowhere, seed, looks=30)

    assert any(d.reason == "no_ball_searched" for d in decisions)
    assert current.same_as(seed)


def test_after_a_failed_sweep_it_waits_for_the_cool_down_or_a_restart():
    env = envelope()
    policy = sx.SwingExposurePolicy(env)
    nowhere = (0.1, lambda _peak: False)
    start = sx.Setting(104, 8.0)
    current, _seen, decisions = _walk(policy, nowhere, start, looks=30)
    ended = next(i for i, d in enumerate(decisions) if d.reason == "no_ball_searched")
    ended_at = float(ended)

    cooling = policy.decide(None, current, now=ended_at + 5.0)
    after = policy.decide(None, current, now=ended_at + sx.SWEEP_COOLDOWN_S + 1.0)

    assert cooling.target is None and cooling.reason == "no_ball_searched"
    assert "again in" in cooling.message
    assert after.status == "searching_for_ball"

    policy2 = sx.SwingExposurePolicy(env)
    current2, _s, decisions2 = _walk(policy2, nowhere, start, looks=30)
    ended2 = next(i for i, d in enumerate(decisions2) if d.reason == "no_ball_searched")
    policy2.restart_search("the placement box moved")
    restarted = policy2.decide(None, current2, now=float(ended2) + 1.0)
    assert restarted.status == "searching_for_ball"


def test_the_sweep_never_passes_the_ceiling_or_the_cap():
    for minimum, ceiling, gain_max in ((9, 294, 15.9375), (4, 150, 8.0), (20, 294, 4.0)):
        env = envelope(minimum=minimum, ceiling=ceiling, gain_max=gain_max)
        for current in (env.floor_setting, env.top_setting, sx.Setting(60, 3.0)):
            for step in sx.sweep_settings(env, current):
                assert _in_envelope(step, env), (minimum, ceiling, gain_max, step)
