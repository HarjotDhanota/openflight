"""Fake-only diagnostic transport and runner regressions."""

import base64
import json
import struct
import sys
import zlib
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ball_lock_inspection as inspection

BUILD = "a" * 64


def packet(calls=0, bins=1):
    counts = [calls] if calls < 24 else [24, calls % 24]
    retained = sum(counts)
    meta = [
        165,
        24,
        64,
        calls,
        2,
        1,
        1,
        1,
        calls - retained,
        0,
        0,
        1,
        len(counts),
        calls - retained + 1,
        calls,
        0,
        0,
        0,
        0,
        0,
    ]
    meta += list(struct.unpack("<16I", BUILD.encode())) + [0, 123, 200000000, 3000]
    words = meta[:]
    ordinal = calls - retained + 1
    for count in counts:
        words += [2, ordinal, count] + [0] * 165
        for _ in range(count):
            words += [ordinal, ordinal * 8, ordinal * 4800000, 20, bins, 0, 0, 0] + [0] * bins
            ordinal += 1
    words += [0] * 165
    payload = struct.pack(f"<{len(words)}I", *words)
    return (
        struct.pack("<8sIII", b"OFBLTR01", 1, len(words), zlib.crc32(payload))
        + payload
        + b"Done\nDone\nl3dump:/>"
    )


class Port:
    def __init__(self, raw, chunk=7, failure=None, count=14):
        self.response, self.raw = raw, b""
        self.chunk, self.failure, self.count = chunk, failure, count
        self.writes = []

    @property
    def in_waiting(self):
        return len(self.raw)

    def write(self, data):
        self.writes.append(data)
        self.raw = self.response
        return self.count

    def read(self, count):
        if self.failure:
            raise self.failure
        data, self.raw = self.raw[: min(count, self.chunk)], self.raw[min(count, self.chunk) :]
        return data


def owner(raw, **kwargs):
    tick = iter(range(0, 100000000000, 1000000))
    return SimpleNamespace(
        port=Port(raw, **kwargs),
        now=lambda: next(tick),
        failed=False,
        context=SimpleNamespace(boot=123, tick_hz=200000000, build=BUILD),
        build=BUILD,
        fresh_runtime=True,
        connection="connection",
        host_boot="host",
        generation=1,
        rows=[
            {"command": "sensorStart", "cli_ready_ns": 1, "error": None},
            {"command": "ball cfg 1 1", "cli_ready_ns": 2, "error": None},
        ],
        profile=("frameCfg 0 2 12 20000 3 1 0",),
        state={"rf_start_attempted": True, "stop_acknowledged": True, "stop_verified": True},
    )


@pytest.fixture
def binding(monkeypatch):
    value = {"source_fingerprint": BUILD, "manifest_sha256": "m", "ram_sha256": "r"}
    monkeypatch.setattr(inspection, "eligible_binding", lambda b: b == value)
    return value


@pytest.mark.parametrize("chunk", [1, 19, 20, 21, 4096])
def test_split_exact_reply(tmp_path, binding, chunk):
    raw = packet()
    collector = owner(raw, chunk=chunk)
    result = inspection.capture_stopped_evidence(
        collector, binding, tmp_path / "wire", session_id="s", attempt_id="1"
    )
    assert result["session_bound"] and not result["isolated_replay_verified"]
    assert (tmp_path / "wire/reply.bin").read_bytes() == raw
    assert collector.port.writes == [b"ball evidence\n"]
    assert collector.rows[-1]["written_bytes"] == 14


@pytest.mark.parametrize(
    "raw,failure,count",
    [
        (b"OF", None, 14),
        (packet()[:30], None, 14),
        (packet()[:-1], None, 14),
        (packet(), OSError("read"), 14),
        (packet(), KeyboardInterrupt(), 14),
        (packet(), None, 1),
        (b"X" * 20, None, 14),
    ],
)
def test_ambiguous_io_retained_and_poisoned(tmp_path, binding, raw, failure, count):
    collector = owner(raw, failure=failure, count=count)
    with pytest.raises(BaseException):
        inspection.capture_stopped_evidence(
            collector, binding, tmp_path / "wire", session_id="s", attempt_id="1"
        )
    assert collector.failed
    row = json.loads((tmp_path / "wire/exchange.json").read_text())
    assert (
        row["error"]
        and base64.b64decode(row["received_b64"]) == (tmp_path / "wire/reply.bin").read_bytes()
    )
    with pytest.raises(ValueError):
        inspection.capture_stopped_evidence(
            collector, binding, tmp_path / "again", session_id="s", attempt_id="2"
        )
    assert len(collector.port.writes) == 1


