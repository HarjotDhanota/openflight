# Impact location — master status and plan

**The authoritative record.** Status as of 2026-08-25. A ✅ means demonstrated on **real
data**, not that code exists.

Standing constraints: **no strobe**; comparators are **Trackman 4, Full Swing KIT,
Mevo Gen 2** (never iO — ceiling-mounted, not comparable).

---

## A. DONE — demonstrated on real data

| Area | What | Evidence |
|---|---|---|
| Geometry | **Gate 0 settled.** Lens is 2.8 mm (not the inferred 6 mm), `focal_px` = 466.7 in the shipped 320×200 2× subsampled mode, plate scale 0.327 px/mm, ball ≈14 px, camera ≈1425 mm | Three independent routes agree: datasheet lens, measured 13.97 px ball, README's documented 3–5 ft placement |
| Capture truth | Exposure **997 µs** (untouched default), gain **15.94** (100 % of max), 2.1380 ms/frame, 2.9 µs jitter | `frames.npz` metadata |
| Ball detection | Polarity-agnostic. The real ball is **62 DN darker** than the clipped mat at address and **+141 DN** in flight — contrast **inverts sign** within one shot | `fusion/ball_detect.py`, 13 tests |
| Circle fit | Geometric (Kasa + Gauss-Newton). Exact on exact points at any arc span; the previous algebraic fit was biased **−44 % at 120°** | test suite |
| Ball selection | By **departure**, not shape. Solves multiple balls on the mat by construction | 3 tests |
| Club tracking | Continuity tracker, seeded where the head is clearest, walking forward and backward | F62–71 verified correct |
| Trigger | Ball-departure detection validated: **43 px steady for 23 frames, then gone, zero false absences** | phone video |
| Pose | Mesh fitting runs on **real pixels**. 6-DOF orientation added | IoU F64 0.221→0.547, F68 0.460→0.668 |
| Fusion | Camera+radar+speed fusion **already exists upstream** and is merged | `camera/club_delivery.py`, `iwr6843/club.py` |

## B. MEASURED LIMITS — not bugs, physical facts about this capture

| Finding | Number |
|---|---|
| Impact zone clipped at 255 | **83–94 %** |
| Clubhead contrast, F67–71 | 267–550 px above 30 DN — trackable |
| Clubhead contrast, **F73–79** | **19–80 px — nothing left to track** |
| Acoustic trigger lag behind impact | **≥ 4.7 frames (~10 ms)**, and it **scales with placement distance** |
| Unmodelled lens distortion → club path / AoA | **~2.7° systematic** |
| Two-point velocity differencing | ~0.65° avoidable |

## C. NOT DONE — required for a functional system

### C1. Capture
- ❌ **Exposure calibrated.** `calibrate_camera_exposure.py` ships and has never been run. This one item breaks detection, segmentation *and* the trigger.
- ❌ Shot detection without the microphone — validated in principle, not implemented.
- ❌ Ball placement zone + READY state — specified, not built.
- ❌ Simultaneous camera + IWR L3 capture — nobody has taken one.

### C2. Geometry
- ❌ **Lens distortion correction.** No model anywhere in `src/openflight`.
- ❌ Camera-to-ball distance, tape-measured. `iwr6843/calibration.py` already has `tee_range_m` for it.
- ❌ Camera height and tilt — assumed 209.55 mm, unverified. The enclosure STEP has no camera in it.

### C3. Detection
- ❌ Ball sizing across varied distances — tested at ~14 px only.
- ❌ Robustness across venues — **one** OpenFlight capture; everything is tuned to it.

### C4. Timing
- ❌ Retrospective impact time wired into the camera path. Two estimators already exist upstream (`iwr6843/shot.py::impact_time_s`, 14/14; `rolling_buffer/processor.py::estimate_impact`) and neither is used by the camera.

### C5. Pose — **the blocker**
- ❌ **Pose is not temporally coherent.** Fitted yaw/pitch/roll jump >100° between adjacent frames; range wanders 1120–1430 mm when the head should recede smoothly.
- ❌ Temporal smoothness constraint.
- ❌ Radar range as a **hard** constraint (currently a free fit parameter).
- ❌ Physical bounds on loft and lie.
- ❌ Any ground-truth pose to validate against.

### C6. Impact location
- ❌ **Does not exist anywhere.** No match for `impact_location`, `strike_location`, `face_impact` or `gear_effect` in `src/openflight`.

### C7. Validation
- ❌ **A-v3 accuracy re-run at real settings.** Every accuracy number this project has produced was simulated at 500 µs and 0.656 px/mm — **both wrong**. There is currently **no validated accuracy figure for real hardware**.

---

## D. The pose approach — landmark correspondence, not blob overlap

**Maintainer's proposal, and it is better than what is implemented.** Rather than scoring
outline-against-outline as an undifferentiated blob, identify *which part of the club faces
the camera* and match that to the corresponding part of the mesh — if the toe points at the
camera during the downswing, the silhouette has a toe-forward signature that the mesh can be
matched to at that vantage.

**Why this beats what we do now.** The current fit maximises IoU, and at 20–40 px many
orientations project to nearly the same outline — which is exactly why the recovered angles
wander unphysically (§C5). Landmarks break that degeneracy because they are *labelled*: a
toe is not a heel even when the outlines match.

**A rejected alternative, and why.** An earlier suggestion was to match interior shading via
the z-buffer. **The maintainer is right to reject it**: interior appearance is club-specific
and lighting-dependent, so it would not generalise across clubs and would make the canonical-
template goal impossible. Landmarks are generic — *every* iron has a toe, heel, hosel
junction, leading edge, topline and sole.

**What it unlocks.** Three or more labelled 2D↔3D correspondences is exactly the input
`cv2.solvePnP` wants. Pose stops being a brute-force grid search over IoU and becomes a
proper least-squares solve with a residual — which also gives an uncertainty estimate, which
IoU never did.

**Known difficulty, stated honestly.** A silhouette's boundary is the *extremal contour* — a
view-dependent set of surface points, not fixed mesh vertices — so not every landmark is
stable under rotation. The hosel junction, the sole line and the leading edge are relatively
stable; the toe and heel extremes migrate across the surface as the club rotates. Any
implementation must handle that rather than assume fixed vertices.

**Not yet attempted.** No landmark detector exists for either the mesh or the image.

---

## E. Critical path, in order

1. **Calibrated-exposure capture.** Unblocks detection, trigger and pose at once. One hour.
2. **Landmark-based pose** (§D) with temporal smoothness and radar range as a hard
   constraint. This is what turns an overlap score into a trustworthy pose.
3. **Interim distortion correction** from the datasheet, if the checkerboard is deferred.
4. **Impact location** — comparatively small work once §2 is trustworthy.
5. **A-v3 re-run** at real settings, for the first honest accuracy figure.

### Why impact location is not step 2

Impact location is `f(clubface pose, ball position)`. Ball position is solid; **pose is the
weak link and we now know exactly how it is weak.** Building `f()` on an incoherent pose
yields a millimetre figure nobody should believe — precisely the trap this project already
fell into with the synthetic 1.050 mm result.

---

## F. Presentation rule

**Overlays must show the exact fitted geometry — never padded.** A circle drawn at 1.6× the
measured radius, or a radius floored to a minimum, misrepresents the system as less accurate
than it is. Both instances have been removed. If an outline looks wrong, that must mean the
fit *is* wrong.
