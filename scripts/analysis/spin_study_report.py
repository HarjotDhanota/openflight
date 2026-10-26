"""Build the local spin study report and contact sheets from saved study tables."""

from __future__ import annotations

import argparse
import collections
import json
import statistics
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps


def table(headers, rows):
    return (
        "\n".join(
            [
                "| " + " | ".join(headers) + " |",
                "|" + "---|" * len(headers),
                *["| " + " | ".join(map(str, row)) + " |" for row in rows],
            ]
        )
        + "\n"
    )


def percent(value):
    return f"{100 * value:.1f}%"


def stats(rows):
    errors = [r["ops_pick_rpm"] - r["reference_spin_rpm"] for r in rows]
    relative = [100 * e / r["reference_spin_rpm"] for e, r in zip(errors, rows)]
    return {
        "n": len(rows),
        "mae_rpm": statistics.mean(abs(e) for e in errors),
        "median_abs_pct": statistics.median(abs(e) for e in relative),
        "median_signed_pct": statistics.median(relative),
        "sample_sd_pct": statistics.stdev(relative) if len(relative) > 1 else None,
        "gross_error_rate": sum(abs(e) > 30 for e in relative) / len(relative),
    }


def sheets(folder, rows):
    output = folder / "sheets"
    output.mkdir(exist_ok=True)
    for dataset in ("may", "oct", "current"):
        group = [r for r in rows if r["dataset"] == dataset]
        for start in range(0, len(group), 6):
            sheet = Image.new("RGB", (1430, 1395), "white")
            ImageDraw.Draw(sheet).text(
                (15, 8), f"{dataset}: recorded spectra; reference only grades", fill="black"
            )
            for index, row in enumerate(group[start : start + 6]):
                with Image.open(folder / "pictures" / f"{row['shot']}.png") as image:
                    sheet.paste(
                        ImageOps.contain(image, (715, 455)),
                        ((index % 2) * 715, 30 + (index // 2) * 455),
                    )
            sheet.save(output / f"{dataset}-{start // 6 + 1:02d}.jpg", quality=93)


def build_report(folder):
    study = json.loads((folder / "study.json").read_text(encoding="utf-8"))
    windows = json.loads((folder / "windows.json").read_text(encoding="utf-8"))
    rows = study["rows"]
    eligible = [r for r in rows if r["eligible"]]
    if collections.Counter(r["dataset"] for r in rows) != {"may": 24, "oct": 18, "current": 33}:
        raise ValueError("this dated report requires the three specified source sessions")
    if collections.Counter(r["dataset"] for r in eligible) != {"may": 20, "oct": 18}:
        raise ValueError(
            "reference eligibility changed; review the dated report before regenerating"
        )
    for dataset, expected_hits in (("may", 12), ("oct", 6)):
        hits = sum(
            r["rank_10"] is not None and r["rank_10"] <= 5
            for r in eligible
            if r["dataset"] == dataset
        )
        if hits != expected_hits:
            raise ValueError(
                "gate result changed; review the dated recommendation before regenerating"
            )
    baseline = []
    for dataset in ("all", "may", "oct"):
        group = [r for r in eligible if dataset == "all" or r["dataset"] == dataset]
        for club in ("all", *sorted({r["reference_club"] for r in group})):
            subset = [r for r in group if club == "all" or r["reference_club"] == club]
            baseline.append({"dataset": dataset, "club": club, **stats(subset)})
    alternatives = []
    for dataset in ("all", "may", "oct"):
        group = [r for r in eligible if dataset == "all" or r["dataset"] == dataset]
        for key, label in [
            ("rank_10", "Production residual/Hann"),
            ("raw_rank_10", "Polynomial detrend only/Hann"),
            ("multitaper_rank_10", "Residual/multitaper power"),
        ]:
            alternatives.append(
                {
                    "dataset": dataset,
                    "spectrum": label,
                    "n": len(group),
                    "hits": sum(r[key] is not None and r[key] <= 5 for r in group),
                }
            )
    pairing = []
    for row in rows:
        if row["dataset"] != "oct":
            continue
        truth = row["reference"]
        pairing.append(
            {
                "shot": row["shot"],
                "tm_shot": truth["tm_shot"],
                "report_delay_s": truth["shot_report_delay_s"],
                "radial_speed_difference_pct": 100
                * (row["ball_speed_mph"] / truth["ball_speed"] - 1),
                "mishit": truth["mishit"],
            }
        )
    sensitivity = []
    selections = {
        "May all 24 including estimated/mismatch": [r for r in rows if r["dataset"] == "may"],
        "May good pairs including estimated": [
            r for r in rows if r["dataset"] == "may" and r["match_quality"] == "good"
        ],
        "October excluding supplied mishits": [
            r for r in eligible if r["dataset"] == "oct" and not r["reference"]["mishit"]
        ],
        "October excluding >10% radial-speed disagreement": [
            r
            for r in eligible
            if r["dataset"] == "oct"
            and abs(r["ball_speed_mph"] / r["reference"]["ball_speed"] - 1) <= 0.10
        ],
    }
    for name, group in selections.items():
        sensitivity.append(
            {
                "selection": name,
                "n": len(group),
                "recall_5_10": sum(r["rank_10"] is not None and r["rank_10"] <= 5 for r in group)
                / len(group),
            }
        )
    failure_classes = dict(collections.Counter(r["failure_class"] for r in eligible))
    harmonic_oracle = sum(
        r["rank_10"] is not None
        or any(
            abs(r["ops_pick_rpm"] * factor / r["reference_spin_rpm"] - 1) <= 0.10
            for factor in (1, 2, 0.5, 3, 1 / 3)
        )
        for r in eligible
    )
    derived = {
        "baseline": baseline,
        "alternatives": alternatives,
        "oct_pairing": pairing,
        "sensitivity": sensitivity,
        "failure_classes": failure_classes,
        "oracle_with_all_peaks_current_pick_and_harmonic_interpretations": {
            "hits": harmonic_oracle,
            "n": len(eligible),
            "recall": harmonic_oracle / len(eligible),
        },
    }
    (folder / "report_numbers.json").write_text(
        json.dumps(derived, indent=2) + "\n", encoding="utf-8"
    )
    sheets(folder, rows)
    lines = [
        "# Can OPS243 spin be rescued by fusion?",
        "",
        "25 October 2026. Offline research; production unchanged.",
        "",
        "## 1. Answer first",
        "",
        "**No trustworthy fusion spin is demonstrated by these captures. Do not build picker C or D yet.** "
        "The true spin is within ±10% of a top-five supported peak in **12/20 May pairs (60%)** and "
        "**6/18 October pairs (33.3%)**, or **18/38 pooled (47.4%)**. Even allowing every local peak "
        "does not improve those totals. This fails the task's approximately 70% gate, so Phase 3 was skipped. "
        "The limit here is the spin information recoverable by this envelope pipeline, not merely which of "
        "its peaks is selected. This does **not** prove that every possible analysis of the original complex I/Q "
        "is incapable of measuring spin. Longer observed windows do not establish a capture setting that "
        "makes this pipeline trustworthy. Keep radar candidates experimental; collect a controlled paired "
        "capture experiment before further picker work.",
        "",
        "**Number labels:** source I/Q and reference exports are recorded measurements; replay outputs, "
        "errors, recalls, timings inferred from samples, cycles and distances below are **derived**. "
        "Thresholds, proposed shot counts and proposed capture settings are **assumed/design choices**, "
        "not measured accuracy. No fitted spin formula, product change, device write or hardware test was made.",
        "",
        "## 2. Data and pairing",
        "",
        "- **May:** 24 raw captures joined by explicit shot number to the supplied comparison. Its TrackMan "
        "shot-number and timestamp columns are blank, so delivery rows were joined uniquely by the comparison's "
        "rounded spin (tolerance 0.02 rpm) and ball speed (0.002 mph) to the normalized export. Every match is "
        "unique. There are 22 comparison rows marked good; shots 9 and 13 are speed mismatches. Reference spins "
        "10 and 17 are explicitly **Estimated**. The primary May denominator is therefore 20 good, measured-spin pairs. "
        "All 24 remain in the table; no exclusions were selected from spin error.",
        "- **October:** the supplied 18-pair mapping was used exactly. Clubs are three PW, ten 7-iron and "
        "five 6-iron, not all PW. Reference total spin is derived as hypot(backspin, sidespin); this matters "
        "for the low-launch shots, including negative backspin. The CSV has no measured/estimated spin flag "
        "and no dynamic loft, so reference quality and perfect-delivery model inputs are incomplete. "
        "The main denominator retains all 18 supplied associations. The supplied mishits are 3, 9, 12, 14, 17, 18. "
        "Shots 14 and 17 have large radar/reference speed disagreements; see sensitivity results below. "
        "Shot-report timestamps are checked after EDT-to-UTC conversion, but are not impact truth. "
        "Their delays include software processing and two timing clusters; a single exact three-second offset "
        "would be an unjustified pairing rule.",
        "- **Current hardware:** 33 captures from 24 October were processed only for signal resemblance and "
        "replay consistency. There is no paired spin truth; no accuracy figure is inferred from them.",
        "- **Additional search:** a brief local census inspected 31 JSONL files across the pilot session logs, "
        "field handoffs and bench archive, plus the allowed TrackMan CSV folder. No further paired raw session "
        "was established. The public October package also has one accepted capture in an earlier session at "
        "09:50 EDT, before its reference export begins at 10:25 EDT; it was not guessed into a pair. "
        "The 4 October CSV exists, but its matching raw OPS session was not found in these locations. "
        "Unsearched owner storage on a Mac or Pi may hold it; that is an open question, not a file-location claim.",
        "",
        "Public source: [October evidence release](https://github.com/Robinante/OpenFlight-Radar-Workbench/releases/tag/trackman_session_10_12_26). "
        "Quote: “Full OPS I/Q is available for accepted shots; rejected events contain diagnostics only.” "
        "The downloaded asset is 661,454,793 bytes (release metadata and local size), rather than the prompt's "
        "approximate 880 MB. It was extracted with PowerShell Expand-Archive. Raw files remain outside Git. "
        "Input hashes are in [study.json](results/study.json). Only numeric shot/reference fields are exported; "
        "profiles, paths and network identifiers are omitted.",
        "",
        "October pairing diagnostics (derived; OPS speed is radial, reference speed is total):",
        "",
        table(
            ["OPS shot", "Reference shot", "Report delay s", "Speed difference %", "Mishit"],
            [
                [
                    r["shot"],
                    r["tm_shot"],
                    f"{r['report_delay_s']:.3f}",
                    f"{r['radial_speed_difference_pct']:+.1f}",
                    r["mishit"],
                ]
                for r in pairing
            ],
        ),
        "## 3. Phase 1: reproduce the current processor",
        "",
        "The script calls production `raw_radar_replay.replay_ops_capture`, which calls `process_capture` "
        "with the recorded selected club and the generic-prior callback. It separately reconstructs the exact "
        "filter, clipping repair, window, fade removal and spectrum and asserts every reconstructed bounded "
        "pick equals production to numerical precision.",
        "",
        "**Code/prompt discrepancy:** the current `detect_spin_multitaper` explicitly discards the expected "
        "spin. It selects the strongest **Hann coherent spectrum** bin after polynomial/two-tone fade removal. "
        "Four DPSS tapers (time-bandwidth 2.5) supply the evidence ratio, not the pick. `_build_spin_candidates`, "
        "prior thresholds, phase witnesses and older rail suppression belong to the legacy `detect_spin` path. "
        "The current path logs one candidate and keeps rail picks experimental. Changing its generic prior alone "
        "cannot change the live result. The spin chain is in `server._resolve_spin`, rather than "
        "`camera/fallback_chain.py`; its route order remains accepted OPS, collision model, then experimental OPS.",
        "",
        "**Logged consistency:** all 18 October and all 33 current-hardware picks match their capture logs "
        "within 0.5 rpm. None of the 24 May picks matches its old capture output; May recorded an older gated "
        "envelope/SNR result, often zero/null. Its exact source revision and full processing configuration "
        "are not recorded, so historical equivalence cannot be established. Only five comparison `spin_of` cells are populated. "
        "These historical nulls are not failures to reproduce today's different estimator. Both historical "
        "fields are preserved in [phase1.csv](results/phase1.csv); the table below scores today's processor.",
        "",
        table(
            [
                "Shot / plot",
                "Reference club",
                "Reference rpm",
                "Current OPS rpm",
                "Error %",
                "Evidence confidence",
                "Quality",
                "Primary?",
            ],
            [
                [
                    f"[{r['shot']}](results/pictures/{r['shot']}.png)",
                    r["reference_club"],
                    f"{r['reference_spin_rpm']:.0f}",
                    f"{r['ops_pick_rpm']:.0f}",
                    f"{r['error_pct']:+.1f}",
                    f"{r['confidence']:.3f}",
                    r["quality"],
                    "yes" if r["eligible"] else "excluded",
                ]
                for r in rows
                if r["dataset"] != "current"
            ],
        ),
        "Baseline performance on the primary pairs (derived):",
        "",
        table(
            ["Dataset", "Club", "n", "MAE rpm", "Median abs %", "Gross error >30%"],
            [
                [
                    r["dataset"],
                    r["club"],
                    r["n"],
                    f"{r['mae_rpm']:.0f}",
                    f"{r['median_abs_pct']:.1f}%",
                    percent(r["gross_error_rate"]),
                ]
                for r in baseline
            ],
        ),
        "The confidence column is a capped spectral evidence score, **not a calibrated probability of correct spin**. "
        "The prompt's historical bias/SD are not treated as a reproduced statistic of this changed denominator; "
        "current signed bias and sample SD are available in [report_numbers.json](results/report_numbers.json).",
        "",
        "## 4. Phase 2: candidate ceiling",
        "",
        "Candidate extraction never sees reference spin. The widened band is 800–14,000 rpm. Every local "
        "maximum is retained, without a top-N or SNR cut; a bounded monotone edge is retained but explicitly "
        "marked as a boundary rather than evidence of a resolved tone. Rank is by coherent power. The full "
        "[candidate table](results/candidates.csv) includes relative power, relative amplitude and multitaper "
        "power divided by the widened band's median, plus its dB value. This is an **SNR proxy**, not a "
        "calibrated signal/noise measurement: broad peaks contaminate that floor. The production SNR evidence "
        "uses a different exclusion region and is separately retained in the shot table.",
        "",
        "Recall table (derived; each four-number cell is top-1 / top-3 / top-5 / all peaks):",
        "",
    ]
    recall_rows = []
    for row in study["recall"]:
        recall_rows.append(
            [
                row["dataset"],
                row["club"],
                row["n"],
                *[
                    " / ".join(percent(row[f"recall_{t}_top_{k}"]) for k in (1, 3, 5, "all"))
                    for t in (10, 20)
                ],
            ]
        )
    lines.append(table(["Dataset", "Club", "n", "Recall ±10%", "Recall ±20%"], recall_rows))
    lines += [
        "Alternative spectra, recall@5 within ±10% (derived; same pairs, no tuning):",
        "",
        table(
            ["Dataset", "Spectrum", "Hits/n", "Recall"],
            [
                [r["dataset"], r["spectrum"], f"{r['hits']}/{r['n']}", percent(r["hits"] / r["n"])]
                for r in alternatives
            ],
        ),
        "Eligibility sensitivity (derived; does not replace the primary denominator):",
        "",
        table(
            ["Selection", "n", "Recall@5 ±10%"],
            [[r["selection"], r["n"], percent(r["recall_5_10"])] for r in sensitivity],
        ),
        "Exclusive failure classes, precedence current-pick OK → another candidate → harmonic-only → "
        "truth outside widened band → no supported peak (derived):",
        "",
        table(["Class", "n"], sorted(failure_classes.items())),
        "Harmonic-only uses ×2, ×½, ×3 or ×⅓ within ±10%. These are diagnostic coincidences, not extra "
        "supported fundamental peaks. Old-band rail flags are retained independently in phase1.csv. "
        "There are three upper-rail picks among all May captures, four among October's 18, and six "
        "among the unpaired current captures. Reference May 16 (1,573 rpm) is near the old lower rail; "
        "October 12 (893 rpm) is below the old search band but inside the widened one. No primary "
        "reference is outside the widened band. Rail flags are separate from the exclusive classes. "
        "A current bounded pick may be within tolerance despite not being a genuine local maximum in the "
        "wider spectrum; that is why current-pick accuracy and widened top-1 recall need not agree.",
        "",
        "**Visual review:** every paired and unpaired plot was viewed in this round using all 13 contact "
        "sheets. Each full-size image above includes the envelope spectra, every residual candidate/rank, "
        "current pick and reference, plus the ball-return spectrogram. Many peaks are broad lobes rather "
        "than narrow, separable spin lines. May driver shots lack a nearby fundamental after fade removal; "
        "May 15 has one before removal that disappears afterward. This makes fade removal a potential "
        "bias, not proof that the original I/Q contains no spin. October and current rail cases often "
        "have very short selected windows; some windows start at a late strongest return rather than "
        "the beginning of ball flight. Some selected signals appear near club/body or net returns. "
        "Those identities are visual hypotheses, not independently marked timing truth.",
        "",
        f"A further truth-assisted diagnostic allowing every peak, the current pick and its ×2/½/3/⅓ "
        f"interpretations reaches {harmonic_oracle}/{len(eligible)} "
        f"({percent(harmonic_oracle / len(eligible))}) within ±10%. This also falls below the pooled gate "
        "and is not a deployable picker or independent harmonic witness.",
        "",
        "Contact sheets: "
        + ", ".join(
            f"[{p.stem}](results/sheets/{p.name})"
            for p in sorted((folder / "sheets").glob("*.jpg"))
        ),
        "",
        "**Gate result:** failed on both independent dates and pooled. Even a truth-assisted oracle choosing "
        "among all supported residual peaks only reaches 47.4% within ±10%. There is no justification for "
        "presenting an interpolated physics-prior answer as recovered radar spin.",
        "",
        "## 5. Phase 3: not run",
        "",
        "Skipped under the requested gate. No B/C/D picker, posterior interval or cross-validation result "
        "is claimed, and no formula or collision coefficients were refitted. There is **no demonstrated "
        "improvement over the collision model alone**. The prompt's approximately 21% collision-model error "
        "is prior information, not an uncertainty distribution measured in this study; it does not justify "
        "a nominal credible interval. October additionally lacks dynamic loft, and the existing model was "
        "fitted to May exports, which would require explicit leakage controls in any later comparison.",
        "",
        "## 6. Phase 4: length and capture settings",
        "",
        "Two fixed, truth-independent anchors were tested: the exact production window start, and a "
        "carrier-onset estimate. The latter is the first run of at least 12 STFT frame centres with "
        "ball-band peak/background ≥12 after the OPS impact estimate plus a 4.27 ms guard. STFT windows "
        "are 256 samples, hops 32, zero padded to 4096. The carrier test compares ±160 Hz around ball "
        "Doppler with the rest of ±700 Hz. The longest consecutive qualifying centre span is reported "
        "as `carrier_clean_ms`: **carrier-qualified, not proof of clean spin modulation or true impact timing**. "
        "It can fail on weak/wandering carriers or mistake another reflector near ball Doppler. "
        "Especially for October's self-trigger, the processor's fallback name `sound_trigger` does not "
        "make it an acoustic impact observation.",
        "",
        "The envelope is filtered once using the full saved buffer; only the estimator's observation "
        "window is truncated. This isolates estimator length, not an end-to-end streaming latency test. "
        "Forward/backward filtering retains its original context. Fade subtraction and spectral analysis "
        "are rerun independently for each window. Requested windows are never padded with invented "
        "observations; unavailable lengths stay unavailable. The comparable cohort below uses only "
        "shots supporting 60 ms at the carrier-onset anchor. It therefore keeps the same shots for "
        "20/30/45/60 ms; this is not a search for the best per-shot duration.",
        "",
    ]
    window_rows = [
        r
        for r in windows["summary"]
        if r["dataset"] != "all"
        and r["anchor"] == "carrier_onset"
        and r["cohort"] == "same_shots_available_60ms"
        and r["requested_ms"] <= 60
    ]
    lines += [
        table(
            [
                "Dataset",
                "Window ms",
                "n",
                "Median abs %",
                "MAE rpm",
                "Pick within 10%",
                "Recall@5 ±10%",
            ],
            [
                [
                    r["dataset"],
                    r["requested_ms"],
                    r["n"],
                    f"{r['median_abs_pct']:.1f}%",
                    f"{r['mae_rpm']:.0f}",
                    percent(r["within_10"]),
                    percent(r["recall_5_10"]),
                ]
                for r in window_rows
            ],
        ),
        "![Signal length comparison](results/signal-length.png)",
        "",
        "Longer windows improve October's pick error over this range but remain unreliable and do not "
        "reliably expose the fundamental. May is not monotonic. The full availability/anchor results, "
        "including 80 and 100 ms, are in [window_summary.csv](results/window_summary.csv); per-shot "
        "availability is in [signal_windows.csv](results/signal_windows.csv). There are no available "
        "100 ms windows under either tested anchor; do not extrapolate an accuracy number for them.",
        "",
        "Signal-length diagnostics (derived; all captures including non-primary/unpaired):",
        "",
    ]
    diagnostic_rows = []
    for dataset in ("may", "oct", "current"):
        group = [r for r in windows["diagnostics"] if r["dataset"] == dataset]
        for key in (
            "post_trigger_ms",
            "production_window_ms",
            "carrier_clean_ms",
            "radial_distance_clean_m",
        ):
            values = [r[key] for r in group if r[key] is not None]
            diagnostic_rows.append(
                [
                    dataset,
                    key,
                    len(values),
                    f"{min(values):.2f}",
                    f"{statistics.median(values):.2f}",
                    f"{max(values):.2f}",
                ]
            )
    lines += [
        table(["Dataset", "Quantity", "n", "Minimum", "Median", "Maximum"], diagnostic_rows),
        "Distances above use OPS radial speed × carrier-qualified duration, with constant-speed and "
        "line-of-sight approximations. They are **not tape-measured usable flight**. The earlier approximately "
        "2.8 m observation is plausible in scale but not reproduced as one universal distance: current "
        "qualified-span median is approximately 2.0 m, and the primary sessions differ in geometry and clubs. "
        "The longer October buffer is not a controlled setting-only experiment.",
        "",
        "**Cycles and resolution:** each shot's reference cycles in its selected and carrier-qualified "
        "windows are in phase1.csv. Natural Fourier scale is 60/T rpm for T seconds. For a 40 ms window "
        "this is 1,500 rpm; a 2,000 rpm driver supplies only 1.33 revolutions. At 100 ms those figures are "
        "600 rpm and 3.33 revolutions. A 200 rpm (10% of 2,000) Rayleigh scale would need 300 ms, longer "
        "than the complete 136.53 ms capture. This is a resolution scale, **not a strict accuracy lower bound**: "
        "high-SNR parametric fitting can beat it, but these broad/unstable lobes do not establish that "
        "regime. Fine zero-padding grid spacing is not new signal information.",
        "",
        "**What is configurable:** 4,096 complex samples at 30 kHz is 136.533 ms. Each of 32 segments is "
        "128 samples / 4.267 ms. Nominal pre/post duration is n×128/fs and (32−n)×128/fs. Recorded "
        "trigger offsets are 68 ms for May/current (post 68.533 ms), and 17 ms for October "
        "(post 119.533 ms). The public October notes specify S#4, which agrees with that split. "
        "At the same sample rate S#12 gives nominal 51.2/85.33 ms and S#8 gives 34.13/102.4 ms. "
        "A nominal S#16→S#8 change reallocates 34.13 ms, **not** an enlarged buffer. At assumed "
        "120 mph radial speed, 5 m takes 93.2 ms; trigger lead/lag, fading and net impact consume "
        "some of the configured window. Less pre-trigger history can harm the club-speed branch. "
        "The local sound-trigger default is already 12, hardware self-trigger default 6; method defaults "
        "and old docstrings are not sufficient evidence of the setting used in a saved capture.",
        "",
        "Manufacturer source: [OmniPreSense AN-027-A](https://omnipresense.com/wp-content/uploads/2025/06/AN-027-A_Rolling-Buffer-1.pdf), "
        "quote: “The rolling buffer is captured at a fixed size of 4096 samples.” "
        "It describes the 32 segments and S#0–32 split. `set_buffer_size` configures the small processing "
        "buffer, not a larger rolling capture. Lowering sample rate lengthens time but reduces unaliased "
        "speed: with the processor's 12.43 mm wavelength, 20 kHz gives approximately 139 mph versus "
        "209 mph at 30 kHz (λfs/4). That would alias faster driver shots, so it is not recommended here.",
        "",
        "## 7. Recommendation and remaining questions",
        "",
        "1. **Stop picker/fusion implementation for now.** Leave the spin evidence experimental; keep any "
        "collision-model value separately labelled as calculated. Do not relabel a prior-dominated value as "
        "measured spin. This study does not establish the expected accuracy of picker C or D.",
        "2. **Do not change production capture settings from these results.** A controlled S#16 versus "
        "S#8 experiment at 30 kHz is reasonable if more data is wanted, but is a proposed experiment, "
        "not a demonstrated fix. Verify the actual on-device setting and trigger identity first. "
        "S#8's approximately 34 ms history must retain club-speed evidence.",
        "3. **Proposed minimum next dataset (design): 100 measured-spin paired shots**, 20 each of driver, "
        "3-wood, hybrid, 7-iron and PW, balanced 10 per capture setting per club in randomized order. "
        "Include clean and documented off-centre/low-launch strikes, varied ball orientations and the "
        "ball models actually used. Save raw I/Q, clipping, sample/trigger times, trigger mode, firmware, "
        "actual split, device/ball/net geometry, speed/launch inputs and the reference's measured-spin flag. "
        "Keep whole sessions or clubs held out from any future picker tuning. Count refusals and mispairs; "
        "never choose shots or windows from their reference error. This shot count is a practical proposal, "
        "not a power calculation or accuracy promise.",
        "4. If new data still lacks spin peaks, investigate the raw-signal extraction, fade/window bias "
        "and independent complex-I/Q evidence before spending effort on physics-weighted selection. "
        "Those are hypotheses for a separate study; this task does not implement or claim them.",
        "",
        "Owner questions: can the October export provide measured/estimated spin flags, and can its two "
        "large speed-disagreement associations be checked independently? Is the missing 4 October raw OPS "
        "session on the Mac or Pi? Is a randomized paired capture-setting experiment with longer observed "
        "flight practical? No action on those machines has been taken.",
        "",
        "## 8. Proven versus hypothesis and reruns",
        "",
        "**Established by offline checks:** exact production spectrum/pick reconstruction; October/current "
        "logged-pick consistency; the reported peak recalls/errors, exclusions and window-availability "
        "counts; viewed spectra/spectrograms; no product-source changes. **Not established:** physical spin "
        "accuracy of a new estimator, posterior coverage, collision-only superiority/inferiority, causal "
        "benefit of a longer capture setting, Pi timing, reference quality where its flag is missing, "
        "or the true identity of every weak reflector. No forbidden artifacts or TrackMan internals were used.",
        "",
        "All scripts live on local branch `codex/spin-fusion-study`, filed RESEARCH. The parent branch is "
        "`codex/integrate-continuation` at f8be8a5f. No push. The pilot checkout remains unchanged. "
        "Run from the study worktree, supplying local paths through shell variables (no home paths "
        "are embedded in code or report):",
        "",
        "```powershell",
        "uv sync --all-extras",
        "uv pip install --reinstall-package opencv-python-headless opencv-python-headless",
        "uv run --no-sync python scripts/analysis/spin_fusion_study.py --oct-session $octSession --oct-trackman $octTrackman --current-session $currentSession --output $results",
        "uv run --no-sync python -m scripts.analysis.spin_signal_windows --study $results/study.json --may-session session_logs/session_20260506_152406_range.jsonl --oct-session $octSession --current-session $currentSession --output $results",
        "uv run --no-sync python -m scripts.analysis.spin_study_report --results $results --report $report",
        "uv run --no-sync pytest -q tests/test_spin_fusion_study.py tests/test_import_boundaries.py",
        "```",
        "",
        "Phase1/candidates/recall CSVs, per-shot images, signal-length tables, source hashes and "
        "report_numbers.json retain the evidence behind the report. The report generator does not rerun "
        "or silently change production calculations. Validation results and local commits are recorded "
        "separately in VALIDATION.md.",
        "",
    ]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    args.report.write_text(build_report(args.results), encoding="utf-8")


if __name__ == "__main__":
    main()
