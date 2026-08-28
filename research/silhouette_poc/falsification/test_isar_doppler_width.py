"""Is the clubhead's rotation visible in the radar's Doppler spread?

The IWR's real-aperture cross-range cell is 277 mm at 1.25 m, against a 90 mm
clubhead -- a third of one cell. It cannot image the club's shape directly. But
a ROTATING target spreads its own Doppler: the toe and heel have different
radial velocities, and that spread is what ISAR turns into cross-range
resolution. If the spread is measurable, two things follow:

  * the rotation rate and axis can be estimated from the radar -- and those are
    exactly the two parameters the mesh fit currently leaves free and watches
    absorb noise;
  * the azimuth phase span that rejects club path on 21 of 21 shots (2.18-3.91
    rad against a pi/2 ceiling) may be this same rotation signature being
    thrown away as noise.

Predicted spread, from omega = v / r: the toe moves 1.01 m/s relative to the
head centre, which at 62 GHz is 419 Hz, so 838 Hz toe-to-heel. Native Doppler
resolution is 617 Hz over the 12-loop, 135 us coherent window, so the rotation
is worth about 1.36 native bins.

The control is the same measurement on an ideal point target pushed through the
same estimator and the same 12-sample window. A real target cannot be NARROWER
than that, so frames that come out below it are flagged: they mark where the
estimator is being driven by noise rather than by the target, and they are
counted rather than quietly dropped.

Range walk is corrected before the Doppler transform. The club covers 57 mm --
1.2 range bins -- during a single coherent window, so holding the bin fixed
would broaden the measurement by exactly the effect under test.
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "research"))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from openflight.iwr6843 import dump, tracking  # noqa: E402
from openflight.iwr6843.club import find_club, pre_impact_window_s  # noqa: E402
from openflight.iwr6843.shot import (  # noqa: E402
    TX2_LOOP_PERIOD_S,
    geometry_from_header,
    is_range_snapshot,
    project_tx_pair,
)

from session_path import find_session  # noqa: E402

LAMBDA_M = 0.004835362225806452
TEE_RANGE_M = 1.575
NFFT = 256
EXCLUDE = {1}


def doppler_spectrum(block, bins_per_loop):
    """Power spectrum over slow time, with range walk removed first.

    `block` is (tx, loop, rx, range) for one frame. Each loop is sampled at the
    fractional range bin the club actually occupies at that instant, so the
    transform sees a stationary scatterer plus its rotation, not a scatterer
    sliding across bins.
    """
    n_tx, n_loops, n_rx, n_bins = block.shape
    grid = np.arange(n_bins)
    total = np.zeros(NFFT)
    for tx in range(n_tx):
        for rx in range(n_rx):
            samples = np.empty(n_loops, dtype=complex)
            for loop in range(n_loops):
                trace = block[tx, loop, rx, :]
                position = bins_per_loop[loop]
                samples[loop] = np.interp(position, grid, trace.real) + 1j * np.interp(
                    position, grid, trace.imag
                )
            total += np.abs(np.fft.fftshift(np.fft.fft(samples, NFFT))) ** 2
    return total


def width_bins(power, n_loops, drop_db=6.0):
    """-N dB width, expressed in NATIVE Doppler bins rather than FFT bins."""
    peak = float(power.max())
    if peak <= 0.0:
        return float("nan")
    normalised = power / peak
    centre = int(power.argmax())
    threshold = 10.0 ** (-drop_db / 10.0)
    left = centre
    while left > 0 and normalised[left] > threshold:
        left -= 1
    right = centre
    while right < len(normalised) - 1 and normalised[right] > threshold:
        right += 1
    return (right - left) * n_loops / NFFT


def peak_to_floor_db(power):
    """Crude SNR: peak over the median of the spectrum."""
    floor = float(np.median(power))
    return 10.0 * np.log10(float(power.max()) / floor) if floor > 0 else float("inf")


def point_target_width(n_loops):
    """The same estimator on an ideal point target in the same window."""
    loops = np.arange(n_loops)
    tone = np.exp(2j * np.pi * 0.31 * loops)
    return width_bins(np.abs(np.fft.fftshift(np.fft.fft(tone, NFFT))) ** 2, n_loops)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--shot", type=int, action="append")
    parser.add_argument("--min-snr-db", type=float, default=6.0)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).with_name("isar_doppler_width.json"),
    )
    args = parser.parse_args()

    session = find_session()
    with open(session / "shots.csv", newline="", encoding="utf-8") as handle:
        rows = {
            int(r["shot_number"]): r
            for r in csv.DictReader(handle)
            if int(r["shot_number"]) not in EXCLUDE
        }
    wanted = sorted(set(rows) & set(args.shot)) if args.shot else sorted(rows)

    records, skipped = [], []
    control = None
    for shot in wanted:
        row = rows[shot]
        matches = sorted(
            glob.glob(str(session / "shots" / f"shot_{shot:03d}*" / "*.l3dump"))
        )
        impact = row.get("iwr_measurement_impact_t_s")
        if not matches or not impact:
            skipped.append((shot, "no dump or no impact time"))
            continue
        raw = Path(matches[0]).read_bytes()
        meta, cube = dump.parse_dump(project_tx_pair(raw, (0, 2)))
        geo = geometry_from_header(meta, loop_period_s=TX2_LOOP_PERIOD_S)
        mti = tracking.mti_filter(
            cube, range_domain=is_range_snapshot(meta), geometry=geo
        )
        impact_t_s = float(impact)
        window = pre_impact_window_s(geo, impact_t_s)
        if window is None:
            skipped.append((shot, "no pre-impact window"))
            continue
        selection = find_club(
            mti,
            geo,
            tee_range_m=TEE_RANGE_M,
            window_s=window,
            ops_club_speed_mph=float(row["club_speed_mph"]),
            impact_t_s=impact_t_s,
        )
        if selection is None:
            skipped.append((shot, "no club track"))
            continue
        track = selection.track
        starts = meta["range_bin_starts"]
        n_loops = mti.shape[2]
        loop_period = geo.loop_period_s
        resolution_hz = 1.0 / (n_loops * loop_period)
        if control is None:
            control = point_target_width(n_loops)

        for frame in range(mti.shape[0]):
            frame_t = frame * geo.frame_period_s
            if not window[0] <= frame_t <= window[1]:
                continue
            bins = [
                track.slope_bins * (frame_t + loop * loop_period)
                + track.intercept_bins
                - starts[frame]
                for loop in range(n_loops)
            ]
            if min(bins) < 0 or max(bins) > mti.shape[-1] - 1:
                continue
            power = doppler_spectrum(mti[frame], bins)
            records.append(
                {
                    "shot": shot,
                    "club": row["club"],
                    "frame": frame,
                    "range_m": float(
                        (track.slope_bins * frame_t + track.intercept_bins)
                        * geo.range_res_m
                    ),
                    "width_bins": float(width_bins(power, n_loops)),
                    "snr_db": float(peak_to_floor_db(power)),
                    "resolution_hz": float(resolution_hz),
                }
            )
        print(
            f"shot {shot:>3}: {sum(1 for r in records if r['shot'] == shot)} frames",
            flush=True,
        )

    if not records:
        raise SystemExit("no frames measured")
    good = [r for r in records if r["snr_db"] >= args.min_snr_db]
    widths = np.array([r["width_bins"] for r in good])
    resolution_hz = good[0]["resolution_hz"]
    below = int((widths < control).sum())
    predicted = 838.0 / resolution_hz

    summary = {
        "n_frames_total": len(records),
        "n_frames_above_snr": len(good),
        "min_snr_db": args.min_snr_db,
        "point_target_control_bins": control,
        "predicted_rotation_bins": predicted,
        "quadrature_expectation_bins": float(np.hypot(control, predicted)),
        "median_width_bins": float(np.median(widths)),
        "mean_width_bins": float(widths.mean()),
        "sd_width_bins": float(widths.std()),
        "frames_below_point_target_floor": below,
        "resolution_hz": resolution_hz,
        "records": records,
    }
    args.output.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(
        f"\n=== n={len(good)} frames above {args.min_snr_db:.0f} dB "
        f"(of {len(records)} measured), {len(wanted) - len(skipped)} shots ==="
    )
    print(
        f"  point-target control        {control:.2f} bins  ({control * resolution_hz:.0f} Hz)"
    )
    print(f"  predicted rotation alone    {predicted:.2f} bins  (838 Hz)")
    print(f"  expected in quadrature      {np.hypot(control, predicted):.2f} bins")
    print(
        f"  MEASURED club median        {np.median(widths):.2f} bins  "
        f"({np.median(widths) * resolution_hz:.0f} Hz)"
    )
    print(f"  mean {widths.mean():.2f} +- {widths.std():.2f} bins")
    print(
        f"\n  frames narrower than a point target: {below}/{len(good)} "
        f"({100.0 * below / len(good):.0f} %) -- unphysical, marks noise-driven estimates"
    )
    if skipped:
        print(f"  skipped shots: {skipped}")


if __name__ == "__main__":
    main()
