"""Grade each swing's IWR launch-point range against a taped ball (13 Oct, B5).

Bench truth is a file the operator writes after taping the ball (never a UI input):

    {"schema": "openflight.launch_range_truth.v1",
     "tape_reference": "what the tape ran from and to",
     "sessions": {"<a folder name in the session's path>": {"tape_m": 2.14}},
     "shots": {"<folder name>#<shot number>": {"tape_m": 2.10}}}

Each shot's own capture is re-ranged with ``openflight.iwr6843.launch_range`` (the
kiosk's estimator), from the session log's OPS243 ball and club speeds and the prior
the session recorded, if any. A shot is graded by its error against the tape and by
whether that error lies within twice its own stated uncertainty.

The gate is PROVISIONAL (owner to confirm at the first taped session): a session
passes when at least half its shots are measured, at least 80 % of the measured are
within twice their uncertainty, and their median absolute error is at most 5 cm.
Until a session passes, the per-swing range is NOT validated against a tape.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

from openflight.iwr6843 import launch_range as lr

TRUTH_SCHEMA = "openflight.launch_range_truth.v1"
REPORT_SCHEMA = "openflight.launch_range_grade.v1"
GATE = {
    "min_measured_fraction": 0.5,
    "within_sigmas": 2.0,
    "min_within_fraction": 0.8,
    "max_median_abs_error_m": 0.05,
    "status": "provisional",
}
NOT_VALIDATED = (
    "The per-swing IWR launch-point range is NOT validated against a tape yet: the owner "
    "has not confirmed it at a taped session."
)


@dataclass(frozen=True)
class Truth:
    """Tape distances by session folder name, and per shot."""

    sessions: Mapping[str, float]
    shots: Mapping[str, float]
    tape_reference: str | None = None

    def tape_for(self, session_dir: Path, shot_number: int | None = None) -> float | None:
        names = list(Path(session_dir).parts)
        if shot_number is not None:
            for name in names:
                key = f"{name}#{shot_number}"
                if key in self.shots:
                    return float(self.shots[key])
        for name in reversed(names):
            if name in self.sessions:
                return float(self.sessions[name])
        return None


def load_truth(path: Path) -> Truth:
    """The bench truth file (see the module docstring)."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping) or payload.get("schema") != TRUTH_SCHEMA:
        raise ValueError(f"the truth file must have schema {TRUTH_SCHEMA!r}")

    def tapes(section: Any) -> dict[str, float]:
        out = {}
        for key, value in dict(section or {}).items():
            tape = value.get("tape_m") if isinstance(value, Mapping) else value
            tape = float(tape)
            if not math.isfinite(tape) or tape <= 0.0:
                raise ValueError(f"tape distance for {key!r} must be positive")
            out[str(key)] = tape
        return out

    return Truth(
        sessions=tapes(payload.get("sessions")),
        shots=tapes(payload.get("shots")),
        tape_reference=payload.get("tape_reference"),
    )


def _read_log(path: Path) -> list[dict]:
    entries = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                entries.append(json.loads(line))
            except ValueError:
                continue
    return entries


def _session_prior(entries: Sequence[Mapping], run_dir: Path) -> lr.LaunchRangePrior | None:
    """The prior the session recorded (kiosk runtime config or the run's tee_range.json)."""
    for entry in entries:
        if entry.get("type") == "session_start":
            iwr = entry.get("iwr6843") or entry.get("config", {}).get("iwr6843") or {}
            prior = lr.LaunchRangePrior.from_dict((iwr or {}).get("range_prior"))
            if prior is not None:
                return prior
    contract = run_dir / "tee_range.json"
    if contract.is_file():
        try:
            handed = json.loads(contract.read_text(encoding="utf-8")).get("handed_to_swings") or {}
        except ValueError:
            handed = {}
        return lr.LaunchRangePrior.from_dict(handed.get("prior"))
    return None


def session_shots(run_dir: Path) -> tuple[list[dict], lr.LaunchRangePrior | None, float | None]:
    """Each logged shot's dump and OPS speeds, the session's prior and its net range."""
    shots: dict[int, dict] = {}
    entries: list[dict] = []
    for log in sorted(Path(run_dir).glob("session_*.jsonl")):
        entries.extend(_read_log(log))
    net_m = None
    for entry in entries:
        kind = entry.get("type")
        number = entry.get("shot_number")
        if kind == "iwr6843_capture" and number is not None:
            dump_name = Path(str(entry.get("capture_path") or "")).name
            shots.setdefault(int(number), {})["dump"] = (
                Path(run_dir) / "iwr6843" / dump_name if dump_name else None
            )
            shots[int(number)]["ball_speed_mph"] = entry.get("ball_speed_mph")
            config = entry.get("runtime_config") or {}
            if config.get("net_range_m"):
                net_m = float(config["net_range_m"])
        elif kind == "shot_detected" and number is not None:
            shots.setdefault(int(number), {})["club_speed_mph"] = entry.get("club_speed_mph")
    listed = [{"shot_number": number, **facts} for number, facts in sorted(shots.items())]
    return listed, _session_prior(entries, Path(run_dir)), net_m


def _run_dirs(path: Path) -> list[Path]:
    path = Path(path)
    if list(path.glob("session_*.jsonl")):
        return [path]
    return sorted({log.parent for log in path.rglob("session_*.jsonl")})


