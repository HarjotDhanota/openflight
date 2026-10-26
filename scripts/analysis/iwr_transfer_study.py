#!/usr/bin/env python3
"""RESEARCH: can the IWR6843 dump get smaller without moving the measurements?

Offline only. Each full-size wide-IQ16 dump (24 frames x 3 ms, 9/7/8 phases, 53 bins) is
run through the kiosk's IWR path (``IWR6843Runtime.process_shot`` with a fake capture
monitor: launch-range estimate, then ``process_raw_capture``) as captured and as:

* ``A``: an IQ8 dump derived from the same event, encoded the way the production
  IQ8 firmware build (``L3_RING_IQ8`` + ``L3_IQ8_EDMA_PACK``) does it: the HWA output is
  right-shifted by ``log2(iq8Scale)`` (``dstScale``), then the EDMA copies the LOW BYTE
  of each int16 component (so values outside int8 wrap, they do not clip), and every
  frame carries the fixed scale. The HWA's rounding on ``dstScale`` is not stated in the
  repo, so both ``floor`` and ``round-half-up`` are run.
* ``B7`` / ``B5``: the earliest 2 / 4 pre-trigger frames removed at the dump level
  (header frame count, timed descriptors re-based to a zero first offset, payload). The
  remaining frames keep their bytes and relative timing, which is what a 7- or 5-frame
  pre ring would have sent.

Writes ``rows.jsonl``, ``summary.json`` and ``snr.json`` into ``--out``.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from openflight.iwr6843.calibration_session import clone_calibration
from openflight.iwr6843.club import ClubWindowPolicy
from openflight.iwr6843.dump import (
    SAMPLE_RANGE_FFT_IQ8_VARIABLE_TIMED,
    SAMPLE_RANGE_FFT_IQ16_VARIABLE_TIMED,
    TIMED_FRAME_DESCRIPTOR,
    pack_dump,
    parse_dump,
    parse_header,
)
from openflight.iwr6843.lcmf import Calibration
from openflight.iwr6843.runtime import IWR6843Runtime

WIDE_STARTS = (20,) * 9 + (32,) * 7 + (47,) * 8
BIN_M = 6.0 / 128
MPS_TO_MPH = 2.2369362920544


# --------------------------------------------------------------------------- variants


def _payload_offset(meta: dict) -> int:
    return meta["header_nbytes"] + (meta.get("frame_metadata_nbytes") or 0)


def iq16_components(raw: bytes) -> tuple[dict, np.ndarray]:
    """The IQ16 payload as the int16 components the HWA wrote (Im, Re order)."""
    meta, _cube = parse_dump(raw)
    if meta["sample_fmt"] != SAMPLE_RANGE_FFT_IQ16_VARIABLE_TIMED:
        raise ValueError("not an IQ16 timed dump")
    n = sum(meta["chirps_per_frame"] * meta["n_rx"] * c for c in meta["range_bin_counts"])
    comps = np.frombuffer(raw, dtype="<i2", offset=_payload_offset(meta), count=2 * n)
    return meta, comps


def hwa_shift(values: np.ndarray, shift: int, rounding: str) -> np.ndarray:
    """HWA ``dstScale`` right shift of int16 components, saturated to int16."""
    v = values.astype(np.int32)
    if shift == 0:
        out = v
    elif rounding == "floor":
        out = v >> shift
    elif rounding == "round_half_up":
        out = (v + (1 << (shift - 1))) >> shift
    else:
        raise ValueError(rounding)
    return np.clip(out, -32768, 32767)


def edma_low_byte(values: np.ndarray) -> np.ndarray:
    """The EDMA copy of each int16's low byte, read back as int8 (wraps, never clips)."""
    return (values.astype(np.int32) & 0xFF).astype(np.uint8).view(np.int8)


