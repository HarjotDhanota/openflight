# Corrected 7-iron F1 and remediation rerun

**Registration status: FROZEN BEFORE CORRECTED OUTCOMES on 2026-08-24.**

**Outcome status: COMPLETE — STOP_FOR_MAINTAINER_REVIEW.**

The source defect correction changes mesh admission and normalization only. The
Titleist 690CB source remains pinned to SHA-256
`f35936799295e6ce344279e557f0265ccbb8acef69c4508daff80d219d03cb85`.
It is re-imported at trusted millimetre scale, geometrically anchored by its
largest coherent extremity face-plane cluster within 15 degrees and lens aspect
0.35–0.65, and must satisfy the registered admission/provenance checks.

The corrected run is the exact 7-iron subset of the frozen F1 registration:
analytic-truth reference plus mesh-truth baseline, Arm B calibrated analytic,
and Arm A mesh-projection LUT; ambient 500 us plus comparison-only strobe 10 us;
N=200 per cell; seeds 20260824–20261023; A0; ten frames/eight pre-trigger;
calibrated 1% dimension residual; sigma 1.2 DN photometric noise; sigma 3 mm
radar noise; zero scattering residual; deterministic sigma 33 us sync jitter;
and the unchanged `AMBIENT_RECOVERY_POLICY`, residual gates, temporal gates,
and 7-iron criteria (solve >=0.80, median <=12 mm, p90 <=24 mm).

Arm B retains the byte-identical runtime solver. Arm A retains its registered
9x9 view grid, 2-degree periodic roll grid, 72-direction contour, 512-pose seed,
and frozen validation thresholds: centroid p99 <=1 px, covariance p99 <=1 px,
and contour IoU p1 >=0.95. A validation failure invalidates Arm A before shots;
no LUT density, threshold, template constant, or temporal gate changes afterward.

The report pairs every corrected iron cell with the previous distorted-axis run.
The retired Maverik driver is excluded. Driver status is `HOLD_CAD_MESH` until
the maintainer supplies an admitted CAD driver. The overall result always stops
for maintainer review before F2.

### Arm A-v2 prospective registration (revision 2.5)

**Registration status: FROZEN BEFORE ARM A-v2 VALIDATION OR SHOT OUTCOMES on
2026-08-24.** This registration appends a prospective v2 arm; it does not alter
the accepted corrected-iron outcome below.

Arm A-v2 uses the same admitted metric 690CB mesh and changes only the LUT's
representation. Its lattice is yaw `[-20, 20]` degrees in 2-degree increments,
pitch `[-20, 20]` degrees in 2-degree increments, and a closed roll interval
`[-90, 90]` degrees in 1-degree increments. The closed roll endpoint is stored
and interpolated without wrapping because the hosel-bearing mesh is not
180-degree symmetric. Centroids and 72-direction contours use trilinear
interpolation on that closed lattice. Covariances are rotated into the queried
roll's co-rotating image basis, represented as symmetric matrix logarithms,
interpolated there, exponentiated, and rotated back; this preserves positive
definiteness and removes coordinate-rotation curvature from interpolation.
The runtime solver gates, search/refinement behavior, temporal policy, and all
shot criteria remain unchanged.

Validation remains fail-closed before any shot: the same 512 poses, 7-iron seed
`2026082492`, pose ranges, native A0 rasterizer, and limits are reused unchanged:
centroid p99 <=1 px, covariance p99 <=1 px, and contour IoU p1 >=0.95. This
native-resolution validation is the registered interpolation error bound. If
any limit fails, no Arm A-v2 shot is generated and the result is reported as
invalid. If validation passes, only the corrected-iron Arm A cells run over the
same frozen F1 grid: N=200, seeds `20260824`-`20261023`, `ambient_500us` plus
`strobed_10us`, and all existing generator/fusion settings and criteria.

Per revision 2.5, only `ambient_500us` is gate-bearing. `strobed_10us` remains a
reported comparison arm and cannot pass or fail the prospective gate. The
accepted baseline and retired Arm B results remain in the paired tables without
rerun. Driver remains `HOLD_CAD_MESH`; F2 remains blocked pending review of the
Arm A-v2 iron result.

### Arm A-v3 prospective registration (exact evaluation model)

**Registration status: FROZEN BEFORE ARM A-v3 SHOT OUTCOMES on 2026-08-24.**
The maintainer accepted A-v2's fail-closed result and attributed the remaining
covariance error to near-discontinuous second moments at pathological views of
the thin blade and hosel, rather than insufficient interpolation density.

