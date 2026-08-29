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

---

# Iteration 2 — a sole from the data, a topline from the ridge, a carry that fits

Same session, same 21 shots, same leave-one-shot-out, same pre-registered gate
(median ≤ 1 px and p90 ≤ 2 px on heel x, toe x and topline y; ≥ 17 of 21 shots
with all of f66–f71). `build_template_v2.py` and `align_and_carry_v2.py`. Every
configuration below is the SAME code with flags flipped, so the before/after is
not a comparison between two programs.

## 0. Two corrections that had to come first

**The marks file now holds two passes and the iteration-1 loader reads the wrong
one.** `build_template.load_marks` keys on `(shot, frame)` with no pass filter,
so it keeps whichever line comes last. `contact_marks.jsonl` has since gained 42
pass-2 lines by a second annotator, and that annotator's convention is a long
way from pass 1 — median |Δ| of 4.3 px in toe x, 8.7 px in toe y, 11.0 px in
topline x. Re-running iteration 1 today would silently score against pass 2.
`build_template_v2.load_marks_pass1` filters on `pass_index == 1` and
`annotator == "harjot"` and refuses duplicates. **`align_and_carry_v2.py
--sole-mode cut --top-margin 0 --no-ridge` reproduces iteration 1 to the
digit** — heel 0.81 / 2.38 (max 2.69, bias +0.14), toe 0.50 / 1.07 (max 2.69),
topline 1.10 / 3.63 (max 6.08, bias +1.10), 141 of 142 frames accepted, 20 / 21
availability — which is also the proof that the published iteration-1 numbers
were pass 1.

**The pass-1 marks are not where iteration 1 assumed they were.** Measured over
all 42 of them: the topline mark sits a median **+11.0 px to the right of the
ball centre** (p10 +6.2, p90 +14.3) because the ball's cap hides the crown
directly above it, and the crown slopes **−0.33 px/px**; the toe mark is the
head's widest point, a median **9.9 px below** the topline row, and the heel
mark 6.8 px below it. Comparing a single fixed landmark at the ball column
therefore carries ~3 px of definition error that is not the template's fault.
Every table below reports two conventions:

* **raw** — iteration 1's: the template's mean-mark landmark, transformed.
* **definition-matched** — topline read off the aligned outline's **top edge at
  the mark's own column**; toe and heel read off the outline's **maximum-x and
  minimum-x extents**.

Residual inter-annotator noise after removing the definition offsets is about
0.4–0.7 px median and 1.3–1.7 px p90. That is the ceiling; nothing below it can
be better and still mean anything.

## 1. The sole edge, from the data

The bottom crop is gone. Samples are cropped on the top (topline row − 2 px) and
the sides (heel/toe ± 2 px) only; the bottom runs to the template grid's floor
(78 mm), and the outline is taken per column as the run of mean occupancy ≥ 0.5
that starts at that column's topmost occupied row.

**The occupancy does fall off before the floor.** The pre-registered fallback
(more than half the occupied columns still occupied at the grid floor → revert
to the 48 mm cut) did not fire on either club: the floor fraction is **0.00**.
This was not obvious in advance — every single-frame mask carries 48–78 mm of
continuous mask below the topline row, sole shadow included — and it works only
because the shadow's extent varies shot to shot while the head does not.

| club | n | bbox | per-column depth (median / p10 / p90) | mean sample IoU |
| --- | --- | --- | --- | --- |
| 7-iron, occupancy sole | 14 | 117.0 × 67.9 mm | **50.0** / 33.9 / 66.1 mm | 0.774 |
| 9-iron, occupancy sole | 23 | 104.5 × 65.2 mm | **50.9** / 33.1 / 64.3 mm | 0.813 |
| 7-iron, 48 mm cut | 14 | 117.0 × 48.2 mm | 33.1 / 22.3 / 47.3 mm | 0.871 |
| 9-iron, 48 mm cut | 23 | 104.5 × 50.9 mm | 38.4 / 25.9 / 50.0 mm | 0.894 |

**Against a mid-iron face height of 45–50 mm: the median column depth lands at
50.0 and 50.9 mm, at the top edge of the band — but that median is dragged down
by the shallow hosel columns, and the face region is deeper than it should be.**
Reading the crown and sole rows separately, in mm below the marked topline row:

| band, heel → toe | 7-iron crown / sole | 9-iron crown / sole |
| --- | --- | --- |
| 1 | +25.0 / +59.0 | +21.4 / +55.4 |
| 2 | +19.2 / +59.9 | +12.5 / +59.0 |
| 3 | +12.1 / +64.3 | +6.3 / +60.7 |
| 4 (crown peak) | +1.8 / +67.0 | −1.8 / +61.6 |
| 5 (toe) | +8.0 / +63.9 | +9.4 / +56.7 |

