"""RESEARCH: does the two-round IWR transfer give the full dump's numbers on recorded shots?

Offline only. Each recorded full wide-IQ16 dump goes through the production runtime
(``IWR6843Runtime.process_shot``) twice with the same inputs:

* ``full``: the whole dump, as the kiosk measures it today;
* ``two_round``: the simulated radar (``two_round_sim``) freezes the dump and the runtime
  fetches it through the protocol: the overview, then the cells its own analysis reads.

Inputs follow the stage-1 studies (``iwr_transfer_study``): OPS ball/club speed from
the rows file; for the lab recordings, a configured tee from the manifest when the
shot's own launch range is withheld; for the outdoor ones, the session log's geometry.
Bytes are counted on the simulated UART (payloads, request lines, echo-free CLI framing);
link seconds take 10 bits a byte plus ``RTT_S`` per exchange.

``--full-only`` writes just the full-dump results (floats as ``repr``), so two source
trees can be checked bit for bit (``--compare-full a.jsonl b.jsonl``).

    PYTHONPATH=src python scripts/iwr6843/two_round_equivalence.py \\
        --rows <research>/iwr-transfer-study/rows.jsonl --out <research>/iwr-onchip-stage1/stage2-host
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from openflight.iwr6843.calibration_session import clone_calibration
from openflight.iwr6843.club import ClubWindowPolicy
from openflight.iwr6843.lcmf import Calibration
from openflight.iwr6843.runtime import TRANSFER_FULL, TRANSFER_TWO_ROUND, IWR6843Runtime
from openflight.iwr6843.two_round import TwoRoundClient
from openflight.iwr6843.two_round_sim import SimulatedIWR6843TwoRound, SimulatedSerial

BIN_M = 6.0 / 128
LAB_GEOMETRY = {"tilt_deg": 10.0, "radar_height_m": 0.1524, "ball_height_m": 0.021335}
SEPTEMBER_TEE_M = 1.575  # manifest default note: tee slant range 1.575 m
LAB_NET_M = 4.064
BAUDS = (1_041_667, 2_000_000)
BITS_PER_BYTE = 10
RTT_S = 0.005  # protocol latency per command/reply exchange (as stage 1C)
FULL_DUMP_BYTES = 732_812
FIELDS = (
    "withheld",
    "launch_status",
    "launch_accepted",
    "launch_deg",
    "horizontal_deg",
    "horizontal_status",
    "impact_t_s",
    "range_status",
    "range_m",
    "range_reason",
    "path_status",
    "path_accepted",
    "path_deg",
    "attack_status",
    "attack_deg",
)
METRICS = {  # metric -> (field, unit scale)
    "launch_deg": ("launch_deg", 1.0),
    "horizontal_deg": ("horizontal_deg", 1.0),
    "range_cm": ("range_m", 100.0),
    "path_deg": ("path_deg", 1.0),
    "attack_deg": ("attack_deg", 1.0),
    "impact_ms": ("impact_t_s", 1000.0),
}


@dataclass
class Shot:
    """One recorded dump and its estimator inputs."""

    path: Path
    dataset: str
    ball_speed_mph: float
    club_speed_mph: float | None
    club: str | None
    tee_fallback_m: float | None
    net_range_m: float
    geometry: dict


def _lab_tee(path: Path, dataset: str) -> float | None:
    if dataset == "september":
        return SEPTEMBER_TEE_M
    manifest = json.loads((path.parent / "manifest.json").read_text())
    entry = manifest.get(path.name, {})
    dest = entry.get("dest_bin", manifest.get("default", {}).get("dest_bin"))
    return dest * BIN_M if dest else None


def _outdoor(path: Path, row: dict) -> Shot:
    session = next(path.parent.parent.glob("session_*.jsonl"))
    capture = shot = None
    shots = {}
    for line in session.read_text().splitlines():
        record = json.loads(line)
        if record.get("type") == "shot_detected":
            shots[record["shot_number"]] = record
        elif (
            record.get("type") == "iwr6843_capture"
            and Path(record["capture_path"].replace("\\", "/")).name == path.name
        ):
            capture = record
    shot = shots.get(capture["shot_number"], {})
    effective = capture["runtime_config"]["calibration"]["effective"]
    return Shot(
        path=path,
        dataset=row["dataset"],
        ball_speed_mph=float(capture["ball_speed_mph"]),
        club_speed_mph=shot.get("club_speed_mph"),
        club=shot.get("club"),
        tee_fallback_m=None,
        net_range_m=float(capture["runtime_config"]["net_range_m"]),
        geometry={
            "tilt_deg": effective["tilt_deg"],
            "radar_height_m": effective["radar_height_m"],
            "ball_height_m": effective["ball_height_m"],
            "board_rotation_deg": effective["iwr_board_rotation_deg"],
        },
    )


def load_shots(rows_path: Path) -> list[Shot]:
    """The study's dumps with the inputs the stage-1 studies gave the estimators."""
    shots = []
    for line in rows_path.read_text().splitlines():
        row = json.loads(line)
        path = Path(row["path"])
        if row["dataset"] == "outdoors-test-7":
            shots.append(_outdoor(path, row))
            continue
        shots.append(
            Shot(
                path=path,
                dataset=row["dataset"],
                ball_speed_mph=float(row["ball_speed_mph"]),
                club_speed_mph=row.get("club_speed_mph"),
                club="7-iron",
                tee_fallback_m=_lab_tee(path, row["dataset"]),
                net_range_m=LAB_NET_M,
                geometry=dict(LAB_GEOMETRY),
            )
        )
    return shots


