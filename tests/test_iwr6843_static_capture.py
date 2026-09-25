"""Raw-first static IWR6843 setup capture tests."""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import replace

import numpy as np
import pytest

from openflight.iwr6843.driver import IWR6843DumpRecoveryError
from openflight.iwr6843.dump import pack_dump
from openflight.iwr6843.static_capture import (
    StaticCaptureInputs,
    capture_static_range,
)


class FakeRadar:
    def __init__(
        self,
        raw: bytes,
        *,
        send_error: Exception | None = None,
        read_error: Exception | None = None,
        health_error: Exception | None = None,
    ):
        self.raw = raw
        self.send_error = send_error
        self.read_error = read_error
        self.health_error = health_error
        self.config_bytes = None
        self.calls = []

    def send_config(self, path: str) -> None:
        self.calls.append("send_config")
        with open(path, "rb") as handle:
            self.config_bytes = handle.read()
        if self.send_error is not None:
            raise self.send_error

    def read_dump(self) -> bytes:
        self.calls.append("read_dump")
        if self.read_error is not None:
            raise self.read_error
        return self.raw

    def verify_post_dump_cli(self) -> None:
        self.calls.append("verify_post_dump_cli")
        if self.health_error is not None:
            raise self.health_error

    def stop_sensor(self) -> None:
        self.calls.append("stop_sensor")

    def close(self) -> None:
        self.calls.append("close")


def _raw_dump() -> bytes:
    cube = np.ones((3, 4, 4, 128), dtype=complex)
    return pack_dump(cube, n_tx=2, version=3, frame_period_us=6000)


def _inputs(tmp_path, *, capture_id="empty-001") -> StaticCaptureInputs:
    sources = tmp_path / "sources"
    sources.mkdir()
    config = sources / "radar.cfg"
    firmware = sources / "firmware.bin"
    rig = sources / "rig.json"
    calibration = sources / "calibration.json"
    config.write_bytes(b"profileCfg exact bytes\nsensorStart\n")
    firmware.write_bytes(b"exact firmware image")
    rig.write_bytes(b'{"rig":"exact"}\n')
    calibration.write_bytes(b'{"calibration":"exact"}\n')
    return StaticCaptureInputs(
        capture_id=capture_id,
        capture_kind="empty",
        output_dir=tmp_path / "output",
        config_path=config,
        firmware_path=firmware,
        rig_geometry_path=rig,
        calibration_path=calibration,
        port="/dev/test-iwr",
        settle_s=0.25,
    )


def _factory(fake):
    return lambda **_kwargs: fake


def test_success_saves_raw_before_profile_and_records_exact_hashes(tmp_path, monkeypatch):
    inputs = _inputs(tmp_path)
    raw = _raw_dump()
    fake = FakeRadar(raw)
    order = []

    from openflight.iwr6843 import static_capture as module

    real_atomic = module._atomic_write_bytes
    real_profile = module.static_range_profile

    def atomic(path, payload):
        if path.suffix == ".l3dump":
            order.append("raw")
        real_atomic(path, payload)

    def profile(*args, **kwargs):
        order.append("profile")
        return real_profile(*args, **kwargs)

    monkeypatch.setattr(module, "_atomic_write_bytes", atomic)
    monkeypatch.setattr(module, "static_range_profile", profile)

    result = capture_static_range(
        inputs,
        radar_factory=_factory(fake),
        wait_for_settle=lambda *_args: False,
    )

    assert result["status"] == "usable"
    assert result["usable"] is True
    assert result["radar_profile_qualified"] is False
    assert result["profile"]["radar_profile_qualified"] is False
    assert order == ["raw", "profile"]
    assert fake.config_bytes == inputs.config_path.read_bytes()
    assert fake.calls == [
        "send_config",
        "read_dump",
        "verify_post_dump_cli",
        "stop_sensor",
        "close",
    ]
    assert result["inputs"] == {
        "firmware": {
            "path": str(inputs.firmware_path.resolve()),
            "sha256": hashlib.sha256(inputs.firmware_path.read_bytes()).hexdigest(),
        },
        "radar_config": {
            "path": str(inputs.config_path.resolve()),
            "sha256": hashlib.sha256(inputs.config_path.read_bytes()).hexdigest(),
        },
        "rig_geometry": {
            "path": str(inputs.rig_geometry_path.resolve()),
            "sha256": hashlib.sha256(inputs.rig_geometry_path.read_bytes()).hexdigest(),
        },
        "calibration": {
            "path": str(inputs.calibration_path.resolve()),
            "sha256": hashlib.sha256(inputs.calibration_path.read_bytes()).hexdigest(),
        },
    }
    assert result["profile"]["capture_sha256"] == hashlib.sha256(raw).hexdigest()
    assert result["raw_evidence_sha256"] == hashlib.sha256(raw).hexdigest()
    assert result["profile"]["radar_profile_sha256"] == result["inputs"]["radar_config"]["sha256"]
    assert result["profile"]["rig_geometry_sha256"] == result["inputs"]["rig_geometry"]["sha256"]
    saved = json.loads((inputs.output_dir / "empty-001.json").read_text(encoding="utf-8"))
    assert saved == result


