"""Observed IWR6843 frame timing and sample integrity from one parsed dump (W4a).

Only what the firmware observed is used. A dump without the version 8/9 evidence block
is "unobserved": its descriptor deltas are the firmware's plan, and nothing here
rebuilds observed times from them.

Frame timing has three states:
- continuous: every retained frame was associated with an armed frame start, sequence
  numbers advance by exactly the planned number of frames, and each frame-start tick
  step, rounded to whole frame periods, equals its sequence step;
- discontinuous: a frame out of order, a sequence gap, a tick gap or an unassociated
  frame was observed (the finding names the frame);
- unobserved: no evidence, or the evidence can't be trusted (a tick running backwards
  while the sequence advances; a planned delta that isn't whole frames).
Rounding a tick step to the nearest whole period classifies it; the half-period
boundary follows from the period itself and is not a tolerance.

An unassociated frame (HWA output with no fresh armed frame start) has no observed
sequence number or start tick. It yields only its "unassociated" finding and no start
offset or output latency; order, gap and tick checks compare each associated frame with
the previous associated one. Offsets are from frame 0's start, so they are all None when
frame 0 is unassociated. If the first post-request frame is unassociated, the request
relation's frame numbers are None and its status is "unobserved" (or "inconsistent" when
a stamp is also out of order). The same holds when there is no first post-request frame at
all: every retained frame precedes the request ("no_post_frame"), or the pre-frame count
is outside the frames retained ("pre_frames_out_of_bounds").
"""

from __future__ import annotations

from itertools import pairwise

import numpy as np

from openflight.iwr6843.dump import (
    CAPTURE_FLAG_IQ8_SCAN,
    SAMPLE_RANGE_FFT_IQ8_VARIABLE_TIMED,
)

FRAME_TIMING_SCHEMA = "openflight.iwr6843.frame_timing.v1"
SAMPLE_INTEGRITY_SCHEMA = "openflight.iwr6843.sample_integrity.v1"
CONTINUOUS = "continuous"
DISCONTINUOUS = "discontinuous"
UNOBSERVED = "unobserved"
_TICK_MODULUS = 1 << 32
_UNOBSERVED_KINDS = frozenset({"tick_backwards", "planned_step"})
_IQ16_RAILS = (-32768.0, 32767.0)
# Findings that leave the request relation unobserved (no first post frame to measure),
# never inconsistent on their own.
_NO_FIRST_POST = frozenset({"first_post_unassociated", "no_post_frame", "pre_frames_out_of_bounds"})
_IQ8_LIMIT = 128  # int8 holds -128..127; magnitudes >= 64 are within a factor of 2


def tick_delta(later: int, earlier: int) -> int:
    """Forward distance from ``earlier`` to ``later`` on the 32-bit cycle counter."""
    return (int(later) - int(earlier)) % _TICK_MODULUS


def _signed(later: int, earlier: int) -> int:
    delta = tick_delta(later, earlier)
    return delta - _TICK_MODULUS if delta >= _TICK_MODULUS // 2 else delta


def _finding(kind: str, frame: int | None, detail: str) -> dict:
    return {"kind": kind, "frame": frame, "detail": detail}


def _span(values: list[float]) -> dict | None:
    return {"min": min(values), "max": max(values)} if values else None


def _unobserved(base: dict, reason: str) -> dict:
    return {
        **base,
        "status": UNOBSERVED,
        "reason": reason,
        "findings": [],
        "observed_start_offsets_us": None,
        "start_jitter_us": None,
        "output_latency_us": None,
        "hwa_missed_total": None,
        "request": None,
    }


