# OpenFlight club delivery and impact-location follow-up

**Date:** 2026-08-26  
**Dataset:** `openflight_session_20260825_181734_filtered`  
**Scope:** 21 shots after excluding shot 001 (old exposure/gain, 99.8% clipped)  
**Image convention:** every stored frame was un-mirrored before analysis

## Bottom line

OpenFlight is not yet measuring club path, attack angle, face angle, dynamic loft, or
impact location on this session. The latest tests find a specific radar phase-combination
failure, but the alternative phase route is still too unstable to promote. The optical
silhouette route remains unobservable at the current plate scale, and neither of the two
sequence regularizers turns it into a validated pose measurement.

The most actionable new finding is in the radar path code:

> The accepted path uses `tx2_phase_at`, which voltage-averages the TX1 and TX3 references.
> Across all 21 shots that average flips between two phase states about 2–3 radians apart.
> The already-existing separate-reference midpoint suppresses the jump by roughly 8x.

That explains the unanimous `rejected_phase_span`; it does **not** yet validate the
separate-reference path values. Frame jackknifes and plausible impact-time shifts still
move path and AoA by many degrees.

Impact location remains a harder problem than face angle. Ball direction plus a valid club
path can infer a model-dependent face normal. Toe/heel and high/low strike coordinates also
require the face origin and in-plane axes at contact: a validated full rigid-body pose plus
authored per-club face registration. The current data do not supply that state.

## What was run

All experiments used `uv`, all 21 eligible shots were attempted before conclusions were
drawn, and exact model masks were rendered over the source images without padding.

### Ball boundary with one-sided contrast loss

The ball's top boundary is cut inward by roughly 1.8 px on a 6.5 px radius. A free circle
fit, Hough circle, or enclosing circle is not robust enough here: the missing side and the
illumination gradient are correlated, so those methods move the centre toward the visible
arc. The appropriate estimator is a **known-radius, missing-data generative fit**:

1. Calibrate camera intrinsics and distortion independently. Use radar/tape range plus the
   42.67 mm ball diameter to predict image radius; do not derive focal length from this same
   biased ball boundary.
2. Search only the two centre coordinates (and a narrow range/radius uncertainty), scoring
   signed radial image gradients at the predicted circumference. Use gradient direction as
   well as magnitude so mat texture does not vote equally with a sphere edge.
3. Treat low-contrast sectors as censored/missing observations, not negative evidence. Use
   a robust loss over sectors and require enough angular coverage plus opposing sectors to
   fail closed.
4. Jointly fit several stationary pre-impact frames with one centre/radius and per-frame
   brightness/background nuisance terms. This separates fixed geometry from flicker and
   sensor noise.
5. For the strongest version, forward-render a blurred, shaded sphere over a local planar
   background and optimize centre/range, PSF width, illumination direction, and background
   gradient. Compare against raw pixels, not a thresholded contour.

Useful initializers include a radial-symmetry transform, Laplacian-of-Gaussian blob centre,
or the bottom/side high-contrast arcs. They should not be the final metric. A generic active
contour may bridge the missing top edge but has no physical radius constraint and can simply
learn the same background bias. The fixed-radius sphere fit is the key reduction: it turns
the weak short-arc problem from free centre-plus-radius into a two-coordinate estimate.

### 1. Speed-constrained rigid rotation

The new sequence model has five parameters for an entire run:

```text
R(t) = exp([omega]x (t-t0)) R0
|omega| = measured club speed / assumed swing radius
free: initial yaw/pitch/roll + angular-axis azimuth/elevation
```

Only six shots contain four or more usable pre-impact head masks. All six were fit at swing
radii 1.4, 1.6, and 1.8 m.

| swing radius | required angular speed | mean constrained IoU | mean IoU cost vs free frames |
|---:|---:|---:|---:|
| 1.4 m | approximately 1,446–1,580 deg/s | 0.3885 | -0.0395 |
| 1.6 m | approximately 1,265–1,382 deg/s | 0.3871 | -0.0408 |
| 1.8 m | approximately 1,124–1,229 deg/s | 0.3861 | -0.0419 |