def test_repeated_static_captures_verify_and_stop_each_serial_lifecycle(tmp_path):
    first_inputs = _inputs(tmp_path, capture_id="empty-001")
    second_inputs = replace(
        first_inputs,
        capture_id="ball-002",
        capture_kind="ball_present",
    )
    radars = [FakeRadar(_raw_dump()), FakeRadar(_raw_dump())]

    first = capture_static_range(
        first_inputs,
        radar_factory=_factory(radars[0]),
        wait_for_settle=lambda *_args: False,
    )
    second = capture_static_range(
        second_inputs,
        radar_factory=_factory(radars[1]),
        wait_for_settle=lambda *_args: False,
    )

    assert first["usable"] is True
    assert second["usable"] is True
    expected = [
        "send_config",
        "read_dump",
        "verify_post_dump_cli",
        "stop_sensor",
        "close",
    ]
    assert radars[0].calls == expected
    assert radars[1].calls == expected


def test_concurrent_same_id_is_refused_before_second_hardware_owner(tmp_path):
    inputs = _inputs(tmp_path)
    entered = threading.Event()
    release = threading.Event()
    factory_calls = []
    outcomes = []

    def factory(**_kwargs):
        factory_calls.append(1)
        return FakeRadar(_raw_dump())

    def wait_for_settle(_cancel, _seconds):
        entered.set()
        assert release.wait(3)
        return False

    def first_capture():
        outcomes.append(
            capture_static_range(
                inputs,
                radar_factory=factory,
                wait_for_settle=wait_for_settle,
            )
        )

    worker = threading.Thread(target=first_capture)
    worker.start()
    assert entered.wait(3)
    try:
        with pytest.raises(FileExistsError, match="reserved"):
            capture_static_range(
                inputs,
                radar_factory=factory,
                wait_for_settle=lambda *_args: False,
            )
    finally:
        release.set()
        worker.join(3)

    assert not worker.is_alive()
    assert len(factory_calls) == 1
    assert outcomes[0]["usable"] is True
    assert (inputs.output_dir / "empty-001.l3dump").read_bytes() == _raw_dump()
    assert not list(inputs.output_dir.glob("*.reserve"))


def test_existing_partial_raw_is_never_replaced_or_sent_to_hardware(tmp_path):
    inputs = _inputs(tmp_path)
    inputs.output_dir.mkdir(parents=True)
    raw_path = inputs.output_dir / "empty-001.l3dump"
    raw_path.write_bytes(b"preserved-partial-evidence")
    factory_calls = []

    with pytest.raises(FileExistsError, match="output already exists"):
        capture_static_range(
            inputs,
            radar_factory=lambda **_kwargs: factory_calls.append(1),
            wait_for_settle=lambda *_args: False,
        )

    assert raw_path.read_bytes() == b"preserved-partial-evidence"
    assert factory_calls == []
    assert not list(inputs.output_dir.glob("*.reserve"))


def test_partial_dump_is_preserved_and_marked_unusable(tmp_path):
    inputs = _inputs(tmp_path)
    partial = b"ILD1-partial-dump"
    fake = FakeRadar(partial)

    result = capture_static_range(
        inputs,
        radar_factory=_factory(fake),
        wait_for_settle=lambda *_args: False,
    )

    assert result["status"] == "error"
    assert result["usable"] is False
    assert result["error"]["stage"] == "derive_profile"
    assert (inputs.output_dir / "empty-001.l3dump").read_bytes() == partial
    assert result["artifacts"]["raw"]["sha256"] == hashlib.sha256(partial).hexdigest()
    assert fake.calls == [
        "send_config",
        "read_dump",
        "verify_post_dump_cli",
        "stop_sensor",
        "close",
    ]


