#!/usr/bin/env python3
"""Why the camera's ball distance is off: fit the rig's unknowns to taped placements.

Each placement, recorded from the study page's live view, holds where the camera
found the ball, how wide the picture alone read it, and the taped distance. The
camera judges distance two ways, and each is wrong for its own reasons:

- from the ball's size, through the focal length: a focal length that is not
  what the lens says, or a ball read small because its shaded side merges with
  the ground, scale every placement by the same factor;
- from where the ball sits on the floor, through the lens height and the
  camera's tilt: a tilt, or a lens mounted off the sensor's centre, is a
  constant angle; a wrong lens height grows with distance; a camera rolled
  about its axis shifts the ball with its sideways position.

Fitting those against the tape says which it is, per camera mode.

    uv run python scripts/analysis/ball_range_check.py placements.jsonl
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy import optimize

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from openflight.rig_geometry import RigGeometry  # noqa: E402

BALL_DIAMETER_MM = 42.67
FOCAL_PX_2X = 466.6667
FOCAL_PX_1X = 933.3333


@dataclass(frozen=True)
class Placement:
    number: int
    width: int
    height: int
    x: float
    y: float
    diameter_px: float
    tape_mm: float  # lens to ball centre

    @property
    def focal(self) -> float:
        return FOCAL_PX_1X if self.width >= 1280 else FOCAL_PX_2X


def load(path: Path, rig: RigGeometry) -> list[Placement]:
    text = path.read_text(encoding="utf-8").strip()
    try:
        # the page's download is one document; the Pi's log is one record a line
        rows = json.loads(text)["placements"]
    except (json.JSONDecodeError, KeyError, TypeError):
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    offset = rig.iwr_offset_mm[2] if rig.iwr_offset_mm else 0.0
    placements = []
    for row in rows:
        ball = row.get("ball") or {}
        if not ball.get("found") or row.get("tee_mm") is None:
            continue
        placements.append(
            Placement(
                number=int(row["placement"]),
                width=int(row["arm"]["width"]),
                height=int(row["arm"]["height"]),
                x=float(ball["x"]),
                y=float(ball["y"]),
                diameter_px=float(ball.get("image_only_diameter_px") or ball["diameter_px"]),
                tape_mm=float(row["tee_mm"]) + offset,
            )
        )
    return placements


def fit_floor(group: list[Placement], drop_mm: float) -> dict:
    """Tilt (with any lens-centre offset), lens height and roll that best explain the rows."""
    free = ["tilt"] + (["drop"] if len(group) >= 3 else []) + (["roll"] if len(group) >= 4 else [])

    def unpack(params):
        values = dict(zip(free, params))
        return values["tilt"], values.get("drop", drop_mm), values.get("roll", 0.0)

    def predicted_rows(params):
        tilt, drop, roll = unpack(params)
        rows = []
        for p in group:
            cx, cy = p.width / 2.0, p.height / 2.0
            aside = p.tape_mm * (p.x - cx) / p.focal
            along = math.sqrt(max(p.tape_mm**2 - drop**2 - aside**2, 1.0))
            below = tilt + math.atan(drop / along)
            rows.append(cy + p.focal * math.tan(below) + (p.x - cx) * math.tan(roll))
        return np.array(rows)

    observed = np.array([p.y for p in group])
    start = {"tilt": math.radians(3.0), "drop": drop_mm, "roll": 0.0}
    result = optimize.least_squares(
        lambda params: predicted_rows(params) - observed, [start[name] for name in free]
    )
    tilt, drop, roll = unpack(result.x)
    residual_px = predicted_rows(result.x) - observed
    # a row error, in millimetres of distance along the floor at each placement
    residual_mm = [
        r * (p.tape_mm**2 + drop**2) / (drop * p.focal) for r, p in zip(residual_px, group)
    ]
    focal = group[0].focal
    return {
        "fitted": free,
        "tilt_down_deg": math.degrees(tilt),
        "lens_offset_equivalent_px": focal * math.tan(tilt),
        "drop_mm": drop,
        "roll_deg": math.degrees(roll),
        "residual_px": [round(float(r), 1) + 0.0 for r in residual_px],
        "residual_mm": [round(float(r)) + 0 for r in residual_mm],
    }


def _num(value: float, places: int = 2) -> str:
    return f"{round(float(value), places) + 0.0:.{places}f}"  # no -0.00


def report(placements: list[Placement], rig: RigGeometry) -> list[str]:
    lines = ["# Ball distance check", ""]
    if not placements:
        return lines + ["No placements with a found ball and a taped distance."]
    drop_mm = (rig.lens_height_above_floor_mm or 0.0) - BALL_DIAMETER_MM / 2.0
    modes: dict[tuple[int, int], list[Placement]] = {}
    for p in placements:
        modes.setdefault((p.width, p.height), []).append(p)
    for (width, height), group in sorted(modes.items()):
        lines += [f"## {width}x{height}: {len(group)} placements", ""]
        lines += [
            "| # | tape | from size | size off | ball px |",
            "| --- | --- | --- | --- | --- |",
        ]
        ratios = []
        for p in group:
            from_size = p.focal * BALL_DIAMETER_MM / p.diameter_px
            ratios.append(p.tape_mm / from_size)
            lines.append(
                f"| {p.number} | {p.tape_mm:.0f} | {from_size:.0f} | "
                f"{100 * (from_size / p.tape_mm - 1):+.0f}% | {p.diameter_px:.1f} |"
            )
        scale = float(np.median(ratios))
        lines += [
            "",
            f"Size: the picture reads the ball at {100 * scale:.0f}% of the width the tape "
            f"implies (spread {100 * min(ratios):.0f}-{100 * max(ratios):.0f}%). A constant "
            "factor is the focal length or a steady under-read of the shaded side; a "
            "factor that moves between placements is the detection.",
            "",
        ]
        if drop_mm <= 0:
            lines += ["Floor: the rig file has no lens height.", ""]
            continue
        floor = fit_floor(group, drop_mm)
        lines += [
            f"Floor: fitted {', '.join(floor['fitted'])} from {len(group)} placements.",
            f"- tilt down {_num(floor['tilt_down_deg'])} deg (rig file "
            f"{_num(-rig.boresight_pitch_deg)}), or the lens centre "
            f"{_num(floor['lens_offset_equivalent_px'], 0)} px low: one angle, not separable here",
            f"- lens {floor['drop_mm'] + BALL_DIAMETER_MM / 2:.0f} mm above the floor "
            f"(rig file {rig.lens_height_above_floor_mm:.0f})",
            f"- roll {_num(floor['roll_deg'])} deg",
            f"- rows left over: {floor['residual_px']} px, i.e. {floor['residual_mm']} mm of distance",
            "",
        ]
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("placements", type=Path)
    parser.add_argument(
        "--rig-geometry", type=Path, default=ROOT / "config" / "enclosure_v3_rig_geometry.json"
    )
    args = parser.parse_args(argv)
    rig = RigGeometry.from_json(args.rig_geometry)
    print("\n".join(report(load(args.placements, rig), rig)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
