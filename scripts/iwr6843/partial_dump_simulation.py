"""Simulate lossy IWR6843 transfers on complete field dumps and score the partial decoder.

The 24 Oct 2026 session lost bytes from 32 of 33 dumps and kept none of them, so
there is no real partial dump. This builds them: it deletes runs of bytes from
complete dumps the way the field lost them (whole 256-byte units, totals drawn
from the 32 logged shortfalls, 1-N runs at random byte positions), appends the
firmware's CLI reply the reader keeps, and runs ``recover_partial_dump``.

Per trial it records whether the decoder placed every kept sample exactly where
the complete dump has it (the safety check: it must never be wrong), how many
frames it kept against the frames the loss left untouched, and, when a session
event carries the shot's runtime config, the launch angle and launch range the
production runtime produces from the recovered dump against the complete one.

    uv run python scripts/iwr6843/partial_dump_simulation.py \\
        --session <session dir>/arm5/paired/run-01/session_<...>.jsonl \\
        --dump <a complete .l3dump> --trials 50 --output partial-sim.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from openflight.iwr6843.dump import parse_dump, parse_header  # noqa: E402
from openflight.iwr6843.monitor import IWR6843Capture  # noqa: E402
from openflight.iwr6843.partial_dump import (  # noqa: E402
    LOSS_QUANTUM_BYTES,
    recover_partial_dump,
    select_frames,
)

CLI_REPLY = b"Done\nl3dump:/>"
# 24 Oct 2026 free_play.log: bytes received per short dump (the 14-byte reply included).
FIELD_RECEIVED_NBYTES = (
    713114, 729498, 731546, 723866, 725914, 714394, 722842, 717466, 721818, 729754,
    717210, 724890, 711322, 722586, 720794, 727450, 720794, 723354, 729242, 720282,
    726938, 724378, 716186, 723354, 711322, 706202, 702874, 710554, 696218, 711066,
    700314, 723610,
)  # fmt: skip
FIELD_EXPECTED_NBYTES = 732_812


def field_loss_units() -> list[int]:
    """The logged shortfalls in 256-byte units, once the CLI reply is counted."""
    units = []
    for received in FIELD_RECEIVED_NBYTES:
        lost = FIELD_EXPECTED_NBYTES - (received - len(CLI_REPLY))
        if lost % LOSS_QUANTUM_BYTES:
            raise ValueError(f"field shortfall {lost} is not whole 256-byte units")
        units.append(lost // LOSS_QUANTUM_BYTES)
    return units


def delete_runs(
    raw: bytes,
    rng: np.random.Generator,
    *,
    total_units: int,
    n_runs: int,
    quantum: int,
    early_nbytes: int = 0,
) -> tuple[bytes, list[tuple[int, int]]]:
    """``raw`` with ``n_runs`` non-overlapping runs (``total_units`` x ``quantum``) removed.

    Runs land at uniform byte positions in the payload; with ``early_nbytes`` the first
    run instead starts uniformly within the payload's first ``early_nbytes`` bytes (the
    stress case: the first loop has nothing earlier to be checked against). Returns the
    transfer as the reader keeps it (the CLI reply appended) and the true runs as
    (payload offset, size).
    """
    meta = parse_header(raw)
    payload_start = meta["header_nbytes"] + meta["frame_metadata_nbytes"]
    payload_nbytes = len(raw) - payload_start
    n_runs = max(1, min(n_runs, total_units))
    cuts = np.sort(rng.choice(np.arange(1, total_units), n_runs - 1, replace=False))
    sizes = [int(size) * quantum for size in np.diff(np.r_[0, cuts, total_units])]
    rng.shuffle(sizes)
    free = payload_nbytes - sum(sizes)
    # stars and bars: n_runs sorted offsets into the free bytes keep runs apart
    gaps_before = np.sort(rng.integers(0, free + 1, n_runs))
    if early_nbytes:
        gaps_before[0] = rng.integers(
            0, min(early_nbytes, int(gaps_before[1:2].min(initial=free)) + 1)
        )
    runs, removed = [], 0
    for gap, size in zip(gaps_before, sizes):
        start = int(gap) + removed
        runs.append((start, size))
        removed += size
    return delete_runs_at(raw, runs)


def delete_runs_at(raw: bytes, runs: list[tuple[int, int]]) -> tuple[bytes, list[tuple[int, int]]]:
    """``raw`` without the (true payload offset, size) runs, as the reader keeps it."""
    meta = parse_header(raw)
    payload_start = meta["header_nbytes"] + meta["frame_metadata_nbytes"]
    keep = np.ones(len(raw), dtype=bool)
    for start, size in runs:
        keep[payload_start + start : payload_start + start + size] = False
    kept = np.frombuffer(raw, dtype=np.uint8)[keep].tobytes()
    return kept + CLI_REPLY, list(runs)


def frames_touched(meta: dict, runs: list[tuple[int, int]]) -> set[int]:
    """Frames that lost at least one byte."""
    frame_nbytes = (
        meta["chirps_per_frame"]
        * meta["n_rx"]
        * (2 if meta["sample_fmt"] == 5 else 4)
        * np.asarray(meta["range_bin_counts"])
    )
    edges = np.concatenate(([0], np.cumsum(frame_nbytes)))
    touched = set()
    for start, size in runs:
        first = int(np.searchsorted(edges, start, side="right")) - 1
        last = int(np.searchsorted(edges, start + size - 1, side="right")) - 1
        touched.update(range(first, last + 1))
    return touched


@dataclass
class ShotContext:
    """Everything the production runtime needs to re-run one recorded shot."""

    shot_number: int
    runtime_config: dict
    ball_speed_mph: float
    club: str | None
    club_speed_mph: float | None


def shot_contexts(session_path: Path) -> dict[str, ShotContext]:
    """Capture file name -> its shot's recorded runtime inputs."""
    contexts = {}
    for line in session_path.read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        if event.get("type") != "iwr6843_capture" or not event.get("capture_path"):
            continue
        config = event.get("runtime_config") or {}
        inputs = config.get("per_shot_inputs") or {}
        if inputs.get("ball_speed_mph") is None:
            continue
        contexts[Path(event["capture_path"].replace("\\", "/")).name] = ShotContext(
            shot_number=event["shot_number"],
            runtime_config=config,
            ball_speed_mph=float(inputs["ball_speed_mph"]),
            club=inputs.get("club"),
            club_speed_mph=inputs.get("club_speed_mph"),
        )
    return contexts


