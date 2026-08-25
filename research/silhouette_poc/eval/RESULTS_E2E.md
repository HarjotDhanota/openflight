# End-to-end silhouette fusion evaluation

**AMBIENT 500 us: UNDECIDED** — material Phase 1b disagreement must be diagnosed before interpreting the gate.

Evaluation hash: `40bb77515c114ec341eabb3ca8a92e3df2b69bd7ca4f2b7eadf1bbed49ff87f1`

Every core cell uses the full immutable artifact path and production fusion solver.
Headline cells include registered per-club template variation (driver ±8%, 7-iron
±10%). Zero-mismatch controls isolate the frozen Phase 1b estimator. Both use one
strictly pre-impact frame; multi-frame temporal behavior is tested separately.
Rejected shots remain in the solve-rate denominator. median AND p90 are reported.

## Spec section 1 criteria — actual end-to-end results

| Club | Candidate | N | Solve rate | Vector median mm | Vector p90 mm | Signed horizontal median/p90 mm | Signed vertical median/p90 mm | IoU median | Fit residual median px | Quality rejection | Result |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| poc_driver | strobed_10us | 200 | 0.930 | 10.10 | 17.39 | 2.62/7.26 | -8.84/-3.48 | 0.923 | 5.75 | 0.050 | FAIL |
| poc_driver | ambient_500us | 200 | 0.975 | 10.62 | 18.44 | 2.47/7.33 | -10.12/-5.06 | 0.932 | 5.85 | 0.000 | FAIL |
| poc_7iron | strobed_10us | 200 | 1.000 | 6.08 | 13.38 | 1.35/5.03 | -5.59/-0.28 | 0.911 | 4.77 | 0.000 | PASS |
| poc_7iron | ambient_500us | 200 | 1.000 | 7.55 | 14.82 | 1.90/5.48 | -6.78/-1.02 | 0.896 | 4.82 | 0.000 | PASS |

## Ambient 500 us verdict

**UNDECIDED** — material Phase 1b disagreement must be diagnosed before interpreting the gate.

The ambient candidate uses 21-sample exposure integration at 500 us and the same
artifact loader, segmentation, exposure-template fit, radar solve, and temporal gates as
the strobed candidate. It is preferred Phase-A hardware only when this verdict is YES.

## Phase 1b reconciliation

**BUG_UNRESOLVED**

Material disagreement limits were frozen before this run: 0.10 solve rate, 2 mm median,
and 4 mm p90 absolute delta.

| Club | Candidate | Solve delta | Median delta mm | p90 delta mm | Status |
|---|---|---:|---:|---:|---|
| poc_driver | strobed_10us | -0.063 | 8.41 | 14.35 | BUG_UNRESOLVED |
| poc_driver | ambient_500us | -0.022 | 8.97 | 15.27 | BUG_UNRESOLVED |
| poc_7iron | strobed_10us | 0.000 | 4.22 | 9.77 | BUG_UNRESOLVED |
| poc_7iron | ambient_500us | 0.000 | 5.65 | 11.06 | BUG_UNRESOLVED |

### Zero-mismatch reconciliation controls

| Club | Candidate | Solve | Median mm | p90 mm | Phase 1b status |
|---|---|---:|---:|---:|---|
| poc_driver | strobed_10us | 0.995 | 9.83 | 17.17 | MATERIAL_DISAGREEMENT |
| poc_driver | ambient_500us | 0.985 | 11.09 | 18.30 | MATERIAL_DISAGREEMENT |
| poc_7iron | strobed_10us | 1.000 | 6.23 | 13.13 | MATERIAL_DISAGREEMENT |
| poc_7iron | ambient_500us | 1.000 | 7.72 | 14.72 | MATERIAL_DISAGREEMENT |

A headline-cell disagreement is diagnosed as the registered template-dimension
model gap only when its paired zero-mismatch control agrees with Phase 1b.

## Failure taxonomy

- `poc_driver/strobed_10us`: silhouette_fit_residual:10, visibility_ball:4
- `poc_driver/ambient_500us`: visibility_ball:5
- `poc_7iron/strobed_10us`: none:0
- `poc_7iron/ambient_500us`: none:0

## Degradation curves

![Median and p90 degradation curves](degradation_curves.svg)

The committed JSON is the canonical curve data. The tables below retain solve rate,
median, and p90 at every sampled point.

### template_variation_fraction

