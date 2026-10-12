"""Bench spin-jig capture: OPS243 raw I/Q dumps of a spinning marked ball.

The jig: a golf ball with a foil sticker, spun at a known rpm on a shaft.
The ball body is stationary (zero Doppler); the sticker's line-of-sight
velocity sweeps sinusoidally at the rotation rate, peaking at

    v_tan = 2*pi * (rpm/60) * 0.021335 m   (6.7 m/s at 3000 rpm)

which at 24.125 GHz is a Doppler swing of +/-1.08 kHz.

This script puts the OPS243 into rolling-buffer mode at a REDUCED sample
rate (default 10 ksps -> 4096 samples = 410 ms = ~20 revolutions at
3000 rpm; still Nyquist-clean to ~11,000 rpm) and software-triggers (S!)
repeated dumps. No sound trigger needed. Each dump is saved as an .npz
with the commanded rpm recorded for truth.

Usage (from repo root, radar on USB):
    uv run python scripts/analysis/bench_spin_capture.py --rpm 3000 --tag foil10mm
    uv run python scripts/analysis/bench_spin_capture.py --rpm 3000 --dumps 10 --ksps 10

Then analyze:
    uv run python scripts/analysis/bench_spin_analysis.py bench_spin/foil10mm
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from openflight.ops243 import OPS243Radar  # noqa: E402

_ARRAY_RE = {
    "I": re.compile(r'"I"\s*:\s*\[([^\]]*)\]'),
    "Q": re.compile(r'"Q"\s*:\s*\[([^\]]*)\]'),
}
_TIME_RE = re.compile(r'"(sample_time|trigger_time)"\s*:\s*"?([-0-9.]+)"?')


def parse_dump(text: str) -> dict | None:
    """Pull I/Q arrays and timing lines out of a raw S! dump."""
    out: dict = {}
    for key, rx in _ARRAY_RE.items():
        m = rx.search(text)
        if not m:
            return None
        try:
            out[key] = np.array(
                [int(x) for x in m.group(1).split(",") if x.strip()], dtype=np.int32
            )
        except ValueError:
            return None
    if len(out["I"]) != len(out["Q"]) or len(out["I"]) < 1024:
        return None
    for m in _TIME_RE.finditer(text):
        out[m.group(1)] = float(m.group(2))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--rpm",
        type=float,
        required=True,
        help="commanded jig rpm (truth; 0 for a background capture)",
    )
    ap.add_argument("--tag", default=None, help="output subfolder name (default: rpm+timestamp)")
    ap.add_argument("--dumps", type=int, default=5, help="number of buffer dumps to capture")
    ap.add_argument(
        "--ksps",
        type=int,
        default=10,
        help="sample rate ksps (10 -> 410 ms window, good to ~11k rpm)",
    )
    ap.add_argument(
        "--power",
        type=int,
        default=3,
        help="OPS transmit power level 0(max)-7 (default 3, as production)",
    )
    ap.add_argument("--port", default=None, help="serial port (default: auto-detect USB)")
    ap.add_argument("--out", default="bench_spin", help="output root directory")
    args = ap.parse_args()

    tan = 2 * np.pi * (args.rpm / 60.0) * 0.021335
    print(
        f"[jig] commanded {args.rpm:.0f} rpm -> sticker tangential {tan:.2f} m/s "
        f"(Doppler +/-{2 * tan / 0.012427:.0f} Hz at 24.125 GHz)"
    )
    nyq = args.ksps * 1000 / 2 * 0.012427 / 2
    if tan > nyq * 0.9:
        print(
            f"[warn] tangential speed near Nyquist ({nyq:.1f} m/s at {args.ksps} ksps) — raise --ksps"
        )

    tag = args.tag or f"rpm{args.rpm:.0f}_{time.strftime('%H%M%S')}"
    outdir = Path(args.out) / tag
    outdir.mkdir(parents=True, exist_ok=True)

    radar = OPS243Radar(port=args.port)
    radar.connect()
    radar.set_transmit_power(args.power)
    radar.enter_rolling_buffer_mode(pre_trigger_segments=16, sample_rate_ksps=args.ksps)

    meta = {
        "rpm_commanded": args.rpm,
        "ksps": args.ksps,
        "power": args.power,
        "ball_radius_m": 0.021335,
        "carrier_ghz": 24.125,
        "captured": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    (outdir / "meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")

    kept = 0
    for n in range(args.dumps):
        # Let the buffer refill with fresh samples (4096/ksps seconds + margin).
        time.sleep(4096 / (args.ksps * 1000) + 0.3)
        text = radar.trigger_capture()
        dump = parse_dump(text)
        if dump is None:
            print(f"[dump {n}] unparseable ({len(text)} bytes) — skipped")
            continue
        np.savez(
            outdir / f"dump_{kept:03d}.npz",
            i=dump["I"],
            q=dump["Q"],
            ksps=args.ksps,
            rpm=args.rpm,
            sample_time=dump.get("sample_time", np.nan),
            trigger_time=dump.get("trigger_time", np.nan),
        )
        span = 1000 * len(dump["I"]) / (args.ksps * 1000)
        print(
            f"[dump {kept}] {len(dump['I'])} samples ({span:.0f} ms, "
            f"~{args.rpm / 60 * span / 1000:.1f} revolutions)"
        )
        kept += 1

    radar.disable_rolling_buffer()
    radar.disconnect()
    print(f"\n{kept}/{args.dumps} dumps -> {outdir}")
    print(f"analyze: uv run python scripts/analysis/bench_spin_analysis.py {outdir}")
    return 0 if kept else 1


if __name__ == "__main__":
    raise SystemExit(main())