The sole line is real and nearly horizontal — its band medians span 8 mm on the
7-iron and 6 mm on the 9-iron across a head 105–117 mm wide, which is what an
iron sole looks like from behind and nothing like the horizontal cut it
replaced. But crown-peak to sole is **65 mm (7-iron) and 63 mm (9-iron)**, about
**15 mm more than a mid-iron face**. Roughly the bottom quarter of the outline
is still shadow — shadow consistent enough across 14–23 samples to stay above
0.5. The occupancy fell off; it fell off in the wrong place, by about 4 image
pixels at contact range.

Renders: `results/template_v2_occupancy_{7,9}-iron.png`. Read them next to
`template_brief_*.png`: the 9-iron in particular is now a recognisable iron
silhouette with a rounded toe, and the toe mark lands on the outline's widest
point.

The self-IoU **drops** from 0.87–0.89 to 0.77–0.81, and that is the honest
direction — individual masks agreed with a shared box more readily than they
agree with a shape.

## 2. Alignment, and what the shaped sole bought

| | iteration 1 | + data sole | + data sole + ridge |
| --- | --- | --- | --- |
| local IoU, median | 0.747 | **0.839** | 0.805 |
| IoU p10 | 0.662 | 0.767 | 0.709 |
| median roll, f66 → f72 | 0.0 throughout | −5.0 −3.0 −4.0 −3.0 −1.0 −0.2 **+1.0** | same |
| frames with \|roll\| ≥ 5° | 8 / 142 | 46 / 142 | 46 / 142 |
| frames accepted | 141 / 142 | 141 / 142 | 141 / 142 |
| availability, all of f66–f71 | 20 / 21 | 20 / 21 | 20 / 21 |

The roll count looks like a regression and is not. Iteration 1's roll was pinned
at 0° on almost every frame because rotating a flat-bottomed box costs IoU and
buys nothing. With a shaped sole the search can see roll, and what it finds is a
**monotone ramp from about −5° at f66 to +1° at f72, visible on 17 of the 21
shots individually** — the face rotating through the last seven frames, not
scatter. It is unverified against anything and is reported only as something the
shaped outline made observable.

**Ridge refinement.** The topline is specular and sharp: on the toe-side columns
of f71/f72, mean intensity is 199–203 counts on the mask's top row against ~127
two pixels above and ~113 two pixels below. The refinement sweeps ±3 px at
0.25 px and maximises an equal-weight, min-max-normalised fusion of the local
IoU and that line response, over toe-side columns more than one ball radius from
the ball (median 11 columns, minimum 4). It moved 121 of 142 frames, by a median
0.75 px, p90 2.0 px, **max 3.0 px — the search bound, so it saturates on some
frames** and the window, not the ridge, is the limit there.

## 3. Score against the pass-1 marks (37 accepted marked frames)

median / p90, in pixels. Bias is (predicted − marked).

| axis | iteration 1 | + data sole | + data sole + ridge |
| --- | --- | --- | --- |
| heel x, raw | 0.81 / 2.38 (b +0.14) | 0.97 / 2.45 (b −0.02) | 0.97 / 2.45 (b −0.02) |
| toe x, raw | 0.50 / 1.07 (b +0.24) | 0.59 / 1.20 (b +0.08) | 0.59 / 1.20 (b +0.08) |
| **topline y, raw** | 1.10 / 3.63 (b **+1.10**) | 0.66 / 2.03 (b +0.16) | **0.92 / 1.71** (b +0.92) |
| heel x, def-matched | 1.83 / 3.70 (b −1.83) | 2.08 / 3.52 (b −1.94) | 2.08 / 3.52 (b −1.94) |
| toe x, def-matched | 0.49 / 1.37 (b −0.19) | 0.79 / 1.29 (b −0.38) | 0.79 / 1.29 (b −0.38) |
| **topline y, def-matched** | 1.17 / 3.72 (b +1.17) | 0.65 / 2.32 (b −0.33) | **0.64 / 1.48** (b +0.64) |

**The +1.10 px systematic topline bias is gone.** It was the 48 mm cut, exactly
as iteration 1 predicted: replacing the cut with the club's own sole drops the
bias to +0.16 px and the median from 1.10 to 0.66 px, with no other change. The
ridge then does the other half of the job — it barely moves the median but pulls
the **p90 from 2.03 to 1.71 px (raw) and from 2.32 to 1.48 px
(definition-matched)**, at the cost of re-introducing a +0.6 to +0.9 px offset.
That trade is what carries the topline through the gate.

Split by frame, the f72 collapse is gone too. Iteration 1's topline at f72 was
1.79 px median with a +1.79 px bias; iteration 2 (raw) is 1.03 / +1.03, and
without the ridge 0.75 / −0.08.

