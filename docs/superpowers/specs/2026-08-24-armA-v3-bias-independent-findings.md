# Arm A-v3 bias: independent root-cause findings

**Date:** 2026-08-24  
**Scope:** Investigation only; no fix, gate rerun, threshold change, or F2 work  
**Driver:** `HOLD_CAD_MESH`

## Sealed-hypothesis discipline

I filed this report before opening
`2026-08-24-armA-v3-bias-sealed-hypothesis.md`. I did not read, search, diff,
or otherwise inspect that file during this investigation.

## Verdict

Arm A-v3's 9.152 mm ambient result is an implementation defect, not a real
physical result.

The exact mechanism is a moment-definition mismatch between segmentation and
the exact mesh observation model:

1. `fusion/pipeline.py::_segment_frame` computes the convex hull of the
   extracted club component. If hull completion exceeds 10%, it replaces the
   observed silhouette's centroid and covariance with **convex-hull moments**.
2. The corrected 690CB's legitimate concave silhouette has roughly 30-36%
   hull completion even without ball occlusion, so its accepted frames always
   take that branch.
3. `eval/mesh_lut.py::_mask_features`, used by
   `eval/exact_mesh_fit.py::ExactMeshProjectionTemplate`, computes centroid and
   covariance from the **raw rasterized mesh mask**. It uses the hull only for
   the contour representation.
4. `fusion/mesh_fit.py::_candidate` subtracts the model's raw-mask centroid
   offset during backprojection and minimizes a raw-mask covariance residual
   against the observation's hull covariance. It does not use contour IoU to
   choose the state. IoU is calculated later in `fusion/pipeline.py`, after the
   state has already been selected.

For this mesh, convex completion moves the observation centroid downward by
about 6.1-6.7 px. At A0's approximately 0.656 px/mm plate scale, that becomes
about 9.3-10.2 mm of downward fitted-center error. Impact height is computed as
`contact - fitted_center`, so a center fitted too low produces the observed
same-sign positive impact-height error. The smaller roll-dependent horizontal
hull shift produces the negative horizontal error.

## Evidence from the code

- Truth renders the (dimension-varied) normalized mesh at each exposure sample
  through `render_mesh_mask` in `generator/synthetic.py`.
- Exact Arm A-v3 loads the same admitted `poc_7iron.npz` and uses the same
  vertex transform, camera projection, and triangle rasterizer in
  `eval/exact_mesh_fit.py`.
- Segmentation constructs `completed_mask` from the component's convex hull,
  calculates `completion_fraction`, and switches `moment_weights` to the hull
  above 0.10 in `fusion/pipeline.py`.
- The exact model's `_mask_features` calculates raw pixel centroid/covariance
  before separately calculating a hull for contour samples in
  `eval/mesh_lut.py`.
- `_candidate` scores only the Frobenius difference between observed and model
  covariance in `fusion/mesh_fit.py`. The predicted contour is returned but is
  not part of that score.
- The current exact-model unit test uses a rectangle. Its raw silhouette and
  convex hull are the same, so the test proves native rasterization but cannot
  expose this mismatch on a concave iron silhouette.

## Targeted diagnostics

All diagnostics used temporary artifacts and inline read-only scripts. No
evaluation or production code was changed.

### 1. Registered-shot reproduction and state inspection

I regenerated ambient seed `20260824` with the registered Arm A-v3 inputs and
solved one shot through the exact model.

- accepted in 23.376 s in this diagnostic run;
- expected impact `[-2.840, 3.086]` mm;
- reported impact `[-5.932, 12.650]` mm;
- error `[-3.092, +9.564]` mm (10.051 mm total);
- mean IoU 0.725;
- mean fit residual 5.742 px;
- fitted impact center error `[-0.211, +0.095, -10.091]` mm.

The two selected frames used fitted rolls of -11.50 and -13.75 degrees while
truth was -3.55 and -3.90 degrees. This established a roll-selection problem,
but forcing the true roll still left the center 9.20 mm too low, so roll was not
the primary vertical-bias mechanism.

### 2. Covariance objective versus contour objective

For frame 6 of that same shot, I scanned the registered roll grid while holding
the extracted observation, radar range, exposure, and motion fixed.

