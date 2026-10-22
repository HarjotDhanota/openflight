#!/usr/bin/env python3
"""EXPERIMENTAL impact location, heel-toe and height, over a saved session. Unqualified.

Runs after a session on its recorded clips (a tester session folder, an unzipped
bundle or a bundle ``.zip``) with ``openflight.camera.impact_session``, and writes

  impact_location.csv / .json   one row per clip: impact_offset_mm (+ toe),
                                impact_height_mm (+ up), both 1-sigma
                                uncertainties, the label experimental_unqualified,
                                and the reason whenever a shot or an axis is refused
  overlays/<clip>.png           the analysed frames with mask, placed outline and
                                the reading -- LOOK AT THESE FIRST

With ``--truth`` (CSV: ``shot_id`` and ``impact_offset_mm`` and/or ``impact_height_mm``,
measured on face photos) it adds agreement per axis: bias, RMS, SD after bias, n,
and how many truths fall inside the reported 1- and 2-sigma.

    uv run python scripts/analysis/impact_location.py SESSION --out OUT_DIR \\
        [--anchors face_centre_anchors.json] [--handedness right|left] [--truth marks.csv]
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from openflight.camera import (  # noqa: E402
    impact_location as il,
    impact_session as session,
)

AXES = ("impact_offset_mm", "impact_height_mm")
SIGMAS = {
    "impact_offset_mm": "impact_offset_sigma_mm",
    "impact_height_mm": "impact_height_sigma_mm",
}


def read_truth(path: Path) -> dict[str, dict[str, float]]:
    """``shot_id`` -> {axis: mm} from a CSV with either or both axis columns."""
    truth: dict[str, dict[str, float]] = {}
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            key = (row.get("shot_id") or "").strip()
            values = {}
            for axis in AXES:
                text = (row.get(axis) or "").strip()
                try:
                    values[axis] = float(text)
                except ValueError:
                    continue
            if key and values:
                truth[key] = values
    return truth


def agreement(predicted: list[float], sigmas: list[float], truths: list[float]) -> dict[str, Any]:
    """Bias, RMSE, SD after bias, n and the share of truths inside 1 and 2 sigma."""
    count = len(predicted)
    if count == 0:
        return {"n": 0}
    error = np.asarray(predicted) - np.asarray(truths)
    z = np.abs(error) / np.maximum(np.asarray(sigmas), 1e-9)
    return {
        "n": count,
        "bias_mm": float(np.mean(error)),
        "rmse_mm": float(np.sqrt(np.mean(error**2))),
        "sd_after_bias_mm": float(np.std(error, ddof=1)) if count > 1 else math.nan,
        "within_1_sigma": float(np.mean(z <= 1.0)),
        "within_2_sigma": float(np.mean(z <= 2.0)),
    }


def score(rows: list[dict[str, Any]], truth: dict[str, dict[str, float]]) -> dict[str, Any]:
    """Agreement per axis over the rows that read that axis and have its truth."""
    result: dict[str, Any] = {}
    for axis in AXES:
        status = "status" if axis == "impact_offset_mm" else "height_status"
        paired = [r for r in rows if r.get(status) == "ok" and axis in truth.get(r["shot_id"], {})]
        result[axis] = agreement(
            [r[axis] for r in paired],
            [r[SIGMAS[axis]] for r in paired],
            [truth[r["shot_id"]][axis] for r in paired],
        )
    result["truth_unmatched"] = sorted(set(truth) - {r["shot_id"] for r in rows})
    return result


def build_parser() -> argparse.ArgumentParser:
    """Command-line interface."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("session", type=Path, help="session folder, unzipped bundle or .zip")
    parser.add_argument("--out", type=Path, default=Path("impact_location_out"))
    parser.add_argument("--anchors", type=Path, default=None, help=session.ANCHOR_SCHEMA)
    parser.add_argument("--truth", type=Path, default=None)
    parser.add_argument("--handedness", choices=il.HANDEDNESS, default=None)
    parser.add_argument("--club", default=None, help="override the club for every clip")
    parser.add_argument("--club-speed-mps", type=float, default=None)
    parser.add_argument(
        "--range-m", type=float, default=None, help="IWR slant range when the session has none"
    )
    parser.add_argument("--contact-fraction", type=float, default=None)
    parser.add_argument("--ball", type=float, nargs=3, metavar=("X", "Y", "DIAMETER"), default=None)
    parser.add_argument("--face-centre-offset-mm", type=float, default=il.FACE_CENTRE_TOEWARD_MM)
    parser.add_argument("--template-scope", choices=("club", "session"), default="club")
    parser.add_argument("--shot", action="append", default=[])
    parser.add_argument("--no-overlays", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Read every clip on both axes, write the report, score it against truth."""
    args = build_parser().parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    root = session.resolve_root(args.session, args.out)
    options = session.Options(
        handedness=args.handedness,
        club=args.club,
        club_speed_mps=args.club_speed_mps,
        range_m=args.range_m,
        contact_fraction=args.contact_fraction,
        ball=tuple(args.ball) if args.ball else None,
        anchors=session.read_anchors(args.anchors),
        face_centre_toeward_mm=args.face_centre_offset_mm,
        shots=tuple(args.shot),
        template_scope=args.template_scope,
    )
    report = session.analyse_session(
        root, options, overlays=None if args.no_overlays else args.out / "overlays"
    )
    if not report["rows"]:
        print(f"no camera clips (frames.npz) under {root}")
        return 1
    if args.truth:
        report["agreement"] = score(report["rows"], read_truth(args.truth))
    session.write_report(report, args.out)
    for line in session.summary_lines(report):
        print(line)
    for axis, stats in (report.get("agreement") or {}).items():
        if isinstance(stats, dict) and stats.get("n"):
            print(
                f"  {axis}: n={stats['n']} bias {stats['bias_mm']:+.1f} mm "
                f"RMSE {stats['rmse_mm']:.1f} mm, within 1/2 sigma "
                f"{stats['within_1_sigma']:.0%}/{stats['within_2_sigma']:.0%}"
            )
    print(f"wrote {args.out / 'impact_location.csv'} and impact_location.json")
    print(json.dumps({"label": il.EXPERIMENTAL_LABEL}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
