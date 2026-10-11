#!/usr/bin/env python3
"""W4a stage-1b IWR feasibility run, one profile per invocation (owner, Pi, kiosk stopped).

Each invocation writes a new, exclusive run directory. It sends the profile's config
(keeping every reply, including the firmware's own "Capture plan:" line), takes the soak
dumps, waits until the ring is ready, arms the sound-trigger queue, then takes one dump per
fresh edge for the real-ball and clap attempts. Every planned attempt gets exactly one row,
whatever happened to it. Nothing unobserved is estimated.

Example (profile A, then its triggered swings; prints READY before each swing):
  uv run python scripts/iwr6843/feasibility_run.py --profile A \\
      --config config/iwr6843_l3dump_wide_24f3ms_53bin_iq16.cfg --out-root ~/w4a-runs \\
      --image-sha256 <64 hex> --soak 200 --real-ball 20 \\
      --placement-label pilot-tee --placement-m 1.4
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import queue
import re
import sys
import threading
import time
import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from openflight.iwr6843.driver import (
    IWR6843DumpRecoveryError,
    IWR6843Radar,
    parse_bench,
    parse_perf_stats,
)

RUN_SCHEMA = "openflight.iwr6843.feasibility_run.v2"
ATTEMPT_SCHEMA = "openflight.iwr6843.feasibility_attempt.v2"
ABORT_SCHEMA = "openflight.iwr6843.feasibility_abort.v1"
# Same acoustic-ringing rule as IWR6843CaptureMonitor.notify_trigger: one edge per 0.1 s.
RING_REJECT_NS = 100_000_000
RUN_ID = re.compile(r"\d{8}T\d{6}Z-[0-9a-f]{8}")
PROFILE_NAME = re.compile(r"[A-Za-z0-9]+")
IMAGE_SHA256 = re.compile(r"[0-9a-f]{64}")
SOAK_STIMULI = ("none", "reflector")
# Reply windows, as IWR6843Radar.perf_stats and bench_kernel use; the script sends these
# itself so it can keep the raw reply even when the parser refuses it.
PERF_WINDOW_S = 2.0
BENCH_WINDOW_S = 10.0
# The gate's frozen profiles run their config file unchanged; C16 is C with iq8Scale 16.
FROZEN_PROFILES = ("A", "B", "C")
CONTROL_PROFILE, CONTROL_SCALE = "C16", 16
_PRE = re.compile(r"\bpre=(\d+)x\d+@(\d+)us\b")
# One line's iq8Scale command (not a "%" comment, not "iq8ScaleX"); its line ending is kept.
_SCALE = re.compile(r"^[ \t]*iq8Scale(?![^ \t\r\n])([^\r\n]*)", re.MULTILINE)
_ACTIVE = re.compile(r"\bactive=1\b")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _scale_lines(cfg_text: str) -> list[re.Match]:
    lines = list(_SCALE.finditer(cfg_text))
    if len(lines) > 1:
        raise ValueError(f"config has more than one iq8Scale line ({len(lines)})")
    return lines


def override_iq8_scale(cfg_text: str, scale: int) -> str:
    """The config with its one iq8Scale line set to ``scale`` (the positive control)."""
    lines = _scale_lines(cfg_text)
    if not lines:
        raise ValueError("config has no iq8Scale line to override")
    match = lines[0]
    return cfg_text[: match.start()] + f"iq8Scale {scale}" + cfg_text[match.end() :]


def config_iq8_scale(cfg_text: str) -> int | None:
    """The config's IQ8 scale, or None when it has no iq8Scale line (IQ16)."""
    lines = _scale_lines(cfg_text)
    if not lines:
        return None
    value = lines[0].group(1).strip()
    if not value.isdigit():
        raise ValueError(f"unreadable iq8Scale line {lines[0].group(0).strip()!r}")
    return int(value)


def capture_plan_line(responses) -> str | None:
    """The firmware's own ``Capture plan:`` line from the config replies, if any."""
    for _command, reply in responses:
        for line in reply.splitlines():
            if line.strip().startswith("Capture plan:"):
                return line.strip()
    return None


