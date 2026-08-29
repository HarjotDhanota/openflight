# Session metrics — results

Session `openflight_session_20260825_181734_filtered`, 21 shots (`shot_001`
excluded), one golfer, 7-iron and 9-iron.

> **NO GROUND TRUTH EXISTS.** Nothing below has been scored against a launch
> monitor. Every figure is a CONSISTENCY result — the session's own sensors
> pushed through published models, then asked whether they agree with each
> other and with a golf swing. A "PASS" means "inside the band a golf swing is
> expected to occupy", never "correct".

Tags: **CONFIRMED** = computed here from the session data. **INFERRED** =
an interpretation of those numbers, not itself a measurement.

---

## Verdicts

| Metric | Verdict | Why |
| --- | --- | --- |
| A. Dynamic loft | **INCONSISTENT** | 7i median 23.9°, 9i 28.3°; both sit below their plausible bands (25–33 / 33–41) and no coefficient in the sweep rescues the 9-iron. |
| A. Spin loft | **INCONSISTENT** | Median 31.0° against a 20–30° band, and it is the same quantity as dynamic loft — `SL = (V − A)/k_v` — so it fails for the same reason. |
| A. Face angle | **INCONSISTENT as shipped, PLAUSIBLE on the track's own path** | Mean −1.9°, sd 4.5° from the fused club path; sd 2.6–3.5° and median −1.4…0.0° when the club path comes from the 3-D head track instead. |
| A. Face-to-path | **INCONSISTENT as shipped, PLAUSIBLE on the track's own path** | −6.1° / −7.8° from the fused path, which no golfer holds for 19 straight shots; −2.6° / −1.1° from the track's path. |
| B. Swing plane | **PLAUSIBLE** | 58.9° median, sd 4.4°, 18/21 shots; plane rms residual 5.5 mm; jackknife moves it 0.8° (sd) per frame removed; the boresight choice moves it 0.4°. |
| B. Swing direction | **PLAUSIBLE in magnitude, UNDETERMINED as a cross-check** | All 18 shots inside ±8.7°, median +4.3°, exactly where an iron swing should be — but it does not correlate with the fused club path (r = −0.08), so the two do not corroborate each other. |
| B. Arc radius | **INCONSISTENT** | 1.14 m median against the 1.4–1.8 m the ω = v/r validator expects; every shot but one is below the band. |
| B. In-plane contact speed | **PLAUSIBLE** | 0.90 of the OPS243 club speed, range 0.85–0.99 across all 18 shots — and the OPS243 takes no part in the computation. |

---

## A. D-plane metrics

`dplane_metrics.py`. Wood et al. 7-iron coefficients, `k_v = 0.81`,
`k_h = 0.69`:

    DL = A + (V − A)/k_v        SL = DL − A
    F  = P + (H − P)/k_h        F − P

`V = launch_angle_vertical`, `H = launch_angle_horizontal`,
`A = experimental_fused_attack_angle_deg`, `P = experimental_fused_club_path_deg`.
All 21 shots carry `experimental_fused_status` of `approach_high` or
`approach_mixed`; 19 are clean and 2 are gross attack outliers, reported
separately.

### Per-club, 19 clean shots — CONFIRMED

| | 7-iron (n=8) | 9-iron (n=11) |
| --- | --- | --- |
| Vertical launch V | **18.51°** (sd 1.65) | **22.06°** (sd 4.00) |
| Horizontal launch H | −0.76° (sd 1.74) | +1.08° (sd 2.36) |
| Attack angle A | −4.66° (sd 2.00) | −4.58° (sd 0.89) |
| Club path P | +3.33° (sd 9.31) | +5.88° (sd 1.69) |
| **Dynamic loft DL** | **23.90°** (sd 2.22, IQR 2.44) | **28.30°** (sd 4.99, IQR 2.44) |
| **Spin loft SL** | **28.00°** (sd 3.62) | **32.99°** (sd 5.29) |
| **Face angle F** | **−2.50°** (sd 5.72) | **−1.48°** (sd 3.26) |
| **Face-to-path F−P** | **−6.10°** (sd 14.74) | **−7.80°** (sd 3.53) |

Medians; spreads in parentheses.

### Gross attack outliers, excluded from the table — CONFIRMED

| Shot | Club | A | V | DL | SL |
| --- | --- | --- | --- | --- | --- |
| 20 | 9-iron | −30.98° | 22.42° | 34.94° | **65.92°** |
| 25 | 9-iron | +25.23° | 26.61° | 26.93° | **1.70°** |

A 66° spin loft and a 1.7° spin loft are not swings. Both are the fusion
failing; the D-plane inversion propagates the failure faithfully, which is all
it can do.