The magnitude constraint is satisfied by construction, but the result is not a validated
pose. Exact overlays show persistent observed-only head/neck pixels, and the best axis is
not stable under the modest radius sweep: axis disagreement reaches 82 degrees on shot 2
and 35 degrees on shot 20. Shots 18 and 23 are much more stable, which shows that the
failure is data-dependent rather than a coordinate singularity shared by every run.

The earlier unconstrained rigid fit recovered a median 508 deg/s against a geometric
requirement of 1,170–1,500 deg/s, an observed/required ratio of about 0.39. That external
speed ratio remains the correct gate for an unconstrained pose model. IoU is retained only
as a diagnostic because it is anti-correlated with pose correctness in this dataset.

**Verdict:** the physical speed constraint prevents the fit from inventing a slow club, but
the remaining silhouette evidence does not determine a stable swing axis or pose.

### 2. Why radar club path is rejected 21/21

The exact post-outlier TX2 phases consumed by `estimate_club_path` were captured and reduced
to per-frame circular medians.

| diagnostic | 21-shot result |
|---|---:|
| accepted-path phase span | median 3.44 rad, range 2.95–4.71 |
| largest step from voltage-averaged reference | median 2.73 rad |
| largest step from separate TX1/TX3 phase midpoint | median 0.34 rad |
| within-frame phase RMS | median 0.27 rad |
| accepted-path cross-range residual | median 29.0 deg |

The phase medians repeat the same shape across clubs: early frames cluster near +0.5 rad,
then one boundary jumps toward roughly -2.7 rad. The within-frame samples are much tighter
than the between-state jump. This is not credible club kinematics.

The source already describes the cause. `tx2_phase_at` averages TX1 and TX3 in complex
voltage space before taking phase. As their elevation-dependent phases separate, their
vector average approaches cancellation and its phase flips. `tx2_reference_phases_at`
exists specifically to avoid the 180-degree midpoint branch flips seen on real captures,
but it currently feeds only `experimental_path_candidate`; the production acceptance path
and phase-span gate still use the cancellation-prone voltage average.

On current-HEAD replay, the separate-reference candidate reports:

- `candidate_available`: 12/21;
- `candidate_noisy_fit`: 8/21;
- `candidate_out_of_bounds`: 1/21;
- median fit residual: 1.96 degrees;
- path range: -22.4 to +46.0 degrees.

The alternative therefore fixes the obvious branch discontinuity but does not establish
accuracy. There is no independent club-path truth in this session, and timing perturbation
below shows that its value is not stable enough to promote.

**Verdict:** do not widen `CLUB_MAX_PHASE_SPAN_RAD`. The gate is correctly rejecting a bad
observable. Move the separately referenced phase estimator into a frozen validation arm,
then validate it against a rigid/common target and a blind club-path reference before it can
become the accepted path.

### 3. Four/five-frame jackknife and impact-time perturbation

Each acquisition frame was deleted in turn from the exact elevation points handed to the
AoA fit. The entire path/AoA replay was also repeated at impact shifts of one camera frame
(2.1385 ms) and the measured camera/radar timing spread (1.4 ms), in both directions.

| test | median full range | shots exceeding 2 degrees |
|---|---:|---:|
| delete-one-frame AoA jackknife | 6.4 deg | 21/21 |
| impact-time perturbation, AoA | 5.1 deg | 18/21 |
| impact-time perturbation, path | 16.5 deg | 21/21 |

These are full ranges, not standard errors. The estimator fails the intended 1–2 degree
stability target on essentially the entire set.

**Verdict:** the problem is not just a conservative reject threshold. Four/five frames,
the current target association, and the present impact-time uncertainty do not support the
claimed club angles.

### 4. The two-times area excess

At the tape range, the observed mask has a median 1.939 times the sharp CAD area. The source
was decomposed in the requested order.

#### 4.1 Proximal shaft leakage

The mesh contains 61.8 mm of hosel/ferrule, not a shaft. That stub is 15.9% of mesh
triangles and contributes roughly 16–21% of its rendered pixels in the checked real pose.

