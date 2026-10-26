"""Research-only capture/probe provenance; never qualified RF acquisition time."""

from __future__ import annotations

import hashlib
import secrets
import time
import uuid
from fractions import Fraction

from openflight.iwr6843.clock_evidence import HALF, _hex, _rate_bounds, _uint, map_tick, parse_probe
from openflight.iwr6843.compact_clock import (
    SCHEMA as COMPACT_SCHEMA,
    CompactExchangeError,
    compact_exchange,
    compact_recording_observations,
)
from openflight.iwr6843.dump import declared_dump_nbytes, parse_dump
from openflight.iwr6843.frame_evidence import frame_timing_report, tick_delta
from openflight.iwr6843.uart_clock_bounds import map_compact_uart_tick
from openflight.ops_capture_evidence import boot_identity

SCHEMA = "openflight.iwr-capture-clock.v1"


def _payload(raw):
    if not isinstance(raw, bytes) or not raw:
        raise ValueError("nonempty original payload bytes required")
    return {"payload_sha256": hashlib.sha256(raw).hexdigest(), "payload_bytes": len(raw)}


def record_capture_clock(
    radar,
    acquire,
    emit,
    save_payload,
    *,
    now=time.monotonic_ns,
    nonce=lambda: secrets.token_hex(8),
    boot=boot_identity,
):
    """Use one exclusively owned radar; acquire must return bytes after CLI recovery.

    The callback receives that same radar. It must not reopen UART or run another
    serial owner. Save raw data even if subsequent identity checks fail. This
    function does not configure/start/stop RF; the supervised caller owns that.
    """
    host = boot()
    if not isinstance(host, str) or not host:
        raise ValueError("host boot identity unavailable")
    connection = uuid.uuid4().hex
    emit(
        {
            "type": "start",
            "schema": SCHEMA,
            "event_clock": "host_monotonic_ns",
            "boot_id": host,
            "connection_id": connection,
            "capture_id": uuid.uuid4().hex,
            "requested_count": 2,
            "mono_ns": now(),
        }
    )
    seen, failure, attempted = set(), None, 0

    def fresh():
        value = _hex(nonce(), 16)
        if value in seen:
            raise ValueError("reused request nonce")
        seen.add(value)
        return value

    def context(kind, seq):
        request = fresh()
        before = now()
        reply = radar.cmd(f"clockProbe {request[:8]} {request[8:]} {seq:08x}", 2.0)
        emit(
            {
                "type": kind,
                "request": request,
                "seq": seq,
                "before_ns": before,
                "after_ns": now(),
                "reply_text": reply,
            }
        )
        return parse_probe(reply, request, seq)

    def probe(seq, initial):
        nonlocal attempted
        attempted += 1
        try:
            value = compact_exchange(
                radar, fresh(), seq, build=initial.build, boot=initial.boot, now=now
            )
        except CompactExchangeError as error:
            emit(error.evidence)
            raise
        emit(value)

    try:
        initial = context("preflight", 0)
        probe(1, initial)
        before = now()
        emit({"type": "capture_begin", "mono_ns": before})
        try:
            raw = acquire(radar)
        except BaseException as error:
            partial = getattr(error, "raw", None)
            if isinstance(partial, bytes) and partial:
                save_payload(partial)
                emit(
                    {
                        "type": "capture_failed",
                        "before_ns": before,
                        "after_ns": now(),
                        "error": type(error).__name__,
                        **_payload(partial),
                    }
                )
            raise
        after = now()
        description = _payload(raw)
        save_payload(raw)
        emit({"type": "capture", "before_ns": before, "after_ns": after, **description})
        probe(2, initial)
        final = context("postflight", 3)
        if (initial.build, initial.boot, initial.tick_hz) != (
            final.build,
            final.boot,
            final.tick_hz,
        ):
            raise ValueError("runtime identity changed across capture")
    except BaseException as error:
        failure = type(error).__name__
        raise
    finally:
        emit(
            {
                "type": "end",
                "boot_id": boot(),
                "connection_id": connection,
                "probe_count": attempted,
                "mono_ns": now(),
                "error": failure,
            }
        )


def _observations(records, raw):
    expected = [
        "start",
        "preflight",
        "compact",
        "capture_begin",
        "capture",
        "compact",
        "postflight",
        "end",
    ]
    if (
        not isinstance(records, list)
        or len(records) != len(expected)
        or not all(isinstance(row, dict) for row in records)
        or [r.get("type") for r in records] != expected
    ):
        raise ValueError("incomplete or reordered capture recording")
    start, capture = records[0], records[4]
    if start.get("schema") != SCHEMA or start.get("requested_count") != 2:
        raise ValueError("invalid capture recording schema/count")
    _hex(start.get("capture_id"), 32)
    if any(capture.get(key) != value for key, value in _payload(raw).items()):
        raise ValueError("capture payload hash or length mismatch")
    virtual = [dict(start, schema=COMPACT_SCHEMA), *records[1:3], *records[5:]]
    observations = compact_recording_observations(virtual)
    begin = _uint(records[3].get("mono_ns"), 63)
    before = _uint(capture.get("before_ns"), 63)
    after = _uint(capture.get("after_ns"), 63)
    if not observations[0].cli_after_ns <= begin == before <= after <= observations[1].before_ns:
        raise ValueError("capture host ordering does not lie between probes")
    return observations