### Coefficient sensitivity — CONFIRMED

Median dynamic loft, and median face angle, over the same 19 shots:

| k_v | 7i DL | 9i DL | | k_h | 7i F | 9i F |
| --- | --- | --- | --- | --- | --- | --- |
| 0.76 | 25.83° | 30.47° | | 0.66 | −2.77° | −1.93° |
| 0.81 | 23.90° | 28.30° | | 0.69 | −2.50° | −1.48° |
| 0.86 | 22.20° | 26.39° | | 0.72 | −2.24° | −1.12° |

The whole k_v bracket moves dynamic loft by 3.6°, and the whole k_h bracket
moves face angle by 0.5°. Even the most favourable coefficient leaves the
9-iron 2.5° below its band. **The coefficient is not the problem.** INFERRED.

### Sanity checks — CONFIRMED

| Check | Result | Detail |
| --- | --- | --- |
| 7-iron DL median in 25–33° | **FAIL** | 23.9° (1.1° low) |
| 9-iron DL median in 33–41° | **FAIL** | 28.3° (4.7° low) |
| Spin loft median in 20–30° | **FAIL** | 31.0° |
| Face angle sd < 4° for one golfer, one session | **FAIL** | 4.5° |
| Face angle mean in 0…+3° | **FAIL** | −1.9° |
| 9i − 7i dynamic-loft gap ≈ 8° | **FAIL** | +4.4° |

### Where each failure comes from

**Dynamic loft comes from V, not A.** `DL = A(1 − 1/k_v) + V/k_v`, so with
k_v = 0.81 the vertical launch angle carries 1.23× the weight and the attack
angle only −0.23×. The 9i − 7i dynamic-loft gap of 4.4° is just the session's
3.55° launch gap divided by k_v. Re-running the inversion with the attack angle
measured independently by the 3-D head track (section B) changes almost nothing:

| | 7-iron | 9-iron |
| --- | --- | --- |
| DL, fused A | 23.90° | 28.30° |
| DL, track A | **23.76°** | **28.08°** |

CONFIRMED. So the implausible lofts are a statement about the radar's vertical
launch angle, not about the fusion's attack angle. INFERRED.

**Face angle comes from P.** `F = 1.449·H − 0.449·P`. Swapping the fused club
path for the 3-D track's own club path, with H untouched:

| | 7-iron F | 9-iron F | 7-iron F−P | 9-iron F−P |
| --- | --- | --- | --- | --- |
| fused P | −2.50° (sd 5.72) | −1.48° (sd 3.26) | −6.10° | −7.80° |
| track P | **−1.35°** (sd 2.61) | **−0.04°** (sd 3.52) | **−2.56°** | **−1.12°** |

CONFIRMED. Face angle tightens by more than 2× on the 7-iron and lands where a
single golfer's session should land, and face-to-path collapses from −6…−8° to
−1…−3°. The face-angle failure is the shipped fused club path's, not the
inversion's. INFERRED.

**The residual offset.** With the fused inputs the face-angle mean is −1.89°.
Centring it on +1.5° needs either **+2.34° on the horizontal launch angle** or
**−7.55° on the club path** — the same discrepancy read off either instrument,
amplified by 1/k_h on H and by (1 − k_h)/k_h on P. This is smaller than, and
opposite in sign to, a naive reading of the known ~5° camera-vs-radar
horizontal offset, so that offset alone does not account for it. INFERRED.

**Two club-path outliers.** Shots 2 (−14.96°) and 5 (−15.80°) sit 20° from the
session's other 7-irons. They stay in every headline number above, per the
brief. Dropping them does *not* fix the face angle (7-iron sd goes 5.72 → 4.94,
median −2.50 → −3.16), which is further evidence the fused club path is noisy
across the board rather than wrong on two shots. CONFIRMED.

Plots: `results/dplane_dl_vs_v_and_f_vs_p.png` (DL vs V, F vs P),
`results/dplane_coefficient_sensitivity.png`.

---

## B. Swing plane and swing direction from the 3-D head track

`swing_plane.py`. Per frame: head mask centroid → camera ray
(`projection._ray_world` on `fit.measured_camera(320, 200)`) → range from
`fusion.ranges_from_radar` anchored at 1581 mm at contact frame
`pre_trigger_frames − 1.575/343 × fps` = **71.85** → world point. Plane by SVD,
circle by algebraic fit inside that plane.

18 of 21 shots reconstructed. Three are held out as low quality: shots **5**
(jackknife spans 55°), **17** (30°) and **25** (16°) — a track whose answer
moves that far when any single frame is removed has not measured anything.
CONFIRMED.

### Per-club, 18 shots — CONFIRMED

