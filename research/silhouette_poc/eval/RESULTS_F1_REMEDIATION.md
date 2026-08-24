# Phase F1 template-remediation gate

**Registration status: FROZEN BEFORE ARM B OR ARM A OUTCOMES on 2026-08-24.**

**Outcome status: COMPLETE — `STOP_NEITHER`.** Arm B ran all four frozen cells;
Arm A failed its frozen interpolation validation before any grid shot and was
invalidated without retuning. The F1 baseline remains the fixed comparison
identified by evaluation hash
`4ba26fb19d378753cc28cffc79610b3b9a7f7c2e3f7e2885b06466be858fa9d1`.
The F1 criteria, cells, N, seeds, artifact path, production segmentation, radar
model, and temporal gates were unchanged.

## Frozen arms and order

Arm B runs first. Arm A runs second unless the maintainer elects to exercise the
registered option to stop after B clears every cell. This run will evaluate both
arms so the requested side-by-side comparison is complete.

### Arm B: mesh-calibrated analytic template

Only `ClubTemplate.radius_u_mm` and `ClubTemplate.radius_v_mm` are exposed shape
parameters, so those are the only fitted constants. `fusion/solver.py`, including
`solve_club_state`, remains byte-identical. The calibrated `ClubTemplate` retains
the registered name, speed distribution, impact bounds, and velocity direction.

Calibration uses 256 deterministic, non-evaluation, fully visible poses per club. Driver seed is
2026082401; 7-iron seed is 2026082402. Each pose samples an impact center with
x=-8..+8 mm, y=-20..+20 mm, z=-5..+10 mm; a pre-impact time uniformly from
-15..-2 ms; registered nominal club speed and velocity direction; and roll
uniformly from -15..+15 degrees. Exposure and ball are absent from calibration.
The normalized nominal mesh is projected through camera A0 at each pose.

**Pre-outcome registration erratum:** the first implementation attempted to use
the first 256 draws directly; 91 driver and 115 iron projections were empty, so
no objective or calibration constants were produced and evaluation never began.
The frozen visibility rule is now explicit: generate a pool of 2,048 candidates
from the same club seed and distributions, then accept the first 256 whose mesh
projection is nonempty and touches no A0 image boundary. If fewer than 256 qualify,
calibration fails closed. Rejected candidate indices and the last examined index
are recorded. This repairs an undefined objective; no evaluation rule changed.

For each pose, the target is the mesh mask's pixel covariance and centroid. The
objective is the mean squared normalized Frobenius covariance disagreement,
`||C_mesh-C_analytic||_F^2 / trace(C_mesh)^2`, plus the normalized squared
centroid disagreement. The centroid term is reported but constant with respect to
the analytic radii; it prevents the calibration report from hiding irreducible
shape-centroid bias. SciPy L-BFGS-B minimizes log radii from the existing nominal
constants, bounded independently to 0.50..1.50 times nominal, with `ftol=1e-12`,
`gtol=1e-10`, and at most 500 iterations. The pose/config hash, input asset hash,
fitted constants, initial/final objectives, and centroid floor are committed.
There is no evaluation-informed adjustment or manual override.

### Arm A: mesh-projection moment/contour template

The ignored local mesh asset is precomputed into an ignored LUT; the local-use
690CB geometry and its shape-bearing LUT are not committed. A0 projections use:

- view yaw and pitch: -20 through +20 degrees, every 5 degrees (9x9 views);
- roll: -90 through +88 degrees, every 2 degrees (90 samples);
- canonical camera range: 1,500 mm;
- convex contour: 72 radial directions around the projected mesh centroid;
- moment fields: centroid offset and 2x2 covariance from the actual projected
  triangle-union mask.

Runtime uses trilinear interpolation in yaw, pitch, and periodic roll, scaled by
camera depth. It searches all 2-degree roll knots, then a +/-2-degree refinement
at 0.25-degree spacing. For every hypothesis, the observed centroid is corrected
by the interpolated mesh centroid offset before radar-range backprojection; mesh
covariance receives the unchanged exposure-blur covariance term. Fit residual is
the same square-root Frobenius statistic and uses the unchanged 8 px sharp / 12
px ambient admission limits. A view outside +/-20 degrees fails closed as
`mesh_lut_view_bounds`. Temporal selection and gates are not part of the LUT and
are unchanged.

