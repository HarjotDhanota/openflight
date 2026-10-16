"""Known-answer optical events do not become hardware accuracy claims."""

import numpy as np
import pytest

from openflight.camera.led_timing import candidate_report, offset_intervals, optical_transition


def simulated_flash(exposure_us=800, phase_ns=300_000, convention="start", shift_ns=250_000):
    starts = 1_000_000_000 + np.arange(24, dtype=np.int64) * 2_000_000
    event = int(starts[10]) + phase_ns
    exposure_ns = exposure_us * 1000
    fraction = np.clip((starts + exposure_ns - event) / exposure_ns, 0, 1)
    frames = (
        np.broadcast_to(np.rint(20 + 180 * fraction)[:, None, None], (24, 4, 6))
        .astype(np.uint8)
        .copy()
    )
    stamp_adjustment = {"start": 0, "midpoint": exposure_ns // 2, "end": exposure_ns}[convention]
    sensor = starts + stamp_adjustment - shift_ns
    return frames, sensor, np.full(24, exposure_us), event


@pytest.mark.parametrize("convention", ["start", "midpoint", "end"])
@pytest.mark.parametrize("phase", [0, 100_000, 790_000, 1_200_000, 1_999_999])
def test_interval_contains_the_known_correction_including_blind_gaps(convention, phase):
    frames, sensor, exposure, event = simulated_flash(convention=convention, phase_ns=phase)
    optical = optical_transition(frames, sensor, exposure, (1, 1, 5, 3))
    intervals = offset_intervals(optical, (event - 100, event + 100))
    low, high = intervals[convention]
    assert low <= 250_000 <= high
    assert high > low
    assert len(optical["roi_mean_dn"]) == 24


@pytest.mark.parametrize("kind", ["dark", "already_on", "saturated", "two_flashes", "weak"])
def test_unusable_optical_events_are_rejected(kind):
    frames, sensor, exposure, _event = simulated_flash()
    if kind == "dark":
        frames[:] = 20
    elif kind == "already_on":
        frames[:] = 200
    elif kind == "saturated":
        frames[12:] = 255
    elif kind == "two_flashes":
        frames[14:17] = 20
    else:
        frames[frames > 20] = 25
    with pytest.raises(ValueError):
        optical_transition(frames, sensor, exposure, (0, 0, 6, 4))


@pytest.mark.parametrize("roi", [(-1, 0, 5, 3), (0, 0, 7, 4), (2, 0, 1, 4), (0, 0, 6)])
def test_roi_must_be_inside_saved_pixels(roi):
    frames, sensor, exposure, _event = simulated_flash()
    with pytest.raises(ValueError, match="ROI"):
        optical_transition(frames, sensor, exposure, roi)


def test_missing_frame_widens_the_support_and_is_counted():
    frames, sensor, exposure, event = simulated_flash(phase_ns=1_200_000)
    normal = optical_transition(frames, sensor, exposure, (0, 0, 6, 4))
    keep = np.arange(24) != 10
    gap = optical_transition(frames[keep], sensor[keep], exposure[keep], (0, 0, 6, 4))
    assert gap["cadence"]["gap_count"] == 1
    normal_bounds = offset_intervals(normal, (event, event))["start"]
    gap_bounds = offset_intervals(gap, (event, event))["start"]
    assert gap_bounds[1] - gap_bounds[0] > normal_bounds[1] - normal_bounds[0]


@pytest.mark.parametrize(
    "failure", ["duplicate_timestamp", "missing_exposure", "changed_exposure", "float_time"]
)
def test_frame_evidence_must_be_observed_and_ordered(failure):
    frames, sensor, exposure, _event = simulated_flash()
    if failure == "duplicate_timestamp":
        sensor[8] = sensor[7]
    elif failure == "missing_exposure":
        exposure[:] = 0
    elif failure == "changed_exposure":
        exposure[12] += 1
    else:
        sensor = sensor.astype(float)
    with pytest.raises(ValueError):
        optical_transition(frames, sensor, exposure, (0, 0, 6, 4))


def _record(capture_id, split, interval, **overrides):
    return {
        "id": capture_id,
        "split": split,
        "status": "usable",
        "boot_id": "boot-a",
        "mode_id": "mode-a",
        "event_id": capture_id,
        "exposure_us": 800,
        "offset_intervals_ns": {name: interval for name in ("start", "midpoint", "end")},
        **overrides,
    }


def test_validation_does_not_change_the_fitted_interval_or_claim_zero_error():
    fit = [_record("a", "fit", [100, 400]), _record("b", "fit", [200, 500])]
    report = candidate_report([*fit, _record("c", "validation", [900, 1000])])
    candidate = report["candidates"]["start"]
    assert candidate["fit_offset_interval_ns"] == [200, 400]
    assert candidate["fixed_offset_ns"] == 300
    assert candidate["validation"]["max_distance_outside_support_ns"] == 600
    assert candidate["validation"]["consistent"] is False
    assert report["qualified_for_fusion"] is False
    assert report["physical_accuracy_ns"] is None


@pytest.mark.parametrize(
    "override", [{"boot_id": "boot-b"}, {"mode_id": "mode-b"}, {"event_id": "a"}]
)
def test_mixed_boots_modes_or_duplicate_events_cannot_produce_candidates(override):
    report = candidate_report(
        [
            _record("a", "fit", [0, 10]),
            _record("b", "fit", [0, 10]),
            _record("c", "validation", [0, 10], **override),
        ]
    )
    assert report["candidates"] == {}
    assert report["reason"]


def test_excluded_validation_is_not_counted_as_consistent():
    report = candidate_report(
        [
            _record("a", "fit", [0, 10]),
            _record("b", "fit", [0, 10]),
            _record("c", "validation", [0, 10]),
            {"id": "d", "split": "validation", "status": "unusable", "reason": "missing_gate"},
        ]
    )
    assert report["candidates"]["start"]["validation"]["consistent"] is False
    assert report["validation_complete"] is False


def test_compatible_intervals_remain_ambiguous_at_one_exposure():
    report = candidate_report(
        [
            _record("a", "fit", [0, 100]),
            _record("b", "fit", [10, 90]),
            _record("c", "validation", [0, 100]),
        ]
    )
    assert report["supported_conventions"] == ["start", "midpoint", "end"]
    assert report["convention"] is None
    assert report["qualified_for_fusion"] is False


def test_no_validation_is_incomplete_and_cannot_support_a_convention():
    report = candidate_report([_record("a", "fit", [0, 10]), _record("b", "fit", [0, 10])])
    assert report["validation_complete"] is False
    assert report["supported_conventions"] == []
