# Stage 0B-1 — Mono-vs-Stereo Pose-Recovery Experiment (silhouette analysis-by-synthesis) — design spec

- **Date:** 2026-06-28 (rev. 2 — fixes camera framing, metric-propagation gate, mesh-bias, dep placement per review)
- **Status:** Draft for review
- **Branch:** `feat/camera-club-data`
- **Related:** [Stage 0A spec](2026-06-28-club-pose-geometry-core-design.md) (see its **§11** correction + `research/club_pose/sensitivity.py`); [v2 research guide](../../Personal%20Research/markerless-club-data-guide-v2-research-corrected.md) §1B, §2.4, §6B. This is **Stage 0B-1** of the full Stage 0B pipeline (0B-2 BlenderProc renderer, 0B-3 BiRefNet segmentation, 0B-4 pose estimators on real-ish frames, 0B-5 integration — separate later specs, GPU/Linux-server targets).

## 1. Purpose & context

The open question that gates hardware spend is **single camera vs stereo** for the club metrics (face angle, dynamic loft, impact location). Stage 0A produced *metric-error-per-pose-error coefficients* but cannot supply the **pose-error magnitudes** mono vs stereo — that needs a camera projection model (Stage 0A spec §11 / `sensitivity.py`).

0B-1 supplies them **geometrically**: render a generic clubhead **silhouette** at known poses from a behind-ball camera (mono and stereo), apply **modeled degradations**, recover pose by **silhouette analysis-by-synthesis**, and propagate the recovered-pose error through the 0A budget into face/loft/impact error — the **mono-vs-stereo verdict**. Pure Python (numpy/scipy/opencv); runs in the Windows/Codex dev flow.

**Scope of the verdict (honest):** 0B-1 is the **geometric/optimistic bound** — idealized binary silhouettes + *modeled* degradations. It answers "is the pose information geometrically there for one camera vs two." It does **not** test real-frame segmentation (that only worsens the mono case), which is 0B-2/0B-3. So 0B-1's single-camera result is an *upper bound* on single-camera viability.

## 2. Scope

**In:** pinhole camera model (mono + stereo rigs, look-at aimed); a procedural generic clubhead mesh (driver + iron) anchored in the 0A **body frame** (+ optional OBJ loader); a pure-Python silhouette rasterizer + IoU/chamfer; modeled degradations (motion blur, segmentation/boundary noise, truncation, ball/shaft occlusion); an analysis-by-synthesis pose fitter (mono + stereo); the experiment harness + verdict; TDD tests.
**Out (later stages):** photorealistic rendering (0B-2), learned segmentation/keypoints (0B-3/4), real images, hardware, GPU. **Keypoint+PnP estimator deferred** — silhouette fitting is the honest markerless baseline for the decision.
**Non-goal:** sim-to-real fidelity. 0B-1 is the geometric bound, not a transfer test.

## 3. Location, dependencies, standards

