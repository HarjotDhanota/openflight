# Silhouette impact-location POC

Classical rear-view silhouette plus calibrated club-range fusion research.

## Phase 4b ambient recovery

**Ambient 500 us: YES** — calibrated ambient driver and 7-iron meet the frozen recovery gates.

The existing single OV9281 320x200 ambient configuration is the only Phase-A
buildable candidate. The strobe remains a deferred comparison fallback.

### Final calibrated results

| Club | Candidate | N | Solve | Median vector error | p90 vector error | Result |
|---|---|---:|---:|---:|---:|---|
| poc_driver | strobed_10us | 200 | 1.000 | 0.95 mm | 1.86 mm | PASS |
| poc_driver | ambient_500us | 200 | 1.000 | 0.93 mm | 1.82 mm | PASS |
| poc_7iron | strobed_10us | 200 | 1.000 | 1.10 mm | 2.77 mm | PASS |
| poc_7iron | ambient_500us | 200 | 1.000 | 1.09 mm | 2.43 mm | PASS |

See [Phase 4b results](eval/RESULTS_E2E_4B.md),
[canonical JSON](eval/results_e2e_4b.json), and
[degradation curves](eval/degradation_curves_4b.svg).

Evaluation hash: `7809e5d38d6f3b53a5ae1990eee6cf7ac2fc9bb197e6341e311fdeafb2ad624e`