def _frame_findings(frames, offsets, period_us: int, period_ticks: float, us) -> tuple:
    findings: list[dict] = []
    jitter: list[float] = []
    for index, record in enumerate(frames):
        if not record["associated"]:
            findings.append(
                _finding("unassociated", index, "HWA output without a fresh armed frame start")
            )
    associated = [index for index, record in enumerate(frames) if record["associated"]]
    for earlier, index in pairwise(associated):
        planned_delta = offsets[index] - offsets[earlier]
        planned_step, remainder = divmod(planned_delta, period_us)
        if remainder or planned_step < 1:
            findings.append(
                _finding(
                    "planned_step",
                    index,
                    f"planned delta {planned_delta} us is not whole {period_us} us frames",
                )
            )
            continue
        previous, current = frames[earlier], frames[index]
        seq_step = _signed(current["seq"], previous["seq"])
        if seq_step <= 0:
            findings.append(
                _finding("order", index, f"sequence {previous['seq']} -> {current['seq']}")
            )
            continue
        tick_step = _signed(current["start_tick"], previous["start_tick"])
        if tick_step <= 0:
            findings.append(
                _finding("tick_backwards", index, f"frame-start tick moved {tick_step} ticks")
            )
            continue
        if seq_step != planned_step:
            findings.append(
                _finding("gap", index, f"{seq_step} frame starts where the plan has {planned_step}")
            )
        tick_frames = round(tick_step / period_ticks)
        if tick_frames != seq_step:
            findings.append(
                _finding(
                    "tick_mismatch",
                    index,
                    f"{tick_step} ticks is {tick_frames} periods for {seq_step} sequence steps",
                )
            )
        else:
            jitter.append(us(tick_step - seq_step * period_ticks))
    return findings, jitter


def _request_relation(capture: dict, frames, trailer: dict | None, us) -> dict:
    findings: list[dict] = []
    pre = capture["pre_frames"]
    first_post = frames[pre] if 0 <= pre < len(frames) else None
    if not 0 <= pre <= len(frames):
        findings.append(
            _finding(
                "pre_frames_out_of_bounds",
                None,
                f"{pre} pre-request frames is outside the {len(frames)} frames retained",
            )
        )
    elif first_post is None:
        findings.append(
            _finding(
                "no_post_frame",
                None,
                "every retained frame precedes the request; no post-request frame was observed",
            )
        )
    elif not first_post["associated"]:
        findings.append(
            _finding(
                "first_post_unassociated",
                pre,
                "the first post-request frame has no fresh armed start; its sequence and "
                "start tick are unobserved",
            )
        )
        first_post = None
    request_to_post = (
        _signed(first_post["start_tick"], capture["request_tick"]) if first_post else None
    )
    if request_to_post is not None and request_to_post <= 0:
        findings.append(
            _finding(
                "post_before_request", pre, "the first post-request frame starts before the request"
            )
        )
    stamps = [
        ("request_tick", capture["request_tick"]),
        ("freeze_tick", capture["freeze_tick"]),
        ("stop_tick", capture["stop_tick"]),
        ("header_tick", capture["header_tick"]),
    ]
    if trailer is not None:
        stamps.append(("payload_end_tick", trailer["payload_end_tick"]))
    for (earlier_name, earlier), (later_name, later) in zip(stamps, stamps[1:]):
        if _signed(later, earlier) < 0:
            findings.append(_finding("stamp_order", None, f"{later_name} precedes {earlier_name}"))
    inconsistent = [f for f in findings if f["kind"] not in _NO_FIRST_POST]
    return {
        "status": "inconsistent" if inconsistent else "unobserved" if findings else "consistent",
        "findings": findings,
        "pre_frames": pre,
        "post_frames": capture["post_frames"],
        "request_seq": capture["request_seq"],
        "first_post_seq": first_post["seq"] if first_post else None,
        "frames_request_to_first_post": (
            _signed(first_post["seq"], capture["request_seq"]) if first_post else None
        ),
        "request_to_first_post_start_us": us(request_to_post) if first_post else None,
        "request_to_freeze_us": us(_signed(capture["freeze_tick"], capture["request_tick"])),
        "freeze_to_stop_us": us(_signed(capture["stop_tick"], capture["freeze_tick"])),
        "stop_to_header_us": us(_signed(capture["header_tick"], capture["stop_tick"])),
        "header_to_payload_end_us": (
            us(_signed(trailer["payload_end_tick"], capture["header_tick"])) if trailer else None
        ),
    }