`split_head` waits until a thin component reaches 60 px from the head core before seeding
the shaft partition. At the measured 0.295 px/mm plate scale, 61.8 mm projects to about
18.2 px. Re-splitting the exact selected component with that physical reach removes a
median 21.4% of the current observed mask (IQR 15.3–44.1%). This is an estimate because a
2-D core distance is not identical to 3-D hosel arc length, but it quantifies the direction
and scale of the leakage instead of calling the CAD stub a shaft.

#### 4.2 Motion blur

The CAD mask was integrated along the observed centroid velocity for the exposure recorded
with each shot. This is a union of translated exact model renders, not a padded outline.

- median smear: 1.86 px;
- median rendered-area increase: 28.3% (IQR 18.2–31.9%).

#### 4.3 Remaining scale

After the physical-reach split and motion integration:

- observed/blurred-model area ratio: median 1.223 (IQR 0.747–1.475);
- equivalent linear CAD scale: median 1.106 (IQR 0.864–1.215).

Thus shaft leakage plus blur explain most of the median 1.94x excess. The remainder is
consistent with roughly 11% linear scale at the median, but the broad IQR includes frames
where the model is already larger. Because pose is unvalidated, the same 7-iron mesh is
also being applied to 9-iron footage, and the physical heads were not measured, this is
**not evidence that the CAD is 11% undersized**. Measure both real heads heel-to-toe,
sole-to-topline, and front-to-back before changing mesh scale.

### 5. Existing `fit_sequence` in the A/B/C harness

`fit_real.fit_sequence` was run on the same 66 masks as the independent-frame A/B/C arms.
A failing regression test first exposed that a singleton range grid was still allowed to
refine away from its value; `refine_range=False` now permits a genuine hard-pinned C arm.

| arm | depth treatment | median IoU | median range | median adjacent pose change | >45 deg |
|---|---|---:|---:|---:|---:|
| A | old grid | 0.4518 | 1175 mm | 1.0 deg | 0% |
| B | grid recentered on tape | 0.3969 | 1256 mm | 0.0 deg | 0% |
| C | hard-pinned 1581 mm | 0.3550 | 1581 mm | 0.0 deg | 0% |

The smoothness penalty charges any motion, so the optimiser buys coherence by freezing the
pose. Its large depth pull and zero-degree median motion are not a recovered swing.

**Verdict:** keep this only as a negative/control arm. Use the speed-constrained rigid
rotation model for physical sequence work.

### 6. Resolution scaling on real segmented edges

The unfinished reverse-scaling check is complete on 65 real fitted masks. Halving the
footage did not double the flat basin:

| parameter | 320x200 basin | 160x100 basin | half/full ratio | linear prediction |
|---|---:|---:|---:|---:|
| yaw / face angle | 11.25 deg | 8.75 deg | 0.78 | 2.00 |
| pitch | 13.75 deg | 10.00 deg | 0.73 | 2.00 |
| roll | 10.00 deg | 8.75 deg | 0.88 | 2.00 |

This does not mean fewer pixels improve pose. Majority downsampling also removes thin shaft
pixels, averages segmentation noise, and changes rasterisation. It means the claimed linear
mapping from plate scale to real-data angular resolution is false for the current pipeline.
Clean-render leverage cannot be extrapolated directly to a 1:1 sensor mode.

The current field of view is about 1.08 m; 1:1 full-frame with the same lens would be about
2.17 m. The ambient-light constraint remains binding because plate scale also increases
blur in pixels. A real 1:1 capture with a measured lux/exposure sweep is required before
buying a 6 mm lens or claiming approximately one-degree face angle.

### 7. Remaining falsification tests

#### Test 6 — D-plane coefficient envelope

| vertical face coefficient | inferred 9i-7i dynamic-loft gap | bootstrap 95% interval |
|---:|---:|---:|
| 0.68 | 4.68 deg | 0.47–8.75 deg |
| 0.72 | 4.39 deg | 0.63–8.09 deg |
| 0.81 | 3.86 deg | 0.72–6.77 deg |
| 0.86 | 3.61 deg | 0.76–6.21 deg |

