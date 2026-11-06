"""Stopped diagnostic export through the existing session's exclusive UART owner.

Decoder is promoted unchanged from research/detector-evidence-2026-11-06/
firmware/host/decoder.py (SHA256 577c8950e8249088e2f10b9622158e9d9eb1551e69f9b61ab76c39717d39b46e).
Transport journaling follows the sealed usb_collector/ActiveCollector pattern;
length framing is deliberately separate from their text command readers.
"""

import base64
import hashlib
import json
from copy import deepcopy

import ball_evidence_decoder as decoder
from run_joint_clock import DIAGNOSTIC_PACKAGES


def eligible_binding(binding):
    identity = DIAGNOSTIC_PACKAGES.get(binding.get("manifest_sha256"))
    return identity is not None and identity == (
        binding.get("source_fingerprint"),
        binding.get("ram_sha256"),
    )


def expected_epochs(rows):
    config = rf = origin = 0
    for row in rows:
        command = row.get("command", "")
        if command.startswith("ball cfg ") or command == "sensorStart":
            if row.get("error") is not None or not isinstance(row.get("cli_ready_ns"), int):
                raise ValueError("unacknowledged recorder context mutation")
            if command.startswith("ball cfg "):
                config += 1
                origin = 1
            else:
                rf += 1
                origin = 2
    if not config or not rf:
        raise ValueError("owned configuration and RF history required")
    return config, rf, origin


def capture_stopped_evidence(
    collector, binding, output, *, session_id, attempt_id, session_path=None
):
    """One bounded canonical reply; no retry, recovery writes or serial changes."""
    if (
        not eligible_binding(binding)
        or collector.build != binding.get("source_fingerprint")
        or getattr(collector.context, "build", None) != collector.build
        or not collector.fresh_runtime
    ):
        raise ValueError("eligible opted-in diagnostic runtime package required")
    if (
        collector.failed
        or collector.context is None
        or not all(
            collector.state.get(k)
            for k in ("rf_start_attempted", "stop_acknowledged", "stop_verified")
        )
    ):
        raise ValueError("known framing and acknowledged verified stop required")
    boot = collector.context.boot
    boot = int(boot, 16) if isinstance(boot, str) else boot
    if not boot or collector.context.tick_hz != 200_000_000:
        raise ValueError("nonzero owned runtime identity and source-declared tick rate required")
    config, rf, origin = expected_epochs(collector.rows)
    frames = [line.split() for line in collector.profile if line.startswith("frameCfg ")]
    if len(frames) != 1:
        raise ValueError("one owned frame profile required")
    frame_us = int(float(frames[0][5]) * 1000)
    output.mkdir(parents=True, exist_ok=False)
    raw = bytearray()
    row = dict(
        command="ball evidence",
        diagnostic=True,
        session_id=session_id,
        session_path=session_path,
        attempt_id=attempt_id,
        before_ns=collector.now(),
        reads=[],
        error=None,
        written_bytes=None,
        connection=collector.connection,
        host_boot=collector.host_boot,
        runtime=collector.context.boot,
        binding=deepcopy(binding),
        expected_config_epoch=config,
        expected_rf_start_epoch=rf,
        generation=collector.generation,
    )
    collector.rows.append(row)
    deadline = row["before_ns"] + 10_000_000_000

    def read_to(size):
        while len(raw) < size:
            if collector.now() >= deadline:
                raise TimeoutError("ball evidence deadline exceeded")
            data = collector.port.read(size - len(raw))
            raw.extend(data)
            stamp = collector.now()
            row["reads"].append(
                dict(received_ns=stamp, bytes_total=len(raw), received_bytes=len(data))
            )
            if len(raw) > size or stamp >= deadline:
                raise ValueError("ball evidence read exceeded framing or deadline")

    try:
        pending = collector.port.in_waiting
        if pending:
            raw.extend(collector.port.read(min(pending, 256)))
            row["reads"].append(
                {
                    "received_ns": collector.now(),
                    "bytes_total": len(raw),
                    "received_bytes": len(raw),
                    "before_write": True,
                }
            )
            raise ValueError("unexpected pending bytes before ball evidence; no write permitted")
        encoded = b"ball evidence\n"
        row["written_bytes"] = collector.port.write(encoded)
        row["write_return_ns"] = collector.now()
        if type(row["written_bytes"]) is not int or row["written_bytes"] != len(encoded):
            raise ValueError("short ball evidence command write")
        read_to(decoder.HEADER.size)
        magic, version, words, _crc = decoder.HEADER.unpack(raw)
        if (
            magic != decoder.MAGIC
            or version != decoder.VERSION
            or not decoder.MIN_PAYLOAD_WORDS <= words <= decoder.MAX_PAYLOAD_WORDS
        ):
            raise ValueError("unrecognized or oversized ball evidence header")
        read_to(decoder.HEADER.size + words * 4 + len(decoder.SUFFIX))
        if collector.port.in_waiting:
            # Retain bounded unexpected trailing bytes, without searching binary text.
            extra = collector.port.read(min(collector.port.in_waiting, 256))
            raw.extend(extra)
            row["reads"].append(
                dict(received_ns=collector.now(), bytes_total=len(raw), received_bytes=len(extra))
            )
            raise ValueError("unexpected trailing bytes after ball evidence")
        decoded = decoder.decode(bytes(raw), expected_build=binding["source_fingerprint"])
        meta = decoded["metadata"]
        if (
            decoded["runtime_token"],
            meta["tick_hz"],
            meta["frame_period_us"],
            meta["config_epoch"],
            meta["rf_start_epoch"],
            meta["origin"],
        ) != (boot, collector.context.tick_hz, frame_us, config, rf, origin):
            raise ValueError("ball evidence differs from owned runtime/rate/profile/epochs")
        row["cli_ready_ns"] = collector.now()
        result = dict(
            structural_valid=True,
            session_bound=True,
            isolated_replay_verified=False,
            physical_timing_qualified=False,
            raw_sha256=hashlib.sha256(raw).hexdigest(),
            retained_interval=decoded["retained_interval"],
            starting_locked=decoded["chunks"][0]["start_state_words"][8] == 4,
            first_transition_coverage="not_independently_verified",
            whole_session_prefix=meta["overwritten_calls"] == meta["discarded_calls"] == 0,
            firmware_quiescence_gate="accepted complete export; host stop alone is insufficient",
        )
        (output / "decoded.json").write_text(json.dumps(decoded, indent=2) + "\n")
        (output / "coverage.json").write_text(json.dumps(result, indent=2) + "\n")
        return result
    except BaseException as error:
        collector.failed = True
        row["error"] = type(error).__name__
        row["error_detail"] = repr(error)
        raise
    finally:
        row["after_ns"] = collector.now()
        row["received_b64"] = base64.b64encode(raw).decode()
        (output / "reply.bin").write_bytes(raw)
        (output / "exchange.json").write_text(json.dumps(row, indent=2) + "\n")
