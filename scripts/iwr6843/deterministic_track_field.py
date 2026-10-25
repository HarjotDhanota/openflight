"""Run the deterministic IWR ball tracker over every complete field dump and report it.

For each dump with its shot's recorded runtime inputs (``session_*.jsonl`` beside it),
the production runtime is re-run as the kiosk ran it: launch angle and status, launch
range, its label, the competing-walk record when the tracker could not separate two
walks, and the time every range-walk search took. A dump without inputs (no OPS speed)
gets the IWR-only search the launch angle starts from (``moving_ball_range_track``).

    uv run python scripts/iwr6843/deterministic_track_field.py \\
        [--handoff <pi-handoff dir>] [--output field.jsonl]

On a Pi, ``--timing-only`` prints the per-search timings alone (the CPU cost check).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts" / "iwr6843"))

import partial_dump_simulation as sim  # noqa: E402

from openflight.iwr6843 import (  # noqa: E402
    runtime as iwr_runtime,
    shot as iwr_shot,
    tracking,
)
from openflight.iwr6843.lcmf import prepare_lcmf_capture  # noqa: E402


class _Recorder:
    """Times every range-walk search and keeps the runtime's shot result."""

    def __init__(self):
        self.searches: list[dict] = []
        self.result = None
        self._search = tracking.search_ball
        self._process = iwr_runtime.IWR6843Runtime.process_shot

    def search(self, *args, **kwargs):
        started = time.perf_counter()
        found = self._search(*args, **kwargs)
        self.searches.append(
            {
                "seconds": time.perf_counter() - started,
                "detections": found.n_detections,
                "pairs": found.n_pairs,
                "consensus_lines": found.n_consensus,
                "ambiguous": found.ambiguous,
            }
        )
        return found

    def process_hook(self):
        """A plain function to stand in for ``IWR6843Runtime.process_shot``."""
        original = self._process

        def process(runtime, **kwargs):
            self.result = original(runtime, **kwargs)
            return self.result

        return process


def complete_dumps(handoff: Path):
    """(session log, dump) for every shot dump recorded beside a session log."""
    for session in sorted(handoff.glob("*/arm*/paired/run-01/session_*.jsonl")):
        for dump in sorted((session.parent / "iwr6843").glob("*.l3dump")):
            yield session, dump


def run_with_inputs(raw: bytes, shot: sim.ShotContext) -> dict:
    """The production runtime on one dump, as the kiosk ran it, with search timings."""
    recorder = _Recorder()
    with (
        patch.object(tracking, "search_ball", recorder.search),
        patch.object(iwr_runtime.IWR6843Runtime, "process_shot", recorder.process_hook()),
    ):
        outcome = sim.run_runtime(raw, shot)
    outcome.pop("partial_recovery", None)
    launch_range = recorder.result.launch_range
    measurement = recorder.result.measurement
    return {
        **outcome,
        "ops_ball_speed_ms": shot.ball_speed_mph / tracking.MPH_PER_MS,
        "range_reason": launch_range.reason if launch_range is not None else None,
        "range_method": launch_range.method if launch_range is not None else None,
        "competing": launch_range.competing if launch_range is not None else None,
        "launch_track_ambiguity": getattr(measurement, "track_ambiguity", None),
        "searches": recorder.searches,
    }


def session_net_range_m(session: Path) -> float | None:
    """The net range the session's runtime ran with (any shot's recorded config)."""
    for line in session.read_text(encoding="utf-8").splitlines():
        config = json.loads(line).get("runtime_config") or {}
        if config.get("net_range_m") is not None:
            return float(config["net_range_m"])
    return None


def run_iwr_only(raw: bytes, net_range_m: float | None) -> dict:
    """The IWR-only search the launch angle starts from, for a dump with no OPS speed."""
    recorder = _Recorder()
    prepared = prepare_lcmf_capture(raw).vertical
    with patch.object(tracking, "search_ball", recorder.search):
        track, scope = iwr_shot.moving_ball_range_track(
            prepared, club=None, net_range_m=net_range_m
        )
    res = prepared.geometry.range_res_m
    return {
        "iwr_only": True,
        "track": None
        if track is None
        else {
            "speed_ms": track.speed_ms,
            "range_at_first_m": track.range_at(track.t_first, res),
            "n_inliers": track.n_inliers,
            "scope": scope,
        },
        "track_ambiguity": track.ambiguity.to_dict()
        if track is not None and track.ambiguity is not None
        else None,
        "searches": recorder.searches,
    }


def _row(record: dict) -> list:
    searches = record["searches"]
    total_ms = 1e3 * sum(item["seconds"] for item in searches)
    slowest_ms = 1e3 * max((item["seconds"] for item in searches), default=0.0)
    if record.get("iwr_only"):
        track = record["track"]
        found = (
            "-"
            if track is None
            else f"{track['speed_ms']:.1f} m/s @ {track['range_at_first_m']:.2f} m"
        )
        flag = "ambiguous" if record["track_ambiguity"] else "-"
        return [record["dump"], "IWR only", found, "-", flag, f"{total_ms:.0f} / {slowest_ms:.0f}"]
    launch = record["launch_deg"]
    competing = record["competing"]
    verdict = competing["verdict"] if competing else "-"
    range_text = (
        f"{record['range_m']:.3f} m ({record['range_label']})"
        if record["range_m"] is not None
        else record["range_label"]
    )
    return [
        record["dump"],
        f"{record['ops_ball_speed_ms']:.1f}",
        f"{launch:.2f} ({record['launch_status']})"
        if launch is not None
        else record["launch_status"],
        range_text,
        verdict,
        f"{total_ms:.0f} / {slowest_ms:.0f}",
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--handoff", type=Path, default=REPO.parent / "pi-handoff")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--timing-only", action="store_true")
    args = parser.parse_args(argv)
    records = []
    for session, dump in complete_dumps(args.handoff):
        shot = sim.shot_contexts(session).get(dump.name)
        raw = dump.read_bytes()
        record = (
            run_with_inputs(raw, shot)
            if shot is not None
            else run_iwr_only(raw, session_net_range_m(session))
        )
        record = {"session": session.parts[-5], "dump": dump.name, **record}
        records.append(record)
        if args.timing_only:
            for item in record["searches"]:
                print(
                    f"{dump.name} {1e3 * item['seconds']:.1f} ms "
                    f"({item['detections']} detections, {item['pairs']} pairs)"
                )
    if args.output:
        args.output.write_text(
            "".join(json.dumps(record, default=str) + "\n" for record in records),
            encoding="utf-8",
        )
    if not args.timing_only:
        header = [
            "dump",
            "OPS m/s",
            "launch deg",
            "launch range",
            "competing",
            "search ms total / max",
        ]
        print("| " + " | ".join(header) + " |")
        print("|" + "---|" * len(header))
        for record in records:
            print("| " + " | ".join(str(cell) for cell in _row(record)) + " |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