| Club | Candidate | Value | N | Solve | Median mm | p90 mm | Failures |
|---|---|---:|---:|---:|---:|---:|---|
| poc_driver | strobed_10us | 0.000 | 24 | 1.000 | 10.24 | 16.52 | none |
| poc_driver | strobed_10us | 0.025 | 24 | 1.000 | 10.82 | 17.24 | none |
| poc_driver | strobed_10us | 0.050 | 24 | 1.000 | 10.98 | 17.41 | none |
| poc_driver | strobed_10us | 0.075 | 24 | 0.958 | 11.62 | 17.79 | silhouette_fit_residual:1 |
| poc_driver | strobed_10us | 0.100 | 24 | 0.917 | 11.07 | 17.98 | silhouette_fit_residual:2 |
| poc_driver | strobed_10us | 0.150 | 24 | 0.542 | 10.85 | 16.28 | silhouette_fit_residual:11 |
| poc_driver | ambient_500us | 0.000 | 24 | 0.958 | 11.79 | 17.61 | visibility_ball:1 |
| poc_driver | ambient_500us | 0.025 | 24 | 0.958 | 11.85 | 16.79 | visibility_ball:1 |
| poc_driver | ambient_500us | 0.050 | 24 | 0.958 | 10.76 | 16.79 | visibility_ball:1 |
| poc_driver | ambient_500us | 0.075 | 24 | 0.958 | 10.76 | 17.24 | visibility_ball:1 |
| poc_driver | ambient_500us | 0.100 | 24 | 0.958 | 12.09 | 18.06 | visibility_ball:1 |
| poc_driver | ambient_500us | 0.150 | 24 | 0.958 | 10.71 | 18.02 | visibility_ball:1 |
| poc_7iron | strobed_10us | 0.000 | 24 | 1.000 | 5.22 | 11.65 | none |
| poc_7iron | strobed_10us | 0.025 | 24 | 1.000 | 5.26 | 10.76 | none |
| poc_7iron | strobed_10us | 0.050 | 24 | 1.000 | 5.48 | 11.28 | none |
| poc_7iron | strobed_10us | 0.075 | 24 | 1.000 | 5.60 | 11.22 | none |
| poc_7iron | strobed_10us | 0.100 | 24 | 1.000 | 5.80 | 11.22 | none |
| poc_7iron | strobed_10us | 0.150 | 24 | 1.000 | 6.22 | 11.56 | none |
| poc_7iron | ambient_500us | 0.000 | 24 | 1.000 | 7.11 | 12.06 | none |
| poc_7iron | ambient_500us | 0.025 | 24 | 1.000 | 7.34 | 12.80 | none |
| poc_7iron | ambient_500us | 0.050 | 24 | 1.000 | 6.96 | 13.21 | none |
| poc_7iron | ambient_500us | 0.075 | 24 | 1.000 | 7.40 | 12.82 | none |
| poc_7iron | ambient_500us | 0.100 | 24 | 1.000 | 6.93 | 12.85 | none |
| poc_7iron | ambient_500us | 0.150 | 24 | 1.000 | 7.10 | 12.22 | none |

### photometric_noise_sigma_dn

