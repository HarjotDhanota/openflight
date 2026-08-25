# Occlusion order and shaft-realism remediation report

**Date:** 2026-08-24  
**Scope:** revision 2.6 plus shaft-realism addendum  
**Driver:** `HOLD_CAD_MESH`  
**Disposition:** `STOP_FOR_MAINTAINER_REVIEW`; F2 remains blocked

## Outcome

The reported Arm A-v3 ambient error of 9.152 mm was an implementation artifact,
not a physical limitation of matched exact-mesh fitting. Correct camera-depth
compositing, removal of convex-hull completion, pre-swing ball referencing, and
head/shaft separation reduce the registered matched-mesh ambient result to
1.050 mm median and 1.767 mm p90. The dominant signed height bias collapses
from +8.594 mm to +0.285 mm.

No gate, solver threshold, acceptance criterion, or driver disposition changed.

## Corrected mechanisms

- Every exposure subsample now compares optical-forward camera depth. The ball
  is painted first and the nearer club is painted over it. Any sample in which
  the ball is not behind the club raises
  `scene_occlusion_order_ball_in_front`.
- The generator renders a shaft by default. Its attachment is derived from the
  admitted mesh's top hosel band, its heel direction comes from that geometry,
  and its projected tapered polygon reaches the image boundary. Diameter,
  taper, and lie are configuration fields; defaults are 10 mm, 25%, and 62
  degrees. Head and hosel remain the shaft-free fit template.
- The extractor finds the bright, boundary-reaching shaft core, walks its
  principal axis to the ball-side attachment, removes the shaft appendage, and
  retains the largest raw head component. The former aspect-ratio rejection is
  no longer used.
- Convex-hull silhouette completion is removed. Observation moments use the
  raw head exposure intensity, the raster counterpart of the continuous filled
  silhouette moments used by the analytic and exact models. No observed club
  pixels are invented.
- Ball location comes from the largest visible pre-swing ball silhouettes. The
  impact-window ball is expected to be partially occluded by the club and is
  not used as the reference center.

Turf, divot, grass spray, and tee launch remain out of scope because fitting
uses strictly pre-impact frames.

## Mandatory cheap checkpoint

Before any grid run, the corrected 690CB mesh was used as both truth and exact
fit geometry with the shaft enabled, correct depth ordering, zero sensor and
photometric noise, 10 us exposure, and the registered 10-frame/eight-pretrigger
capture.

| Measurement | Result |
|---|---:|
| Impact-vector error | 0.038 mm |
| Fitted world-center error | 0.110 mm |
| Template-fit IoU | 0.9928 |
| Fit residual | 1.152 px |
| Exact solve wall time | 24.09 s |

An additional 500 us zero-noise safeguard produced 0.292 mm impact-vector error,
0.291 mm center error, 0.9584 IoU, and 1.369 px residual in 29.30 s. The sharp
registered checkpoint therefore met the required approximately-zero recovery
and approximately-0.99 IoU before the expensive run began.

## Corrected iron evaluation

The frozen corrected-iron registration was rerun in full: 800 baseline shots,
400 Arm B shots, and 400 Arm A-v3 exact shots. The final evaluation hash is
`5f74a9b72cab056726f4d68f7715e1420c511cb1f73c6a5f872e17b236ef37f3`.
The pre-remediation hash is
`2c9bcdd91e61bb43b36fd1f6cf7cee81f099a9695650b93c6bd027361bbf2bc8`.

### Arm A-v3 exact: paired before/after

| Candidate | Version | Solve | Median mm | p90 mm | Signed offset median/p90 mm | Signed height median/p90 mm | IoU median | Residual median px |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| strobe 10 us | before | 0.605 | 9.608 | 11.220 | -2.661/-0.974 | 8.877/10.892 | 0.712 | 7.044 |
| strobe 10 us | after | 0.995 | 0.938 | 1.631 | 0.074/0.720 | 0.027/1.041 | 0.985 | 2.144 |
| ambient 500 us | before | 0.840 | 9.152 | 11.577 | -2.354/0.282 | 8.594/11.274 | 0.722 | 5.903 |
| ambient 500 us | after | 0.990 | 1.050 | 1.767 | -0.134/0.652 | 0.285/1.402 | 0.966 | 2.388 |