**Pre-outcome registration clarification:** the first LUT build failed before
producing a LUT or evaluating a shot because placing the projected club center at
the +/-20-degree view knots puts it outside A0's approximately +/-8.8-degree
horizontal field. “View yaw and pitch” are therefore implemented as virtual
off-axis perspectives recentered on A0's image plane: project at the registered
view direction and 1,500 mm camera depth, translate every projected vertex by
the same amount that places the projected club origin at `(cx, cy)`, then rasterize.
This separates perspective from crop position and leaves the registered view
grid, native A0 rasterizer, moment/contour features, runtime interpolation, and
held-out validation unchanged. No LUT or evaluation outcome existed when this
clarification was frozen.

Before evaluation, 512 deterministic held-out poses per club (seeds 2026082491
and 2026082492) uniformly sample yaw/pitch within +/-18 degrees and roll within
/-90 degrees. Against exact triangle projection at native A0 resolution, the LUT
must achieve p99 centroid error <=1.0 px, p99 square-root Frobenius covariance
error <=1.0 px, and p1 convex-contour IoU >=0.95. Failure invalidates Arm A before
outcomes; density is not changed after this registration.

## Frozen evaluation and decision

Each remediation arm uses mesh truth for the exact four F1 cells: driver and
7-iron, ambient 500 us primary and strobe 10 us comparison-only, N=200, seeds
20260824 through 20261023, A0, ten frames/eight pre-trigger, calibrated 1%
dimension residual, sigma 1.2 DN photometric noise, sigma 3 mm radar noise, zero
scattering residual, deterministic sigma 33 us sync jitter, and the byte-identical
`AMBIENT_RECOVERY_POLICY` temporal thresholds.

Unchanged criteria:

| Club | Median vector error | p90 vector error | Solve rate |
|---|---:|---:|---:|
| Driver | <=10 mm | <=20 mm | >=0.80 |
| 7-iron | <=12 mm | <=24 mm | >=0.80 |

The report includes analytic-truth reference, uncalibrated mesh-truth F1 baseline,
Arm B, and Arm A side by side, with signed errors, IoU, fit residual, visibility,
and complete rejection taxonomy for every arm.

Decision precedence is frozen:

1. Arm B clears all four cells: `SHIP_B`; Arm A is optional evidence only.
2. B fails and Arm A clears all four cells: `SHIP_A`.
3. Neither clears all four cells: `STOP_NEITHER`.

No temporal-gate loosening or post-outcome threshold/template tuning is allowed.

## Results

**F1 REMEDIATION GATE: STOP_NEITHER**

## Criteria comparison

| Truth / fit arm | Club | Candidate | Solve rate | Median mm | p90 mm | Status |
|---|---|---|---:|---:|---:|---|
| Analytic truth reference | poc_driver | strobed_10us | 1.000 | 0.948 | 1.861 | comparison |
| Analytic truth reference | poc_driver | ambient_500us | 1.000 | 0.931 | 1.822 | comparison |
| Analytic truth reference | poc_7iron | strobed_10us | 1.000 | 1.103 | 2.769 | comparison |
| Analytic truth reference | poc_7iron | ambient_500us | 1.000 | 1.090 | 2.434 | comparison |
| F1 baseline: analytic fit / mesh truth | poc_driver | strobed_10us | 0.550 | 9.954 | 11.436 | comparison |
| F1 baseline: analytic fit / mesh truth | poc_driver | ambient_500us | 0.660 | 8.956 | 11.050 | comparison |
| F1 baseline: analytic fit / mesh truth | poc_7iron | strobed_10us | 0.495 | 4.005 | 5.128 | comparison |
| F1 baseline: analytic fit / mesh truth | poc_7iron | ambient_500us | 0.595 | 3.659 | 5.126 | comparison |
| Arm B: calibrated analytic / mesh truth | poc_driver | strobed_10us | 0.525 | 14.265 | 15.597 | FAIL |
| Arm B: calibrated analytic / mesh truth | poc_driver | ambient_500us | 0.990 | 14.683 | 19.729 | FAIL |
| Arm B: calibrated analytic / mesh truth | poc_7iron | strobed_10us | 0.495 | 4.485 | 6.424 | FAIL |
| Arm B: calibrated analytic / mesh truth | poc_7iron | ambient_500us | 0.600 | 2.568 | 3.794 | FAIL |
| Arm A: mesh projection / mesh truth | poc_driver | strobed_10us | — | — | — | INVALID / NOT RUN |
| Arm A: mesh projection / mesh truth | poc_driver | ambient_500us | — | — | — | INVALID / NOT RUN |
| Arm A: mesh projection / mesh truth | poc_7iron | strobed_10us | — | — | — | INVALID / NOT RUN |
| Arm A: mesh projection / mesh truth | poc_7iron | ambient_500us | — | — | — | INVALID / NOT RUN |