def _capture_metadata(raw):
    if declared_dump_nbytes(raw) != len(raw):
        raise ValueError("capture is incomplete or has extra bytes")
    meta, _cube = parse_dump(raw)
    timing = frame_timing_report(meta)
    if timing["status"] != "continuous" or timing["request"]["status"] != "consistent":
        raise ValueError("capture has missing, discontinuous or inconsistent frame evidence")
    return meta


def _bracket(meta, observations, rates, max_distance_ns):
    first, last = observations
    span = last.after_ns - first.before_ns
    if span >= Fraction(HALF * 10**9, rates[1]):
        raise ValueError("host span permits an ambiguous half-wrap")
    if span > max_distance_ns:
        raise ValueError("host span exceeds declared target proximity")
    capture, frames = meta["capture_evidence"], meta["frame_evidence"]
    if capture["tick_hz"] != first.reply.tick_hz:
        raise ValueError("capture/probe configured counter rate mismatch")
    sequence_span = tick_delta(last.reply.frame_seq, first.reply.frame_seq)
    tick_span = tick_delta(last.reply.rx_tick, first.reply.tx_tick)
    if (
        sequence_span >= HALF
        or tick_span >= HALF
        or tick_span > Fraction(span * rates[1], 10**9) + 1
    ):
        raise ValueError("capture probe sequence/counter span is inconsistent")
    for frame in frames:
        seq = tick_delta(frame["seq"], first.reply.frame_seq)
        if not 0 < seq <= sequence_span:
            raise ValueError("capture frame sequence lies outside probe bracket")
        start = tick_delta(frame["start_tick"], first.reply.tx_tick)
        done = tick_delta(frame["done_tick"], first.reply.tx_tick)
        if not 0 < start <= done <= tick_span:
            raise ValueError("capture frame ticks lie outside probe bracket")
    request_seq = tick_delta(capture["request_seq"], first.reply.frame_seq)
    if not 0 <= request_seq <= sequence_span:
        raise ValueError("capture request sequence lies outside probe bracket")
    stamps = [capture[key] for key in ("request_tick", "freeze_tick", "stop_tick", "header_tick")]
    stamps.append(meta["dump_trailer"]["payload_end_tick"])
    if any(not 0 < tick_delta(tick, first.reply.tx_tick) <= tick_span for tick in stamps):
        raise ValueError("capture lifecycle ticks lie outside probe bracket")
    return span


def audit_capture_clock(records, raw, *, rate_hz_bounds, max_distance_ns, uart_assumptions=None):
    """Bind exact original full-dump bytes to conditional start/done counter intervals.

    Matching pre/post identities and ordering conditionally associate this capture;
    no embedded runtime token, counter fault detection or physical timing truth is
    invented. Host provenance still relies on the reviewed single-owner collector.
    """
    result = {
        "schema": "openflight.iwr-capture-clock-audit.v1",
        "status": "unavailable",
        "physical_timing_qualified": False,
        "wire_timing_qualified": False,
        "rf_acquisition_interval_ns": None,
        "frames": [],
        "reason": None,
    }
    try:
        rates = _rate_bounds(rate_hz_bounds)
        observations = _observations(records, raw)
        meta = _capture_metadata(raw)
        span = _bracket(meta, observations, rates, _uint(max_distance_ns, 63))
        epoch = (observations[0].reply.build, observations[0].reply.boot)

        def mapped(tick):
            kwargs = {
                "target_epoch": epoch,
                "rate_hz_bounds": rate_hz_bounds,
                "max_distance_ns": max_distance_ns,
            }
            if uart_assumptions is not None:
                return map_compact_uart_tick(
                    observations, tick, assumptions=uart_assumptions, **kwargs
                )
            return map_tick(observations, tick, **kwargs)

        frames = [
            {
                "index": i,
                "seq": frame["seq"],
                "start_tick": frame["start_tick"],
                "done_tick": frame["done_tick"],
                "start_counter": mapped(frame["start_tick"]),
                "done_counter": mapped(frame["done_tick"]),
            }
            for i, frame in enumerate(meta["frame_evidence"])
        ]
        result.update(
            status="conditional_capture_counter_link",
            capture_id=records[0]["capture_id"],
            **_payload(raw),
            target_epoch_declared=list(epoch),
            host_boot=observations[0].host_boot,
            connection=observations[0].connection,
            observed_host_bracket_ns=span,
            frames=frames,
            association_basis="single_owner_pre_post_identity_and_frame_order",
            embedded_capture_runtime_token=False,
        )
    except (ValueError, TypeError, KeyError, IndexError) as error:
        result.update(reason=str(error))
    return result