def test_ineligible_before_write(tmp_path):
    collector = owner(packet())
    with pytest.raises(ValueError):
        inspection.capture_stopped_evidence(
            collector, {}, tmp_path / "wire", session_id="s", attempt_id="1"
        )
    assert not collector.port.writes


def mutate(raw, index, value):
    raw = bytearray(raw)
    struct.pack_into("<I", raw, 20 + index * 4, value)
    words = struct.unpack_from("<I", raw, 12)[0]
    struct.pack_into("<I", raw, 16, zlib.crc32(raw[20 : 20 + words * 4]))
    return bytes(raw)


@pytest.mark.parametrize(
    "index,value",
    [(37, 124), (37, 0), (38, 199999999), (39, 4000), (5, 2), (6, 2), (20, 0x62626262)],
)
def test_wrong_runtime_identity_rate_epochs_build(tmp_path, binding, index, value):
    collector = owner(mutate(packet(), index, value))
    with pytest.raises(ValueError):
        inspection.capture_stopped_evidence(
            collector, binding, tmp_path / "wire", session_id="s", attempt_id="1"
        )
    assert collector.failed and (tmp_path / "wire/reply.bin").exists()


@pytest.mark.parametrize("suffix", [b"extra", b"Done\n", b"\r\n"])
def test_trailing_bytes_poison_and_retain(tmp_path, binding, suffix):
    collector = owner(packet() + suffix)
    with pytest.raises(ValueError):
        inspection.capture_stopped_evidence(
            collector, binding, tmp_path / "wire", session_id="s", attempt_id="1"
        )
    assert collector.failed
    assert (tmp_path / "wire/reply.bin").read_bytes().startswith(packet())


def test_maximum_packet_and_binary_console_text(tmp_path, binding):
    raw = packet(47, 64)
    assert len(raw) == 15739
    # Actual input words remain positive finite floats despite console-like bytes.
    first_power_word = 40 + 3 + 165 + 8
    for offset in range(0, 16, 4):
        raw = mutate(
            raw,
            first_power_word + offset // 4,
            struct.unpack("<I", b"Error\nl3dump:/>\nX"[offset : offset + 4])[0],
        )
    collector = owner(raw)
    result = inspection.capture_stopped_evidence(
        collector, binding, tmp_path / "wire", session_id="s", attempt_id="1"
    )
    assert result["retained_interval"]["retained_calls"] == 47


def test_repeated_directory_cannot_overwrite(tmp_path, binding):
    collector = owner(packet())
    inspection.capture_stopped_evidence(
        collector, binding, tmp_path / "wire", session_id="s", attempt_id="1"
    )
    with pytest.raises(FileExistsError):
        inspection.capture_stopped_evidence(
            collector, binding, tmp_path / "wire", session_id="s", attempt_id="1"
        )
    assert len(collector.port.writes) == 1


@pytest.mark.parametrize("reason", ["none", "gone"])
def test_real_runner_stops_at_first_observed_lock_before_arm(tmp_path, monkeypatch, reason):
    import tester_sync_session as session
    from test_joint_shot_clock import wire

    collector, port = wire(1)
    port.ball_reason = reason
    collector.diagnostic_binding = {"source_fingerprint": collector.context.build}
    monkeypatch.setattr(inspection, "eligible_binding", lambda _binding: True)
    files = session.SessionFiles(tmp_path / "session")
    captured = []

    def capture(current, _binding, _output, **identity):
        assert current is collector
        assert current.state["stop_acknowledged"] and current.state["stop_verified"]
        captured.append(identity)
        return {"session_bound": True}

    monkeypatch.setattr(inspection, "capture_stopped_evidence", capture)
    result = session.inspect_ball_lock(collector, files, files.folder / "attempt-001")
    commands = [row["command"] for row in collector.rows]
    assert commands.count("sensorStop") == 1
    assert not any(
        command.startswith("triggerCfg ") and command.endswith(" 1") for command in commands
    )
    assert "ball status" not in commands
    assert captured and result["outcome"] == "host_observed_lock"
    assert result["first_locked_state"]["ball"]["reason"] == reason
    assert result["first_locked_stats_received_b64"]
    assert not result["captured_shot"] and (files.folder / "attempt-001/RESULT.json").exists()
    assert result["name"] == "attempt-001"
    assert result["status"] == "diagnostic_saved_unqualified"


