# Results — data-built clubhead outline, aligned and carried to contact

**CONSISTENCY ONLY. No ground truth exists.** Every error here is measured
against `openflight/contact_marks.jsonl` — the maintainer's hand marks, pass 1,
one human's reading of the same pixels. Nothing is scored against a launch
monitor. The marks also define the template's crop, origin and landmark
convention, so even leave-one-shot-out leaves the outline and the reference
sharing an annotator's convention.

Session `openflight_session_20260825_181734_filtered`, 21 shots (8 × 7-iron,
13 × 9-iron), `shot_001` excluded. Contact frame `f_c = 71.853` on every shot.
Reference plate scale (9-iron median) **3.5732 mm/px**.

## Verdict

> **No.** A data-built outline placed by translation and roll reaches ±1 px on
> this session **only in the horizontal channel, and only at the median.**
> Toe x is the one clean pass (median 0.50 px, p90 1.07 px). Heel x passes at
> the median (0.81 px) and misses at p90 (2.38 px). The topline row misses
> both (1.10 px / 3.63 px) and carries a systematic +1.10 px low bias.
> **Pre-registered gate: FAIL.** The vertical channel is the wall, and the
> reason is structural: the brief's 48 mm crop leaves the outline with a flat,
> uninformative bottom edge, so the vertical fit is driven by the head's sole
> shadow rather than by the head.

Second, separable finding: **the brief's linear carry is the wrong model and it
is the largest error in the whole pipeline** — 6.1 px in x and 9.8 px in y,
five to ten times the per-frame alignment error. Fitting a quadratic over the
same six frames drops that to 1.0 px and 1.8 px.

## 1. The templates

Built from every marked `(shot, frame ∈ {71, 72})` with a head mask.

| normalisation | club | n | extent (mm) | mean sample IoU | median | min |
| --- | --- | --- | --- | --- | --- | --- |
| brief (translate + scale) | 7-iron | 14 | 117.0 × 48.2 | 0.872 | 0.874 | 0.777 |
| brief (translate + scale) | 9-iron | 23 | 104.5 × 48.2 | 0.903 | 0.911 | 0.766 |
| roll-normalised | 7-iron | 14 | 120.6 × 48.2 | 0.873 | 0.884 | 0.799 |
| roll-normalised | 9-iron | 23 | 108.1 × 48.2 | 0.904 | 0.915 | 0.761 |

Worst samples: `shot_009_7-iron` f72 (0.777) and f71 (0.829);
`shot_021_9-iron` f72 (0.766), `shot_014_9-iron` f72 (0.808). Every worst case
is at f72, where the head has crossed onto the bright mat and its mask
collapses to a rim before the close-and-fill rebuilds it.

Three things to read off this table:

* **Individual masks agree with the template to 0.87–0.90 IoU.** That is high,
  but the crop is mark-defined on three sides, so most of what is being agreed
  on is a box the annotator drew, not a shape the pixels found.
* **The height is exactly 48.2 mm for all four templates** — i.e. the mask
  fills the crop to its floor in essentially every sample. The bottom edge is
  the crop, not the club. This is the direct cause of the vertical bias in §3.
* **Roll normalisation buys 0.001 of mean IoU.** The club's shot-to-shot roll
  spread is not what is blurring the average. All figures below use the brief's
  translate-and-scale template.

The 7-iron template is 12 mm wider than the 9-iron, in the right direction and
of a plausible size. Both are wider than a bare iron blade, because the
annotator's heel mark sits at the hosel.

Renders: `results/template_brief_7-iron.png`, `results/template_brief_9-iron.png`
(occupancy map left, outline right). The occupancy maps show a clean rising
heel → crown → toe profile on the top edge and hard vertical/horizontal cuts on
the other three.

## 2. Alignment, f66–f72, leave-one-shot-out

147 shot-frames attempted, **142 produced a head mask**, **141 accepted**.

