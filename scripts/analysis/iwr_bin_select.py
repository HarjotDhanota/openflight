#!/usr/bin/env python3
"""RESEARCH: would the host estimators read the same numbers from a bin-selected IWR dump?

Offline only (stage 1A of the on-chip study). For each full wide IQ16 dump the radar
would keep, per frame, only the range bins along the ball and the club (every chirp and
antenna at IQ16) and send a small summary for the rest. This script builds that reduced
capture, turns it back into a full dump of the production format (research-only
reconstruction; production code is untouched) and runs the kiosk's IWR path on it
(``iwr_transfer_study.run_kiosk``: launch range, then ``process_raw_capture``).

Selectors (each with a +-k bin margin):

* ``oracle``: the bins along every walk the host searches returned on the full dump
  (ball, club, launch-range walks and runner-ups, the OPS-guided candidates). An upper
  bound: it uses the host tracker's own results.
* ``chip``: what the radar could compute itself on its 24 stored frames: burst-MTI power
  per frame and bin summed over chirps and antennas, a per-frame median noise floor, and
  an exhaustive straight-walk search from a launch bin inside a broad configured range
  gate (ball after the launch time, club up to it). No labels, no host results, no OPS.

Fills for the unselected bins (what the summary carries):

* ``zero``: nothing (naive cut).
* ``clutter``: per absolute bin, TX and RX, the complex mean of the unselected samples
  (so each selected bin's window-scope MTI is exactly the original's), plus the two
  noise scalars the LCMF snapshot SNR uses (median MTI power, burst and window scope).
* ``clutter_noise``: ``clutter`` plus a per frame x bin noise map (mean burst-MTI power
  per sample, 0.5 dB uint8); the host fills each unselected bin with complex Gaussian
  noise of that power so the tracker's per-loop gate medians see a noise floor.
* ``framemean_noise``: like ``clutter_noise`` but the fill is each unselected bin's own
  per-frame loop mean (per TX and RX) rather than the capture mean.

Writes ``rows.jsonl``, ``summary.json``, ``tables.md``, ``bytes.json`` and
``selection.jsonl`` into ``--out``.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

import iwr_transfer_study as ts  # noqa: E402  pylint: disable=wrong-import-position

from openflight.iwr6843 import (
    runtime as iwr_runtime,  # noqa: E402
    shot as iwr_shot,  # noqa: E402
    tracking,  # noqa: E402
)
from openflight.iwr6843.dump import pack_dump, parse_dump  # noqa: E402
from openflight.iwr6843.lcmf import prepare_lcmf_capture  # noqa: E402
from openflight.iwr6843.music import LAM  # noqa: E402
from openflight.iwr6843.shot import TX2_LOOP_PERIOD_S  # noqa: E402

BAUDS = (1_041_667, 2_000_000)
BITS_PER_BYTE = 10
CELL_BYTES = 36 * 4 * 4  # chirps x RX x IQ16 per (frame, bin)
N_TX = 3
N_LOOPS = 12
FRAME_MASK_BYTES = 7  # 53 bits per frame: which bins follow
NOISE_DB_STEP = 0.5

# --------------------------------------------------------------------------- recording


class _Recorder:
    """Records every walk the host's searches return while one capture is processed."""

    def __init__(self):
        self.tracks: list[dict] = []
        self._search = tracking.search_ball
        self._credible = iwr_runtime._credible_ops_candidates  # pylint: disable=protected-access

    def __enter__(self):
        recorder = self

        def search_ball(*args, **kwargs):
            out = recorder._search(*args, **kwargs)  # pylint: disable=protected-access
            caller = sys._getframe(2).f_code.co_name  # pylint: disable=protected-access
            if out.selected is not None:
                recorder.add(out.selected.track, f"{caller}:selected")
            if out.runner_up is not None:
                recorder.add(out.runner_up.track, f"{caller}:runner_up")
            return out

        def credible(candidates):
            out = recorder._credible(candidates)  # pylint: disable=protected-access
            for candidate in out:
                recorder.add(candidate.track, "ops_guided_candidate")
            return out

        tracking.search_ball = search_ball
        iwr_runtime._credible_ops_candidates = credible  # pylint: disable=protected-access
        return self

    def __exit__(self, *exc):
        tracking.search_ball = self._search
        iwr_runtime._credible_ops_candidates = self._credible  # pylint: disable=protected-access

    def add(self, track, tag):
        self.tracks.append(
            {
                "tag": tag,
                "slope_bins": float(track.slope_bins),
                "intercept_bins": float(track.intercept_bins),
                "quad_bins": list(track.quad_bins) if track.quad_bins else None,
                "t_first": float(track.t_first),
                "t_last": float(track.t_last),
                "speed_ms": float(track.speed_ms),
            }
        )


