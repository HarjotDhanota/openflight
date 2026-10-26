"""The two-round IWR6843 transfer: wire formats, both ends, and the host's rounds.

After a shot the radar holds its frozen 24-frame ring as one **session** until the host
releases it: freeze -> ``overview`` -> ``cells`` (once or more) -> ``release``.

* **Round 1, ``overview``:** the whole window in summary. The dump's own header,
  per-frame metadata and evidence (timed descriptors, ``ILE1`` block, frame records) and
  its ``ILT1`` trailer, verbatim; exact int32 sums per absolute bin, TX (1, 3) and RX of
  every sample; a 16-bit log power map per loop (TX1+TX3 MTI power summed over the
  8 channels) in both MTI scopes; the two scopes' median MTI power.
* **Host:** the whole kiosk path runs on round 1 alone (``sparse_capture``) and records
  the (frame, bin) cells it reads.
* **Round 2+, ``cells <request>``:** the host sends those cells as per-frame bin runs;
  the radar answers with each cell's IQ16 samples. The host runs the kiosk path again
  on the rebuilt capture; a cell it reads that was not sent is the next request.
* **``release <session>``:** the radar rearms only on this acknowledged command, or on
  its logged hold timeout (a host that crashed or lost the link never leaves the ring
  frozen for good).

Wire layout (frozen; ``firmware/iwr6843/two_round_format.h`` will mirror it): every
integer little-endian; every binary payload starts with its fixed header, declares its
total length (header to CRC inclusive) and ends with a CRC-32 (IEEE 802.3, as
``zlib.crc32``) over every byte before it. A payload is followed on the UART by
``Done\\n`` and the CLI prompt; an error is a text line starting ``Error`` instead.
"""

from __future__ import annotations

import logging
import struct
import time
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

import numpy as np

from openflight.iwr6843.dump import (
    SAMPLE_RANGE_FFT_IQ16_VARIABLE,
    SAMPLE_RANGE_FFT_IQ16_VARIABLE_TIMED,
    parse_dump,
    parse_header,
    payload_nbytes,
)
from openflight.iwr6843.sparse_capture import MTI_SCOPES, SparseDump, SparseEvidence

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- the contract
# Every magic, version and command name of the protocol, in one place. ILO1/ILC1 v1 are
# reserved for stage 2 in the firmware coordination (2026-10-26) beside ILD1 (dump),
# ILE1/ILT1 (evidence, trailer) and OFC2 (compact clock). The request magic ILQ1 and the
# text replies are PROVISIONAL: not yet in the firmware's format registry.
OVERVIEW_MAGIC = b"ILO1"
OVERVIEW_VERSION = 1
CELLS_MAGIC = b"ILC1"
CELLS_VERSION = 1
REQUEST_MAGIC = b"ILQ1"  # PROVISIONAL
REQUEST_VERSION = 1  # PROVISIONAL
CMD_OVERVIEW = "overview"  # CLI slot 15
CMD_CELLS = "cells"  # CLI slot 16; argument: the request in hex
CMD_RELEASE = "release"  # CLI slot 17; argument: the session id, 8 hex digits
RELEASED_REPLY = b"released"  # PROVISIONAL: "released <session>" acknowledges a release
CLI_DONE = b"Done\n"
CLI_PROMPT = b"l3dump:/>"
CLI_ERROR = b"Error"

# overview flags
FLAG_PREVIOUS_SESSION_TIMED_OUT = 0x0001  # the last session was rearmed by the hold timeout
# power map coding: code = round(LOG16_CODES_PER_DECADE * log10(max(power, 1))), 1/500 dB
POWER_CODE_LOG16 = 1
LOG16_CODES_PER_DECADE = 5000
LOG16_MAX_CODE = 0xFFFF

# Overview header (44 B): magic, version, header bytes, total bytes, session id, frames,
# chirps per frame, TX, RX, bins per frame (max), summed bins, map cells (sum of the
# per-frame bin counts), map code, scopes, burst noise, window noise, dump prefix bytes,
# dump suffix bytes, flags. Body: dump prefix (header + per-frame metadata + evidence),
# dump suffix (trailer), int32 sums [summed bin][TX1, TX3][RX][re, im] in ascending
# absolute bin, uint16 map [burst, window][frame][loop][local bin < count], CRC-32.
OVERVIEW_HEADER = struct.Struct("<4sHHIIHHBBHHHBBffHHH")
# Request (12 B): magic, version, request sequence (1, 2, ...), frames, session id.
# Body per frame: run count (u8), then (first local bin, length) u8 pairs; CRC-32.
REQUEST_HEADER = struct.Struct("<4sBBHI")
# Cells header (28 B): magic, version, header bytes, total bytes, session id, request
# sequence, reserved, cell count, the request's CRC-32, bytes per cell. Body: each cell
# in request order (frame ascending, bin ascending) as [chirp][RX] int16 (Im, Re) pairs,
# the dump's own sample order; CRC-32.
CELLS_HEADER = struct.Struct("<4sHHIIBBHII")
CRC = struct.Struct("<I")

