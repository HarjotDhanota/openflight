#!/usr/bin/env python3
"""RESEARCH: a two-round IWR6843 transfer simulated end to end (stage 1C).

Offline only. For each full wide IQ16 dump:

* **Round 1** (what the radar would compute from its own ring, nothing sent yet): the
  per-loop power map of every cell (TX1+TX3, 8 channels, burst and window MTI scope) in
  a chosen 16/32-bit format, the exact integer sum per absolute bin, TX (1 and 3) and RX
  of every sample (so the host gets the window-scope means exactly), and the two noise
  scalars (median MTI power per element, burst and window scope).
* **Host, round 1 alone:** the host's own trackers (launch range, ball walk, OPS-guided
  candidates, club walk) run on a capture rebuilt from round 1 only: every cell is a
  fill, the trackers' per-loop power comes from the map and every cube-wide noise median
  (``PreparedShotDump.noise_power`` and the two inline ``np.median`` calls in ``doa``)
  comes from the scalars. Walks, bins and frames are compared with the same steps on
  the full dump.
* **Request:** the cells along the round-1 walks, +-k bins (``lean``: the ball walk and
  the club walk with its impact windows; ``spec``: also every OPS-guided candidate and
  the club walk each would imply).
* **Round 2 (+ round 3...):** the requested cells at IQ16. The host rebuilds and runs the
  production kiosk path (``iwr_transfer_study.run_kiosk``) while logging every
  (frame, bin) cell an estimator reads. A read of a cell not yet sent becomes the next
  round's request; the loop ends when every read cell was sent.

Unsent cells are filled with integers whose per-bin sums equal the round-1 sums exactly
(window-scope means bit-identical); no noise fill. Production code is untouched.

Writes ``rows.jsonl``, ``trackers.jsonl``, ``summary.json``, ``tables.md`` and
``bytes.json`` into ``--out``.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from dataclasses import replace
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

import iwr_bin_select as bs  # noqa: E402  pylint: disable=wrong-import-position
import iwr_transfer_study as ts  # noqa: E402  pylint: disable=wrong-import-position

from openflight.iwr6843 import (  # noqa: E402
    club as iwr_club,
    lcmf as iwr_lcmf,
    runtime as iwr_runtime,
    shot as iwr_shot,
    tracking,
)
from openflight.iwr6843.club import (  # noqa: E402
    ClubWindowPolicy,
    _ImpactSegmentedTrack,
    find_club,
    impact_centered_window_s,
    pre_impact_window_s,
)
from openflight.iwr6843.dump import (  # noqa: E402
    is_range_snapshot,
    pack_dump,
    parse_dump,
    project_tx_pair,
)
from openflight.iwr6843.launch_range import estimate_launch_range, shot_ball_range  # noqa: E402
from openflight.iwr6843.lcmf import prepare_lcmf_capture  # noqa: E402
from openflight.iwr6843.recovery import find_recovery_candidates  # noqa: E402
from openflight.iwr6843.shot import (  # noqa: E402
    TX2_LOOP_PERIOD_S,
    geometry_from_header,
    impact_time_s,
    moving_ball_range_track,
)

BAUDS = (1_041_667, 2_000_000)
BITS_PER_BYTE = 10
RTT_S = 0.005  # protocol latency per round trip
CELL_BYTES = bs.CELL_BYTES  # 36 chirps x 4 RX x IQ16
N_TX = 3
N_LOOPS = 12
SCOPES = ("burst", "window")
CLUB_IMPACT_CORRECTION_S = -0.002  # IWR6843Runtime default
REQ_HEADER = 8  # command, sequence, length, CRC
MSG_HEADER = 8


# --------------------------------------------------------------------------- host patches


class _Ctx:
    """What the patched host functions read while one capture is processed."""

    reads: set | None = None
    noise: dict | None = None
    maps: dict | None = None
    unsel: np.ndarray | None = None
    unmatched = 0
    noise_hits = 0


class Tagged(np.ndarray):
    """An MTI or cube view that logs (frame, local bin) reads and knows its scope."""

    def __array_finalize__(self, obj):  # pylint: disable=arguments-differ
        self._scope = getattr(obj, "_scope", None)
        self._full_shape = getattr(obj, "_full_shape", None)

    def __getitem__(self, key):
        if _Ctx.reads is not None and isinstance(key, tuple) and len(key) >= 2:
            frame, rbin = key[0], key[-1]
            if isinstance(frame, (int, np.integer)) and isinstance(rbin, (int, np.integer)):
                _Ctx.reads.add((int(frame), int(rbin)))
        return super().__getitem__(key)

    def __array_function__(self, func, types, args, kwargs):
        # doa's inline noise: np.median(np.abs(mti) ** 2) over the whole MTI cube
        if func is np.median and _Ctx.noise is not None and len(args) == 1 and not kwargs:
            arr = args[0]
            scope = getattr(arr, "_scope", None)
            if scope in _Ctx.noise and arr.shape == getattr(arr, "_full_shape", None):
                _Ctx.noise_hits += 1
                return np.float64(_Ctx.noise[scope])
        return super().__array_function__(func, types, args, kwargs)


def _tag(arr: np.ndarray, scope: str | None) -> Tagged:
    out = np.asarray(arr).view(Tagged)
    out._scope = scope  # pylint: disable=protected-access
    out._full_shape = out.shape  # pylint: disable=protected-access
    return out


class Host:
    """Patch the host so it runs on a rebuilt capture as it would on the transfer.

    ``maps``/``unsel``: per-loop power for unsent cells comes from the round-1 map.
    ``noise``: cube-wide noise medians come from the round-1 scalars.
    ``reads``: a set that collects every (frame, local bin) an estimator reads.
    """

    def __init__(self, *, maps=None, unsel=None, noise=None, reads=None):
        self.cfg = (maps, unsel, noise, reads)
        self.saved = {}

    def __enter__(self):
        maps, unsel, noise, reads = self.cfg
        _Ctx.maps, _Ctx.unsel, _Ctx.noise, _Ctx.reads = maps, unsel, noise, reads
        _Ctx.unmatched = 0
        _Ctx.noise_hits = 0
        orig_mti, orig_power = tracking.mti_filter, tracking.loop_power
        orig_noise = iwr_shot.PreparedShotDump.noise_power
        orig_hmti = iwr_lcmf.PreparedLCMFCapture.horizontal_mti
        orig_club_parse, orig_lcmf_parse = iwr_club.parse_dump, iwr_lcmf.parse_dump
        self.saved = {
            "mti": orig_mti,
            "power": orig_power,
            "noise": orig_noise,
            "hmti": orig_hmti,
            "club_parse": orig_club_parse,
            "lcmf_parse": orig_lcmf_parse,
        }

        def mti_filter(cube, scope="burst", **kwargs):
            return _tag(orig_mti(np.asarray(cube), scope=scope, **kwargs), scope)

        def loop_power(mti):
            power = orig_power(np.asarray(mti))
            if _Ctx.maps is None:
                return power
            scope = getattr(mti, "_scope", None)
            if scope not in _Ctx.maps:
                _Ctx.unmatched += 1
                return power
            cells = np.repeat(_Ctx.unsel, mti.shape[2], axis=0)[:, : power.shape[1]]
            power = power.copy()
            power[cells] = _Ctx.maps[scope][cells]
            return power

        def noise_power(self_, scope="burst"):
            if _Ctx.noise is not None:
                return _Ctx.noise[scope]
            return orig_noise(self_, scope)

        def horizontal_mti(self_):
            return _tag(orig_hmti(self_), "horizontal")

        def parse(raw):
            meta, cube = orig_club_parse(raw)
            return meta, _tag(cube, None)

        tracking.mti_filter, tracking.loop_power = mti_filter, loop_power
        iwr_shot.PreparedShotDump.noise_power = noise_power
        iwr_lcmf.PreparedLCMFCapture.horizontal_mti = horizontal_mti
        iwr_club.parse_dump = parse
        iwr_lcmf.parse_dump = parse
        return self

    def __exit__(self, *exc):
        s = self.saved
        tracking.mti_filter, tracking.loop_power = s["mti"], s["power"]
        iwr_shot.PreparedShotDump.noise_power = s["noise"]
        iwr_lcmf.PreparedLCMFCapture.horizontal_mti = s["hmti"]
        iwr_club.parse_dump, iwr_lcmf.parse_dump = s["club_parse"], s["lcmf_parse"]
        _Ctx.maps = _Ctx.unsel = _Ctx.noise = _Ctx.reads = None


# --------------------------------------------------------------------------- round 1


def quantize(power: np.ndarray, fmt: str) -> np.ndarray:
    """The power map as the host decodes it from the round-1 format."""
    if fmt == "f32":
        return power.astype(np.float32).astype(float)
    if fmt == "log16":  # 1/500 dB code, 0-131 dB (stage 1A)
        code = np.clip(np.round(10 * np.log10(np.maximum(power, 1.0)) * 500), 0, 65535)
        return 10 ** (code / 5000)
    if fmt == "bf16":  # top 16 bits of the float32, round to nearest even
        u = power.astype(np.float32).view(np.uint32).astype(np.uint64)
        u = (u + 0x7FFF + ((u >> 16) & 1)) & 0xFFFF0000
        return u.astype(np.uint32).view(np.float32).astype(float)
    raise ValueError(fmt)


def round1(raw: bytes, meta: dict, cube: np.ndarray, fmt: str) -> dict:
    """The round-1 summary, computed from the dump's own samples (no host input)."""
    vertical = prepare_lcmf_capture(raw).vertical
    maps = {s: quantize(tracking.loop_power(np.asarray(vertical.mti(s))), fmt) for s in SCOPES}
    noise = {s: float(np.float32(vertical.noise_power(s))) for s in SCOPES}
    starts, counts = meta["range_bin_starts"], meta["range_bin_counts"]
    nf, cpf, nrx, ns = cube.shape
    tdm = cube.reshape(nf, cpf // N_TX, N_TX, nrx, ns)
    n_abs = max(s + c for s, c in zip(starts, counts))
    sums = np.zeros((2, nrx, n_abs, 2), np.int64)  # TX1/TX3 x RX x abs bin x (re, im)
    held = np.zeros(n_abs, bool)
    for f in range(nf):
        c = counts[f]
        block = tdm[f, :, ::2, :, :c].sum(axis=0)  # [2 tx, rx, bins]
        sums[:, :, starts[f] : starts[f] + c, 0] += np.rint(block.real).astype(np.int64)
        sums[:, :, starts[f] : starts[f] + c, 1] += np.rint(block.imag).astype(np.int64)
        held[starts[f] : starts[f] + c] = True
    map_bits = 32 if fmt == "f32" else 16
    n_cells = int(sum(counts))
    nbytes = {
        "header": ts._payload_offset(meta),  # pylint: disable=protected-access
        "power_map": n_cells * N_LOOPS * len(SCOPES) * map_bits // 8,
        "bin_sums": int(held.sum()) * 2 * nrx * 2 * 4,  # complex int32
        "noise_scalars": 8,
    }
    nbytes["total"] = sum(nbytes.values())
    assert np.abs(sums).max() < 2**31
    return {"maps": maps, "noise": noise, "sums": sums, "bytes": nbytes, "fmt": fmt}


def rebuild(meta: dict, cube: np.ndarray, sent: np.ndarray, r1: dict) -> bytes:
    """A production-format dump from the sent cells plus integer fills.

    Each unsent (frame, bin)'s TX1/TX3 samples are integers whose per absolute bin, TX
    and RX sum is the round-1 sum minus the sent cells' (so every window-scope mean the
    host computes is the full dump's); TX2 is zero.
    """
    starts, counts = meta["range_bin_starts"], meta["range_bin_counts"]
    nf, cpf, nrx, ns = cube.shape
    tdm = cube.reshape(nf, cpf // N_TX, N_TX, nrx, ns)
    sel = np.zeros((nf, ns), bool)
    sel[:, : sent.shape[1]] = sent[:, :ns]
    out = np.where(sel[:, None, None, None, :], tdm, 0)
    sums = r1["sums"].copy()
    n_abs = sums.shape[2]
    holders: list[list[tuple[int, int]]] = [[] for _ in range(n_abs)]
    for f in range(nf):
        for b in range(counts[f]):
            a = starts[f] + b
            if sel[f, b]:
                blk = tdm[f, :, ::2, :, b].sum(axis=0)  # [2, rx]
                sums[:, :, a, 0] -= np.rint(blk.real).astype(np.int64)
                sums[:, :, a, 1] -= np.rint(blk.imag).astype(np.int64)
            else:
                holders[a].append((f, b))
    for a, cells in enumerate(holders):
        if not cells:
            continue
        n = len(cells) * N_LOOPS
        for ti, tx in enumerate((0, 2)):
            for rx in range(nrx):
                vals = []
                for part in (0, 1):
                    total = int(sums[ti, rx, a, part])
                    q, r = divmod(total, n)
                    vals.append(q + (np.arange(n) < r))
                fill = (vals[0] + 1j * vals[1]).reshape(len(cells), N_LOOPS)
                for i, (f, b) in enumerate(cells):
                    out[f, :, tx, rx, b] = fill[i]
    if np.abs(out.real).max() > 32767 or np.abs(out.imag).max() > 32767:
        raise ValueError("fill out of int16 range")
    data = pack_dump(
        out.reshape(nf, cpf, nrx, ns),
        n_tx=meta["n_tx"],
        **ts._repack(meta),  # pylint: disable=protected-access
    )
    _m, back = parse_dump(data)
    if not np.array_equal(
        back[sel[:, None, None, :].repeat(cpf, 1).repeat(nrx, 2)],
        cube[sel[:, None, None, :].repeat(cpf, 1).repeat(nrx, 2)],
    ):
        raise AssertionError("sent cells are not bit-exact after the rebuild")
    return data


# --------------------------------------------------------------------------- host trackers


def _line(track, tag: str, spec: bool, margin_s: float) -> dict:
    return {
        "tag": tag,
        "spec": spec,
        "kind": "line",
        "slope_bins": float(track.slope_bins),
        "intercept_bins": float(track.intercept_bins),
        "t_lo": float(track.t_first) - margin_s,
        "t_hi": float(track.t_last) + margin_s,
        "facts": _facts(track),
    }


def _facts(track) -> dict | None:
    if track is None:
        return None
    return {
        "slope_bins": float(track.slope_bins),
        "intercept_bins": float(track.intercept_bins),
        "t_first": float(track.t_first),
        "t_last": float(track.t_last),
        "n_inliers": int(track.n_inliers),
        "rms_bins": float(track.rms_bins),
        "speed_ms": float(track.speed_ms),
        "quad_bins": list(track.quad_bins) if getattr(track, "quad_bins", None) else None,
    }


def _club_walks(
    raw: bytes, cal, shot, impact_t_s: float | None, tag: str, spec: bool, margin_s: float
) -> tuple[list[dict], dict]:
    """The production club tracker (``estimate_club_path`` up to ``find_club``) and the
    cells its impact-centred path and attack windows read."""
    if not shot.club_speed_mph or impact_t_s is None:
        return [], {"status": "no_club_speed_or_impact"}
    impact = impact_t_s + CLUB_IMPACT_CORRECTION_S
    projected = project_tx_pair(raw, (0, 2))
    meta, cube = parse_dump(projected)
    geo = geometry_from_header(meta, loop_period_s=TX2_LOOP_PERIOD_S)
    window = pre_impact_window_s(geo, impact)
    if window is None:
        return [], {"status": "no_pre_impact_frames"}
    mti = tracking.mti_filter(cube, range_domain=is_range_snapshot(meta), geometry=geo)
    sel = find_club(
        mti,
        geo,
        tee_range_m=cal.tee_range_m,
        window_s=window,
        ops_club_speed_mph=shot.club_speed_mph,
        impact_t_s=impact,
        tee_uncertainty_m=cal.tee_range_uncertainty_m,
    )
    if sel is None:
        return [], {"status": "no_club_track"}
    track = sel.track
    walks = [_line(track, tag, spec, margin_s)]
    policy = ClubWindowPolicy()
    res = geo.range_res_m
    for name, pre, post, scale in (
        ("path", policy.path_pre_frames, policy.path_post_frames, policy.path_post_speed_scale),
        (
            "attack",
            policy.attack_pre_frames,
            policy.attack_post_frames,
            policy.attack_post_speed_scale,
        ),
    ):
        win = impact_centered_window_s(geo, impact, pre_frames=pre, post_frames=post)
        if win is None:
            continue
        seg = _ImpactSegmentedTrack(
            base=track,
            impact_t_s=impact,
            range_res_m=res,
            post_speed_scale=scale,
            t_first=min(track.t_first, win[0]),
            t_last=max(track.t_last, win[1]),
        )
        walks.append(
            {
                "tag": f"{tag}:{name}_window",
                "spec": spec,
                "kind": "segmented",
                "seg": seg,
                "t_lo": seg.t_first - margin_s,
                "t_hi": seg.t_last + margin_s,
            }
        )
    return walks, {"status": "found", "mode": sel.mode, "impact_t_s": impact, **_facts(track)}


def host_trackers(raw: bytes, shot, cal_path: str, *, margin_s: float) -> dict:
    """Every host step that decides which cells the estimators read, run in order.

    Mirrors ``IWR6843Runtime.process_shot``: launch range -> tee (else the configured
    fallback, else withheld) -> ball walk -> baseline impact -> OPS-guided candidates ->
    club walk at that impact (and, speculatively, at each candidate's impact)."""
    out: dict = {"walks": [], "recorded": []}
    cal = ts._calibration(cal_path, shot, None)  # pylint: disable=protected-access
    prepared = prepare_lcmf_capture(raw)
    vertical = prepared.vertical
    geo = vertical.geometry
    with bs._Recorder() as rec:  # pylint: disable=protected-access
        lr = estimate_launch_range(
            vertical,
            ball_speed_mph=shot.ball_speed_mph,
            club_speed_mph=shot.club_speed_mph,
            prior=None,
            net_range_m=shot.net_range_m,
            range_bias_m=float(getattr(cal, "range_bias_m", 0.0) or 0.0),
        )
    out["recorded"] += [{**t, "step": "launch_range"} for t in rec.tracks]
    out["launch_range"] = {
        "status": lr.status,
        "range_m": lr.range_m,
        "method": getattr(lr, "method", None),
        "impact_t_s": getattr(lr, "impact_t_s", None),
        "reason": lr.reason,
    }
    br = shot_ball_range(lr, None)
    if br.range_m is not None:
        out["tee_mode"] = "kiosk_launch_range"
        shot_cal = replace(
            cal,
            tee_range_m=float(br.range_m),
            tee_range_uncertainty_m=float(br.uncertainty_m or 0.0),
        )
    elif shot.tee_fallback_m is not None:
        out["tee_mode"] = "configured_tee_from_manifest"
        shot_cal = ts._calibration(cal_path, shot, shot.tee_fallback_m)  # pylint: disable=protected-access
    else:
        out["tee_mode"] = "withheld"
        return out
    out["tee_m"] = float(shot_cal.tee_range_m)
    with bs._Recorder() as rec:  # pylint: disable=protected-access
        track, scope = moving_ball_range_track(
            vertical, club=shot.club, net_range_m=shot.net_range_m
        )
    out["recorded"] += [{**t, "step": "ball"} for t in rec.tracks]
    out["ball"] = {"scope": scope, **(_facts(track) or {})} if track is not None else None
    impact = (
        impact_time_s(track, geo, shot_cal.tee_range_m, range_bias_m=shot_cal.range_bias_m)
        if track is not None
        else None
    )
    out["impact_t_s"] = impact
    if track is not None:
        out["walks"].append(_line(track, f"ball:{scope}", False, margin_s))
    with bs._Recorder() as rec:  # pylint: disable=protected-access
        cands = iwr_runtime._credible_ops_candidates(  # pylint: disable=protected-access
            find_recovery_candidates(
                raw,
                shot_cal,
                ball_speed_mph=shot.ball_speed_mph,
                net_range_m=shot.net_range_m,
                prepared=vertical,
            )
        )
    out["ops_candidates"] = [{"scope": c.scope, **_facts(c.track)} for c in cands]
    for i, c in enumerate(cands):
        out["walks"].append(_line(c.track, f"ops_candidate{i}:{c.scope}", True, margin_s))
    with bs._Recorder() as rec:  # pylint: disable=protected-access
        walks, info = _club_walks(raw, shot_cal, shot, impact, "club", False, margin_s)
    out["recorded"] += [{**t, "step": "club"} for t in rec.tracks]
    out["walks"] += walks
    out["club"] = info
    out["candidate_clubs"] = []
    seen = {None if impact is None else round(impact, 9)}
    for i, c in enumerate(cands):
        imp = impact_time_s(c.track, geo, shot_cal.tee_range_m, range_bias_m=shot_cal.range_bias_m)
        key = None if imp is None else round(imp, 9)
        if key in seen:
            continue
        seen.add(key)
        walks, info = _club_walks(raw, shot_cal, shot, imp, f"club@ops{i}", True, margin_s)
        out["walks"] += walks
        out["candidate_clubs"].append(info)
    return out


def walk_mask(walk: dict, meta: dict, times: np.ndarray, k: int) -> np.ndarray:
    starts, counts = tuple(meta["range_bin_starts"]), tuple(meta["range_bin_counts"])
    mask = np.zeros((meta["n_frames"], max(counts)), bool)
    if walk["kind"] == "line":

        def bin_at(t, w=walk):
            return w["slope_bins"] * t + w["intercept_bins"]

    else:
        bin_at = walk["seg"].bin_at
    bs.cells_from_walk(mask, starts, counts, times, bin_at, walk["t_lo"], walk["t_hi"], k)
    return mask


def request_mask(walks: list[dict], meta, times, k: int, policy: str) -> np.ndarray:
    counts = meta["range_bin_counts"]
    mask = np.zeros((meta["n_frames"], max(counts)), bool)
    for w in walks:
        if w["spec"] and policy != "spec":
            continue
        mask |= walk_mask(w, meta, times, k)
    return mask


def rle_bytes(mask: np.ndarray) -> int:
    """Request as per-frame bin runs: 1 B run count per frame + 2 B (start, length) per run."""
    total = 0
    for row in mask:
        edges = np.diff(np.concatenate(([0], row.astype(int), [0])))
        total += 1 + 2 * int((edges == 1).sum())
    return REQ_HEADER + total


# --------------------------------------------------------------------------- comparison


def _diff(a, b) -> tuple[bool, float]:
    """(every discrete field equal: types, None-ness, ints, strings, lengths; max relative
    difference of the float fields, relative to max(1, |a|))."""
    if a is None or b is None:
        return (a is None and b is None), 0.0
    if isinstance(a, dict) and isinstance(b, dict):
        if a.keys() != b.keys():
            return False, 0.0
        parts = [_diff(a[x], b[x]) for x in a]
        return all(p[0] for p in parts), max([p[1] for p in parts] or [0.0])
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        if len(a) != len(b):
            return False, 0.0
        parts = [_diff(x, y) for x, y in zip(a, b)]
        return all(p[0] for p in parts), max([p[1] for p in parts] or [0.0])
    if isinstance(a, float) or isinstance(b, float):
        if isinstance(a, bool) or isinstance(b, bool):
            return a == b, 0.0
        return True, abs(float(a) - float(b)) / max(1.0, abs(float(a)))
    return a == b, 0.0


def _close(a, b, tol=1e-9) -> bool:
    same, rel = _diff(a, b)
    return same and rel <= tol


def compare_trackers(full: dict, r1: dict, meta, times) -> dict:
    out = {}
    for key in (
        "tee_mode",
        "tee_m",
        "launch_range",
        "ball",
        "impact_t_s",
        "ops_candidates",
        "club",
        "candidate_clubs",
        "recorded",
    ):
        same, rel = _diff(full.get(key), r1.get(key))
        out[key] = {
            "discrete_identical": same,
            "max_rel_diff": rel,
            "bit_identical": same and rel == 0.0,
        }
    out["recorded"]["n_full"] = len(full["recorded"])
    out["recorded"]["n_round1"] = len(r1["recorded"])
    # cell by cell, walk by walk (k = 0)
    tags_f = [w["tag"] for w in full["walks"]]
    tags_r = [w["tag"] for w in r1["walks"]]
    per_walk = []
    if tags_f == tags_r:
        for a, b in zip(full["walks"], r1["walks"]):
            ma, mb = walk_mask(a, meta, times, 0), walk_mask(b, meta, times, 0)
            per_walk.append(
                {"tag": a["tag"], "cells": int(ma.sum()), "cells_differ": int((ma ^ mb).sum())}
            )
    out["walk_cells_k0"] = {
        "tags_same": tags_f == tags_r,
        "tags_full": tags_f,
        "tags_round1": tags_r,
        "identical": tags_f == tags_r and all(w["cells_differ"] == 0 for w in per_walk),
        "per_walk": per_walk,
    }
    return out


def dilate(mask: np.ndarray, k: int, counts) -> np.ndarray:
    out = mask.copy()
    for d in range(1, k + 1):
        out[:, d:] |= mask[:, :-d]
        out[:, :-d] |= mask[:, d:]
    for f, c in enumerate(counts):
        out[f, c:] = False
    return out


# --------------------------------------------------------------------------- end to end


def end_to_end(raw, meta, cube, shot, cal_path, tee, r1, sent0, max_rounds=6) -> dict:
    sent = sent0.copy()
    rounds = [int(sent.sum())]
    compute_s = []
    result = None
    error = None
    for _ in range(max_rounds):
        data = rebuild(meta, cube, sent, r1)
        reads: set = set()
        error = None
        t0 = time.perf_counter()
        with Host(maps=r1["maps"], unsel=~sent, noise=r1["noise"], reads=reads):
            try:
                result = ts.run_kiosk(data, cal_path, shot, tee)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                error = f"{type(exc).__name__}: {exc}"
                result = None
            unmatched = _Ctx.unmatched
        compute_s.append(time.perf_counter() - t0)
        missing = [(f, b) for f, b in reads if not sent[f, b]]
        if not missing:
            break
        for f, b in missing:
            sent[f, b] = True
        rounds.append(len(set(missing)))
    return {
        "result": result,
        "error": error,
        "rounds_cells": rounds,
        "n_rounds_after_r1": len(rounds),
        "compute_s": compute_s,
        "unmatched_power_calls": unmatched,
        "final_cells": int(sent.sum()),
        "sent": sent,
    }


def seconds(nbytes: int, baud: int) -> float:
    return nbytes * BITS_PER_BYTE / baud


# --------------------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:  # pylint: disable=too-many-locals,too-many-statements
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--recordings", required=True, type=Path)
    parser.add_argument("--outdoor", required=True, type=Path)
    parser.add_argument("--cal", default="config/iwr6843_calibration_reference.json")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--only", default="", help="comma list of dump basenames")
    parser.add_argument("--ks", default="0,1,2,3")
    parser.add_argument("--policies", default="lean,spec,dryrun")
    parser.add_argument("--map-fmt", default="log16", help="end-to-end map format")
    parser.add_argument("--tracker-fmts", default="f32,log16,bf16")
    parser.add_argument("--margin-ms", type=float, default=2.0)
    parser.add_argument("--floor-rows", type=Path, default=None, help="1A rows.jsonl (C_dither)")
    parser.add_argument(
        "--ablate-median",
        action="store_true",
        help="only: send the full dump's read set, with and without the doa noise-median hook",
    )
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    margin_s = args.margin_ms / 1000.0

    rejects: list = []
    shots = ts.recording_shots(args.recordings, rejects) + ts.outdoor_shots(args.outdoor, rejects)
    if args.only:
        names = set(args.only.split(","))
        shots = [s for s in shots if os.path.basename(s.path) in names]
    if args.limit:
        shots = shots[: args.limit]
    ks = [int(k) for k in args.ks.split(",")]
    policies = args.policies.split(",")
    tracker_fmts = args.tracker_fmts.split(",")
    variants = [f"{p}_k{k}" for p in policies for k in ks]
    floor = {}
    if args.floor_rows and args.floor_rows.exists():
        for line in args.floor_rows.read_text().splitlines():
            row = json.loads(line)
            if "C_dither" in row:
                floor[os.path.basename(row["path"])] = (row["original"], row["C_dither"])

    if args.ablate_median:
        return ablate_median(shots, args)
    rows, tracker_rows = [], []
    started = time.time()
    for index, shot in enumerate(shots):
        name = os.path.basename(shot.path)
        raw = Path(shot.path).read_bytes()
        meta, cube = parse_dump(raw)
        times = bs.loop_times(raw)
        # reference: the full dump, read set logged (no overrides)
        full_reads: set = set()
        with Host(reads=full_reads):
            original = ts.run_kiosk(raw, args.cal, shot, None)
        tee_mode, tee = "kiosk_launch_range", None
        if original["withheld"] and shot.tee_fallback_m is not None:
            tee_mode, tee = "configured_tee_from_manifest", shot.tee_fallback_m
            full_reads = set()
            with Host(reads=full_reads):
                original = ts.run_kiosk(raw, args.cal, shot, tee)
        full_read_mask = np.zeros((meta["n_frames"], max(meta["range_bin_counts"])), bool)
        for f, b in full_reads:
            full_read_mask[f, b] = True
        with Host():
            full_tr = host_trackers(raw, shot, args.cal, margin_s=margin_s)
        row = {
            "path": shot.path,
            "dataset": shot.dataset,
            "tee_mode": tee_mode,
            "original": original,
            "full_read_cells": int(full_read_mask.sum()),
            "bytes": {"original": len(raw)},
        }
        if name in floor:
            row["floor_original_matches"] = _close(
                {k: v for k, v in floor[name][0].items()}, original, 1e-9
            )
            row["C_dither"] = floor[name][1]
        # round 1 alone, per map format
        r1_by_fmt = {}
        for fmt in tracker_fmts + ([args.map_fmt] if args.map_fmt not in tracker_fmts else []):
            r1 = round1(raw, meta, cube, fmt)
            r1_by_fmt[fmt] = r1
            nothing = np.zeros((meta["n_frames"], max(meta["range_bin_counts"])), bool)
            data0 = rebuild(meta, cube, nothing, r1)
            t0 = time.perf_counter()
            with Host(maps=r1["maps"], unsel=~nothing, noise=r1["noise"]):
                r1_tr = host_trackers(data0, shot, args.cal, margin_s=margin_s)
                unmatched = _Ctx.unmatched
            host_s = time.perf_counter() - t0
            cmp = compare_trackers(full_tr, r1_tr, meta, times)
            tracker_rows.append(
                {
                    "dump": name,
                    "fmt": fmt,
                    "host_tracker_s": host_s,
                    "unmatched_power_calls": unmatched,
                    "compare": cmp,
                    "tee_mode_full": full_tr.get("tee_mode"),
                    "tee_mode_round1": r1_tr.get("tee_mode"),
                    "n_ops_candidates": len(full_tr.get("ops_candidates") or []),
                    "full_launch_range": full_tr.get("launch_range"),
                    "round1_launch_range": r1_tr.get("launch_range"),
                }
            )
            if fmt == args.map_fmt:
                row["round1_bytes"] = r1["bytes"]
                row["round1_host_s"] = host_s
                row["round1_tracker_tee_mode"] = r1_tr.get("tee_mode")
                main_tr = r1_tr
                # the dry run: the whole kiosk path on round 1 alone, reads logged
                dry_reads: set = set()
                tee_r1 = (
                    shot.tee_fallback_m
                    if r1_tr.get("tee_mode") == "configured_tee_from_manifest"
                    else None
                )
                t0 = time.perf_counter()
                with Host(maps=r1["maps"], unsel=~nothing, noise=r1["noise"], reads=dry_reads):
                    try:
                        ts.run_kiosk(data0, args.cal, shot, tee_r1)
                        dry_error = None
                    except Exception as exc:  # pylint: disable=broad-exception-caught
                        dry_error = f"{type(exc).__name__}: {exc}"
                row["dryrun_host_s"] = time.perf_counter() - t0
                row["dryrun_error"] = dry_error
                dry_mask = np.zeros_like(nothing)
                for f, b in dry_reads:
                    dry_mask[f, b] = True
                row["dryrun_read_cells"] = int(dry_mask.sum())
                row["dryrun_covers_full_reads"] = bool(np.all(dry_mask | ~full_read_mask))
        r1 = r1_by_fmt[args.map_fmt]
        row["full_read_within"] = {}
        for policy in policies:
            for k in ks:
                vname = f"{policy}_k{k}"
                if main_tr.get("tee_mode") == "withheld":
                    row[vname] = {
                        **{k2: None for k2 in original},
                        "withheld": "tee_range_unresolved",
                    }
                    row["bytes"][vname] = {"withheld": True}
                    continue
                if policy == "dryrun":
                    req = dilate(dry_mask, k, meta["range_bin_counts"])
                else:
                    req = request_mask(main_tr["walks"], meta, times, k, policy)
                tee_r1 = (
                    shot.tee_fallback_m
                    if main_tr["tee_mode"] == "configured_tee_from_manifest"
                    else None
                )
                e2e = end_to_end(raw, meta, cube, shot, args.cal, tee_r1, r1, req)
                row[vname] = e2e["result"] or {k2: None for k2 in original}
                row["full_read_within"][vname] = bool(np.all(e2e["sent"] | ~full_read_mask))
                r_cells = e2e["rounds_cells"]
                round_bytes = [MSG_HEADER + c * CELL_BYTES for c in r_cells]
                req_bytes = [rle_bytes(req)] + [REQ_HEADER + 3 * c for c in r_cells[1:]]
                row["bytes"][vname] = {
                    "round1": r1["bytes"]["total"],
                    "rounds_cells": r_cells,
                    "round_bytes": round_bytes,
                    "request_bytes": req_bytes,
                    "request_bitmask_bytes": REQ_HEADER + meta["n_frames"] * 7,
                    "n_walks": sum(
                        1 for w in main_tr["walks"] if policy == "spec" or not w["spec"]
                    ),
                    "compute_s": e2e["compute_s"],
                    "pre_request_host_s": row["round1_host_s"]
                    + (row["dryrun_host_s"] if policy == "dryrun" else 0.0),
                    "error": e2e["error"],
                    "unmatched_power_calls": e2e["unmatched_power_calls"],
                }
        rows.append(row)
        print(
            f"[{index + 1}/{len(shots)}] {name} {tee_mode} {time.time() - started:.0f}s", flush=True
        )

    with (args.out / "rows.jsonl").open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, default=float) + "\n")
    with (args.out / "trackers.jsonl").open("w", encoding="utf-8") as handle:
        for row in tracker_rows:
            handle.write(json.dumps(row, default=float) + "\n")
    names = variants + (["C_dither"] if floor else [])
    summary = ts.summarize([r for r in rows if all(v in r and r[v] for v in variants)], names)
    summary["_inputs"] = {
        "dumps": len(rows),
        "rejected": rejects,
        "map_fmt": args.map_fmt,
        "margin_ms": args.margin_ms,
    }
    (args.out / "summary.json").write_text(json.dumps(summary, indent=1, default=float))
    ts.write_tables(args.out, [summary])
    (args.out / "bytes.json").write_text(json.dumps(byte_summary(rows, variants), indent=1))
    (args.out / "trackers_summary.json").write_text(
        json.dumps(tracker_summary(tracker_rows), indent=1, default=float)
    )
    return 0


