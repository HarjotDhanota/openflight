# Spin from Speed-Track Ripple — Experiment Findings

**Date:** 2026-07-24
**Branch:** `feat/spin-experiments`
**Design:** [`docs/superpowers/specs/2026-07-24-spin-speed-ripple-design.md`](superpowers/specs/2026-07-24-spin-speed-ripple-design.md)
**Data:** one TrackMan-paired session, 51 scored shots

## Headline

**No variant merits production integration as it stands.** All four ripple
variants score 0% within ±300 RPM of TrackMan, 0 rescues and 0 regressions.

But the failure is systematic, not random: **every one of the 27 ripple
detections under-reads TrackMan, and 18 of them land within ±300 RPM of
exactly half the TrackMan value.** If the detected RPM is simply doubled, the
variants go from 0% to 56–75% within ±300 RPM — better than the envelope
baseline scores on the same shots by either convention. The ripple carries
real seam information; the estimator is locking onto the half-rate
subharmonic instead of the fundamental.

The recommendation is therefore *not* "ship it" and *not* "drop it", but
"chase the factor of two, then re-score". Sample sizes are small (4–9
detections per variant) and this is a single session, so treat the doubled
numbers as a lead, not a result.

## What was run

### Valid pairing (the only one)

```bash
uv run python scripts/analysis/experiment_spin_ripple.py \
  --openflight session_logs/session_20260605_132943_trackman.jsonl \
  --comparison session_logs/comparison_20260605_132943_trackman.csv \
  --output session_logs/spin_ripple_experiment_20260605.csv
```

Session has 64 `shot_detected` and 64 `rolling_buffer_capture` entries; the
shot-number join matched all of them with no skipped-shot warnings. 51 shots
survived the `match_quality == "good"` and non-null `spin_tm` filters — which
matches the 51 qualifying rows in the comparison CSV exactly.

Sanity check on the join: OpenFlight and TrackMan ball speeds agree to a mean
of 2.8 mph (max 4.5 mph) across the 51 shots, confirming the rows really are
the same shots.

### Session inventory — why there is only one session

`session_logs/` holds seven `*_trackman.jsonl` sessions but only four
comparison CSVs, and the CSVs cover only three dates:

| Comparison CSV | OpenFlight dates covered | Qualifying rows |
|---|---|---|
| `comparison_20260506.csv` | 2026-05-06 | 22 |
| `comparison_test2.csv` | 2026-05-11 | 61 |
| `comparison_angles_test2.csv` | 2026-05-11 (same shots as above) | 61 |
| `comparison_20260605_132943_trackman.csv` | 2026-06-05 | 51 |

The five `session_20260527_*_trackman.jsonl` sessions were shot on 2026-05-27.
**No comparison CSV contains a single 2026-05-27 timestamp**, so none of them
has TrackMan ground truth. `session_20260609_115721_trackman.jsonl` contains
no shots at all (3 entries: session start, clock sync, connection).

I still ran the four smaller 05-27 sessions against both plausible CSVs, as
the brief asks, and the runs do emit rows — because every session restarts
shot numbering at 1, so the shot-number join happily matches 05-27 shot #12
against an unrelated 05-06 or 05-11 shot #12. Those rows are cross-session
garbage, and the ball-speed check proves it:

| Attempted pairing | Rows | Ball-speed \|Δ\| mean | max |
|---|---|---|---|
| `session_20260527_125208` × `comparison_20260506` | 17 | 23.2 mph | 52.7 |
| `session_20260527_125208` × `comparison_test2` | 16 | 22.5 mph | 52.3 |
| `session_20260527_131325` × `comparison_20260506` | 11 | 31.2 mph | 108.3 |
| `session_20260527_131325` × `comparison_test2` | 9 | 29.2 mph | 63.6 |
| `session_20260527_133000` × `comparison_20260506` | 7 | 7.3 mph | 13.0 |
| `session_20260527_133000` × `comparison_test2` | 5 | 5.5 mph | 10.8 |
| `session_20260527_150521` × `comparison_20260506` | 7 | 8.7 mph | 11.7 |
| `session_20260527_150521` × `comparison_test2` | 5 | 6.0 mph | 14.2 |