class _OneCapture:
    """A capture monitor that hands the runtime one fixed dump, or one failed transfer."""

    def __init__(self, raw: bytes | None, partial_raw: bytes | None = None):
        self.raw = raw
        self.partial_raw = partial_raw

    def capture_for_shot(self, _timestamp, *, timeout_s):  # noqa: D401 - monitor API
        del timeout_s
        return IWR6843Capture(
            sequence=1,
            trigger_timestamp=0.0,
            completed_timestamp=0.0,
            dump_duration_s=0.0,
            raw=self.raw,
            path=None,
            error=None if self.raw is not None else "IWR6843 dump ended early (simulated)",
            partial_nbytes=len(self.partial_raw or b""),
            partial_raw=self.partial_raw,
        )


def run_runtime(raw: bytes | None, shot: ShotContext, *, partial_raw: bytes | None = None) -> dict:
    """Launch angle and launch range from the production runtime, as the kiosk ran it.

    ``raw`` is a whole dump; with ``partial_raw`` instead the runtime gets a failed
    transfer and decodes it itself, as it would in the kiosk.
    """
    from openflight.iwr6843.club import ClubWindowPolicy
    from openflight.iwr6843.replay import build_replay_calibration
    from openflight.iwr6843.runtime import IWR6843Runtime

    config = shot.runtime_config
    effective = config["calibration"]["effective"]
    inputs = config.get("per_shot_inputs") or {}
    tilt = inputs.get("effective_tilt_deg")
    roll = inputs.get("effective_roll_deg")
    with tempfile.TemporaryDirectory(prefix="openflight-partial-sim-") as frozen:
        path = Path(frozen) / "calibration.json"
        path.write_text(json.dumps(config["calibration"]["source_payload"]), encoding="utf-8")
        calibration = build_replay_calibration(
            path,
            tee_range_m=effective.get("tee_slant_range_m"),
            tilt_deg=tilt if tilt is not None else effective["tilt_deg"],
            radar_height_m=effective["radar_height_m"],
            ball_height_m=effective["ball_height_m"],
            board_rotation_deg=effective.get("iwr_board_rotation_deg"),
            roll_deg=roll,
        )
    calibration = replace(calibration, range_bias_m=float(effective["range_bias_m"]))
    runtime = IWR6843Runtime(
        capture_monitor=_OneCapture(raw, partial_raw),
        calibration=calibration,
        net_range_m=config.get("net_range_m"),
        tx_order=config.get("tx_order", "normal"),
        tdm_sign_policy=config.get("tdm_sign_policy", "positive"),
        azimuth_offset_deg=config.get("azimuth_offset_deg"),
        horizontal_phase_reference_rad=config.get("horizontal_phase_reference_rad"),
        club_window_policy=ClubWindowPolicy(**config["club_window_policy"]),
        club_impact_correction_s=float(config["club_impact_correction_s"]),
    )
    result = runtime.process_shot(
        impact_timestamp=0.0,
        ball_speed_mph=shot.ball_speed_mph,
        club=shot.club,
        club_speed_mph=shot.club_speed_mph,
        tilt_deg=tilt,
        roll_deg=roll,
        tee_range_m=inputs.get("tee_range_m"),
    )
    measurement, launch_range = result.measurement, result.launch_range
    accepted = measurement is not None and measurement.accepted
    return {
        "launch_status": measurement.status if measurement is not None else result.withheld_reason,
        "launch_deg": measurement.angle_deg if accepted else None,
        "horizontal_deg": measurement.horizontal_deg if accepted else None,
        "range_status": launch_range.status if launch_range is not None else None,
        "range_m": launch_range.range_m
        if launch_range is not None and launch_range.status == "measured"
        else None,
        "range_label": launch_range.label if launch_range is not None else None,
        "partial_recovery": result.partial_recovery,
    }


