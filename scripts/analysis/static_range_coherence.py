"""Compare power differencing with phase-aligned complex differencing on static range pairs.

Offline research tool. It reads guided setup epochs (their saved raw IWR dumps),
reruns the production v2 selector, and reports how a coherent difference would
rank the same range bins. The radar re-initialises between the empty and
ball-present captures, which rotates each channel's phase; the coherent path
fits one complex factor per virtual channel on the strongest static bins before
subtracting. Nothing here changes a production decision.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np

from openflight.iwr6843.dump import is_range_snapshot, parse_dump
from openflight.iwr6843.range_evidence import (
    compare_static_range_profiles,
    static_range_profile_v2,
)

DEFAULT_CALIBRATION = Path("config/iwr6843_calibration_reference.json")
REFERENCE_BINS = 8


def channel_means(raw: bytes) -> tuple[np.ndarray, int, float]:
    """Per-TX, per-RX mean complex range profile over frames and TDM loops."""
    metadata, cube = parse_dump(raw)
    starts = metadata.get("range_bin_starts")
    start = int(starts[0] if starts else metadata.get("range_bin_start", 0))
    range_domain = is_range_snapshot(metadata)
    cube = cube if range_domain else np.fft.fft(cube, axis=-1)
    fft_size = 128 if range_domain else metadata["n_samples"]
    n_tx = int(metadata["n_tx"])
    frames, chirps, rx, bins = cube.shape
    loops = chirps // n_tx
    means = cube.mean(axis=0)[: loops * n_tx].reshape(loops, n_tx, rx, bins).mean(axis=0)
    return means, start, 6.0 / fft_size


def coherent_residual(empty: np.ndarray, present: np.ndarray) -> dict[str, Any]:
    """Residual power per bin after fitting one complex factor per virtual channel."""
    static = np.mean(np.abs(empty) ** 2, axis=(0, 1))
    reference = np.argsort(static)[-REFERENCE_BINS:]
    factors = np.empty(empty.shape[:2], dtype=complex)
    for tx in range(empty.shape[0]):
        for rx in range(empty.shape[1]):
            e = empty[tx, rx, reference]
            factors[tx, rx] = np.vdot(e, present[tx, rx, reference]) / np.vdot(e, e)
    residual = np.mean(np.abs(present - factors[..., None] * empty) ** 2, axis=(0, 1))
    return {
        "residual": residual,
        "static": static,
        "phase_deg": np.degrees(np.angle(factors)),
        "gain": np.abs(factors),
    }


def _latest(directory: Path, prefix: str) -> Path:
    usable = []
    for record_path in sorted(directory.glob(f"{prefix}-*.json")):
        record = json.loads(record_path.read_text(encoding="utf-8"))
        raw = directory / str((record.get("artifacts") or {}).get("raw", {}).get("path", ""))
        if record.get("usable") and raw.is_file():
            usable.append((record_path, raw))
    if not usable:
        raise ValueError(f"{directory} has no usable {prefix} capture with raw bytes")
    return usable[-1][0]


def _contrast(values: np.ndarray, index: int, search: np.ndarray) -> float:
    others = [i for i in np.flatnonzero(search) if abs(i - index) > 1]
    return float(values[index] / max(values[others])) if others else float("inf")


def analyse_epoch(epoch: Path, *, bias_m: float, tape_m: float | None) -> dict[str, Any]:
    """Selector v2 decision and coherent ranking for one saved setup epoch."""
    iwr = epoch / "iwr"
    records = {kind: _latest(iwr, kind) for kind in ("empty", "ball_present")}
    loaded = {kind: json.loads(path.read_text(encoding="utf-8")) for kind, path in records.items()}
    raws = {kind: (iwr / loaded[kind]["artifacts"]["raw"]["path"]).read_bytes() for kind in loaded}
    profiles = {
        kind: static_range_profile_v2(
            raws[kind],
            radar_profile_sha256=loaded[kind]["inputs"]["radar_config"]["sha256"],
            rig_geometry_sha256=loaded[kind]["inputs"]["rig_geometry"]["sha256"],
        )
        for kind in raws
    }
    selector = compare_static_range_profiles(
        profiles["empty"],
        profiles["ball_present"],
        plausible_apparent_range_m=(0.5 + bias_m, 4.0 + bias_m),
    )
    empty, start, resolution = channel_means(raws["empty"])
    present, _start, _resolution = channel_means(raws["ball_present"])
    coherent = coherent_residual(empty, present)
    corrected = (np.arange(empty.shape[-1]) + start) * resolution - bias_m
    search = (corrected > 0.5) & (corrected < 4.0)
    residual = coherent["residual"]
    top = int(np.flatnonzero(search)[np.argmax(residual[search])])
    result: dict[str, Any] = {
        "epoch": epoch.name,
        "selector_status": selector.status,
        "selector_range_m": (
            selector.apparent_range_m - bias_m if selector.apparent_range_m is not None else None
        ),
        "coherent_top_bin": top + start,
        "coherent_top_range_m": float(corrected[top]),
        "coherent_top_contrast": _contrast(residual, top, search),
        "phase_deg": np.round(coherent["phase_deg"], 2).tolist(),
        "gain": np.round(coherent["gain"], 4).tolist(),
        "tape_m": tape_m,
    }
    if tape_m is not None:
        ball = int(np.argmin(np.abs(corrected - tape_m)))
        ranked = [int(i) for i in np.flatnonzero(search)[np.argsort(-residual[search])]]
        result.update(
            {
                "tape_bin": ball + start,
                "tape_bin_coherent_rank": ranked.index(ball) + 1 if ball in ranked else None,
                "tape_bin_coherent_contrast": _contrast(residual, ball, search),
            }
        )
    return result


def _tape_labels(values: list[str]) -> dict[str, float]:
    labels = {}
    for value in values:
        epoch, _, metres = value.partition("=")
        if not metres:
            raise ValueError(f"--tape expects EPOCH=METRES, got {value!r}")
        labels[epoch] = float(metres)
    return labels


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("epochs", nargs="+", type=Path, help="guided setup epoch directories")
    parser.add_argument("--tape", action="append", default=[], help="EPOCH_ID=METRES truth")
    parser.add_argument("--calibration", type=Path, default=DEFAULT_CALIBRATION)
    parser.add_argument("--json", action="store_true", help="print one JSON object per epoch")
    args = parser.parse_args(argv)
    calibration = json.loads(args.calibration.read_text(encoding="utf-8"))
    bias_m = float(calibration.get("range_bias_const_m", calibration.get("range_offset_m", 0.0)))
    tape = _tape_labels(args.tape)
    for epoch in args.epochs:
        result = analyse_epoch(epoch, bias_m=bias_m, tape_m=tape.get(epoch.name))
        if args.json:
            print(json.dumps(result))
            continue
        selector_range = result["selector_range_m"]
        line = (
            f"{result['epoch']}: selector {result['selector_status']}"
            + (f" at {selector_range:.3f} m" if selector_range is not None else "")
            + f" | coherent top {result['coherent_top_range_m']:.3f} m"
            f" ({result['coherent_top_contrast']:.1f}x next)"
        )
        if result["tape_m"] is not None:
            line += (
                f" | tape {result['tape_m']:.3f} m: coherent rank {result['tape_bin_coherent_rank']}"
                f", {result['tape_bin_coherent_contrast']:.2f}x next"
            )
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