def encode_iq8(raw: bytes, scale: int, rounding: str) -> tuple[bytes, dict]:
    """IQ16 dump bytes -> the IQ8 dump the EDMA-pack firmware would send for the event."""
    if scale < 16 or scale > 256 or scale & (scale - 1):
        raise ValueError("iq8Scale is a power of two from 16 to 256")
    shift = int(math.log2(scale))
    meta, comps = iq16_components(raw)
    shifted = hwa_shift(comps, shift, rounding)
    packed = edma_low_byte(shifted)
    stats = {
        "components": int(comps.size),
        "iq16_saturated": int(np.count_nonzero((comps == 32767) | (comps == -32768))),
        "iq8_wrapped": int(np.count_nonzero((shifted > 127) | (shifted < -128))),
        "iq16_max_abs": int(np.max(np.abs(comps.astype(np.int32)))),
    }
    # Skeleton from the reference packer (header, temperature, descriptors, scale table),
    # then the firmware's own fixed scale and low-byte payload.
    nf = meta["n_frames"]
    skeleton_cube = np.zeros(
        (nf, meta["chirps_per_frame"], meta["n_rx"], meta["n_samples"]), complex
    )
    kwargs = _repack(meta)
    kwargs["sample_fmt"] = SAMPLE_RANGE_FFT_IQ8_VARIABLE_TIMED
    skeleton = bytearray(pack_dump(skeleton_cube, n_tx=meta["n_tx"], **kwargs))
    smeta = parse_header(bytes(skeleton))
    scale_at = smeta["header_nbytes"] + TIMED_FRAME_DESCRIPTOR.size * nf
    skeleton[scale_at : scale_at + 2 * nf] = np.full(nf, scale, dtype="<u2").tobytes()
    body_at = scale_at + 2 * nf
    if len(skeleton) - body_at != packed.size:
        raise ValueError("IQ8 payload size mismatch")
    skeleton[body_at:] = packed.tobytes()
    out = bytes(skeleton)
    # Self-check: the decoder reads back exactly low_byte * scale.
    _m, cube8 = parse_dump(out)
    _m16, cube16 = parse_dump(raw)
    expect = packed.astype(np.float64) * scale
    got = _flatten_iq(cube8, meta)
    if not np.array_equal(got, expect):
        raise AssertionError("IQ8 encode/decode self-check failed")
    stats["max_abs_error"] = float(np.max(np.abs(_flatten_iq(cube16, meta) - expect)))
    return out, stats


def _flatten_iq(cube: np.ndarray, meta: dict) -> np.ndarray:
    parts = []
    for frame, count in enumerate(meta["range_bin_counts"]):
        flat = cube[frame, ..., :count].reshape(-1)
        iq = np.empty(flat.size * 2)
        iq[0::2] = flat.imag
        iq[1::2] = flat.real
        parts.append(iq)
    return np.concatenate(parts)


def _repack(meta: dict) -> dict:
    return {
        "trigger_frame": meta["trigger_frame"],
        "version": meta["version"],
        "frame_period_us": meta.get("frame_period_us", 0),
        "sample_fmt": meta["sample_fmt"],
        "range_bin_starts": meta.get("range_bin_starts"),
        "range_bin_counts": meta.get("range_bin_counts"),
        "frame_time_offsets_us": meta.get("frame_time_offsets_us"),
        "temperature_report": meta.get("temperature_report"),
    }


def dither_iq16(raw: bytes, seed: int) -> bytes:
    """Control: the same IQ16 dump with +-1 LSB of random noise on every component.

    Not a transfer option. It measures how far each estimator moves under a perturbation
    ~100x smaller than IQ8's, so a variant's change can be read against that floor.
    """
    meta, comps = iq16_components(raw)
    rng = np.random.default_rng(seed)
    noisy = np.clip(comps.astype(np.int32) + rng.integers(-1, 2, comps.size), -32768, 32767)
    at = _payload_offset(meta)
    out = bytearray(raw)
    out[at : at + 2 * comps.size] = noisy.astype("<i2").tobytes()
    return bytes(out)


def trim_pre_frames(raw: bytes, drop: int) -> bytes:
    """Drop the earliest ``drop`` (pre-trigger) frames: what a shorter pre ring sends."""
    meta, cube = parse_dump(raw)
    if meta.get("capture_evidence") is not None:
        raise ValueError("evidence dumps (v8/v9) need their records trimmed too")
    if meta["trigger_frame"] != 0:
        raise ValueError("expects time-ordered slots (trigger_frame 0)")
    pre = sum(1 for s in meta["range_bin_starts"] if s == meta["range_bin_starts"][0])
    if drop >= pre:
        raise ValueError("cannot drop every pre-trigger frame")
    kwargs = _repack(meta)
    offsets = meta["frame_time_offsets_us"][drop:]
    kwargs["frame_time_offsets_us"] = tuple(o - offsets[0] for o in offsets)
    kwargs["range_bin_starts"] = tuple(meta["range_bin_starts"][drop:])
    kwargs["range_bin_counts"] = tuple(meta["range_bin_counts"][drop:])
    return pack_dump(cube[drop:], n_tx=meta["n_tx"], **kwargs)