No coefficient in the plausible vertical sweep changes the point estimate into an 8-degree
gap. The intervals are wide because there are only 8 eligible 7-irons and 13 9-irons and
the attack values are noisy. Horizontally, varying the face coefficient from 0.61 to 0.87
changes the inferred face sign on 6 of 19 shots. Per-shot face classification is therefore
coefficient-prior dependent even before club-path error is included.

#### Test 7 — LCMF component disagreement

The two saved elevation components independently retain almost the same club gap:

| component | 9i-7i gap |
|---|---:|
| `two8` | 2.99 deg |
| `four4_path_tdm` | 3.09 deg |

They correlate at 0.894 but differ by a mean 2.58 degrees (SD 1.73) in absolute angle. The
gap is not created by winner selection, while the absolute datum remains model-dependent.
The requested raw pre-selection elevation phase slope was not logged in this export, so
that part of test 7 remains open and should become a required diagnostic field.

#### Test 10 — same-point radial consistency

All 21 shots were attempted. Nineteen supplied a ball track and at least one consecutive
optical head-centroid interval. Optical flow plus OPS speed predicted radar radial speed
with:

- median residual: +0.3 m/s;
- residual MAD: 1.1 m/s;
- median within-shot residual range: 0.9 m/s.

This test does **not** falsify radial-rate correspondence on the intervals with coverage.
It also does not validate same-point fusion: the radar club selector is OPS-speed-gated and
the optical prediction uses OPS magnitude, so the agreement is not independent; most shots
have only one to three consecutive intervals; and absolute scattering-centre position was
not observed. Treat it as a passed weak necessary check, not proof that centroid, radar
scatterer, and contact point coincide.

#### Test 11 — physical impulse feasibility

A rejection-only grid used measured ball/club speed and launch, D-plane face-normal
envelopes, centred effective mass 0.10–0.50 kg, 0–30 mm strike offset with
0.0003–0.001 kg m² rotational inertia, restitution 0.55–0.90, and friction 0.10–0.60. It
did not use project-derived spin. At least one feasible grid point exists on 18/19 shots
with complete D-plane inputs. Shot 20 has none; it is also the known gross fused-AoA failure
near -31 degrees.

Feasible points are not fitted strike, restitution, or face measurements. This test only
says the remaining 18 shots are not rejected by that broad necessary-condition grid.

#### Test 8 — geometry perturbation

The baseline replay reproduces every saved vertical angle to floating-point precision. All
21 shots remain accepted in every variant.

| perturbation | mean absolute-angle shift | shot-to-shot SD of shift | 9i-7i gap |
|---|---:|---:|---:|
| baseline | 0.000 deg | 0.000 | 3.041 deg |
| tilt -2 deg | -4.125 deg | 0.552 | 2.969 deg |
| tilt +2 deg | +4.049 deg | 0.579 | 2.595 deg |
| radar height -20 mm | -0.753 deg | 0.177 | 3.103 deg |
| radar height +20 mm | +0.780 deg | 0.213 | 2.947 deg |
| ball height -10 mm | +0.405 deg | 0.066 | 3.042 deg |
| ball height +10 mm | -0.412 deg | 0.079 | 3.073 deg |
| tee range -30 mm | -0.462 deg | 0.117 | 2.989 deg |
| tee range +30 mm | +0.479 deg | 0.119 | 3.119 deg |
| alternating channel phase -0.1 rad | -1.527 deg | 1.108 | 2.783 deg |
| alternating channel phase +0.1 rad | +1.579 deg | 0.522 | 2.721 deg |

The absolute launch datum is strongly calibration-dependent. The present model responds to
a 2-degree tilt perturbation by about 4.1 degrees; that approximately 2:1 sensitivity needs
a dedicated model/code audit rather than being assumed physical. A modest alternating
0.1-rad channel-phase error moves the mean by about 1.5 degrees. The club gap is more robust
than the absolute angle but still spans 2.595–3.119 degrees across this limited grid, a
0.524-degree envelope. Calibration uncertainty belongs in every reported result; optimiser
peak width alone materially understates it.

