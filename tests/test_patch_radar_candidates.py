"""The radar searches only the patch's range window and reports every candidate (P8-3).

Neither the power-profile (magnitude) difference nor the coherent one is always
right: on harjot-indoor-test-1 the coherent difference accepted 1.575 m against a
tape of about 1.25 m, and the magnitude's strongest change, about 1.20 m, failed
its fractional gate; on the 29 Sept door setup both read the tape's 1.00 m. So
both methods' peaks are reported with their scores and elevations, and the
camera decides between them (P8-4).
"""

import json
from pathlib import Path

import numpy as np
import pytest

from openflight.iwr6843.range_evidence import (
    STATIC_PROFILE_V2_SCHEMA,
    StaticChannelProfile,
    StaticRangeProfileV2,
    static_patch_candidates,
)

FIXTURES = Path(__file__).parent / "fixtures"
CALIBRATION = json.loads(
    (Path(__file__).parents[1] / "config" / "iwr6843_calibration_reference.json").read_text(
        encoding="utf-8"
    )
)
CORRECTION = np.exp(-1j * np.asarray(CALIBRATION["elem_phase_rad"])) / np.asarray(
    CALIBRATION["elem_gain"]
)
GROUND = (-19.0, -4.0)


def _indoor():
    fixture = json.loads(
        (FIXTURES / "ground_patch" / "indoor-c77d227da087-radar.json").read_text(encoding="utf-8")
    )
    return (
        fixture,
        StaticRangeProfileV2(**fixture["empty_power"]),
        StaticRangeProfileV2(**fixture["present_power"]),
        StaticChannelProfile(**fixture["empty_channels"]),
        StaticChannelProfile(**fixture["present_channels"]),
    )


def _door():
    power = json.loads(
        (FIXTURES / "iwr6843_static_range" / "field-20260929-door-1m.json").read_text(
            encoding="utf-8"
        )
    )
    channels = json.loads(
        (FIXTURES / "iwr6843_static_coherent" / "field-20260929-door-1m.json").read_text(
            encoding="utf-8"
        )
    )
    return (
        power,
        StaticRangeProfileV2(**power["empty"]["profile_v2"]),
        StaticRangeProfileV2(**power["present"]["profile_v2"]),
        StaticChannelProfile(**channels["empty"]),
        StaticChannelProfile(**channels["present"]),
    )


def _candidates(profiles, window_m, *, fit_exclusion_m=None):
    fixture, empty, present, empty_channels, present_channels = profiles
    bias = fixture["range_bias_const_m"]
    return static_patch_candidates(
        empty,
        present,
        window_m=window_m,
        bias_m=bias,
        empty_channels=empty_channels,
        present_channels=present_channels,
        element_correction=CORRECTION,
        ground_elevation_deg=GROUND,
        fit_exclusion_m=fit_exclusion_m,
    )


def _ranges(result, method):
    return [item["range_m"] for item in result.candidates if item["method"] == method]


@pytest.mark.parametrize(
    "window",
    [
        (0.38, 2.7),  # before the tilt is calibrated the window is wide
        (0.75, 2.0),  # a calibrated patch 1.25 m out
    ],
)
def test_the_indoor_setup_reports_both_methods_peaks(window):
    result = _candidates(_indoor(), window, fit_exclusion_m=(0.75, 2.0))

    magnitude = _ranges(result, "magnitude")
    coherent = _ranges(result, "coherent")
    # the magnitude's strongest change, near the tape's 1.25 m, is reported although
    # it fails the old fractional gate (0.37 against 0.50)
    assert any(abs(value - 1.20) < 0.03 for value in magnitude)
    top = next(item for item in result.candidates if item["method"] == "magnitude")
    assert top["fractional_excess"] == pytest.approx(0.37, abs=0.02)
    assert top["fractional_excess"] < 0.5
    # the coherent difference's 1.575 m is reported too, with its elevation
    assert any(abs(value - 1.575) < 0.03 for value in coherent)
    coherent_top = next(item for item in result.candidates if item["method"] == "coherent")
    assert coherent_top["score"] == pytest.approx(49.0, abs=4.0)
    assert coherent_top["ground_level"] is True
    assert -15.0 < coherent_top["elevation_deg"] < -5.0
    for item in result.candidates:
        assert window[0] <= item["range_m"] <= window[1]
        assert item["uncertainty_m"] > 0.0
        assert set(item) >= {"method", "range_m", "score", "elevation_deg", "ground_level"}
    # the methods' own verdicts are kept as they were
    assert result.coherent.status == "accepted_unqualified"
    assert result.magnitude.status == "rejected_no_ball"


def test_nothing_outside_the_window_is_reported():
    result = _candidates(_indoor(), (0.9, 1.45), fit_exclusion_m=(0.9, 1.45))

    assert not any(abs(value - 1.575) < 0.05 for value in _ranges(result, "coherent"))
    assert any(abs(value - 1.20) < 0.03 for value in _ranges(result, "magnitude"))


def test_the_29_sept_door_setup_still_reads_its_tape_by_both_methods():
    result = _candidates(_door(), (0.5, 1.6), fit_exclusion_m=(0.5, 1.6))

    for method in ("magnitude", "coherent"):
        best = next(item for item in result.candidates if item["method"] == method)
        assert best["range_m"] == pytest.approx(1.00, abs=0.05)


def _synthetic(present_extra, *, lost=None):
    count = 53
    base = np.full(count, 1.0e6)
    empty = base.copy()
    present = base.copy()
    for index, gain in present_extra:
        present[index] += gain
    if lost is not None:
        index, fraction = lost
        empty[index] += 4.0e6
        present[index] += 4.0e6 * (1.0 - fraction)
    common = {
        "radar_profile_sha256": "c" * 64,
        "radar_profile_qualified": False,
        "rig_geometry_sha256": "d" * 64,
        "capture_config_sha256": "e" * 64,
        "range_bin_start": 8,
        "range_bin_count": count,
        "range_resolution_m": 0.046875,
        "frame_mad_fraction": [0.01] * count,
        "frame_count": 24,
        "schema": STATIC_PROFILE_V2_SCHEMA,
    }
    return (
        StaticRangeProfileV2(capture_sha256="a" * 64, power=tuple(empty), **common),
        StaticRangeProfileV2(capture_sha256="b" * 64, power=tuple(present), **common),
    )


def test_a_changed_reflector_beside_the_ball_is_a_warning_not_a_refusal():
    # the ball at bin 27 (1.27 m apparent), a still reflector two bins away lost half
    empty, present = _synthetic([(19, 3.0e6)], lost=(21, 0.6))

    result = static_patch_candidates(empty, present, window_m=(0.8, 2.0), bias_m=0.066)

    assert result.magnitude.status == "rejected_scene_changed"
    best = next(item for item in result.candidates if item["method"] == "magnitude")
    assert best["range_m"] == pytest.approx(27 * 0.046875 - 0.066, abs=0.03)
    assert any("scene_changed" in warning for warning in best["warnings"])
    assert any("scene_changed" in warning for warning in result.warnings)


def test_a_window_past_the_capture_is_clipped_and_says_so():
    empty, present = _synthetic([(19, 3.0e6)])

    result = static_patch_candidates(empty, present, window_m=(0.8, 4.0), bias_m=0.066)

    assert result.window_m[1] < 2.8
    assert any("beyond the capture" in warning for warning in result.warnings)
    assert result.candidates