N_LOOP_TX = 3  # the protocol carries 3-TX TDM captures (TX1, TX2, TX3 per loop)
SUMMED_TX = (0, 2)  # TX1 and TX3: the vertical pair the trackers use
IQ16_FORMATS = (SAMPLE_RANGE_FFT_IQ16_VARIABLE, SAMPLE_RANGE_FFT_IQ16_VARIABLE_TIMED)
MAX_CELL_ROUNDS = 4  # cells rounds before the host asks for every remaining cell


class TwoRoundError(ValueError):
    """A two-round payload, request or reply that is malformed or does not belong."""


class TwoRoundRemoteError(TwoRoundError):
    """The radar answered a command with an error line."""


# --------------------------------------------------------------------------- helpers


def _crc_ok(data: bytes) -> bool:
    return (
        len(data) >= CRC.size and zlib.crc32(data[: -CRC.size]) == CRC.unpack(data[-CRC.size :])[0]
    )


def _seal(body: bytes) -> bytes:
    return body + CRC.pack(zlib.crc32(body))


def _check_length(data: bytes, header: struct.Struct, magic: bytes, what: str) -> tuple:
    if len(data) < header.size:
        raise TwoRoundError(f"{what} truncated: {len(data)} bytes < {header.size}-byte header")
    fields = header.unpack_from(data, 0)
    if fields[0] != magic:
        raise TwoRoundError(f"{what}: bad magic {fields[0]!r} (expected {magic!r})")
    return fields


def _layout(meta: dict) -> tuple[int, int]:
    """(payload offset, sample bytes) of a dump's IQ16 sample block."""
    offset = meta["header_nbytes"] + (meta.get("frame_metadata_nbytes") or 0)
    samples = sum(meta["range_bin_counts"]) * meta["chirps_per_frame"] * meta["n_rx"] * 4
    return offset, samples


def _check_capture(meta: dict) -> None:
    if meta["sample_fmt"] not in IQ16_FORMATS:
        raise TwoRoundError(
            f"two-round transfer needs a variable IQ16 dump, not {meta['sample_fmt']}"
        )
    if meta["n_tx"] != N_LOOP_TX or meta["chirps_per_frame"] % N_LOOP_TX:
        raise TwoRoundError(f"two-round transfer needs a 3-TX capture, not {meta['n_tx']} TX")
    if meta["n_samples"] > 255:
        raise TwoRoundError(f"{meta['n_samples']} bins per frame do not fit a one-byte run")


def held_bins(meta: dict) -> np.ndarray:
    """The absolute range bins any frame holds, ascending (the summed bins)."""
    held = set()
    for start, count in zip(meta["range_bin_starts"], meta["range_bin_counts"]):
        held.update(range(start, start + count))
    return np.array(sorted(held), dtype=int)


def log16_encode(power: np.ndarray) -> np.ndarray:
    """Per-loop power -> the overview's 16-bit log code (1/500 dB, 0 dB = power 1)."""
    code = np.round(LOG16_CODES_PER_DECADE * np.log10(np.maximum(power, 1.0)))
    return np.clip(code, 0, LOG16_MAX_CODE).astype("<u2")


def log16_decode(code: np.ndarray) -> np.ndarray:
    """The 16-bit log code -> per-loop power as the host uses it."""
    return 10.0 ** (np.asarray(code, dtype=float) / LOG16_CODES_PER_DECADE)


