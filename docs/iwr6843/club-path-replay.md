# Radar club-path replay

## 2026-08-28 A0 control-gate result and arithmetic follow-ups

Session `20260825_181734` was replayed from all 22 archived `.l3dump` files
using the geometry and phase configuration recorded in its `session_start`
event. A0 called the unchanged production `estimate_club_path` implementation.
The live runtime's −2 ms club-impact correction and each row's recorded window
policy and TDM sign were preserved.

The control comparison accepts a value when it rounds to the decimal precision
stored in `shots.csv`, with an additional relative/absolute `1e-10` allowance
for machine-scale NumPy reduction differences. A0 still produced 13 material
field mismatches on four shots, so the initial control did not pass.

The follow-up replay added `--a0-legacy-median`, which temporarily substitutes
the exact candidate-by-candidate implementation from
`git show 3d69870^:src/openflight/iwr6843/doa.py` during A0 only. It produced the
same 13 material mismatches and the same replay values shown below. The
pre-refactor reduction therefore does not reproduce the four discrepant shots.

The second follow-up instrumented the current A0 without changing its decision.
For shots 11, 16, 28, and 29 it wrote every snapshot phase, its frame median,
the circular deviation, the 0.6 rad `CLUB_MAX_PHASE_DEVIATION_RAD` threshold,
distance from that threshold, and keep decision. The proposed host-arithmetic
control also fails:

| Shot | Minimum `|deviation − 0.6|` (rad) | Archived / replay kept | Within `1e-6` or count differs by one? |
|---:|---:|---:|---|
| 11 | 0.001761747 | 45 / 45 | No |
| 16 | 0.003746697 | 41 / 41 | No |
| 28 | 0.020190576 | 37 / 38 | Yes — count differs by one |
| 29 | 0.000280031 | 36 / 36 | No |

Only shot 28 meets the alternative count condition; no shot has a sample
within `1e-6` rad of the gate. Shots 11, 16, and 29 retain exactly the archived
number of main-path snapshots. Their mismatches are instead confined to
`candidate_path_deg` and `candidate_path_fit_residual_deg`, which are computed
from the separate TX1/TX3 reference-phase candidate before the main
`phase_outlier_mask` gate. Per the conditional stop rule, no later arm was run
and no plots were rendered.

| Arm | Result | Control decision |
|---|---|---|
| A0 — shipped code | 18/22 shots reproduced every `iwr_club_path_*` field; shots 11, 16, 28, and 29 differed | **FAIL — stop** |
| A0 — pre-`3d69870` median | Same 18/22 shots and same 13 field mismatches | **FAIL — stop** |
| A0 — hard-gate diagnostic | Host condition met on 1/4 discrepant shots | **FAIL — stop** |
| A1 — window-scope static removal | Not run | Blocked by A0 diagnostics |
| A2 — alternate TDM velocities | Not run | Blocked by A0 diagnostics |
| A3 — free/club-anchored attack fit | Not run | Blocked by A0 diagnostics |

All 22 top-level statuses still reproduced as `rejected_phase_span`. The
material discrepancies were:

| Shot | Differing field | `shots.csv` | A0 replay |
|---:|---|---:|---:|
| 11 | candidate path (deg) | 15.116559 | 14.913477 |
| 11 | candidate-path residual (deg) | 0.854972 | 0.852020 |
| 16 | candidate path (deg) | 0.108522 | 1.843270 |
| 16 | candidate-path residual (deg) | 4.177026 | 4.011072 |
| 28 | candidate path (deg) | 28.899492 | 28.848241 |
| 28 | candidate-path residual (deg) | 0.864512 | 0.859420 |
| 28 | azimuth rate (deg/s) | 5452.538322 | 5520.533456 |
| 28 | club range (m) | 1.250519 | 1.252396 |
| 28 | snapshots kept / rejected | 37 / 11 | 38 / 10 |
| 28 | path-fit residual (deg) | 30.011754 | 29.949429 |
| 29 | candidate path (deg) | 18.745973 | 18.730551 |
| 29 | candidate-path residual (deg) | 0.795107 | 0.526918 |

The archive was captured on August 25. Commit `3d69870` on August 26 replaced
the candidate-by-candidate `doa.circular_median` reduction with a vectorized
matrix reduction. The replay-only substitution shows that this refactor is not
the cause: current and legacy reductions return identical control outputs on
this host. The unresolved differences remain confined to phase-derived values
except for shot 28, where one sample also crosses the fixed phase-outlier
boundary. The largest candidate-path difference is 1.734747° on shot 16.

**Per-burst MTI verdict:** not evaluated; A1 was prohibited by the failed A0
control. **Tee-height attack-anchor verdict:** not evaluated; A3 was prohibited
by the same gate. No production code was changed.
