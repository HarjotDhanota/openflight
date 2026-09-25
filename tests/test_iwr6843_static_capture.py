"""Raw-first static IWR6843 setup capture tests."""

from __future__ import annotations

import hashlib
import json
import threading

import numpy as np

from openflight.iwr6843.dump import pack_dump
from openflight.iwr6843.static_capture import (
    StaticCaptureInputs,
    capture_static_range,
)


class FakeRadar:
    def __init__(self, raw: bytes, *, send_error: Exception | None = None):
        self.raw = raw
        self.send_error = send_error
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
        return self.raw

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
    assert fake.calls == ["send_config", "read_dump", "stop_sensor", "close"]
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
    assert result["profile"]["radar_profile_sha256"] == result["inputs"]["radar_config"]["sha256"]
    assert result["profile"]["rig_geometry_sha256"] == result["inputs"]["rig_geometry"]["sha256"]
    saved = json.loads((inputs.output_dir / "empty-001.json").read_text(encoding="utf-8"))
    assert saved == result


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
    assert fake.calls == ["send_config", "read_dump", "stop_sensor", "close"]


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