**The heel does not move, in any configuration.** Its definition-matched bias of
−1.94 px *is* the ±2 px crop margin: the template's left boundary is a vertical
cut 2.1 px outside the heel mark by construction, so the minimum-x extent
measures the crop. Debiased leave-one-shot-out it is 0.94 / 2.62 px, against
0.97 / 2.45 px for the raw landmark — the same number by two routes.
**Iteration 2 did not touch the heel, because the brief's ±2 px side crop leaves
the heel edge of the template as the annotator's box, not the club.**

### Pre-registered gate

| criterion | required | iteration 1 | iteration 2, raw | iteration 2, def-matched |
| --- | --- | --- | --- | --- |
| heel x median | ≤ 1 | 0.81 PASS | 0.97 PASS | 2.08 FAIL (0.94 debiased) |
| heel x p90 | ≤ 2 | 2.38 **FAIL** | 2.45 **FAIL** | 3.52 **FAIL** (2.62 debiased) |
| toe x median / p90 | ≤ 1 / ≤ 2 | 0.50 / 1.07 PASS | 0.59 / 1.20 PASS | 0.79 / 1.29 PASS |
| topline y median | ≤ 1 | 1.10 **FAIL** | **0.92 PASS** | **0.64 PASS** |
| topline y p90 | ≤ 2 | 3.63 **FAIL** | **1.71 PASS** | **1.48 PASS** |
| availability f66–f71 | ≥ 17 | 20 / 21 PASS | 20 / 21 PASS | 20 / 21 PASS |

**Overall: FAIL, on heel p90 alone.** Five of the six criteria pass. The topline
went from the worst channel in the pipeline to a passing one.

### Heel, split by hosel/shaft visibility — a null result

`split_head` returns a shaft mask on every marked frame, but on 19 of the 37 it
is more than 3 px from the head mask (median gap 4.4 px, p90 27.2 px): the hosel
neck has dropped out of the moving mask and the two halves are disconnected.
Split on that:

| | n | heel raw median / p90 | heel def median / p90 | IoU median |
| --- | --- | --- | --- | --- |
| junction attached (gap < 3 px) | 18 | 1.04 / 2.57 | 2.23 / 3.93 | 0.787 |
| junction broken (gap ≥ 3 px) | 19 | 0.93 / 1.92 | 1.75 / 2.91 | 0.770 |

**The heel error does not depend on whether the hosel junction is visible**, and
the sign is the wrong way round for an occlusion story. The junction is never
inside the ball's disc on any marked frame. Whatever costs the heel its p90, it
is not the hosel.

## 4. Carry to contact

Linear is dropped. Primary = interpolation between the aligned f71 and f72
outlines at `f_c`; cross-check = a quadratic over every accepted frame of
f66–f72. Against the marks' own f71/f72 interpolation, in pixels:

| model | Δx median / p90 | Δy median / p90 | Δ topline y median / p90 |
| --- | --- | --- | --- |
| iteration 1, linear f66–f71 | 6.07 / 8.40 | 9.80 / 11.99 | — |
| iteration 1, pair f71+f72 | 0.58 / 1.22 | 1.62 / 3.62 | 1.74 / 3.83 |
| iteration 1, quadratic | 0.49 / 1.44 | 1.13 / 3.12 | 1.14 / 3.37 |
| **iteration 2, pair f71+f72** (n = 16) | 0.88 / 1.52 | **0.96 / 1.69** | **1.01 / 1.85** |
| **iteration 2, quadratic f66–f72** (n = 21) | 0.85 / 1.54 | 1.20 / 2.07 | 0.90 / 1.85 |

The horizontal channel is marginally worse than iteration 1 and the vertical is
markedly better — 3.62 → 1.69 px at p90 on the pair carry, 3.83 → 1.85 px on the
topline. Both are now inside 2 px at p90, which the linear carry missed by a
factor of six.

**Pair and quadratic agree to well inside their own error.** Median disagreement
0.14 px in x and 0.07 px in y (p90 0.27 / 0.37, max 1.52); on the topline row,
0.12 px median and 0.47 px p90. Two different models over different frame sets
land on the same point, so the carry is no longer where the error lives. The
quadratic is available on all 21 shots and the pair on 16 — f72's mask is still
the one that fails — so **the quadratic is the model to use, with the pair as
the check**, which is the reverse of what the brief expected.

Ball minus outline at the contact instant, per shot, in millimetres
(median ± sd):

