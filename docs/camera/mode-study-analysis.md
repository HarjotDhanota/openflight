# Camera mode study — analysis plan

What the tester sessions must answer, which numbers answer it, how each number
is computed from the archive, and the rule that picks a mode. This is written
before the data exists so the decision cannot be fitted to it afterwards.

## The question

Which OV9281 readout mode gives the fusion estimators the best evidence per unit
of light, across the light levels testers actually have — and what light floor
does each mode need? "Best evidence" is defined by what the estimators consume,
not by how a frame looks.

## The arms

Five arms, 7-iron only, five swings each, every tester at their own light level.
Exposure is a smear budget in millimetres, not a pixel ceiling: 4 mm on the
approach frames the path and attack-angle estimators use, at the 7-iron toe
edge's measured speed across the image (13.6 m/s; from behind the ball the head
moves mostly in depth). That is ~300 µs, the same in every mode. Gain is set
automatically to the target brightness and never above 12×, where the sensor
starts adding offset instead of signal; the light index records what light
there was.

| Arm | Mode | Exposure | Isolates |
| --- | --- | --- | --- |
| 1 | 320×200 @ 450 | 300 µs | reference: 2× sampling, high frame rate |
| 2 | 320×200 @ 450 | 175 µs | arm 1 at a shorter exposure → blur against noise |
| 3 | 320×200 @ 450 | 87 µs | 1.5 px at the full 130 mph head speed → blur against noise |
| 4 | 640×400 @ 120 | 300 µs | arm 1 at 1:1's frame rate → frame rate alone |
| 5 | 1280×800 @ 120 | 300 µs | arm 4 at 1:1 sampling → pixels alone |

Arms 1–3 are the exposure question: one mode at three exposures, so blur and
noise trade against each other in each tester's own light. Arm 1 vs 4 is the
frame-rate question. Arm 4 vs 5 is the pixel question; at equal exposure the
photons per millimetre are equal and gain only rescales brightness. A
1280×200 @ ~450 strip joins if the bench proves the sensor runs one.

The 2× decimated modes all share one angular sampling (each output pixel spans
6 µm), so "pixels" has exactly two levels. Whether those modes average or sum
the pixels they combine is decided once on the bench with a grey card, not by
testers; it scales the light interpretation of arm 5 by up to 4× and nothing
else.

## Metrics, per shot

Every metric below is computed automatically from `frames.npz`,
`metadata.json`, the session JSONL and the estimator outputs. None needs a human
to look at a frame. Grouped by what consumes them.

### A. Capture integrity — did the arm run as declared

| Metric | From | Why |
| --- | --- | --- |
| resolved mode (width, height, sensor crop, format) | `metadata.json` → `resolved`, read back from libcamera after configure | An arm that ran a different readout than requested is not that arm |
| delivered fps, gap count, max gap | `sensor_timestamp_ns` via `timing_summary` | Frame-rate arms are only comparable at their delivered rate; a gap in the pre-impact window loses the clubhead |
| exposure_us, analogue_gain, per frame | `frames.npz` arrays | Confirms the arm's exposure was held. The camera applies exposure in whole rows (87 µs requested runs at 80), and the config block records only the startup value |
| camera light index | signal per µs per unit gain above the black floor: the slope of a line through the gain screen's unclipped gains up to 12×, at the applied exposure; the intercept is the black floor, recorded beside it | The pooling key across testers, independent of the gain each arm picked |
| flicker | periodicity and swing of per-frame mean brightness within a capture | Mains-driven LED and fluorescent light pulses at 100/120 Hz; at sub-millisecond exposures that is frame-to-frame banding. Measured from the frames, never asked of the tester |
| resting-ball diameter, px | `detect_reference_ball` on the pre-swing frames | **Falsifier H1**: equal across arms 1–4, 2× in arm 5. Any other pattern means mode substitution or a software rescale |

### B. Resting ball — the geometry anchor

Every camera-derived number starts here: diameter sets range and scale, column
sets lateral offset, row sets height.

| Metric | From | Why |
| --- | --- | --- |
| detected | `detect_reference_ball` succeeds | No ball, no geometry, nothing downstream |
| diameter jitter, px | std of diameter across the pre-swing frames | Edge quality. Half a pixel is 60–75 mm of range |
| peak DN and clipped fraction in the ball zone | pixels ≥ 250 within the ball radius | A saturated ball has no edge; the diameter is soft however tight it looks |
| edge gradient magnitude | mean gradient on the ball boundary | Sharpness independent of size; the pixel arms should raise this |
| range disagreement vs radar tee range, mm | `SetupSolution.range_disagreement_mm` | The independent check on the whole solve |

### C. Clubhead observability — what the delivery estimators actually see

| Metric | From | Why |
| --- | --- | --- |
| pre-impact frames with the head in view | frames between head entry and the impact index | Path and attack angle are velocities: two positions minimum, three to five to be robust. This is the cost of a 120 fps arm |
| head blur, px and **mm** | edge-spread width on the leading edge; mm = px / plate scale | mm is invariant to readout mode and proportional to exposure: across arms 1–3 its slope is the head's speed across the image, the number the exposure budget rests on. px shows what sampling did with it |
| head local contrast | head mean minus background mean, over background noise σ | The extractor's `\|diff\| > max(4σ, 18)` gate in physical terms; gain raises σ |
| head mask area consistency | std of mask area across the used frames | A mask that breathes frame to frame is not a stable landmark |

