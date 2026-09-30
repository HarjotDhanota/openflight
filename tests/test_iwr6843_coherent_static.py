"""P7-6: the setup's static radar difference, subtracted coherently per virtual channel."""

import json
from pathlib import Path

import numpy as np
import pytest

from openflight.iwr6843.dump import SAMPLE_RANGE_FFT_IQ16, pack_dump
from openflight.iwr6843.range_evidence import (
    StaticChannelProfile,
    StaticRangeProfileV2,
    compare_static_channel_profiles,
    compare_static_range_profiles,
    static_channel_estimator_policy,
    static_channel_profile,
)

SHA = {"radar_profile_sha256": "a" * 64, "rig_geometry_sha256": "b" * 64}
FIXTURES = Path(__file__).parent / "fixtures" / "iwr6843_static_coherent"
START, COUNT, RESOLUTION = 8, 53, 0.046875
BIAS = 0.066
# where a ball on the surface 1.0-2.5 m out appears to the array, aimed 10 deg up
GROUND = (-17.0, -3.0)


def _steer(elevation_deg):
    """The 8-element vertical snapshot of a far target, in the order the array reads it."""
    physical = np.exp(1j * np.pi * np.sin(np.radians(elevation_deg)) * np.arange(8))
    return physical[::-1]


def _channels(targets, *, seed, clutter=None, rotate=None):
    """(3 TX, 4 RX, bins) complex means; targets are (bin, amplitude, elevation, phase).

    A new scene has the unit's own near reflections (the mat's front edge, the
    enclosure) at bins 12 and 16, which is what the per-channel fit locks onto.
    """
    rng = np.random.default_rng(seed)
    if clutter is not None:
        cube = clutter.copy()
    else:
        cube = (rng.normal(size=(3, 4, COUNT)) + 1j * rng.normal(size=(3, 4, COUNT))) * 30.0
        targets = [(12, 900.0, -25.0, 0.2), (16, 700.0, -18.0, 2.4), *targets]
    for bin_index, amplitude, elevation, phase in targets:
        snapshot = _steer(elevation)
        for offset, share in ((-1, 0.35), (0, 1.0), (1, 0.35)):
            index = bin_index - START + offset
            value = amplitude * share * np.exp(1j * (phase + 2.1 * offset))
            cube[0, :, index] += value * snapshot[:4]
            cube[2, :, index] += value * snapshot[4:]
            # the middle transmitter sits half a wavelength across: azimuth
            cube[1, :, index] += value * snapshot[:4] * np.exp(1j * (0.4 + 0.3 * elevation))
    cube += (rng.normal(size=cube.shape) + 1j * rng.normal(size=cube.shape)) * 0.5
    if rotate is not None:
        cube *= rotate[..., None]
    return cube


def _profile(channels, capture):
    return StaticChannelProfile.from_channels(
        channels,
        capture_sha256=capture * 64,
        capture_config_sha256="e" * 64,
        range_bin_start=START,
        range_resolution_m=RESOLUTION,
        frame_count=24,
        **SHA,
    )


def _compare(empty, present, *, window=(1.0 + BIAS, 2.5 + BIAS), elevation=GROUND):
    return compare_static_channel_profiles(
        _profile(empty, "a"),
        _profile(present, "b"),
        plausible_apparent_range_m=(0.5 + BIAS, 2.9),
        candidate_window_m=window,
        element_correction=np.ones(8, dtype=complex),
        ground_elevation_deg=elevation,
    )


def _scene(seed=3):
    """A static scene: a strong mat edge at bin 34, the fence further out."""
    empty = _channels([(34, 400.0, -11.0, 0.3), (48, 900.0, -2.0, 1.1)], seed=seed)
    return empty


def test_one_ground_level_ball_is_accepted_unqualified_at_its_range():
    empty = _scene()
    present = _channels([(35, 150.0, -11.0, 2.0)], seed=9, clutter=empty)

    result = _compare(empty, present)

    assert result.status == "accepted_unqualified"
    assert result.apparent_range_m == pytest.approx(35 * RESOLUTION, abs=0.3 * RESOLUTION)
    assert result.peak_elevation_deg == pytest.approx(-11.0, abs=1.5)
    assert result.method == "coherent_per_virtual_channel"
    assert result.peak_score >= 8.0


def test_a_radar_restarted_between_captures_still_subtracts():
    """Each channel's phase turns when the radar re-initialises; one factor per channel fits it."""
    empty = _scene()
    rotate = np.exp(1j * np.random.default_rng(5).uniform(-np.pi, np.pi, size=(3, 4)))
    present = _channels([(35, 150.0, -11.0, 2.0)], seed=9, clutter=empty, rotate=rotate)

    result = _compare(empty, present)

    assert result.status == "accepted_unqualified"
    assert result.apparent_range_m == pytest.approx(35 * RESOLUTION, abs=0.3 * RESOLUTION)