| Club | Candidate | Value | N | Solve | Median mm | p90 mm | Failures |
|---|---|---:|---:|---:|---:|---:|---|
| poc_driver | strobed_10us | 0.000 | 24 | 1.000 | 10.24 | 16.52 | none |
| poc_driver | strobed_10us | 0.600 | 24 | 1.000 | 10.24 | 16.52 | none |
| poc_driver | strobed_10us | 1.200 | 24 | 1.000 | 10.24 | 16.52 | none |
| poc_driver | strobed_10us | 2.400 | 24 | 1.000 | 10.24 | 16.52 | none |
| poc_driver | strobed_10us | 4.800 | 24 | 1.000 | 10.24 | 16.58 | none |
| poc_driver | strobed_10us | 9.600 | 24 | 0.125 | 114.58 | 184.87 | silhouette_fit_residual:20, visibility_club:1 |
| poc_driver | ambient_500us | 0.000 | 24 | 0.958 | 11.64 | 17.61 | visibility_ball:1 |
| poc_driver | ambient_500us | 0.600 | 24 | 0.958 | 11.79 | 17.61 | visibility_ball:1 |
| poc_driver | ambient_500us | 1.200 | 24 | 0.958 | 11.79 | 17.61 | visibility_ball:1 |
| poc_driver | ambient_500us | 2.400 | 24 | 1.000 | 11.96 | 18.26 | none |
| poc_driver | ambient_500us | 4.800 | 24 | 1.000 | 11.32 | 18.01 | none |
| poc_driver | ambient_500us | 9.600 | 24 | 0.292 | 59.44 | 106.60 | silhouette_fit_residual:13, visibility_club:4 |
| poc_7iron | strobed_10us | 0.000 | 24 | 1.000 | 5.22 | 11.65 | none |
| poc_7iron | strobed_10us | 0.600 | 24 | 1.000 | 5.22 | 11.65 | none |
| poc_7iron | strobed_10us | 1.200 | 24 | 1.000 | 5.22 | 11.65 | none |
| poc_7iron | strobed_10us | 2.400 | 24 | 1.000 | 5.22 | 11.65 | none |
| poc_7iron | strobed_10us | 4.800 | 24 | 1.000 | 5.35 | 11.65 | none |
| poc_7iron | strobed_10us | 9.600 | 24 | 0.333 | 14.25 | 61.83 | silhouette_fit_residual:16 |
| poc_7iron | ambient_500us | 0.000 | 24 | 1.000 | 7.12 | 12.08 | none |
| poc_7iron | ambient_500us | 0.600 | 24 | 1.000 | 7.11 | 12.40 | none |
| poc_7iron | ambient_500us | 1.200 | 24 | 1.000 | 7.11 | 12.06 | none |
| poc_7iron | ambient_500us | 2.400 | 24 | 1.000 | 7.09 | 12.21 | none |
| poc_7iron | ambient_500us | 4.800 | 24 | 1.000 | 7.37 | 12.15 | none |
| poc_7iron | ambient_500us | 9.600 | 24 | 0.292 | 35.10 | 67.39 | silhouette_fit_residual:12, visibility_club:5 |

### radar_residual_mm

| Club | Candidate | Value | N | Solve | Median mm | p90 mm | Failures |
|---|---|---:|---:|---:|---:|---:|---|
| poc_driver | strobed_10us | -40.000 | 24 | 1.000 | 14.81 | 20.29 | none |
| poc_driver | strobed_10us | -20.000 | 24 | 1.000 | 12.67 | 18.50 | none |
| poc_driver | strobed_10us | -10.000 | 24 | 1.000 | 11.49 | 17.45 | none |
| poc_driver | strobed_10us | 0.000 | 24 | 1.000 | 10.24 | 16.52 | none |
| poc_driver | strobed_10us | 10.000 | 24 | 1.000 | 9.07 | 15.87 | none |
| poc_driver | strobed_10us | 20.000 | 24 | 1.000 | 7.87 | 14.67 | none |
| poc_driver | strobed_10us | 40.000 | 24 | 1.000 | 6.59 | 12.89 | none |
| poc_driver | ambient_500us | -40.000 | 24 | 0.958 | 15.69 | 20.73 | visibility_ball:1 |
| poc_driver | ambient_500us | -20.000 | 24 | 0.958 | 14.49 | 19.21 | visibility_ball:1 |
| poc_driver | ambient_500us | -10.000 | 24 | 0.958 | 13.03 | 17.94 | visibility_ball:1 |
| poc_driver | ambient_500us | 0.000 | 24 | 0.958 | 11.79 | 17.61 | visibility_ball:1 |
| poc_driver | ambient_500us | 10.000 | 24 | 0.958 | 11.05 | 16.52 | visibility_ball:1 |
| poc_driver | ambient_500us | 20.000 | 24 | 0.958 | 9.60 | 15.56 | visibility_ball:1 |
| poc_driver | ambient_500us | 40.000 | 24 | 0.958 | 7.75 | 14.56 | visibility_ball:1 |
| poc_7iron | strobed_10us | -40.000 | 24 | 1.000 | 9.67 | 15.99 | none |
| poc_7iron | strobed_10us | -20.000 | 24 | 1.000 | 7.45 | 13.98 | none |
| poc_7iron | strobed_10us | -10.000 | 24 | 1.000 | 6.37 | 12.92 | none |
| poc_7iron | strobed_10us | 0.000 | 24 | 1.000 | 5.22 | 11.65 | none |
| poc_7iron | strobed_10us | 10.000 | 24 | 1.000 | 4.16 | 10.70 | none |
| poc_7iron | strobed_10us | 20.000 | 24 | 1.000 | 3.33 | 9.63 | none |
| poc_7iron | strobed_10us | 40.000 | 24 | 1.000 | 4.22 | 7.44 | none |
| poc_7iron | ambient_500us | -40.000 | 24 | 1.000 | 11.08 | 17.00 | none |
| poc_7iron | ambient_500us | -20.000 | 24 | 1.000 | 8.91 | 14.30 | none |
| poc_7iron | ambient_500us | -10.000 | 24 | 1.000 | 8.73 | 13.36 | none |
| poc_7iron | ambient_500us | 0.000 | 24 | 1.000 | 7.11 | 12.06 | none |
| poc_7iron | ambient_500us | 10.000 | 24 | 1.000 | 5.93 | 11.37 | none |
| poc_7iron | ambient_500us | 20.000 | 24 | 1.000 | 5.02 | 10.69 | none |
| poc_7iron | ambient_500us | 40.000 | 24 | 1.000 | 4.32 | 8.83 | none |

