#!/usr/bin/env python3
"""Score the camera mode study against docs/camera/mode-study-analysis.md.

Reads one or more session exports (one per arm, from export_session.py),
computes the per-shot metrics the plan names, aggregates them per arm and light
bin, tests the pre-registered hypotheses that the data can decide, applies the
decision rule, and writes mode_study.json and mode_study.md. Nothing here is a
measurement of accuracy: every figure is the rig's own data read back.

    uv run python scripts/analysis/score_mode_study.py --export out/arm1 out/arm2 out/arm3 out/arm4 --out study/
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from openflight.camera.club_motion import detect_reference_ball  # noqa: E402

ACCEPTED_STATUSES = frozenset({"ok", "fused", "chained_high", "approach_high"})
REFERENCE_ARM = "arm1"
MIN_CELL_ACCEPTED = 3
# Motion mask: the extractor's own rule, |diff| > max(4 sigma, 18).
MOTION_SIGMA_MULT = 4.0
MOTION_FLOOR = 18
MIN_HEAD_AREA_PX = 60


@dataclass
class ShotMetrics:
    shot_number: int
    accepted: bool
    fused_status: str | None
    delivered_fps: float | None
    gap_count: int | None
    exposure_us: float | None
    gain: float | None
    resolved_mode: dict | None
    ball_detected: bool
    ball_diameter_px: float | None
    ball_diameter_jitter_px: float | None
    ball_clipped_pct: float | None
    ball_peak_dn: float | None
    ball_edge_gradient: float | None
    pre_impact_head_frames: int | None
    club_path_deg: float | None
    attack_angle_deg: float | None


@dataclass
class ArmScore:
    arm_id: str
    label: str
    width: int | None
    fps: float | None
    light_index: float | None
    light_bin: str
    attempted: int
    accepted: int
    availability: float | None
    status_histogram: dict
    insufficient: bool
    medians: dict = field(default_factory=dict)
    mads: dict = field(default_factory=dict)


def _median(values):
    clean = [
        float(v) for v in values if v is not None and not (isinstance(v, float) and math.isnan(v))
    ]
    return statistics.median(clean) if clean else None


def _mad(values):
    clean = [float(v) for v in values if v is not None]
    if len(clean) < 2:
        return None
    med = statistics.median(clean)
    return statistics.median(abs(v - med) for v in clean)


def _float(value):
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def light_bin(light_index: float | None) -> str:
    """Coarse octave bins on the camera light index, so testers pool by light."""
    if light_index is None or light_index <= 0:
        return "unknown"
    return f"2^{math.floor(math.log2(light_index))}"


def ball_metrics(frames: np.ndarray, pre_trigger: int) -> dict:
    """Resting-ball quality from the pre-swing frames; None by name when absent."""
    n = max(1, min(pre_trigger, len(frames)))
    quiet = frames[: max(1, n // 2)]
    out = {
        "ball_detected": False,
        "ball_diameter_px": None,
        "ball_diameter_jitter_px": None,
        "ball_clipped_pct": None,
        "ball_peak_dn": None,
        "ball_edge_gradient": None,
    }
    try:
        ball = detect_reference_ball(quiet)
    except (ValueError, RuntimeError):
        return out
    out["ball_detected"] = True
    out["ball_diameter_px"] = float(ball.diameter_px)
    # jitter: the detector medians a stack and needs three frames, so measure it
    # on short sub-stacks spread through the quiet window rather than single frames
    if len(quiet) >= 6:
        starts = np.linspace(0, len(quiet) - 3, num=min(5, len(quiet) // 3), dtype=int)
        diameters = []
        for start in starts:
            try:
                diameters.append(float(detect_reference_ball(quiet[start : start + 3]).diameter_px))
            except (ValueError, RuntimeError):
                continue
        if len(diameters) >= 2:
            out["ball_diameter_jitter_px"] = float(np.std(diameters))
    background = np.median(quiet, axis=0)
    yy, xx = np.mgrid[0 : background.shape[0], 0 : background.shape[1]]
    r = max(ball.diameter_px / 2.0, 1.0)
    dist = np.hypot(xx - ball.x, yy - ball.y)
    zone = dist <= 1.5 * r
    if zone.any():
        out["ball_clipped_pct"] = float(np.mean(background[zone] >= 250) * 100.0)
        out["ball_peak_dn"] = float(background[zone].max())
    gy, gx = np.gradient(background.astype(np.float32))
    annulus = (dist >= 0.7 * r) & (dist <= 1.3 * r)
    if annulus.any():
        out["ball_edge_gradient"] = float(np.hypot(gx, gy)[annulus].mean())
    return out


def pre_impact_head_frames(
    frames: np.ndarray, pre_trigger: int, ball_xy: tuple[float, float] | None
) -> int | None:
    """Frames before the trigger with a moving blob near the ball, by the extractor's own mask rule.

    An approximation of clubhead observability: it counts frames, not the head's
    quality. It is labelled as such in the report.
    """
    if pre_trigger < 4 or len(frames) < pre_trigger:
        return None
    quiet = frames[: max(1, pre_trigger // 2)].astype(np.float32)
    background = np.median(quiet, axis=0)
    sigma = float(np.std(quiet - background))
    threshold = max(MOTION_SIGMA_MULT * sigma, MOTION_FLOOR)
    if ball_xy is not None:
        yy, xx = np.mgrid[0 : frames.shape[1], 0 : frames.shape[2]]
        near = np.hypot(xx - ball_xy[0], yy - ball_xy[1]) <= 0.45 * max(frames.shape[1:])
    else:
        near = np.ones(frames.shape[1:], dtype=bool)
    count = 0
    for frame in frames[max(0, pre_trigger - 12) : pre_trigger]:
        moving = (np.abs(frame.astype(np.float32) - background) > threshold) & near
        if int(moving.sum()) >= MIN_HEAD_AREA_PX:
            count += 1
    return count


def score_shot(export: Path, row: dict) -> ShotMetrics:
    shot_dir = export / row["dir"]
    metadata = json.loads((shot_dir / "camera_metadata.json").read_text(encoding="utf-8"))
    with np.load(shot_dir / "frames.npz") as bundle:
        frames = np.asarray(bundle["frames"])
        exposure = _median(bundle["exposure_us"]) if "exposure_us" in bundle else None
        gain = _median(bundle["analogue_gain"]) if "analogue_gain" in bundle else None
        pre_trigger = (
            int(bundle["pre_trigger_count"])
            if "pre_trigger_count" in bundle
            else int(metadata.get("pre_trigger_frames") or len(frames) // 2)
        )
    ball = ball_metrics(frames, pre_trigger)
    ball_xy = None
    if ball["ball_detected"]:
        try:
            found = detect_reference_ball(frames[: max(1, pre_trigger // 2)])
            ball_xy = (float(found.x), float(found.y))
        except (ValueError, RuntimeError):
            ball_xy = None
    status = row.get("fused_status") or None
    return ShotMetrics(
        shot_number=int(row["shot_number"]),
        accepted=status in ACCEPTED_STATUSES,
        fused_status=status,
        delivered_fps=_float(metadata.get("delivered_fps")),
        gap_count=int(metadata.get("gap_count"))
        if metadata.get("gap_count") not in (None, "")
        else None,
        exposure_us=exposure,
        gain=gain,
        resolved_mode=metadata.get("resolved"),
        pre_impact_head_frames=pre_impact_head_frames(frames, pre_trigger, ball_xy),
        club_path_deg=_float(row.get("experimental_fused_club_path_deg")),
        attack_angle_deg=_float(row.get("experimental_fused_attack_angle_deg")),
        **ball,
    )


def score_export(export: Path) -> tuple[ArmScore, list[ShotMetrics]]:
    manifest = json.loads((export / "manifest.json").read_text(encoding="utf-8"))
    arm = manifest.get("arm") or {}
    with (export / "shots.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    shots = [score_shot(export, row) for row in rows]
    excluded = len(manifest.get("excluded_shots") or [])
    attempted = len(shots) + excluded
    accepted = sum(1 for s in shots if s.accepted)
    histogram = Counter(s.fused_status or "unscored" for s in shots)
    for entry in manifest.get("excluded_shots") or []:
        histogram["excluded:" + (entry.get("reasons") or ["?"])[0].split(" ")[0]] += 1
    light = (manifest.get("environment") or {}).get("light_index")
    score = ArmScore(
        arm_id=arm.get("arm_id") or export.name,
        label=arm.get("label") or export.name,
        width=arm.get("width"),
        fps=arm.get("fps"),
        light_index=light,
        light_bin=light_bin(light),
        attempted=attempted,
        accepted=accepted,
        availability=(accepted / attempted) if attempted else None,
        status_histogram=dict(histogram),
        insufficient=accepted < MIN_CELL_ACCEPTED,
    )
    for key in (
        "delivered_fps",
        "gap_count",
        "exposure_us",
        "gain",
        "ball_diameter_px",
        "ball_diameter_jitter_px",
        "ball_clipped_pct",
        "ball_peak_dn",
        "ball_edge_gradient",
        "pre_impact_head_frames",
        "club_path_deg",
        "attack_angle_deg",
    ):
        values = [getattr(s, key) for s in shots]
        score.medians[key] = _median(values)
        score.mads[key] = _mad(values)
    return score, shots


def test_hypotheses(arms: dict[str, ArmScore]) -> dict:
    """The hypotheses the exported data can decide; each names its verdict and evidence."""
    out: dict[str, dict] = {}
    ref = arms.get(REFERENCE_ARM)
    # H1: ball diameter equal across 2x arms and 2x in 1:1 arms
    diam = {a: s.medians.get("ball_diameter_px") for a, s in arms.items()}
    two_x = [d for a, d in diam.items() if d and arms[a].width and arms[a].width < 1280]
    one_x = [d for a, d in diam.items() if d and arms[a].width and arms[a].width >= 1280]
    if two_x and one_x:
        ratio = statistics.median(one_x) / statistics.median(two_x)
        out["H1"] = {
            "verdict": "pass" if 1.8 <= ratio <= 2.2 else "FAIL",
            "evidence": f"1:1 / 2x ball diameter ratio = {ratio:.2f} (expect ~2.0)",
        }
    elif len(two_x) >= 2:
        spread = max(two_x) / min(two_x)
        out["H1"] = {
            "verdict": "pass" if spread <= 1.1 else "FAIL",
            "evidence": f"2x arms ball diameter spread = {spread:.2f} (expect ~1.0)",
        }
    else:
        out["H1"] = {"verdict": "undecided", "evidence": "fewer than two arms with a detected ball"}
    # H4: 1:1 availability vs light
    ones = {a: s for a, s in arms.items() if s.width and s.width >= 1280}
    if ones:
        parts = [
            f"{a}: availability {s.availability:.2f} at light {s.light_index}"
            for a, s in ones.items()
            if s.availability is not None
        ]
        out["H4"] = {"verdict": "reported", "evidence": "; ".join(parts) or "no 1:1 arm scored"}
    # H5: frame rate vs pre-impact head frames (arm1 vs arm2)
    a1, a2 = arms.get("arm1"), arms.get("arm2")
    if (
        a1
        and a2
        and a1.medians.get("pre_impact_head_frames") is not None
        and a2.medians.get("pre_impact_head_frames") is not None
    ):
        h1, h2 = a1.medians["pre_impact_head_frames"], a2.medians["pre_impact_head_frames"]
        out["H5"] = {
            "verdict": "pass" if h1 >= h2 else "FAIL",
            "evidence": f"pre-impact head frames (approx) arm1 {h1:.1f} vs arm2 {h2:.1f}",
        }
    # H6: availability is binding
    if ref and ref.availability is not None:
        losers = [
            a
            for a, s in arms.items()
            if a != REFERENCE_ARM
            and s.availability is not None
            and s.light_bin == ref.light_bin
            and s.availability < ref.availability
        ]
        out["H6"] = {
            "verdict": "reported",
            "evidence": f"arms below the reference's availability at its light bin: {losers or 'none'}",
        }
    return out


def decide(arms: dict[str, ArmScore]) -> dict:
    """The decision rule per arm against the reference, at the same light bin."""
    ref = arms.get(REFERENCE_ARM)
    verdicts: dict[str, dict] = {}
    for arm_id, s in arms.items():
        if arm_id == REFERENCE_ARM:
            verdicts[arm_id] = {"preferred": None, "reason": "reference"}
            continue
        if ref is None or s.insufficient or ref.insufficient:
            verdicts[arm_id] = {
                "preferred": False,
                "reason": "insufficient accepted swings to compare",
            }
            continue
        if s.light_bin != ref.light_bin:
            verdicts[arm_id] = {
                "preferred": False,
                "reason": f"different light bin ({s.light_bin} vs {ref.light_bin}); compare across testers",
            }
            continue
        reasons = []
        if s.availability < ref.availability:
            reasons.append("availability below reference")
        for key in ("ball_diameter_jitter_px", "ball_clipped_pct"):
            a, b = s.medians.get(key), ref.medians.get(key)
            if a is not None and b is not None and a > b:
                reasons.append(f"{key} worse than reference")
        improves = []
        if (s.medians.get("pre_impact_head_frames") or 0) > (
            ref.medians.get("pre_impact_head_frames") or 0
        ):
            improves.append("more pre-impact head frames")
        if (s.medians.get("ball_edge_gradient") or 0) > (
            ref.medians.get("ball_edge_gradient") or 0
        ):
            improves.append("sharper ball edge")
        for key in ("club_path_deg", "attack_angle_deg"):
            a, b = s.mads.get(key), ref.mads.get(key)
            if a is not None and b is not None and a < b:
                improves.append(f"tighter {key}")
        if not improves:
            reasons.append("no delivery endpoint improved")
        verdicts[arm_id] = {
            "preferred": not reasons,
            "reason": "; ".join(reasons) or "; ".join(improves),
        }
    return verdicts


def write_markdown(out: Path, arms: dict[str, ArmScore], hyps: dict, verdicts: dict) -> None:
    lines = [
        "# Mode study — scored",
        "",
        "Consistency only: the rig's own data read back. Not accuracy.",
        "",
    ]
    lines.append(
        "| arm | mode | light bin | attempted | accepted | availability | ball px | jitter px | clipped % | edge grad | head frames (approx) | path MAD | AoA MAD | verdict |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")

    def f(v, d=2):
        return "—" if v is None else (f"{v:.{d}f}" if isinstance(v, float) else str(v))

    for arm_id, s in arms.items():
        v = verdicts.get(arm_id, {})
        verdict = (
            "reference"
            if v.get("preferred") is None
            else ("PREFERRED" if v.get("preferred") else "not preferred")
        )
        lines.append(
            "| "
            + " | ".join(
                [
                    arm_id,
                    s.label,
                    s.light_bin,
                    str(s.attempted),
                    str(s.accepted),
                    f(s.availability),
                    f(s.medians.get("ball_diameter_px"), 1),
                    f(s.medians.get("ball_diameter_jitter_px")),
                    f(s.medians.get("ball_clipped_pct")),
                    f(s.medians.get("ball_edge_gradient"), 1),
                    f(s.medians.get("pre_impact_head_frames"), 0),
                    f(s.mads.get("club_path_deg")),
                    f(s.mads.get("attack_angle_deg")),
                    verdict + (" (insufficient)" if s.insufficient else ""),
                ]
            )
            + " |"
        )
    lines += ["", "## Status histogram", ""]
    for arm_id, s in arms.items():
        lines.append(
            f"- **{arm_id}**: "
            + ", ".join(f"{k} {n}" for k, n in sorted(s.status_histogram.items()))
        )
    lines += ["", "## Hypotheses", ""]
    for name, h in hyps.items():
        lines.append(f"- **{name}** — {h['verdict']}: {h['evidence']}")
    lines += ["", "## Decision", ""]
    for arm_id, v in verdicts.items():
        lines.append(f"- **{arm_id}** — {v['reason']}")
    lines += [
        "",
        "Head-frame counts are an approximation from a motion mask, not a clubhead detector; blur in mm and local contrast are not computed here.",
    ]
    (out / "mode_study.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--export", type=Path, nargs="+", required=True, help="Exported arm directories"
    )
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)

    arms: dict[str, ArmScore] = {}
    per_shot: dict[str, list[dict]] = defaultdict(list)
    for export in args.export:
        score, shots = score_export(export)
        arms[score.arm_id] = score
        per_shot[score.arm_id] = [asdict(s) for s in shots]
    hyps = test_hypotheses(arms)
    verdicts = decide(arms)
    (args.out / "mode_study.json").write_text(
        json.dumps(
            {
                "arms": {k: asdict(v) for k, v in arms.items()},
                "shots": per_shot,
                "hypotheses": hyps,
                "decision": verdicts,
            },
            indent=2,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )
    write_markdown(args.out, arms, hyps, verdicts)
    print((args.out / "mode_study.md").read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