def ablate_median(shots, args) -> int:
    """Send exactly the cells the full dump's estimators read; the noise for doa's inline
    ``np.median`` either comes from the round-1 scalar or from the rebuilt cube."""
    hooked = Tagged.__array_function__

    def unhooked(self, func, types, a, kw):
        return super(Tagged, self).__array_function__(func, types, a, kw)

    out = []
    for shot in shots:
        raw = Path(shot.path).read_bytes()
        meta, cube = parse_dump(raw)
        reads: set = set()
        with Host(reads=reads):
            original = ts.run_kiosk(raw, args.cal, shot, None)
        tee = None
        if original["withheld"] and shot.tee_fallback_m is not None:
            tee = shot.tee_fallback_m
            reads = set()
            with Host(reads=reads):
                original = ts.run_kiosk(raw, args.cal, shot, tee)
        sent = np.zeros((meta["n_frames"], max(meta["range_bin_counts"])), bool)
        for f, b in reads:
            sent[f, b] = True
        r1 = round1(raw, meta, cube, args.map_fmt)
        row = {"path": shot.path, "original": original, "cells": int(sent.sum())}
        for label, func in (("scalar", hooked), ("no_median_hook", unhooked)):
            Tagged.__array_function__ = func
            try:
                e2e = end_to_end(raw, meta, cube, shot, args.cal, tee, r1, sent)
            finally:
                Tagged.__array_function__ = hooked
            row[label] = e2e["result"]
            row[f"{label}_rounds"] = e2e["rounds_cells"]
        out.append(row)
        print(os.path.basename(shot.path), flush=True)
    with (args.out / "median_hook_rows.jsonl").open("w", encoding="utf-8") as handle:
        for row in out:
            handle.write(json.dumps(row, default=float) + "\n")
    summary = ts.summarize(out, ["scalar", "no_median_hook"])
    (args.out / "median_hook_summary.json").write_text(json.dumps(summary, indent=1, default=float))
    return 0