# --------------------------------------------------------------------------- inputs


@dataclass
class Shot:
    path: str
    dataset: str
    ball_speed_mph: float
    club_speed_mph: float | None
    club: str | None
    tee_fallback_m: float | None
    input_source: str
    net_range_m: float
    geometry: dict
    labels: dict | None


def _label_speed_mps(points: list[dict]) -> float | None:
    if len(points) < 3:
        return None
    pts = sorted(points, key=lambda p: p["frame"])[:8]
    frames = np.array([p["frame"] for p in pts], float)
    bins = np.array([p["range_bin"] for p in pts], float)
    slope = np.polyfit(frames, bins, 1)[0]
    return abs(float(slope)) * BIN_M / 0.003


def is_full_wide_iq16(raw: bytes) -> bool:
    try:
        meta, _ = parse_dump(raw)
    except (ValueError, IndexError):
        return False
    return (
        meta["sample_fmt"] == SAMPLE_RANGE_FFT_IQ16_VARIABLE_TIMED
        and meta["n_frames"] == 24
        and meta["chirps_per_frame"] == 36
        and meta["n_tx"] == 3
        and meta["n_rx"] == 4
        and meta["frame_period_us"] == 3000
        and tuple(meta["range_bin_starts"]) == WIDE_STARTS
        and set(meta["range_bin_counts"]) == {53}
        and meta.get("capture_evidence") is None
    )


def recording_shots(root: Path, rejects: list) -> list[Shot]:
    shots = []
    for sub, dataset in (("", "august"), ("golfer_2026-09", "september")):
        folder = root / sub if sub else root
        manifest = json.loads((folder / "manifest.json").read_text())
        default = manifest.get("default", {})
        for path in sorted(folder.glob("*.l3dump")):
            raw = path.read_bytes()
            if not is_full_wide_iq16(raw):
                rejects.append({"path": path.name, "reason": "not a full wide IQ16 dump"})
                continue
            label_path = Path(str(path) + ".labels.json")
            labels = json.loads(label_path.read_text()) if label_path.exists() else None
            ball = _label_speed_mps(labels["ball"]["points"]) if labels else None
            if ball is None:
                rejects.append({"path": path.name, "reason": "no labelled ball track"})
                continue
            club = _label_speed_mps(labels["club"]["points"]) if labels else None
            entry = manifest.get(path.name, {})
            dest = entry.get("dest_bin", default.get("dest_bin"))
            if dataset == "september":
                tee = 1.575  # manifest default note: tee slant range 1.575 m
            else:
                tee = dest * BIN_M if dest else None
            shots.append(
                Shot(
                    path=str(path),
                    dataset=dataset,
                    ball_speed_mph=ball * MPS_TO_MPH,
                    club_speed_mph=club * MPS_TO_MPH if club else None,
                    club="7-iron",
                    tee_fallback_m=tee,
                    input_source="labels_radial_speed",
                    net_range_m=4.064,
                    geometry={
                        "tilt_deg": 10.0,
                        "radar_height_m": 0.1524,
                        "ball_height_m": 0.021335,
                    },
                    labels=labels,
                )
            )
    return shots


def outdoor_shots(folder: Path, rejects: list) -> list[Shot]:
    session = next(folder.glob("session_*.jsonl"))
    logged = {}
    shots_by_number = {}
    for line in session.read_text().splitlines():
        rec = json.loads(line)
        if rec.get("type") == "iwr6843_capture":
            logged[os.path.basename(rec["capture_path"])] = rec
        elif rec.get("type") == "shot_detected":
            shots_by_number[rec["shot_number"]] = rec
    shots = []
    for path in sorted((folder / "iwr6843").glob("*.l3dump")):
        raw = path.read_bytes()
        if not is_full_wide_iq16(raw):
            rejects.append({"path": path.name, "reason": "not a full wide IQ16 dump"})
            continue
        rec = logged.get(path.name)
        if rec is None:
            rejects.append({"path": path.name, "reason": "no OPS shot logged for this dump"})
            continue
        shot = shots_by_number.get(rec["shot_number"], {})
        eff = rec["runtime_config"]["calibration"]["effective"]
        shots.append(
            Shot(
                path=str(path),
                dataset="outdoors-test-7",
                ball_speed_mph=float(rec["ball_speed_mph"]),
                club_speed_mph=shot.get("club_speed_mph"),
                club=shot.get("club"),
                tee_fallback_m=None,
                input_source="session_log",
                net_range_m=float(rec["runtime_config"]["net_range_m"]),
                geometry={
                    "tilt_deg": eff["tilt_deg"],
                    "radar_height_m": eff["radar_height_m"],
                    "ball_height_m": eff["ball_height_m"],
                    "board_rotation_deg": eff["iwr_board_rotation_deg"],
                },
                labels=None,
            )
        )
    return shots