| | 7-iron (n=7) | 9-iron (n=11) |
| --- | --- | --- |
| **Swing plane to ground** | **60.35°** (sd 2.65) | **57.25°** (sd 5.25) |
| **Swing direction** | **+4.43°** (sd 0.83) | **+4.25°** (sd 3.12) |
| **Arc radius** | **1.14 m** (sd 0.12) | **1.14 m** (sd 0.15) |
| In-plane speed at contact | 77.05 mph | 71.82 mph |
| Speed ÷ OPS243 club speed | **0.90** | **0.90** |
| Club path from the arc tangent | +0.86° (sd 1.35) | +1.60° (sd 2.72) |
| Attack angle from the arc tangent | −5.77° (sd 1.52) | −4.61° (sd 2.06) |
| Plane rms residual | 6.38 mm | 4.77 mm |
| Frames used per shot | 10 | 12 |

Medians. Signs: swing direction and club path are positive to the **right** of
the target line, matching the repo's convention (`server.py`,
`launch_angle.py`). Session-wide: swing plane median **58.9°**, sd 4.4°; swing
direction median **+4.3°**, full range [−2.7°, +8.7°]; arc radius median
**1.14 m**, range [0.91, 1.46]; speed ratio median **0.90**, range [0.85, 0.99].

### Sanity checks — CONFIRMED

| Check | Result | Detail |
| --- | --- | --- |
| Swing plane median in 55–65° | **PASS** | 58.9°, sd 4.4° |
| Every swing direction within ±10° | **PASS** | median +4.3°, range [−2.7, +8.7] |
| Arc radius median in 1.4–1.8 m | **FAIL** | 1.14 m, range [0.91, 1.46] |
| In-plane contact speed within 15% of OPS243 | **PASS** | ratio 0.90, range [0.85, 0.99] |
| Head arrives within 30 mm of the ball centre, across the boresight, at contact | **PASS** | median transverse miss 20 mm, range [8, 41] |
| Track attack angle agrees with the fused attack angle within 3° | **PASS** | median difference −0.55°, sd 2.40°, n = 17 |
| Swing direction correlates positively with the fused club path | **FAIL** | r = −0.08 (n = 18); r = −0.29 over the 17 shots with fused path inside ±12° |
| Track club path correlates positively with the fused club path | **FAIL** | r = −0.01; track path sd 2.24° vs fused path sd 5.85° |

### What is load-bearing

**The contact check is the strongest evidence here.** The radar anchor fixes
only the head's *depth* at contact. Where it lands *across* the boresight is
free, and it lands a median of 20 mm from the ball centre. That is the
acoustic contact frame, the taped range and the boresight all agreeing to
within half a ball. CONFIRMED.

**The speed check is a genuine cross-sensor validation.** The OPS243 club speed
takes no part in the reconstruction and the in-plane speed lands at 0.90 of it
on every one of the 18 shots (range 0.85–0.99). The 10% deficit is consistent
and is what one expects of a centroid-based track that is smoothing an
accelerating body across a symmetric window. CONFIRMED figure, INFERRED cause.

**The attack angle is an independent corroboration of the fusion.** The arc
tangent at contact gives −5.77° / −4.61° against the fusion's −4.66° / −4.58°,
a median difference of −0.55° with sd 2.40° over 17 shots — computed from
different arithmetic on the same two sensors. CONFIRMED.

**Stability.** Removing any single frame moves the swing plane by a median
0.81° (sd across the jackknife), full jackknife range 2.96°, and moves the arc
radius by a median 0.11 m. Plane rms residual is 5.5 mm over a ~1.3 m arc.
CONFIRMED.

### Boresight sensitivity — CONFIRMED

`projection` aims the lens at the world origin; the rig's camera is level
(2026-08-26 calibration). Running both ways:

| | `--boresight ball` (default) | `--boresight raw` (as the brief specifies) |
| --- | --- | --- |
| Swing plane median | 58.9° | 59.3° |
| Arc radius median | 1.14 m | 1.14 m |
| Swing direction median | +4.3° | +6.2° |
| Attack angle, 7i / 9i | −5.77° / −4.61° | −12.11° / −11.04° |
| Head-at-contact transverse miss | **20 mm** | **196 mm** |

The as-shipped aim-at-origin model puts the clubhead 196 mm below and beside
the ball at the moment of contact, and drags the track's attack angle to −6.85°
against the fusion where the ball-anchored boresight gives −0.55°. The
swing plane and the arc radius do not care. So **the swing plane is robust to
the boresight; every elevation-dependent quantity is not.** INFERRED.

### Why the arc radius fails

1.14 m against 1.4–1.8 m, and the miss is one-sided: 17 of 18 shots are below
the band. Three candidate causes, none of them separated by this data:

