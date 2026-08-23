"""Run the Phase 1 Appendix-B grid and write RESULTS_0C_RADAR.md.

Usage:
    uv run --group research python -m silhouette_poc.eval.run_budget_0c_radar \
        --n 96 --seed 0 [--output research/silhouette_poc/eval]
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from .budget_radar import build_grid, run_cell

# Spec section 1 gates (impact vector = offset+height combined, median).
PASS_MM = {"driver": 10.0, "iron": 12.0}


def _fmt(x: float) -> str:
    return "nan" if not math.isfinite(float(x)) else f"{float(x):.2f}"


def _buildable(cell: dict) -> bool:
    """Buildable today = as-shipped exposure, or the strobed proposed mode."""
    shipped = cell["preset_px_per_mm"] in (0.656, 1.31) and cell["exposure_us"] == 500.0
    proposed = cell["preset_px_per_mm"] == 1.33 and cell["exposure_us"] == 10.0
    return (shipped or proposed) and cell["depth_label"] != "stereo_ref_3mm"


def render(cells: list[dict], n: int, seed: int) -> str:
    lines = [
        "# Phase 1 results: 0C budget at the real camera/radar (pre-registered grid)",
        "",
        f"Run: `python -m silhouette_poc.eval.run_budget_0c_radar --n {n} --seed {seed}`.",
        "Marker-keypoint fitting is an optimistic proxy for silhouette fitting:",
        "failing cells fail for the real system; passing cells are necessary, not sufficient.",
        "",
        "| club | px/mm | sync | depth | exp us | ok | impact mm med | offset mm | height mm | face deg | gate |",
        "|---|---:|---|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for c in sorted(
        cells,
        key=lambda c: (
            c["club"],
            -c["preset_px_per_mm"],
            c["sync_label"],
            c["depth_label"],
            c["exposure_us"],
        ),
    ):
        impact = c["impact_err_mm_median"]
        gate = PASS_MM[c["club"]]
        verdict = (
            "PASS" if math.isfinite(impact) and impact <= gate and c["ok_rate"] >= 0.8 else "fail"
        )
        tag = " (buildable)" if _buildable(c) else ""
        lines.append(
            f"| {c['club']} | {c['preset_px_per_mm']} | {c['sync_label']} | {c['depth_label']}"
            f" | {c['exposure_us']:.0f} | {c['ok_rate']:.2f} | {_fmt(impact)} | {_fmt(c['offset_err_mm_median'])}"
            f" | {_fmt(c['height_err_mm_median'])} | {_fmt(c['face_err_deg_median'])} | {verdict}{tag} |"
        )

    buildable_pass = [
        c
        for c in cells
        if _buildable(c)
        and math.isfinite(c["impact_err_mm_median"])
        and c["impact_err_mm_median"] <= PASS_MM[c["club"]]
        and c["ok_rate"] >= 0.8
    ]
    lines += ["", "## Gate verdict", ""]
    if buildable_pass:
        best = min(buildable_pass, key=lambda c: c["impact_err_mm_median"])
        lines.append(
            f"**GO** — {len(buildable_pass)} buildable cell(s) pass the spec gate. Best: "
            f"{best['club']} @ {best['preset_px_per_mm']} px/mm, {best['sync_label']}, "
            f"{best['depth_label']}, {best['exposure_us']:.0f} us -> "
            f"{best['impact_err_mm_median']:.2f} mm median impact vector."
        )
    else:
        lines.append(
            "**NO-GO as architected** — no buildable cell passes the spec section 1 gate. "
            "Stop before building Studio; report per spec section 9."
        )
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n", type=int, default=96)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()

    grid = build_grid(n=args.n, seed=args.seed)
    cells = []
    for i, spec in enumerate(grid, 1):
        cells.append(run_cell(spec))
        print(
            f"[{i}/{len(grid)}] {spec['club']} {spec['preset_px_per_mm']} px/mm "
            f"{spec['sync_label']} {spec['depth_label']} {spec['exposure_us']:.0f}us",
            flush=True,
        )

    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "results_0c_radar.json").write_text(
        json.dumps(cells, indent=1), encoding="utf-8"
    )
    md = render(cells, args.n, args.seed)
    (args.output / "RESULTS_0C_RADAR.md").write_text(md, encoding="utf-8")
    print(md.split("## Gate verdict")[-1])


if __name__ == "__main__":
    main()