# --------------------------------------------------------------------------- estimator


@dataclass
class _Capture:
    raw: bytes
    valid: bool = True
    sequence: int = 0
    partial_raw: bytes | None = None


class _Monitor:
    def __init__(self, raw: bytes):
        self.capture = _Capture(raw)

    def capture_for_shot(self, _impact, timeout_s=0.0):  # noqa: ARG002
        return self.capture

    def stop(self):
        return None


def _calibration(cal_path: str, shot: Shot, tee: float | None) -> Calibration:
    g = shot.geometry
    cal = clone_calibration(
        Calibration.load(cal_path),
        tilt_deg=g.get("tilt_deg"),
        radar_height_m=g.get("radar_height_m"),
        ball_height_m=g.get("ball_height_m"),
        board_rotation_deg=g.get("board_rotation_deg"),
    )
    if g.get("ball_height_m") is not None:
        cal.tee_ball_height_m = g["ball_height_m"]
    cal.tee_range_m = tee
    return cal


def run_kiosk(raw: bytes, cal_path: str, shot: Shot, tee: float | None) -> dict:
    runtime = IWR6843Runtime(
        capture_monitor=_Monitor(raw),
        calibration=_calibration(cal_path, shot, tee),
        net_range_m=shot.net_range_m,
        tx_order="normal",
        azimuth_offset_deg=0.0,
        horizontal_phase_reference_rad=None,
        tdm_sign_policy="positive",
        club_window_policy=ClubWindowPolicy(),
    )
    res = runtime.process_shot(
        impact_timestamp=None,
        ball_speed_mph=shot.ball_speed_mph,
        club=shot.club,
        club_speed_mph=shot.club_speed_mph,
    )
    m, c, lr = res.measurement, res.club_path, res.launch_range
    return {
        "withheld": res.withheld_reason,
        "launch_status": getattr(m, "status", None),
        "launch_accepted": bool(m.accepted) if m is not None else False,
        "launch_deg": getattr(m, "angle_deg", None),
        "horizontal_deg": getattr(m, "horizontal_deg", None),
        "horizontal_status": getattr(m, "horizontal_status", None),
        "impact_t_s": getattr(m, "impact_t_s", None),
        "range_status": getattr(lr, "status", None),
        "range_m": getattr(lr, "range_m", None) if lr is not None and lr.measured else None,
        "range_reason": getattr(lr, "reason", None),
        "path_status": getattr(c, "status", None),
        "path_accepted": bool(c.accepted) if c is not None else False,
        "path_deg": getattr(c, "path_deg", None),
        "attack_status": getattr(c, "attack_angle_status", None),
        "attack_deg": getattr(c, "candidate_attack_angle_deg", None),
    }


# --------------------------------------------------------------------------- SNR


def ball_bin_snr(raw16: bytes, raw8: bytes, labels: dict | None) -> dict | None:
    if not labels:
        return None
    meta, c16 = parse_dump(raw16)
    _m, c8 = parse_dump(raw8)
    sig = err = sig_mti = err_mti = 0.0
    n = 0
    for point in labels["ball"]["points"]:
        frame = int(point["frame"])
        if frame >= meta["n_frames"]:
            continue
        local = int(round(point["range_bin"])) - meta["range_bin_starts"][frame]
        lo, hi = max(0, local - 1), min(meta["range_bin_counts"][frame], local + 2)
        if lo >= hi:
            continue
        a = c16[frame, :, :, lo:hi]
        b = c8[frame, :, :, lo:hi]
        sig += float(np.sum(np.abs(a) ** 2))
        err += float(np.sum(np.abs(b - a) ** 2))
        # burst MTI: remove each TX/RX/bin's mean over the frame's loops
        a3 = a.reshape(-1, 3, *a.shape[1:])
        b3 = b.reshape(-1, 3, *b.shape[1:])
        am = a3 - a3.mean(axis=0, keepdims=True)
        bm = b3 - b3.mean(axis=0, keepdims=True)
        sig_mti += float(np.sum(np.abs(am) ** 2))
        err_mti += float(np.sum(np.abs(bm - am) ** 2))
        n += 1
    if n == 0 or err == 0:
        return None
    return {
        "points": n,
        "snr_db": 10 * math.log10(sig / err),
        "snr_mti_db": 10 * math.log10(sig_mti / err_mti) if err_mti else None,
    }