- covariance minimum: -14 degrees, residual 5.611 px, IoU 0.717;
- contour-IoU maximum: -4 degrees, IoU 0.771, residual 9.541 px;
- truth: -3.895 degrees, IoU 0.770, residual 9.583 px.

Thus the implemented covariance objective actively prefers the wrong roll,
while the unused contour diagnostic identifies the true roll. Nevertheless,
the true-roll center remained 9.20 mm low, confirming that this is secondary
to the moment convention mismatch.

### 3. Decisive identical-pixel convention experiment

I rendered the nominal corrected 690CB once per pose, then held mesh, pixels,
pose, exact radar range, motion (zero), exposure (zero), and noise (zero)
constant. The only changed input was the observation moment convention:

| Center mm / roll | Native hull completion | Hull minus raw centroid px | Raw-moment center error mm | Hull-moment center error mm |
|---|---:|---:|---:|---:|
| `[0,-15,-5]` / -8 deg | 36.176% | `[+1.624,+6.670]` | `[-0.001,+0.047,-0.014]` | `[-1.035,+2.602,-10.203]` |
| `[0,0,2.5]` / 0 deg | 36.060% | `[+2.603,+6.475]` | `[+0.005,-0.033,+0.053]` | `[-0.962,+3.765,-9.701]` |
| `[0,+15,+10]` / +8 deg | 36.081% | `[+3.445,+6.120]` | `[+0.002,-0.006,+0.019]` | `[-0.930,+5.227,-9.285]` |

Raw-mask moments recover the center within 0.07 mm in every case. Applying the
segmentation hull convention to the identical noiseless pixels creates the
same approximately 9-10 mm vertical bias seen at the gate. This isolates a
constant observation-frame convention offset with modest pose dependence;
physical noise is not required.

For the reproduced registered shot, early frames failed visibility/component
checks, while accepted frames 5 and 6 both used
`convex_silhouette_completion`, with 29.840% and 31.896% completion. Therefore
the defective branch is actually active in the reported evaluation path.

## Ruled out

- **Mesh mismatch as the root cause:** the decisive test uses the identical
  nominal mesh on both sides. The registered 1% dimension variation can add a
  small mismatch but cannot cause this defect.
- **Radar noise/bias or camera-radar extrinsics:** exact radar range with the
  registered sensor origin still produces the hull-convention bias; raw
  moments recover the center.
- **Photometric noise, exposure blur, synchronization, temporal extrapolation,
  or motion model:** all were removed in the decisive test.
- **Actual ball occlusion:** the decisive masks contain only the club, yet the
  native concavity alone triggers 36% convex completion.
- **A global axis/sign swap:** raw moments recover all three world coordinates
  at multiple positions and rolls. The sign of the reported vertical error
  follows directly from subtracting a center that the hull convention placed
  too low.
- **Roll ambiguity as the primary cause:** it exists and degrades IoU, but
  forcing truth roll leaves the dominant vertical error.

## Confidence and falsification

**Confidence: very high (greater than 99%).** The defect is reproduced from the
registered path, its pixel displacement predicts the magnitude and sign of the
reported error, and an identical-pixel/noiseless experiment turns the error on
and off by changing only the two conventions already present in the code.

This conclusion would be falsified if a diagnostic—not a gate rerun—made the
observation and exact model use the same moment definition while holding all
other inputs fixed and the approximately 9-10 mm bias remained. It would also
be falsified if accepted 690CB frames did not take the convex-completion branch
or if the measured raw-to-hull centroid delta failed to predict the fitted
center error. Current evidence shows the opposite in each case.

## Runtime disposition

Arm A-v3-exact is an evaluation instrument only. The recorded ambient timing
samples are median 35.356 s, p90 36.234 s, maximum 50.593 s, and 6,999.858 s
total for 200 attempts. Sixteen serial shots at the median would take about
566 s (9.43 minutes). It must not be wired into Sim Studio's interactive
regeneration path and is not a Raspberry Pi runtime candidate. The eventual
runtime candidate remains the LUT/domain-restricted variant.

`HOLD_CAD_MESH` remains unchanged. No F2 work was performed.
