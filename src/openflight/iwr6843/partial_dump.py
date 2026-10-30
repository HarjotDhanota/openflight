"""Recover the intact frames of an IWR6843 dump whose serial transfer lost bytes.

What a partial dump is. When the host cannot drain the CP2105 fast enough, bytes
are dropped silently mid-stream; the reader then times out with fewer bytes than
the header declares and keeps everything it received, including the firmware's
CLI reply after the dump ("Done\\n" + "l3dump:/>", 14 bytes).

What the field data proves. All 32 short dumps of the 24 Oct 2026 session were
short by an exact multiple of 256 bytes once that 14-byte reply is counted
(1,280-36,608 B, 5-143 x 256); 32 unrelated shortfalls would all do that with
odds of 256^-31. So bytes go in whole 256-byte units. That the unit is the cp210x
driver's 256-byte read URB, dropped whole while the host is backlogged, is the
likely mechanism but not proven; where the runs fall, and how many each dump had,
was not recorded.

Why the runs can be found. The payload carries no markers, but the scene does:
static clutter makes every TDM loop of a frame nearly repeat the loop before it
(same TX, RX and absolute range bin). A received sample placed at the right true
position matches the last value placed for its key; one placed after an
unaccounted gap does not. A 256-byte unit shifts the I/Q16 stream by 64 samples,
and 64*k is distinct modulo one loop (636 samples on the production profile) for
every k below 159, so each gap's size is identifiable from one loop of data after
it, and the total shortfall bounds the search.

What is recovered. Decoding walks the received samples, keeps the current shift
while they fit, and on a misfit tests every remaining 256-byte shift over the
next loop. A clear winner is a located gap: the change point is found per sample
and every position the evidence cannot rule out, plus a margin, is discarded. An
unclear winner stops the decode; nothing after it is placed. The first loop has
nothing before it, so it is checked against the next loop instead.

What cannot be seen, and how it is caught. Loop agreement fixes shifts only
relative to each other: a run lost in the payload's first ~16 samples moves every
later sample by the same amount and they all still agree. The frames where the
range window moves are the absolute anchor (``_anchor_failure``); a placement that
fails one, or a capture with no window change to check, is refused whole.

A frame is kept only when every one of its samples was placed and its loops agree;
the kept frames are repacked, time offsets rebased to the first kept frame, as an
ordinary dump the estimators read unchanged. Missing bytes are never filled in.
Only timed range-snapshot dumps (sample formats 4 and 5) are decoded: their frames
are in time order with per-frame time offsets, so a frame subset stays a valid
capture. Other formats are refused.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np

from openflight.iwr6843.dump import (
    DUMP_TRAILER,
    DUMP_TRAILER_MAGIC,
    SAMPLE_RANGE_FFT_IQ8_VARIABLE_TIMED,
    SAMPLE_RANGE_FFT_IQ16_VARIABLE_TIMED,
    TIMED_FRAME_DESCRIPTOR,
    _parse_frame_metadata,
    pack_dump,
    parse_dump,
    parse_header,
    payload_nbytes,
)

# The label a partial dump's estimates carry: weaker than a whole dump's "measured".
PARTIAL_LABEL = "iwr_partial"

# Field-proven loss unit (24 Oct 2026: 32 of 32 shortfalls).
LOSS_QUANTUM_BYTES = 256

# The firmware's CLI reply after the dump; the reader keeps it when a dump ends early.
_CLI_TAIL = re.compile(rb"(?:Done|Error[^\r\n]{0,64})\r?\n[^\r\n]{0,32}:/>\Z")

# An evidence dump whose trailer was lost is repacked as the same layout without evidence.
_VERSION_WITHOUT_EVIDENCE = {8: 7, 9: 6}

_SUPPORTED_FORMATS = (SAMPLE_RANGE_FFT_IQ16_VARIABLE_TIMED, SAMPLE_RANGE_FFT_IQ8_VARIABLE_TIMED)

# Normalised misfit |a - b| / (|a| + |b|): 0 for equal samples, about 0.7-0.9 for
# unrelated ones. Measured on eight field dumps (shots and static captures, Sept-Oct
# 2026), correctly placed 64-sample blocks have a median misfit of 0.11-0.21 and a
# worst block of 0.65 (a shot's impact frame); any wrong 256-byte shift scores
# 0.60-0.90 over a loop. The thresholds sit between.
BLOCK_MISFIT_OK = 0.45
WINDOW_MISFIT_MAX = 0.6
# A located gap must beat staying put and every other shift by these margins.
GAP_MARGIN = 0.15
UNIQUE_MARGIN = 0.08
# A frame's loops must agree this well once placed (median over the frame).
FRAME_MISFIT_MAX = 0.5
# Fewest referenced samples a window needs before its score is trusted.
MIN_REFERENCED = 48
# A gap's change point is uncertain over every split costing within this much
# (summed misfit) of the best; that span plus a margin either side is discarded.
CHANGE_POINT_SLACK = 2.0
CHANGE_POINT_MARGIN = 16
# The first loop is checked against the next: summed (misfit - START_MISFIT) from the
# payload's first sample must reach START_EVIDENCE to call an early gap (about 16
# disagreeing samples at the field dumps' typical 0.75 misfit).
START_MISFIT = 0.45
START_EVIDENCE = 4.0
# The same sum, taken back from a window change, that marks the whole placement as
# sitting late. A real misalignment is at least one 64-sample unit, about +19; the
# higher bar keeps a busy frame end from refusing a good placement.
ANCHOR_EVIDENCE = 8.0


@dataclass(frozen=True)
class LostRun:
    """One located run of missing payload bytes, in true dump coordinates."""

    payload_offset: int  # first missing payload byte (true position)
    nbytes: int  # bytes the transfer dropped here
    guard_nbytes: int = 0  # received bytes discarded either side as unplaceable

    def to_dict(self) -> dict:
        """JSON-safe record."""
        return {
            "payload_offset": self.payload_offset,
            "nbytes": self.nbytes,
            "guard_nbytes": self.guard_nbytes,
        }


@dataclass
class PartialDumpRecovery:
    """What a partial dump yields: the intact frames as a dump, and what was missing."""

    status: str  # "recovered", "complete" or "unrecoverable"
    reason: str | None
    received_nbytes: int
    expected_nbytes: int | None
    lost_nbytes: int | None = None
    cli_tail_nbytes: int = 0
    runs: tuple[LostRun, ...] = ()
    tail_lost_nbytes: int = 0  # missing bytes after the last received payload byte
    unplaced_nbytes: int = 0  # received bytes not placed (guards, an undecodable rest)
    n_frames: int = 0
    frames_kept: tuple[int, ...] = ()
    frames_dropped: tuple[int, ...] = ()
    time_origin_shift_us: int = 0
    raw: bytes | None = field(default=None, repr=False)

    @property
    def usable(self) -> bool:
        """Whether there is a recovered dump to run estimators on."""
        return self.raw is not None and bool(self.frames_kept)

    @property
    def fraction_bytes_received(self) -> float | None:
        """Received bytes over the header-declared size."""
        if not self.expected_nbytes:
            return None
        return (self.received_nbytes - self.cli_tail_nbytes) / self.expected_nbytes

    @property
    def fraction_frames_kept(self) -> float | None:
        """Kept frames over the dump's frames."""
        return len(self.frames_kept) / self.n_frames if self.n_frames else None

    def missing_summary(self) -> str:
        """One plain sentence naming what the recovered result lacks."""
        if self.status == "complete":
            return "nothing missing"
        if not self.usable:
            return f"nothing recovered: {self.reason}"
        dropped = ", ".join(str(frame) for frame in self.frames_dropped) or "none"
        return (
            f"{self.lost_nbytes} of {self.expected_nbytes} bytes lost in "
            f"{len(self.runs) + (1 if self.tail_lost_nbytes else 0)} run(s); "
            f"{len(self.frames_kept)} of {self.n_frames} frames kept, dropped frames {dropped}"
        )

    def to_dict(self) -> dict:
        """JSON-safe record (the recovered bytes are not included)."""
        return {
            "label": PARTIAL_LABEL,
            "status": self.status,
            "reason": self.reason,
            "received_bytes": self.received_nbytes,
            "expected_bytes": self.expected_nbytes,
            "lost_bytes": self.lost_nbytes,
            "cli_tail_bytes": self.cli_tail_nbytes,
            "runs": [run.to_dict() for run in self.runs],
            "tail_lost_bytes": self.tail_lost_nbytes,
            "unplaced_bytes": self.unplaced_nbytes,
            "n_frames": self.n_frames,
            "frames_kept": list(self.frames_kept),
            "frames_dropped": list(self.frames_dropped),
            "fraction_bytes_received": self.fraction_bytes_received,
            "fraction_frames_kept": self.fraction_frames_kept,
            "time_origin_shift_us": self.time_origin_shift_us,
            "missing": self.missing_summary(),
        }