### D. Estimator availability — the free, decisive one

| Metric | From | Why |
| --- | --- | --- |
| chained-delivery status | one of the estimator's 20 named statuses (`ok`, `low_light`, `overexposed`, `rejected_insufficient_features`, `no_impact`, …) | **Availability per arm is a headline output.** An arm that accepts two of five swings at a tester's light level has found its lux floor |
| status histogram | counts per status per arm | *Why* an arm fails matters as much as how often: `low_light` and `rejected_insufficient_features` point to different fixes |
| timing_plausible | estimator flag | Whether the detected impact sat inside the trigger window |

### E. Estimator consistency — coarse at five swings, still informative

| Metric | From | Why |
| --- | --- | --- |
| camera-vs-OPS speed ratio and its spread | `speed_ratio_ops` per shot | Should sit near 1.0; its spread is the cleanest single consistency number the fused path produces |
| velocity MAD | `velocity_mad_mph` | Internal agreement of the feature tracks within a shot |
| club path and attack angle, median and MAD across the arm | `experimental_fused_*` | Same golfer, same club, same light — the spread is the mode's noise floor. Five swings gives a coarse floor, not a fine one |

## Aggregation

Arms are compared **within a tester**: the same room, setup, light and golfer,
so the only thing that changes between arms is the mode. Every capture run of
an arm is merged. Testers are then grouped by light bin (octaves of the camera
light index) to assemble the per-light-level table; no tester's arm replaces
another's. For each cell: availability (fraction accepted), the status
histogram, and the median and MAD of every metric above. A cell with fewer than
three accepted swings reports its histogram and is otherwise marked
insufficient, not averaged.

Setup is recorded beside every arm: the taped radar-to-ball distance the kiosk
used, and the range the camera solved from the resting ball in the gain-screen
frame. Their disagreement across testers is the evidence for whether the solve
can replace the tape.

## Pre-registered hypotheses

Stated before collection; each has a metric that decides it.

| | Hypothesis | Decided by |
| --- | --- | --- |
| H1 | Each arm ran the readout it claims | Resting-ball diameter equal in arms 1–4 and 2× in 5, and the resolved mode matches |
| H2 | Blur in millimetres grows with exposure and is equal across modes at the same exposure | Head blur, mm: its slope over arms 1–3 is the head's speed across the image, which the budget took as 13.6 m/s on approach; arms 1, 4 and 5 agree |
| H3 | Finer sampling improves edge quality at equal light | Arm 5 vs 4: lower diameter jitter, higher edge gradient, lower head-blur px for the same mm |
| H4 | The light cost of 1:1 is what the bench predicted | Arm 5: availability and `low_light` rate versus the light index; the floor where its availability drops |
| H5 | Frame rate governs clubhead observability | Arm 1 vs 4: pre-impact head frames, and the `rejected_insufficient_features` rate |
| H6 | Availability is the binding constraint, not image quality | If any arm's availability falls below the reference at a light level where the reference holds, that arm loses there regardless of its edge metrics |
| H7 | Up to the budget, a longer exposure costs the estimators nothing | Arms 1–3: availability, resting-ball jitter and path/attack-angle spread against exposure at each tester's light. If the decision rule prefers arm 2 or 3 over arm 1, blur is costing more than the budget allows and the exposure comes down |

## Decision rule

An arm is preferred over the reference (arm 1) at a given light level only if
**all** of:

1. Its availability is not lower than the reference's at that light level.
2. Its resting-ball metrics are not worse — diameter jitter and clipped
   fraction — because a softer geometry anchor poisons every metric downstream.
3. It improves at least one delivery endpoint with the others no worse:
   pre-impact head frames, head local contrast, or the speed-ratio spread.

The output is not one winner. It is a table: per light level, which arm meets
the rule, and each arm's lux floor — the light index below which its
availability drops under the reference's. That table is the exposure policy the
product ships: mode, exposure, gain range, and the floor it states to the
user.

If the result depends on an unmeasured input — the average/sum factor, the
head's speed across the image, the gain at which the extractor degrades — the
plan says so and no arm is promoted until the bench closes it.

## What this cannot certify

Everything here is consistency: the rig against itself, one arm against
another, the camera against the radar. None of it is accuracy against a
reference instrument. A mode that wins this study has the best *evidence*; it
has not been shown to produce a *correct* club path. That is a separate
validation with a separate label.

## Tooling

- **Capture:** the tester runner, with per-arm counters and the per-shot verdict
  (metrics A and D, live).
- **Export:** the session exporter and `manifest.json`, which carries the arm
  and light index per shot.
- **Scoring:** `scripts/analysis/score_mode_study.py` — reads one or more
  exported sessions, computes metrics A–E per shot, aggregates per arm × light
  bin, tests H1 and H4–H7, applies the decision rule, and writes
  `mode_study.json` and a one-page `mode_study.md`. H2 and H3 need head blur
  and local contrast, which it does not measure yet.
- **Bench, once, on the maintainer's unit:** grey-card average/sum factor, 1:1
  delivered cadence, a single lux reading to map the light index to lux, gain
  degradation on the outline extractor, and a moving-club measurement of the
  head's speed across the image.
