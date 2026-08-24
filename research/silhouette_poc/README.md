# Silhouette impact-location POC

Classical rear-view silhouette plus calibrated club-range fusion research.

## Results

**Ambient 500 us: NO** — poc_driver solve rate 0.665 < 0.800.

| Club | Candidate | N | Solve rate | Median vector error | p90 vector error | Result |
|---|---|---:|---:|---:|---:|---|
| poc_driver | strobed_10us | 200 | 0.880 | 1.35 mm | 3.18 mm | PASS |
| poc_driver | ambient_500us | 200 | 0.665 | 1.21 mm | 2.51 mm | FAIL |
| poc_7iron | strobed_10us | 200 | 1.000 | 1.69 mm | 3.44 mm | PASS |
| poc_7iron | ambient_500us | 200 | 0.935 | 1.48 mm | 3.14 mm | PASS |

Headline cells include registered per-club template variation: driver ±8% and
7-iron ±10%. These are synthetic POC results, not closure of physical Gates 0, R, or T.

See [the full end-to-end report](eval/RESULTS_E2E.md),
[canonical JSON](eval/results_e2e.json), and
[degradation curves](eval/degradation_curves.svg).

Evaluation hash: `b4d4f99d1f9337105a7cf41f0885286cf51c1103680ce1bd049056c27715d5ad`