## Arm A frozen interpolation validation

| Club | Centroid p99 px (<=1) | Covariance p99 px (<=1) | Contour IoU p1 (>=0.95) | Result |
|---|---:|---:|---:|---|
| poc_driver | 4.627 | 2.578 | 0.816 | FAIL |
| poc_7iron | 0.255 | 2.091 | 0.975 | FAIL |

## Arm B signed errors and fit diagnostics

| Club | Candidate | Offset median mm | Height median mm | IoU median | Fit residual median px |
|---|---|---:|---:|---:|---:|
| poc_driver | strobed_10us | 13.791 | 2.731 | 0.809 | 7.456 |
| poc_driver | ambient_500us | 13.670 | 3.811 | 0.802 | 7.175 |
| poc_7iron | strobed_10us | 3.900 | -1.541 | 0.798 | 4.357 |
| poc_7iron | ambient_500us | 1.014 | -1.936 | 0.823 | 5.254 |

## Rejection taxonomy

Arm A has no shot rejection taxonomy because fail-closed LUT validation prevented evaluation; reporting zero rejections would be misleading.

| Arm | Club | Candidate | Rejections |
|---|---|---|---|
| Analytic truth reference | poc_driver | strobed_10us | none |
| Analytic truth reference | poc_driver | ambient_500us | none |
| Analytic truth reference | poc_7iron | strobed_10us | none |
| Analytic truth reference | poc_7iron | ambient_500us | none |
| F1 baseline: analytic fit / mesh truth | poc_driver | strobed_10us | extrapolation_horizon=25, insufficient_temporal_frames=1, silhouette_fit_residual=64 |
| F1 baseline: analytic fit / mesh truth | poc_driver | ambient_500us | extrapolation_horizon=56, silhouette_fit_residual=12 |
| F1 baseline: analytic fit / mesh truth | poc_7iron | strobed_10us | extrapolation_horizon=83, silhouette_fit_residual=18 |
| F1 baseline: analytic fit / mesh truth | poc_7iron | ambient_500us | extrapolation_horizon=7, silhouette_fit_residual=2, temporal_acceleration=72 |
| Arm B: calibrated analytic / mesh truth | poc_driver | strobed_10us | extrapolation_horizon=34, silhouette_fit_residual=61 |
| Arm B: calibrated analytic / mesh truth | poc_driver | ambient_500us | extrapolation_horizon=2 |
| Arm B: calibrated analytic / mesh truth | poc_7iron | strobed_10us | extrapolation_horizon=83, silhouette_fit_residual=18 |
| Arm B: calibrated analytic / mesh truth | poc_7iron | ambient_500us | extrapolation_horizon=10, silhouette_fit_residual=2, temporal_acceleration=68 |

## Decision

Arm B does not clear all four unchanged cells. Arm A's registered runtime approximation is invalid under its pre-registered error bound and was not run. Under the frozen precedence rule, neither arm clears: **STOP_NEITHER**. No gate, template constant, LUT density, or temporal threshold was changed after outcomes.

Evaluation hash: `530a22e8fdbbb5c8191bfe4fe4634f0114a365f8212957350c9b45aa84761e43`