def _calibration(cal_path: str, shot: Shot, tee: float | None) -> Calibration:
    g = shot.geometry
    cal = clone_calibration(
        Calibration.load(cal_path),
        tilt_deg=g.get("tilt_deg"),
        radar_height_m=g.get("radar_height_m"),
        ball_height_m=g.get("ball_height_m"),
        board_rotation_deg=g.get("board_rotation_deg"),
    )
    if g.get("ball_height_m") is not None:
        cal.tee_ball_height_m = g["ball_height_m"]
    cal.tee_range_m = tee
    return cal


@dataclass
class _Capture:
    raw: bytes | None
    sequence: int = 0
    valid: bool = True
    partial_raw: bytes | None = None
    two_round_link: object | None = None


class _Monitor:
    def __init__(self, capture):
        self.capture = capture

    def capture_for_shot(self, _impact, timeout_s=0.0):  # noqa: ARG002
        """The one capture."""
        return self.capture

    def stop(self):
        """Nothing to release."""
        return None


def run(capture, shot: Shot, cal_path: str, tee: float | None, transfer: str):
    """The kiosk's IWR path on one capture."""
    runtime = IWR6843Runtime(
        capture_monitor=_Monitor(capture),
        calibration=_calibration(cal_path, shot, tee),
        net_range_m=shot.net_range_m,
        tx_order="normal",
        azimuth_offset_deg=0.0,
        horizontal_phase_reference_rad=None,
        tdm_sign_policy="positive",
        club_window_policy=ClubWindowPolicy(),
        transfer=transfer,
    )
    return runtime.process_shot(
        impact_timestamp=None,
        ball_speed_mph=shot.ball_speed_mph,
        club=shot.club,
        club_speed_mph=shot.club_speed_mph,
    )


