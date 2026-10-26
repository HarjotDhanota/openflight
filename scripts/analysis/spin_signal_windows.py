"""Duration ablation of saved envelopes; no synthetic extension or device writes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from scripts.analysis.spin_fusion_study import (
    FS,
    carrier_diagnostic,
    filtered_envelope,
    load_events,
    spectra,
    supporting_rank,
    write_csv,
)

LENGTHS = (20, 30, 45, 60, 80, 100)


def onset_sample(times, ratios, impact_ms):
    qualified = (ratios >= 12.0) & (times * 1000 >= impact_ms + 4.27)
    indices = np.flatnonzero(qualified)
    groups = np.split(indices, np.flatnonzero(np.diff(indices) != 1) + 1)
    first = next((group for group in groups if len(group) >= 12), None)
    return int(times[first[0]] * FS) if first is not None else None


def score_window(envelope):
    spectrum = spectra(envelope)
    rpm = spectrum["frequencies"] * 60
    valid = np.flatnonzero((rpm >= 1500) & (rpm <= 11000))
    pick = float(rpm[valid[np.argmax(spectrum["coherent"][valid])]])
    return pick, spectrum["candidates"]


def summarize(rows):
    results = []
    for dataset in ("all", "may", "oct"):
        for anchor in ("production_start", "carrier_onset"):
            selected = [
                r
                for r in rows
                if r["eligible"]
                and r["anchor"] == anchor
                and (dataset == "all" or r["dataset"] == dataset)
            ]
            available_at_60 = {
                r["shot"] for r in selected if r["requested_ms"] == 60 and r["available"]
            }
            for cohort, keys in (
                ("available_each_length", None),
                ("same_shots_available_60ms", available_at_60),
            ):
                for length in LENGTHS:
                    group = [
                        r
                        for r in selected
                        if r["requested_ms"] == length
                        and r["available"]
                        and (keys is None or r["shot"] in keys)
                    ]
                    errors = [abs(r["pick_rpm"] / r["truth_rpm"] - 1) * 100 for r in group]
                    results.append(
                        {
                            "dataset": dataset,
                            "anchor": anchor,
                            "cohort": cohort,
                            "requested_ms": length,
                            "n": len(group),
                            "median_abs_pct": float(np.median(errors)) if errors else None,
                            "mae_rpm": float(
                                np.mean([abs(r["pick_rpm"] - r["truth_rpm"]) for r in group])
                            )
                            if group
                            else None,
                            "within_10": float(np.mean([e <= 10 for e in errors]))
                            if errors
                            else None,
                            "recall_5_10": float(
                                np.mean(
                                    [r["rank_10"] is not None and r["rank_10"] <= 5 for r in group]
                                )
                            )
                            if group
                            else None,
                        }
                    )
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, required=True)
    parser.add_argument("--may-session", type=Path, required=True)
    parser.add_argument("--oct-session", type=Path, required=True)
    parser.add_argument("--current-session", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    study = json.loads(args.study.read_text(encoding="utf-8"))
    sources = {
        dataset: load_events(path)[0]
        for dataset, path in [
            ("may", args.may_session),
            ("oct", args.oct_session),
            ("current", args.current_session),
        ]
    }
    records, diagnostics = [], []
    for row in study["rows"]:
        capture = sources[row["dataset"]][row["number"]]
        iq, envelope, doppler, _ = filtered_envelope(capture, row["ball_speed_mph"])
        carrier = carrier_diagnostic(iq, doppler, row["impact_ms"])
        onset = onset_sample(carrier[1], carrier[3], row["impact_ms"])
        processor_start = round(row["window_start_ms"] * FS / 1000)
        processor_end = round(row["window_end_ms"] * FS / 1000)
        trigger_ms = (capture["trigger_time"] - capture["sample_time"]) * 1000
        diagnostics.append(
            {
                "shot": row["shot"],
                "dataset": row["dataset"],
                "trigger_ms": trigger_ms,
                "post_trigger_ms": len(iq) / FS * 1000 - trigger_ms,
                "carrier_onset_ms": onset / FS * 1000 if onset is not None else None,
                "carrier_to_end_ms": len(iq) / FS * 1000 - onset / FS * 1000
                if onset is not None
                else None,
                "production_window_ms": row["window_ms"],
                "carrier_clean_ms": row["carrier_clean_ms"],
                "radial_distance_clean_m": row["ball_speed_mph"]
                * 0.44704
                * row["carrier_clean_ms"]
                / 1000,
            }
        )
        for anchor, start in (("production_start", processor_start), ("carrier_onset", onset)):
            end_limit = processor_end if anchor == "production_start" else len(iq) - 42
            for length in LENGTHS:
                count = round(length * FS / 1000)
                available = start is not None and start + count <= end_limit
                pick, candidates = (
                    score_window(envelope[start : start + count]) if available else (None, [])
                )
                truth = row["reference_spin_rpm"]
                records.append(
                    {
                        "shot": row["shot"],
                        "dataset": row["dataset"],
                        "eligible": row["eligible"],
                        "anchor": anchor,
                        "requested_ms": length,
                        "available": available,
                        "start_ms": start / FS * 1000 if start is not None else None,
                        "truth_rpm": truth,
                        "pick_rpm": pick,
                        "rank_10": supporting_rank(candidates, truth, 0.1)
                        if truth and available
                        else None,
                    }
                )
        print(row["shot"], flush=True)
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    summary = summarize(records)
    write_csv(output / "signal_windows.csv", records)
    write_csv(output / "signal_lengths.csv", diagnostics)
    write_csv(output / "window_summary.csv", summary)
    (output / "windows.json").write_text(
        json.dumps({"summary": summary, "diagnostics": diagnostics}, indent=2) + "\n",
        encoding="utf-8",
    )
    fig, axes = plt.subplots(1, 2, figsize=(11, 4), layout="constrained")
    for dataset in ("may", "oct"):
        points = [
            r
            for r in summary
            if r["dataset"] == dataset
            and r["anchor"] == "carrier_onset"
            and r["cohort"] == "same_shots_available_60ms"
            and r["n"]
            and r["requested_ms"] <= 60
        ]
        axes[0].plot(
            [r["requested_ms"] for r in points],
            [r["median_abs_pct"] for r in points],
            "o-",
            label=dataset,
        )
        axes[1].plot(
            [r["requested_ms"] for r in points],
            [100 * r["recall_5_10"] for r in points],
            "o-",
            label=dataset,
        )
    axes[0].set(xlabel="Window length (ms)", ylabel="Median absolute error (%)")
    axes[1].set(xlabel="Window length (ms)", ylabel="Recall@5 within 10% (%)", ylim=(0, 100))
    for axis in axes:
        axis.legend()
        axis.grid(alpha=0.3)
    fig.suptitle("Same-shot cohort; carrier-onset anchor; reference only grades")
    fig.savefig(output / "signal-length.png", dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    main()
