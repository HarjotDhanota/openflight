#!/usr/bin/env python3
"""Grade each swing's IWR launch-point range against a taped ball (13 Oct, B5).

Decision "a" (13 Oct): the resting ball's distance comes from each swing's own IWR
capture, worked back from the ball's post-impact track. This re-ranges every logged
shot in the given session folders with the kiosk's estimator
(``openflight.iwr6843.launch_range``) and grades it against the tape in a bench
truth FILE (never a UI input; schema in ``openflight.iwr6843.launch_range_grade``):

    {"schema": "openflight.launch_range_truth.v1",
     "tape_reference": "radar window face to the near side of the ball",
     "sessions": {"indoor-test-3": {"tape_m": 2.14}}}

Without ``--truth`` every shot is still listed, ungraded. The method is NOT
validated against a tape until a taped session passes the provisional gate.

    uv run python scripts/analysis/grade_launch_range.py SESSION_DIR... \\
        --truth tape.json [--json grade.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from openflight.iwr6843 import launch_range_grade as grade  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("sessions", nargs="+", type=Path, help="session or run folders")
    parser.add_argument("--truth", type=Path, help="bench truth file with the taped distances")
    parser.add_argument("--json", type=Path, help="write the full grade here")
    args = parser.parse_args(argv)
    truth = grade.load_truth(args.truth) if args.truth else grade.Truth(sessions={}, shots={})
    report = grade.grade_sessions(args.sessions, truth)
    print(grade.format_report(report))
    if args.json:
        args.json.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