def _stats(values: list[float]) -> dict:
    if not values:
        return {}
    return {
        "median": float(statistics.median(values)),
        "max": float(max(values)),
        "p90": float(ts._pct(values, 0.9)),
    }  # pylint: disable=protected-access


def byte_summary(rows: list[dict], variants: list[str]) -> dict:
    out = {}
    for v in variants:
        per = [r["bytes"][v] for r in rows if v in r["bytes"] and "rounds_cells" in r["bytes"][v]]
        if not per:
            continue
        r1_compute = [p["pre_request_host_s"] for p in per]
        entry = {
            "n": len(per),
            "rounds_after_r1": dict(
                sorted(
                    {
                        n: sum(1 for p in per if len(p["rounds_cells"]) == n)
                        for n in {len(p["rounds_cells"]) for p in per}
                    }.items()
                )
            ),
            "round1_bytes": _stats([p["round1"] for p in per]),
            "round2_cells": _stats([p["rounds_cells"][0] for p in per]),
            "round2_bytes": _stats([p["round_bytes"][0] for p in per]),
            "later_round_bytes": _stats([sum(p["round_bytes"][1:]) for p in per]),
            "request_bytes": _stats([sum(p["request_bytes"]) for p in per]),
            "errors": sum(1 for p in per if p["error"]),
        }
        for baud in BAUDS:
            link = []
            total = []
            for p, h1 in zip(per, r1_compute):
                n_rt = 1 + len(p["rounds_cells"])  # round 1 + each request/response
                down = p["round1"] + sum(p["round_bytes"])
                up = sum(p["request_bytes"])
                t_link = seconds(down + up, baud) + n_rt * RTT_S
                link.append(t_link)
                # host compute: trackers on round 1, then a kiosk run after every later round
                total.append(t_link + h1 + sum(p["compute_s"]))
            entry[f"link_s_{baud}"] = _stats(link)
            entry[f"total_with_pc_compute_s_{baud}"] = _stats(total)
            entry[f"round1_s_{baud}"] = _stats([seconds(p["round1"], baud) + RTT_S for p in per])
            entry[f"round2_s_{baud}"] = _stats(
                [seconds(p["round_bytes"][0] + p["request_bytes"][0], baud) + RTT_S for p in per]
            )
            entry[f"later_rounds_s_{baud}"] = _stats(
                [
                    seconds(sum(p["round_bytes"][1:]) + sum(p["request_bytes"][1:]), baud)
                    + RTT_S * (len(p["rounds_cells"]) - 1)
                    for p in per
                ]
            )
        entry["pc_round1_tracker_s"] = _stats(r1_compute)
        entry["pc_estimator_s_per_run"] = _stats([c for p in per for c in p["compute_s"]])
        out[v] = entry
    out["original"] = {"bytes": 732812, "seconds": {str(b): seconds(732812, b) for b in BAUDS}}
    return out


