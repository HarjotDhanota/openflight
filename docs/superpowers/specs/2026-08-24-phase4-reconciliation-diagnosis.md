# Phase 4 reconciliation: independent diagnosis

**Date:** 2026-08-24  
**Scope:** diagnosis only; no fix, Phase 4/4b rerun, gate reinterpretation, or F2 work  
**Status reviewed first:** silhouette POC revision 2.6 root-cause correction  
**Driver:** `HOLD_CAD_MESH`

## Verdict

The Phase 4 zero-mismatch disagreement is an implementation-path defect, not
template mismatch and not evidence that the accepted Phase 1b club-state solver
degraded by 7.5x.

The decisive defect is on the **ball observation**, not the clubhead moment
observation:

1. Revision 2.6 correctly changed the rendered depth order so the club hides the
   overlapping part of the ball.
2. The artifact solver locates the ball by taking the centroid of the visible
   bright connected component. That is the centroid of a clipped disk, not the
   center of the physical ball.
3. The registered three-frame capture has `pre_trigger_count=2`. Its trigger
   index is therefore 1, and `range(trigger_index)` exposes only frame 0 to the
   legacy policy. The code's "largest pre-swing silhouette" selection has no
   second candidate from which to recover an unoccluded center.
4. Phase 1b instead projects the full analytic ball and adds zero-mean centroid
   noise to that true center. Its observation is therefore materially different
   even when club template mismatch is zero.
5. The biased ball ray is back-projected with ball radar range and used directly
   in `contact - fitted_club_center`, producing the impact-vector bias.

The club segmentation/moment change proposed as the leading hypothesis is not
the material cause of this reconciliation failure. The corrected raw-head path
recovers the club state closely enough that changing only the ball centroid
eliminates the disagreement.

## Decisive paired experiment

I regenerated the full registered driver/strobe zero-template-mismatch control:
200 seeds `20260824..20261023`, A0, 10 us, three frames, two pre-trigger frames,
1.2 DN photometric noise, 3 mm radar noise, and the registered 33 us sync jitter.
For every accepted shot I held the rendered frames, extracted club observation,
club solve, radar evidence, timing, gates, thresholds, and impact calculation
fixed. The only counterfactual input was the ball image centroid: the centroid
of the visible connected component was replaced by the projection of the known
physical ball center. Ball radar range remained noisy and unchanged.

| Measurement | Current path | True ball UV only |
|---|---:|---:|
| Accepted shots | 199/200 | same accepted shots |
| Median impact error | 9.828 mm | 0.649 mm |
| p90 impact error | 17.175 mm | 1.289 mm |

The current-path numbers exactly reproduce the committed reconciliation-control
artifact. Changing one observation removes 9.179 mm from the median and 15.886
mm from p90. The counterfactual is also comfortably inside the frozen Phase 1b
reconciliation limits; no gate or threshold was changed.

Distributional diagnostics on those same 199 accepted shots:

- visible ball area was 46.9% of the full rendered disk at the median and 12.7%
  at p10;
- visible-component centroid bias was +1.505 px horizontal and +5.722 px
  vertical at the median;
- 5.722 px at A0's 0.656 px/mm plate scale is 8.72 mm, matching the committed
  median signed height error of -8.767 mm in magnitude and predicted sign;
- recovered club impact-center error was 2.188 mm median / 4.902 mm p90;
- absolute roll error was 0.0132 rad at p90.

Seed `20260824` supplies a directly inspectable example:

- expected impact `[-2.290, +13.738]` mm;
- current result `[+3.526, -0.009]` mm, 14.927 mm error;
- full-ball projection `[149.258, 85.850]` px;
- visible-component centroid `[153, 95]` px;
- recovered club impact center was 0.125 mm from truth;
- replacing only the ball UV produced 0.296 mm impact error.

This is a one-variable intervention on the registered artifact path, and the
measured pixel displacement independently predicts the reported millimetre
bias.

## Code evidence

- `eval/e2e.py` registers three frames and two pre-trigger frames.
- `fusion/pipeline.py` computes the trigger index as `pre_trigger_count - 1`
  and selects candidates from `range(trigger_index)`, leaving only frame 0.
- `_ball_component` returns the connected component's centroid, while the
  "largest" reference logic selects among that one-frame set.
- `_segment_frame` now records `moment_source=raw_head_intensity`; the club
  observation is subsequently refined against the analytic exposure template.
- `eval/phase1b.py` obtains `ball_uv` from full analytic ball geometry and makes
  `observed_ball_uv` by adding zero-mean centroid noise.
- The artifact path back-projects `ball_reference_uv` directly for the final
  contact calculation.

## Ruled out

- **Template mismatch:** the registered control sets dimension variation to
  zero, and the intervention does not alter either truth or fit template.
- **Club segmentation/moments as the material cause:** the ball-only
  intervention reduces median/p90 below the Phase 1b reference while leaving
  the extracted club observation and solved club state untouched.
- **Radar, synchronization, extrapolation, or thresholds:** all remain exactly
  as sampled in each registered shot.
- **A gate-accounting bug:** the current experiment reproduces the committed
  9.828/17.175 mm summary exactly and the solve-rate delta versus Phase 1b is
  only +0.002. The material disagreement is specifically the accuracy bias.
- **Random photometric error:** the centroid bias has the direction, magnitude,
  and low-tail visible-area dependence expected from deterministic occlusion.

## Disposition and recommendation

Retire the legacy Phase 4 three-frame/single-frame path as an active gate or
candidate solver; do not repair or rerun it. Its registered capture contains
only one eligible pre-impact observation and therefore cannot infer the center
of a partially club-occluded ball without adding a new ball-shape model. Adding
that model would turn the supposedly frozen legacy comparator into a new solver
and duplicate work in the Phase 4b/A-v3 artifact path.

Keep its committed artifact only as historical attribution evidence. Phase 1b
should likewise remain a frozen analytic solver benchmark, not be interpreted
as validation of the artifact ball estimator: it observes the full ball center
by construction. Future registered evaluation should use the Phase 4b/A-v3
path after its partial-ball observation is explicitly validated, with A-v3
exact remaining evaluation-only as already decided.

No Phase 4 or Phase 4b verdict is reinstated by this diagnosis.

## Confidence and falsification

**Confidence: very high (greater than 99%).** The experiment reproduces the
registered failure, predicts its signed magnitude from pixels and plate scale,
and removes it by changing only the differing ball observation.

This conclusion would be falsified if the same accepted shots retained a
material Phase 1b disagreement after substituting only the true ball image
centroid while keeping the club observation and all other evidence fixed. It
would also be falsified if a partial-disk-aware ball-center estimator recovered
an unbiased center but the approximately 9-17 mm error distribution remained.
The observed counterfactual is the opposite: 0.649/1.289 mm.

No implementation code was changed and no Phase 4, Phase 4b, or F2 run was
performed.