# --------------------------------------------------------------------------- summary

METRICS = ("launch_deg", "horizontal_deg", "range_m", "path_deg", "attack_deg")
LARGE = {
    "launch_deg": 0.5,
    "horizontal_deg": 0.5,
    "range_m": 0.02,
    "path_deg": 1.0,
    "attack_deg": 1.0,
}
STATUS = {
    "launch_accepted": "launch_accepted",
    "horizontal_status": "horizontal_status",
    "range_status": "range_status",
    "path_accepted": "path_accepted",
    "attack_status": "attack_status",
}


def _pct(values: list[float], q: float) -> float:
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def summarize(rows: list[dict], variants: list[str]) -> dict:
    out = {}
    for variant in variants:
        pairs = [(r["original"], r[variant], r) for r in rows if variant in r]
        v = {"n": len(pairs), "status": {}, "metrics": {}, "large": []}
        for key in STATUS:
            same = sum(1 for o, x, _ in pairs if o[key] == x[key])
            entry = {"agree": same, "disagree": len(pairs) - same}
            if key.endswith("accepted"):
                entry["lost"] = sum(1 for o, x, _ in pairs if o[key] and not x[key])
                entry["gained"] = sum(1 for o, x, _ in pairs if x[key] and not o[key])
            v["status"][key] = entry
        for metric in METRICS:
            diffs = []
            for o, x, r in pairs:
                if o[metric] is None or x[metric] is None:
                    continue
                d = abs(float(x[metric]) - float(o[metric]))
                diffs.append(d)
                if d > LARGE[metric]:
                    v["large"].append(
                        {
                            "dump": os.path.basename(r["path"]),
                            "metric": metric,
                            "original": o[metric],
                            "variant": x[metric],
                            "abs_diff": d,
                            "orig_status": o.get(
                                metric.replace("_deg", "_status").replace("_m", "_status")
                            ),
                            "variant_status": x.get(
                                metric.replace("_deg", "_status").replace("_m", "_status")
                            ),
                        }
                    )
            one_sided = sum(1 for o, x, _ in pairs if (o[metric] is None) != (x[metric] is None))
            v["metrics"][metric] = (
                {
                    "n": len(diffs),
                    "median": statistics.median(diffs),
                    "p90": _pct(diffs, 0.9),
                    "max": max(diffs),
                    "only_one_side": one_sided,
                }
                if diffs
                else {"n": 0, "only_one_side": one_sided}
            )
        out[variant] = v
    return out