def trial(raw, cube, meta, rng, *, total_units, n_runs, shot, baseline, early_nbytes=0) -> dict:
    """One simulated lossy transfer, decoded and scored."""
    partial, runs = delete_runs(
        raw,
        rng,
        total_units=total_units,
        n_runs=n_runs,
        quantum=LOSS_QUANTUM_BYTES,
        early_nbytes=early_nbytes,
    )
    started = time.perf_counter()
    recovery = recover_partial_dump(partial)
    decode_s = time.perf_counter() - started
    touched = frames_touched(meta, runs)
    row = {
        "total_units": total_units,
        "n_runs": len(runs),
        "runs": runs,
        "frames_touched": sorted(touched),
        "status": recovery.status,
        "reason": recovery.reason,
        "frames_kept": list(recovery.frames_kept),
        "runs_located": len(recovery.runs),
        "decode_s": round(decode_s, 3),
        "wrong_samples": 0,
    }
    if not recovery.usable:
        return row
    _meta, recovered = parse_dump(recovery.raw)
    for index, frame in enumerate(recovery.frames_kept):
        row["wrong_samples"] += int(np.count_nonzero(recovered[index] != cube[frame]))
    if shot is not None and baseline is not None:
        outcome = run_runtime(None, shot, partial_raw=partial)
        outcome.pop("partial_recovery")
        row.update({f"recovered_{key}": value for key, value in outcome.items()})
    return row


