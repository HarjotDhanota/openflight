# Phase F1 mesh-truth fidelity gate

**Registration:** frozen before outcomes on 2026-08-24; source IDs, grid, criteria, materiality, normalization, and stop rules were fixed in this file.

**Pre-outcome source amendment (2026-08-24):** the original Sketchfab iron failed strict author validation after its display name changed to Unicode fraktur. The validator was not loosened. Before any F1 outcome, the maintainer supplied the local-use-only Titleist 690CB right-handed binary STL pinned by SHA-256. This changed acquisition and provenance only; the grid, seeds, normalization, solver, thresholds, materiality, and outcome precedence stayed frozen.

**F1 GATE: TEMPLATE_COLLAPSE**

Strobe remains comparison-only. The production solver fitted its unchanged analytic template in both arms.

Evaluation hash: `4ba26fb19d378753cc28cffc79610b3b9a7f7c2e3f7e2885b06466be858fa9d1`

## Frozen gate rules

N=200 per arm/club/candidate, paired seeds 20260824 through 20261023. TEMPLATE_COLLAPSE takes precedence if any mesh cell solves below 0.80 or loses more than 0.10 solve rate against its analytic pair. Otherwise accuracy is NO-GO if driver exceeds 10 mm median or 20 mm p90, or 7-iron exceeds 12 mm median or 24 mm p90. PASS requires every mesh cell to clear all three gates.

## Source provenance

| Source | ID | License/use | Source SHA-256 | Normalized asset SHA-256 |
|---|---|---|---|---|
| Callaway Maverik golf driver | `978d0740dc514c8695bbb02f4083f0e3` | CC-BY-4.0 | `0b9fac0caa2f7f26bc7492a6e12047c1552a6f8d934f6828cc2b3537f75105a2` | `6b9ba5a70b868f61fab40d2bdf11b7c355204b612f17efde76500b45b5308dc1` |
| Titleist 690CB 7-iron golf club | `grabcad:titleist-7-iron-golf-club-1:690cb-right-handed` | LicenseRef-GrabCAD-Local-Research-Only | `f35936799295e6ce344279e557f0265ccbb8acef69c4508daff80d219d03cb85` | `47847962459fc55793e66274f2577226e6d17debb962fe451a3e9066b5926575` |

## Criteria table

| Truth | Club | Candidate | N | Solve | Median mm | p90 mm | Signed horizontal median/p90 mm | Signed vertical median/p90 mm | IoU median/p10 | Fit residual median/p90 px | Visibility failures |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| analytic_truth | poc_driver | strobed_10us | 200 | 1.000 | 0.95 | 1.86 | 0.06/1.01 | -0.09/1.15 | 0.967/0.957 | 3.31/4.22 | 0 |
| analytic_truth | poc_driver | ambient_500us | 200 | 1.000 | 0.93 | 1.82 | 0.08/1.06 | -0.15/1.06 | 0.975/0.967 | 4.30/5.11 | 0 |
| analytic_truth | poc_7iron | strobed_10us | 200 | 1.000 | 1.10 | 2.77 | 0.33/1.96 | -0.45/1.06 | 0.959/0.944 | 3.05/3.58 | 0 |
| analytic_truth | poc_7iron | ambient_500us | 200 | 1.000 | 1.09 | 2.43 | 0.11/1.22 | -0.33/1.04 | 0.960/0.947 | 3.83/4.50 | 0 |
| mesh_truth | poc_driver | strobed_10us | 200 | 0.550 | 9.95 | 11.44 | 9.46/11.26 | 2.96/5.31 | 0.762/0.758 | 7.16/7.52 | 0 |
| mesh_truth | poc_driver | ambient_500us | 200 | 0.660 | 8.96 | 11.05 | 7.91/10.13 | 3.52/6.61 | 0.771/0.762 | 5.91/7.66 | 0 |
| mesh_truth | poc_7iron | strobed_10us | 200 | 0.495 | 4.00 | 5.13 | 2.35/3.79 | -2.91/-1.24 | 0.803/0.795 | 3.71/4.15 | 0 |
| mesh_truth | poc_7iron | ambient_500us | 200 | 0.595 | 3.66 | 5.13 | 0.99/2.12 | -3.25/-1.53 | 0.817/0.809 | 4.16/4.59 | 0 |

## Mesh-minus-analytic reconciliation

| Club | Candidate | Solve delta | Median delta mm | p90 delta mm |
|---|---|---:|---:|---:|
| poc_driver | strobed_10us | -0.450 | 9.01 | 9.57 |
| poc_driver | ambient_500us | -0.340 | 8.03 | 9.23 |
| poc_7iron | strobed_10us | -0.505 | 2.90 | 2.36 |
| poc_7iron | ambient_500us | -0.405 | 2.57 | 2.69 |

## Rejection taxonomy

- `analytic_truth/poc_7iron/ambient_500us`: none
- `analytic_truth/poc_7iron/strobed_10us`: none
- `analytic_truth/poc_driver/ambient_500us`: none
- `analytic_truth/poc_driver/strobed_10us`: none
- `mesh_truth/poc_7iron/ambient_500us`: extrapolation_horizon:7, silhouette_fit_residual:2, temporal_acceleration:72
- `mesh_truth/poc_7iron/strobed_10us`: extrapolation_horizon:83, silhouette_fit_residual:18
- `mesh_truth/poc_driver/ambient_500us`: extrapolation_horizon:56, silhouette_fit_residual:12
- `mesh_truth/poc_driver/strobed_10us`: extrapolation_horizon:25, insufficient_temporal_frames:1, silhouette_fit_residual:64

## Gate reasons

- poc_driver/strobed_10us: mesh solve=0.550, paired loss=0.450
- poc_driver/ambient_500us: mesh solve=0.660, paired loss=0.340
- poc_7iron/strobed_10us: mesh solve=0.495, paired loss=0.505
- poc_7iron/ambient_500us: mesh solve=0.595, paired loss=0.405

## Frozen method

Both arms used N=200 and seeds 20260824 through 20261023 per club/candidate, A0 320x200, 10 frames with eight pre-trigger frames, calibrated 1% dimensions, sigma 1.2 DN photometric noise, sigma 3 mm radar noise, zero club residual, and deterministic sigma 33 us sync jitter. Ambient used the existing 21-sample exposure integration; strobe used three samples.

The mesh arm selected the largest compact welded connected component, assigned PCA axes by extent, independently normalized depth/width/height to the calibrated dimensions, and projected every triangle with the existing camera model using a NumPy scanline-union rasterizer. No gate or template constant changed after outcomes.