- **Location:** `research/club_pose/sim/` (new subpackage) — reuses 0A `types`, `template`, `metrics`, `sensitivity` directly.
- **Dependency placement (review #4):** `opencv-python` is needed **only** by `sim`. Do **not** add it to base/production deps — `pyproject.toml` deliberately keeps camera/OpenCV out of base. Add a dedicated group:
  ```toml
  [dependency-groups]
  research = ["opencv-python>=4.8"]
  ```
  and run tests with **`uv run --group research pytest research/club_pose/tests/ -v`**. (Quick alternative without editing pyproject: `uv run --with opencv-python pytest ...`.) numpy/scipy/pytest already present.
- **Test gate:** `uv run --group research pytest research/club_pose/tests/ -v` (env note: if `uv` isn't on PATH, use the winget `uv.exe` full path or a fresh terminal). Built test-first.

## 4. Conventions & camera model (review #1)

**World frame = 0A's** (right-handed, +X downrange, +Y player-left, +Z up; origin = ball at address).

**Pinhole intrinsics:** `K = [[fx,0,cx],[0,fy,cy],[0,0,1]]`, image `width × height`. Default: IMX296 **1456×1088**, 3.45 µm px, **16 mm** lens → `fx = fy = 16/0.00345 ≈ 4638 px`, `cx = 728`, `cy = 544`.

**Extrinsic = look-at (NOT a level axis — a level camera above the ball points off-frame).** A camera is defined by center `C` (world mm), look-at **target** `T` (the impact-zone center it aims at), up hint `U = +Z`. World→camera rotation rows:
```
forward = normalize(T − C)        # optical axis (camera +z)
right   = normalize(forward × U)  # image +x  (≈ world −Y = player right)
down    = forward × right         # image +y
R_wc    = [right; down; forward]
```
Projection of world point `p`: `p_cam = R_wc @ (p − C)`; `u = fx·(p_cam_x/p_cam_z) + cx`, `v = fy·(p_cam_y/p_cam_z) + cy`; valid iff `p_cam_z > 0`.
**Sanity (now correct):** with `C=(−1200,0,300)`, `T=(0,0,0)`, the ball at origin gives `p_cam ≈ (0,0,1237)` → projects at `(cx, cy)` — centered and in-frame (the rev-1 level-axis default put it at v≈1704, off-frame; fixed by aiming).

**Rigs (defaults):**
- **Mono:** `look_at(C=(−1200,0,300), T=impact-zone center ≈ (0,0,0))`.
- **Stereo:** two cameras at `C ± (0, 75, 0)` (horizontal baseline along world Y, total **150 mm**), each `look_at` the same `T` (slightly verged). Horizontal baseline resolves the +X depth mono cannot.
- **In-frame requirement:** for the default rigs, the ball and the nominal clubhead bounding box must project inside the image (enforced by a test, §7).

## 5. Components (six small, testable units in `research/club_pose/sim/`)

### 5.1 `camera.py`
- `CameraIntrinsics(fx, fy, cx, cy, width, height)`.
- `Camera(intrinsics, center_world, R_wc)`; classmethod `look_at(intrinsics, center, target, up=+Z) -> Camera`; `project(points_world) -> (pixels Nx2, in_front mask)`.
- `mono_rig() -> Camera`; `stereo_rig(baseline_mm=150.0) -> (Camera, Camera)` — both via `look_at` aimed at the impact zone.

### 5.2 `headmesh.py`
- `HeadMesh(vertices Nx3 (body coords), faces Mx3, category)`; `transformed(pose) -> world vertices`.
- `procedural(category) -> HeadMesh`: a **frozen, realistic** low-poly head — driver (rounded crown blob + sole + hosel stub offset toward the heel) / iron (blade slab + hosel + distinct toe/heel/topline). Anchored to the 0A body frame (origin = head geometric center; +X ≈ face normal at zero loft, consistent with `template.py`). **Shape constraints are fixed up front and documented; the mesh is NOT tuned to make mono recover (review #3).**
- `load_obj(path) -> HeadMesh`: minimal OBJ loader (optional; real CAD plugs in for higher fidelity).
- `distinctive_test_mesh() -> HeadMesh`: a deliberately asymmetric/feature-rich mesh used **only** to validate the rasterizer + optimizer machinery (§7), never for the verdict.

### 5.3 `silhouette.py`
- `render_silhouette(mesh, pose, camera) -> mask (H×W bool)`: project transformed vertices; fill every triangle (`cv2.fillConvexPoly`); union → silhouette.
- `iou(a, b) -> float`; `chamfer(a, b) -> float` (mean boundary distance via distance transform).

### 5.4 `degrade.py`
- `DegradationParams(blur_px, blur_dir, boundary_sigma_px, truncate_frac, occlude_ball, occlude_shaft)` + presets `none / light / realistic / severe`.
- `degrade(mask, params, rng) -> mask`: motion blur, boundary/segmentation noise (random morphological erode/dilate + jitter), truncation, ball/shaft occlusion. Deterministic given a numpy `rng` seed.

### 5.5 `posefit.py`
- `FitResult(pose, iou, success, n_evals)`.
- `fit_pose_mono(observed_mask, mesh, camera, prior_pose) -> FitResult`: minimize `1 − IoU(render(pose), observed)` (+ optional chamfer) over 6-DOF (rotvec 3 + translation 3) via `scipy.optimize` (Powell/Nelder-Mead), seeded from `prior_pose` with a small multi-start.
- `fit_pose_stereo(observed_masks, mesh, cameras, prior_pose) -> FitResult`: minimize the **sum** of `1 − IoU` across both cameras.
- `prior_pose`: a coarse seed = true pose perturbed by a configurable amount (stand-in for the radar velocity / ball-at-tee prior).

### 5.6 `experiment.py`
- `sample_poses(n, category, rng) -> list[ClubheadPose]`: realistic impact-zone positions + face-angle / dynamic-loft / lie / path ranges (via 0A `groundtruth` + realistic spans).
- **Metric-error propagation (review #2): use the UNGATED raw path, NOT `compute_metrics`.** `compute_metrics`'s contact gate returns `None` when a perturbed/recovered pose makes the fixed ball a non-contact (the same reason `sensitivity.py` uses the raw projection). So:
  - face angle / dynamic loft: `metrics.face_angle` / `metrics.dynamic_loft` (ungated) on true vs recovered pose.
  - impact location: `template.point_to_face_uv` (raw projection of the fixed true ball center under true vs recovered pose) → Δoffset, Δheight (mm).
  - `compute_metrics` (gated) is reserved for real-shot outputs, never for experiment error.
- `run_experiment(n, category, severity, baseline_mm, rng) -> results`: per pose → render clean silhouette(s) → degrade → `fit_pose_mono` + `fit_pose_stereo` → pose error (rotation °, translation mm split **depth (+X)** vs **in-plane**) → ungated metric error (above). Aggregate distributions mono vs stereo; sweep `severity`.
- `verdict(results) -> dict`: does **mono** meet the single-camera tier (face/loft **±3–5°**, impact **~5–15 mm**)? does **stereo** meet the stretch tier (**±2°**, **±3–5 mm**)? With the geometric-bound caveat.

## 6. Data flow
```
GT pose ─► render silhouette(s) [look-at camera] ─► degrade ─► fit_pose (mono | stereo) ─► recovered pose
                                                                                              │
  UNGATED metric error: face_angle/dynamic_loft + point_to_face_uv,  true vs recovered ◄──────┘
                                                  │
                          aggregate over poses × severities ─► verdict
```

## 7. Validation strategy (TDD)
- **camera:** on-axis point (at the look-at target) → principal point; a point higher in world (+Z) → smaller `v`; player-left (+Y) → smaller `u` — guards the look-at convention.
- **in-frame (review #1):** for `mono_rig()` and `stereo_rig()`, the ball at origin **and** the nominal clubhead bounding box project with `0 ≤ u < width`, `0 ≤ v < height`.
- **headmesh:** procedural mesh is not mirror-symmetric about any world plane; finite verts; iron thin, driver bulky.
- **silhouette:** identity-pose mesh → expected area/centroid; `iou(m,m)=1`; disjoint → 0.
- **Machinery validation (review #3) — guards optimizer/rasterizer bugs:** with **no degradation**, recovery returns the true pose to **≤0.5° / ≤1 mm** **using the `distinctive_test_mesh` and/or STEREO**. This proves the renderer + optimizer are correct *independent of mono ambiguity*.
- **Realistic-mesh mono clean-recovery is a RESULT, not a gate:** run mono clean recovery on the frozen realistic mesh and **record** the error. If it's large, that is a finding (mono is geometrically ambiguous for a realistic clubhead) — **do not change the mesh to make it pass** (only fix a clear optimizer/rasterizer bug, which the machinery-validation test would also catch).
- **degrade:** deterministic given seed; severity monotonic (more blur → lower IoU vs clean).
- **posefit:** on a deliberately depth-ambiguous pose, **stereo translation error ≤ mono** (stereo resolves +X depth).
- **experiment:** produces distributions + a `verdict` dict; zero-degradation stereo → near-zero metric error.
- All green under `uv run --group research pytest research/club_pose/tests/`.

## 8. Success criteria (0B-1 gate)
1. Machinery validation passes (rasterizer + optimizer correct, proven via stereo / distinctive mesh).
2. The experiment produces **mono-vs-stereo metric-error distributions** (face angle, dynamic loft, impact offset/height) across realistic poses and degradation severities, with realistic-mesh mono recovery reported as a result.
3. A clear, evidence-based **verdict**: whether a single behind-ball camera can hit the single-camera tier (±3–5° face/loft, ~5–15 mm impact) and whether stereo reaches the stretch tier (±2°, ±3–5 mm) — i.e., the hardware recommendation, explicitly flagged as the **geometric/optimistic bound** (real-frame segmentation tested in 0B-2/3).

## 9. Error handling
- Optimizer non-convergence → `FitResult(success=False)`; excluded from stats with a **logged count** (no silent drop).
- Behind-camera / off-frame projections → clipped/flagged, not crashing.
- Empty/degenerate masks → `iou` defined (0 if exactly one empty; both empty → skipped with a count).
- `opencv-python` missing → clear `ImportError` at `sim` import (with the `--group research` hint).

## 10. Open questions / documented assumptions
- **Mesh fidelity (review #3):** the realistic mesh is **frozen before the verdict run**; realistic-mesh mono recovery is a measured result, not something to engineer to pass. Real CAD meshes (via `load_obj`, and 0B-2) are the higher-fidelity check.
- **Degradation realism:** modeled, not measured; severities are estimates that 0B-2/3 (real rendering + segmentation) will calibrate.
- **Optimizer local minima:** mitigated by prior seeding + multi-start; residual failures counted, not hidden.
- **Geometric verdict only:** excludes real-segmentation error, which can only worsen the mono case — so 0B-1's single-camera result is an **upper bound** on single-camera viability.