def _envelope_record(name: str, raw: bytes, shot: ShotContext) -> dict:
    seeds = seed_envelope(raw, shot)
    record = {"dump": name, "seeds": seeds}
    for key in ("launch_deg", "range_m"):
        values = [row[key] for row in seeds if row[key] is not None]
        if values:
            record[key] = (min(values), max(values))
    return record


def frame_drop_control(raw: bytes, shot: ShotContext, baseline: dict) -> list[dict]:
    """The estimators with each single frame removed and nothing else missing.

    Separates what the estimators do with fewer frames from anything the decoder does.
    """
    n_frames = parse_header(raw)["n_frames"]
    rows = []
    for dropped in range(n_frames):
        kept = [frame for frame in range(n_frames) if frame != dropped]
        result = run_runtime(select_frames(raw, kept), shot)
        rows.append({"control_dropped": [dropped], "baseline": baseline, **result})
    return rows


def seed_envelope(raw: bytes, shot: ShotContext) -> list[dict]:
    """The whole dump's answer, the yardstick a partial dump's answer is judged against.

    Until 25 Oct 2026 the ball tracker sampled point pairs from a seeded generator and
    this re-ran the dump under seeds 1-6 to show how far the estimators moved with no
    data lost. The tracker now tries every pair (``tracking.search_range_walks``): the
    same bytes give one answer, so the envelope is that single run.
    """
    return [{"seed": None, **run_runtime(raw, shot)}]


def _table(rows: list[list], header: list[str]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows]
    return "\n".join(lines)


def _pct(part: int, whole: int) -> str:
    return f"{100.0 * part / whole:.1f}%" if whole else "-"


def _spread(values) -> str:
    values = np.abs(np.asarray(values, dtype=float))
    if not values.size:
        return "-"
    return f"{np.median(values):.2f} / {np.percentile(values, 95):.2f} / {values.max():.2f}"


def _decoder_table(rows: list[dict]) -> str:
    by_runs: dict[int, list[dict]] = {}
    for row in rows:
        by_runs.setdefault(row["n_runs_asked"], []).append(row)
    table = []
    for n_runs, group in sorted(by_runs.items()):
        usable = sum(1 for row in group if row["status"] == "recovered")
        untouched = sum(24 - len(row["frames_touched"]) for row in group)
        kept = sum(len(row["frames_kept"]) for row in group)
        table.append(
            [
                n_runs,
                len(group),
                _pct(usable, len(group)),
                sum(row["wrong_samples"] for row in group),
                f"{kept}/{untouched} ({_pct(kept, untouched)})",
                len(group) - usable,
            ]
        )
    return _table(
        table,
        ["runs", "trials", "decoded", "misplaced samples", "kept / untouched frames", "refused"],
    )


def _estimator_table(group: list[dict], envelope: dict) -> str:
    base = group[0]["baseline"]
    lines = []
    for n_runs in sorted({row["n_runs_asked"] for row in group}):
        subset = [row for row in group if row["n_runs_asked"] == n_runs]
        cells = [n_runs, len(subset)]
        for key, scale, unit in (("launch_deg", 1.0, "deg"), ("range_m", 1000.0, "mm")):
            if base.get(key) is None:
                cells += ["-", "-", "-"]
                continue
            got = [
                row[f"recovered_{key}"] for row in subset if row.get(f"recovered_{key}") is not None
            ]
            low, high = envelope.get(key, (base[key], base[key]))
            inside = sum(1 for value in got if low - 0.5 / scale <= value <= high + 0.5 / scale)
            cells += [
                _pct(len(got), len(subset)),
                _spread([(value - base[key]) * scale for value in got]) + f" {unit}",
                _pct(inside, len(got)),
            ]
        lines.append(cells)
    return _table(
        lines,
        [
            "runs",
            "trials",
            "launch out",
            "launch |err| p50/p95/max",
            "in seed envelope",
            "range out",
            "range |err| p50/p95/max",
            "in seed envelope",
        ],
    )