def test_a_ball_whose_echo_cancels_the_mat_edge_is_not_lost_to_the_power_gate():
    """Outdoors-test-7: the ball beside the mat edge lowered that bin's power; the
    magnitude comparison failed its fractional gate, the coherent one does not."""
    empty = _channels([(35, 300.0, -14.0, 0.0), (48, 900.0, -2.0, 1.1)], seed=3)
    # the ball's echo, a little higher on the array, arrives in anti-phase with the edge's
    present = _channels([(35, 170.0, -10.0, np.pi)], seed=9, clutter=empty)

    coherent = _compare(empty, present)
    power = compare_static_range_profiles(
        _power_profile(empty, "a"),
        _power_profile(present, "b"),
        plausible_apparent_range_m=(0.5 + BIAS, 2.9),
    )

    assert power.status != "accepted"
    assert coherent.status == "accepted_unqualified"
    assert coherent.apparent_range_m == pytest.approx(35 * RESOLUTION, abs=0.3 * RESOLUTION)


def _power_profile(channels, capture):
    power = np.mean(np.abs(channels) ** 2, axis=(0, 1))
    return StaticRangeProfileV2(
        capture_sha256=capture * 64,
        radar_profile_qualified=False,
        capture_config_sha256="e" * 64,
        range_bin_start=START,
        range_bin_count=COUNT,
        range_resolution_m=RESOLUTION,
        power=tuple(float(value) for value in power),
        frame_mad_fraction=(0.0,) * COUNT,
        frame_count=24,
        **SHA,
    )


def test_a_change_high_above_the_surface_is_not_the_ball():
    empty = _scene()
    present = _channels([(35, 150.0, 25.0, 2.0)], seed=9, clutter=empty)

    result = _compare(empty, present)

    assert result.status == "rejected_no_ball"
    assert result.apparent_range_m is None
    assert any(peak["elevation_deg"] > 20.0 for peak in result.alternate_peaks)


def test_two_comparable_ground_level_changes_are_ambiguous():
    empty = _scene()
    present = _channels([(30, 150.0, -11.0, 2.0), (40, 130.0, -12.0, 0.5)], seed=9, clutter=empty)

    result = _compare(empty, present)

    assert result.status == "rejected_ambiguous"
    assert result.apparent_range_m is None


def test_only_the_candidate_window_may_hold_the_ball():
    empty = _scene()
    present = _channels([(30, 150.0, -11.0, 2.0), (40, 130.0, -12.0, 0.5)], seed=9, clutter=empty)
    near = (29 * RESOLUTION - 0.05, 32 * RESOLUTION)

    result = _compare(empty, present, window=near)

    assert result.status == "accepted_unqualified"
    assert result.apparent_range_m == pytest.approx(30 * RESOLUTION, abs=0.3 * RESOLUTION)
    assert result.candidate_window_m == pytest.approx(near)


def test_a_reflector_that_weakened_far_behind_the_ball_is_ignored():
    """The 29 Sept door: a strong static reflector 0.7 m behind the ball lost power."""
    empty = _channels([(34, 150.0, -11.0, 0.3), (48, 900.0, -12.0, 1.1)], seed=3)
    present = empty.copy()
    present[:, :, 48 - START] *= 0.7
    present = _channels([(33, 150.0, -11.0, 2.0)], seed=9, clutter=present)

    result = _compare(empty, present)

    assert result.status == "accepted_unqualified"
    assert result.apparent_range_m == pytest.approx(33 * RESOLUTION, abs=0.3 * RESOLUTION)
    assert any(abs(loss["bin"] - 48) <= 1 for loss in result.ignored_losses)


def test_a_reflector_that_changed_beside_the_ball_rejects_the_scene():
    empty = _channels([(34, 150.0, -11.0, 0.3), (37, 900.0, -12.0, 1.1)], seed=3)
    present = empty.copy()
    present[:, :, 37 - START] *= 0.7
    present = _channels([(34, 150.0, -11.0, 2.0)], seed=9, clutter=present)

    result = _compare(empty, present)

    assert result.status == "rejected_scene_changed"
    assert result.apparent_range_m is None


def test_a_capture_too_short_to_judge_is_refused():
    empty = _scene()
    present = _channels([(35, 150.0, -11.0, 2.0)], seed=9, clutter=empty)
    short = StaticChannelProfile.from_channels(
        present,
        capture_sha256="b" * 64,
        capture_config_sha256="e" * 64,
        range_bin_start=START,
        range_resolution_m=RESOLUTION,
        frame_count=6,
        **SHA,
    )

    result = compare_static_channel_profiles(
        _profile(empty, "a"),
        short,
        plausible_apparent_range_m=(0.5 + BIAS, 2.9),
        candidate_window_m=None,
        element_correction=np.ones(8, dtype=complex),
        ground_elevation_deg=GROUND,
    )

    assert result.status == "rejected_insufficient_frames"