Compare to 2.8 mph mean / 4.5 mph max on the valid pairing. These outputs
were written to a scratch directory and **discarded** — they are not in
`session_logs/` and must not be treated as results.

The large `session_20260527_152443_trackman.jsonl` (222 MB, 125 shots with
captures) is the most valuable unscored data in the repo, and it is unscored
purely for lack of a comparison CSV. Generating one with
`scripts/analysis/compare_trackman.py` against the 05-27 TrackMan export, if
that export still exists, would roughly triple the sample size.

## Results — session 2026-06-05 (51 shots)

Verbatim CLI output:

```
method           n   cov%     MAE  <=300%  rescue  regress
envelope         7   13.7  1636.3     0.0       -        -
freq_hop32       4    7.8  2127.3     0.0       0        0
mag_hop32        9   17.6  2503.5     0.0       0        0
freq_hop16       5    9.8  2370.3     0.0       0        0
mag_hop16        9   17.6  2450.1     0.0       0        0
Wrote session_logs/spin_ripple_experiment_20260605.csv (51 shots)
```

`n` is detections, `cov%` is coverage over all 51 shots. Since this is the
only scored session, the per-method totals across sessions are identical to
the table above.

Shot mix: 11 driver, 5 3-wood, 7 5-iron, 6 6-iron, 7 7-iron, 7 8-iron,
5 9-iron, 3 PW. TrackMan spin spans 899–7891 RPM (median 3806); OpenFlight
ball speed spans 99–158 mph (median 118).

### The envelope baseline is not a usable control here

The envelope method detected 7 shots and got **0 of 7** within ±300 RPM
(MAE 1636). That has two consequences worth stating plainly:

1. **The regression count is uninformative.** A regression is only counted
   when the envelope was within ±300 RPM and the variant was not. The
   envelope was never within ±300 RPM, so "0 regressions" is arithmetically
   guaranteed and says nothing about variant safety.
2. **This session is hard.** Whatever is defeating the ripple estimator is
   largely defeating production spin detection too. Reading the ripple
   numbers as "worse than production" overstates the gap; both are failing.

Coverage is the one place a variant beats the baseline: `mag_hop32` and
`mag_hop16` each detected 9 shots vs the envelope's 7, and 7 shots had a
variant detection where the envelope had none. Ten of 51 shots (19.6%) got a
detection from at least one variant.

## The half-rate lock

This is the substantive finding. Across all 27 pooled ripple detections:

- **Every single one under-reads TrackMan.** Not one over-read.
- Median ratio of detected RPM to TrackMan RPM is 0.50–0.52 for all four
  variants independently.
- **18 of 27 (67%) land within ±300 RPM of half the TrackMan value.** Zero
  land within ±300 of 1×, 2×, or 3× TrackMan.

I tested the obvious competing explanation — that peaks are simply pinned
near the bottom of the seam band (`MIN_SEAM_HZ = 33 Hz` ≈ 1980 RPM) and only
*look* like half because TrackMan happens to sit near 2× the floor:

| Model | Bias | Residual SD | MAE |
|---|---|---|---|
| A: detected = 0.5 × TrackMan | +110 RPM | 439 RPM | 296 RPM |
| B: detected = constant (2625 RPM) | 0 | 520 RPM | 398 RPM |

Model A wins. And a 439 RPM residual SD is about half the ripple FFT's raw
bin width, so the subharmonic model fits about as tightly as the resolution
permits. For context, the ball-visible window is ~70 ms at both hops
(66 windows at hop 32, 131 at hop 16), giving a raw bin width of ~855 RPM
before zero-padding and parabolic interpolation. That is coarse: even a
perfectly-tuned estimator cannot do much better than ±400 RPM on this data,
which is itself an argument that ±300 RPM is a demanding bar for this method.