def strip_cli_tail(raw: bytes) -> tuple[bytes, int]:
    """Split the firmware's trailing CLI reply off a short read: (bytes, tail length)."""
    match = _CLI_TAIL.search(raw[-128:])
    if match is None:
        return raw, 0
    tail = len(match.group(0))
    return raw[:-tail], tail


@dataclass(frozen=True)
class _Layout:
    """Per true payload sample: its reference key, frame, and value scale."""

    key: np.ndarray
    scale: np.ndarray
    frame_sample_offsets: np.ndarray  # n_frames + 1
    loop_samples: int
    bytes_per_complex: int

    @property
    def n_samples(self) -> int:
        """Samples in the whole payload."""
        return int(self.frame_sample_offsets[-1])


def _layout(meta: dict) -> _Layout:
    """Key, scale and frame offsets of every payload sample, in true order."""
    cpf, n_rx, n_tx = meta["chirps_per_frame"], meta["n_rx"], meta["n_tx"]
    counts = meta["range_bin_counts"]
    starts = meta["range_bin_starts"]
    iq8 = meta["sample_fmt"] == SAMPLE_RANGE_FFT_IQ8_VARIABLE_TIMED
    max_bin = max(start + count for start, count in zip(starts, counts))
    keys, scales = [], []
    offsets = [0]
    for frame, (start, count) in enumerate(zip(starts, counts)):
        chirp = np.repeat(np.arange(cpf), n_rx * count)
        rx = np.tile(np.repeat(np.arange(n_rx), count), cpf)
        absolute_bin = start + np.tile(np.arange(count), cpf * n_rx)
        keys.append(((chirp % n_tx) * n_rx + rx) * max_bin + absolute_bin)
        scales.append(np.full(chirp.size, float(meta["iq8_scales"][frame]) if iq8 else 1.0))
        offsets.append(offsets[-1] + chirp.size)
    return _Layout(
        key=np.concatenate(keys),
        scale=np.concatenate(scales),
        frame_sample_offsets=np.asarray(offsets),
        loop_samples=n_tx * n_rx * int(counts[0]),
        bytes_per_complex=2 if iq8 else 4,
    )