class _NoiseOverride:
    """The summary's noise scalars stand in for the cube-wide median (sparse-input path)."""

    def __init__(self, values: dict[str, float] | None):
        self.values = values
        self._orig = iwr_shot.PreparedShotDump.noise_power

    def __enter__(self):
        if self.values is not None:
            values = self.values

            def noise_power(_self, scope="burst"):
                return values[scope]

            iwr_shot.PreparedShotDump.noise_power = noise_power
        return self

    def __exit__(self, *exc):
        iwr_shot.PreparedShotDump.noise_power = self._orig


class _PowerMapOverride:
    """Sparse-input path for track finding: the tracker's per-loop power for the bins
    the radar did not send comes from the summary's power map (one map per MTI scope).

    ``tracking.mti_filter`` outputs are tagged with their scope so ``tracking.loop_power``
    knows which map to read; selected cells keep the power of the data that was sent.
    """

    def __init__(self, maps: dict[str, np.ndarray] | None, unsel: np.ndarray | None):
        self.maps, self.unsel = maps, unsel
        self._mti, self._power = tracking.mti_filter, tracking.loop_power
        self._scopes: dict[int, tuple[str, np.ndarray]] = {}
        self.unmatched = 0

    def __enter__(self):
        if self.maps is None:
            return self
        me = self

        def mti_filter(cube, scope="burst", **kwargs):
            out = me._mti(cube, scope=scope, **kwargs)
            me._scopes[id(out)] = (scope, out)
            return out

        def loop_power(mti):
            power = me._power(mti)
            tagged = me._scopes.get(id(mti))
            if tagged is None or tagged[1] is not mti or tagged[0] not in me.maps:
                me.unmatched += 1
                return power
            n_loops = mti.shape[2]
            cells = np.repeat(me.unsel, n_loops, axis=0)[:, : power.shape[1]]
            power = power.copy()
            power[cells] = me.maps[tagged[0]][cells]
            return power

        tracking.mti_filter, tracking.loop_power = mti_filter, loop_power
        return self

    def __exit__(self, *exc):
        tracking.mti_filter, tracking.loop_power = self._mti, self._power


def power_maps(raw: bytes, bits: int, scopes: tuple[str, ...]) -> dict[str, np.ndarray]:
    """The radar's per-loop power map (2-TX projected, summed over 8 channels) per scope.

    ``bits`` 32 sends float32, 16 a 0.002 dB log code; 8 sends 0.5 dB log codes (decoded as the host would).
    """
    vertical = prepare_lcmf_capture(raw).vertical
    out = {}
    for scope in scopes:
        power = tracking.loop_power(vertical.mti(scope))
        if bits == 32:
            out[scope] = power.astype(np.float32).astype(float)
        elif bits == 16:  # 16-bit log code, 1/500 dB steps (0-131 dB)
            code = np.clip(np.round(10 * np.log10(np.maximum(power, 1.0)) * 500), 0, 65535)
            out[scope] = 10 ** (code / 5000)
        else:
            out[scope] = _db_dequant(_db_quant(power))
    return out