| | |
| --- | --- |
| Local IoU, all frames | median **0.747**, p90 0.821, min 0.616, max 0.899 |
| IoU by frame (median) | f66 0.720 · f67 0.743 · f68 0.773 · f69 0.747 · f70 0.741 · f71 0.736 · f72 0.790 |
| Radar scale applied | ×1.358 at f66 falling to ×0.993 at f72 |
| Roll | median 0.0°; \|roll\| ≥ 5° on 8 of 142 frames; **never on the ±20° boundary** |
| Failed closed | 1 frame — `shot_004_7-iron` f66, on the search-radius boundary |
| Availability, all of f66–f71 | **20 / 21 shots** (gate: ≥ 17) — PASS |
| Availability, ≥ 3 of f66–f71 | 21 / 21 shots |

The radar-range scaling is doing real work and it is visibly right: the head
images 36 % larger at f66 than at contact, and the scaled outline tracks that
change frame by frame (`results/sheets/*.png`).

### Wrong locks, reported honestly

I rendered and read every sheet. There are no gross mislocks — nothing latched
onto the shaft, the golfer or the ball. The failures are all of one kind and
one direction:

* **`shot_004_7-iron` f66** — the head is half out of frame at the left edge.
  The search pinned it at the 55 px radius boundary and the gate rejected it.
  This is the gate working.
* **`shot_015_9-iron` f72, `shot_021_9-iron` f72, `shot_004_7-iron` f72** —
  roll locks at +9° to +11°, visibly tilted against a head that is not tilted.
  These carry the worst topline errors of the set (`shot_015` f72: +6.08 px).
  The roll is soaking up a vertical mismatch it cannot fix.
* **`shot_009_7-iron`** — the head mask carries a large lower lobe (sole
  shadow and turf disturbance) that is not part of the club. The outline's flat
  crop-bottom sits happily on it. This shot has the lowest median IoU (0.652)
  and the largest heel error (+2.69 px).

## 3. Score against the hand marks (test set, 37 accepted marked frames)

| axis | median \|err\| | p90 | max | bias (pred − mark) | LOO-debiased median / p90 |
| --- | --- | --- | --- | --- | --- |
| **heel x** | **0.81 px** | 2.38 px | 2.69 px | +0.14 px | 0.81 / 2.53 |
| **toe x** | **0.50 px** | 1.07 px | 2.69 px | +0.24 px | 0.50 / 1.23 |
| **topline row (y)** | **1.10 px** | 3.63 px | 6.08 px | **+1.10 px** | 1.05 / 2.73 |

2-D Euclidean, for context: heel 2.04 / 3.39 px, toe 1.58 / 4.08 px,
topline 1.73 / 3.72 px, midpoint 1.32 / 3.33 px (median / p90).

Split by frame:

| | heel x | toe x | topline y |
| --- | --- | --- | --- |
| f71 bias / median \|err\| | +0.06 / 0.62 px | +0.08 / 0.41 px | +0.89 / 0.75 px |
| f72 bias / median \|err\| | +0.35 / 0.99 px | +0.17 / 0.57 px | **+2.19 / 1.79 px** |

**f71 alone would very nearly pass.** Everything degrades at f72, where the
head has moved onto the bright mat, its mask thins, and both the roll and the
vertical placement start absorbing segmentation error.

The topline bias is a real bias, not noise: the aligned outline sits **1.10 px
(≈ 3.9 mm) below** the annotator's topline, consistently. Removing it
leave-one-shot-out improves p90 from 3.63 to 2.73 px but barely moves the
median — so the bias is only about a quarter of the vertical problem. The rest
is the flat crop-bottom letting the outline slide down onto the sole shadow,
which varies shot to shot.

### Pre-registered gate