| quantity | it 1 pair | it 2 pair (n = 16) | it 2 quadratic (n = 21) | from the marks |
| --- | --- | --- | --- | --- |
| ball − midpoint, x | −24.4 ± 10.2 | −22.9 ± 11.2 | −19.8 ± 11.2 | **−19.1 ± 9.9** |
| ball − midpoint, y | +9.2 ± 8.0 | +10.0 ± 10.2 | +11.8 ± 10.1 | **+12.0 ± 9.6** |
| ball − topline, y | +9.1 ± 8.0 | +8.6 ± 10.5 | +11.3 ± 10.4 | **+12.0 ± 9.6** |

The quadratic carry lands within 0.7 mm of the marks in x and 0.2 mm in y.

## 5. The three roll locks, rendered

`results/rolllock_{it1repro,v2}_shot_{004,015,021}_f72.png` — raw, mask, the
fitted roll and a roll-0 overlay, at 8×.

* **`shot_015_9-iron` f72 — the TEMPLATE was at fault, and iteration 2 fixed
  it.** Iteration 1 locked at +9° with IoU 0.773; the raw frame shows a level
  head with a bright topline and a shadow lobe below its heel, and the
  flat-bottomed box bought IoU by tilting into that lobe. With the shaped sole
  the same frame locks at **−1.0° with IoU 0.829** and the outline sits on the
  head.
* **`shot_004_7-iron` f72 — the same, +9.0° → +1.0°.**
* **`shot_021_9-iron` f72 — the MASK is at fault, and iteration 2 made it
  worse: +11° → +17°.** Contact is at f71.85, so by f72 the ball is *moving*,
  and `head_masks` applies no tee veto on the stated grounds that "every frame
  requested is at or before contact". It is not: at f72 the departing ball joins
  the moving component and `split_head` hands it back as part of the head. The
  render shows the outline chasing a round ball blob plus a shadow lobe; the
  shaped outline has more shape to mis-fit, so it tilts further. **This is a
  defect in the iteration-1 mask recipe, not in either template.**

The two largest residuals in the whole set are the same story. `shot_016_9-iron`
f71 (topline +6.15 px, heel +2.85 px) has a 920 px head mask — the largest in
the session, half again the median — that merges head, shaft and turf shadow
into one blob, and the range-scaled outline slides low and left inside it.
`shot_009_7-iron` f71 (heel +3.59 px) is the same failure at the heel.

## 6. Verdict

> **No.** The data-built outline placed by translation and roll reaches ±1 px on
> **toe x and topline y**, at the median and at p90, in both scoring
> conventions. It does **not** reach it on **heel x**: 0.97 px median but
> 2.45 px p90, unchanged by either of iteration 2's template changes. The
> pre-registered gate fails on that one criterion.

What iteration 2 established, separately from the gate:

1. **The 48 mm cut was the vertical bias, and the club's own sole removes it.**
   +1.10 px → +0.16 px with no other change. The occupancy map does fall through
   0.5 before the shadow, on both clubs, and the resulting sole is a
   near-horizontal line — but it sits ~15 mm too deep, so about a quarter of the
   outline's height is shadow that averaging did not kill.
2. **The specular topline is a real, usable measurement.** Fusing it with the
   mask edge cuts the topline p90 from 2.32 to 1.48 px. It saturates its own
   ±3 px window on some frames, so the window is now the binding constraint.
3. **The heel is not an occlusion problem and not a sole problem — it is the
   ±2 px side crop.** Three of iteration 1's four template edges were the crop;
   iteration 2 replaced one. The heel edge is still a vertical cut through the
   annotator's box, and every heel figure in this document measures that cut.
   The next measurement worth taking is a side crop set the way the sole now
   is — by where the occupancy falls off — not by the marks.
4. **The carry is solved.** Pair and quadratic agree to 0.14 px, both land inside
   2 px at p90 against the marks, and the quadratic works on all 21 shots. Use
   the quadratic.
5. **`head_masks` must veto the ball at f72.** Contact is f71.85; f72 is after
   it. That one assumption explains the worst remaining lock in the set.

## Reproducing

```bash
export OPENFLIGHT_SESSION=.../openflight_session_20260825_181734_filtered
uv run python research/empirical_template/build_template_v2.py
uv run python research/empirical_template/align_and_carry_v2.py --tag v2
uv run python research/empirical_template/align_and_carry_v2.py \
    --sole-mode cut --top-margin 0 --no-ridge --no-sheets --tag it1repro
uv run python research/empirical_template/align_and_carry_v2.py \
    --no-ridge --no-sheets --tag v2_noridge
```

Outputs: `template_v2_{occupancy,cut}_*.png|npz`, `template_v2_summary.json`,
`align_{tag}_{per_frame,per_shot}.csv`, `align_{tag}_summary.json`,
`sheets_v2/<shot>.png`, `rolllock_{tag}_<shot>_f72.png`.