def tracker_summary(tracker_rows: list[dict]) -> dict:
    out = {}
    for fmt in sorted({r["fmt"] for r in tracker_rows}):
        rs = [r for r in tracker_rows if r["fmt"] == fmt]
        keys = rs[0]["compare"].keys()
        out[fmt] = {
            "n": len(rs),
            **{
                key: (
                    {
                        "identical": sum(1 for r in rs if r["compare"][key]["identical"]),
                        "tags_same": sum(1 for r in rs if r["compare"][key]["tags_same"]),
                        "cells_differ_total": sum(
                            w["cells_differ"] for r in rs for w in r["compare"][key]["per_walk"]
                        ),
                    }
                    if key == "walk_cells_k0"
                    else {
                        "discrete_identical": sum(
                            1 for r in rs if r["compare"][key]["discrete_identical"]
                        ),
                        "bit_identical": sum(1 for r in rs if r["compare"][key]["bit_identical"]),
                        "max_rel_diff": max(r["compare"][key]["max_rel_diff"] for r in rs),
                    }
                )
                for key in keys
            },
            "host_tracker_s": _stats([r["host_tracker_s"] for r in rs]),
            "unmatched_power_calls": sum(r["unmatched_power_calls"] for r in rs),
            "dumps_with_ops_candidates": sum(1 for r in rs if r["n_ops_candidates"]),
        }
    return out


if __name__ == "__main__":
    sys.exit(main())
