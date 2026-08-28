# Silhouette impact-location POC

Classical rear-view silhouette plus calibrated club-range fusion research.

## Results

**Ambient 500 us: UNDECIDED** — material Phase 1b disagreement must be diagnosed before interpreting the gate.

| Club | Candidate | N | Solve rate | Median vector error | p90 vector error | Result |
|---|---|---:|---:|---:|---:|---|
| poc_driver | strobed_10us | 200 | 0.930 | 10.10 mm | 17.39 mm | FAIL |
| poc_driver | ambient_500us | 200 | 0.975 | 10.62 mm | 18.44 mm | FAIL |
| poc_7iron | strobed_10us | 200 | 1.000 | 6.08 mm | 13.38 mm | PASS |
| poc_7iron | ambient_500us | 200 | 1.000 | 7.55 mm | 14.82 mm | PASS |

Headline cells include registered per-club template variation: driver ±8% and
7-iron ±10%. These are synthetic POC results, not closure of physical Gates 0, R, or T.

See [the full end-to-end report](eval/RESULTS_E2E.md),
[canonical JSON](eval/results_e2e.json), and
[degradation curves](eval/degradation_curves.svg).

Evaluation hash: `40bb77515c114ec341eabb3ca8a92e3df2b69bd7ca4f2b7eadf1bbed49ff87f1`