def summarize(rows: list[dict], envelopes: dict[str, dict] | None = None) -> str:
    """Markdown tables: decoder safety and yield per run count, then estimator outcomes."""
    out = [_decoder_table(rows)]
    estimated = [row for row in rows if row.get("baseline")]
    for dump in sorted({row["dump"] for row in estimated}):
        group = [row for row in estimated if row["dump"] == dump]
        base = group[0]["baseline"]
        envelope = (envelopes or {}).get(dump, {})
        out.append(
            f"{dump}: whole dump launch {base.get('launch_deg')}, range {base.get('range_m')}"
        )
        if envelope:
            ranges = {key: envelope[key] for key in ("launch_deg", "range_m") if key in envelope}
            out.append(f"whole-dump answer (the tracker is deterministic): {ranges}")
        out.append(_estimator_table(group, envelope))
    return "\n\n".join(out)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dump", type=Path, action="append", default=[])
    parser.add_argument("--session", type=Path, action="append", default=[])
    parser.add_argument("--trials", type=int, default=20)
    parser.add_argument("--runs", type=int, nargs="+", default=[1, 2, 4, 8])
    parser.add_argument("--seed", type=int, default=20261025)
    parser.add_argument(
        "--early-nbytes",
        type=int,
        default=0,
        help="start each trial's first run within the payload's first N bytes (stress test)",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--summarize",
        type=Path,
        nargs="+",
        help="print markdown tables for earlier --output files instead of simulating",
    )
    parser.add_argument(
        "--envelopes",
        type=Path,
        help="with --summarize: the --seed-envelope output",
    )
    parser.add_argument(
        "--seed-envelope",
        action="store_true",
        help="instead of simulating loss, record each whole dump's (deterministic) answer",
    )
    parser.add_argument(
        "--frame-drop-control",
        action="store_true",
        help="instead of simulating loss, re-run each shot with one whole frame removed",
    )
    args = parser.parse_args(argv)
    if args.summarize:
        rows = [
            json.loads(line)
            for path in args.summarize
            for line in path.read_text(encoding="utf-8").splitlines()
        ]
        envelopes = None
        if args.envelopes:
            envelopes = {
                record["dump"]: record
                for record in map(
                    json.loads, args.envelopes.read_text(encoding="utf-8").splitlines()
                )
            }
        print(summarize(rows, envelopes))
        return 0
    if args.output is None or not args.dump:
        parser.error("--dump and --output are required unless --summarize")
    contexts: dict[str, ShotContext] = {}
    for session in args.session:
        contexts.update(shot_contexts(session))
    units = field_loss_units()
    rng = np.random.default_rng(args.seed)
    with args.output.open("w", encoding="utf-8") as out:
        for dump_path in args.dump:
            raw = dump_path.read_bytes()
            meta, cube = parse_dump(raw)
            shot = contexts.get(dump_path.name)
            baseline = run_runtime(raw, shot) if shot is not None else None
            if args.seed_envelope:
                if shot is not None:
                    out.write(json.dumps(_envelope_record(dump_path.name, raw, shot)) + "\n")
                continue
            if args.frame_drop_control:
                for row in frame_drop_control(raw, shot, baseline) if shot is not None else []:
                    row["dump"] = dump_path.name
                    out.write(json.dumps(row, default=float) + "\n")
                continue
            for n_runs in args.runs:
                for _ in range(args.trials):
                    row = trial(
                        raw,
                        cube,
                        meta,
                        rng,
                        total_units=int(rng.choice(units)),
                        n_runs=n_runs,
                        shot=shot,
                        baseline=baseline,
                        early_nbytes=args.early_nbytes,
                    )
                    row.update(dump=dump_path.name, baseline=baseline, n_runs_asked=n_runs)
                    out.write(json.dumps(row, default=float) + "\n")
                    out.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
