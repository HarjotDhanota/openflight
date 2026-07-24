#!/usr/bin/env python3
"""Replay a session's raw captures through envelope + ripple spin variants.

No TrackMan comparison needed — this is the quick-look tool for a live test
session: per shot, the production envelope spin next to all four ripple
variants (freq/mag x hop 32/16), with rejection reasons for misses. Use
experiment_spin_ripple.py instead when a TrackMan comparison CSV exists.

Usage:
    uv run python scripts/analysis/replay_spin_ripple.py \
        ~/openflight_sessions/session_20260724_*.jsonl
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

logging.getLogger("openflight").setLevel(logging.ERROR)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from spin_experiment_lib import capture_from_entry, load_session_entries, to_int  # noqa: E402
from spin_ripple_estimator import VARIANT_NAMES, run_ripple_variants  # noqa: E402

from openflight.rolling_buffer.processor import RollingBufferProcessor  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", type=Path, nargs="+")
    args = parser.parse_args()

    processor = RollingBufferProcessor()
    header = f"{'shot':>4} {'ball mph':>8} {'env rpm':>8} {'env conf':>8}"
    for name in VARIANT_NAMES:
        header += f" {name:>11}"
    print(header)

    for session_path in args.session:
        shots, captures = load_session_entries(session_path)
        if not captures:
            print(f"{session_path}: no rolling_buffer_capture entries")
            continue
        print(f"-- {session_path} ({len(captures)} captures)")
        for capture_entry in captures:
            shot_number = to_int(capture_entry.get("shot_number"))
            capture = capture_from_entry(capture_entry)
            processed = processor.process_capture(capture)
            if not processed:
                print(f"{shot_number or '?':>4}  (no outbound readings)")
                continue

            spin = processed.spin
            env_ok = bool(
                spin and spin.spin_rpm > 0
                and not spin.at_lower_rail and not spin.at_upper_rail
            )
            row = (
                f"{shot_number or '?':>4} {processed.ball_speed_mph:>8.1f} "
                f"{spin.spin_rpm if env_ok else 0:>8.0f} "
                f"{spin.confidence if spin else 0:>8.2f}"
            )
            results = run_ripple_variants(
                capture.i_samples,
                capture.q_samples,
                processed.ball_speed_mph,
                processed.ball_timestamp_ms,
            )
            reasons = []
            for name in VARIANT_NAMES:
                result = results[name]
                if result.detected:
                    row += f" {result.spin_rpm:>11.0f}"
                else:
                    row += f" {'--':>11}"
                    reasons.append(f"{name}: {result.rejection_reason}")
            print(row)
            for reason in reasons:
                print(f"      {reason}")


if __name__ == "__main__":
    main()