def pre_history_ns(plan_line: str | None) -> int | None:
    """Pre-request history (pre frames × frame period) in integer ns, from the plan line."""
    match = _PRE.search(plan_line or "")
    return int(match.group(1)) * int(match.group(2)) * 1_000 if match else None


def pre_history_s(plan_line: str | None) -> float | None:
    """Pre-request history (pre frames × frame period) in seconds, from the plan line."""
    history = pre_history_ns(plan_line)
    return history / 1e9 if history is not None else None


def _check_run_id(run_id: str) -> str:
    if not RUN_ID.fullmatch(run_id):
        raise ValueError(f"run ID {run_id!r} is not <UTC yyyymmddTHHMMSSZ>-<8 hex>")
    return run_id


def new_run_dir(out_root: Path, profile: str, *, run_id: str | None = None) -> tuple[str, Path]:
    """A fresh run ID and its directory, created exclusively (never reused)."""
    if run_id is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        run_id = f"{stamp}-{uuid.uuid4().hex[:8]}"
    _check_run_id(run_id)
    run_dir = Path(out_root) / f"{profile}-{run_id}"
    run_dir.parent.mkdir(parents=True, exist_ok=True)
    run_dir.mkdir(exist_ok=False)
    return run_id, run_dir


class EdgeQueue:
    """Sound-trigger GPIO edges (host monotonic ns); only armed, ready-time edges queue.

    ``on_edge`` runs on the GPIO callback thread; everything else on the run's thread.
    Every edge that is not handed to an attempt is counted under exactly one reason.
    """

    def __init__(self, clock=time.monotonic_ns):
        self._clock = clock
        self._queue: queue.Queue[int] = queue.Queue()
        self._lock = threading.Lock()
        self._armed = False
        self._busy = False
        self._closed = False
        self._last: int | None = None
        self._counts = {
            "pre_arm_discarded": 0,
            "busy_discarded": 0,
            "stale_discarded": 0,
            "ringing_discarded": 0,
            "after_run_discarded": 0,
        }

    def now_ns(self) -> int:
        """The queue's clock (host monotonic ns in a real run)."""
        return self._clock()

    def on_edge(self, now_ns: int | None = None) -> None:
        """One GPIO edge: queued if armed, idle and not ringing; otherwise counted."""
        now = self._clock() if now_ns is None else now_ns
        with self._lock:
            if self._last is not None and now - self._last < RING_REJECT_NS:
                self._counts["ringing_discarded"] += 1
                return
            self._last = now
            if self._closed:
                self._counts["after_run_discarded"] += 1
                return
            if not self._armed:
                self._counts["pre_arm_discarded"] += 1
                return
            if self._busy:
                self._counts["busy_discarded"] += 1
                return
            self._queue.put(now)

    def arm(self) -> None:
        """Start accepting edges; anything queued before now is discarded and counted."""
        with self._lock:
            while not self._queue.empty():
                self._queue.get_nowait()
                self._counts["pre_arm_discarded"] += 1
            self._armed = True
            self._busy = False

    def close(self) -> None:
        """End of the triggered phase: leftover and later edges are counted, never queued."""
        with self._lock:
            self._closed = True
            while not self._queue.empty():
                self._queue.get_nowait()
                self._counts["after_run_discarded"] += 1

    def set_busy(self, busy: bool) -> None:
        """Busy: a dump is in flight or the ring is refilling; edges are counted, not queued."""
        with self._lock:
            self._busy = busy

    def next_fresh_edge(self, timeout_s: float, max_age_ns: int) -> int | None:
        """The next queued edge no older than ``max_age_ns``; stale ones are dropped."""
        deadline = self._clock() + int(timeout_s * 1e9)
        while True:
            try:
                remaining = (deadline - self._clock()) / 1e9
                if remaining > 0:
                    edge = self._queue.get(timeout=remaining)
                else:
                    edge = self._queue.get_nowait()
            except queue.Empty:
                return None
            if self._clock() - edge > max_age_ns:
                with self._lock:
                    self._counts["stale_discarded"] += 1
                continue
            return edge

    def claim(self, edge_ns: int, max_age_ns: int) -> int | None:
        """Re-check ``edge_ns``'s age just before its request (R-F5) and go busy if fresh.

        Returns the age in ns, or None after counting the edge as stale. Checking and going
        busy under one lock means no other edge can queue between the check and the dump.
        """
        with self._lock:
            age = self._clock() - edge_ns
            if age > max_age_ns:
                self._counts["stale_discarded"] += 1
                return None
            self._busy = True
            return age

    def counters(self) -> dict:
        """Edges dropped so far, by reason."""
        with self._lock:
            return dict(self._counts)