def test_a_channel_profile_is_derived_from_the_raw_dump_and_round_trips():
    cube = np.zeros((4, 6, 4, 128), dtype=complex)
    cube[:, 0::3, :, 30] = 100.0 + 50.0j
    cube[:, 1::3, :, 30] = 10.0
    cube[:, 2::3, :, 30] = -20.0j
    raw = pack_dump(cube, n_tx=3, version=3, frame_period_us=6000, sample_fmt=SAMPLE_RANGE_FFT_IQ16)

    profile = static_channel_profile(raw, **SHA)
    again = StaticChannelProfile(**json.loads(json.dumps(profile.to_dict())))

    assert profile.channels.shape == (3, 4, profile.range_bin_count)
    assert profile.channels[0, 0, 30] == pytest.approx(100.0 + 50.0j)
    assert profile.channels[2, 3, 30] == pytest.approx(-20.0j)
    assert profile.frame_count == 4
    np.testing.assert_allclose(again.channels, profile.channels)


def test_the_policy_names_its_gates():
    policy = static_channel_estimator_policy()

    assert policy["name"] == "iwr_static_coherent_channel_difference"
    assert policy["accepted_status"] == "accepted_unqualified"
    assert policy["gates"]["minimum_peak_score"] == 8.0


# Every recorded setup from Outdoors-test-1 to -7 (29-30 Sept), and the 29 Sept door
# setup whose ball was taped at 1.00 m, reduced to their two captures' per-channel
# means (scripts/analysis/static_channel_fixture.py). Searched as a setup searches
# before the camera has the ball: the hitting area, 1.0-2.5 m corrected. Values are
# corrected ranges; None is a rejection, which hands over no range.
RECORDED = {
    "outdoors-test-1-13573b54e193": ("rejected_ambiguous", None),
    "outdoors-test-2-0e0af1876722": ("rejected_ambiguous", None),
    "outdoors-test-3-8f65f5293c16": ("accepted_unqualified", 1.467),
    "outdoors-test-5-4b79b655af27": ("accepted_unqualified", 1.431),
    "outdoors-test-5-962aff81835c": ("rejected_clutter", None),
    "outdoors-test-5-b4330a10260d": ("accepted_unqualified", 1.440),
    "outdoors-test-6-b97f4d4e1d62": ("accepted_unqualified", 1.122),
    "outdoors-test-6-d00052ffb9a2": ("accepted_unqualified", 1.467),
    "outdoors-test-7-4921659aa8bc": ("accepted_unqualified", 1.631),
    "outdoors-test-7-5a7821af3641": ("accepted_unqualified", 1.581),
    "field-20260929-door-1m": ("accepted_unqualified", 1.00),
}


def _recorded(name):
    fixture = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    empty = StaticChannelProfile(**fixture["empty"])
    present = StaticChannelProfile(**fixture["present"])
    return fixture, empty, present


def _calibrated_correction():
    calibration = json.loads(
        (Path(__file__).parents[1] / "config" / "iwr6843_calibration_reference.json").read_text(
            encoding="utf-8"
        )
    )
    return np.exp(-1j * np.asarray(calibration["elem_phase_rad"])) / np.asarray(
        calibration["elem_gain"]
    )


@pytest.mark.parametrize("name", sorted(RECORDED))
def test_the_recorded_setups_give_what_the_coherent_difference_finds(name):
    fixture, empty, present = _recorded(name)
    bias = fixture["range_bias_const_m"]
    hitting = (1.0 + bias, 2.5 + bias)

    result = compare_static_channel_profiles(
        empty,
        present,
        plausible_apparent_range_m=(0.5 + bias, 4.0 + bias),
        candidate_window_m=hitting,
        element_correction=_calibrated_correction(),
        ground_elevation_deg=fixture["ground_elevation_deg"],
        fit_exclusion_m=hitting,
    )

    status, corrected_m = RECORDED[name]
    assert result.status == status
    if corrected_m is None:
        assert result.apparent_range_m is None
        return
    assert result.apparent_range_m - bias == pytest.approx(corrected_m, abs=0.01)
    # one ground-level reflector: 9-14 deg below the array's 10 deg-up boresight
    assert -15.0 <= result.peak_elevation_deg <= -8.0


def test_the_door_setup_is_accepted_at_its_tape_range_coherently_too():
    """The 29 Sept door: the ball taped at 1.00 m, the door 0.7 m behind it changed."""
    fixture, empty, present = _recorded("field-20260929-door-1m")
    bias = fixture["range_bias_const_m"]

    result = compare_static_channel_profiles(
        empty,
        present,
        plausible_apparent_range_m=(0.5 + bias, 4.0 + bias),
        candidate_window_m=(1.0 + bias, 2.5 + bias),
        element_correction=_calibrated_correction(),
        ground_elevation_deg=fixture["ground_elevation_deg"],
        fit_exclusion_m=(1.0 + bias, 2.5 + bias),
    )

    assert result.status == "accepted_unqualified"
    assert result.apparent_range_m - bias == pytest.approx(1.00, abs=0.05)
    assert any(abs(change["range_m"] - bias - 1.67) < 0.05 for change in result.ignored_losses)


def test_the_fixtures_stay_small():
    assert sum(path.stat().st_size for path in FIXTURES.glob("*.json")) < 2_000_000
