"""Known-answer host clocks distinguish delivery delay from physical alignment."""

import importlib.util
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts/hardware-test/camera_clock_preflight.py"
SPEC = importlib.util.spec_from_file_location("camera_clock_preflight", SCRIPT)
check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check)


def pairs():
    return [
        {"mono_before_ns": 0, "boottime_ns": 110, "mono_after_ns": 20},
        {"mono_before_ns": 1000, "boottime_ns": 1110, "mono_after_ns": 1020},
    ]


def frames():
    return [
        {"sensor_timestamp_ns": 200, "host_timestamp_ns": 150, "exposure_us": 150},
        {"sensor_timestamp_ns": 300, "host_timestamp_ns": 250, "exposure_us": 150},
        {"sensor_timestamp_ns": 400, "host_timestamp_ns": 350, "exposure_us": 150},
    ]


def test_clock_bounds_sign_and_delivery_latency_are_not_fitted_away():
    report = check.summarize(frames(), pairs(), "boot", "boot")
    assert report["issues"] == []
    assert report["boottime_minus_monotonic_envelope_ns_derived"] == [90, 110]
    lag = report["callback_delivery_lag_ns_derived"]
    assert lag["p50_lower"] == 40
    assert lag["p50_upper"] == 60
    assert report["cadence_derived"]["median_sensor_interval_ns"] == 100
    assert report["physical_timing_qualified"] is False


def test_later_callback_does_not_move_sensor_clock_mapping():
    delayed = deepcopy(frames())
    for row in delayed:
        row["host_timestamp_ns"] += 500
    report = check.summarize(delayed, pairs(), "boot", "boot")
    assert report["boottime_minus_monotonic_envelope_ns_derived"] == [90, 110]
    assert report["callback_delivery_lag_ns_derived"]["p50_lower"] == 540


def test_clock_step_invalidates_conversion_despite_regular_frames():
    stepped = pairs()
    stepped[1]["boottime_ns"] += 100
    report = check.summarize(frames(), stepped, "boot", "boot")
    assert "clock_offset_not_constant" in report["issues"]
    assert report["callback_delivery_lag_ns_derived"] is None


@pytest.mark.parametrize("before,after", [(None, None), ("first", "next")])
def test_missing_or_changed_boot_cannot_qualify_clock(before, after):
    report = check.summarize(frames(), pairs(), before, after)
    assert "boot_identity_missing_or_changed" in report["issues"]
    assert report["boottime_minus_monotonic_envelope_ns_derived"] is None


@pytest.mark.parametrize("value", [True, -1, None, 1.5, 2**64])
def test_malformed_clock_observation_is_preserved_as_failure(value):
    bad = pairs()
    bad[0]["mono_before_ns"] = value
    report = check.summarize(frames(), bad, "boot", "boot")
    assert "malformed_clock_pair" in report["issues"]
    assert report["callback_delivery_lag_ns_derived"] is None


def test_unordered_clock_pairs_cannot_convert():
    report = check.summarize(frames(), list(reversed(pairs())), "boot", "boot")
    assert "unordered_clock_pair" in report["issues"]


def test_missing_clock_pairs_cannot_convert():
    report = check.summarize(frames(), [], "boot", "boot")
    assert "clock_pairs_missing" in report["issues"]
    assert report["callback_delivery_lag_ns_derived"] is None


@pytest.mark.parametrize("rows", [[], frames()[:1], list(reversed(frames())), frames()[:1] * 2])
def test_no_cadence_invented_for_missing_duplicate_or_unordered_frames(rows):
    report = check.summarize(rows, pairs(), "boot", "boot")
    assert "insufficient_or_unordered_frames" in report["issues"]
    assert report["cadence_derived"] is None


def test_frames_must_be_enclosed_by_clock_observations():
    observed = frames()
    observed[-1]["host_timestamp_ns"] = 1021
    report = check.summarize(observed, pairs(), "boot", "boot")
    assert "frames_outside_clock_observations" in report["issues"]


def test_gap_is_reported_without_moving_sensor_times():
    observed = frames()
    observed[-1]["sensor_timestamp_ns"] += 200
    observed[-1]["host_timestamp_ns"] += 200
    observed.insert(2, {"sensor_timestamp_ns": 400, "host_timestamp_ns": 350, "exposure_us": 150})
    report = check.summarize(observed, pairs(), "boot", "boot")
    assert report["cadence_derived"]["gap_count"] == 1


def test_raw_timestamp_files_are_exact_jsonl_and_never_overwritten(tmp_path):
    path = tmp_path / "frames.jsonl"
    digest = check._save_rows(path, frames())
    assert len(digest) == 64
    assert [json.loads(line) for line in path.read_text().splitlines()] == frames()
    with pytest.raises(FileExistsError):
        check._save_rows(path, [])


def test_existing_output_stops_before_camera_access(tmp_path):
    assert check.main(["--output-dir", str(tmp_path)]) == 2


@pytest.mark.parametrize("arguments", [["--seconds", "nan"], ["--fps", "0"], ["--gain", "-1"]])
def test_bad_capture_settings_are_rejected_before_device_access(tmp_path, arguments):
    with pytest.raises(SystemExit):
        check.main([*arguments, "--output-dir", str(tmp_path / "unused")])


def test_recording_saves_only_timestamps_and_releases_camera(tmp_path, monkeypatch):
    clock = SimpleNamespace(seconds=1.0, running=False)
    instances = []

    class Runtime:
        def __init__(self, **kwargs):
            assert kwargs["use_gpio_trigger"] is False
            assert kwargs["settings"].auto_exposure is False
            self._ring = SimpleNamespace(add_frame=lambda _frame: None)
            self._resolved_config = {"raw": {"size": [1280, 800], "format": "R8"}}
            self.stopped = False
            instances.append(self)

        def start(self):
            clock.running = True

        def stop(self):
            clock.running = False
            self.stopped = True

    def sleep(seconds):
        clock.seconds += seconds
        if clock.running and seconds >= 0.001:
            stamp = round(clock.seconds * 1e9)
            instances[-1]._ring.add_frame(
                SimpleNamespace(
                    image=None,
                    sensor_timestamp_ns=stamp - 1_000_000,
                    host_timestamp_ns=stamp,
                    exposure_us=150,
                    analogue_gain=8,
                )
            )

    monkeypatch.setattr(check, "CameraCaptureRuntime", Runtime)
    monkeypatch.setattr(check.time, "sleep", sleep)
    monkeypatch.setattr(check.time, "monotonic", lambda: clock.seconds)
    monkeypatch.setattr(
        check,
        "clock_pair",
        lambda: {
            "mono_before_ns": round(clock.seconds * 1e9),
            "boottime_ns": round(clock.seconds * 1e9),
            "mono_after_ns": round(clock.seconds * 1e9),
        },
    )
    monkeypatch.setattr(check, "boot_identity", lambda: "test-boot")
    output = tmp_path / "observations"
    assert check.main(["--seconds", "0.2", "--output-dir", str(output)]) == 0
    assert instances[0].stopped
    assert sorted(path.name for path in output.iterdir()) == [
        "clock-pairs.jsonl",
        "frames.jsonl",
        "report.json",
    ]
    report = json.loads((output / "report.json").read_text())
    assert report["frame_count_measured"] >= 2
    assert report["physical_timing_qualified"] is False
    assert report["callback_delivery_lag_ns_derived"]["p50_lower"] == 1_000_000
