"""W4a firmware performance diagnostics: ``perfstats`` and ``l3bench`` over the IWR6843 CLI.

Bench tooling only; the kiosk never sends these commands.
"""

from __future__ import annotations

import re
from typing import Any

# Reply windows for the W4a firmware's perfstats and l3bench (l3bench passes over the whole
# stopped ring). Public so a caller that sends these commands itself, to keep the raw
# reply, waits exactly as long as perf_stats and bench_kernel do.
PERF_REPLY_WINDOW_S = 2.0
BENCH_REPLY_WINDOW_S = 10.0

_NOT_IN_BUILD = "not_in_build"
_NOT_CREATED = "not_created"
_NO_SAMPLES = "no samples"
_MARGIN_UNSET = 0xFFFFFFFF  # margin_min before any margin was measured

# perfstats line tag -> {firmware field -> (result key, value kind)}.
# Kinds: "int"; "pair" (used/size); "pair?" (a pair, or the marker not_created).
_PERF_GRAMMAR: dict[str, dict[str, tuple[str, str]]] = {
    "perf": {
        name: (name, "int")
        for name in (
            "tick_hz",
            "rearm_max",
            "rearms",
            "task_max",
            "task_runs",
            "margin_min",
            "margin_frames",
            "scan_max",
            "scan_frames",
            "scan_total_hi",
            "scan_total_lo",
            "pack_max",
            "packs",
            "pack_overlap",
            "iq8_overflow",
            "iq8_overflow_saturated",
        )
    },
    "heap": {
        "size": ("heap_size", "int"),
        "free": ("heap_free", "int"),
        "free_min": ("heap_free_min", "int"),
        "largest": ("heap_largest", "int"),
    },
    "stacks": {
        "init": ("stack_init", "pair?"),
        "ctrl": ("stack_ctrl", "pair?"),
        "rearm": ("stack_rearm", "pair?"),
        "cli": ("stack_cli", "pair?"),
        "hwi": ("stack_hwi", "pair"),
    },
}
# "perf scan=not_in_build pack=not_in_build": a build without the IQ8 scan and EDMA pack.
_PERF_NO_SCAN_WORDS = (f"scan={_NOT_IN_BUILD}", f"pack={_NOT_IN_BUILD}")
_PERF_SCAN_RESULT_KEYS = (
    "scan_max",
    "scan_frames",
    "scan_total",
    "pack_max",
    "packs",
    "pack_overlap",
    "iq8_overflow",
    "iq8_overflow_saturated",
)
_PERF_SCAN_KEYS = (
    "scan_max",
    "scan_frames",
    "scan_total_hi",
    "scan_total_lo",
    "pack_max",
    "packs",
    "pack_overlap",
    "iq8_overflow",
    "iq8_overflow_saturated",
)
# Required in every build; the scan group is added unless the no-scan marker line is present.
_PERF_REQUIRED = (
    "tick_hz",
    "rearm_max",
    "rearms",
    "task_max",
    "task_runs",
    "margin_min",
    "margin_frames",
    "heap_size",
    "heap_free",
    "heap_free_min",
    "heap_largest",
    "stack_init",
    "stack_ctrl",
    "stack_rearm",
    "stack_cli",
    "stack_hwi",
)
# Result key -> the count that says whether it was ever measured.
_PERF_COUNTED = {
    "rearm_max": "rearms",
    "task_max": "task_runs",
    "margin_min": "margin_frames",
    "scan_max": "scan_frames",
    "scan_total": "scan_frames",
    "iq8_overflow": "scan_frames",
    "pack_max": "packs",
    "pack_overlap": "packs",
}
_BENCH_LINE = re.compile(r"bench samples=(\d+) cycles=(\d+) sum_hi=(\d+) sum_lo=(\d+)")


