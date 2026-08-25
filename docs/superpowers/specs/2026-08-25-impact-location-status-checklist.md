# Impact location — what is done, what is not, what "functional" requires

Status as of 2026-08-25. Written to be honest rather than encouraging: a ✅ means it was
demonstrated on **real data**, not that code exists.

Standing constraints: **no strobe**; comparators are **Trackman 4, Full Swing KIT, Mevo Gen 2**
(never iO — it is ceiling-mounted and not comparable).

---

## 1. Capture

| | Item | Notes |
|---|---|---|
| ✅ | Camera capture with full metadata | `test_camera_clap_buffer.py` → `frames.npz`. 467.7 fps, 2.9 µs jitter. |
| ❌ | **Exposure calibrated** | `calibrate_camera_exposure.py` ships and has **never been run**. The one real capture used the untouched 1000 µs default; 83–94 % of the impact zone is clipped. This single item breaks ball detection, club segmentation *and* the shot trigger. |
| ❌ | Shot detection without a microphone | Ball-departure logic validated on the phone video (43 px steady for 23 frames, then gone, zero false absences). **Not implemented** in the capture path. |
| ❌ | Ball placement zone + READY state | Specified in `2026-08-25-ball-placement-and-exposure-readiness.md`. Not built. |
| ❌ | Simultaneous camera + IWR L3 capture | Nobody has taken one. Required to test radar clubhead tracking. |

## 2. Geometry

| | Item | Notes |
|---|---|---|
| ✅ | Plate scale / focal length | **Settled.** 2.8 mm lens, `focal_px` 466.7, 0.327 px/mm, ~14 px ball. Three independent routes agree. |
| ❌ | **Lens distortion correction** | No distortion model anywhere in `src/openflight`. Datasheet says < −17 % TV distortion. Estimated **~2.7° systematic** error on club path / AoA. Checkerboard deferred by the maintainer. |
| ❌ | Camera-to-ball distance, measured | Inferred as ~1425 mm. A tape measure settles it. `iwr6843/calibration.py` already has `tee_range_m` for it. |
| ❌ | Camera height and tilt | The POC assumes 209.55 mm and a look-at target. **Unverified.** The enclosure STEP contains housing only — no camera — so this must be measured physically. |

## 3. Detection

| | Item | Notes |
|---|---|---|
| ✅ | Ball at address | Polarity-agnostic; the real ball is **62 DN darker** than the clipped mat. 13 tests. |
| ✅ | Ball in flight | Motion-gated track; x holds ~128 px while radius shrinks 7.8→5.5 px. |
| ✅ | Club tracked through impact | Continuity tracker, frames 62–79 continuous except F72. |
| ✅ | Multiple balls on the mat | Solved by construction — spares do not depart. 3 tests. |
| ❌ | Ball sizing across varied distances | `find_teed_ball` has no scale adaptivity. Tested at ~14 px only. |
| ❌ | Robustness across lighting and venues | **One** OpenFlight capture. Everything is tuned to it. |

## 4. Timing

| | Item | Notes |
|---|---|---|
| ✅ | Retrospective estimators exist | `iwr6843/shot.py::impact_time_s` (14/14 on the 2026-07-25 captures) and `rolling_buffer/processor.py::estimate_impact`. |
| ❌ | Wired into the camera path | The camera capture uses the **raw acoustic trigger** as its time reference. That trigger lags impact by ≥ 4.7 frames (~10 ms) and the lag **scales with placement distance**. |
| ❌ | Camera-side impact time validated | Back-extrapolation of the ball's flight works in principle; not implemented as a component. |

## 5. Clubface pose — **the current blocker**

| | Item | Notes |
|---|---|---|
| ✅ | Mesh fitting runs on real pixels | First time. Corrected camera model; a ray-origin bug fixed. |
| ✅ | 6-DOF orientation | The shipped model was **4-DOF with a hardcoded face normal** — it could not represent loft, lie or face angle at all. 6-DOF roughly doubles IoU at F64 and gains 45 % at F68. |
| ❌ | **Pose is not temporally coherent** | Fitted yaw/pitch/roll jump >100° between adjacent frames and range wanders 1120–1430 mm when the head should recede smoothly. A 20–40 px silhouette **does not uniquely determine 6 DOF**. |
| ❌ | Temporal smoothness constraint | Not implemented. |
| ❌ | Radar range as a hard constraint | Currently range is a *free* fit parameter. The radar should pin it. |
| ❌ | Physical bounds on loft / lie | Unbounded. The fit is free to choose absurd orientations. |
| ❌ | Validation against known truth | No ground-truth pose exists for any real frame. |

## 6. Impact location itself

| | Item | Notes |
|---|---|---|
| ❌ | **Does not exist anywhere** | No match for `impact_location`, `strike_location`, `face_impact` or `gear_effect` in `src/openflight`. |
| ❌ | Face-relative ball position | Needs a trustworthy pose (§5). |
| ❌ | Gear-effect correction | Deferred by decision, pending community input. |

## 7. Validation

| | Item | Notes |
|---|---|---|
| ❌ | **A-v3 accuracy re-run at real settings** | Every accuracy number this project has produced was simulated at 500 µs and 0.656 px/mm. Both wrong. Must re-run at 997 µs and 0.327 px/mm, with frame rate swept. **There is currently no validated accuracy figure for real hardware.** |
| ❌ | Ground truth for impact location | Impact tape, or a comparator reading. Nothing yet. |
| ❌ | Multi-shot, multi-user, multi-venue | n = 1 capture. |

---

## The critical path, in order

1. **Calibrated-exposure capture.** Unblocks detection, trigger and pose simultaneously. One hour.
2. **Constrain the pose fit** — temporal smoothness, radar range as a hard constraint, loft/lie
   bounds. This is what turns an IoU number into a *trustworthy* pose.
3. **Interim distortion correction**, from the datasheet if a checkerboard is deferred.
4. **Impact location**, which is comparatively small work once §5 is trustworthy.
5. **A-v3 re-run** at real settings, to get the first honest accuracy figure.

### Why impact location is NOT step 2

Impact location is `f(clubface pose, ball position)`. Ball position is solid. **Pose is the
weak link and we now know exactly how it is weak** — the fit maximises silhouette overlap
while the recovered angles wander unphysically.

Building `f()` on top of that produces a millimetre figure nobody should believe, which is
precisely the trap the project already fell into once with the synthetic 1.050 mm result.
Constrain the pose first; the arithmetic that turns pose into impact location is the easy part.
