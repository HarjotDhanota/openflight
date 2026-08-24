# Corrected 7-iron F1 and remediation rerun

**Registration status: FROZEN BEFORE CORRECTED OUTCOMES on 2026-08-24.**

**Outcome status: NOT RUN.**

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

Pending.