def wait_ring_ready(radar, pre_history: float | None, sleep, polls: int = 60) -> bool:
    """True once the sensor is active and its pre-request history has refilled."""
    if pre_history is None:
        return False
    for _ in range(polls):
        if _ACTIVE.search(radar.stats()):
            sleep(pre_history)
            return True
        sleep(0.1)
    return False


def _write_new(path: Path, data: bytes) -> None:
    """Create ``path`` exclusively, write ``data`` and make it durable."""
    with path.open("xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def _write_json(path: Path, value: dict) -> None:
    """``value`` as indented, key-sorted JSON in a new file."""
    _write_new(path, (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8"))


def attempt_row(  # pylint: disable=too-many-arguments
    *,
    run_id: str,
    profile: str,
    phase: str,
    index: int,
    stimulus: str,
    placement: tuple[str | None, float | None],
    outcome: str,
    path: str | None = None,
    raw: bytes | None = None,
    timing=None,
    edge_ns: int | None = None,
    edge_age_at_check_ns: int | None = None,
    error: str | None = None,
) -> dict:
    """One ``attempts.jsonl`` row (schema ``openflight.iwr6843.feasibility_attempt.v2``).

    ``raw`` is None when no request was made (or its bytes are unknown): ``nbytes`` is then
    null, never 0. ``timing`` is the transfer's ``DumpTransferTiming`` or None. The edge's
    age at the request is ``request_written_ns - edge_ns`` when both were observed.
    """
    written = timing.request_written_ns if timing is not None else None
    placement_label, placement_m = placement
    return {
        "schema": ATTEMPT_SCHEMA,
        "run_id": run_id,
        "attempt_id": f"{run_id}/{phase}/{index:03d}",
        "profile": profile,
        "phase": phase,
        "index": index,
        "stimulus": stimulus,
        "placement_label": placement_label,
        "placement_m": placement_m,
        "outcome": outcome,
        "path": path,
        "nbytes": len(raw) if raw is not None else None,
        "sha256": _sha256(raw) if raw else None,
        "edge_ns": edge_ns,
        "edge_age_at_check_ns": edge_age_at_check_ns,
        "edge_age_at_request_ns": (
            written - edge_ns if written is not None and edge_ns is not None else None
        ),
        "timing": timing.to_dict() if timing is not None else None,
        "error": error,
    }


Attempt = tuple[str, int, str]  # (phase, index, stimulus)


class AttemptLog:
    """One run's ``attempts.jsonl``, created exclusively; one durable row per attempt."""

    def __init__(self, run_dir: Path, run_id: str, profile: str, placement: tuple):
        self.run_dir = run_dir
        self.run_id = run_id
        self.profile = profile
        self.placement = placement
        self.outcomes: dict[str, int] = {}
        self._handle = (run_dir / "attempts.jsonl").open("x", encoding="utf-8")

    def record(self, attempt: Attempt, outcome: str, **observed) -> None:
        """Write the attempt's dump (any bytes at all) and then its row."""
        phase, index, stimulus = attempt
        raw = observed.get("raw")
        path = None
        if raw:
            path = f"{phase}_{index:03d}.l3dump"
            _write_new(self.run_dir / path, raw)
        row = attempt_row(
            run_id=self.run_id,
            profile=self.profile,
            phase=phase,
            index=index,
            stimulus=stimulus,
            placement=self.placement,
            outcome=outcome,
            path=path,
            **observed,
        )
        self._handle.write(json.dumps(row, sort_keys=True) + "\n")
        self._handle.flush()
        os.fsync(self._handle.fileno())
        self.outcomes[outcome] = self.outcomes.get(outcome, 0) + 1

    def record_all(self, plan: list[Attempt], outcome: str, error: str) -> None:
        """The same outcome and reason for every attempt in ``plan``."""
        for attempt in plan:
            self.record(attempt, outcome, error=error)

    def close(self) -> None:
        """Close the log; rows already written are on disk."""
        self._handle.close()


def _take(log: AttemptLog, radar, attempt: Attempt, edge=(None, None)) -> None:
    """One ``l3dump`` request; its row is written whatever happens, even on an abort."""
    edge_ns, age = edge
    seen = {"edge_ns": edge_ns, "edge_age_at_check_ns": age}
    try:
        raw, timing = radar.read_dump_timed()
    except IWR6843DumpRecoveryError as exc:
        log.record(attempt, "dump_error", raw=exc.raw, timing=exc.timing, error=str(exc), **seen)
    except RuntimeError as exc:  # refused before any byte: a request with no bytes
        log.record(attempt, "dump_error", raw=b"", error=str(exc), **seen)
    except BaseException as exc:
        log.record(
            attempt, "dump_error", error=f"{type(exc).__name__}: {exc} (run aborted)", **seen
        )
        raise
    else:
        log.record(attempt, "captured", raw=raw, timing=timing, **seen)


@dataclass(frozen=True)
class _Pacing:
    """How the triggered phase waits: the injected sleep, the edge wait and the give-up rule."""

    sleep: Callable[[float], None]
    edge_timeout_s: float
    max_no_edge: int
    announce: Callable[[str], None]


def _ready_line(attempt: Attempt, total: int, placement: tuple, again: bool) -> str:
    """The cue the owner swings on."""
    _phase, index, stimulus = attempt
    label, metres = placement
    line = f"{stimulus} {index + 1} of {total} at {label} ({metres} m)"
    if again:
        return f"READY again: {line} (the last edge aged past the history before its request)"
    return f"READY: {line}"


def _await_edge(edges: EdgeQueue, max_age_ns: int, pacing: _Pacing, ready) -> tuple | None:
    """A claimed fresh edge ``(edge_ns, age_at_check_ns)``, or None at the timeout.

    An edge that went stale between the queue and the request is counted (in ``claim``)
    and never used; the attempt keeps waiting for a fresh one until its own deadline.
    """
    deadline = edges.now_ns() + int(pacing.edge_timeout_s * 1e9)
    announce = pacing.announce
    announce(ready(False))
    while True:
        remaining_s = max(0, deadline - edges.now_ns()) / 1e9
        edge_ns = edges.next_fresh_edge(remaining_s, max_age_ns)
        if edge_ns is None:
            return None
        age = edges.claim(edge_ns, max_age_ns)
        if age is not None:
            return edge_ns, age
        announce(ready(True))


def _triggered(
    log: AttemptLog, radar, plan: list[Attempt], edges, history_ns, pacing: _Pacing
) -> None:
    """Arm after the ring is ready; one fresh edge per attempt; never reuse an edge."""
    if not plan:
        return
    history_s = history_ns / 1e9 if history_ns is not None else None
    if not wait_ring_ready(radar, history_s, pacing.sleep):
        log.record_all(
            plan,
            "not_armed",
            "ring never ready before arming (no active=1, or "
            "no Capture plan line in the config replies)",
        )
        return
    edges.arm()
    totals = Counter(phase for phase, _index, _stimulus in plan)
    misses = 0
    for position, attempt in enumerate(plan):
        if misses >= pacing.max_no_edge:
            log.record(
                attempt,
                "no_edge",
                error=f"not attempted: run abandoned after {pacing.max_no_edge} no-edge waits",
            )
            continue
        try:
            claimed = _await_edge(
                edges,
                history_ns,
                pacing,
                lambda again, a=attempt: _ready_line(a, totals[a[0]], log.placement, again),
            )
        except BaseException as exc:
            log.record(
                attempt, "no_edge", error=f"{type(exc).__name__}: {exc} (run aborted while waiting)"
            )
            raise
        if claimed is None:
            misses += 1
            log.record(
                attempt, "no_edge", error=f"no fresh sound edge within {pacing.edge_timeout_s:g} s"
            )
            continue
        misses = 0
        _take(log, radar, attempt, claimed)
        # The queue reopens only once the ring holds a full pre-request history again.
        if not wait_ring_ready(radar, history_s, pacing.sleep):
            log.record_all(
                plan[position + 1 :],
                "not_armed",
                "ring not ready after the previous dump (no active=1); the queue stayed closed",
            )
            return
        edges.set_busy(False)


def _check_counts(soak: int, real_ball: int, clap: int, edges) -> None:
    """Whole, non-negative counts; at least one attempt; an edge queue when triggered."""
    counts = (soak, real_ball, clap)
    if any(isinstance(n, bool) or not isinstance(n, int) or n < 0 for n in counts):
        raise ValueError(f"attempt counts must be whole numbers >= 0, got {counts}")
    if not any(counts):
        raise ValueError("no attempts planned (soak, real_ball and clap are all 0)")
    if (real_ball or clap) and edges is None:
        raise ValueError("triggered attempts need an edge queue (the sound-trigger GPIO)")


def _check_placement(needed: bool, label: str | None, metres: float | None) -> None:
    """Triggered and reflector attempts happen at a taped, measured mark."""
    if not needed:
        return
    if not isinstance(label, str) or not label.strip():
        raise ValueError("triggered or reflector attempts need --placement-label (the taped mark)")
    if not isinstance(metres, (int, float)) or not math.isfinite(metres) or metres <= 0:
        raise ValueError(f"triggered or reflector attempts need --placement-m > 0, got {metres}")


def _check_settings(  # pylint: disable=too-many-arguments
    *,
    profile: str,
    image_sha256: str,
    soak_stimulus: str,
    run_id,
    soak_gap_s: float,
    edge_timeout_s: float,
    max_no_edge: int,
) -> None:
    """The run's names, hashes and timing settings, before anything is created."""
    if not PROFILE_NAME.fullmatch(profile):
        raise ValueError(f"profile name {profile!r} must be letters and digits only")
    if not isinstance(image_sha256, str) or not IMAGE_SHA256.fullmatch(image_sha256):
        raise ValueError(f"image SHA-256 {image_sha256!r} is not 64 lowercase hex digits")
    if soak_stimulus not in SOAK_STIMULI:
        raise ValueError(f"soak stimulus {soak_stimulus!r} is not one of {SOAK_STIMULI}")
    if run_id is not None:
        _check_run_id(run_id)
    for name, value in (("soak_gap_s", soak_gap_s), ("edge_timeout_s", edge_timeout_s)):
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"{name} must be a finite number >= 0, got {value}")
    if isinstance(max_no_edge, bool) or not isinstance(max_no_edge, int) or max_no_edge < 1:
        raise ValueError(f"max_consecutive_no_edge must be a whole number >= 1, got {max_no_edge}")


def _effective_config(profile: str, config_bytes: bytes, scale: int | None) -> bytes:
    """The exact bytes to send: the file itself, or C16's file with only iq8Scale changed."""
    if profile == CONTROL_PROFILE and scale != CONTROL_SCALE:
        raise ValueError(f"profile C16 is C with iq8Scale {CONTROL_SCALE}: pass --iq8-scale 16")
    if profile in FROZEN_PROFILES and scale is not None:
        raise ValueError(
            f"profile {profile} runs its frozen config unchanged; "
            "--iq8-scale is for C16 (or smoke/warmup)"
        )
    if scale is None:
        return config_bytes
    return override_iq8_scale(config_bytes.decode("utf-8"), scale).encode("utf-8")


def _close_out(radar) -> dict:
    """Counters, then stop the sensor and time the kernel; a missing value keeps its reason.

    The raw ``perfstats`` and ``l3bench`` replies are kept whatever the parsers make of
    them. ``perf`` is Task 6's parsed dict exactly as returned: unmeasured values stay None
    with their reasons under ``perf["unavailable"]``.
    """
    out = {
        "stats_text": radar.stats(),
        "perf_text": radar.cmd("perfstats", PERF_WINDOW_S),
        "perf": None,
        "perf_error": None,
        "stop_error": None,
        "bench_text": None,
        "bench": None,
        "bench_error": None,
    }
    try:
        out["perf"] = parse_perf_stats(out["perf_text"])
    except ValueError as exc:
        out["perf_error"] = str(exc)
    try:
        radar.stop_sensor()
    except RuntimeError as exc:
        out["stop_error"] = str(exc)
        out["bench_error"] = f"not run: the sensor did not stop ({exc})"
        return out
    out["bench_text"] = radar.cmd("l3bench", BENCH_WINDOW_S)
    try:
        out["bench"] = parse_bench(out["bench_text"])
    except RuntimeError as exc:
        out["bench_error"] = str(exc)
    return out


def _write_abort(run_dir: Path, run_id: str, exc: BaseException, responses, log) -> None:
    """Why the run stopped early, for the owner; the run stays incomplete (no run.json)."""
    try:
        _write_json(
            run_dir / "run_aborted.json",
            {
                "schema": ABORT_SCHEMA,
                "run_id": run_id,
                "error": f"{type(exc).__name__}: {exc}",
                "responses": [list(pair) for pair in responses],
                "outcomes": dict(log.outcomes) if log is not None else {},
                "aborted_at_utc": _utc_now(),
            },
        )
    except OSError:
        pass  # the original failure matters more than its note


def run_profile(  # pylint: disable=too-many-arguments,too-many-locals
    radar,
    *,
    profile: str,
    config_path: Path,
    out_root: Path,
    image_sha256: str,
    soak: int,
    real_ball: int,
    clap: int,
    edges,
    soak_stimulus: str = "none",
    placement_label: str | None = None,
    placement_m: float | None = None,
    iq8_scale_override: int | None = None,
    note: str | None = None,
    soak_gap_s: float = 1.0,
    edge_timeout_s: float = 300.0,
    max_consecutive_no_edge: int = 2,
    sleep=time.sleep,
    run_id: str | None = None,
    announce=lambda _message: None,
) -> dict:
    """Run one profile into a new directory; return the summary written to run.json.

    Every check runs before the directory exists, so a refused invocation leaves nothing
    that could be counted as an aborted run.
    """
    _check_settings(
        profile=profile,
        image_sha256=image_sha256,
        soak_stimulus=soak_stimulus,
        run_id=run_id,
        soak_gap_s=soak_gap_s,
        edge_timeout_s=edge_timeout_s,
        max_no_edge=max_consecutive_no_edge,
    )
    _check_counts(soak, real_ball, clap, edges)
    _check_placement(
        bool(real_ball or clap or soak_stimulus != "none"), placement_label, placement_m
    )
    config_bytes = Path(config_path).read_bytes()
    effective = _effective_config(profile, config_bytes, iq8_scale_override)
    iq8_scale = config_iq8_scale(effective.decode("utf-8"))
    run_id, run_dir = new_run_dir(out_root, profile, run_id=run_id)
    _write_new(run_dir / "effective.cfg", effective)
    started = {
        "schema": RUN_SCHEMA,
        "run_id": run_id,
        "profile": profile,
        "config_path": str(config_path),
        "config_sha256": _sha256(config_bytes),
        "effective_config_sha256": _sha256(effective),
        "iq8_scale": iq8_scale,
        "image_sha256": image_sha256,
        "planned": {"soak": soak, "real_ball": real_ball, "clap": clap},
        "soak_stimulus": soak_stimulus,
        "placement_label": placement_label,
        "placement_m": placement_m,
        "note": note,
        "script_sha256": _sha256(Path(__file__).read_bytes()),
        "started_at_utc": _utc_now(),
    }
    _write_json(run_dir / "run_started.json", started)
    responses: list[tuple[str, str]] = []
    log = None
    try:
        log = AttemptLog(run_dir, run_id, profile, (placement_label, placement_m))
        radar.send_config(str(run_dir / "effective.cfg"), responses=responses)
        plan_line = capture_plan_line(responses)
        for index in range(soak):
            sleep(soak_gap_s)  # lets the pre-request ring refill after the previous restart
            _take(log, radar, ("soak", index, soak_stimulus))
        plan = [("real_ball", i, "real_ball") for i in range(real_ball)]
        plan += [("clap", i, "clap") for i in range(clap)]
        pacing = _Pacing(sleep, edge_timeout_s, max_consecutive_no_edge, announce)
        _triggered(log, radar, plan, edges, pre_history_ns(plan_line), pacing)
        if edges is not None:
            edges.close()
        closing = _close_out(radar)
    except BaseException as exc:
        _write_abort(run_dir, run_id, exc, responses, log)
        raise
    finally:
        if log is not None:
            log.close()
    summary = {
        **started,
        "capture_plan_line": plan_line,
        "responses": [list(pair) for pair in responses],
        "pre_history_s": pre_history_s(plan_line),
        **closing,
        "outcomes": dict(log.outcomes),
        "edges": edges.counters() if edges is not None else None,
        "completed_at_utc": _utc_now(),
    }
    _write_json(run_dir / "run.json", summary)
    return summary


def _gpio_edges(pin: int):
    # pylint: disable=import-outside-toplevel,import-error
    from openflight.gpio_factory import ensure_lgpio_pin_factory

    ensure_lgpio_pin_factory()  # must precede the first gpiozero device on a Pi 5
    from gpiozero import Button

    edges = EdgeQueue()
    button = Button(pin, pull_up=False, bounce_time=None)  # as IWR6843CaptureMonitor does
    button.when_pressed = lambda: edges.on_edge()  # pylint: disable=unnecessary-lambda
    return edges, button


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--profile", required=True, choices=("A", "B", "C", "C16", "smoke", "warmup")
    )
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--out-root", required=True, type=Path)
    parser.add_argument("--image-sha256", required=True)
    parser.add_argument("--soak", type=int, default=200)
    parser.add_argument("--soak-stimulus", choices=SOAK_STIMULI, default="none")
    parser.add_argument("--real-ball", type=int, default=0)
    parser.add_argument("--claps", type=int, default=0)
    parser.add_argument("--placement-label")
    parser.add_argument("--placement-m", type=float)
    parser.add_argument("--iq8-scale", type=int, choices=(16, 32, 64, 128, 256))
    parser.add_argument("--note")
    parser.add_argument("--soak-gap-s", type=float, default=1.0)
    parser.add_argument("--edge-timeout-s", type=float, default=300.0)
    parser.add_argument("--gpio-pin", type=int, default=17)
    parser.add_argument("--port")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run one profile on the Pi; exit 0 only if every attempt and every reading came back."""
    args = _parser().parse_args(argv)
    triggered = args.real_ball + args.claps
    edges, button = _gpio_edges(args.gpio_pin) if triggered else (None, None)
    try:
        radar = IWR6843Radar(port=args.port)
        try:
            summary = run_profile(
                radar,
                profile=args.profile,
                config_path=args.config,
                out_root=args.out_root,
                image_sha256=args.image_sha256,
                soak=args.soak,
                real_ball=args.real_ball,
                clap=args.claps,
                edges=edges,
                soak_stimulus=args.soak_stimulus,
                placement_label=args.placement_label,
                placement_m=args.placement_m,
                iq8_scale_override=args.iq8_scale,
                note=args.note,
                soak_gap_s=args.soak_gap_s,
                edge_timeout_s=args.edge_timeout_s,
                announce=lambda message: print(message, flush=True),
            )
        finally:
            radar.close()
    finally:
        if button is not None:
            button.close()
    print(f"run directory: {Path(args.out_root) / (summary['profile'] + '-' + summary['run_id'])}")
    keys = (
        "profile",
        "run_id",
        "outcomes",
        "edges",
        "capture_plan_line",
        "perf_error",
        "stop_error",
        "bench_error",
    )
    print(json.dumps({key: summary[key] for key in keys}, indent=2))
    readings_ok = not any(summary[key] for key in ("perf_error", "stop_error", "bench_error"))
    return 0 if set(summary["outcomes"]) <= {"captured"} and readings_ok else 1


if __name__ == "__main__":
    sys.exit(main())