# --------------------------------------------------------------------------- geometry


def loop_times(raw: bytes) -> np.ndarray:
    """[n_frames, n_loops] sample times on the host's (TX1/TX3-projected) clock."""
    geo = prepare_lcmf_capture(raw).vertical.geometry
    return np.array(
        [[geo.loop_time(f, loop) for loop in range(geo.n_loops)] for f in range(geo.n_frames)]
    )


def cells_from_walk(
    mask: np.ndarray,
    starts: tuple[int, ...],
    counts: tuple[int, ...],
    times: np.ndarray,
    bin_at,
    t_lo: float,
    t_hi: float,
    k: int,
) -> None:
    """Mark every (frame, local bin) a walk crosses inside [t_lo, t_hi], +-k bins."""
    for frame in range(times.shape[0]):
        ts_in = times[frame][(times[frame] >= t_lo) & (times[frame] <= t_hi)]
        if ts_in.size == 0:
            continue
        b = np.asarray([bin_at(t) for t in ts_in], float)
        lo = int(math.floor(b.min())) - k
        hi = int(math.ceil(b.max())) + k
        lo_l = max(lo - starts[frame], 0)
        hi_l = min(hi - starts[frame], counts[frame] - 1)
        if hi_l >= lo_l:
            mask[frame, lo_l : hi_l + 1] = True


def oracle_mask(meta: dict, times: np.ndarray, tracks: list[dict], k: int) -> np.ndarray:
    starts, counts = tuple(meta["range_bin_starts"]), tuple(meta["range_bin_counts"])
    mask = np.zeros((meta["n_frames"], max(counts)), bool)
    for tr in tracks:

        def bin_at(t, tr=tr):  # BallTrack.bin_at: the line (the quadratic is speed only)
            return tr["slope_bins"] * t + tr["intercept_bins"]

        # snapshot reads run 2 ms past each end; club windows reach a few frames further
        cells_from_walk(
            mask, starts, counts, times, bin_at, tr["t_first"] - 0.006, tr["t_last"] + 0.006, k
        )
    return mask


# --------------------------------------------------------------------------- chip selector