def numbers(result) -> dict:
    """The fields the stage-1 studies compared."""
    m, c, lr = result.measurement, result.club_path, result.launch_range
    return {
        "withheld": result.withheld_reason,
        "launch_status": getattr(m, "status", None),
        "launch_accepted": bool(m.accepted) if m is not None else False,
        "launch_deg": getattr(m, "angle_deg", None),
        "horizontal_deg": getattr(m, "horizontal_deg", None),
        "horizontal_status": getattr(m, "horizontal_status", None),
        "impact_t_s": getattr(m, "impact_t_s", None),
        "range_status": getattr(lr, "status", None),
        "range_m": getattr(lr, "range_m", None) if lr is not None and lr.measured else None,
        "range_reason": getattr(lr, "reason", None),
        "path_status": getattr(c, "status", None),
        "path_accepted": bool(c.accepted) if c is not None else False,
        "path_deg": getattr(c, "path_deg", None),
        "attack_status": getattr(c, "attack_angle_status", None),
        "attack_deg": getattr(c, "candidate_attack_angle_deg", None),
    }


def full_numbers(shot: Shot, cal_path: str) -> tuple[dict, float | None]:
    """The full dump's numbers and the tee used (a fallback only when range is withheld)."""
    raw = shot.path.read_bytes()
    out = numbers(run(_Capture(raw), shot, cal_path, None, TRANSFER_FULL))
    tee = None
    if out["withheld"] and shot.tee_fallback_m is not None:
        tee = shot.tee_fallback_m
        out = numbers(run(_Capture(raw), shot, cal_path, tee, TRANSFER_FULL))
    return out, tee


def two_round_numbers(shot: Shot, cal_path: str, tee: float | None) -> tuple[dict, dict]:
    """The numbers through the simulated radar, and the transfer's bytes and rounds."""
    radar = SimulatedIWR6843TwoRound()
    radar.freeze(shot.path.read_bytes())
    serial = SimulatedSerial(radar)
    started = time.perf_counter()
    result = run(
        _Capture(None, two_round_link=TwoRoundClient(serial)),
        shot,
        cal_path,
        tee,
        TRANSFER_TWO_ROUND,
    )
    elapsed = time.perf_counter() - started
    record = dict(result.transfer or {})
    exchanges = len(radar.commands)
    record.update(
        uart_bytes_to_radar=serial.written,
        uart_bytes_from_radar=serial.read_bytes,
        exchanges=exchanges,
        host_wall_s=elapsed,
        radar_still_frozen=radar.frozen,
    )
    for baud in BAUDS:
        record[f"link_s_{baud}"] = (
            serial.written + serial.read_bytes
        ) * BITS_PER_BYTE / baud + exchanges * RTT_S
    return numbers(result), record


def compare(full: dict, two: dict) -> dict:
    """Every discrete field equal, and each metric's absolute difference."""
    discrete = {
        key: full[key] == two[key]
        for key in FIELDS
        if not isinstance(full[key], float) or not isinstance(two[key], float)
    }
    diffs = {}
    for metric, (key, scale) in METRICS.items():
        a, b = full[key], two[key]
        if isinstance(a, float) and isinstance(b, float):
            diffs[metric] = abs(a - b) * scale
    return {
        "discrete_identical": all(discrete.values()),
        "discrete_differ": [key for key, same in discrete.items() if not same],
        "abs_diff": diffs,
        "bit_identical": full == two,
    }


