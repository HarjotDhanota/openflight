"""Bench spin-jig analysis: read rotation rate from OPS243 I/Q dumps.

For each dump captured by bench_spin_capture.py:
  1. Remove DC/clutter (the stationary ball body and room).
  2. Complex STFT -> Doppler-velocity spectrogram. A working sticker shows a
     periodic signature (a sinusoidal velocity trace, or periodic energy
     flashes if the foil is specular).
  3. Two independent rotation-rate estimates, each compared to the commanded rpm:
       - envelope method: FFT of the non-clutter spectral energy vs time
         (periodic at the rotation rate or its 2nd harmonic);
       - trace method: FFT of the per-frame peak signed velocity.
  4. A per-dump PNG (spectrogram + energy trace + rotation spectrum) and a
     summary table. Eyeball the PNGs on the first session — if no periodic
     structure is visible, no estimator will save it, and the answer is
     "sticker not detectable at this size/range/material".

Usage:
    uv run python scripts/analysis/bench_spin_analysis.py bench_spin/foil10mm
    uv run python scripts/analysis/bench_spin_analysis.py bench_spin/foil10mm --notch 0.6
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

LAMBDA_M = 0.012427  # 24.125 GHz
BALL_R_M = 0.021335


def stft(z: np.ndarray, win: int, hop: int) -> np.ndarray:
    w = np.hanning(win)
    n = 1 + (len(z) - win) // hop
    frames = np.stack([z[k * hop : k * hop + win] * w for k in range(n)])
    return np.fft.fftshift(np.fft.fft(frames, axis=1), axes=1)


def rotation_line(signal: np.ndarray, frame_rate: float, lo=3.0, hi=400.0):
    """Strongest periodicity of a time series in [lo, hi] Hz -> (hz, snr)."""
    x = signal - signal.mean()
    if not np.any(x):
        return None, 0.0
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    freqs = np.fft.rfftfreq(len(x), 1.0 / frame_rate)
    band = (freqs >= lo) & (freqs <= hi)
    if not np.any(band):
        return None, 0.0
    k = np.argmax(spec[band])
    noise = np.median(spec[band]) + 1e-12
    return float(freqs[band][k]), float(spec[band][k] / noise)


def analyze(path: Path, notch_ms: float, outdir: Path) -> dict:
    d = np.load(path)
    fs = float(d["ksps"]) * 1000.0
    rpm_true = float(d["rpm"])
    z = (d["i"].astype(float) - d["i"].mean()) + 1j * (d["q"].astype(float) - d["q"].mean())
    # clutter: remove a moving-average (slow/static returns) before the STFT
    k = max(3, int(fs * 0.02) | 1)
    kern = np.ones(k) / k
    z = z - (np.convolve(z.real, kern, "same") + 1j * np.convolve(z.imag, kern, "same"))

    win, hop = 128, 16
    S = stft(z, win, hop)
    frame_rate = fs / hop
    vel = np.fft.fftshift(np.fft.fftfreq(win, 1.0 / fs)) * LAMBDA_M / 2.0
    mag = np.abs(S)

    keep = np.abs(vel) > notch_ms  # exclude the clutter notch
    energy = (mag[:, keep] ** 2).sum(axis=1)
    peak_v = np.array(
        [vel[keep][np.argmax(row[keep])] if row[keep].max() > 0 else 0.0 for row in mag]
    )

    f_env, snr_env = rotation_line(energy, frame_rate)
    f_trc, snr_trc = rotation_line(peak_v, frame_rate)

    t = np.arange(len(energy)) * hop / fs * 1000.0
    fig, ax = plt.subplots(3, 1, figsize=(11, 9), constrained_layout=True)
    db = 20 * np.log10(mag.T + 1e-6)
    ax[0].imshow(
        db,
        aspect="auto",
        origin="lower",
        cmap="magma",
        extent=[t[0], t[-1], vel[0], vel[-1]],
        vmin=db.max() - 45,
        vmax=db.max(),
    )
    v_tan = 2 * np.pi * rpm_true / 60.0 * BALL_R_M
    for s in (+1, -1):
        ax[0].axhline(s * v_tan, color="cyan", ls="--", lw=0.8)
    ax[0].set(
        ylabel="LOS velocity (m/s)",
        title=f"{path.name}: spectrogram "
        f"(cyan = expected +/-{v_tan:.2f} m/s at {rpm_true:.0f} rpm)",
    )
    ax[1].plot(t, energy, lw=0.8)
    ax[1].set(ylabel="non-clutter energy", xlabel="time (ms)")
    x = energy - energy.mean()
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    freqs = np.fft.rfftfreq(len(x), 1.0 / frame_rate)
    ax[2].plot(freqs, spec, lw=0.9)
    ax[2].axvline(rpm_true / 60.0, color="g", ls="--", label=f"commanded {rpm_true / 60:.1f} Hz")
    ax[2].axvline(rpm_true / 30.0, color="g", ls=":", label="2nd harmonic")
    if f_env:
        ax[2].axvline(
            f_env, color="r", ls="-", lw=0.8, label=f"detected {f_env:.1f} Hz (snr {snr_env:.0f})"
        )
    ax[2].set(xlim=(0, min(400, frame_rate / 2)), xlabel="envelope frequency (Hz)", ylabel="|FFT|")
    ax[2].legend(fontsize=8)
    png = outdir / f"{path.stem}.png"
    fig.savefig(png, dpi=110)
    plt.close(fig)

    return {
        "dump": path.name,
        "rpm_true": rpm_true,
        "rpm_env": None if f_env is None else f_env * 60.0,
        "snr_env": snr_env,
        "rpm_env_half": None if f_env is None else f_env * 30.0,  # if the line is the 2nd harmonic
        "rpm_trace": None if f_trc is None else f_trc * 60.0,
        "snr_trc": snr_trc,
        "png": str(png),
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("dir", help="capture directory (from bench_spin_capture.py)")
    ap.add_argument(
        "--notch", type=float, default=0.4, help="clutter notch half-width in m/s (default 0.4)"
    )
    args = ap.parse_args()

    indir = Path(args.dir)
    dumps = sorted(indir.glob("dump_*.npz"))
    if not dumps:
        print(f"no dump_*.npz in {indir}")
        return 1
    outdir = indir / "analysis"
    outdir.mkdir(exist_ok=True)

    rows = [analyze(p, args.notch, outdir) for p in dumps]
    print(
        f"\n{'dump':<14}{'true rpm':>9}{'env rpm':>10}{'(snr)':>7}{'env/2':>9}{'trace rpm':>11}{'(snr)':>7}"
    )
    for r in rows:
        env = "-" if r["rpm_env"] is None else f"{r['rpm_env']:.0f}"
        env2 = "-" if r["rpm_env_half"] is None else f"{r['rpm_env_half']:.0f}"
        trc = "-" if r["rpm_trace"] is None else f"{r['rpm_trace']:.0f}"
        print(
            f"{r['dump']:<14}{r['rpm_true']:>9.0f}{env:>10}{r['snr_env']:>7.0f}{env2:>9}{trc:>11}{r['snr_trc']:>7.0f}"
        )
    print(
        f"\nplots -> {outdir}  (read the spectrograms first; the table is only as good as the signature)"
    )
    print(
        "interpretation: env rpm OR env/2 matching true = sticker detected; snr < ~5 = not detected"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