def burst_power(cube: np.ndarray) -> np.ndarray:
    """[n_frames, n_bins] burst-MTI power summed over loops, TX and RX (all 36 chirps)."""
    nf, cpf, nrx, ns = cube.shape
    tdm = cube.reshape(nf, cpf // N_TX, N_TX, nrx, ns)
    mti = tdm - tdm.mean(axis=1, keepdims=True)
    return (np.abs(mti) ** 2).sum(axis=(1, 2, 3))


def chip_select(
    cube: np.ndarray,
    meta: dict,
    times: np.ndarray,
    k: int,
    *,
    gate_m: tuple[float, float] = (1.0, 3.5),
    doppler: bool = False,
) -> tuple[np.ndarray, dict]:
    """The radar-side selector: walks found from per-frame bin power only.

    1. P[f, b]: burst-MTI power per frame and bin, summed over 36 chirps x 4 RX.
    2. S[f, b] = P / median_b P[f, :]; score s = clip(10 log10 S - 6 dB, 0, 20).
       Each bin's median score over its frames is removed first (body, hands: no walk).
       With ``doppler`` the power is per Doppler bin (a 12-point DFT over each frame's
       loops) and a walk at speed v reads only the +-1 Doppler bins its aliased phase
       step predicts; without it, the loop-summed power for every speed.
    3. Ball: exhaustive search over launch bin Bt (inside ``gate_m``), launch time t0
       (1 ms grid, 15 ms before to 3 ms after the trigger frame) and speed v (25-85 m/s):
       score = sum over frames after t0 of g[f, Bt + v (t_f - t0)], g = s max-filtered
       over +-1 bin, at least 3 frames hit.
       Club: the same search over its bin at t0 (Bt - 10 .. Bt + 2) and speed (10 m/s up
       to v - 2 m/s) over the 30 ms before t0.
    4. Select: the ball walk from t0 - 3 ms on, the club walk from t0 - 30 ms to
       t0 + 3 ms, and the launch bin in every frame, each over its in-frame span, +-k bins.
    Integer-only in C apart from the log score (a 256-entry table on the DSP).
    """
    starts = np.asarray(meta["range_bin_starts"])
    counts = np.asarray(meta["range_bin_counts"])
    nf = cube.shape[0]
    n_abs = int((starts + counts).max())
    present = np.zeros((nf, n_abs + 2), bool)
    for f in range(nf):
        present[f, starts[f] : starts[f] + counts[f]] = True

    def score_map(power: np.ndarray) -> np.ndarray:
        """[nf, local bins] power -> absolute-bin score map, bin-median removed, max-filtered."""
        noise = np.array([np.median(power[f, : counts[f]]) for f in range(nf)])
        score = np.clip(10 * np.log10(power / noise[:, None] + 1e-12) - 6.0, 0.0, 20.0)
        grid = np.zeros((nf, n_abs + 2))
        for f in range(nf):
            grid[f, starts[f] : starts[f] + counts[f]] = score[f, : counts[f]]
        # a target that stays in one bin (body, hands) is not a walk: remove each bin's
        # median score over the frames that hold it
        for b in range(grid.shape[1]):
            col = present[:, b]
            if col.any():
                grid[col, b] = np.maximum(grid[col, b] - np.median(grid[col, b]), 0.0)
        padded = np.pad(grid, ((0, 0), (1, 1)))
        return np.maximum(np.maximum(padded[:, :-2], padded[:, 1:-1]), padded[:, 2:])

    speeds_ms = np.arange(10.0, 86.0, 1.0)
    if doppler:
        # 12-point Doppler DFT over each frame's loops (the HWA's usual second FFT); a walk
        # at radial speed v reads only the Doppler bins its (aliased) phase step predicts
        nfr, cpf, nrx, ns = cube.shape
        tdm = cube.reshape(nfr, cpf // N_TX, N_TX, nrx, ns)
        spec = np.fft.fft(tdm, axis=1)
        pd = (np.abs(spec) ** 2).sum(axis=(2, 3))  # [nf, doppler, bins]
        maps = np.stack([score_map(pd[:, d, :]) for d in range(N_LOOPS)])
        step = 2.0 * TX2_LOOP_PERIOD_S / LAM * N_LOOPS  # Doppler bins per m/s
        dbin = np.rint(-speeds_ms * step).astype(int) % N_LOOPS
        grids = np.stack(
            [
                np.maximum(np.maximum(maps[(d - 1) % N_LOOPS], maps[d]), maps[(d + 1) % N_LOOPS])
                for d in dbin
            ]
        )
    else:
        grids = np.broadcast_to(score_map(burst_power(cube)), (speeds_ms.size, nf, n_abs + 2))
    t_mid = times.mean(axis=1)
    res = 6.0 / 128
    b_lo, b_hi = int(gate_m[0] / res), int(gate_m[1] / res)
    in_window = [(starts[f] + 1, starts[f] + counts[f] - 2) for f in range(nf)]

    lo_w = np.array([w[0] for w in in_window])
    hi_w = np.array([w[1] for w in in_window])

    def walk_scores(bts, t0, j_idx, frames):
        """Scores and hit counts for every (launch bin, speed index) walk over ``frames``."""
        fr = np.asarray(frames)
        v_bins = speeds_ms[j_idx] / res
        b = np.rint(
            bts[:, None, None] + v_bins[None, :, None] * (t_mid[fr] - t0)[None, None, :]
        ).astype(int)
        ok = (b >= lo_w[fr]) & (b <= hi_w[fr])
        vals = np.where(
            ok,
            grids[j_idx[None, :, None], fr[None, None, :], np.clip(b, 0, grids.shape[2] - 1)],
            0.0,
        )
        return vals.sum(axis=2), (vals > 0).sum(axis=2)

    best = (-1.0, None)
    # the trigger (first frame after the pre-trigger ring) predicts the launch time
    n_pre = int(np.sum(starts == starts[0]))
    t_trigger = float(times[n_pre, 0])
    t0_grid = np.arange(t_trigger - 0.015, t_trigger + 0.0035, 0.001)
    speeds = speeds_ms / res
    j_all = np.arange(speeds_ms.size)
    ball_ok = speeds_ms >= 25.0
    bts = np.arange(b_lo, b_hi + 1, dtype=float)
    for t0 in t0_grid:
        after = [f for f in range(nf) if t_mid[f] > t0 + 0.001]
        if len(after) < 3:
            continue
        sb, hb = walk_scores(bts, t0, j_all, after)
        sb = np.where((hb >= 3) & ball_ok[None, :], sb, -np.inf)
        i, j = np.unravel_index(int(np.argmax(sb)), sb.shape)
        if np.isfinite(sb[i, j]) and sb[i, j] > best[0]:
            best = (float(sb[i, j]), (int(bts[i]), float(t0), int(j)))
    club = None
    if best[1] is not None:
        bt, t0, jb = best[1]
        before = [f for f in range(nf) if t0 - 0.030 <= t_mid[f] < t0 - 0.001]
        bcs = np.arange(bt - 10, bt + 3, dtype=float)
        jc_all = np.arange(max(jb - 1, 1))
        if len(before) >= 2 and jc_all.size:
            sc, hc = walk_scores(bcs, t0, jc_all, before)
            sc = np.where(hc >= 2, sc, -np.inf)
            i, j = np.unravel_index(int(np.argmax(sc)), sc.shape)
            if np.isfinite(sc[i, j]):
                club = (float(bcs[i]), float(speeds[jc_all[j]]), float(sc[i, j]))
    info: dict = {"ball": None, "club": None}
    mask = np.zeros((nf, int(counts.max())), bool)
    if best[1] is None:
        mask[:] = True  # nothing found: the radar sends everything
        info["fallback"] = "full"
        return mask, info
    bt, t0, jb = best[1]
    v = float(speeds[jb])
    info["t_trigger_s"] = t_trigger
    info["ball"] = {"launch_bin": bt, "t0_s": t0, "speed_ms": v * res, "score": best[0]}
    st, ct = tuple(int(x) for x in starts), tuple(int(x) for x in counts)
    cells_from_walk(mask, st, ct, times, lambda t: bt + v * (t - t0), t0 - 0.003, 1.0, k)
    if club is not None:
        bc, vc, club_score = club
        info["club"] = {"impact_bin": bc, "speed_ms": vc * res, "score": club_score}
        cells_from_walk(
            mask, st, ct, times, lambda t: bc + vc * (t - t0), t0 - 0.030, t0 + 0.003, k
        )
    else:  # no club walk: the wedge a 20-45 m/s club could occupy before launch
        for vw in (20.0 / res, 45.0 / res):
            cells_from_walk(
                mask, st, ct, times, lambda t, vw=vw: bt + vw * (t - t0), t0 - 0.030, t0, k
            )
    cells_from_walk(mask, st, ct, times, lambda _t: bt, -1.0, 1.0, k)
    return mask, info


# --------------------------------------------------------------------------- reduction


def _db_quant(power: np.ndarray) -> np.ndarray:
    db = 10 * np.log10(np.maximum(power, 1e-3))
    return np.clip(np.round(db / NOISE_DB_STEP), 0, 255).astype(np.uint8)


def _db_dequant(code: np.ndarray) -> np.ndarray:
    return 10 ** (code.astype(float) * NOISE_DB_STEP / 10)


def noise_scalars(raw: bytes) -> dict[str, float]:
    vertical = prepare_lcmf_capture(raw).vertical
    return {s: float(vertical.noise_power(s)) for s in ("burst", "window")}


def reduce_capture(raw: bytes, mask: np.ndarray, fill: str, seed: int) -> tuple[bytes, dict]:
    """The reduced capture (selected cells + summary) rebuilt as a production-format dump."""
    meta, cube = parse_dump(raw)
    starts, counts = meta["range_bin_starts"], meta["range_bin_counts"]
    nf, cpf, nrx, ns = cube.shape
    tdm = cube.reshape(nf, cpf // N_TX, N_TX, nrx, ns)
    sel = np.zeros((nf, ns), bool)
    sel[:, : mask.shape[1]] = mask[:, :ns]
    for f in range(nf):
        sel[f, counts[f] :] = False
    unsel = ~sel
    for f in range(nf):
        unsel[f, counts[f] :] = False
    out = np.where(sel[:, None, None, None, :], tdm, 0)
    n_sel = int(sel.sum())
    n_unsel = int(unsel.sum())
    summary_bytes = 0
    if fill in ("clutter", "clutter_noise"):
        n_abs = max(s + c for s, c in zip(starts, counts))
        tot = np.zeros((N_TX, nrx, n_abs), complex)
        cnt = np.zeros(n_abs)
        for f in range(nf):
            for b in np.flatnonzero(unsel[f]):
                tot[:, :, starts[f] + b] += tdm[f, :, :, :, b].sum(axis=0)
                cnt[starts[f] + b] += tdm.shape[1]
        mean = tot / np.maximum(cnt, 1)[None, None, :]
        # sent as complex int16 per absolute bin, TX and RX
        mean = np.round(mean.real) + 1j * np.round(mean.imag)
        summary_bytes += int((cnt > 0).sum()) * N_TX * nrx * 4
        for f in range(nf):
            for b in np.flatnonzero(unsel[f]):
                out[f, :, :, :, b] = mean[None, :, :, starts[f] + b]
    if fill == "framemean_noise":
        fmean = tdm.mean(axis=1)
        fmean = np.round(fmean.real) + 1j * np.round(fmean.imag)
        summary_bytes += n_unsel * N_TX * nrx * 4
        for f in range(nf):
            for b in np.flatnonzero(unsel[f]):
                out[f, :, :, :, b] = fmean[f, None, :, :, b]
    if fill in ("clutter_noise", "framemean_noise"):
        mti = tdm - tdm.mean(axis=1, keepdims=True)
        cell_power = (np.abs(mti) ** 2).mean(axis=(1, 2, 3))  # per sample
        code = _db_quant(cell_power)
        summary_bytes += n_unsel  # one byte per unselected cell (sent as the full map)
        sigma2 = _db_dequant(code) * N_LOOPS / (N_LOOPS - 1)
        rng = np.random.default_rng(seed)
        noise = np.zeros_like(tdm)
        for f in range(nf):
            for b in np.flatnonzero(unsel[f]):
                shape = (tdm.shape[1], N_TX, nrx)
                draw = rng.standard_normal(shape) + 1j * rng.standard_normal(shape)
                noise[f, :, :, :, b] = draw * math.sqrt(sigma2[f, b] / 2)
        # zero-sum per absolute bin, TX and RX, so the selected bins' window-scope MTI
        # (the mean over every frame holding the bin) is left exactly as sent
        n_abs = max(s + c for s, c in zip(starts, counts))
        tot = np.zeros((N_TX, nrx, n_abs), complex)
        cnt = np.zeros(n_abs)
        for f in range(nf):
            for b in np.flatnonzero(unsel[f]):
                tot[:, :, starts[f] + b] += noise[f, :, :, :, b].sum(axis=0)
                cnt[starts[f] + b] += tdm.shape[1]
        nmean = tot / np.maximum(cnt, 1)[None, None, :]
        for f in range(nf):
            for b in np.flatnonzero(unsel[f]):
                out[f, :, :, :, b] += noise[f, :, :, :, b] - nmean[None, :, :, starts[f] + b]
    if fill != "zero":
        summary_bytes += 8  # two float32 noise scalars
    rebuilt = out.reshape(nf, cpf, nrx, ns)
    rebuilt = np.clip(np.round(rebuilt.real), -32768, 32767) + 1j * np.clip(
        np.round(rebuilt.imag), -32768, 32767
    )
    data = pack_dump(rebuilt, n_tx=meta["n_tx"], **ts._repack(meta))  # pylint: disable=protected-access
    # selected cells must be bit-exact
    _m, back = parse_dump(data)
    for f in range(nf):
        idx = np.flatnonzero(sel[f])
        if idx.size and not np.array_equal(back[f][..., idx], cube[f][..., idx]):
            raise AssertionError("selected cells are not bit-exact after the rebuild")
    header = ts._payload_offset(meta)  # pylint: disable=protected-access
    payload = header + nf * FRAME_MASK_BYTES + n_sel * CELL_BYTES
    total = payload + summary_bytes
    return (
        data,
        unsel,
        {
            "cells_selected": n_sel,
            "cells_total": int(sum(counts)),
            "payload_bytes": payload,
            "summary_bytes": summary_bytes,
            "total_bytes": total,
            "seconds": {str(b): total * BITS_PER_BYTE / b for b in BAUDS},
        },
    )


# --------------------------------------------------------------------------- labels (scoring only)


def label_coverage(mask: np.ndarray, meta: dict, labels: dict | None) -> dict | None:
    """Fraction of labelled ball/club points inside the selection (evaluation only)."""
    if not labels:
        return None
    out = {}
    for name in ("ball", "club"):
        pts = labels.get(name, {}).get("points", [])
        hit = n = 0
        for p in pts:
            f = int(p["frame"])
            if f >= meta["n_frames"]:
                continue
            b = int(round(p["range_bin"])) - meta["range_bin_starts"][f]
            if 0 <= b < meta["range_bin_counts"][f]:
                n += 1
                hit += bool(mask[f, b])
        out[name] = {"points": n, "covered": hit}
    return out


# --------------------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--recordings", required=True, type=Path)
    parser.add_argument("--outdoor", required=True, type=Path)
    parser.add_argument("--cal", default="config/iwr6843_calibration_reference.json")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--ks", default="1,2,3")
    parser.add_argument(
        "--variants",
        default="oracle:clutter_noise,chip:clutter_noise",
        help="selector:fill pairs; k is swept for each",
    )
    parser.add_argument("--dither", action="store_true", help="rerun the +-1 LSB control")
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)

    rejects: list = []
    shots = ts.recording_shots(args.recordings, rejects) + ts.outdoor_shots(args.outdoor, rejects)
    if args.limit:
        shots = shots[: args.limit]
    ks = [int(k) for k in args.ks.split(",")]
    pairs = [tuple(v.split(":")) for v in args.variants.split(",")]
    variant_names = [f"{s}_k{k}_{fill}" for s, fill in pairs for k in ks]
    if args.dither:
        variant_names.append("C_dither")

    rows, sel_rows, byte_rows = [], [], {}
    started = time.time()
    for index, shot in enumerate(shots):
        raw = Path(shot.path).read_bytes()
        meta, cube = parse_dump(raw)
        times = loop_times(raw)
        with _Recorder() as rec:
            original = ts.run_kiosk(raw, args.cal, shot, None)
        tee_mode = "kiosk_launch_range"
        tee = None
        if original["withheld"] and shot.tee_fallback_m is not None:
            tee_mode = "configured_tee_from_manifest"
            tee = shot.tee_fallback_m
            with _Recorder() as rec:
                original = ts.run_kiosk(raw, args.cal, shot, tee)
        row = {
            "path": shot.path,
            "dataset": shot.dataset,
            "tee_mode": tee_mode,
            "original": original,
            "bytes": {"original": len(raw)},
        }
        scalars = noise_scalars(raw)
        chip_cache: dict[tuple[str, int], tuple[np.ndarray, dict]] = {}
        for selector, fill in pairs:
            for k in ks:
                name = f"{selector}_k{k}_{fill}"
                if selector == "oracle":
                    mask, info = (
                        oracle_mask(meta, times, rec.tracks, k),
                        {"tracks": len(rec.tracks)},
                    )
                elif selector in ("chip", "chipdop"):
                    key = (selector, k)
                    if key not in chip_cache:
                        chip_cache[key] = chip_select(
                            cube, meta, times, k, doppler=selector == "chipdop"
                        )
                    mask, info = chip_cache[key]
                else:
                    raise ValueError(selector)
                base, _, map_spec = fill.partition("+")
                data, unsel, nbytes = reduce_capture(raw, mask, base, seed=index * 101 + k)
                maps = None
                if map_spec:
                    bits = int(map_spec[3:5]) if map_spec[3:5].isdigit() else 8
                    scopes = ("burst",) if map_spec.endswith("burst") else ("burst", "window")
                    maps = power_maps(raw, bits, scopes)
                    extra = int(unsel.sum()) * N_LOOPS * bits // 8 * len(scopes)
                    nbytes["summary_bytes"] += extra
                    nbytes["total_bytes"] += extra
                    nbytes["seconds"] = {
                        str(b): nbytes["total_bytes"] * BITS_PER_BYTE / b for b in BAUDS
                    }
                with (
                    _NoiseOverride(None if base == "zero" else scalars),
                    _PowerMapOverride(maps, unsel) as pm,
                ):
                    row[name] = ts.run_kiosk(data, args.cal, shot, tee)
                nbytes["power_map_unmatched_calls"] = pm.unmatched
                row["bytes"][name] = nbytes
                byte_rows.setdefault(name, []).append(nbytes)
                sel_rows.append(
                    {
                        "dump": os.path.basename(shot.path),
                        "variant": name,
                        "cells": nbytes["cells_selected"],
                        "per_frame": mask.sum(axis=1).tolist(),
                        "info": info,
                        "label_coverage": label_coverage(mask, meta, shot.labels),
                    }
                )
        if args.dither:
            row["C_dither"] = ts.run_kiosk(ts.dither_iq16(raw, seed=index), args.cal, shot, tee)
        row["oracle_tracks"] = rec.tracks
        rows.append(row)
        print(
            f"[{index + 1}/{len(shots)}] {os.path.basename(shot.path)} {tee_mode} "
            f"{time.time() - started:.0f}s",
            flush=True,
        )

    with (args.out / "rows.jsonl").open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, default=float) + "\n")
    with (args.out / "selection.jsonl").open("w", encoding="utf-8") as handle:
        for row in sel_rows:
            handle.write(json.dumps(row, default=float) + "\n")
    summary = ts.summarize(rows, variant_names)
    summary["_inputs"] = {"dumps": len(rows), "rejected": rejects}
    (args.out / "summary.json").write_text(json.dumps(summary, indent=1, default=float))
    ts.write_tables(args.out, [summary])
    bytes_summary = {}
    for name, values in byte_rows.items():
        totals = [v["total_bytes"] for v in values]
        bytes_summary[name] = {
            "median_total_bytes": float(np.median(totals)),
            "max_total_bytes": int(max(totals)),
            "median_summary_bytes": float(np.median([v["summary_bytes"] for v in values])),
            "median_cells": float(np.median([v["cells_selected"] for v in values])),
            "median_seconds": {str(b): float(np.median(totals)) * BITS_PER_BYTE / b for b in BAUDS},
            "max_seconds": {str(b): max(totals) * BITS_PER_BYTE / b for b in BAUDS},
        }
    bytes_summary["original"] = {
        "total_bytes": 732812,
        "seconds": {str(b): 732812 * BITS_PER_BYTE / b for b in BAUDS},
    }
    (args.out / "bytes.json").write_text(json.dumps(bytes_summary, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