def _stats(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    ordered = sorted(values)
    p90 = ordered[min(len(ordered) - 1, math.ceil(0.9 * len(ordered)) - 1)]
    return {
        "n": len(values),
        "median": statistics.median(values),
        "p90": p90,
        "max": max(values),
    }


def summarize(rows: list[dict]) -> dict:
    """Equivalence counts, per-metric differences and bytes per round."""
    transfers = [row["transfer"] for row in rows]
    rounds: dict[str, int] = {}
    for t in transfers:
        rounds[str(t["cell_rounds"])] = rounds.get(str(t["cell_rounds"]), 0) + 1
    out = {
        "dumps": len(rows),
        "discrete_identical": sum(row["compare"]["discrete_identical"] for row in rows),
        "bit_identical": sum(row["compare"]["bit_identical"] for row in rows),
        "transfer_errors": sum(1 for t in transfers if t.get("error")),
        "released": sum(1 for t in transfers if t.get("released")),
        "complete_requests": sum(1 for t in transfers if t.get("complete_request")),
        "metric_abs_diff": {
            metric: _stats(
                [
                    row["compare"]["abs_diff"][metric]
                    for row in rows
                    if metric in row["compare"]["abs_diff"]
                ]
            )
            for metric in METRICS
        },
        "cell_rounds": dict(sorted(rounds.items())),
        "overview_bytes": _stats([t["overview_bytes"] for t in transfers]),
        "round2_cells": _stats(
            [t["cells_per_round"][0] for t in transfers if t["cells_per_round"]]
        ),
        "round2_bytes": _stats([t["cells_bytes"][0] for t in transfers if t["cells_bytes"]]),
        "later_round_bytes": _stats([sum(t["cells_bytes"][1:]) for t in transfers]),
        "request_bytes": _stats([sum(t["request_bytes"]) for t in transfers]),
        "uart_bytes_total": _stats(
            [t["uart_bytes_to_radar"] + t["uart_bytes_from_radar"] for t in transfers]
        ),
        "host_wall_s_pc": _stats([t["host_wall_s"] for t in transfers]),
        "analysis_runs": _stats([len(t["analysis_s"]) for t in transfers]),
        "full_dump": {
            "bytes": FULL_DUMP_BYTES,
            **{f"link_s_{b}": FULL_DUMP_BYTES * BITS_PER_BYTE / b for b in BAUDS},
        },
    }
    for baud in BAUDS:
        out[f"link_s_{baud}"] = _stats([t[f"link_s_{baud}"] for t in transfers])
    return out


def compare_full(path_a: Path, path_b: Path) -> int:
    """Two ``--full-only`` result files, bit for bit."""
    a = [json.loads(line) for line in path_a.read_text().splitlines()]
    b = [json.loads(line) for line in path_b.read_text().splitlines()]
    same = sum(1 for x, y in zip(a, b) if x == y)
    print(json.dumps({"dumps": len(a), "bit_identical": same, "lengths_match": len(a) == len(b)}))
    return 0 if same == len(a) == len(b) else 1


def main(argv: list[str] | None = None) -> int:
    """Run the comparison and write rows.jsonl, summary.json and tables.md."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--rows", type=Path, help="stage-1 rows.jsonl listing the dumps")
    parser.add_argument("--cal", default="config/iwr6843_calibration_reference.json")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--full-only", type=Path, help="write only the full-dump numbers here")
    parser.add_argument("--compare-full", type=Path, nargs=2)
    args = parser.parse_args(argv)
    if args.compare_full:
        return compare_full(*args.compare_full)
    shots = load_shots(args.rows)
    if args.limit:
        shots = shots[: args.limit]
    if args.full_only:
        with args.full_only.open("w", encoding="utf-8") as handle:
            for shot in shots:
                out, tee = full_numbers(shot, args.cal)
                record = {"dump": shot.path.name, "tee": tee, **out}
                handle.write(json.dumps(record, default=repr) + "\n")
        return 0
    args.out.mkdir(parents=True, exist_ok=True)
    rows = []
    for index, shot in enumerate(shots):
        full, tee = full_numbers(shot, args.cal)
        two, transfer = two_round_numbers(shot, args.cal, tee)
        rows.append(
            {
                "dump": shot.path.name,
                "dataset": shot.dataset,
                "tee_mode": "configured_tee_from_manifest"
                if tee is not None
                else "kiosk_launch_range",
                "full": full,
                "two_round": two,
                "compare": compare(full, two),
                "transfer": transfer,
            }
        )
        print(
            f"[{index + 1}/{len(shots)}] {shot.path.name} "
            f"identical={rows[-1]['compare']['discrete_identical']} "
            f"rounds={transfer['cell_rounds']} bytes={transfer['link_bytes']}",
            flush=True,
        )
    with (args.out / "rows.jsonl").open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")
    summary = summarize(rows)
    (args.out / "summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary, indent=1))
    return 0 if summary["discrete_identical"] == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