def _integer_tdm(raw: bytes) -> tuple[dict, np.ndarray, np.ndarray]:
    """A dump's samples as int64 (re, im) [frame, loop, tx, rx, bin], as the ring holds them."""
    meta, cube = parse_dump(bytes(raw))
    _check_capture(meta)
    nf, cpf, nrx, ns = cube.shape
    shape = (nf, cpf // N_LOOP_TX, N_LOOP_TX, nrx, ns)
    real = np.rint(cube.real).astype(np.int64).reshape(shape)
    imag = np.rint(cube.imag).astype(np.int64).reshape(shape)
    return meta, real, imag


# --------------------------------------------------------------------------- overview


@dataclass(frozen=True)
class Overview:
    """Round 1 as the host decodes it."""

    session_id: int
    flags: int
    meta: dict
    prefix: bytes
    suffix: bytes
    # [absolute bin, TX1/TX3, RX, (re, im)] int64; zero for bins no frame holds
    sums: np.ndarray
    power_maps: dict[str, np.ndarray]
    noise_power: dict[str, float]
    nbytes: int


def build_overview(raw: bytes, *, session_id: int, flags: int = 0) -> bytes:
    """Round 1 as the radar computes it from its frozen ring (integer sums, exact medians).

    MTI power is formed exactly in integers: burst scope d = L*x - sum over a frame's
    L loops, window scope d = N*x - sum over the N samples of that absolute bin across
    the window; power |d|^2 / L^2 (or N^2). Medians are exact order statistics of those
    values; the map is coded by ``log16_encode``.
    """
    meta, real, imag = _integer_tdm(raw)
    nf, loops, _ntx, nrx, ns = real.shape
    starts, counts = meta["range_bin_starts"], meta["range_bin_counts"]
    offset, sample_nbytes = _layout(meta)
    declared = meta["header_nbytes"] + payload_nbytes(meta, raw)
    prefix = bytes(raw[:offset])
    suffix = bytes(raw[offset + sample_nbytes : declared])
    vre, vim = real[:, :, SUMMED_TX], imag[:, :, SUMMED_TX]  # [f, loop, 2, rx, bin]

    n_abs = max(s + c for s, c in zip(starts, counts))
    sums = np.zeros((n_abs, 2, nrx, 2), dtype=np.int64)
    holders = np.zeros(n_abs, dtype=np.int64)
    for frame, (start, count) in enumerate(zip(starts, counts)):
        sums[start : start + count, ..., 0] += (
            vre[frame, ..., :count].sum(axis=0).transpose(2, 0, 1)
        )
        sums[start : start + count, ..., 1] += (
            vim[frame, ..., :count].sum(axis=0).transpose(2, 0, 1)
        )
        holders[start : start + count] += loops
    held = held_bins(meta)
    if np.abs(sums).max(initial=0) >= 2**31:
        raise TwoRoundError("a bin sum overflows int32")

    maps: dict[str, list[np.ndarray]] = {scope: [] for scope in MTI_SCOPES}
    elements: dict[str, list[np.ndarray]] = {scope: [] for scope in MTI_SCOPES}
    for frame, (start, count) in enumerate(zip(starts, counts)):
        xre, xim = vre[frame, ..., :count], vim[frame, ..., :count]
        burst_re = loops * xre - xre.sum(axis=0, keepdims=True)
        burst_im = loops * xim - xim.sum(axis=0, keepdims=True)
        burst = (burst_re**2 + burst_im**2) / float(loops**2)
        n_win = holders[start : start + count]
        win_re = n_win * xre - sums[start : start + count, :, :, 0].transpose(1, 2, 0)
        win_im = n_win * xim - sums[start : start + count, :, :, 1].transpose(1, 2, 0)
        window = (win_re**2 + win_im**2) / (n_win.astype(float) ** 2)
        for scope, power in (("burst", burst), ("window", window)):
            maps[scope].append(log16_encode(power.sum(axis=(1, 2))).reshape(-1))
            elements[scope].append(power.reshape(-1))
    noise = [float(np.median(np.concatenate(elements[scope]))) for scope in MTI_SCOPES]

    n_map_cells = int(sum(counts))
    body = b"".join(
        [
            prefix,
            suffix,
            sums[held].astype("<i4").tobytes(),
            *(np.concatenate(maps[scope]).tobytes() for scope in MTI_SCOPES),
        ]
    )
    total = OVERVIEW_HEADER.size + len(body) + CRC.size
    header = OVERVIEW_HEADER.pack(
        OVERVIEW_MAGIC,
        OVERVIEW_VERSION,
        OVERVIEW_HEADER.size,
        total,
        session_id,
        nf,
        meta["chirps_per_frame"],
        N_LOOP_TX,
        nrx,
        ns,
        len(held),
        n_map_cells,
        POWER_CODE_LOG16,
        len(MTI_SCOPES),
        *noise,
        len(prefix),
        len(suffix),
        flags,
    )
    return _seal(header + body)


def parse_overview(  # pylint: disable=too-many-locals,too-many-branches
    data: bytes, *, session_id: int | None = None
) -> Overview:
    """Decode and check round 1: length, CRC, dimensions against its own dump header."""
    fields = _check_length(data, OVERVIEW_HEADER, OVERVIEW_MAGIC, "overview")
    (
        _magic,
        version,
        header_nbytes,
        total,
        session,
        nf,
        cpf,
        ntx,
        nrx,
        ns,
        n_sum_bins,
        n_map_cells,
        map_code,
        n_scopes,
        noise_burst,
        noise_window,
        prefix_nbytes,
        suffix_nbytes,
        flags,
    ) = fields
    if version != OVERVIEW_VERSION or header_nbytes != OVERVIEW_HEADER.size:
        raise TwoRoundError(f"overview version {version}/{header_nbytes} B is not supported")
    if len(data) < total:
        raise TwoRoundError(f"overview truncated: {len(data)} of {total} bytes")
    if len(data) > total:
        raise TwoRoundError(f"overview has {len(data) - total} bytes past its declared end")
    if not _crc_ok(data):
        raise TwoRoundError("overview CRC mismatch")
    if session_id is not None and session != session_id:
        raise TwoRoundError(f"overview of session {session:#010x}, expected {session_id:#010x}")
    if map_code != POWER_CODE_LOG16 or n_scopes != len(MTI_SCOPES):
        raise TwoRoundError(f"overview map code {map_code} x {n_scopes} scopes is not supported")
    loops = cpf // max(ntx, 1)
    expected = (
        OVERVIEW_HEADER.size
        + prefix_nbytes
        + suffix_nbytes
        + n_sum_bins * 2 * nrx * 2 * 4
        + n_scopes * n_map_cells * loops * 2
        + CRC.size
    )
    if expected != total:
        raise TwoRoundError(f"overview declares {total} bytes but its sections need {expected}")
    at = OVERVIEW_HEADER.size
    prefix = bytes(data[at : at + prefix_nbytes])
    at += prefix_nbytes
    suffix = bytes(data[at : at + suffix_nbytes])
    at += suffix_nbytes
    try:
        meta = parse_header(prefix)
        payload_nbytes(meta, prefix)
    except (ValueError, IndexError, KeyError, struct.error) as error:
        raise TwoRoundError(f"overview's dump header does not parse: {error}") from error
    _check_capture(meta)
    dims = (
        meta["n_frames"],
        meta["chirps_per_frame"],
        meta["n_tx"],
        meta["n_rx"],
        meta["n_samples"],
    )
    if dims != (nf, cpf, ntx, nrx, ns):
        raise TwoRoundError(
            f"overview dimensions {(nf, cpf, ntx, nrx, ns)} differ from its dump header {dims}"
        )
    if prefix_nbytes != _layout(meta)[0] or suffix_nbytes != (meta.get("trailer_nbytes") or 0):
        raise TwoRoundError("overview's dump prefix or suffix length does not match its header")
    held = held_bins(meta)
    if len(held) != n_sum_bins or sum(meta["range_bin_counts"]) != n_map_cells:
        raise TwoRoundError("overview's summed bins or map cells do not match its dump header")
    flat = np.frombuffer(data, dtype="<i4", offset=at, count=n_sum_bins * 2 * nrx * 2)
    at += flat.nbytes
    sums = np.zeros((int(held.max()) + 1, 2, nrx, 2), dtype=np.int64)
    sums[held] = flat.reshape(n_sum_bins, 2, nrx, 2)
    maps = {}
    counts = meta["range_bin_counts"]
    for scope in MTI_SCOPES:
        codes = np.frombuffer(data, dtype="<u2", offset=at, count=n_map_cells * loops)
        at += codes.nbytes
        power = np.zeros((nf * loops, ns))
        cursor = 0
        for frame, count in enumerate(counts):
            block = codes[cursor : cursor + loops * count].reshape(loops, count)
            power[frame * loops : (frame + 1) * loops, :count] = log16_decode(block)
            cursor += loops * count
        maps[scope] = power
    return Overview(
        session_id=session,
        flags=flags,
        meta=meta,
        prefix=prefix,
        suffix=suffix,
        sums=sums,
        power_maps=maps,
        noise_power={"burst": float(noise_burst), "window": float(noise_window)},
        nbytes=total,
    )


# --------------------------------------------------------------------------- request


@dataclass(frozen=True)
class CellRequest:
    """One ``cells`` request: which (frame, local bin) cells, for which session."""

    session_id: int
    sequence: int
    mask: np.ndarray
    data: bytes

    @property
    def crc(self) -> int:
        """The request's CRC-32, echoed by the radar's reply."""
        return CRC.unpack(self.data[-CRC.size :])[0]

    @property
    def n_cells(self) -> int:
        """Cells requested."""
        return int(self.mask.sum())

    def command(self) -> bytes:
        """The CLI line that sends it."""
        return f"{CMD_CELLS} {self.data.hex()}\n".encode("ascii")


def _runs(row: np.ndarray) -> list[tuple[int, int]]:
    edges = np.diff(np.concatenate(([0], row.astype(np.int8), [0])))
    firsts = np.flatnonzero(edges == 1)
    lasts = np.flatnonzero(edges == -1)
    return [(int(a), int(b - a)) for a, b in zip(firsts, lasts)]


def encode_request(
    mask: np.ndarray, *, session_id: int, sequence: int, range_bin_counts
) -> CellRequest:
    """The cells in ``mask`` [frame, local bin] as per-frame runs of bins."""
    mask = np.asarray(mask, dtype=bool)
    counts = tuple(range_bin_counts)
    if mask.shape[0] != len(counts):
        raise TwoRoundError(f"request mask has {mask.shape[0]} frames, capture has {len(counts)}")
    if not 1 <= sequence <= 255:
        raise TwoRoundError(f"request sequence {sequence} is outside 1-255")
    body = bytearray(
        REQUEST_HEADER.pack(REQUEST_MAGIC, REQUEST_VERSION, sequence, len(counts), session_id)
    )
    for frame, count in enumerate(counts):
        if mask[frame, count:].any():
            raise TwoRoundError(f"request frame {frame} asks for a bin beyond its {count}")
        runs = _runs(mask[frame, :count])
        if len(runs) > 255:
            raise TwoRoundError(f"request frame {frame} has {len(runs)} runs")
        body.append(len(runs))
        for first, length in runs:
            body += bytes((first, length))
    data = _seal(bytes(body))
    return CellRequest(session_id, sequence, mask.copy(), data)


def decode_request(data: bytes, *, range_bin_counts, n_samples: int) -> CellRequest:
    """The radar's side: a request's cells, checked against the frozen capture's frames."""
    fields = _check_length(data, REQUEST_HEADER, REQUEST_MAGIC, "cell request")
    _magic, version, sequence, n_frames, session = fields
    counts = tuple(range_bin_counts)
    if version != REQUEST_VERSION:
        raise TwoRoundError(f"cell request version {version} is not supported")
    if not _crc_ok(data):
        raise TwoRoundError("cell request CRC mismatch")
    if n_frames != len(counts):
        raise TwoRoundError(f"cell request has {n_frames} frames, capture has {len(counts)}")
    mask = np.zeros((n_frames, n_samples), dtype=bool)
    at, end = REQUEST_HEADER.size, len(data) - CRC.size
    for frame, count in enumerate(counts):
        if at >= end:
            raise TwoRoundError("cell request truncated")
        n_runs = data[at]
        at += 1
        if at + 2 * n_runs > end:
            raise TwoRoundError("cell request truncated")
        previous_end = 0
        for _ in range(n_runs):
            first, length = data[at], data[at + 1]
            at += 2
            if length == 0 or first < previous_end or first + length > count:
                raise TwoRoundError(f"cell request frame {frame}: bad run ({first}, {length})")
            mask[frame, first : first + length] = True
            previous_end = first + length
    if at != end:
        raise TwoRoundError(f"cell request has {end - at} bytes past its last frame")
    return CellRequest(session, sequence, mask, bytes(data))


# --------------------------------------------------------------------------- cells


def cell_nbytes(meta: dict) -> int:
    """Bytes of one (frame, bin) cell: every chirp and RX at IQ16."""
    return meta["chirps_per_frame"] * meta["n_rx"] * 4


def build_cells(raw: bytes, request: CellRequest) -> bytes:
    """A ``cells`` reply as the radar sends it from its frozen ring."""
    meta, cube = parse_dump(bytes(raw))
    _check_capture(meta)
    frames, bins = np.nonzero(request.mask)
    # [cells, chirp, rx] -> (Im, Re) int16, the dump's own component order
    cells = cube[frames, :, :, bins]
    pairs = np.stack([np.rint(cells.imag), np.rint(cells.real)], axis=-1).astype("<i2")
    body = pairs.tobytes()
    per_cell = cell_nbytes(meta)
    total = CELLS_HEADER.size + len(body) + CRC.size
    header = CELLS_HEADER.pack(
        CELLS_MAGIC,
        CELLS_VERSION,
        CELLS_HEADER.size,
        total,
        request.session_id,
        request.sequence,
        0,
        len(frames),
        request.crc,
        per_cell,
    )
    return _seal(header + body)


def parse_cells(data: bytes, request: CellRequest, meta: dict) -> np.ndarray:
    """A ``cells`` reply -> int16 [cell, chirp, rx, (Im, Re)], checked against its request.

    A reply that echoes another session, sequence or request CRC is refused: a late
    reply can never attach to a different frozen capture or request.
    """
    fields = _check_length(data, CELLS_HEADER, CELLS_MAGIC, "cells")
    (
        _magic,
        version,
        header_nbytes,
        total,
        session,
        sequence,
        _reserved,
        n_cells,
        request_crc,
        per_cell,
    ) = fields
    if version != CELLS_VERSION or header_nbytes != CELLS_HEADER.size:
        raise TwoRoundError(f"cells version {version}/{header_nbytes} B is not supported")
    if len(data) < total:
        raise TwoRoundError(f"cells truncated: {len(data)} of {total} bytes")
    if len(data) > total:
        raise TwoRoundError(f"cells reply has {len(data) - total} bytes past its declared end")
    if not _crc_ok(data):
        raise TwoRoundError("cells CRC mismatch")
    if (session, sequence, request_crc) != (request.session_id, request.sequence, request.crc):
        raise TwoRoundError(
            f"cells reply for session {session:#010x} request {sequence} ({request_crc:#010x}) "
            f"does not answer session {request.session_id:#010x} request {request.sequence}"
        )
    if per_cell != cell_nbytes(meta) or n_cells != request.n_cells:
        raise TwoRoundError(
            f"cells reply carries {n_cells} x {per_cell} B, request needs "
            f"{request.n_cells} x {cell_nbytes(meta)} B"
        )
    if total != CELLS_HEADER.size + n_cells * per_cell + CRC.size:
        raise TwoRoundError("cells reply length does not match its cell count")
    samples = np.frombuffer(
        data, dtype="<i2", offset=CELLS_HEADER.size, count=n_cells * per_cell // 2
    )
    return samples.reshape(n_cells, meta["chirps_per_frame"], meta["n_rx"], 2)


# --------------------------------------------------------------------------- rebuild


@dataclass
class TwoRoundCapture:
    """What the host holds of one frozen capture: the overview and the cells sent so far."""

    overview: Overview
    sent: np.ndarray = field(init=False)
    # [frame, chirp, rx, bin, (Im, Re)] int16; meaningful where ``sent``
    samples: np.ndarray = field(init=False)

    def __post_init__(self):
        meta = self.overview.meta
        nf, cpf, nrx, ns = (
            meta["n_frames"],
            meta["chirps_per_frame"],
            meta["n_rx"],
            meta["n_samples"],
        )
        self.sent = np.zeros((nf, ns), dtype=bool)
        self.samples = np.zeros((nf, cpf, nrx, ns, 2), dtype=np.int16)

    def add(self, request: CellRequest, cells: np.ndarray) -> None:
        """Keep one ``cells`` reply's samples."""
        frames, bins = np.nonzero(request.mask)
        self.samples[frames, :, :, bins] = cells
        self.sent[frames, bins] = True

    def unsent(self) -> np.ndarray:
        """Every cell of the capture not yet sent."""
        meta = self.overview.meta
        mask = ~self.sent
        for frame, count in enumerate(meta["range_bin_counts"]):
            mask[frame, count:] = False
        return mask

    def sparse_dump(self) -> SparseDump:
        """A production-format dump of what was sent, every other cell a sum-keeping fill.

        Each unsent cell's TX1/TX3 samples are integers whose sum per absolute bin, TX
        and RX equals the overview's sum less the sent cells' (so every window-scope mean
        is the full dump's); its TX2 samples are zero. Fills spread a remainder one count
        at a time over the cells in frame order and their loops.
        """
        ov = self.overview
        meta = ov.meta
        nf, cpf, nrx, ns = (
            meta["n_frames"],
            meta["chirps_per_frame"],
            meta["n_rx"],
            meta["n_samples"],
        )
        loops = cpf // N_LOOP_TX
        starts, counts = meta["range_bin_starts"], meta["range_bin_counts"]
        tdm = self.samples.reshape(nf, loops, N_LOOP_TX, nrx, ns, 2).astype(np.int64)
        out = np.where(self.sent[:, None, None, None, :, None], tdm, 0)
        remaining = ov.sums.copy()
        holders: list[list[tuple[int, int]]] = [[] for _ in range(remaining.shape[0])]
        for frame, (start, count) in enumerate(zip(starts, counts)):
            for local in range(count):
                if self.sent[frame, local]:
                    block = tdm[frame, :, SUMMED_TX, :, local].sum(axis=1)  # [tx, rx, (Im, Re)]
                    remaining[start + local, :, :, 0] -= block[..., 1]
                    remaining[start + local, :, :, 1] -= block[..., 0]
                else:
                    holders[start + local].append((frame, local))
        for absolute, cells in enumerate(holders):
            if not cells:
                continue
            n = len(cells) * loops
            for t, tx in enumerate(SUMMED_TX):
                for rx in range(nrx):
                    for part, component in ((0, 1), (1, 0)):  # re -> Re slot, im -> Im slot
                        quotient, remainder = divmod(int(remaining[absolute, t, rx, part]), n)
                        fill = (quotient + (np.arange(n) < remainder)).reshape(len(cells), loops)
                        for i, (frame, local) in enumerate(cells):
                            out[frame, :, tx, rx, local, component] = fill[i]
        if np.abs(out).max(initial=0) > 32767:
            raise TwoRoundError("a fill is outside the int16 range")
        flat = out.reshape((nf, cpf, nrx, ns, 2)).astype("<i2")
        payload = b"".join(
            flat[frame, :, :, :count].tobytes() for frame, count in enumerate(counts)
        )
        evidence = SparseEvidence(
            range_bin_counts=tuple(counts),
            n_samples=ns,
            sent=self.sent.copy(),
            power_maps=ov.power_maps,
            noise_power=ov.noise_power,
        )
        # pylint: disable-next=too-many-function-args
        return SparseDump(ov.prefix + payload + ov.suffix, evidence)


# --------------------------------------------------------------------------- link


class ByteTransport(Protocol):
    """A serial-like byte stream: what the live serial child owns, or a simulator."""

    def write(self, data: bytes) -> int | None:
        """Send bytes."""

    def read(self, size: int) -> bytes:
        """Up to ``size`` bytes; empty when none arrived in the port's timeout."""


class TwoRoundClient:
    """The host's protocol reader over one byte stream.

    Each binary reply is read to its declared length, its CRC checked, then the
    complete ``Done``/prompt; text never goes through the generic CLI helper (it
    decodes text and clears input). It owns no serial port, so it can run inside the
    serial child that owns the device.
    """

    def __init__(self, transport: ByteTransport, *, timeout_s: float = 5.0):
        self.transport = transport
        self.timeout_s = timeout_s

    def _read_exact(self, size: int, deadline: float) -> bytes:
        data = bytearray()
        while len(data) < size:
            chunk = self.transport.read(size - len(data))
            if chunk:
                data += chunk
            elif time.monotonic() > deadline:
                raise TwoRoundError(f"timed out after {len(data)} of {size} bytes")
        return bytes(data)

    def _read_through(self, marker: bytes, deadline: float, limit: int = 4096) -> bytes:
        data = bytearray()
        while not data.endswith(marker):
            data += self._read_exact(1, deadline)
            if len(data) > limit:
                raise TwoRoundError(f"no {marker!r} within {limit} bytes")
        return bytes(data)

    def _send(self, line: bytes) -> float:
        self.transport.write(line)
        return time.monotonic() + self.timeout_s

    def _binary(self, line: bytes, magic: bytes, header: struct.Struct) -> bytes:
        deadline = self._send(line)
        # an echo of the command may precede the payload; an error line replaces it
        lead = bytearray()
        while not lead.endswith(magic):
            lead += self._read_exact(1, deadline)
            if CLI_ERROR in lead:
                if not lead.endswith(CLI_PROMPT):
                    lead += self._read_through(CLI_PROMPT, deadline)
                text = bytes(lead[lead.find(CLI_ERROR) : -len(CLI_PROMPT)]).strip()
                raise TwoRoundRemoteError(text.decode("ascii", "replace"))
            if len(lead) > len(line) + len(magic) + 8:
                raise TwoRoundError(f"unexpected bytes before the payload: {bytes(lead[:32])!r}")
        start = bytes(lead[-len(magic) :])
        fixed = start + self._read_exact(header.size - len(magic), deadline)
        total = header.unpack_from(fixed, 0)[3]
        if total < header.size + CRC.size:
            raise TwoRoundError(f"payload declares {total} bytes")
        data = fixed + self._read_exact(total - header.size, deadline)
        tail = self._read_through(CLI_PROMPT, deadline).replace(b"\r\n", b"\n")
        if tail.lstrip(b"\n") != CLI_DONE + CLI_PROMPT:
            raise TwoRoundError(f"payload not followed by Done and the prompt: {tail[:64]!r}")
        return data

    def overview(self, *, session_id: int | None = None) -> tuple[Overview, bytes]:
        """Round 1 (also the reconnect path: the frozen session's overview, again)."""
        data = self._binary(f"{CMD_OVERVIEW}\n".encode("ascii"), OVERVIEW_MAGIC, OVERVIEW_HEADER)
        return parse_overview(data, session_id=session_id), data

    def cells(self, request: CellRequest, meta: dict) -> tuple[np.ndarray, bytes]:
        """One round of cells."""
        data = self._binary(request.command(), CELLS_MAGIC, CELLS_HEADER)
        return parse_cells(data, request, meta), data

    def release(self, session_id: int) -> None:
        """Release the frozen session; the radar rearms only on this acknowledged release."""
        deadline = self._send(f"{CMD_RELEASE} {session_id:08x}\n".encode("ascii"))
        reply = self._read_through(CLI_PROMPT, deadline).replace(b"\r\n", b"\n")
        expected = RELEASED_REPLY + f" {session_id:08x}\n".encode("ascii") + CLI_DONE + CLI_PROMPT
        if not reply.endswith(expected):
            text = reply.strip().decode("ascii", "replace")
            raise TwoRoundRemoteError(f"release of {session_id:#010x} not acknowledged: {text}")


# --------------------------------------------------------------------------- host rounds


@dataclass
class TwoRoundTransfer:
    """Bytes and rounds one two-round transfer used (radar -> host and host -> radar)."""

    session_id: int | None = None
    overview_bytes: int = 0
    request_bytes: list[int] = field(default_factory=list)
    cells_bytes: list[int] = field(default_factory=list)
    cells_per_round: list[int] = field(default_factory=list)
    analysis_s: list[float] = field(default_factory=list)
    complete_request: bool = False
    released: bool = False
    error: str | None = None

    @property
    def cell_rounds(self) -> int:
        """Rounds after the overview."""
        return len(self.cells_per_round)

    @property
    def link_bytes(self) -> int:
        """Every byte both ways (payloads and request lines, not the CLI framing)."""
        return self.overview_bytes + sum(self.request_bytes) + sum(self.cells_bytes)

    def to_dict(self) -> dict:
        """JSON record for the shot result and session log."""
        return {
            "session_id": self.session_id,
            "overview_bytes": self.overview_bytes,
            "request_bytes": list(self.request_bytes),
            "cells_bytes": list(self.cells_bytes),
            "cells_per_round": list(self.cells_per_round),
            "cell_rounds": self.cell_rounds,
            "link_bytes": self.link_bytes,
            "analysis_s": list(self.analysis_s),
            "complete_request": self.complete_request,
            "released": self.released,
            "error": self.error,
        }


class TwoRoundTransferError(RuntimeError):
    """A two-round transfer failed; ``transfer`` and ``capture`` keep what had arrived."""

    def __init__(self, message: str, transfer: TwoRoundTransfer, capture: TwoRoundCapture | None):
        super().__init__(message)
        self.transfer = transfer
        self.capture = capture


def run_two_round(  # pylint: disable=too-many-locals
    client: TwoRoundClient,
    analyse: Callable[[SparseDump], object],
    *,
    max_cell_rounds: int = MAX_CELL_ROUNDS,
):
    """Fetch one frozen capture in rounds and return (``analyse``'s result, transfer).

    ``analyse`` is the whole kiosk path on a dump. It runs first on the overview alone
    (a dry run: whatever it reads becomes the first request), then once per cells round
    until every cell it reads has been sent. After ``max_cell_rounds`` the host asks for
    every remaining cell, so the answer can never rest on a fill. The session is
    released (acknowledged) at the end; a failed request keeps what arrived in the error
    and is released explicitly and logged, never silently.
    """
    transfer = TwoRoundTransfer()
    capture: TwoRoundCapture | None = None
    try:
        overview, data = client.overview()
        transfer.session_id = overview.session_id
        transfer.overview_bytes = len(data)
        capture = TwoRoundCapture(overview)
        counts = overview.meta["range_bin_counts"]
        dry_run = True
        while True:
            dump = capture.sparse_dump()
            started = time.perf_counter()
            error: Exception | None = None
            try:
                result = analyse(dump)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                if not dry_run:
                    raise
                error = exc  # the dry run's reads still name the cells to ask for
            transfer.analysis_s.append(time.perf_counter() - started)
            missing = dump.evidence.unsent_reads()
            if not missing.any():
                if error is not None:
                    raise error
                break
            if transfer.cell_rounds >= max_cell_rounds:
                missing = capture.unsent()
                transfer.complete_request = True
            request = encode_request(
                missing,
                session_id=overview.session_id,
                sequence=transfer.cell_rounds + 1,
                range_bin_counts=counts,
            )
            cells, data = client.cells(request, overview.meta)
            transfer.request_bytes.append(len(request.command()))
            transfer.cells_bytes.append(len(data))
            transfer.cells_per_round.append(request.n_cells)
            capture.add(request, cells)
            dry_run = False
    except TwoRoundError as error:
        transfer.error = f"{type(error).__name__}: {error}"
        logger.warning("[IWR6843] two-round transfer failed: %s", transfer.error)
        if transfer.session_id is not None:
            _release(client, transfer)
        raise TwoRoundTransferError(transfer.error, transfer, capture) from error
    _release(client, transfer)
    return result, transfer


def _release(client: TwoRoundClient, transfer: TwoRoundTransfer) -> None:
    try:
        client.release(transfer.session_id)
        transfer.released = True
    except TwoRoundError as error:
        # the radar's hold timeout rearms it; say so rather than assume
        logger.warning(
            "[IWR6843] release of session %#010x failed (%s); the radar's hold timeout rearms it",
            transfer.session_id,
            error,
        )
        if transfer.error is None:
            transfer.error = f"release failed: {error}"


__all__ = [
    "CELLS_MAGIC",
    "CELLS_VERSION",
    "CMD_CELLS",
    "CMD_OVERVIEW",
    "CMD_RELEASE",
    "CellRequest",
    "OVERVIEW_MAGIC",
    "OVERVIEW_VERSION",
    "Overview",
    "REQUEST_MAGIC",
    "TwoRoundCapture",
    "TwoRoundClient",
    "TwoRoundError",
    "TwoRoundRemoteError",
    "TwoRoundTransfer",
    "TwoRoundTransferError",
    "build_cells",
    "build_overview",
    "decode_request",
    "encode_request",
    "parse_cells",
    "parse_overview",
    "run_two_round",
]