### sync_offset_us
| Club | Candidate | Value | N | Solve | Median mm | p90 mm | Failures |
|---|---|---:|---:|---:|---:|---:|---|
| poc_driver | strobed_10us | -1000.000 | 24 | 1.000 | 28.49 | 32.50 | none |
| poc_driver | strobed_10us | -500.000 | 24 | 1.000 | 19.22 | 24.78 | none |
| poc_driver | strobed_10us | -250.000 | 24 | 1.000 | 14.55 | 21.01 | none |
| poc_driver | strobed_10us | 0.000 | 24 | 1.000 | 10.24 | 16.52 | none |
| poc_driver | strobed_10us | 250.000 | 24 | 1.000 | 6.89 | 12.38 | none |
| poc_driver | strobed_10us | 500.000 | 24 | 0.000 | — | — | extrapolation_horizon:24 |
| poc_driver | strobed_10us | 1000.000 | 24 | 0.000 | — | — | extrapolation_horizon:24 |
| poc_driver | ambient_500us | -1000.000 | 24 | 0.958 | 29.79 | 34.24 | visibility_ball:1 |
| poc_driver | ambient_500us | -500.000 | 24 | 0.958 | 20.81 | 25.74 | visibility_ball:1 |
| poc_driver | ambient_500us | -250.000 | 24 | 0.958 | 16.18 | 21.39 | visibility_ball:1 |
| poc_driver | ambient_500us | 0.000 | 24 | 0.958 | 11.79 | 17.61 | visibility_ball:1 |
| poc_driver | ambient_500us | 250.000 | 24 | 0.958 | 8.16 | 12.55 | visibility_ball:1 |
| poc_driver | ambient_500us | 500.000 | 24 | 0.000 | — | — | extrapolation_horizon:24 |
| poc_driver | ambient_500us | 1000.000 | 24 | 0.000 | — | — | extrapolation_horizon:24 |
| poc_7iron | strobed_10us | -1000.000 | 24 | 1.000 | 22.54 | 28.11 | none |
| poc_7iron | strobed_10us | -500.000 | 24 | 1.000 | 13.82 | 19.81 | none |
| poc_7iron | strobed_10us | -250.000 | 24 | 1.000 | 9.69 | 15.80 | none |
| poc_7iron | strobed_10us | 0.000 | 24 | 1.000 | 5.22 | 11.65 | none |
| poc_7iron | strobed_10us | 250.000 | 24 | 1.000 | 3.52 | 7.97 | none |
| poc_7iron | strobed_10us | 500.000 | 24 | 0.000 | — | — | extrapolation_horizon:24 |
| poc_7iron | strobed_10us | 1000.000 | 24 | 0.000 | — | — | extrapolation_horizon:24 |
| poc_7iron | ambient_500us | -1000.000 | 24 | 1.000 | 24.34 | 29.61 | none |
| poc_7iron | ambient_500us | -500.000 | 24 | 1.000 | 15.32 | 21.07 | none |
| poc_7iron | ambient_500us | -250.000 | 24 | 1.000 | 10.82 | 17.08 | none |
| poc_7iron | ambient_500us | 0.000 | 24 | 1.000 | 7.11 | 12.06 | none |
| poc_7iron | ambient_500us | 250.000 | 24 | 1.000 | 4.14 | 8.32 | none |
| poc_7iron | ambient_500us | 500.000 | 24 | 0.000 | — | — | extrapolation_horizon:24 |
| poc_7iron | ambient_500us | 1000.000 | 24 | 0.000 | — | — | extrapolation_horizon:24 |