### If the factor of two were corrected

Scoring the same detections with the RPM doubled:

| method | n | MAE | ≤300 RPM | ≤500 RPM |
|---|---|---|---|---|
| freq_hop32 | 4 | 454 | 75% | 75% |
| mag_hop32 | 9 | 621 | 56% | 67% |
| freq_hop16 | 5 | 592 | 60% | 60% |
| mag_hop16 | 9 | 625 | 56% | 67% |
| envelope (for reference) | 7 | 2547 | 14% | 14% |

Doubling the envelope baseline does *not* rescue it (14%), so this is not an
artifact of the scoring — the ripple tracks specifically carry a strong,
consistent half-rate component that the envelope path does not.

**Caveat, and it is a large one:** n is 4 to 9. The `freq_hop32` 75% figure is
three shots out of four. These percentages have confidence intervals wide
enough to swallow most of the differences between variants. Do not rank the
variants on this table.

### Why the harmonic disambiguation did not catch it

The expected-spin prior should have preferred the 2× candidate. It did not,
and the likely reason is that **the prior itself is badly miscalibrated on
this session**: `get_optimal_spin_for_ball_speed` overshoots TrackMan by an
average of **+2306 RPM** (MAE 2335) across all 51 shots — e.g. shot 22 has a
prior of 8523 RPM against a TrackMan value of 5678. A prior that is itself
~1.6× high cannot reliably arbitrate between a candidate and its 2× partner.
Note this is a *production* prior, not something the experiment introduced.

A second contributor: at ~855 RPM per raw bin the candidate list is coarse,
so the 2× partner of a ~2400 RPM peak may not appear as a distinct resolvable
peak at all.

## Gate behavior

Rejection-reason counts over all 51 shots:

| Gate | freq_hop32 | mag_hop32 | freq_hop16 | mag_hop16 |
|---|---|---|---|---|
| Detected | 4 | 9 | 5 | 9 |
| Peak at lower rail | 10 | 22 | 10 | 22 |
| Not persistent across halves | 16 | 4 | 15 | 5 |
| Too few seam cycles | 6 | 13 | 6 | 12 |
| SNR below 2.5 | 15 | 3 | 15 | 3 |

Two clear patterns:

- **The lower-rail guard is doing most of the rejecting on the magnitude
  tracks** (22 of 51 each). Given the half-rate lock, this is exactly what
  you would expect: for a shot at 4000 RPM true spin, the half-rate tone sits
  at ~2000 RPM, right at the 1980 RPM band floor. The rail guard is
  suppressing the same artifact the detections are falling into — it is
  catching the worst cases and letting the marginal ones through.
- **Frequency and magnitude tracks fail differently.** The frequency track is
  SNR-limited (15 rejections) and persistence-limited (16); the magnitude
  track has plenty of SNR (detected SNRs run 6–60, vs 6–11 for frequency) and
  is limited by the rail and cycle-count gates instead. They are not
  redundant, which is mildly encouraging for a future fusion approach.
- **Hop 16 buys essentially nothing.** It doubles the track sample count
  (131 vs 66 windows) but the detections, RPM values and rejection profile
  are near-identical to hop 32. This matches the design doc's physics note:
  hop does not improve ripple-FFT resolution, which is set by the ~70 ms
  visibility window. There is no case for hop 16 over hop 32.

### Envelope-baseline reporting gap

28 of the 51 rows have `env_detected = False` with a **null**
`env_rejection_reason` and `env_confidence = 0.5`. These are rail-flagged
results whose reason string is never populated — the reason field is only
filled for the 7 shots rejected with an explicit `Lower-rail peak at 2417 RPM
(envelope-drift leakage suspected)` message. Read `env_detected` as
authoritative; the reason column under-reports.

