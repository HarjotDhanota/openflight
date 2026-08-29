"""D-plane metrics for the 21-shot session, as a CONSISTENCY check only.

NO GROUND TRUTH EXISTS for any number this script prints. Nothing here is
validated against a launch monitor; every figure is the session's own launch
angles pushed through published ball-flight coefficients. Read the output as
"are these mutually consistent and physically plausible", never as "this is the
golfer's dynamic loft".

The model is the standard D-plane inversion with Wood et al. 7-iron
coefficients:

    vertical launch   V ~= A + k_v * (DL - A)   ->  DL = A + (V - A) / k_v
    horizontal launch H ~= P + k_h * (F  - P)   ->  F  = P + (H - P) / k_h

with A the attack angle, P the club path, DL the dynamic loft, F the face
angle, k_v = 0.81 and k_h = 0.69. Spin loft is DL - A and face-to-path is F - P.

Inputs come from `shots.csv`:
    V = launch_angle_vertical                       (radar)
    H = launch_angle_horizontal                     (camera-assisted or radar)
    A = experimental_fused_attack_angle_deg         (camera+radar fusion)
    P = experimental_fused_club_path_deg            (camera+radar fusion)

Run from the repository root:

    uv run python research/session_metrics/dplane_metrics.py \
        --session /path/to/export --out /path/to/output
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

# Wood et al. 7-iron transfer coefficients, plus the sensitivity bracket the
# brief asks for. The centre value is the one every headline number uses.
K_VERTICAL = (0.76, 0.81, 0.86)
K_HORIZONTAL = (0.66, 0.69, 0.72)
K_V_NOMINAL = 0.81
K_H_NOMINAL = 0.69

ACCEPTED_FUSED_STATUS = {"approach_high", "approach_mixed"}
EXCLUDED_SHOTS = {1}
# Attack angles this far from a golfer's plausible delivery are the fusion
# failing, not the swing. Both are reported, never silently dropped.
GROSS_ATTACK_LIMIT_DEG = 15.0
# The same, for club path. The brief only asks for the attack outliers to be
# split out, so these stay in every headline number -- but face angle is
# 1.449*H - 0.449*P, so a 15 deg path error moves face angle by 6.7 deg, and
# the supplementary block says what the session looks like without them.
GROSS_PATH_LIMIT_DEG = 12.0

SANITY = {
    "7-iron": {"dynamic_loft_deg": (25.0, 33.0)},
    "9-iron": {"dynamic_loft_deg": (33.0, 41.0)},
}
SPIN_LOFT_RANGE_DEG = (20.0, 30.0)
FACE_ANGLE_CENTRE_DEG = (0.0, 3.0)
FACE_ANGLE_SD_LIMIT_DEG = 4.0


def _float(value: str | None) -> float | None:
    try:
        parsed = float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None
    return parsed if parsed is not None and math.isfinite(parsed) else None


def _session_path(value: str | None, parser: argparse.ArgumentParser) -> Path:
    supplied = value or os.environ.get("OPENFLIGHT_SESSION")
    if not supplied:
        parser.error("provide --session or set OPENFLIGHT_SESSION")
    session = Path(supplied).expanduser().resolve()
    if not (session / "shots.csv").is_file():
        parser.error(f"session export is missing shots.csv: {session}")
    return session


def dplane(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    vertical_deg: float,
    horizontal_deg: float,
    attack_deg: float,
    path_deg: float,
    k_v: float = K_V_NOMINAL,
    k_h: float = K_H_NOMINAL,
) -> dict[str, float]:
    """Invert the launch angles into club-delivery angles.

    Args:
        vertical_deg: Vertical launch angle.
        horizontal_deg: Horizontal launch angle, positive right of target.
        attack_deg: Attack angle, negative for a descending blow.
        path_deg: Club path, positive right of target.
        k_v: Vertical launch fraction of dynamic loft above attack angle.
        k_h: Horizontal launch fraction of face angle away from path.

    Returns:
        Dynamic loft, spin loft, face angle and face-to-path, all in degrees.

    Raises:
        ValueError: If either coefficient is outside (0, 1].
    """
    if not 0.0 < k_v <= 1.0 or not 0.0 < k_h <= 1.0:
        raise ValueError("launch coefficients must lie in (0, 1]")
    dynamic_loft = attack_deg + (vertical_deg - attack_deg) / k_v
    face_angle = path_deg + (horizontal_deg - path_deg) / k_h
    return {
        "dynamic_loft_deg": dynamic_loft,
        "spin_loft_deg": dynamic_loft - attack_deg,
        "face_angle_deg": face_angle,
        "face_to_path_deg": face_angle - path_deg,
    }


def _rows(session: Path) -> list[dict[str, str]]:
    with (session / "shots.csv").open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _record(row: dict[str, str]) -> dict[str, Any] | None:
    number = int(row["shot_number"])
    if number in EXCLUDED_SHOTS:
        return None
    status = row.get("experimental_fused_status", "")
    vertical = _float(row.get("launch_angle_vertical"))
    horizontal = _float(row.get("launch_angle_horizontal"))
    attack = _float(row.get("experimental_fused_attack_angle_deg"))
    path = _float(row.get("experimental_fused_club_path_deg"))
    if status not in ACCEPTED_FUSED_STATUS or None in (vertical, horizontal, attack, path):
        return {
            "shot_number": number,
            "club": row.get("club", ""),
            "usable": False,
            "reason": f"status={status or 'missing'}",
        }
    record: dict[str, Any] = {
        "shot_number": number,
        "club": row.get("club", ""),
        "usable": True,
        "reason": "ok",
        "gross_attack_outlier": abs(attack) > GROSS_ATTACK_LIMIT_DEG,
        "gross_path_outlier": abs(path) > GROSS_PATH_LIMIT_DEG,
        "launch_vertical_deg": vertical,
        "launch_horizontal_deg": horizontal,
        "attack_angle_deg": attack,
        "club_path_deg": path,
        "club_speed_mph": _float(row.get("club_speed_mph")),
        "ball_speed_mph": _float(row.get("ball_speed_mph")),
        "spin_rpm": _float(row.get("spin_rpm")),
        "horizontal_source": row.get("launch_angle_horizontal_source", ""),
    }
    for k_v in K_VERTICAL:
        for k_h in K_HORIZONTAL:
            suffix = f"kv{k_v:.2f}_kh{k_h:.2f}"
            for name, value in dplane(vertical, horizontal, attack, path, k_v, k_h).items():
                record[f"{name}__{suffix}"] = value
    nominal = f"kv{K_V_NOMINAL:.2f}_kh{K_H_NOMINAL:.2f}"
    for name in ("dynamic_loft_deg", "spin_loft_deg", "face_angle_deg", "face_to_path_deg"):
        record[name] = record[f"{name}__{nominal}"]
    return record


def _summary(values: np.ndarray) -> dict[str, float | int]:
    if values.size == 0:
        return {"n": 0, "median": math.nan, "mean": math.nan, "sd": math.nan, "iqr": math.nan}
    return {
        "n": int(values.size),
        "median": float(np.median(values)),
        "mean": float(np.mean(values)),
        "sd": float(np.std(values, ddof=1)) if values.size > 1 else 0.0,
        "iqr": float(np.percentile(values, 75) - np.percentile(values, 25)),
        "min": float(values.min()),
        "max": float(values.max()),
    }


def _check(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"check": name, "result": "PASS" if passed else "FAIL", "detail": detail}


def _sanity(clean: list[dict[str, Any]]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for club, limits in SANITY.items():
        low, high = limits["dynamic_loft_deg"]
        values = np.asarray([r["dynamic_loft_deg"] for r in clean if r["club"] == club])
        if values.size == 0:
            continue
        median = float(np.median(values))
        checks.append(
            _check(
                f"{club} dynamic loft median in {low:.0f}-{high:.0f} deg",
                low <= median <= high,
                f"median {median:.1f} deg (n={values.size})",
            )
        )
    spin = np.asarray([r["spin_loft_deg"] for r in clean])
    median_spin = float(np.median(spin))
    checks.append(
        _check(
            f"spin loft median in {SPIN_LOFT_RANGE_DEG[0]:.0f}-{SPIN_LOFT_RANGE_DEG[1]:.0f} deg",
            SPIN_LOFT_RANGE_DEG[0] <= median_spin <= SPIN_LOFT_RANGE_DEG[1],
            f"median {median_spin:.1f} deg",
        )
    )
    face = np.asarray([r["face_angle_deg"] for r in clean])
    face_sd = float(np.std(face, ddof=1))
    face_mean = float(np.mean(face))
    checks.append(
        _check(
            f"face angle sd < {FACE_ANGLE_SD_LIMIT_DEG:.0f} deg (one golfer, one session)",
            face_sd < FACE_ANGLE_SD_LIMIT_DEG,
            f"sd {face_sd:.1f} deg",
        )
    )
    checks.append(
        _check(
            "face angle mean in 0..+3 deg",
            FACE_ANGLE_CENTRE_DEG[0] <= face_mean <= FACE_ANGLE_CENTRE_DEG[1],
            f"mean {face_mean:.1f} deg; a mean far from this is the known camera-vs-radar "
            f"horizontal offset entering as delta/{K_H_NOMINAL}",
        )
    )
    seven = np.asarray([r["dynamic_loft_deg"] for r in clean if r["club"] == "7-iron"])
    nine = np.asarray([r["dynamic_loft_deg"] for r in clean if r["club"] == "9-iron"])
    if seven.size and nine.size:
        gap = float(np.median(nine) - np.median(seven))
        checks.append(
            _check(
                "9i minus 7i dynamic-loft gap ~8 deg (corroborates the launch-gap finding)",
                6.0 <= gap <= 10.0,
                f"gap {gap:+.1f} deg",
            )
        )
    return checks


def implied_offsets(clean: list[dict[str, Any]], target_deg: float = 1.5) -> dict[str, float]:
    """What single sensor offset would centre the face angle on ``target_deg``.

    ``F = H/k_h - P*(1-k_h)/k_h``, so a constant bias in the camera's horizontal
    launch angle enters the face angle amplified by ``1/k_h``, and a constant
    bias in the radar's club path enters it attenuated by ``(1-k_h)/k_h``. The
    two numbers below are the SAME discrepancy read off either instrument; which
    one is actually biased this cannot say.
    """
    face = np.asarray([r["face_angle_deg"] for r in clean])
    gap = target_deg - float(np.mean(face))
    return {
        "face_angle_mean_deg": float(np.mean(face)),
        "target_deg": target_deg,
        "implied_horizontal_launch_offset_deg": gap * K_H_NOMINAL,
        "implied_club_path_offset_deg": -gap * K_H_NOMINAL / (1.0 - K_H_NOMINAL),
    }


def track_variant(clean: list[dict[str, Any]], track_csv: Path) -> dict[str, Any]:
    """The same inversion with A and P taken from `swing_plane.py` instead.

    The fused attack angle and club path are one estimate of the delivery. The
    3-D head track is another, built from different arithmetic on the same two
    sensors. Running the D-plane on both says whether the implausible dynamic
    lofts come from the LAUNCH angles or from the DELIVERY angles.
    """
    with track_csv.open(newline="", encoding="utf-8") as handle:
        track = {
            int(row["shot_number"]): row
            for row in csv.DictReader(handle)
            if row.get("status") == "ok"
        }
    rows: list[dict[str, Any]] = []
    for record in clean:
        source = track.get(record["shot_number"])
        if source is None:
            continue
        attack = _float(source.get("track_attack_angle_deg"))
        path = _float(source.get("track_club_path_deg"))
        if attack is None or path is None:
            continue
        row = {
            "shot_number": record["shot_number"],
            "club": record["club"],
            "attack_angle_deg": attack,
            "club_path_deg": path,
            **dplane(record["launch_vertical_deg"], record["launch_horizontal_deg"], attack, path),
        }
        rows.append(row)
    return {
        "n": len(rows),
        "per_club": {
            club: {
                name: _summary(np.asarray([r[name] for r in rows if r["club"] == club]))
                for name in (
                    "attack_angle_deg",
                    "club_path_deg",
                    "dynamic_loft_deg",
                    "spin_loft_deg",
                    "face_angle_deg",
                    "face_to_path_deg",
                )
            }
            for club in sorted({r["club"] for r in rows})
        },
    }


def _sensitivity(clean: list[dict[str, Any]]) -> list[dict[str, Any]]:
    table: list[dict[str, Any]] = []
    for k_v in K_VERTICAL:
        for k_h in K_HORIZONTAL:
            suffix = f"kv{k_v:.2f}_kh{k_h:.2f}"
            for club in ("7-iron", "9-iron"):
                subset = [r for r in clean if r["club"] == club]
                if not subset:
                    continue
                row = {"club": club, "k_v": k_v, "k_h": k_h, "n": len(subset)}
                for name in (
                    "dynamic_loft_deg",
                    "spin_loft_deg",
                    "face_angle_deg",
                    "face_to_path_deg",
                ):
                    values = np.asarray([r[f"{name}__{suffix}"] for r in subset])
                    row[f"{name}_median"] = float(np.median(values))
                    row[f"{name}_sd"] = float(np.std(values, ddof=1)) if values.size > 1 else 0.0
                table.append(row)
    return table


def _plots(clean: list[dict[str, Any]], outliers: list[dict[str, Any]], out: Path) -> list[Path]:
    colours = {"7-iron": "#1f77b4", "9-iron": "#d62728"}
    written: list[Path] = []

    figure, axes = plt.subplots(1, 2, figsize=(11.5, 5.0))
    for club, colour in colours.items():
        subset = [r for r in clean if r["club"] == club]
        if not subset:
            continue
        axes[0].scatter(
            [r["launch_vertical_deg"] for r in subset],
            [r["dynamic_loft_deg"] for r in subset],
            c=colour,
            label=f"{club} (n={len(subset)})",
            s=42,
        )
        axes[1].scatter(
            [r["club_path_deg"] for r in subset],
            [r["face_angle_deg"] for r in subset],
            c=colour,
            label=f"{club} (n={len(subset)})",
            s=42,
        )
    for record in outliers:
        axes[0].scatter(
            [record["launch_vertical_deg"]],
            [record["dynamic_loft_deg"]],
            facecolors="none",
            edgecolors="black",
            s=90,
            label="gross attack outlier" if record is outliers[0] else None,
        )
    for record in clean + outliers:
        axes[0].annotate(
            str(record["shot_number"]),
            (record["launch_vertical_deg"], record["dynamic_loft_deg"]),
            fontsize=6,
            xytext=(3, 3),
            textcoords="offset points",
        )
        if record in clean:
            axes[1].annotate(
                str(record["shot_number"]),
                (record["club_path_deg"], record["face_angle_deg"]),
                fontsize=6,
                xytext=(3, 3),
                textcoords="offset points",
            )
    axes[0].set_xlabel("vertical launch V (deg)")
    axes[0].set_ylabel("dynamic loft DL (deg)")
    axes[0].set_title(f"DL vs V   (k_v={K_V_NOMINAL})")
    axes[1].plot([-20, 12], [-20, 12], color="0.6", lw=1, ls="--", label="face = path")
    axes[1].axhline(0.0, color="0.85", lw=1)
    axes[1].axvline(0.0, color="0.85", lw=1)
    axes[1].set_xlabel("club path P (deg, + = right of target)")
    axes[1].set_ylabel("face angle F (deg)")
    axes[1].set_title(f"F vs P   (k_h={K_H_NOMINAL})")
    for axis in axes:
        axis.grid(alpha=0.25)
        axis.legend(fontsize=8)
    figure.suptitle("D-plane inversion - CONSISTENCY ONLY, no ground truth exists", fontsize=11)
    figure.tight_layout()
    path = out / "dplane_dl_vs_v_and_f_vs_p.png"
    figure.savefig(path, dpi=140)
    plt.close(figure)
    written.append(path)

    figure, axis = plt.subplots(figsize=(7.5, 4.6))
    for k_v in K_VERTICAL:
        suffix_values = []
        labels = []
        for club in ("7-iron", "9-iron"):
            subset = [r for r in clean if r["club"] == club]
            if not subset:
                continue
            values = [r[f"dynamic_loft_deg__kv{k_v:.2f}_kh{K_H_NOMINAL:.2f}"] for r in subset]
            suffix_values.append(float(np.median(values)))
            labels.append(club)
        axis.plot(labels, suffix_values, marker="o", label=f"k_v={k_v}")
    for index, (club, limits) in enumerate(SANITY.items()):
        low, high = limits["dynamic_loft_deg"]
        axis.bar(
            index,
            high - low,
            bottom=low,
            width=0.55,
            color="0.87",
            zorder=0,
            label="plausible band" if index == 0 else None,
        )
        axis.annotate(
            f"{club} {low:.0f}-{high:.0f}",
            (index, high),
            ha="center",
            va="bottom",
            fontsize=8,
            color="0.35",
        )
    axis.set_ylabel("median dynamic loft (deg)")
    axis.set_title("Coefficient sensitivity - shaded = plausible band")
    axis.grid(alpha=0.25)
    axis.legend(fontsize=8)
    figure.tight_layout()
    path = out / "dplane_coefficient_sensitivity.png"
    figure.savefig(path, dpi=140)
    plt.close(figure)
    written.append(path)
    return written


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    keys: list[str] = []
    for row in rows:
        for key in row:
            if key not in keys:
                keys.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    """Invert every usable shot and report the D-plane tables."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", default=None)
    parser.add_argument("--out", default=None)
    parser.add_argument("--copy-plots-to", default=None)
    parser.add_argument(
        "--track-csv",
        default=None,
        help="swing_plane_per_shot.csv, to re-run the inversion on the 3-D track's own "
        "attack angle and club path",
    )
    args = parser.parse_args()
    session = _session_path(args.session, parser)
    out = Path(args.out).expanduser() if args.out else Path(tempfile.mkdtemp(prefix="dplane_"))
    out.mkdir(parents=True, exist_ok=True)

    records = [record for record in (_record(row) for row in _rows(session)) if record is not None]
    usable = [r for r in records if r["usable"]]
    outliers = [r for r in usable if r["gross_attack_outlier"]]
    clean = [r for r in usable if not r["gross_attack_outlier"]]

    _write_csv(out / "dplane_per_shot.csv", records)
    sensitivity = _sensitivity(clean)
    _write_csv(out / "dplane_coefficient_sensitivity.csv", sensitivity)

    per_club: dict[str, Any] = {}
    for club in sorted({r["club"] for r in clean}):
        subset = [r for r in clean if r["club"] == club]
        per_club[club] = {
            name: _summary(np.asarray([r[name] for r in subset]))
            for name in (
                "launch_vertical_deg",
                "launch_horizontal_deg",
                "attack_angle_deg",
                "club_path_deg",
                "dynamic_loft_deg",
                "spin_loft_deg",
                "face_angle_deg",
                "face_to_path_deg",
            )
        }
    tight = [r for r in clean if not r["gross_path_outlier"]]
    supplementary = {
        "excluded_club_path_outliers": [
            {"shot": r["shot_number"], "club_path_deg": r["club_path_deg"]}
            for r in clean
            if r["gross_path_outlier"]
        ],
        "per_club": {
            club: {
                name: _summary(np.asarray([r[name] for r in tight if r["club"] == club]))
                for name in (
                    "dynamic_loft_deg",
                    "spin_loft_deg",
                    "face_angle_deg",
                    "face_to_path_deg",
                    "club_path_deg",
                )
            }
            for club in sorted({r["club"] for r in tight})
        },
    }
    checks = _sanity(clean)
    offsets = implied_offsets(clean)
    track = track_variant(clean, Path(args.track_csv).expanduser()) if args.track_csv else None
    plots = _plots(clean, outliers, out)
    if args.copy_plots_to:
        destination = Path(args.copy_plots_to).expanduser()
        destination.mkdir(parents=True, exist_ok=True)
        for plot in plots:
            (destination / plot.name).write_bytes(plot.read_bytes())

    report = {
        "caveat": (
            "CONSISTENCY ONLY. No ground truth exists for any figure here; nothing is "
            "compared against a launch monitor."
        ),
        "session": str(session),
        "model": {"k_v": K_V_NOMINAL, "k_h": K_H_NOMINAL, "source": "Wood et al. 7-iron"},
        "counts": {
            "records": len(records),
            "usable": len(usable),
            "clean": len(clean),
            "gross_attack_outliers": [r["shot_number"] for r in outliers],
        },
        "per_club": per_club,
        "checks": checks,
        "implied_offsets": offsets,
        "supplementary_without_club_path_outliers": supplementary,
        "track_angle_variant": track,
    }
    (out / "dplane_summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"session: {session}")
    print(report["caveat"])
    print(
        f"records {len(records)}  usable {len(usable)}  clean {len(clean)}  "
        f"gross attack outliers {[r['shot_number'] for r in outliers]}"
    )
    for club, block in per_club.items():
        print(f"\n{club} (n={block['dynamic_loft_deg']['n']})")
        for name, stats in block.items():
            print(
                f"  {name:24s} median {stats['median']:7.2f}  mean {stats['mean']:7.2f}  "
                f"sd {stats['sd']:5.2f}  iqr {stats['iqr']:5.2f}  "
                f"[{stats['min']:7.2f}, {stats['max']:7.2f}]"
            )
    if outliers:
        print("\ngross attack outliers, reported separately:")
        for record in outliers:
            print(
                f"  shot {record['shot_number']:3d} {record['club']:7s} "
                f"A={record['attack_angle_deg']:+7.2f} V={record['launch_vertical_deg']:6.2f} "
                f"DL={record['dynamic_loft_deg']:7.2f} SL={record['spin_loft_deg']:7.2f}"
            )
    print(
        f"\nface angle mean {offsets['face_angle_mean_deg']:+.2f} deg; centring it on "
        f"{offsets['target_deg']:+.1f} deg needs either "
        f"{offsets['implied_horizontal_launch_offset_deg']:+.2f} deg on H or "
        f"{offsets['implied_club_path_offset_deg']:+.2f} deg on P"
    )
    if supplementary["excluded_club_path_outliers"]:
        print(
            "\nsupplementary, dropping the club-path outliers "
            + str([o["shot"] for o in supplementary["excluded_club_path_outliers"]])
        )
        for club, block in supplementary["per_club"].items():
            print(
                f"  {club:7s} n={block['face_angle_deg']['n']:2d} "
                f"DL {block['dynamic_loft_deg']['median']:6.2f}  "
                f"SL {block['spin_loft_deg']['median']:6.2f}  "
                f"F {block['face_angle_deg']['median']:+6.2f} "
                f"(sd {block['face_angle_deg']['sd']:.2f})  "
                f"F-P {block['face_to_path_deg']['median']:+6.2f}"
            )
    if track:
        print(f"\nsame inversion with A and P from the 3-D head track (n={track['n']})")
        for club, block in track["per_club"].items():
            print(
                f"  {club:7s} n={block['dynamic_loft_deg']['n']:2d} "
                f"A {block['attack_angle_deg']['median']:+6.2f}  "
                f"P {block['club_path_deg']['median']:+6.2f}  "
                f"DL {block['dynamic_loft_deg']['median']:6.2f}  "
                f"SL {block['spin_loft_deg']['median']:6.2f}  "
                f"F {block['face_angle_deg']['median']:+6.2f} "
                f"(sd {block['face_angle_deg']['sd']:.2f})  "
                f"F-P {block['face_to_path_deg']['median']:+6.2f}"
            )
    print("\nsanity checks")
    for check in checks:
        print(f"  [{check['result']}] {check['check']}: {check['detail']}")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