def write_tables(out: Path, summaries: list[dict]) -> None:
    """One markdown table per study folder: status agreement, then |diff| per metric."""
    lines = [
        "| variant | metric | n | median | p90 | max | over threshold | one side only |",
        "|---|---|---|---|---|---|---|---|",
    ]
    status = [
        "| variant | launch accepts lost/gained | horizontal status disagree | range status "
        "disagree | attack status disagree |",
        "|---|---|---|---|---|",
    ]
    for summary in summaries:
        for variant, v in summary.items():
            if variant.startswith("_"):
                continue
            st = v["status"]
            status.append(
                f"| {variant} | {st['launch_accepted']['lost']}/{st['launch_accepted']['gained']} "
                f"| {st['horizontal_status']['disagree']} | {st['range_status']['disagree']} "
                f"| {st['attack_status']['disagree']} |"
            )
            for metric, e in v["metrics"].items():
                over = sum(1 for big in v["large"] if big["metric"] == metric)
                unit = 100.0 if metric == "range_m" else 1.0
                if e["n"]:
                    lines.append(
                        f"| {variant} | {metric.replace('_m', '_cm') if unit > 1 else metric} | {e['n']} "
                        f"| {e['median'] * unit:.3f} | {e['p90'] * unit:.3f} | {e['max'] * unit:.2f} "
                        f"| {over} | {e['only_one_side']} |"
                    )
                else:
                    lines.append(
                        f"| {variant} | {metric} | 0 | - | - | - | - | {e['only_one_side']} |"
                    )
    text = "\n".join(status + [""] + lines) + "\n"
    (out / "tables.md").write_text(text, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--recordings", required=True, type=Path)
    parser.add_argument("--outdoor", required=True, type=Path)
    parser.add_argument("--cal", default="config/iwr6843_calibration_reference.json")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--iq8-scales", default="128")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument(
        "--tables-from",
        nargs="*",
        type=Path,
        default=None,
        help="only rebuild tables.md in --out from these study folders",
    )
    parser.add_argument("--only-iq8", action="store_true", help="skip the B and control variants")
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    if args.tables_from:
        write_tables(
            args.out, [json.loads((f / "summary.json").read_text()) for f in args.tables_from]
        )
        return 0

    rejects: list = []
    shots = recording_shots(args.recordings, rejects) + outdoor_shots(args.outdoor, rejects)
    if args.limit:
        shots = shots[: args.limit]
    scales = [int(s) for s in args.iq8_scales.split(",")]
    variants = [f"A{s}_{r}" for s in scales for r in ("floor", "round_half_up")] + (
        [] if args.only_iq8 else ["B7", "B5", "C_dither"]
    )

    rows, snr_rows = [], []
    started = time.time()
    for index, shot in enumerate(shots):
        raw = Path(shot.path).read_bytes()
        meta, _ = parse_dump(raw)
        repacked = pack_dump(parse_dump(raw)[1], n_tx=meta["n_tx"], **_repack(meta))
        if repacked != raw:
            raise AssertionError(f"{shot.path}: the reference packer does not round-trip")
        builds = {}
        enc_stats = {}
        for s in scales:
            for r in ("floor", "round_half_up"):
                builds[f"A{s}_{r}"], enc_stats[f"A{s}_{r}"] = encode_iq8(raw, s, r)
        if not args.only_iq8:
            builds["B7"] = trim_pre_frames(raw, 2)
            builds["B5"] = trim_pre_frames(raw, 4)
            builds["C_dither"] = dither_iq16(raw, seed=index)
        row = {
            "path": shot.path,
            "dataset": shot.dataset,
            "input_source": shot.input_source,
            "ball_speed_mph": shot.ball_speed_mph,
            "club_speed_mph": shot.club_speed_mph,
            "bytes": {"original": len(raw), **{k: len(v) for k, v in builds.items()}},
            "iq8_encode": enc_stats,
        }
        row["original"] = run_kiosk(raw, args.cal, shot, None)
        tee_mode = "kiosk_launch_range"
        if row["original"]["withheld"] and shot.tee_fallback_m is not None:
            tee_mode = "configured_tee_from_manifest"
            row["original"] = run_kiosk(raw, args.cal, shot, shot.tee_fallback_m)
        row["tee_mode"] = tee_mode
        tee = shot.tee_fallback_m if tee_mode != "kiosk_launch_range" else None
        for name, data in builds.items():
            row[name] = run_kiosk(data, args.cal, shot, tee)
        rows.append(row)
        for s in scales:
            for r in ("floor", "round_half_up"):
                snr = ball_bin_snr(raw, builds[f"A{s}_{r}"], shot.labels)
                if snr:
                    snr_rows.append(
                        {
                            "dump": os.path.basename(shot.path),
                            "variant": f"A{s}_{r}",
                            **snr,
                            **enc_stats[f"A{s}_{r}"],
                        }
                    )
        print(
            f"[{index + 1}/{len(shots)}] {os.path.basename(shot.path)} {tee_mode} "
            f"{time.time() - started:.0f}s",
            flush=True,
        )

    with (args.out / "rows.jsonl").open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, default=float) + "\n")
    summary = summarize(rows, variants)
    summary["_inputs"] = {
        "dumps": len(rows),
        "rejected": rejects,
        "tee_modes": {
            m: sum(1 for r in rows if r["tee_mode"] == m) for m in {r["tee_mode"] for r in rows}
        },
    }
    (args.out / "summary.json").write_text(json.dumps(summary, indent=1, default=float))
    (args.out / "snr.json").write_text(json.dumps(snr_rows, indent=1, default=float))
    return 0


if __name__ == "__main__":
    sys.exit(main())
