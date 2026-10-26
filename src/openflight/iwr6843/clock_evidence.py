"""Research-only IWR clock sidecars and conditional USB exchange envelopes.

No symmetry of transport delay is assumed. These are software-counter envelopes,
not qualified RF acquisition timestamps or a correction used by shot processing.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from fractions import Fraction

from openflight.iwr6843.frame_evidence import tick_delta

MODULUS = 1 << 32
HALF = MODULUS // 2
MAX_REPLY_BYTES = 65536


def _uint(value, bits=32):
    if type(value) is not int or not 0 <= value < 1 << bits:
        raise ValueError(f"expected unsigned {bits}-bit integer")
    return value


def _decimal(value):
    if not isinstance(value, str) or not re.fullmatch(r"0|[1-9][0-9]*", value):
        raise ValueError("invalid decimal field")
    return _uint(int(value))


def _hex(value, digits):
    if not isinstance(value, str) or not re.fullmatch(rf"[0-9a-f]{{{digits}}}", value):
        raise ValueError("invalid lowercase hex field")
    if digits == 16 and int(value, 16) == 0:
        raise ValueError("zero nonce")
    return value


def _lines(reply):
    if not isinstance(reply, str) or len(reply) > MAX_REPLY_BYTES or not reply.isascii():
        raise ValueError("invalid or oversized reply")
    lines = reply.splitlines()
    if lines.count("Done") != 1:
        raise ValueError("missing or duplicate CLI completion")
    if any("Error" in line or "not recognized" in line for line in lines):
        raise ValueError("CLI rejected command")
    before, after = lines[: lines.index("Done")], lines[lines.index("Done") + 1 :]
    if any("OFCLK" in line for line in after):
        raise ValueError("clock evidence after CLI completion")
    records = []
    for line in before:
        if "OFCLK" not in line:
            continue
        parts = line.split()
        if not parts or not parts[0].startswith("OFCLK"):
            raise ValueError("misframed clock record")
        fields = {}
        for part in parts[1:]:
            if part.count("=") != 1:
                raise ValueError("malformed clock field")
            key, value = part.split("=")
            if not key or not value or key in fields:
                raise ValueError("empty or duplicate clock field")
            fields[key] = value
        records.append((parts[0], fields))
    return records


def _fields(record, tag, expected):
    name, fields = record
    if name != tag or set(fields) != set(expected.split()):
        raise ValueError("unexpected clock record or fields")
    return fields


@dataclass(frozen=True)
class ProbeReply:
    """Counter readings inside one identified CLI command; ticks are still raw."""

    build: str
    request: str
    boot: str
    seq: int
    rx_tick: int
    tx_tick: int
    tick_hz: int
    frame_seq: int


def parse_probe(reply, request, seq):
    """Accept exactly one complete matching probe, with a distinct runtime token."""
    _hex(request, 16)
    _uint(seq)
    records = _lines(reply)
    if len(records) != 3:
        raise ValueError("expected one complete clock probe")
    head = _fields(records[0], "OFCLKPROBE1", "build request boot seq tick_hz counter_bits")
    stamp = _fields(records[1], "OFCLKSTAMP1", "rx_tick tx_tick frame_seq physical")
    end = _fields(records[2], "OFCLKPROBEEND1", "request seq")
    if (
        head["request"] != request
        or end["request"] != request
        or _decimal(head["seq"]) != seq
        or _decimal(end["seq"]) != seq
        or head["counter_bits"] != "32"
        or stamp["physical"] != "unqualified"
    ):
        raise ValueError("probe identity or qualification mismatch")
    hz = _decimal(head["tick_hz"])
    if hz == 0:
        raise ValueError("zero configured tick rate")
    return ProbeReply(
        _hex(head["build"], 64),
        request,
        _hex(head["boot"], 16),
        seq,
        _decimal(stamp["rx_tick"]),
        _decimal(stamp["tx_tick"]),
        hz,
        _decimal(stamp["frame_seq"]),
    )


def parse_gate(reply):
    """Retain a complete GPIO batch and losses, without pairing it to Pi edges."""
    records = _lines(reply)
    if len(records) < 4:
        raise ValueError("incomplete gate batch")
    head = _fields(records[0], "OFCLK1", "build nonce gpio edge tick_hz counter_bits")
    state = _fields(
        records[1],
        "OFCLKSTATE1",
        "queued dropped discarded unattached seq_hi seq_lo read_tick",
    )
    quality = _fields(records[2], "OFCLKQUAL1", "physical irq_latency loss_counters")
    end = _fields(records[-1], "OFCLKEND1", "nonce emitted")
    _hex(head["build"], 64)
    _hex(head["nonce"], 16)
    if (
        head["gpio"] != "1"
        or head["edge"] != "rising"
        or head["counter_bits"] != "32"
        or quality
        != {"physical": "unqualified", "irq_latency": "unmeasured", "loss_counters": "saturating"}
        or end["nonce"] != head["nonce"]
        or _decimal(head["tick_hz"]) == 0
    ):
        raise ValueError("gate identity or qualification mismatch")
    counters = {key: _decimal(value) for key, value in state.items()}
    count = counters["queued"]
    if count > 64 or _decimal(end["emitted"]) != count or len(records) != count + 4:
        raise ValueError("gate batch count mismatch")
    events = []
    issues = [name for name in ("dropped", "discarded", "unattached") if counters[name]]
    next_seq = (counters["seq_hi"] << 32) | counters["seq_lo"]
    for record in records[3:-1]:
        fields = _fields(record, "OFCLKE1", "seq_hi seq_lo tick frame_seq level")
        event = {key: _decimal(value) for key, value in fields.items()}
        sequence = (event["seq_hi"] << 32) | event["seq_lo"]
        if event["level"] not in (0, 1):
            raise ValueError("invalid sampled level")
        if not 0 < (next_seq - sequence) % (1 << 64) < 1 << 63:
            raise ValueError("event is outside snapshot sequence")
        if events and sequence != (events[-1]["sequence"] + 1) % (1 << 64):
            raise ValueError("gate sequence gap, duplicate or reorder")
        if event["level"] == 0 and "late_level_low" not in issues:
            issues.append("late_level_low")
        events.append({**event, "sequence": sequence})
    if events and not issues and (events[-1]["sequence"] + 1) % (1 << 64) != next_seq:
        raise ValueError("snapshot contains unreported sequence loss")
    return {
        "header": head,
        "state": counters,
        "events": events,
        "issues": issues,
        "physical_timing_qualified": False,
        "pi_edge_association": None,
    }


@dataclass(frozen=True)
class ProbeObservation:
    """One probe enclosed by host MONOTONIC reads on one declared connection."""

    reply: ProbeReply
    before_ns: int
    after_ns: int
    host_boot: str
    connection: str

    def validate(self):
        """Require a whole causal bracket, not a callback or write-return estimate."""
        _uint(self.before_ns, 63)
        _uint(self.after_ns, 63)
        for value in (
            self.reply.seq,
            self.reply.rx_tick,
            self.reply.tx_tick,
            self.reply.tick_hz,
            self.reply.frame_seq,
        ):
            _uint(value)
        _hex(self.reply.request, 16)
        _hex(self.reply.boot, 16)
        _hex(self.reply.build, 64)
        if (
            self.after_ns < self.before_ns
            or self.reply.tick_hz == 0
            or not isinstance(self.host_boot, str)
            or not self.host_boot
            or not isinstance(self.connection, str)
            or not self.connection
        ):
            raise ValueError("invalid host bracket or epoch")


def _rate_bounds(bounds):
    if not isinstance(bounds, (tuple, list)) or len(bounds) != 2:
        raise ValueError("explicit oscillator rate bounds required")
    low, high = (_uint(value) for value in bounds)
    if not 0 < low <= high:
        raise ValueError("invalid rate bounds")
    return low, high


def _duration_bounds(ticks, rates):
    values = [Fraction(ticks * 1_000_000_000, rate) for rate in rates]
    return min(values), max(values)


def probe_envelope(observation, target_tick, *, target_epoch, rate_hz_bounds, max_distance_ns):
    """Map a nearby same-epoch counter conditionally, with outward integer rounding.

    max_distance_ns declares an independently justified proximity limit. It cannot
    establish a wrap count or prove that a frame belongs to this runtime epoch.
    """
    observation.validate()
    _uint(target_tick)
    _uint(max_distance_ns, 63)
    rates = _rate_bounds(rate_hz_bounds)
    probe = observation.reply
    if target_epoch != (probe.build, probe.boot):
        raise ValueError("target runtime epoch mismatch")
    if not 0 < max_distance_ns < Fraction(HALF * 1_000_000_000, rates[1]):
        raise ValueError("proximity must be shorter than fastest half-wrap")
    process_ticks = tick_delta(probe.tx_tick, probe.rx_tick)
    if process_ticks >= HALF:
        raise ValueError("counter reversed or ambiguous during exchange")
    if _duration_bounds(process_ticks, rates)[0] > observation.after_ns - observation.before_ns:
        raise ValueError("counter duration exceeds host bracket")
    deltas = []
    for origin in (probe.rx_tick, probe.tx_tick):
        delta = tick_delta(target_tick, origin)
        if delta == HALF:
            raise ValueError("ambiguous half-wrap")
        delta = delta - MODULUS if delta > HALF else delta
        if _duration_bounds(abs(delta), rates)[1] > max_distance_ns:
            raise ValueError("target outside declared proximity")
        deltas.append(delta)
    if deltas[0] - deltas[1] != process_ticks:
        raise ValueError("ambiguous counter span")
    low = observation.before_ns + _duration_bounds(deltas[0], rates)[0]
    high = observation.after_ns + _duration_bounds(deltas[1], rates)[1]
    if low > high:
        raise ValueError("inconsistent counter envelope")
    return math.floor(low), math.ceil(high)


def map_tick(observations, target_tick, *, target_epoch, rate_hz_bounds, max_distance_ns):
    """Intersect every supplied probe; never discard inconvenient exchanges."""
    if not observations:
        raise ValueError("no clock observations")
    host = (observations[0].host_boot, observations[0].connection)
    seen = set()
    previous = None
    intervals = []
    for observation in observations:
        if (observation.host_boot, observation.connection) != host:
            raise ValueError("host boot or connection changed")
        if observation.reply.request in seen:
            raise ValueError("reused request nonce")
        if previous is not None:
            if observation.before_ns < previous.after_ns:
                raise ValueError("overlapping or reordered host exchanges")
            if observation.reply.seq != previous.reply.seq + 1:
                raise ValueError("probe sequence gap or duplicate")
            if observation.reply.tick_hz != previous.reply.tick_hz:
                raise ValueError("configured counter rate changed")
        seen.add(observation.reply.request)
        intervals.append(
            probe_envelope(
                observation,
                target_tick,
                target_epoch=target_epoch,
                rate_hz_bounds=rate_hz_bounds,
                max_distance_ns=max_distance_ns,
            )
        )
        previous = observation
    low = max(interval[0] for interval in intervals)
    high = min(interval[1] for interval in intervals)
    if low > high:
        raise ValueError("probe envelopes have no common intersection")
    return {
        "conditional_mono_interval_ns": [low, high],
        "interval_width_ns": high - low,
        "status": "conditional_software_counter_only",
        "physical_timing_qualified": False,
        "rate_hz_bounds_assumed": list(rate_hz_bounds),
        "max_distance_ns_assumed": max_distance_ns,
        "target_epoch_declared": list(target_epoch),
        "probe_count": len(observations),
        "rf_acquisition_interval_ns": None,
    }


def recording_observations(records):
    """Require a clean complete recording and one unchanged runtime/host epoch."""
    if not isinstance(records, list) or len(records) < 3:
        raise ValueError("recording is missing start, probes or end")
    if not all(isinstance(row, dict) for row in records):
        raise ValueError("invalid recording row")
    start, end = records[0], records[-1]
    if (
        start.get("type") != "start"
        or start.get("schema") != "openflight.iwr-clock-probe.v1"
        or start.get("event_clock") != "host_monotonic_ns"
        or not isinstance(start.get("boot_id"), str)
        or not start["boot_id"]
        or not isinstance(start.get("connection_id"), str)
        or not start["connection_id"]
        or end.get("type") != "end"
        or "error" not in end
        or end.get("error") is not None
        or end.get("boot_id") != start["boot_id"]
        or end.get("connection_id") != start["connection_id"]
        or type(end.get("probe_count")) is not int
        or end["probe_count"] != len(records) - 2
        or type(start.get("requested_count")) is not int
        or not 1 <= start["requested_count"] <= 1000
        or start["requested_count"] != end["probe_count"]
    ):
        raise ValueError("incomplete recording or changed host epoch")
    previous = _uint(start.get("mono_ns"), 63)
    finish = _uint(end.get("mono_ns"), 63)
    observations = []
    seen = set()
    for seq, row in enumerate(records[1:-1]):
        if row.get("type") != "probe" or type(row.get("seq")) is not int or row["seq"] != seq:
            raise ValueError("missing, duplicated or reordered probe")
        parsed = parse_probe(row.get("reply_text"), row.get("request"), seq)
        obs = ProbeObservation(
            parsed,
            row.get("before_ns"),
            row.get("after_ns"),
            start["boot_id"],
            start["connection_id"],
        )
        obs.validate()
        if obs.before_ns < previous or obs.after_ns > finish or parsed.request in seen:
            raise ValueError("probe order, bracket or request nonce invalid")
        if observations and (parsed.build, parsed.boot, parsed.tick_hz) != (
            observations[0].reply.build,
            observations[0].reply.boot,
            observations[0].reply.tick_hz,
        ):
            raise ValueError("device runtime, build or configured rate changed")
        previous = obs.after_ns
        seen.add(parsed.request)
        observations.append(obs)
    return observations