def _samples(payload: bytes, bytes_per_complex: int) -> np.ndarray:
    """Received payload bytes as complex samples (Im first, as the firmware sends)."""
    usable = len(payload) - len(payload) % bytes_per_complex
    dtype = np.int8 if bytes_per_complex == 2 else np.dtype("<i2")
    iq = np.frombuffer(payload[:usable], dtype=dtype).astype(np.float64)
    return iq[1::2] + 1j * iq[0::2]


def _misfit(values: np.ndarray, refs: np.ndarray) -> np.ndarray:
    return np.abs(values - refs) / (np.abs(values) + np.abs(refs) + 1e-9)


@dataclass
class _Commit:
    """One committed block, with the reference state it replaced (for rollback)."""

    start: int
    stop: int
    shift: int
    keys: np.ndarray
    replaced_value: np.ndarray
    replaced_position: np.ndarray


class _Aligner:
    """Sequential placement of received samples against earlier placed samples per key.

    Each key keeps its two most recent placed values with their true positions, so a
    sample is only ever scored against a sample placed EARLIER in true order (never
    against itself), and a block committed before a gap was noticed can be rolled back.
    """

    def __init__(self, received: np.ndarray, layout: _Layout, deficit_samples: int, quantum: int):
        self.received = received
        self.layout = layout
        self.deficit = deficit_samples
        self.quantum = quantum
        n_keys = int(layout.key.max()) + 1
        self.value = np.zeros((2, n_keys), dtype=complex)  # [latest, previous]
        self.position = np.full((2, n_keys), -1, dtype=np.int64)
        self.log: list[_Commit] = []

    def _window(self, start: int, stop: int, shift: int):
        true = np.arange(start, stop) + shift
        inside = true < self.layout.n_samples
        true = true[inside]
        values = self.received[start:stop][inside] * self.layout.scale[true]
        return true, values, self.layout.key[true]

    def _refs(self, true: np.ndarray, keys: np.ndarray):
        latest = (self.position[0, keys] >= 0) & (self.position[0, keys] < true)
        previous = (self.position[1, keys] >= 0) & (self.position[1, keys] < true)
        refs = np.where(latest, self.value[0, keys], self.value[1, keys])
        return refs, latest | previous

    def misfit(self, start: int, stop: int, shift: int) -> float | None:
        """Median misfit of received [start, stop) placed at ``shift``; None without refs."""
        true, values, keys = self._window(start, stop, shift)
        refs, have = self._refs(true, keys)
        if int(have.sum()) < min(MIN_REFERENCED, max(1, stop - start) // 2):
            return None
        return float(np.median(_misfit(values[have], refs[have])))

    def per_sample(self, start: int, stop: int, shift: int) -> np.ndarray:
        """Per-sample misfit, capped at 1 (0.5, uninformative, where there is no reference)."""
        true, values, keys = self._window(start, stop, shift)
        refs, have = self._refs(true, keys)
        out = np.full(stop - start, 0.5)
        scores = np.full(values.size, 0.5)
        scores[have] = np.minimum(_misfit(values[have], refs[have]), 1.0)
        out[: values.size] = scores
        return out

    def commit(self, start: int, stop: int, shift: int) -> None:
        """Place received [start, stop) at ``shift`` as the newest reference for its keys."""
        if stop <= start:
            return
        true, values, keys = self._window(start, stop, shift)
        self.log.append(
            _Commit(
                start, stop, shift, keys, self.value[:, keys].copy(), self.position[:, keys].copy()
            )
        )
        self.value[1, keys] = self.value[0, keys]
        self.position[1, keys] = self.position[0, keys]
        self.value[0, keys] = values
        self.position[0, keys] = true

    def rollback(self, received_index: int) -> None:
        """Undo every commit of received samples at or after ``received_index``."""
        while self.log and self.log[-1].stop > received_index:
            entry = self.log.pop()
            self.value[:, entry.keys] = entry.replaced_value
            self.position[:, entry.keys] = entry.replaced_position
            if entry.start < received_index:
                self.commit(entry.start, received_index, entry.shift)
                return


@dataclass(frozen=True)
class _Gap:
    """A located gap in received coordinates: [start, stop) is discarded, then ``added``."""

    start: int
    stop: int
    change: int  # the most likely first post-gap sample
    added: int  # samples missing here


def _size_gap(aligner: _Aligner, position: int, shift: int) -> tuple[int | None, str | None]:
    """The shift that fits the data from ``position`` on, or None and why not.

    Tests every remaining whole-unit shift over one loop, widening to four loops
    while the answer is unclear. Returns ``shift`` itself when nothing fits better
    (busy data, not misplaced data).
    """
    n_received = aligner.received.size
    loop = aligner.layout.loop_samples
    candidates = list(range(shift, aligner.deficit + 1, aligner.quantum))
    reason = None
    for loops in (1, 2, 4):
        stop = min(position + loops * loop, n_received)
        scores = [aligner.misfit(position, stop, candidate) for candidate in candidates]
        ranked = sorted((s, c) for s, c in zip(scores, candidates) if s is not None)
        if not ranked:
            return shift, None
        best_score, best = ranked[0]
        if best == shift:
            return shift, None
        stay = scores[0] if scores[0] is not None else 1.0
        runner_up = ranked[1][0] if len(ranked) > 1 else 1.0
        if (
            best_score <= WINDOW_MISFIT_MAX
            and stay - best_score >= GAP_MARGIN
            and runner_up - best_score >= UNIQUE_MARGIN
        ):
            return best, None
        reason = (
            f"best misfit {best_score:.2f}, staying {stay:.2f}, next {runner_up:.2f} "
            f"over {loops} loop(s)"
        )
        if stop == n_received:
            break
    return None, reason


def _change_point(aligner: _Aligner, lo: int, hi: int, old: int, new: int) -> tuple[int, int, int]:
    """Where received samples stop fitting ``old`` and start fitting ``new``.

    Returns (first uncertain, most likely, last uncertain + 1): every split whose
    cost is within ``CHANGE_POINT_SLACK`` of the best is treated as possible.
    """
    first, change, last = _split(aligner.per_sample(lo, hi, old), aligner.per_sample(lo, hi, new))
    return lo + first, lo + change, lo + last


def _split(before: np.ndarray, after: np.ndarray) -> tuple[int, int, int]:
    """(first possible, most likely, last possible) split of before[:g] + after[g:]."""
    cost = np.concatenate(([0.0], np.cumsum(before))) + np.concatenate(
        (np.cumsum(after[::-1])[::-1], [0.0])
    )
    possible = np.nonzero(cost <= cost.min() + CHANGE_POINT_SLACK)[0]
    return int(possible.min()), int(np.argmin(cost)), int(possible.max())


def _start_gap(aligner: _Aligner) -> tuple[_Gap | None, str | None]:
    """A gap inside the first two loops, found by comparing each loop with the next.

    Nothing precedes the first loop, so it has no reference: a sample placed there is
    checked against the same key one loop LATER. Before an early gap at g the two
    disagree (one side is pre-gap, the other post-gap); after it they agree. A gap
    starting within about the first 16 samples (64 bytes) of the payload leaves too
    few disagreeing samples to see; that residual blind spot is reported, not hidden.
    """
    received, layout = aligner.received, aligner.layout
    loop = layout.loop_samples
    if received.size < 2 * loop:
        return None, None
    forward = np.minimum(_misfit(received[:loop], received[loop : 2 * loop]), 1.0)

    def pair(indices: np.ndarray, refs: np.ndarray, added: int) -> np.ndarray:
        values = received[indices] * layout.scale[indices]
        return np.minimum(_misfit(values, received[refs] * layout.scale[refs + added]), 1.0)

    rise = np.concatenate(([0.0], np.cumsum(forward - START_MISFIT)))
    edge = int(np.argmax(rise))
    if rise[edge] < START_EVIDENCE:
        return None, None
    # size it: samples before the edge are pre-gap and match the same key one or more
    # loops later in the post-gap data
    indices = np.arange(edge)

    def pre_gap_refs(added: int) -> tuple[np.ndarray, np.ndarray]:
        loops_on = np.maximum(1, -(-(edge + added - indices) // loop))
        refs = indices + loops_on * loop - added
        usable = refs < received.size
        usable[usable] &= (
            layout.key[indices[usable]]
            == layout.key[np.minimum(refs[usable] + added, layout.n_samples - 1)]
        )
        return refs, usable

    candidates = list(range(0, aligner.deficit + 1, aligner.quantum))
    scores = []
    for added in candidates:
        refs, usable = pre_gap_refs(added)
        if int(usable.sum()) < max(8, edge // 2):
            scores.append(None)
            continue
        scores.append(float(np.median(pair(indices[usable], refs[usable], added))))
    ranked = sorted((s, c) for s, c in zip(scores, candidates) if s is not None)
    if not ranked or ranked[0][1] == 0:
        return None, None
    best_score, best = ranked[0]
    stay = scores[0] if scores[0] is not None else 1.0
    runner_up = ranked[1][0] if len(ranked) > 1 else 1.0
    if (
        best_score > WINDOW_MISFIT_MAX
        or stay - best_score < GAP_MARGIN
        or runner_up - best_score < UNIQUE_MARGIN
    ):
        return None, (
            f"a gap in the first loop could not be sized (best misfit {best_score:.2f}, "
            f"staying {stay:.2f}, next {runner_up:.2f})"
        )
    span = min(loop, edge + 2 * aligner.quantum)
    indices = np.arange(span)
    refs, usable = pre_gap_refs(best)
    before = np.full(span, 0.5)
    before[usable] = pair(indices[usable], refs[usable], best)
    after = forward[:span]
    first, change, last = _split(before, after)
    return (
        _Gap(
            max(0, first - CHANGE_POINT_MARGIN),
            min(received.size, last + CHANGE_POINT_MARGIN),
            change,
            best,
        ),
        None,
    )


def _locate(received: np.ndarray, layout: _Layout, deficit_samples: int, quantum: int):
    """Segments [(received start, received stop, shift)], the located gaps, a stop reason."""
    aligner = _Aligner(received, layout, deficit_samples, quantum)
    n_received = received.size
    block = quantum
    segments: list[list[int]] = []
    gaps: list[_Gap] = []
    shift = 0
    position = 0
    segment_start = 0
    early, reason = _start_gap(aligner)
    if reason is not None:
        return [], [], reason
    if early is not None:
        segments.append([0, early.start, 0])
        aligner.commit(0, early.start, 0)
        gaps.append(early)
        shift = early.added
        segment_start = position = early.stop
    while position < n_received:
        stop = min(position + block, n_received)
        score = aligner.misfit(position, stop, shift)
        if score is None or score <= BLOCK_MISFIT_OK or shift >= deficit_samples:
            aligner.commit(position, stop, shift)
            position = stop
            continue
        best, reason = _size_gap(aligner, position, shift)
        if best is None:
            segments.append([segment_start, position, shift])
            byte = (position + shift) * layout.bytes_per_complex
            return segments, gaps, f"a gap near payload byte {byte} could not be sized: {reason}"
        if best == shift:
            aligner.commit(position, stop, shift)
            position = stop
            continue
        # the gap may predate the block that first misfit: widen until the
        # uncertain span no longer touches the search's start; samples committed
        # in the searched span are rolled back so none is scored against itself
        lo = position
        while True:
            lo = max(segment_start, lo - 4 * block)
            aligner.rollback(lo)
            hi = min(n_received, position + 2 * block)
            first, change, last = _change_point(aligner, lo, hi, shift, best)
            if first > lo or lo == segment_start:
                break
        discard_start = max(segment_start, first - CHANGE_POINT_MARGIN)
        discard_stop = min(n_received, last + CHANGE_POINT_MARGIN)
        aligner.rollback(discard_start)
        aligner.commit(lo, discard_start, shift)
        segments.append([segment_start, discard_start, shift])
        gaps.append(_Gap(discard_start, discard_stop, change, best - shift))
        shift = best
        segment_start = discard_stop
        position = segment_start
    segments.append([segment_start, n_received, shift])
    return segments, gaps, None


def _placed_cube(received, layout, segments):
    """True-order samples and a placed mask from the segments."""
    values = np.zeros(layout.n_samples, dtype=complex)
    placed = np.zeros(layout.n_samples, dtype=bool)
    for start, stop, shift in segments:
        if stop <= start:
            continue
        true = np.arange(start, stop) + shift
        inside = true < layout.n_samples
        values[true[inside]] = received[start:stop][inside] * layout.scale[true[inside]]
        placed[true[inside]] = True
    return values, placed


def _frame_consistent(values: np.ndarray, layout: _Layout, frame: int) -> bool:
    """Whether a fully placed frame's loops repeat each other as a real frame's do."""
    lo, hi = layout.frame_sample_offsets[frame], layout.frame_sample_offsets[frame + 1]
    loop = layout.loop_samples
    frame_values = values[lo:hi]
    if frame_values.size < 2 * loop:
        return True
    misfit = _misfit(frame_values[loop:], frame_values[:-loop])
    return float(np.median(misfit)) <= FRAME_MISFIT_MAX


def _anchor_failure(values, placed, layout: _Layout, starts) -> str | None:
    """Why the placement's absolute alignment cannot be trusted, or None.

    Loop-to-loop agreement only fixes shifts relative to each other: a run lost in
    the payload's first few samples moves EVERY later sample by the same whole
    units and leaves them agreeing. The frames where the range window moves are
    the anchor: if everything sits a few units late, the last samples of the frame
    before a window change are really the next frame's, in other bins, and stop
    agreeing with the loop before them. Every window change whose two closing loops
    were placed is checked; at least one must be, and all must pass.
    """
    loop = layout.loop_samples
    changes = [frame for frame in range(1, len(starts)) if starts[frame] != starts[frame - 1]]
    if not changes:
        return "no window change in this capture to verify the placement against"
    checked = 0
    for frame in changes:
        end = int(layout.frame_sample_offsets[frame])
        if (
            end - 2 * loop < layout.frame_sample_offsets[frame - 1]
            or not placed[end - 2 * loop : end].all()
        ):
            continue
        misfit = np.minimum(
            _misfit(values[end - loop : end], values[end - 2 * loop : end - loop]), 1.0
        )
        rise = np.cumsum((misfit - START_MISFIT)[::-1])
        if rise.max() >= ANCHOR_EVIDENCE:
            return (
                f"the placement fails the window change at frame {frame}: the closing samples "
                "of the frame before it disagree with the loop before them, so every sample "
                "may sit late by a run lost at the start of the payload"
            )
        checked += 1
    if not checked:
        return "every window change fell in a gap, so the placement cannot be verified"
    return None


class _Refused(ValueError):
    """A partial dump that cannot be decoded, with what is known about it."""

    def __init__(self, reason: str, expected: int | None = None, lost: int | None = None):
        super().__init__(reason)
        self.expected = expected
        self.lost = lost


def _read(body: bytes, quantum_bytes: int) -> tuple[dict, int]:
    """The header and frame table of a short transfer, and the size it declares."""
    try:
        meta = parse_header(body)
        _parse_frame_metadata(body, meta)
        expected = meta["header_nbytes"] + payload_nbytes(meta, body)
    except (ValueError, KeyError, IndexError) as error:
        raise _Refused(f"the header or frame table is unreadable: {error}") from error
    if meta["sample_fmt"] not in _SUPPORTED_FORMATS:
        raise _Refused(f"sample format {meta['sample_fmt']} is not a timed snapshot", expected)
    lost = expected - len(body)
    if lost < 0:
        raise _Refused("more bytes than the header declares", expected)
    if lost % quantum_bytes:
        raise _Refused(
            f"{lost} bytes are missing, not a whole number of {quantum_bytes}-byte units, so "
            "the runs cannot be sized",
            expected,
            lost,
        )
    return meta, expected


def _split_trailer(meta: dict, payload: bytes, lost: int) -> tuple[bytes, bytes, int]:
    """(payload without the trailer, the trailer if it arrived, payload bytes lost)."""
    if not meta.get("trailer_nbytes"):
        return payload, b"", lost
    if payload[-DUMP_TRAILER.size :][:4] == DUMP_TRAILER_MAGIC:
        return payload[: -DUMP_TRAILER.size], payload[-DUMP_TRAILER.size :], lost
    return payload, b"", lost - DUMP_TRAILER.size


def _kept_frames(values, placed, layout: _Layout) -> list[int]:
    """Frames every sample of which was placed, whose loops agree."""
    kept = []
    for frame in range(len(layout.frame_sample_offsets) - 1):
        lo, hi = layout.frame_sample_offsets[frame], layout.frame_sample_offsets[frame + 1]
        if placed[lo:hi].all() and _frame_consistent(values, layout, frame):
            kept.append(frame)
    return kept


def _lost_runs(gaps: list[_Gap], bytes_per_complex: int) -> tuple[LostRun, ...]:
    """Located gaps in true payload bytes."""
    runs, prior = [], 0
    for gap in gaps:
        runs.append(
            LostRun(
                payload_offset=(gap.change + prior) * bytes_per_complex,
                nbytes=gap.added * bytes_per_complex,
                guard_nbytes=(gap.stop - gap.start) * bytes_per_complex,
            )
        )
        prior += gap.added
    return tuple(runs)


def recover_partial_dump(
    raw: bytes, *, quantum_bytes: int = LOSS_QUANTUM_BYTES
) -> PartialDumpRecovery:
    """Decode a short transfer into its intact frames; see the module docstring."""
    body, tail = strip_cli_tail(raw)
    try:
        meta, expected = _read(body, quantum_bytes)
    except _Refused as refused:
        return PartialDumpRecovery(
            status="unrecoverable",
            reason=str(refused),
            received_nbytes=len(raw),
            expected_nbytes=refused.expected,
            lost_nbytes=refused.lost,
            cli_tail_nbytes=tail,
        )
    n_frames = meta["n_frames"]
    lost = expected - len(body)
    if lost == 0:
        return PartialDumpRecovery(
            status="complete",
            reason=None,
            received_nbytes=len(raw),
            expected_nbytes=expected,
            lost_nbytes=0,
            cli_tail_nbytes=tail,
            n_frames=n_frames,
            frames_kept=tuple(range(n_frames)),
            raw=body,
        )
    payload, trailer, payload_lost = _split_trailer(
        meta, body[meta["header_nbytes"] + meta["frame_metadata_nbytes"] :], lost
    )
    layout = _layout(meta)
    bpc = layout.bytes_per_complex
    received = _samples(payload, bpc)
    segments, gaps, stop_reason = _locate(
        received, layout, payload_lost // bpc, quantum_bytes // bpc
    )
    values, placed = _placed_cube(received, layout, segments)
    kept = _kept_frames(values, placed, layout)
    unanchored = _anchor_failure(values, placed, layout, meta["range_bin_starts"])
    if unanchored is not None:
        stop_reason, kept = unanchored, []
    placed_samples = sum(max(0, stop - start) for start, stop, _ in segments)
    result = PartialDumpRecovery(
        status="recovered" if kept else "unrecoverable",
        reason=stop_reason if kept else (stop_reason or "no frame survived intact"),
        received_nbytes=len(raw),
        expected_nbytes=expected,
        lost_nbytes=lost,
        cli_tail_nbytes=tail,
        runs=_lost_runs(gaps, bpc),
        tail_lost_nbytes=0 if stop_reason else payload_lost - segments[-1][2] * bpc,
        unplaced_nbytes=len(payload) - placed_samples * bpc,
        n_frames=n_frames,
        frames_kept=tuple(kept),
        frames_dropped=tuple(frame for frame in range(n_frames) if frame not in kept),
    )
    if kept:
        result.time_origin_shift_us = int(meta["frame_time_offsets_us"][kept[0]])
        result.raw = _repack(meta, values, layout, kept, trailer)
    return result


def select_frames(raw: bytes, frames) -> bytes:
    """A complete timed dump cut down to ``frames`` (ascending), its time origin rebased.

    The same repack a recovered partial dump gets; offline it measures how an
    estimator responds to losing frames when nothing else is missing.
    """
    meta, cube = parse_dump(raw)
    if meta["sample_fmt"] not in _SUPPORTED_FORMATS:
        raise ValueError(f"sample format {meta['sample_fmt']} is not a timed snapshot")
    frames = [int(frame) for frame in frames]
    if not frames or frames != sorted(set(frames)) or not 0 <= frames[0] <= frames[-1] < len(cube):
        raise ValueError(f"frames must be ascending, distinct and within 0-{len(cube) - 1}")
    values = np.concatenate(
        [
            cube[frame, ..., :count].reshape(-1)
            for frame, count in enumerate(meta["range_bin_counts"])
        ]
    )
    dump_trailer = meta.get("dump_trailer") or {}
    trailer = (
        DUMP_TRAILER.pack(DUMP_TRAILER_MAGIC, dump_trailer["payload_end_tick"])
        if dump_trailer
        else b""
    )
    return _repack(meta, values, _layout(meta), frames, trailer)


def _repack(meta: dict, values: np.ndarray, layout: _Layout, kept: list[int], trailer: bytes):
    """The kept frames as an ordinary dump of the same version and format."""
    cpf, n_rx, n_samples = meta["chirps_per_frame"], meta["n_rx"], meta["n_samples"]
    cube = np.zeros((len(kept), cpf, n_rx, n_samples), dtype=complex)
    for index, frame in enumerate(kept):
        lo, hi = layout.frame_sample_offsets[frame], layout.frame_sample_offsets[frame + 1]
        count = meta["range_bin_counts"][frame]
        cube[index, ..., :count] = values[lo:hi].reshape(cpf, n_rx, count)
    origin = meta["frame_time_offsets_us"][kept[0]]
    version = meta["version"]
    capture_evidence = frame_evidence = dump_trailer = None
    if meta.get("evidence_nbytes") and trailer:
        capture_evidence = dict(meta["capture_evidence"])
        pre = capture_evidence["pre_frames"]
        capture_evidence["pre_frames"] = sum(1 for frame in kept if frame < pre)
        capture_evidence["post_frames"] = len(kept) - capture_evidence["pre_frames"]
        frame_evidence = [meta["frame_evidence"][frame] for frame in kept]
        dump_trailer = {"payload_end_tick": DUMP_TRAILER.unpack(trailer)[1]}
    elif meta.get("evidence_nbytes"):
        # the trailer was lost and no tick may be invented for it: the repack is the
        # same layout without evidence (8 -> 7 keeps the temperature report, 9 -> 6)
        version = _VERSION_WITHOUT_EVIDENCE[version]
    kept_starts = [meta["range_bin_starts"][frame] for frame in kept]
    kept_counts = [meta["range_bin_counts"][frame] for frame in kept]
    packed = pack_dump(
        cube,
        n_tx=meta["n_tx"],
        trigger_frame=meta["trigger_frame"],
        version=version,
        frame_period_us=meta.get("frame_period_us", 0),
        sample_fmt=meta["sample_fmt"],
        range_bin_starts=kept_starts,
        range_bin_counts=kept_counts,
        frame_time_offsets_us=[meta["frame_time_offsets_us"][frame] - origin for frame in kept],
        temperature_report=meta.get("temperature_report") if version != 6 else None,
        clutter_map=meta.get("clutter_map"),
        capture_evidence=capture_evidence,
        frame_evidence=frame_evidence,
        dump_trailer=dump_trailer,
    )
    if meta["sample_fmt"] != SAMPLE_RANGE_FFT_IQ8_VARIABLE_TIMED:
        return packed  # int16 values round-trip exactly
    # pack_dump picks its own int8 scale; put back the firmware's scales and int8 bytes
    scales = np.asarray([meta["iq8_scales"][frame] for frame in kept], dtype="<u2")
    iq8 = []
    for index, frame in enumerate(kept):
        flat = cube[index, ..., : kept_counts[index]].reshape(-1) / scales[index]
        pairs = np.empty(flat.size * 2, dtype=np.int8)
        pairs[0::2] = np.round(flat.imag).astype(np.int8)
        pairs[1::2] = np.round(flat.real).astype(np.int8)
        iq8.append(pairs.tobytes())
    out = parse_header(packed)
    scales_at = out["header_nbytes"] + TIMED_FRAME_DESCRIPTOR.size * len(kept)
    payload_at = out["header_nbytes"] + out["frame_metadata_nbytes"]
    body = b"".join(iq8)
    return (
        packed[:scales_at]
        + scales.tobytes()
        + packed[scales_at + scales.nbytes : payload_at]
        + body
        + packed[payload_at + len(body) :]
    )
