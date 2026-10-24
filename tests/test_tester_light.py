"""The tester ladder's light: the kiosk's seed in the setup's light, and the light label."""

import pytest

from openflight.camera import tester_light
from tests.test_swing_exposure import CLIP_RANGE, limits


def test_kiosk_would_use_in_today_s_room():
    light = 53 / (298 * 12.0)  # the setup's 1280x800 index at 1.32 m, 13 Oct

    full = tester_light.kiosk_would_use(light, "arm5", limits(9))
    half = tester_light.kiosk_would_use(light, "arm6", limits(4))

    assert (full["exposure_us"], full["gain"]) == (294, pytest.approx(15.9375))
    assert full["text"] == "kiosk would use: 1280×800 · 294 µs × gain 15.94"
    # 640x400 doubles the light, so the gain need not be capped
    assert half["exposure_us"] == 294
    assert half["gain"] < 15.9375
    assert half["light_index_dn_per_us_gain"] == pytest.approx(2.0 * light)


def test_kiosk_would_use_in_sun_sits_at_the_sensor_floor():
    sun = 0.6 * CLIP_RANGE / 7.0  # 7 us x 1 put the ball at 0.6 of clip (30 Sept)

    full = tester_light.kiosk_would_use(sun, "arm5", limits(9))
    half = tester_light.kiosk_would_use(sun, "arm6", limits(9))

    assert (full["exposure_us"], full["gain"]) == (9, 1.0)
    assert full["status"] == "ball_driven"
    assert (half["exposure_us"], half["gain"]) == (9, 1.0)
    assert half["status"] == "too_bright"


def test_kiosk_would_use_needs_the_sensor_minimum_and_a_light():
    assert tester_light.kiosk_would_use(0.05, "arm5", None)["status"] == "needs_sensor_minimum"
    assert tester_light.kiosk_would_use(None, "arm5", limits())["status"] == "not_measured"


def test_light_labels_name_only_the_bright_and_dark_causes():
    assert tester_light.light_label("ok") is None and tester_light.light_label(None) is None
    assert tester_light.light_label("ball_dark") == tester_light.light_label("zone_dark")
    assert tester_light.light_label("ball_clipped") == tester_light.light_label(
        "zone_clipped_no_ball"
    )
    assert tester_light.light_label("ball_dark") != tester_light.light_label("ball_clipped")