Separately, the recurrence of **exactly 2417 RPM** in all 7 explicit envelope
rejections, and **exactly 3076 RPM** across 7 detections, is a fixed-bin
artifact signature. The 3076 RPM detections span only 3 shots (25, 26 and
44), and shot 26 alone accounts for 5 of the 7 — one from every method. That
concentration is the more striking form of the artifact: rather than a value
recurring loosely across many shots, five independent estimators land on the
identical spurious bin for a single shot. A real seam tone would not produce
bit-identical values across methods that share no processing after the STFT.
Both values sit in the range production already treats as suspect
(`SPIN_LOW_BAND_SUSPECT_MAX_RPM = 3100`), and the ripple estimator mirrors
production's rail guard but **not** its low-band-suspect machinery — so it
admits peaks production would flag.

## Notable per-shot cases

Detections by shot (`.` = no detection; RPM otherwise):

```
shot club     ball  tm_rpm  prior    env    f32     m32     f16     m16
  15 pw         99    7337  11520   4834      .       .       .       .
  19 9-iron    106    5791   9738   5273      .       .       .       .
  21 9-iron    107    6263   9689   4834      .       .       .       .
  22 8-iron    106    5678   8523   4175      .    3941       .    4106
  24 8-iron    111    5034   8384      .      .    2602       .    2609
  25 8-iron    109    5301   8421   3076      .    3008       .    3008
  26 8-iron    109    5883   8426   3076   3076    3076    3076    3076
  27 8-iron    102    5930   8615      .      .    2245    2403    2403
  28 8-iron    108    5428   8461      .      .    2527       .    2527
  30 7-iron    116    4525   7356      .      .    2383       .    2390
  31 7-iron    115    4311   7376      .   2094    2142    2101    2142
  33 7-iron    116    4582   7356      .   2211    2218    2321    2362
  40 6-iron    120    3490   6381      .   2376       .    2444       .
  44 5-iron    126    2607   5671   3076      .       .       .       .
```

- **No rescues, and none were close.** The rescue bar is ±500 RPM on a shot
  the envelope missed. Seven shots had a variant fire where the envelope did
  not (24, 27, 28, 30, 31, 33, 40); the closest of those was shot 40 at
  −1046 RPM (`freq_hop16`, 2444 vs 3490). Under a 2× correction, **five of
  those seven — shots 24, 28, 30, 31 and 33 — would clear the ±500 bar**,
  with best-variant errors of +170, −374, +241, −27 and +60 RPM
  respectively. The two that would not are shot 27 (−1124) and shot 40
  (+1262), which are also the two shots furthest from the half-rate ratio
  (0.38–0.41 and 0.68–0.70 against a cluster median of 0.50).
- **Worst errors:** shot 27 (`mag_hop32`, 2245 vs 5930, −3685 RPM, ratio
  0.38) and shot 27 again on `freq_hop16`/`mag_hop16` (2403, −3527). Shot 27
  is also the shot furthest from a clean 0.5 ratio.
- **Best errors:** shot 22 `mag_hop16` (4106 vs 5678, −1572) and shot 40
  `freq_hop16` (2444 vs 3490, −1046). Both still far outside ±300.
- **Shot 26 is the clearest artifact case:** all five methods, including the
  envelope, return *exactly* 3076 RPM against a TrackMan value of 5883. Five
  independent estimators agreeing to the RPM on a wrong answer means they are
  all locking to the same spurious bin, not converging on physics.
- **Every detection is an iron or wedge.** Not one driver or 3-wood shot
  produced a ripple detection, despite drivers being the largest club group
  (11 shots). Driver spin here runs 899–2700 RPM, and **9 of the 11 driver
  shots are below the 1980 RPM band floor** — the method as specified cannot
  see them at all. That is a structural coverage ceiling, not a tuning
  problem. (The 3-wood shots, 2338–3585 RPM, are all inside the band and
  still produced nothing.)

## Recommendation

**Against the spec's bar — "adds rescues without adding regressions, or beats
the envelope method outright" — no variant passes. Do not integrate any of
the four into production now.**