## What OpenFlight is doing wrong

1. **The accepted radar path uses the known cancellation-prone phase combination.** The
   separate-reference implementation exists but is confined to an experimental field.
2. **Threshold tuning is being asked to solve an observable failure.** A 2–3 rad branch
   flip should be removed at the signal model, not admitted by widening phase span.
3. **Four/five samples are treated as sufficient because they define a line.** They do not
   validate target identity or slope; the all-shot jackknife proves the distinction.
4. **The shipped preferred optical path interval still crosses impact.** Its first choice is
   `(-2, +1)`, even though collision changes translation and rotation. Delivery must be fit
   pre-impact and extrapolated to first contact; cross-impact motion is a diagnostic only.
5. **Impact time is treated too sharply.** A plausible shift changes path by 16.5 degrees
   at the median. Contact time must be a latent interval with propagated uncertainty.
6. **The optical area objective mixes head, proximal shaft, and exposure integration while
   the hypothesis is a sharp 61.8 mm-stub CAD render.** Range then absorbs the mismatch.
7. **`fit_sequence` rewards immobility.** Its coherent output is a regularization artifact,
   not physical validation.
8. **Clean-render pixel leverage is being promoted as real-camera resolution.** Real masks
   fail the assumed scaling law.
9. **Face inference and strike inference are being conflated.** D-plane inversion can give a
   weak face-normal prior; it cannot supply the face origin or strike coordinates.
10. **The accepted CAD coordinate frame is inferred from geometry.** Every supported club
   needs authored face origin/normal/toe/up axes, with a human-visible registration render.
11. **Several checks share OPS or the same camera datum and are described as independent.**
    Agreement must be labelled according to shared inputs.

## Recommended order of work

1. Freeze the 21-shot replay and add per-frame TX1, TX3, separate-midpoint, range bin,
   weight, and target-identity diagnostics to every club-path result.
2. Build a separate-reference club-path validation arm. Keep it fail-closed and do not
   change the production phase-span threshold yet.
3. Collect a rigid common-target swing or pendulum visible to camera and radar, followed by
   a blind reference session. Validate path, AoA, timing, and same-point residuals together.
4. Use sensor exposure timestamps and a calibrated camera/radar clock map. Estimate contact
   inside an interval and propagate it through every club metric.
5. Keep the five-parameter speed-constrained rotation as the optical physical model, but
   withhold pose unless its axis is stable under radius/calibration perturbations and the
   exact overlay is physically credible.
6. Replace the 60 px shaft seed with a geometry-aware head/hosel/shaft model, then refit
   after rendering exposure-integrated hypotheses.
7. Measure the actual 7-iron and 9-iron head dimensions. Do not scale the mesh from current
   area ratios.
8. Before an optics purchase, record a real 1:1 ambient-light exposure ladder with lux,
   blur, segmentation stability, and face-angle basin. No strobe is assumed.
9. Report ball metrics, D-plane estimates, and measured club/strike metrics as separate
   claim classes. Face angle remains experimental; impact location remains withheld.

## Reproducibility files

- `research/silhouette_poc/falsification/test_rigid_rotation_constrained.py`
- `research/silhouette_poc/replay/rigid_motion.py`
- `research/silhouette_poc/falsification/test_club_phase_walk.py`
- `research/silhouette_poc/falsification/test3_4_club_stability.py`
- `research/silhouette_poc/falsification/test_area_excess_sources.py`
- `research/silhouette_poc/falsification/test_fit_sequence_abc.py`
- `research/silhouette_poc/falsification/test_resolution_scaling_check.py`
- `research/silhouette_poc/falsification/test6_7_11_dplane_envelopes.py`
- `research/silhouette_poc/falsification/test10_same_point_consistency.py`
- `research/silhouette_poc/falsification/test8_geometry_perturbation.py`

The comparator set remains Trackman 4, Full Swing KIT, and Mevo Gen 2. No Trackman iO
claim is used. The optical recommendations assume ambient light and no strobe.