* The arc spans about 1.3 m of a circle and the frames are unevenly distributed
  either side of contact (median 8.5 pre, 2.5 post), so the circle fit is
  poorly conditioned. The pre-impact-only fit gives a median of 1.24 m but with
  a range of 0.47–2.21 m, so it does not rescue the figure.
* The sole shadow biases the head centroid toward the mat, and it does so most
  strongly near the bottom of the arc where the head is closest to the mat —
  which curves the fitted arc more tightly than the truth.
* The post-impact points rest on the constant-range-rate assumption, which
  stretches the track downrange.

The consequence is not confined to the radius: ω = v/r gives a median 29.5 rad/s
(1690 °/s) here, and at r = 1.6 m the same speeds give 24 rad/s. **Anything
downstream that consumes this radius inherits a ~25% bias.** INFERRED.

### Why swing direction does not track the fused club path

They are not the same quantity — the plane's ground heading is not the head's
instantaneous heading — but they should still move together, and they do not
(r = −0.08). The dispersions say which side the problem is on: the track's own
club path has sd 2.24° across the session while the fused club path has sd
5.85°, and the fused values include two shots at −15°. The 3-D track produces
the tighter estimate of the two. Whether it produces the more *accurate* one
cannot be settled here, because no ground truth exists. CONFIRMED numbers,
UNDETERMINED conclusion.

Plots: `results/swing_plane_overview.png` (swing plane, direction vs fused
path, radius, speed vs OPS243) and `results/swing_plane_tracks.png` (every
reconstructed head track, side and plan view).

---

## Segmentation: what the masks actually look like

Contact sheets for all 21 shots are written to `<out>/masks/`. They were
rendered and inspected before any centroid was trusted. What they show:

* **The clubhead images as a rim.** Its interior matches the mat, so the raw
  moving mask is an annulus whose maximum inscribed radius collapses to ~3 px.
  `split_head` needs ≥ 4 px to seed a head core and returned `None` or a
  fragment on roughly half the frames. A 3×3 morphological close plus a binary
  hole fill is applied before components are taken; on shot 002 that took
  usable head frames from 11 to 24. This is a documented deviation from the
  brief's recipe. CONFIRMED.
* **The tee veto must not be applied before contact.** The teed ball is
  stationary, so it never enters the moving mask — but the clubhead *is* at the
  ball there, and the veto punches an 8.7 px hole through the middle of it. On
  shot 002 frames 67–71 that dropped the component's maximum inscribed radius
  from 5.0–6.4 px to 3.2–3.6 px and made `split_head` fail outright. The veto
  is applied only from the contact frame on. Second documented deviation.
  CONFIRMED.
* **The sole shadow is inside the mask.** On frames where the head is near the
  mat the accepted mask is visibly a comma: head plus the shadow trailing below
  and left of it. This is a *bias*, not noise, and it is not removed. Its
  effect on plane orientation is bounded by the jackknife columns (median
  0.81° per frame removed); its effect on the arc radius is one of the three
  candidate causes listed above.
* **Motion blur near contact.** Frames within [contact − 1, contact + 2] are
  dropped outright (frames 71–73 on this rig), matching the brief.
* **The head is unsegmentable for several frames after impact** on most shots:
  it crosses the bright mat edge and its contrast against the background
  vanishes. Median post-impact frames per shot is 2.5, against 8.5 pre-impact.
  CONFIRMED.

Rejection reasons are recorded per shot in `swing_plane_per_shot.csv`
(`rejected_contact`, `rejected_tee_overlap`, `rejected_border`,
`rejected_fragment`, `rejected_late`, `rejected_no_head`, `rejected_off_plane`).

---

## Assumptions, stated as assumptions

1. **Post-impact range.** `iwr_club_path_range_rate_ms` is fitted before
   impact; this analysis continues that same constant rate through the
   post-impact frames. The clubhead is decelerating and turning there. Capped
   at 10 frames past contact. This is the weakest link in the reconstruction.
2. **Contact frame.** `pre_trigger_frames − 1.575/343 × fps` = 71.85, i.e. the
   acoustic trigger corrected for the ball-to-unit flight time.
3. **Boresight.** Default runs re-aim the camera so the observed teed ball lands
   on the taped ball position. The un-corrected alternative is reported above.
4. **Wood et al. coefficients** are treated as exact at k_v = 0.81, k_h = 0.69;
   the sweep shows what the bracket does.
5. **Plausibility bands** (7i DL 25–33°, 9i DL 33–41°, spin loft 20–30°, swing
   plane 55–65°, swing direction ±10°, arc radius 1.4–1.8 m) are the brief's,
   not measurements. Every PASS and FAIL above is against those bands.