Arm A-v3 therefore has no LUT and no interpolation. At every pose hypothesis,
including each centroid-correction iteration and roll-refinement query, it
projects the admitted metric 690CB triangles through the native A0 camera and
uses the existing NumPy triangle rasterizer to compute the exact centroid
offset, covariance, and 72-direction contour. It uses the registered Arm A-v1
roll search (`[-90, 90)` degrees at 2-degree spacing) followed by the unchanged
`[-2, 2]` degree refinement at 0.25-degree spacing. The production solver's
fit-residual gate, ambiguity gate, temporal gates, fail-closed behavior, and
multi-frame policy are unchanged. The exact model is evaluation-only; the v1/v2
LUT implementations and their validation machinery remain in-tree.

LUT validation is inapplicable to A-v3 because no sampled representation is
queried: its fit model invokes the same native rasterizer directly for every
pose. This is recorded as `NOT_APPLICABLE_EXACT_MODEL`, not treated as a waived
or passing LUT bound. The corrected-iron shot registration remains unchanged:
N=200 per cell, seeds `20260824`-`20261023`, A0, ten frames/eight pre-trigger,
calibrated 1% dimension residual, sigma 1.2 DN photometric noise, sigma 3 mm
radar noise, zero scattering residual, deterministic sigma 33 us sync jitter,
the frozen mesh truth and 21-sample exposure integration, and the 7-iron gates
(solve >=0.80, median <=12 mm, p90 <=24 mm). `ambient_500us` is gate-bearing;
`strobed_10us` runs and is reported as comparison-only under revision 2.5.

Each attempted shot records `time.perf_counter()` wall time from immediately
before through immediately after `AMBIENT_RECOVERY_POLICY.solve`. This includes
archive loading, extraction, radar/temporal fusion, and all exact pose renders;
it excludes synthetic artifact generation, truth-sidecar scoring, and temporary
directory setup. The JSON retains all 200 per-shot seconds for each cell; the
report publishes total, median, p90, and maximum solve wall time. Timing is
descriptive compute-cost evidence and is not a gate. Driver remains
`HOLD_CAD_MESH`; F2 remains blocked pending maintainer review of A-v3.

## Results

**DRIVER: HOLD_CAD_MESH**

**IRON: IRON_NEITHER**

**OVERALL: STOP_FOR_MAINTAINER_REVIEW**

Evaluation hash: `5f74a9b72cab056726f4d68f7715e1420c511cb1f73c6a5f872e17b236ef37f3`

## Paired old-vs-corrected criteria

| Geometry | Arm | Candidate | Solve | Median mm | p90 mm | Solve delta vs old | Median delta mm vs old |
|---|---|---|---:|---:|---:|---:|---:|
| old distorted-axis | F1 mesh-truth baseline (analytic_truth) | strobed_10us | 1.000 | 1.103 | 2.769 | — | — |
| old distorted-axis | F1 mesh-truth baseline (analytic_truth) | ambient_500us | 1.000 | 1.090 | 2.434 | — | — |
| old distorted-axis | F1 mesh-truth baseline (mesh_truth) | strobed_10us | 0.495 | 4.005 | 5.128 | — | — |
| old distorted-axis | F1 mesh-truth baseline (mesh_truth) | ambient_500us | 0.595 | 3.659 | 5.126 | — | — |
| corrected metric CAD | F1 mesh-truth baseline (analytic_truth) | strobed_10us | 1.000 | 0.965 | 1.636 | 0.000 | -0.138 |
| corrected metric CAD | F1 mesh-truth baseline (analytic_truth) | ambient_500us | 1.000 | 1.116 | 1.937 | 0.000 | 0.025 |
| corrected metric CAD | F1 mesh-truth baseline (mesh_truth) | strobed_10us | 0.000 | — | — | -0.495 | — |
| corrected metric CAD | F1 mesh-truth baseline (mesh_truth) | ambient_500us | 0.000 | — | — | -0.595 | — |
| old distorted-axis | Arm B calibrated analytic | strobed_10us | 0.495 | 4.485 | 6.424 | — | — |
| old distorted-axis | Arm B calibrated analytic | ambient_500us | 0.600 | 2.568 | 3.794 | — | — |
| corrected metric CAD | Arm B calibrated analytic | strobed_10us | 0.000 | — | — | -0.495 | — |
| corrected metric CAD | Arm B calibrated analytic | ambient_500us | 0.915 | 15.427 | 20.990 | 0.315 | 12.859 |
| corrected metric CAD | Arm A mesh projection | — | — | — | — | — | — |

## Arm A LUT validation