def test_wedged_post_dump_cli_preserves_raw_and_marks_capture_unusable(tmp_path):
    inputs = _inputs(tmp_path)
    raw = _raw_dump()
    fake = FakeRadar(raw, health_error=RuntimeError("CLI wedged"))

    result = capture_static_range(
        inputs,
        radar_factory=_factory(fake),
        wait_for_settle=lambda *_args: False,
    )

    assert result["usable"] is False
    assert result["error"] == {
        "stage": "post_dump_cli_health",
        "type": "RuntimeError",
        "message": "CLI wedged",
    }
    assert (inputs.output_dir / "empty-001.l3dump").read_bytes() == raw
    assert result["raw_evidence_sha256"] == hashlib.sha256(raw).hexdigest()
    assert fake.calls == ["send_config", "read_dump", "verify_post_dump_cli", "close"]


def test_dump_recovery_failure_carries_and_preserves_complete_raw(tmp_path):
    inputs = _inputs(tmp_path)
    raw = _raw_dump()
    failure = IWR6843DumpRecoveryError("firmware did not return to its CLI", raw)
    fake = FakeRadar(raw, read_error=failure)

    result = capture_static_range(
        inputs,
        radar_factory=_factory(fake),
        wait_for_settle=lambda *_args: False,
    )

    assert result["usable"] is False
    assert result["error"]["stage"] == "post_dump_cli_health"
    assert (inputs.output_dir / "empty-001.l3dump").read_bytes() == raw
    assert fake.calls == ["send_config", "read_dump", "close"]


def test_configuration_error_writes_unusable_result_and_closes(tmp_path):
    inputs = _inputs(tmp_path)
    fake = FakeRadar(_raw_dump(), send_error=RuntimeError("configuration rejected"))

    result = capture_static_range(
        inputs,
        radar_factory=_factory(fake),
        wait_for_settle=lambda *_args: False,
    )

    assert result["usable"] is False
    assert result["error"] == {
        "stage": "configure",
        "type": "RuntimeError",
        "message": "configuration rejected",
    }
    assert not (inputs.output_dir / "empty-001.l3dump").exists()
    assert fake.calls == ["send_config", "stop_sensor", "close"]


def test_cancel_during_settle_records_result_and_stops_sensor(tmp_path):
    inputs = _inputs(tmp_path)
    fake = FakeRadar(_raw_dump())
    cancel = threading.Event()

    def cancel_wait(_event, _seconds):
        cancel.set()
        return True

    result = capture_static_range(
        inputs,
        radar_factory=_factory(fake),
        cancel_event=cancel,
        wait_for_settle=cancel_wait,
    )

    assert result["status"] == "cancelled"
    assert result["usable"] is False
    assert result["error"]["stage"] == "settle"
    assert fake.calls == ["send_config", "stop_sensor", "close"]


def test_cleanup_failure_is_recorded_and_capture_is_unusable(tmp_path):
    inputs = _inputs(tmp_path)

    class StopFailureRadar(FakeRadar):
        def stop_sensor(self):
            self.calls.append("stop_sensor")
            raise RuntimeError("stop failed")

    fake = StopFailureRadar(_raw_dump())
    result = capture_static_range(
        inputs,
        radar_factory=_factory(fake),
        wait_for_settle=lambda *_args: False,
    )

    assert result["usable"] is False
    assert result["status"] == "error"
    assert result["cleanup_errors"] == [
        {"operation": "stop_sensor", "type": "RuntimeError", "message": "stop failed"}
    ]
    assert fake.calls[-2:] == ["stop_sensor", "close"]
    assert result["profile"]["capture_sha256"] == hashlib.sha256(_raw_dump()).hexdigest()


def test_rejects_capture_id_path_traversal_before_creating_output(tmp_path):
    inputs = _inputs(tmp_path, capture_id="../escape")

    try:
        capture_static_range(inputs, radar_factory=_factory(FakeRadar(_raw_dump())))
    except ValueError as error:
        assert "capture_id" in str(error)
    else:  # pragma: no cover - assertion spelling for a clear failure
        raise AssertionError("path traversal capture_id was accepted")

    assert not inputs.output_dir.exists()