def range_shot(shot: Mapping, prior, net_m: float | None) -> dict:
    """Re-range one logged shot from its own dump with the kiosk's estimator."""
    from openflight.iwr6843.lcmf import prepare_lcmf_capture  # noqa: PLC0415

    dump = shot.get("dump")
    base = {"shot_number": shot.get("shot_number"), "dump": str(dump) if dump else None}
    if dump is None or not Path(dump).is_file():
        return {**base, "status": "not_measured", "reason": "the IWR dump is not in the folder"}
    try:
        prepared = prepare_lcmf_capture(Path(dump).read_bytes())
        estimate = lr.estimate_launch_range(
            prepared.vertical,
            ball_speed_mph=shot.get("ball_speed_mph"),
            club_speed_mph=shot.get("club_speed_mph"),
            prior=prior,
            net_range_m=net_m,
        )
    except (OSError, ValueError, IndexError) as error:
        return {**base, "status": "not_measured", "reason": f"the dump did not decode: {error}"}
    record = estimate.to_dict()
    return {
        **base,
        "status": "measured" if estimate.measured else "not_measured",
        "reason": estimate.reason,
        "method": estimate.method,
        "range_m": estimate.range_m,
        "uncertainty_m": estimate.uncertainty_m,
        "candidate_range_m": estimate.candidate_range_m,
        "ball_track": record["ball_track"],
        "club_track": record["club_track"],
    }


def summarize(shots: Iterable[Mapping], *, tape_m: float | None) -> dict:
    """The session's grade against its tape, by the provisional gate."""
    shots = list(shots)
    measured = [shot for shot in shots if shot.get("status") == "measured"]
    summary: dict[str, Any] = {
        "shots": len(shots),
        "measured": len(measured),
        "measured_fraction": (len(measured) / len(shots)) if shots else 0.0,
        "gate_thresholds": dict(GATE),
    }
    ranges = [float(shot["range_m"]) for shot in measured]
    if ranges:
        summary["median_range_m"] = float(np.median(ranges))
        summary["spread_m"] = float(np.ptp(ranges)) if len(ranges) > 1 else 0.0
    if tape_m is None:
        summary["gate"] = "no_tape"
        return summary
    errors = [float(shot["range_m"]) - tape_m for shot in measured]
    within = [
        abs(error) <= GATE["within_sigmas"] * float(shot["uncertainty_m"])
        for error, shot in zip(errors, measured, strict=True)
    ]
    summary.update(
        {
            "tape_m": tape_m,
            "median_error_m": float(np.median(errors)) if errors else None,
            "median_abs_error_m": float(np.median(np.abs(errors))) if errors else None,
            "within_fraction": (sum(within) / len(within)) if within else 0.0,
        }
    )
    passed = (
        bool(errors)
        and summary["measured_fraction"] >= GATE["min_measured_fraction"]
        and summary["within_fraction"] >= GATE["min_within_fraction"]
        and summary["median_abs_error_m"] <= GATE["max_median_abs_error_m"]
    )
    summary["gate"] = "pass" if passed else "fail"
    return summary


def grade_sessions(paths: Sequence[Path], truth: Truth) -> dict:
    """Every shot of every session folder, graded against its tape where there is one."""
    sessions = []
    for path in paths:
        for run_dir in _run_dirs(Path(path)):
            shots, prior, net_m = session_shots(run_dir)
            graded = []
            for shot in shots:
                result = range_shot(shot, prior, net_m)
                tape = truth.tape_for(run_dir, shot.get("shot_number"))
                if tape is not None and result.get("range_m") is not None:
                    result["tape_m"] = tape
                    result["error_m"] = float(result["range_m"]) - tape
                graded.append(result)
            tape_m = truth.tape_for(run_dir)
            sessions.append(
                {
                    "name": _session_name(run_dir, Path(path)),
                    "run_dir": str(run_dir),
                    "tape_m": tape_m,
                    "prior": prior.to_dict() if prior is not None else None,
                    "shots": graded,
                    "summary": summarize(graded, tape_m=tape_m),
                }
            )
    passed = [item for item in sessions if item["summary"]["gate"] == "pass"]
    return {
        "schema": REPORT_SCHEMA,
        "tape_reference": truth.tape_reference,
        "validated_against_tape": False,
        # never "validated" from here: the owner confirms the gate at a taped session
        "statement": NOT_VALIDATED
        + (
            f" {len(passed)} taped session(s) here passed the PROVISIONAL gate; the owner "
            "must confirm the gate before the per-swing range is called validated."
            if passed
            else ""
        ),
        "sessions": sessions,
    }


def _session_name(run_dir: Path, given: Path) -> str:
    given = Path(given)
    return given.name if run_dir == given or given in run_dir.parents else run_dir.name


def format_report(report: Mapping) -> str:
    """A short text table of the grade."""
    lines = [report["statement"], ""]
    for session in report["sessions"]:
        summary = session["summary"]
        tape = f"{session['tape_m']:.3f} m" if session["tape_m"] is not None else "no tape"
        lines.append(f"{session['name']}  tape {tape}  gate {summary['gate']}")
        for shot in session["shots"]:
            if shot["status"] == "measured":
                error = f"  error {shot['error_m']:+.3f} m" if "error_m" in shot else ""
                lines.append(
                    f"  shot {shot['shot_number']}: {shot['range_m']:.3f} +/- "
                    f"{shot['uncertainty_m']:.3f} m ({shot['method']}){error}"
                )
            else:
                lines.append(f"  shot {shot['shot_number']}: not measured: {shot['reason']}")
        lines.append("")
    return "\n".join(lines)


__all__ = [
    "GATE",
    "NOT_VALIDATED",
    "REPORT_SCHEMA",
    "TRUTH_SCHEMA",
    "Truth",
    "format_report",
    "grade_sessions",
    "load_truth",
    "range_shot",
    "session_shots",
    "summarize",
]