def parse_perf_stats(text: str) -> dict[str, Any]:
    """Parse the firmware's four ``perfstats`` lines (``l3_cli_perfStats``); refuse anything else.

    Values the firmware did not measure are ``None`` with their reason in
    ``result["unavailable"]``, never 0: ``not_in_build`` (scan and pack in a build
    without them), ``not_created`` (a task with no handle) and ``no samples`` (a maximum
    or total whose own count is 0, or ``margin_min`` at its 4294967295 sentinel with
    ``margin_frames=0``). ``scan_total`` is the 64-bit sum of ``scan_total_hi/lo``.
    ``iq8_overflow_saturated`` is a bool: True means ``iq8_overflow`` is a floor (at least
    that many components), not a count. Fields are ints; ``stack_*`` are
    ``{"used", "size"}``; the counts (``rearms``, ``task_runs``, ``margin_frames``,
    ``scan_frames``, ``packs``) stay ints, 0 included.
    """
    values: dict[str, Any] = {}
    unavailable: dict[str, str] = {}
    markers: set[str] = set()
    for line in text.splitlines():
        words = line.strip().split()
        if not words or words[0] not in _PERF_GRAMMAR:
            continue
        tag = words[0]
        for word in words[1:]:
            if tag == "perf" and word in _PERF_NO_SCAN_WORDS:
                markers.add(word.partition("=")[0])
            else:
                _read_perf_word(tag, word, values, unavailable)
    if markers:
        if markers != {"scan", "pack"} or any(key in values for key in _PERF_SCAN_KEYS):
            raise ValueError(
                "perfstats scan/pack marker line is incomplete or mixed with scan fields"
            )
    required = _PERF_REQUIRED + (() if markers else _PERF_SCAN_KEYS)
    missing = [key for key in required if key not in values]
    if missing:
        raise ValueError(f"perfstats reply lacks {missing} (firmware without W4a evidence?)")
    if values["tick_hz"] == 0:
        raise ValueError("unreadable perfstats field tick_hz=0")
    if markers:
        for key in _PERF_SCAN_RESULT_KEYS:
            values[key] = None
            unavailable[key] = _NOT_IN_BUILD
    else:
        flag = values["iq8_overflow_saturated"]
        if flag not in (0, 1):
            raise ValueError(f"unreadable perfstats field iq8_overflow_saturated={flag}")
        values["iq8_overflow_saturated"] = bool(flag)
        values["scan_total"] = (values.pop("scan_total_hi") << 32) | values.pop("scan_total_lo")
    for key, count_key in _PERF_COUNTED.items():
        if key not in unavailable:
            _gate_unmeasured(values, unavailable, key, count_key)
    values["unavailable"] = unavailable
    return values


def _read_perf_word(
    tag: str, word: str, values: dict[str, Any], unavailable: dict[str, str]
) -> None:
    """Store one ``name=value`` word of a perfstats line under its result key."""
    name, _, raw = word.partition("=")
    if name not in _PERF_GRAMMAR[tag]:
        raise ValueError(f"unexpected perfstats field {word!r} on the {tag} line")
    key, kind = _PERF_GRAMMAR[tag][name]
    if key in values:
        raise ValueError(f"duplicate perfstats field {key}")
    first, slash, second = raw.partition("/")
    if kind == "int" and first.isdigit() and not slash:
        values[key] = int(first)
    elif kind.startswith("pair") and first.isdigit() and second.isdigit():
        values[key] = {"used": int(first), "size": int(second)}
    elif kind == "pair?" and raw == _NOT_CREATED:
        values[key] = None
        unavailable[key] = _NOT_CREATED
    else:
        raise ValueError(f"unreadable perfstats field {word!r} ({key})")


def _gate_unmeasured(
    values: dict[str, Any], unavailable: dict[str, str], key: str, count_key: str
) -> None:
    """None (reason "no samples") for a value whose own count is 0; refuse a contradiction."""
    sentinel = _MARGIN_UNSET if key == "margin_min" else 0
    if values[count_key] == 0:
        if values[key] != sentinel:
            raise ValueError(f"perfstats {key}={values[key]} beside {count_key}=0")
        values[key] = None
        unavailable[key] = _NO_SAMPLES
    elif key == "margin_min" and values[key] == sentinel:
        raise ValueError(
            f"perfstats margin_min is the unmeasured sentinel beside "
            f"margin_frames={values[count_key]}"
        )


def parse_bench(text: str) -> dict[str, Any]:
    """Parse the ``l3bench`` reply; the sum is exact (two 32-bit halves)."""
    if "stop the sensor" in text:
        raise RuntimeError("l3bench refused: stop the sensor before l3bench")
    match = _BENCH_LINE.search(text)
    if match is None:
        raise RuntimeError(f"no l3bench reply in {text.strip()[:120]!r}")
    samples, cycles, high, low = (int(group) for group in match.groups())
    return {
        "samples": samples,
        "cycles": cycles,
        "sum": (high << 32) | low,
        "cycles_per_sample": cycles / samples if samples else None,
    }


def perf_stats(radar) -> dict[str, Any]:
    """The W4a firmware's ``perfstats`` high-water marks (ValueError on old images)."""
    return parse_perf_stats(radar.cmd("perfstats", PERF_REPLY_WINDOW_S))


def bench_kernel(radar) -> dict[str, Any]:
    """Time one pass over the stopped ring (``l3bench``); the sensor must be stopped."""
    return parse_bench(radar.cmd("l3bench", BENCH_REPLY_WINDOW_S))
