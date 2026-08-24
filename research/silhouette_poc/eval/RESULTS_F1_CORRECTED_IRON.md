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

## Results

**DRIVER: HOLD_CAD_MESH**

**IRON: IRON_NEITHER**

**OVERALL: STOP_FOR_MAINTAINER_REVIEW**

Evaluation hash: `9f673a63a8f08bcbb6ede8d25ac2604e8e1972324e0330fc63aed9a174b9eff4`

## Paired old-vs-corrected criteria

| Geometry | Arm | Candidate | Solve | Median mm | p90 mm | Solve delta vs old | Median delta mm vs old |
|---|---|---|---:|---:|---:|---:|---:|
| old distorted-axis | F1 mesh-truth baseline (analytic_truth) | strobed_10us | 1.000 | 1.103 | 2.769 | — | — |
| old distorted-axis | F1 mesh-truth baseline (analytic_truth) | ambient_500us | 1.000 | 1.090 | 2.434 | — | — |
| old distorted-axis | F1 mesh-truth baseline (mesh_truth) | strobed_10us | 0.495 | 4.005 | 5.128 | — | — |
| old distorted-axis | F1 mesh-truth baseline (mesh_truth) | ambient_500us | 0.595 | 3.659 | 5.126 | — | — |
| corrected metric CAD | F1 mesh-truth baseline (analytic_truth) | strobed_10us | 1.000 | 1.103 | 2.769 | 0.000 | 0.000 |
| corrected metric CAD | F1 mesh-truth baseline (analytic_truth) | ambient_500us | 1.000 | 1.090 | 2.434 | 0.000 | 0.000 |
| corrected metric CAD | F1 mesh-truth baseline (mesh_truth) | strobed_10us | 0.000 | — | — | -0.495 | — |
| corrected metric CAD | F1 mesh-truth baseline (mesh_truth) | ambient_500us | 0.000 | — | — | -0.595 | — |
| old distorted-axis | Arm B calibrated analytic | strobed_10us | 0.495 | 4.485 | 6.424 | — | — |
| old distorted-axis | Arm B calibrated analytic | ambient_500us | 0.600 | 2.568 | 3.794 | — | — |
| corrected metric CAD | Arm B calibrated analytic | strobed_10us | 0.000 | — | — | -0.495 | — |
| corrected metric CAD | Arm B calibrated analytic | ambient_500us | 0.905 | 10.587 | 15.622 | 0.305 | 8.019 |
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
| F1 mesh-truth baseline (analytic_truth) | strobed_10us | 0.329/1.963 | -0.447/1.061 | 0.959/0.944 | 3.048/3.580 |
| F1 mesh-truth baseline (analytic_truth) | ambient_500us | 0.107/1.215 | -0.332/1.044 | 0.960/0.947 | 3.830/4.498 |
| F1 mesh-truth baseline (mesh_truth) | strobed_10us | —/— | —/— | —/— | —/— |
| F1 mesh-truth baseline (mesh_truth) | ambient_500us | —/— | —/— | —/— | —/— |
| Arm B calibrated analytic | strobed_10us | —/— | —/— | —/— | —/— |
| Arm B calibrated analytic | ambient_500us | 5.135/12.873 | -4.810/4.989 | 0.809/0.762 | 10.122/10.710 |

## Corrected rejection taxonomy

- `F1 mesh-truth baseline (analytic_truth)/strobed_10us`: none
- `F1 mesh-truth baseline (analytic_truth)/ambient_500us`: none
- `F1 mesh-truth baseline (mesh_truth)/strobed_10us`: insufficient_temporal_frames:1, silhouette_fit_residual:199
- `F1 mesh-truth baseline (mesh_truth)/ambient_500us`: insufficient_temporal_frames:2, silhouette_fit_residual:198
- `Arm B calibrated analytic/strobed_10us`: insufficient_temporal_frames:1, silhouette_fit_residual:199
- `Arm B calibrated analytic/ambient_500us`: insufficient_temporal_frames:2, silhouette_fit_residual:16, temporal_acceleration:1
- Arm A: no shot taxonomy; frozen LUT validation failed before evaluation.

## Decision

The provisional iron result is **IRON_NEITHER**. Driver remains **HOLD_CAD_MESH**. The overall work order is **STOP_FOR_MAINTAINER_REVIEW**; no F2 work began.