def test_control_exception_retains_identity_and_exports_after_stop(tmp_path, monkeypatch):
    import tester_sync_session as session
    from test_joint_shot_clock import wire

    collector, _port = wire()
    collector.diagnostic_binding = {"source_fingerprint": collector.context.build}
    monkeypatch.setattr(inspection, "eligible_binding", lambda _binding: True)
    files = session.SessionFiles(tmp_path / "session")
    error = KeyboardInterrupt("operator")
    monkeypatch.setattr(collector, "wait_ball", lambda *_args: (_ for _ in ()).throw(error))
    captures = []
    monkeypatch.setattr(
        inspection,
        "capture_stopped_evidence",
        lambda *args, **kwargs: captures.append(args[0].state.copy()),
    )
    with pytest.raises(KeyboardInterrupt) as caught:
        session.inspect_ball_lock(collector, files, files.folder / "attempt-001")
    assert caught.value is error
    assert captures[0]["stop_verified"]
    assert json.loads((files.folder / "attempt-001/RESULT.json").read_text())["error"]


@pytest.mark.parametrize("mode", ["timeout", "stop"])
def test_timeout_and_cooperative_stop_export_known_framing(tmp_path, monkeypatch, mode):
    import tester_sync_session as session
    from test_joint_shot_clock import wire

    collector, _port = wire()
    collector.diagnostic_binding = {"source_fingerprint": collector.context.build}
    monkeypatch.setattr(inspection, "eligible_binding", lambda _binding: True)
    error = TimeoutError() if mode == "timeout" else session.AttemptStopped()
    monkeypatch.setattr(collector, "wait_ball", lambda *_args: (_ for _ in ()).throw(error))
    captures = []
    monkeypatch.setattr(
        inspection,
        "capture_stopped_evidence",
        lambda *args, **kwargs: captures.append(args[0].state.copy()),
    )
    files = session.SessionFiles(tmp_path / "session")
    result = session.inspect_ball_lock(collector, files, files.folder / "attempt-001")
    assert captures[0]["stop_verified"]
    assert result["outcome"] == ("timeout" if mode == "timeout" else "operator_stop")


def test_direct_inspection_refuses_old_owner_before_rf(tmp_path):
    import tester_sync_session as session
    from test_joint_shot_clock import wire

    collector, port = wire()
    before = list(port.writes)
    with pytest.raises(ValueError, match="explicitly selected"):
        session.inspect_ball_lock(collector, session.SessionFiles(tmp_path), tmp_path / "attempt")
    assert port.writes == before


def test_diagnostic_and_normal_package_modes_are_disjoint(tmp_path):
    from run_joint_clock import load_package

    with pytest.raises(ValueError, match="separate modes"):
        load_package(tmp_path, diagnostic=True, shot_dumps=True)


@pytest.mark.parametrize(
    "diagnostic,package_name,expected",
    [
        (True, "OPENFLIGHT_DIAGNOSTIC_PACKAGE", 0),
        (False, "OPENFLIGHT_DIAGNOSTIC_PACKAGE", 1),
        (True, "OPENFLIGHT_CLOCK_PACKAGE", 1),
    ],
)
def test_cli_preflight_only_opted_in_matching_package(diagnostic, package_name, expected):
    import os
    import subprocess

    package_path = os.environ.get(package_name)
    if not package_path:
        pytest.skip(f"{package_name} not supplied")
    args = [
        sys.executable,
        str(Path(__file__).with_name("tester_sync_session.py")),
        "--clock-package",
        package_path,
    ]
    if diagnostic:
        args.append("--inspect-ball-lock")
    result = subprocess.run(args, capture_output=True, text=True, timeout=20, check=False)
    assert result.returncode == expected, result.stderr
    if expected == 0:
        output = json.loads(result.stdout)
        assert output["diagnostic"] and not output["devices_opened"] and not output["captured_shot"]


@pytest.mark.parametrize("condition", ["inactive", "rf_fault"])
def test_locked_stats_rejected_before_callback_remain_exact(tmp_path, monkeypatch, condition):
    import tester_sync_session as session
    from test_joint_shot_clock import wire

    collector, port = wire()
    collector.diagnostic_binding = {"source_fingerprint": collector.context.build}
    monkeypatch.setattr(inspection, "eligible_binding", lambda _binding: True)
    original = port.write

    def write(data):
        count = original(data)
        if data == b"stats\n":
            if condition == "inactive":
                port.pending = port.pending.replace(b"active=1", b"active=0")
            else:
                port.pending = port.pending.replace(b"rf_faults=0", b"rf_faults=1")
        return count

    monkeypatch.setattr(port, "write", write)
    monkeypatch.setattr(inspection, "capture_stopped_evidence", lambda *_args, **_kwargs: {})
    files = session.SessionFiles(tmp_path / "session")
    with pytest.raises(ValueError):
        session.inspect_ball_lock(collector, files, files.folder / "attempt-001")
    result = json.loads((files.folder / "attempt-001/RESULT.json").read_text())
    assert result["outcome"] == "interrupted" and "first_locked_state" not in result
    assert "ValueError" in result["error"]
    rows = json.loads((files.folder / "attempt-001/iwr-exchanges.json").read_text())
    assert any(b"ball state=locked" in base64.b64decode(row["received_b64"]) for row in rows)