| Geometry | Centroid p99 px | Covariance p99 px | Contour IoU p1 | Result |
|---|---:|---:|---:|---|
| old distorted-axis | 0.255 | 2.091 | 0.975 | FAIL |
| corrected metric CAD | 0.296 | 2.984 | 0.980 | FAIL |

## Offline Arm B calibration

| Geometry | Fitted analytic radii (u x v) mm |
|---|---:|
| old distorted-axis | 40.506 x 22.620 |
| corrected metric CAD | 59.863 x 37.500 |

## Corrected signed errors and diagnostics

| Arm | Candidate | Offset median/p90 mm | Height median/p90 mm | IoU median/p10 | Fit residual median/p90 px |
|---|---|---:|---:|---:|---:|
| F1 mesh-truth baseline (analytic_truth) | strobed_10us | 0.067/0.958 | -0.040/1.104 | 0.968/0.949 | 2.779/3.355 |
| F1 mesh-truth baseline (analytic_truth) | ambient_500us | 0.264/1.243 | -0.408/0.986 | 0.958/0.890 | 3.091/3.687 |
| F1 mesh-truth baseline (mesh_truth) | strobed_10us | —/— | —/— | —/— | —/— |
| F1 mesh-truth baseline (mesh_truth) | ambient_500us | —/— | —/— | —/— | —/— |
| Arm B calibrated analytic | strobed_10us | —/— | —/— | —/— | —/— |
| Arm B calibrated analytic | ambient_500us | 10.002/18.031 | -8.933/-1.039 | 0.750/0.741 | 11.136/11.494 |

## Corrected rejection taxonomy

- `F1 mesh-truth baseline (analytic_truth)/strobed_10us`: none
- `F1 mesh-truth baseline (analytic_truth)/ambient_500us`: none
- `F1 mesh-truth baseline (mesh_truth)/strobed_10us`: insufficient_temporal_frames:1, silhouette_fit_residual:199
- `F1 mesh-truth baseline (mesh_truth)/ambient_500us`: insufficient_temporal_frames:2, silhouette_fit_residual:198
- `Arm B calibrated analytic/strobed_10us`: insufficient_temporal_frames:1, silhouette_fit_residual:199
- `Arm B calibrated analytic/ambient_500us`: insufficient_temporal_frames:2, silhouette_fit_residual:15
- Arm A: no shot taxonomy; frozen LUT validation failed before evaluation.

## Arm A-v3 exact-model result

**ARM A-v3 IRON GATE: IRON_A_V3_CLEARS_AMBIENT**

Previous evaluation hash: `a081f3e53810c2e29bfa083990a87ed0eac5d31536872ab95d3fc6370c21b10d`

LUT validation: **NOT_APPLICABLE_EXACT_MODEL**. Every pose hypothesis was rasterized exactly; the retained LUT validation machinery was not invoked.

### Paired Arm A-v1/v2/v3 criteria

| Model | Candidate | Gate role | Solve | Median mm | p90 mm |
|---|---|---|---:|---:|---:|
| v1 LUT invalid | — | — | — | — | — |
| v3 exact | strobed_10us | comparison-only | 0.995 | 0.938 | 1.631 |
| v3 exact | ambient_500us | primary-gate | 0.990 | 1.050 | 1.767 |

### Arm A-v3 signed errors and diagnostics

| Candidate | Offset median/p90 mm | Height median/p90 mm | IoU median/p10 | Fit residual median/p90 px |
|---|---:|---:|---:|---:|
| strobed_10us | 0.074/0.720 | 0.027/1.041 | 0.985/0.972 | 2.144/3.184 |
| ambient_500us | -0.134/0.652 | 0.285/1.402 | 0.966/0.938 | 2.388/3.500 |

### Solve wall-time (all attempted shots)

| Candidate | N | Total s | Median s | p90 s | Max s |
|---|---:|---:|---:|---:|---:|
| strobed_10us | 200 | 7358.341 | 36.527 | 38.184 | 54.307 |
| ambient_500us | 200 | 7449.625 | 36.988 | 38.706 | 56.436 |

### Arm A-v3 rejection taxonomy

- `strobed_10us` (comparison_only): insufficient_temporal_frames:1
- `ambient_500us` (primary_gate): insufficient_temporal_frames:2

## Decision

The accepted historical iron result remains **IRON_NEITHER**. The exact-model prospective result is **IRON_A_V3_CLEARS_AMBIENT**; only ambient 500 us determined its gate and strobe remained comparison-only. Driver remains **HOLD_CAD_MESH**, the work order is **STOP_FOR_MAINTAINER_REVIEW**, and F2 remains blocked.