Arm A-v3 collapses to the approximately 1.09 mm matched-template reference as
predicted by the identical-pixel diagnostic. Ambient is the only gate-bearing
candidate and reports `IRON_A_V3_CLEARS_AMBIENT`; strobe remains comparison
only. The historical accepted iron disposition remains `IRON_NEITHER` until
maintainer review.

### Baseline and Arm B consequences

The corrected analytic-on-analytic 10-frame baseline remains near the matched
reference: strobe is 1.000 solve, 0.965/1.636 mm median/p90 and ambient is 1.000
solve, 1.116/1.937 mm. Raw corrected mesh truth no longer receives convex
completion that flatters an analytic template: the mesh-truth baseline fails
the unchanged residual gate, and Arm B ambient changes to 0.915 solve with
15.427/20.990 mm median/p90, 0.750 IoU, and 11.136 px residual. This is removal
of compensating model flattery, not a threshold regression. Arm B strobe also
fails the unchanged residual gate.

## Phase 4 and 4b recheck

The full Phase 4 artifact grid completed under its frozen registration with
evaluation hash
`40bb77515c114ec341eabb3ca8a92e3df2b69bd7ca4f2b7eadf1bbed49ff87f1`.
Its previous conclusion does **not** survive. The ambient verdict is
`UNDECIDED`, and zero-mismatch reconciliation is `BUG_UNRESOLVED`.

| Club/candidate | Before median/p90 mm | After median/p90 mm | After solve | After zero-mismatch median/p90 mm |
|---|---:|---:|---:|---:|
| driver strobe | 1.354/3.184 | 10.103/17.391 | 0.930 | 9.828/17.175 |
| driver ambient | 1.207/2.511 | 10.624/18.440 | 0.975 | 11.094/18.304 |
| 7-iron strobe | 1.688/3.441 | 6.083/13.375 | 1.000 | 6.227/13.125 |
| 7-iron ambient | 1.482/3.143 | 7.549/14.821 | 1.000 | 7.718/14.715 |

These legacy three-frame/single-frame results disagree materially with Phase
1b even at zero template mismatch despite high IoU. They must not be used to
reinstate the former Phase 4 conclusion; the unchanged reconciliation logic
correctly fails closed.

The full Phase 4b attempt completed all 3,200 recovery shots and all 800
controls, then failed loud in its final registered degradation sweep. The first
invalid scene is driver, strobe 10 us, 130 mph, seed `20270824`; 90 through 120
mph generated validly for all 24 registered seeds. At 130 mph at least one
exposure sample puts the ball in front of the club, so the generator raises
`scene_occlusion_order_ball_in_front`. No partial Phase 4b artifact was
published. Consequently the former Phase 4b conclusion also does **not**
survive as a complete registered result; its high-speed scene domain must be
reviewed by the maintainer, not silently composited or rescued by a threshold.

## Runtime disposition

Arm A-v3 exact remains an evaluation-only instrument. Across all 200 attempts
per cell, strobe solve time was 36.527 s median, 38.184 s p90, and 54.307 s
maximum; ambient was 36.988 s median, 38.706 s p90, and 56.436 s maximum.
Sixteen serial ambient solves at the median require about 592 s (9.86 minutes).
It must not be connected to Sim Studio's interactive regeneration path and is
not a Raspberry Pi runtime candidate. The eventual runtime candidate remains
the LUT/domain-restricted variant.

## Verification

- `research/silhouette_poc/tests`: 122 passed.
- `research/club_pose/tests`: 108 passed, 1 skipped.
- `research/ball_spin/tests`: 20 passed.
- The silhouette suite includes the PR215 archive-loader round trip.

No F2 work was performed. Driver remains `HOLD_CAD_MESH`.