def frame_timing_report(meta: dict) -> dict:
    """Classify one parsed dump's observed frame timing; see the module docstring."""
    base = {
        "schema": FRAME_TIMING_SCHEMA,
        "dump_version": meta.get("version"),
        "frames": meta.get("n_frames"),
        "frame_time_source": meta.get("frame_time_source"),
    }
    capture = meta.get("capture_evidence")
    frames = meta.get("frame_evidence")
    if capture is None or frames is None:
        return _unobserved(
            base, f"dump version {meta.get('version')} carries no observed frame ticks"
        )
    period_us = meta.get("frame_period_us") or 0
    offsets = meta.get("frame_time_offsets_us")
    if period_us <= 0 or offsets is None or len(frames) != meta["n_frames"]:
        return _unobserved(base, "no planned frame period or descriptors to compare against")
    tick_hz = capture["tick_hz"]

    def us(ticks: float) -> float:
        return round(ticks * 1e6 / tick_hz, 3)

    findings, jitter = _frame_findings(frames, offsets, period_us, period_us * tick_hz / 1e6, us)
    unobserved = [f for f in findings if f["kind"] in _UNOBSERVED_KINDS]
    status = UNOBSERVED if unobserved else DISCONTINUOUS if findings else CONTINUOUS
    origin = frames[0]["start_tick"] if frames[0]["associated"] else None
    relation = _request_relation(capture, frames, meta.get("dump_trailer"), us)
    if meta.get("transfer_timing_source") == "snapshot_metadata":
        relation["metadata_snapshot_to_ready_us"] = relation["header_to_payload_end_us"]
        relation["stop_to_metadata_snapshot_us"] = relation["stop_to_header_us"]
        relation["header_to_payload_end_us"] = None
        relation["stop_to_header_us"] = None
    return {
        **base,
        "status": status,
        "reason": (
            unobserved[0]["detail"] if unobserved else findings[0]["detail"] if findings else None
        ),
        "findings": findings,
        "observed_start_offsets_us": [
            us(_signed(f["start_tick"], origin)) if origin is not None and f["associated"] else None
            for f in frames
        ],
        "start_jitter_us": _span(jitter),
        "output_latency_us": _span(
            [us(_signed(f["done_tick"], f["start_tick"])) for f in frames if f["associated"]]
        ),
        "hwa_missed_total": capture["hwa_missed_total"],
        "request": relation,
    }


def _iq8_integrity(meta: dict) -> dict:
    capture = meta.get("capture_evidence")
    frames = meta.get("frame_evidence")
    report = {"format": "iq8"}
    if capture is None or frames is None:
        return {
            **report,
            "status": UNOBSERVED,
            "reason": f"dump version {meta.get('version')} records nothing before IQ8 narrowing",
        }
    unscanned = [i for i, f in enumerate(frames) if not f["iq8_scanned"]]
    if not capture["flags"] & CAPTURE_FLAG_IQ8_SCAN or unscanned:
        return {
            **report,
            "status": UNOBSERVED,
            "reason": f"frames {unscanned} were not scanned before narrowing"
            if unscanned
            else "the capture's IQ8 scan flag is off",
        }
    # The firmware caps each frame's count at 65535: a frame reading 65535 means "at least
    # 65535", so overflow_components is then a floor, not an exact count.
    overflow = [f["iq8_overflow"] for f in frames]
    max_abs = [f["iq8_max_abs"] for f in frames]
    total = sum(overflow)
    return {
        **report,
        "status": "overflowed" if total else "clean",
        "reason": None,
        "frames_scanned": len(frames),
        "overflow_components": total,
        "frames_with_overflow": [i for i, count in enumerate(overflow) if count],
        "max_abs": max(max_abs),
        "frames_within_2x_of_wrap": sum(1 for value in max_abs if value >= _IQ8_LIMIT // 2),
    }


def sample_integrity_report(meta: dict, cube: np.ndarray) -> dict:
    """IQ8: the firmware's pre-narrowing scan. IQ16: components at the int16 rails."""
    if meta.get("sample_fmt") == SAMPLE_RANGE_FFT_IQ8_VARIABLE_TIMED:
        return {"schema": SAMPLE_INTEGRITY_SCHEMA, **_iq8_integrity(meta)}
    railed = np.isin(cube.real, _IQ16_RAILS) | np.isin(cube.imag, _IQ16_RAILS)
    both = np.isin(cube.real, _IQ16_RAILS) & np.isin(cube.imag, _IQ16_RAILS)
    per_frame = railed.reshape(cube.shape[0], -1).sum(axis=1) + both.reshape(cube.shape[0], -1).sum(
        axis=1
    )
    total = int(per_frame.sum())
    return {
        "schema": SAMPLE_INTEGRITY_SCHEMA,
        "format": "iq16",
        "status": "railed" if total else "clean",
        "reason": None,
        "rail_components": total,
        "frames_with_rails": [int(i) for i in np.flatnonzero(per_frame)],
    }