| criterion | required | measured | |
| --- | --- | --- | --- |
| heel x median | ≤ 1 px | 0.81 | PASS |
| heel x p90 | ≤ 2 px | 2.38 | **FAIL** |
| toe x median / p90 | ≤ 1 / ≤ 2 px | 0.50 / 1.07 | PASS |
| topline y median | ≤ 1 px | 1.10 | **FAIL** |
| topline y p90 | ≤ 2 px | 3.63 | **FAIL** |
| availability (f66–f71) | ≥ 17 shots | 20 / 21 | PASS |

**Overall: FAIL.**

## 4. Carry to contact

The brief's model — the aligned midpoint fitted **linearly** over f66–f71 and
evaluated at f_c — is where the pipeline actually breaks.

| carry model | fit rms (median) | \|Δ vs marks\| median x / y | p90 x / y | bias x / y |
| --- | --- | --- | --- | --- |
| **linear, f66–f71 (brief)** | **3.94 px** | **6.07 / 9.80 px** | 8.40 / 11.99 px | −6.07 / −9.80 px |
| quadratic, f66–f71 | 1.24 px | 1.02 / 1.76 px | 1.62 / 4.19 px | +0.04 / +1.17 px |
| two-point, f71 & f72 | — | 0.58 / 1.62 px | 1.22 / 3.62 px | −0.44 / −1.62 px |

The midpoint's projected motion is not linear over six frames and is not close
to it. On `shot_002_7-iron` the midpoint travels **32.5 px between f66 and f68
and 7.1 px between f69 and f71** — the club is swinging onto the camera's
optical axis, so its transverse projection decelerates hard. A line through
those six points leaves a 5.93 px rms residual and overshoots the marks by
(−8.4, −12.0) px; a parabola leaves 2.12 px and lands at (−1.0, +5.0) px.
Across the set the linear residual is 2.7–5.9 px (median 3.94) against the
quadratic's median 1.24 px, and the sign of the linear error is the same on all
21 shots — this is a model error, not scatter.

Ball-minus-outline at the contact instant, per shot, in millimetres
(`align_per_shot.csv`; median over 21 shots, ± sd):

| quantity | linear carry | quadratic carry | two-point (n = 16) | **from the marks** |
| --- | --- | --- | --- | --- |
| ball − midpoint, x | −41.2 ± 12.9 mm | −20.1 ± 11.5 mm | −24.4 ± 10.2 mm | **−19.1 ± 9.9 mm** |
| ball − midpoint, y | −22.2 ± 12.6 mm | +15.1 ± 12.5 mm | +9.2 ± 8.0 mm | **+12.0 ± 9.6 mm** |

Ball − topline, single frames: **+9.0 ± 9.5 mm at f71**, **+9.3 ± 8.2 mm at
f72** (positive = ball centre below the outline's topline).

## 5. What this says about the approach

1. **The outline does not need the mesh.** A 0.87–0.90 self-IoU template built
   from 14–23 masks is a usable shape, and it separates the two clubs by 12 mm
   of width in the right direction.
2. **The radar range is a good size prior.** Pinning scale by
   `1581 / range_mm(f)` tracks a 36 % apparent-size change across seven frames
   with no fitted scale parameter and no size collapse.
3. **Horizontal is solved to about a pixel; vertical is not.** Toe x is at
   0.50 px median. The topline row is at 1.10 px with a 1.10 px bias, and it
   degrades to 1.79 px at f72. Since face angle reads off the heel–toe axis,
   the horizontal result is the one that matters most — but the ±1 px claim
   does not hold across both axes or at p90.
4. **The 48 mm crop is the binding constraint on the method, not on the
   annotation.** It gave the template a clean top edge and three artificial
   ones. The next measurement worth taking is a crop whose depth is set by
   where the occupancy map itself falls off, so the outline's bottom carries
   the club's sole instead of a horizontal cut.
5. **Do not carry linearly.** The single largest error in this pipeline is a
   model choice, not a pixel. A quadratic over the same six frames, or simply
   interpolating the two frames either side of contact, is 6–10× better. The
   two-point carry is the best of the three and needs only f71 and f72 — but it
   is available on 16 of 21 shots, because f72's mask is the one that collapses.