The rescue count is 0 for all four. The regression count is also 0, but as
noted that number is vacuous on this dataset because the envelope baseline
never cleared ±300 RPM. Accuracy is worse than the envelope on MAE for every
variant. On the letter of the bar this is a clean no-go.

**But do not close the experiment.** The half-rate lock is a specific,
falsifiable bug with a specific fix, and correcting it moves the variants
from 0% to 56–75% within ±300 RPM on the shots they do detect. That is a
large enough swing to justify one more iteration rather than abandoning the
approach. Concretely, in rough priority order:

1. **Establish whether the 2× is physical or a code defect.** A golf ball's
   seam is a great circle, so for most spin axes it crosses the radar's line
   of sight *twice* per revolution — meaning the observed modulation should
   be 2× the spin rate, not 0.5×. Observing 0.5× is the opposite of the naive
   expectation and needs explaining before any correction factor is applied.
   Both production (`processor.py:724`) and the estimator
   (`spin_ripple_estimator.py:347`) use `rpm = freq * 60` with production's
   comment asserting "seam = 1x spin", so the two paths agree with each other
   and a units slip in the estimator alone is ruled out. Do not hard-code a
   ×2 until the mechanism is understood; a blind factor that happens to fit
   nine shots is how you ship a wrong constant.
2. **Get more ground truth.** `session_20260527_152443_trackman.jsonl` has
   125 captured shots and no comparison CSV. Generating one would more than
   triple N and is the single highest-value thing available. Everything above
   rests on 4–9 detections from one session with one ball and one player.
3. **Log rejected peak RPMs.** The CSV writes RPM only for detections, so the
   22 rail-rejected peaks per magnitude variant are invisible. If those also
   sit at half the TrackMan value it would confirm the subharmonic model on
   ~3× the sample; right now that check is impossible without a code change.
4. **Fix or bypass the expected-spin prior for this analysis.** A prior
   biased +2306 RPM cannot arbitrate harmonics. Re-scoring with the TrackMan
   value as an oracle prior would separate "the estimator cannot find the
   fundamental" from "the estimator finds it but picks the wrong candidate".
5. **Drop hop 16 from the grid.** It costs 2× the compute for
   indistinguishable results, exactly as the design predicted.
6. **If it is revisited, prefer the magnitude track at hop 32.** Best
   coverage (9/51), by far the strongest SNR (6–60), and the frequency
   track's persistence failures (16 of 51) suggest the FM ripple is too noisy
   at this visibility duration. This is a weak preference on weak data.

A realistic assessment: even with the factor of two resolved, coverage is
17.6% and the 1980 RPM band floor structurally excludes most driver shots
(9 of 11 here). This looks like a complementary estimator for iron and wedge
shots at best, not a replacement for the envelope method.

## Re-running

Primary (and only valid) pairing:

```bash
uv run python scripts/analysis/experiment_spin_ripple.py \
  --openflight session_logs/session_20260605_132943_trackman.jsonl \
  --comparison session_logs/comparison_20260605_132943_trackman.csv \
  --output session_logs/spin_ripple_experiment_20260605.csv
```

Verify the join before trusting any new pairing — OpenFlight and TrackMan
ball speeds should agree within a few mph:

```bash
uv run python -c "
import csv, statistics
rows = list(csv.DictReader(open('session_logs/spin_ripple_experiment_20260605.csv')))
d = [abs(float(r['ball_speed_of']) - float(r['ball_speed_tm'])) for r in rows if r['ball_speed_tm']]
print('n=%d mean=%.1f max=%.1f mph' % (len(d), statistics.mean(d), max(d)))
"
```

Estimator unit tests:

```bash
uv run pytest tests/test_spin_ripple_estimator.py tests/test_spin_experiment_lib.py -v
```

The 2026-05-27 sessions can only be scored once a matching comparison CSV
exists. Pairing them with `comparison_20260506.csv` or the `test2` CSVs
produces rows, but those rows are cross-session joins and are invalid.
