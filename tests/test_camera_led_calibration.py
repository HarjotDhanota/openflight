"""File and recorder integration uses simulated inputs; no Pi hardware is opened."""

import hashlib
import json
from types import SimpleNamespace

import numpy as np
import pytest

from openflight.camera import led_recording
from openflight.camera.capture_runtime import CameraCaptureRuntime, CameraCaptureSettings
from openflight.camera.led_calibration import analyze_main, analyze_manifest
from openflight.camera.triggered_buffer import CameraFrame, TriggeredCapture
from openflight.gate_timing import GateEvent
from tests.test_camera_led_timing import simulated_flash

BOOT_OFFSET_NS = 50_000


def write_capture(root, capture_id, split, *, exposure=800, phase=300_000, boot_id="test-boot"):
    directory = root / capture_id
    directory.mkdir(parents=True)
    frames, sensor, exposures, event = simulated_flash(exposure_us=exposure, phase_ns=phase)
    np.savez(
        directory / "frames.npz",
        frames=frames,
        sensor_timestamp_ns=sensor,
        exposure_us=exposures,
        analogue_gain=np.ones(24, dtype=np.float32),
    )
    mono = event - BOOT_OFFSET_NS
    gate = {
        "event_id": capture_id,
        "mono_before_ns": mono + 999_900,
        "mono_after_ns": mono + 1_000_100,
        "age_ns": 1_000_000,
        "backend": "LGPIOFactory",
        "reading_error": None,
    }
    settings = {
        "width": 6,
        "height": 4,
        "fps": 500.0,
        "stream": "raw",
        "scaler_crop": None,
        "rotate_180": False,
        "mirror_horizontal": False,
        "auto_exposure": False,
        "exposure_us": exposure,
    }
    startup = {
        "settings": settings,
        "camera_properties": {"Model": "SIMULATED"},
        "resolved_config": {"sensor": {"bit_depth": 8}},
    }
    fingerprint = hashlib.sha256(
        json.dumps(startup, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    metadata = {
        "trigger_clocks": {
            "gate_event": gate,
            "gate_to_camera_callback_ns": [1_000_000, 1_000_200],
        },
        "tester_setup": {
            "led_timing": {
                "capture_id": capture_id,
                "split": split,
                "boot_id": boot_id,
                "load_label": "simulated",
                "clock": {
                    "mono_before_ns": mono + 2_000_000,
                    "mono_after_ns": mono + 2_000_100,
                    "boottime_ns": event + 2_000_050,
                },
            }
        },
        "capture_mode": {
            "context_status": "uniform",
            "contexts": [{"id": fingerprint, "startup": startup}],
            "frames": {
                "context_index": [0] * 24,
                "scaler_crop": [[0, 0, 6, 4]] * 24,
                "frame_duration_us": [2000] * 24,
                "saved_width": [6] * 24,
                "saved_height": [4] * 24,
            },
        },
    }
    (directory / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    return {"id": capture_id, "split": split, "path": capture_id}


def write_manifest(root, entries, **overrides):
    value = {
        "schema_version": 1,
        "kind": "camera_led_recording",
        "status": "completed",
        "roi": [0, 0, 6, 4],
        "min_contrast_dn": 20.0,
        "max_saturation_fraction": 0.05,
        "captures": entries,
        **overrides,
    }
    path = root / "manifest.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def dataset(root):
    entries = []
    for index, exposure in enumerate((500, 800, 1500)):
        for number, (split, phase) in enumerate(
            (("fit", 200_000), ("fit", 1_400_000), ("validation", 700_000))
        ):
            entries.append(
                write_capture(
                    root, f"capture-{index}-{number}", split, exposure=exposure, phase=phase
                )
            )
    return write_manifest(root, entries)


def mutate_metadata(root, change):
    path = root / "capture-0-2" / "metadata.json"
    metadata = json.loads(path.read_text(encoding="utf-8"))
    change(metadata)
    path.write_text(json.dumps(metadata), encoding="utf-8")


def test_complete_cli_report_preserves_hashes_and_never_installs_a_correction(tmp_path):
    manifest = dataset(tmp_path)
    out = tmp_path / "report.json"
    assert analyze_main([str(manifest), "--out", str(out)]) == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["qualified_for_fusion"] is False
    assert report["physical_accuracy_ns"] is None
    assert report["exposure_design_met"] is True
    assert len(report["captures"]) == 9
    assert all(item["status"] == "usable" for item in report["captures"])
    assert report["captures"][0]["hashes"]["frames.npz"]
    assert report["diagnostics"]["sensor_gap_count"] == 0
    assert report["diagnostics"]["callback_delay_max_upper_ns"] == 1_000_200
    assert report["captures"][0]["event_boottime_interval_ns"] == [1_020_199_850, 1_020_200_150]
    with pytest.raises(SystemExit) as error:
        analyze_main([str(manifest), "--out", str(out)])
    assert error.value.code == 2


def test_real_camera_runtime_saves_the_evidence_the_analyser_requires(tmp_path):
    runtime = CameraCaptureRuntime(
        output_dir=tmp_path / "saved",
        settings=CameraCaptureSettings(width=6, height=4, auto_exposure=False),
        use_gpio_trigger=False,
    )
    runtime._running = True
    runtime._ring = SimpleNamespace(trigger=lambda _stamp: True, latest_frame=None)
    entries = []
    for index, (split, phase) in enumerate(
        (("fit", 200_000), ("fit", 1_400_000), ("validation", 700_000))
    ):
        entry = write_capture(tmp_path / "prepared", f"input-{index}", split, phase=phase)
        source = tmp_path / "prepared" / entry["path"]
        metadata = json.loads((source / "metadata.json").read_text(encoding="utf-8"))
        runtime._trigger_evidence_provider = lambda _wall, frozen=metadata: frozen["tester_setup"]
        raw_gate = metadata["trigger_clocks"]["gate_event"]
        event = GateEvent.observe(lambda frozen=raw_gate: frozen, 123.0)
        assert runtime.notify_gate_event(event)
        with np.load(source / "frames.npz") as archive:
            frames = tuple(
                CameraFrame(
                    image=image,
                    sensor_timestamp_ns=int(stamp),
                    host_timestamp_ns=int(stamp) + 4_000_000,
                    exposure_us=800,
                    analogue_gain=1.0,
                    scaler_crop=(0, 0, 6, 4),
                    frame_duration_us=2000,
                    capture_mode=metadata["capture_mode"]["contexts"][0]["startup"],
                )
                for image, stamp in zip(archive["frames"], archive["sensor_timestamp_ns"])
            )
        saved = runtime._save_capture(
            index + 1,
            123.0 + index,
            TriggeredCapture(frames, 10, 123),
            trigger_evidence=runtime._trigger_evidence.get_nowait(),
            trigger_clocks=runtime._trigger_clocks.get_nowait(),
            auto_exposure=runtime._trigger_auto_exposure.get_nowait(),
        )
        entries.append(
            {"id": entry["id"], "split": split, "path": saved.path.relative_to(tmp_path).as_posix()}
        )
    result = analyze_manifest(write_manifest(tmp_path, entries))
    assert all(item["status"] == "usable" for item in result["captures"])
    assert result["supported_conventions"]
    assert result["qualified_for_fusion"] is False


@pytest.mark.parametrize(
    "change",
    [
        lambda m: m["trigger_clocks"].pop("gate_event"),
        lambda m: m["trigger_clocks"]["gate_event"].update(backend="RPiGPIOFactory"),
        lambda m: m["trigger_clocks"]["gate_event"].update(previous_callback_mono_ns=2_000_000_000),
        lambda m: m["tester_setup"]["led_timing"]["clock"].update(boottime_ns=None),
        lambda m: m["tester_setup"]["led_timing"]["clock"].update(mono_before_ns=3_000_000_000),
        lambda m: m["tester_setup"]["led_timing"].update(split="fit"),
        lambda m: m["tester_setup"]["led_timing"].update(boot_id=None),
        lambda m: m["capture_mode"]["contexts"][0].update(id="tampered"),
        lambda m: m["capture_mode"]["frames"]["frame_duration_us"].__setitem__(0, None),
    ],
)
def test_bad_validation_evidence_is_retained_and_cannot_pass(tmp_path, change):
    manifest = dataset(tmp_path)
    mutate_metadata(tmp_path, change)
    report = analyze_manifest(manifest)
    rejected = report["captures"][2]
    assert rejected["status"] == "unusable" and rejected["reason"]
    assert report["validation_complete"] is False
    assert not report["supported_conventions"]


def test_mixed_boots_and_duplicate_events_are_refused(tmp_path):
    manifest = dataset(tmp_path)
    mutate_metadata(
        tmp_path, lambda m: m["tester_setup"]["led_timing"].update(boot_id="other-boot")
    )
    assert analyze_manifest(manifest)["reason"] == "mixed_boots_or_modes"
    mutate_metadata(tmp_path, lambda m: m["tester_setup"]["led_timing"].update(boot_id="test-boot"))
    mutate_metadata(
        tmp_path, lambda m: m["trigger_clocks"]["gate_event"].update(event_id="capture-0-0")
    )
    assert analyze_manifest(manifest)["reason"] == "duplicate_gate_event"


def test_a_clock_offset_jump_requires_a_new_calibration_epoch(tmp_path):
    manifest = dataset(tmp_path)
    mutate_metadata(
        tmp_path,
        lambda m: m["tester_setup"]["led_timing"]["clock"].update(boottime_ns=3_000_000_000),
    )
    report = analyze_manifest(manifest)
    assert report["reason"] == "clock_offset_epoch_changed"
    assert report["candidates"] == {}


@pytest.mark.parametrize("path", ["../outside", "C:/outside", "C:outside", "/outside"])
def test_manifest_paths_cannot_escape_the_recording(tmp_path, path):
    manifest = write_manifest(tmp_path, [{"id": "a", "split": "fit", "path": path}])
    record = analyze_manifest(manifest)["captures"][0]
    assert record["status"] == "unusable"
    assert "path" in record["reason"]


def test_interrupted_recording_keeps_results_without_a_candidate(tmp_path):
    manifest = dataset(tmp_path)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["status"] = "interrupted"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    report = analyze_manifest(manifest)
    assert len(report["captures"]) == 9
    assert report["reason"] == "recording_incomplete" and not report["candidates"]


def test_historical_capture_without_new_clock_evidence_stays_unmeasured(tmp_path):
    entry = write_capture(tmp_path, "old", "fit")
    (tmp_path / "old" / "metadata.json").write_text("{}", encoding="utf-8")
    report = analyze_manifest(write_manifest(tmp_path, [entry]))
    assert report["captures"][0]["status"] == "unusable"
    assert report["candidates"] == {}


def args_for(root, *extras):
    return led_recording.recording_arguments(
        [
            "--output-dir",
            str(root),
            "--roi",
            "0,0,6,4",
            "--width",
            "6",
            "--height",
            "4",
            "--fps",
            "500",
            "--fit-captures",
            "2",
            "--validation-captures",
            "1",
            "--lens-label",
            "SIMULATED",
            *extras,
        ]
    )


class FakeRuntime:
    instances = []
    fail_at = None

    def __init__(self, **kwargs):
        self.settings = kwargs["settings"]
        self.output_dir = kwargs["output_dir"]
        self.provider = kwargs["trigger_evidence_provider"]
        self.count = 0
        self.stopped = False
        self.identity = None
        type(self).instances.append(self)

    def start(self):
        assert self.settings.auto_exposure is False
        assert self.settings.diagnostic_capture is True

    def stop(self):
        self.stopped = True

    def notify_gate_event(self, _event):
        self.identity = self.provider(123.0)["led_timing"]
        return True

    def capture_for_shot(self, _impact, *, timeout_s):
        assert timeout_s <= 0.5
        self.count += 1
        if self.count == self.fail_at:
            raise KeyboardInterrupt()
        gate = GateEvent.observe(
            lambda: {
                "mono_before_ns": 1_000_000,
                "mono_after_ns": 1_000_100,
                "age_ns": 500_000,
                "backend": "LGPIOFactory",
            },
            123.0,
        )
        assert self.notify_gate_event(gate)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        capture_id = self.identity["capture_id"]
        entry = write_capture(
            self.output_dir, capture_id, self.identity["split"], exposure=self.settings.exposure_us
        )
        path = self.output_dir / entry["path"]
        metadata = json.loads((path / "metadata.json").read_text(encoding="utf-8"))
        return SimpleNamespace(valid=True, path=path, metadata=metadata)


def record_fake(args, **overrides):
    FakeRuntime.instances = []
    return led_recording.record_session(
        args,
        runtime_factory=FakeRuntime,
        boot_reader=lambda: "test-boot",
        clock_reader=lambda: {
            "mono_before_ns": 2_000_000_000,
            "mono_after_ns": 2_000_000_100,
            "boottime_ns": 2_000_050_050,
        },
        provenance_reader=lambda *_args: {"status": "mocked"},
        **overrides,
    )


def test_recorder_declares_splits_and_closes_all_mock_devices(tmp_path):
    output = tmp_path / "recording"
    result = record_fake(args_for(output))
    assert result["status"] == "completed"
    assert result["journal_counts"]["gate_observed"] == 9
    assert result["unlisted_accepted_trigger_count"] == 0
    assert len(result["captures"]) == 9
    assert [item["split"] for item in result["captures"]] == ["fit", "fit", "validation"] * 3
    assert len({item["id"] for item in result["captures"]}) == 9
    assert all(runtime.stopped for runtime in FakeRuntime.instances)
    events = [
        json.loads(line)
        for line in (output / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len([event for event in events if event["event"] == "gate_observed"]) == 9
    assert events[-1]["status"] == "completed"
    with pytest.raises(FileExistsError):
        record_fake(args_for(output))


def test_recorder_preserves_partial_manifest_on_interrupt(tmp_path, monkeypatch):
    monkeypatch.setattr(FakeRuntime, "fail_at", 2)
    output = tmp_path / "interrupted"
    result = record_fake(args_for(output))
    assert result["status"] == "interrupted"
    assert len(result["captures"]) == 1
    assert FakeRuntime.instances[0].stopped
    assert (output / "manifest.json").exists()
    assert analyze_manifest(output / "manifest.json")["reason"] == "recording_incomplete"


def test_recorder_refuses_missing_boot_identity_without_starting_devices(tmp_path):
    FakeRuntime.instances = []
    result = led_recording.record_session(
        args_for(tmp_path / "no-boot"),
        runtime_factory=FakeRuntime,
        boot_reader=lambda: None,
        clock_reader=lambda: {"boottime_ns": None},
        provenance_reader=lambda *_args: {},
    )
    assert result["status"] == "failed"
    assert FakeRuntime.instances == []


def test_device_start_failure_closes_resources_and_keeps_the_error(tmp_path, monkeypatch):
    def broken(_self):
        raise RuntimeError("camera busy")

    monkeypatch.setattr(FakeRuntime, "start", broken)
    output = tmp_path / "failed"
    result = record_fake(args_for(output))
    assert result["status"] == "failed"
    assert "camera busy" in result["error"]
    assert FakeRuntime.instances[0].stopped
    assert (output / "manifest.json").exists()


def test_capture_timeout_keeps_manifest_and_stops_the_runtime(tmp_path, monkeypatch):
    ticks = iter(range(100))
    monkeypatch.setattr(led_recording.time, "monotonic", lambda: next(ticks))
    output = tmp_path / "timeout"
    result = record_fake(args_for(output, "--timeout-s", "0.1"))
    assert result["status"] == "failed"
    assert "TimeoutError" in result["error"]
    assert FakeRuntime.instances[0].stopped
    assert result["captures"] == []


def test_corrupt_capture_is_retained_as_a_failure(tmp_path):
    manifest = dataset(tmp_path)
    (tmp_path / "capture-0-2" / "frames.npz").write_bytes(b"broken")
    report = analyze_manifest(manifest)
    assert report["captures"][2]["status"] == "unusable"
    assert not report["supported_conventions"]


def test_non_object_manifest_has_a_clear_error(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="manifest"):
        analyze_manifest(manifest)


def test_clock_reader_brackets_and_does_not_invent_boottime(monkeypatch):
    ticks = iter([100, 200])
    monkeypatch.setattr(led_recording.time, "monotonic_ns", lambda: next(ticks))
    monkeypatch.setattr(led_recording.time, "CLOCK_BOOTTIME", 7, raising=False)
    monkeypatch.setattr(led_recording.time, "clock_gettime_ns", lambda _clock: 500, raising=False)
    assert led_recording.read_clock_bracket() == {
        "mono_before_ns": 100,
        "mono_after_ns": 200,
        "boottime_ns": 500,
    }
    monkeypatch.delattr(led_recording.time, "CLOCK_BOOTTIME")
    ticks = iter([300, 400])
    assert led_recording.read_clock_bracket()["boottime_ns"] is None


@pytest.mark.parametrize(
    "extra",
    [
        ["--fps", "nan"],
        ["--gain", "0"],
        ["--exposures-us", "0,500"],
        ["--exposures-us", "3000"],
        ["--exposures-us", "bad"],
        ["--roi", "0,0,7,4"],
        ["--fit-captures", "1"],
        ["--max-saturation-fraction", "1"],
    ],
)
def test_bad_recording_plan_is_rejected_before_hardware(tmp_path, extra):
    with pytest.raises(SystemExit) as error:
        args_for(tmp_path / "untouched", *extra)
    assert error.value.code == 2
    assert not (tmp_path / "untouched").exists()


def test_dry_run_never_opens_hardware_or_creates_files(tmp_path):
    output = tmp_path / "dry"
    assert (
        led_recording.record_main(
            [
                "--output-dir",
                str(output),
                "--roi",
                "0,0,6,4",
                "--lens-label",
                "SIMULATED",
                "--dry-run",
            ]
        )
        == 0
    )
    assert not output.exists()


def test_gate_journal_is_quiescent_before_its_file_closes():
    notes = []
    runtime = SimpleNamespace(notify_gate_event=lambda _event: True)
    finish = led_recording._journal_gate_events(
        runtime, lambda *args, **fields: notes.append((args, fields))
    )
    event = GateEvent.observe(None, 123.0)
    assert runtime.notify_gate_event(event)
    finish()
    assert runtime.notify_gate_event(event) is False
    assert len(notes) == 1


def test_unlisted_accepted_trigger_prevents_a_complete_candidate(tmp_path):
    manifest = dataset(tmp_path)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["unlisted_accepted_trigger_count"] = 1
    manifest.write_text(json.dumps(data), encoding="utf-8")
    report = analyze_manifest(manifest)
    assert report["reason"] == "recording_has_unlisted_captures"
    assert report["candidates"] == {}
