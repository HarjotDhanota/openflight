#!/usr/bin/env python3
"""Summarize a stage 1b Pi trace into JSON and Markdown (no hardware needed)."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from openflight import pi_trace_summary as pts  # noqa: E402  pylint: disable=wrong-import-position


def _finite_number(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a number") from None
    if not math.isfinite(value):
        raise argparse.ArgumentTypeError(f"{text!r} is not a finite number")
    return value


def _allowance(text: str) -> float:
    value = _finite_number(text)
    if value < 0:
        raise argparse.ArgumentTypeError(f"allowance must be >= 0, not {text}")
    return value


def _positive(text: str) -> float:
    value = _finite_number(text)
    if value <= 0:
        raise argparse.ArgumentTypeError(f"must be > 0, not {text}")
    return value


def main(argv: list[str] | None = None) -> int:
    """Write <out>.json and <out>.md; print the P1 headline or what makes it incomplete."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace", type=Path)
    parser.add_argument("--out", type=Path, required=True, help="output stem (.json and .md added)")
    parser.add_argument(
        "--sheet",
        type=Path,
        default=None,
        help="Part C attempt log as CSV: sheet_id,kind,wall_time",
    )
    parser.add_argument(
        "--allowance-ms",
        type=_allowance,
        default=None,
        help="impact-to-edge allowance A: acoustic (+ board delay when measured)",
    )
    parser.add_argument("--board-delay-unmeasured", action="store_true")
    parser.add_argument("--match-window-s", type=_positive, default=pts.MATCH_WINDOW_S)
    parser.add_argument("--sample-rate-hz", type=_positive, default=30000)
    parser.add_argument("--browser-sid", default=None, help="designated kiosk socket id")
    args = parser.parse_args(argv)
    try:
        trace = pts.load_trace(args.trace)
        sheet = pts.read_sheet(args.sheet) if args.sheet else None
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    attempts = pts.build_attempts(
        trace, sample_rate_hz=args.sample_rate_hz, browser_sid=args.browser_sid
    )
    ledger = pts.build_ledger(
        attempts,
        trace,
        sheet,
        allowance_ms=args.allowance_ms,
        board_delay_unmeasured=args.board_delay_unmeasured,
        match_window_s=args.match_window_s,
    )
    summary = pts.summarize(trace, attempts, ledger, allowance_ms=args.allowance_ms)
    stem = str(args.out)
    Path(f"{stem}.json").write_text(
        json.dumps(summary, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    Path(f"{stem}.md").write_text(pts.render_markdown(summary), encoding="utf-8")
    p1 = summary["p1"]
    print(p1["headline"] if p1["complete"] else f"P1 incomplete: {', '.join(p1['missing'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
