"""Synthetic IWR6843 dumps and lossy transfers for the partial-dump tests.

A scene is static clutter per (TX, RX, absolute range bin), white noise, and a
moving target, packed in the production layout (3 TX x 12 loops x 4 RX x 53 bins,
moving windows 20/32/47, timed descriptors). The clutter and noise levels are
chosen to resemble the field dumps' loop-to-loop agreement, not measured from them.
"""

from __future__ import annotations

import numpy as np

from openflight.iwr6843 import dump
from openflight.iwr6843.dump import SAMPLE_RANGE_FFT_IQ16_VARIABLE_TIMED, pack_dump, parse_header

TEMPERATURE = {key: 45 for key in dump.TEMP_REPORT_KEYS}
CLI_REPLY = b"Done\nl3dump:/>"  # what the firmware sends after a dump; a short read keeps it
N_TX, LOOPS, N_RX, BINS = 3, 12, 4, 53
FRAME_NBYTES = N_TX * LOOPS * N_RX * BINS * 4


def starts(n_frames: int) -> list[int]:
    """The production profile's moving windows, scaled to ``n_frames``."""
    third = max(1, n_frames // 3)
    return [20 if f < third else 32 if f < 2 * third else 47 for f in range(n_frames)]


def scene(n_frames=12, *, seed=0, clutter=800.0, noise=60.0, structure=True) -> np.ndarray:
    """A complex cube [frames, chirps, rx, bins] of integer I/Q."""
    rng = np.random.default_rng(seed)
    frame_starts = starts(n_frames)
    max_bin = max(frame_starts) + BINS
    shape = (N_TX, N_RX, max_bin)
    static = clutter * (rng.standard_normal(shape) + 1j * rng.standard_normal(shape))
    cube = np.zeros((n_frames, N_TX * LOOPS, N_RX, BINS), dtype=complex)
    if structure:
        for frame, start in enumerate(frame_starts):
            for chirp in range(N_TX * LOOPS):
                cube[frame, chirp] = static[chirp % N_TX, :, start : start + BINS]
    cube += noise * (rng.standard_normal(cube.shape) + 1j * rng.standard_normal(cube.shape))
    if structure:
        # a receding target from frame 4, two bins a frame, a fresh phase every chirp
        for frame in range(4, n_frames):
            local = 40 + 2 * (frame - 4) - frame_starts[frame]
            if 0 <= local < BINS:
                phase = np.exp(2j * np.pi * rng.random((N_TX * LOOPS, N_RX)))
                cube[frame, :, :, local] += 5 * clutter * phase
    return np.round(cube.real) + 1j * np.round(cube.imag)


def pack(cube, *, sample_fmt=SAMPLE_RANGE_FFT_IQ16_VARIABLE_TIMED, version=7, **extra) -> bytes:
    """The cube as a timed dump: 3 ms frames, the scene's moving windows."""
    n_frames = cube.shape[0]
    return pack_dump(
        cube,
        n_tx=N_TX,
        version=version,
        frame_period_us=3000,
        sample_fmt=sample_fmt,
        range_bin_starts=starts(n_frames),
        range_bin_counts=[BINS] * n_frames,
        frame_time_offsets_us=[3000 * f for f in range(n_frames)],
        temperature_report=TEMPERATURE if version in (7, 8) else None,
        **extra,
    )


def payload_start(raw: bytes) -> int:
    """Where the dump's sample payload begins."""
    meta = parse_header(raw)
    return meta["header_nbytes"] + meta["frame_metadata_nbytes"]


def lose(raw: bytes, runs, *, reply=CLI_REPLY) -> bytes:
    """``raw`` without the (true payload offset, nbytes) runs, as the reader keeps it."""
    start = payload_start(raw)
    keep = np.ones(len(raw), dtype=bool)
    for offset, nbytes in runs:
        keep[start + offset : start + offset + nbytes] = False
    return np.frombuffer(raw, dtype=np.uint8)[keep].tobytes() + reply
