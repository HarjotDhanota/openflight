"""Offline, truth-separated OPS spectrum recall study; never changes device state.

Run with --help. Inputs are local files; only whitelisted numeric fields reach
the output. TrackMan data grades candidate recall and never enters the spectrum.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.signal import butter, find_peaks, sosfiltfilt, stft

from openflight.clubs import ClubType
from openflight.raw_radar_replay import replay_ops_capture
from openflight.rolling_buffer.multitaper import (
    _dpss_tapers,
    _polynomial_design,
    _remove_multipath_fade,
    repair_clipped_iq,
)
from openflight.rolling_buffer.processor import RollingBufferProcessor

FS = 30_000
BAND = (800.0, 14_000.0)
OCT_PAIRS = (18, 19, 20, 23, 24, 25, 27, 31, 32, 33, 34, 35, 37, 38, 40, 41, 42, 43)
CLUBS = {club.value: club for club in ClubType}


@dataclass
class Evidence:
    dataset: str
    number: int
    capture: dict
    shot: dict
    truth: dict
    eligible: bool

    @property
    def key(self):
        return f"{self.dataset}-{self.number:02d}"


def number(value):
    if value in (None, ""):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def load_events(path):
    with path.open(encoding="utf-8-sig") as handle:
        events = [json.loads(line) for line in handle if line.strip()]
    captures, shots = {}, {}
    for event in events:
        if event.get("type") not in ("rolling_buffer_capture", "shot_detected", "shot_updated"):
            continue
        data = event.get("data", event)
        shot_number = data["shot_number"]
        if event["type"] == "rolling_buffer_capture":
            if shot_number in captures:
                raise ValueError("duplicate OPS capture identity")
            captures[shot_number] = event
        else:
            shots.setdefault(shot_number, {}).update(data)
    return captures, shots


def may_evidence(session, comparison, trackman):
    captures, shots = load_events(session)
    with comparison.open(encoding="utf-8-sig", newline="") as handle:
        pairs = list(csv.DictReader(handle))
    with trackman.open(encoding="utf-8-sig", newline="") as handle:
        raw = list(csv.reader(handle))
    header_index = next(i for i, row in enumerate(raw) if row and row[0] == "Date")
    export = [dict(zip(raw[header_index], row)) for row in raw[header_index + 2 :]]
    result = []
    used = set()
    for pair in pairs:
        shot_number = int(pair["shot_number_of"])
        matches = [
            (i, row)
            for i, row in enumerate(export, 1)
            if abs(float(row["Spin Rate"]) - float(pair["spin_tm"])) < 0.02
            and abs(float(row["Ball Speed"]) - float(pair["ball_speed_tm"])) < 0.002
        ]
        if len(matches) != 1 or matches[0][0] in used:
            raise ValueError("May export join is missing or ambiguous")
        tm_number, row = matches[0]
        used.add(tm_number)
        truth = {
            "tm_shot": tm_number,
            "club": pair["club"],
            "spin": number(row["Spin Rate"]),
            "spin_type": row["Spin Rate Type"],
            "ball_speed": number(row["Ball Speed"]),
            "launch": number(row["Launch Angle"]),
            "attack": number(row["Attack Angle"]),
            "path": number(row["Club Path"]),
            "dynamic_loft": number(row["Dyn. Loft"]),
            "club_speed": number(row["Club Speed"]),
            "face": number(row["Face Angle"]),
            "launch_of": number(pair["launch_v_of"]),
            "comparison_spin": number(pair["spin_of"]),
            "match_quality": pair["match_quality"],
        }
        eligible = pair["match_quality"] == "good" and row["Spin Rate Type"] == "Measured"
        result.append(
            Evidence("may", shot_number, captures[shot_number], shots[shot_number], truth, eligible)
        )
    return sorted(result, key=lambda item: item.number)


def oct_evidence(session, trackman):
    captures, shots = load_events(session)
    with trackman.open(encoding="utf-8-sig", newline="") as handle:
        rows = {
            int(row["Shot number"]): row
            for row in csv.DictReader(handle)
            if row["Shot number"].isdigit()
        }
    result = []
    for shot_number, tm_number in enumerate(OCT_PAIRS, 1):
        row, shot = rows[tm_number], shots[shot_number]
        tm_time = datetime.fromisoformat(row["Date"].replace("Z", "+00:00")).replace(tzinfo=None)
        shot_time = datetime.fromisoformat(shot["ts"]) + timedelta(hours=4)
        back, side = number(row["Backspin"]), number(row["Sidespin"])
        truth = {
            "tm_shot": tm_number,
            "club": {"Pitching Wedge": "pw", "6 Iron": "6-iron", "7 Iron": "7-iron"}.get(
                row["Club type"], row["Club type"]
            ),
            "spin": math.hypot(back, side),
            "backspin": back,
            "sidespin": side,
            "spin_type": "not specified by export",
            "ball_speed": number(row["Ball speed"]),
            "launch": number(row["Vertical Launch angle"]),
            "attack": number(row["Angle of attack"]),
            "path": number(row["Club path"]),
            "dynamic_loft": None,
            "club_speed": number(row["Club speed"]),
            "face": number(row["Face to target"]),
            "launch_of": number(shot.get("launch_angle_vertical")),
            "comparison_spin": number(shot.get("spin_rpm")),
            "match_quality": "owner supplied mapping; time/speed checked",
            "shot_report_delay_s": (shot_time - tm_time).total_seconds(),
            "mishit": float(row["Vertical Launch angle"]) < 15.0,
        }
        result.append(Evidence("oct", shot_number, captures[shot_number], shot, truth, True))
    return result


def filtered_envelope(capture, ball_speed):
    processor = RollingBufferProcessor(sample_rate=FS)
    iq, clipped = repair_clipped_iq(np.array(capture["i_samples"]), np.array(capture["q_samples"]))
    doppler = 2.0 * ball_speed / processor.MPS_TO_MPH / processor.WAVELENGTH_M
    sos = butter(
        4,
        [max(0.001, (doppler - 700) / (FS / 2)), min(0.999, (doppler + 700) / (FS / 2))],
        btype="band",
        output="sos",
    )
    filtered = sosfiltfilt(sos, iq)
    return iq, np.abs(filtered), doppler, clipped


def detrend(values):
    polynomial = _polynomial_design(len(values))
    return values - polynomial @ np.linalg.lstsq(polynomial, values, rcond=None)[0]


def local_candidates(frequencies, coherent, power):
    valid = np.flatnonzero((frequencies * 60 >= BAND[0]) & (frequencies * 60 <= BAND[1]))
    peaks = list(find_peaks(coherent[valid])[0])
    for index in (0, len(valid) - 1):
        neighbor = 1 if index == 0 else index - 1
        if coherent[valid[index]] > coherent[valid[neighbor]]:
            peaks.append(index)
    ranked = sorted((int(valid[i]) for i in peaks), key=lambda i: coherent[i], reverse=True)
    strongest = max((coherent[i] for i in ranked), default=1.0)
    floor = max(float(np.median(power[valid])), np.finfo(float).tiny)
    return [
        {
            "rank": rank,
            "rpm": float(frequencies[index] * 60),
            "relative_power": float(coherent[index] / strongest),
            "relative_amplitude": float(np.sqrt(coherent[index] / strongest)),
            "multitaper_peak_to_band_median": float(power[index] / floor),
            "snr_proxy_db": float(10 * np.log10(max(power[index] / floor, 1e-12))),
            "band_boundary": index in (valid[0], valid[-1]),
        }
        for rank, index in enumerate(ranked, 1)
    ]


def spectra(envelope):
    residual, fade = _remove_multipath_fade(envelope, FS, fade_band_hz=(25.0, 90.0))
    residual = detrend(residual)
    fft_size = max(8192, 1 << math.ceil(math.log2(len(envelope) * 8)))
    frequencies = np.fft.rfftfreq(fft_size, 1.0 / FS)
    coherent = abs(np.fft.rfft(residual * np.hanning(len(residual)), fft_size)) ** 2
    raw = abs(np.fft.rfft(detrend(envelope) * np.hanning(len(envelope)), fft_size)) ** 2
    tapers = _dpss_tapers(len(residual), 2.5, 4)
    power = np.mean([abs(np.fft.rfft(residual * taper, fft_size)) ** 2 for taper in tapers], axis=0)
    return {
        "frequencies": frequencies,
        "coherent": coherent,
        "power": power,
        "raw": raw,
        "fade_hz": fade,
        "candidates": local_candidates(frequencies, coherent, power),
        "raw_candidates": local_candidates(frequencies, raw, raw),
        "multitaper_candidates": local_candidates(frequencies, power, power),
    }


def supporting_rank(candidates, truth, tolerance):
    ranks = [c["rank"] for c in candidates if abs(c["rpm"] / truth - 1) <= tolerance]
    return min(ranks) if ranks else None


def classify(truth, pick, candidates):
    if abs(pick / truth - 1) <= 0.10:
        return "current_pick_ok"
    rank = supporting_rank(candidates, truth, 0.10)
    if rank is not None:
        return "another_candidate"
    if any(abs(pick * factor / truth - 1) <= 0.10 for factor in (2, 0.5, 3, 1 / 3)):
        return "harmonic_only"
    if not BAND[0] <= truth <= BAND[1]:
        return "truth_outside_band"
    return "no_supported_peak"


def carrier_diagnostic(iq, doppler, impact_ms):
    frequencies, times, z = stft(
        iq,
        FS,
        nperseg=256,
        noverlap=224,
        nfft=4096,
        return_onesided=False,
        boundary=None,
        padded=False,
    )
    power = abs(z) ** 2
    region = (frequencies >= doppler - 700) & (frequencies <= doppler + 700)
    near = (frequencies >= doppler - 160) & (frequencies <= doppler + 160)
    off = region & ~near
    ratio = np.max(power[near], axis=0) / np.maximum(
        np.median(power[off], axis=0), np.finfo(float).tiny
    )
    good = (ratio >= 12.0) & (times * 1000 >= impact_ms)
    indices = np.flatnonzero(good)
    groups = np.split(indices, np.flatnonzero(np.diff(indices) != 1) + 1)
    longest = max(groups, key=len)
    # Frame-centre span is conservative: do not count the overlapping window twice.
    clean_ms = float((times[longest[-1]] - times[longest[0]]) * 1000) if len(longest) > 1 else 0.0
    return frequencies, times, power, ratio, clean_ms


def plot_shot(item, row, spectrum, carrier, output):
    fig, axes = plt.subplots(2, 1, figsize=(11, 7), layout="constrained")
    freq = spectrum["frequencies"] * 60
    mask = (freq >= BAND[0]) & (freq <= BAND[1])
    for name, label, style in [
        ("raw", "Before fade removal", "--"),
        ("coherent", "Production residual / Hann", "-"),
        ("power", "Multitaper evidence", ":"),
    ]:
        power = spectrum[name]
        axes[0].plot(
            freq[mask],
            10 * np.log10(np.maximum(power[mask] / max(power[mask]), 1e-9)),
            style,
            label=label,
            alpha=0.85,
        )
    for candidate in spectrum["candidates"]:
        rpm = candidate["rpm"]
        axes[0].axvline(rpm, color="0.8", lw=0.6)
        axes[0].text(rpm, 2, str(candidate["rank"]), ha="center", fontsize=8)
    axes[0].axvline(row["ops_pick_rpm"], color="tab:orange", label="Current OPS pick")
    if item.truth:
        axes[0].axvline(item.truth["spin"], color="tab:green", label="Reference (grading only)")
    axes[0].set(
        xlabel="Equivalent spin (rpm)",
        ylabel="Power relative to each spectrum max (dB)",
        ylim=(-55, 6),
        xlim=BAND,
    )
    axes[0].legend(fontsize=8, ncol=2, loc="lower right")
    frequencies, times, power, _, _ = carrier
    region = (frequencies > 0) & (frequencies < 14000)
    image = axes[1].pcolormesh(
        times * 1000,
        frequencies[region],
        10 * np.log10(np.maximum(power[region], 1e-12)),
        shading="auto",
        cmap="magma",
        vmin=-10,
        vmax=45,
    )
    axes[1].axhline(row["ball_doppler_hz"], color="cyan", ls="--", lw=0.7)
    axes[1].axvline(row["impact_ms"], color="lime", label="OPS impact estimate")
    axes[1].axvline(row["window_start_ms"], color="cyan", label="Spin window start")
    axes[1].axvline(row["window_end_ms"], color="cyan", ls=":", label="Spin window end")
    axes[1].set(xlabel="Time from buffer start (ms)", ylabel="Outbound Doppler (Hz)")
    axes[1].legend(fontsize=8, ncol=3, loc="upper right")
    fig.colorbar(image, ax=axes[1], label="STFT power (dB, ADC units)")
    title = f"{item.key} | recorded club {row['recorded_club']} | {row['quality']} | window {row['window_ms']:.1f} ms"
    fig.suptitle(title)
    fig.savefig(output, dpi=130)
    plt.close(fig)


def analyse(item, output, make_plot=True):
    club = CLUBS.get(item.shot.get("club"), ClubType.UNKNOWN)
    replay = replay_ops_capture(item.capture, sample_rate_hz=FS, club_type=club)
    result = replay["result"]
    if result is None:
        raise ValueError(f"{item.key}: production processor did not detect ball")
    spin = result["spin"]
    iq, envelope, doppler, clipped = filtered_envelope(item.capture, result["ball_speed_mph"])
    processor = RollingBufferProcessor(sample_rate=FS)
    start = max(0, int(result["ball_timestamp_ms"] * FS / 1000))
    end = processor._ball_signal_end_sample(envelope, start)
    transient = int(FS / processor.SPIN_BANDPASS_BW_HZ)
    if end - start > 2 * transient + processor.SPIN_MIN_SAMPLES:
        start += transient
        end -= transient
    if end - start < processor.MULTITAPER_MIN_SAMPLES:
        raise ValueError(f"{item.key}: insufficient envelope samples")
    spectrum = spectra(envelope[start:end])
    impact = result.get("impact") or {}
    impact_ms = impact.get("timestamp_ms")
    if impact_ms is None:
        impact_ms = result["ball_timestamp_ms"]
    carrier = carrier_diagnostic(iq, doppler, impact_ms)
    bounded = (spectrum["frequencies"] * 60 >= 1500) & (spectrum["frequencies"] * 60 <= 11000)
    reproduced_spectrum_pick = float(
        spectrum["frequencies"][np.flatnonzero(bounded)[np.argmax(spectrum["coherent"][bounded])]]
        * 60
    )
    assert abs(reproduced_spectrum_pick - spin["spin_rpm"]) < 1e-8
    truth = item.truth.get("spin")
    row = {
        "shot": item.key,
        "number": item.number,
        "dataset": item.dataset,
        "recorded_club": item.shot.get("club"),
        "reference_club": item.truth.get("club"),
        "eligible": item.eligible,
        "reference_type": item.truth.get("spin_type"),
        "match_quality": item.truth.get("match_quality"),
        "tm_shot": item.truth.get("tm_shot"),
        "reference_spin_rpm": truth,
        "ops_pick_rpm": spin["spin_rpm"],
        "error_pct": 100 * (spin["spin_rpm"] / truth - 1) if truth else None,
        "confidence": spin["confidence"],
        "quality": spin["quality"],
        "snr_evidence": spin["snr"],
        "at_lower_rail": spin["at_lower_rail"],
        "at_upper_rail": spin["at_upper_rail"],
        "logged_spin_rpm": item.capture.get("spin_rpm"),
        "comparison_spin_rpm": item.truth.get("comparison_spin"),
        "logged_spin_difference_rpm": spin["spin_rpm"] - (item.capture.get("spin_rpm") or 0),
        "ball_speed_mph": result["ball_speed_mph"],
        "club_speed_mph": result["club_speed_mph"],
        "ball_doppler_hz": doppler,
        "impact_ms": impact_ms,
        "impact_source": impact.get("source"),
        "window_start_ms": start / FS * 1000,
        "window_end_ms": end / FS * 1000,
        "window_ms": (end - start) / FS * 1000,
        "buffer_samples": len(iq),
        "carrier_clean_ms": carrier[-1],
        "cycles_in_window": (end - start) / FS * truth / 60 if truth else None,
        "cycles_in_clean_carrier": carrier[-1] / 1000 * truth / 60 if truth else None,
        "clipped_fraction": clipped,
        "fade_hz": spectrum["fade_hz"],
        "candidate_count": len(spectrum["candidates"]),
        "rank_10": supporting_rank(spectrum["candidates"], truth, 0.10) if truth else None,
        "rank_20": supporting_rank(spectrum["candidates"], truth, 0.20) if truth else None,
        "raw_rank_10": supporting_rank(spectrum["raw_candidates"], truth, 0.10) if truth else None,
        "multitaper_rank_10": supporting_rank(spectrum["multitaper_candidates"], truth, 0.10)
        if truth
        else None,
        "failure_class": classify(truth, spin["spin_rpm"], spectrum["candidates"])
        if truth
        else None,
        "reference": item.truth,
        "capture_payload_sha256": replay["canonical_capture_payload_sha256"],
    }
    if make_plot:
        plot_shot(item, row, spectrum, carrier, output / "pictures" / f"{item.key}.png")
    return row, spectrum


def recall_summary(rows):
    eligible = [row for row in rows if row["eligible"]]
    results = []
    datasets = ["all", *sorted({row["dataset"] for row in eligible})]
    for dataset in datasets:
        subset = [row for row in eligible if dataset == "all" or row["dataset"] == dataset]
        for club in ["all", *sorted({row["reference_club"] for row in subset})]:
            group = [row for row in subset if club == "all" or row["reference_club"] == club]
            record = {"dataset": dataset, "club": club, "n": len(group)}
            for tolerance in (10, 20):
                for k in (1, 3, 5, "all"):
                    hits = sum(
                        row[f"rank_{tolerance}"] is not None
                        and (k == "all" or row[f"rank_{tolerance}"] <= k)
                        for row in group
                    )
                    record[f"recall_{tolerance}_top_{k}"] = hits / len(group)
            results.append(record)
    return results


def write_csv(path, rows):
    if not rows:
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--may-session", type=Path, default=Path("session_logs/session_20260506_152406_range.jsonl")
    )
    parser.add_argument(
        "--may-comparison", type=Path, default=Path("session_logs/comparison_20260506.csv")
    )
    parser.add_argument(
        "--may-trackman", type=Path, default=Path("session_logs/OpenFlight-Test.Normalized.csv")
    )
    parser.add_argument("--oct-session", type=Path)
    parser.add_argument("--oct-trackman", type=Path)
    parser.add_argument("--current-session", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    logging.basicConfig(level=logging.ERROR)
    output = args.output
    (output / "pictures").mkdir(parents=True, exist_ok=True)
    evidence = may_evidence(args.may_session, args.may_comparison, args.may_trackman)
    if args.oct_session:
        if not args.oct_trackman:
            parser.error("--oct-trackman is required with --oct-session")
        evidence += oct_evidence(args.oct_session, args.oct_trackman)
    if args.current_session:
        captures, shots = load_events(args.current_session)
        evidence += [
            Evidence("current", n, capture, shots[n], {}, False)
            for n, capture in sorted(captures.items())
        ]
    rows, candidates = [], []
    for item in evidence:
        row, spectrum = analyse(item, output)
        rows.append(row)
        for kind in ("candidates", "raw_candidates", "multitaper_candidates"):
            candidates.extend(
                {"shot": item.key, "spectrum": kind, **candidate} for candidate in spectrum[kind]
            )
        print(
            f"{item.key}: {row['ops_pick_rpm']:.0f} rpm; rank10={row['rank_10']}; {row['window_ms']:.1f} ms",
            flush=True,
        )
    summary = recall_summary(rows)
    sources = [
        args.may_session,
        args.may_comparison,
        args.may_trackman,
        args.oct_session,
        args.oct_trackman,
        args.current_session,
    ]
    manifest = [
        {"filename": p.name, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
        for p in sources
        if p
    ]
    (output / "study.json").write_text(
        json.dumps(
            {
                "rows": rows,
                "recall": summary,
                "inputs": manifest,
                "sample_rate_hz": FS,
                "widened_band_rpm": BAND,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    write_csv(
        output / "phase1.csv", [{k: v for k, v in row.items() if k != "reference"} for row in rows]
    )
    write_csv(output / "candidates.csv", candidates)
    write_csv(output / "recall.csv", summary)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
