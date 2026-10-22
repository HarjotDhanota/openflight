#!/usr/bin/env python3
"""How long the kiosk's live impact reading takes per shot on this machine. EXPERIMENTAL.

Runs ``openflight.camera.impact_live.LiveAnalyser`` the way the kiosk's worker
does: one shot at a time, in order, with the idle-time outline refresh after
each. Prints every shot's reading time by stage and a summary.

    uv run python scripts/analysis/impact_live_benchmark.py --synthetic 10 [--size 1280x800]
    uv run python scripts/analysis/impact_live_benchmark.py SESSION [--range-m 1.6]

``--synthetic N`` renders N swings with the test suite's synthetic iron (every
stage runs). ``SESSION`` reads a recorded session's clips with each shot's own
logged record; ``--range-m`` stands in for a shot without its own ball distance.
``--max-width 0`` reads the clips at their recorded size.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
for path in (ROOT / "src", ROOT / "tests"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from openflight.camera import (  # noqa: E402
    impact_live as live,
    impact_session as session,
)


def synthetic_jobs(count: int, width: int, height: int, folder: Path) -> list[dict]:
    """``count`` synthetic swings with a 7-iron, as the kiosk would send them."""
    import test_camera_impact_location as synthetic  # noqa: PLC0415
    import test_impact_live as fixtures  # noqa: PLC0415

    scale = width / synthetic.WIDTH
    jobs = []
    for index in range(count):
        frames, _ball = synthetic.render_swing(
            truth_mm=-6.0 + index,
            frames=24,
            width=width,
            height=height,
            focal=synthetic.FOCAL * scale,
            contact_time=10.3 + 0.05 * (index % 8),
        )
        clip = folder / f"camera_{index + 1:03d}"
        clip.mkdir(parents=True)
        np.savez(clip / "frames.npz", frames=frames, pre_trigger_count=np.int32(11))
        (clip / "metadata.json").write_text(
            json.dumps({"delivered_fps": synthetic.FPS}), encoding="utf-8"
        )
        jobs.append(
            {
                "job_id": index + 1,
                "session_uuid": "benchmark",
                "shot_number": index + 1,
                "clip_path": str(clip),
                "shot": fixtures.shot_record(index + 1),
                "start": fixtures.START,
                "handedness": "right",
            }
        )
    return jobs


def session_jobs(root: Path, range_m: float | None) -> list[dict]:
    """Every saved clip of a recorded session with its shot's logged record."""
    jobs = []
    for clip in session.find_clips(root):
        run = session.read_run(clip.run_dir) if clip.run_dir else None
        number = run.captures.get(clip.shot_id) if run else None
        shot = dict(run.shots.get(number, {})) if run and number is not None else {}
        if range_m and not shot.get("ball_range_m"):
            shot.update(ball_range_m=range_m, ball_range_source="benchmark_given")
        jobs.append(
            {
                "job_id": len(jobs) + 1,
                "session_uuid": "benchmark",
                "shot_number": number if number is not None else 1000 + len(jobs),
                "clip_path": str(clip.path),
                "shot": shot,
                "start": (run.start.get("config") if run else None) or {},
                "handedness": session.session_handedness(run.start if run else {})[0],
            }
        )
    return jobs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("session", nargs="?", type=Path)
    parser.add_argument("--synthetic", type=int, default=0)
    parser.add_argument("--size", default="1280x800")
    parser.add_argument("--range-m", type=float)
    parser.add_argument("--max-width", type=int, default=live.ANALYSIS_MAX_WIDTH)
    args = parser.parse_args(argv)
    analyser = live.LiveAnalyser(analysis_max_width=args.max_width or None)
    with tempfile.TemporaryDirectory() as folder:
        if args.synthetic:
            width, height = (int(v) for v in args.size.lower().split("x"))
            jobs = synthetic_jobs(args.synthetic, width, height, Path(folder))
        elif args.session:
            jobs = session_jobs(args.session, args.range_m)
        else:
            parser.error("give a SESSION folder or --synthetic N")
        totals, refreshes = [], []
        for job in jobs:
            reading = analyser.analyse(job)
            started = time.perf_counter()
            analyser.refresh()
            refreshes.append((time.perf_counter() - started) * 1000.0)
            totals.append(reading["analysis_ms"])
            stages = " ".join(f"{k}={v:.0f}" for k, v in reading["stages_ms"].items())
            print(
                f"shot {job['shot_number']}: {reading['analysis_ms']:7.0f} ms ({stages}) "
                f"refresh after {refreshes[-1]:6.0f} ms  {reading['status']}: "
                f"{reading.get('reason') or reading.get('impact_offset_mm')}"
            )
    print(
        f"{len(totals)} shots at {reading['analysis_frame_size']}: reading median "
        f"{statistics.median(totals):.0f} ms, max {max(totals):.0f} ms; idle refresh max "
        f"{max(refreshes):.0f} ms"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
