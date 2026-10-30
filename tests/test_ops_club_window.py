"""Selected coherent club peaks retain their actual complex sample support."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from openflight.ops_speed_track import ops_speed_track
from openflight.rolling_buffer.motion_tracks import MotionTrack, Peak
from openflight.rolling_buffer.types import IQCapture
from tests.test_ops_speed_track import _processed


def processed_window():
    processed = _processed()
    processed.club_speed_mph, processed.club_timestamp_ms = 80.0, 32.0
    processed.processor_config = {
        "sample_rate_hz": 30000,
        "constants": {"FFT_SIZE": 4096, "WAVELENGTH_M": 0.01243},
        "motion_policy": {"window_samples": 256, "hop_samples": 32, "window_function": "hamming"},
    }
    processed.capture = IQCapture(
        sample_time=0.0,
        trigger_time=0.04,
        i_samples=list(range(1024)),
        q_samples=list(range(2048, 3072)),
        capture_id="own-capture",
    )
    peaks = (Peak(2, 6.4, 69.0, 15.0), Peak(3, 7.466666666666667, 72.0, 20.0))
    processed.motion = SimpleNamespace(club=MotionTrack(70.0, peaks, 1), window_samples=256)
    return processed


def test_selected_peak_keeps_its_original_iq_and_processing_geometry():
    processed = processed_window()
    original = deepcopy(processed.capture)

    record = ops_speed_track(processed)["club_window"]

    assert record["sample_start"] == 96
    assert record["sample_stop"] == 352
    assert record["i_samples"] == original.i_samples[96:352]
    assert record["q_samples"] == original.q_samples[96:352]
    assert record["sample_rate_hz"] == 30000
    assert record["fft_size"] == 4096
    assert record["wavelength_m"] == 0.01243
    assert record["window_function"] == "hamming"
    assert record["capture_id"] == "own-capture"
    assert record["frame"] == 3
    assert record["point_identity"] == "unqualified_spectral_scatterer"
    assert processed.capture.i_samples == original.i_samples


def test_retained_samples_are_detached_from_capture_buffers():
    processed = processed_window()
    record = ops_speed_track(processed)["club_window"]
    processed.capture.i_samples[96] = -1
    processed.capture.q_samples[96] = -2
    assert record["i_samples"][0] == 96
    assert record["q_samples"][0] == 2144


@pytest.mark.parametrize(
    "problem",
    [
        "missing_capture",
        "short_i",
        "short_q",
        "missing_policy",
        "wrong_size",
        "wrong_frame",
        "fractional_sample",
    ],
)
def test_unbound_or_incomplete_support_is_not_fabricated(problem):
    processed = processed_window()
    if problem == "missing_capture":
        processed.capture = None
    elif problem == "short_i":
        processed.capture.i_samples = processed.capture.i_samples[:300]
    elif problem == "short_q":
        processed.capture.q_samples = processed.capture.q_samples[:300]
    elif problem == "missing_policy":
        processed.processor_config.pop("motion_policy")
    elif problem == "wrong_size":
        processed.processor_config["motion_policy"]["window_samples"] = 128
    elif problem == "wrong_frame":
        processed.motion.club = MotionTrack(70.0, (Peak(4, 7.466666666666667, 72.0, 20.0),))
    else:
        processed.motion.club = MotionTrack(70.0, (Peak(3, 7.47, 72.0, 20.0),))

    record = ops_speed_track(processed)

    assert record["club_window"] is None
    assert record["club_window_reason"]
    assert record["club_reading"]["radial_mph"] == 72.0


def test_legacy_scalar_does_not_gain_a_claimed_coherent_window():
    processed = processed_window()
    processed.motion = None
    record = ops_speed_track(processed)
    assert record["club_window"] is None
    assert "coherent" in record["club_window_reason"]
    assert record["club_reading"] == {"window_start_ms": 32.0, "radial_mph": 80.0}


def test_raw_window_stays_private_on_the_shot():
    from openflight.rolling_buffer import RollingBufferMonitor

    shot = RollingBufferMonitor(port=None, trigger_type="sound")._create_shot(processed_window())
    assert shot.ops_speed_track["club_window"]["sample_start"] == 96
    assert "ops_speed_track" not in shot.to_dict()