def test_repeated_successful_inspections_have_new_identity_and_owned_epochs(tmp_path, monkeypatch):
    import tester_sync_session as session
    from test_joint_shot_clock import wire

    collector, _port = wire()
    collector.diagnostic_binding = {"source_fingerprint": collector.context.build}
    monkeypatch.setattr(inspection, "eligible_binding", lambda _binding: True)
    epochs = []

    def capture(current, *_args, **_kwargs):
        epochs.append(inspection.expected_epochs(current.rows))
        return {}

    monkeypatch.setattr(inspection, "capture_stopped_evidence", capture)
    files = session.SessionFiles(tmp_path / "session")
    first = session.inspect_ball_lock(collector, files, files.folder / "attempt-001")
    second = session.inspect_ball_lock(collector, files, files.folder / "attempt-002")
    assert first["attempt_id"] != second["attempt_id"]
    assert epochs == [(1, 1, 1), (2, 2, 1)]


def test_pending_bytes_refused_before_write_and_retained(tmp_path, binding):
    collector = owner(packet())
    collector.port.raw = b"leftover"
    with pytest.raises(ValueError, match="pending bytes"):
        inspection.capture_stopped_evidence(
            collector, binding, tmp_path / "wire", session_id="s", attempt_id="1"
        )
    assert collector.failed and not collector.port.writes
    assert (tmp_path / "wire/reply.bin").read_bytes() == b"leftove"


def test_failed_stop_does_not_export_or_permit_next_attempt(tmp_path, monkeypatch):
    import tester_sync_session as session
    from test_joint_shot_clock import wire

    collector, port = wire()
    collector.diagnostic_binding = {"source_fingerprint": collector.context.build}
    monkeypatch.setattr(inspection, "eligible_binding", lambda _binding: True)
    original = port.write

    def write(data):
        count = original(data)
        if data == b"sensorStop\n":
            port.pending = b"Error\nDone\nl3dump:/>"
        return count

    monkeypatch.setattr(port, "write", write)
    files = session.SessionFiles(tmp_path / "session")
    with pytest.raises(Exception):
        session.inspect_ball_lock(collector, files, files.folder / "attempt-001")
    writes = list(port.writes)
    assert collector.failed and b"ball evidence\n" not in writes
    with pytest.raises(ValueError, match="explicitly selected"):
        session.inspect_ball_lock(collector, files, files.folder / "attempt-002")
    assert port.writes == writes


@pytest.mark.parametrize(
    "kind", ["crc", "oversize", "suffix", "partial_read_error", "write_interrupt"]
)
def test_failure_forms_keep_original_bytes_and_control_exception(
    tmp_path, binding, monkeypatch, kind
):
    raw = packet()
    if kind == "crc":
        raw = raw[:16] + b"BAD!" + raw[20:]
    elif kind == "oversize":
        raw = raw[:12] + struct.pack("<I", inspection.decoder.MAX_PAYLOAD_WORDS + 1) + raw[16:]
    elif kind == "suffix":
        raw = raw[: -len(inspection.decoder.SUFFIX)] + b"Done\r\nDone\r\nl3dump:/>"
    collector = owner(raw)
    error = KeyboardInterrupt("write interrupted")
    if kind == "write_interrupt":
        monkeypatch.setattr(collector.port, "write", lambda _data: (_ for _ in ()).throw(error))
    if kind == "partial_read_error":
        original = collector.port.read
        reads = []

        def read(count):
            if len(reads) >= 5:
                raise OSError("partial read")
            reads.append(count)
            return original(count)

        monkeypatch.setattr(collector.port, "read", read)
    with pytest.raises(BaseException) as caught:
        inspection.capture_stopped_evidence(
            collector, binding, tmp_path / "wire", session_id="s", attempt_id="1"
        )
    if kind == "write_interrupt":
        assert caught.value is error
    assert collector.failed
    assert (tmp_path / "wire/exchange.json").exists()
    saved = (tmp_path / "wire/reply.bin").read_bytes()
    if kind == "partial_read_error":
        assert saved and raw.startswith(saved)
