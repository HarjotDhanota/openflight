# Stage 0B-1 — Mono-vs-Stereo Pose-Recovery Experiment (silhouette analysis-by-synthesis) — design spec

- **Date:** 2026-06-28
- **Status:** Draft for review
- **Branch:** `feat/camera-club-data`
- **Related:** [Stage 0A spec](2026-06-28-club-pose-geometry-core-design.md); [v2 research guide](../../Personal%20Research/markerless-club-data-guide-v2-research-corrected.md) §1B, §2.4, §6B. This is **Stage 0B-1** of the full Stage 0B pipeline (0B-2 BlenderProc renderer, 0B-3 BiRefNet segmentation, 0B-4 pose estimators on real-ish frames, 0B-5 integration — separate later specs, GPU/Linux-server targets).

## 1. Purpose & context

The open question that gates hardware spend is **single camera vs stereo** for the club metrics (face angle, dynamic loft, impact location). Stage 0A produced *metric-error-per-pose-error coefficients* but cannot supply the **pose-error magnitudes** mono vs stereo — that needs a camera projection model (spec 0A §11).

0B-1 supplies them **geometrically**: render a generic clubhead **silhouette** at known poses from a behind-ball camera (mono and stereo), apply **modeled degradations**, recover pose by **silhouette analysis-by-synthesis**, and propagate the recovered-pose error through the 0A budget into face/loft/impact error — the **mono-vs-stereo verdict**. Pure Python (numpy/scipy/opencv); runs in the Windows/Codex dev flow.

**Scope of the verdict (honest):** 0B-1 is the **geometric/optimistic bound** — idealized binary silhouettes + *modeled* degradations. It answers "is the pose information geometrically there for one camera vs two." It does **not** test real-frame segmentation (that only worsens the mono case), which is 0B-2/0B-3. So 0B-1's single-camera result is an *upper bound* on single-camera viability.

## 2. Scope

**In:** pinhole camera model (mono + stereo rigs); a procedural generic clubhead mesh (driver + iron) anchored in the 0A **body frame** (+ optional OBJ loader); a pure-Python silhouette rasterizer + IoU/chamfer; modeled degradations (motion blur, segmentation/boundary noise, truncation, ball/shaft occlusion); an analysis-by-synthesis pose fitter (mono + stereo); the experiment harness + verdict; TDD tests.
**Out (later stages):** photorealistic rendering (BlenderProc — 0B-2), learned segmentation/keypoints (0B-3/4), real images, hardware, GPU. **Keypoint+PnP estimator deferred** — silhouette fitting is the honest markerless baseline for the decision.
**Non-goal:** sim-to-real fidelity. 0B-1 is the geometric bound, not a transfer test.

## 3. Location, dependencies, standards

- **Location:** `research/club_pose/sim/` (new subpackage) — reuses 0A `types`, `template`, `metrics`, `sensitivity` directly.
- **New dependency:** `opencv-python` (mask fill / IoU / morphology). Add it to the sandbox env (`uv add opencv-python` or a research dependency group). numpy/scipy/pytest already present.
- **Test gate:** `uv run pytest research/club_pose/tests/ -v` (env note: if `uv` isn't on PATH, use the winget `uv.exe` full path or a fresh terminal). Built test-first.

## 4. Conventions & camera model

**World frame = 0A's** (right-handed, +X downrange, +Y player-left, +Z up; origin = ball at address).

**Pinhole intrinsics:** `K = [[fx,0,cx],[0,fy,cy],[0,0,1]]`, image `width × height`. Default from §6A.1 optics: IMX296 **1456×1088**, 3.45 µm px, **16 mm** lens → `fx = fy = 16 / 0.00345 ≈ 4638 px`, `cx = width/2`, `cy = height/2`.

**Camera extrinsic (explicit, to avoid sign bugs):** camera center `C` in world; world→camera rotation `R_wc` whose rows are the camera axes expressed in world coords, for a behind-ball camera looking **+X** with image-right = world-right (−Y) and image-down = world-down (−Z):
```
R_wc = [[ 0, -1,  0],   # image right  = world -Y (player right)
        [ 0,  0, -1],   # image down   = world -Z
        [ 1,  0,  0]]   # optical axis = world +X (downrange)
```
Projection of a world point `p`: `p_cam = R_wc @ (p − C)`; pixel `u = fx·(p_cam_x / p_cam_z) + cx`, `v = fy·(p_cam_y / p_cam_z) + cy`; valid only if `p_cam_z > 0` (in front of camera). (Sanity: ball at origin with `C=(−D,0,h)` → `p_cam=(0,h,D)` → projects at `(cx, cy + fy·h/D)`, i.e. below center for a raised camera — correct.)

**Rigs (defaults from §6A.1):**
- **Mono:** one camera at `C = (−1200, 0, 300)` mm (≈3–4 ft behind, ~0.3 m up), `R_wc` above.
- **Stereo:** two cameras at `C ± (0, baseline/2, 0)` (horizontal baseline along world Y), both `R_wc` above (parallel). Default `baseline = 150 mm`. Horizontal baseline resolves the +X depth that mono cannot.

## 5. Components (six small, testable units in `research/club_pose/sim/`)

### 5.1 `camera.py`
- `CameraIntrinsics(fx, fy, cx, cy, width, height)`.
- `Camera(intrinsics, center_world, R_wc)`: `project(points_world) -> (pixels Nx2, in_front mask)`.
- `mono_rig() -> Camera`; `stereo_rig(baseline_mm=150.0) -> (Camera, Camera)`.

### 5.2 `headmesh.py`
- `HeadMesh(vertices Nx3 (body coords), faces Mx3 (indices), category)`; `transformed(pose) -> world vertices`.
- `procedural(category) -> HeadMesh`: generate a low-poly **asymmetric** head — driver (rounded crown blob + sole + a hosel stub offset toward the heel) / iron (blade slab + hosel + distinct toe/heel/topline). Anchored to the 0A body frame (origin = head geometric center; +X ≈ face normal at zero loft, consistent with `template.py`). **Must be asymmetric enough that a clean silhouette determines pose** (guarded by the clean-recovery test §7).
- `load_obj(path) -> HeadMesh`: minimal OBJ vertex/face loader (optional; lets real CAD meshes plug in for higher fidelity).

### 5.3 `silhouette.py`
- `render_silhouette(mesh, pose, camera) -> mask (H×W bool)`: project transformed vertices; fill every triangle (`cv2.fillConvexPoly`) on the mask; union → silhouette (binary; no z-buffer needed for a single closed mesh's outline).
- `iou(a, b) -> float`; `chamfer(a, b) -> float` (mean boundary distance, via distance transform).

### 5.4 `degrade.py`
- `DegradationParams(blur_px, blur_dir, boundary_sigma_px, truncate_frac, occlude_ball, occlude_shaft)` + severity presets `none / light / realistic / severe`.
- `degrade(mask, params, rng) -> mask`: motion blur (smear along `blur_dir` by `blur_px`), boundary/segmentation noise (random morphological erode/dilate + boundary jitter), truncation (drop a frame edge), ball/shaft occlusion (subtract a region). Deterministic given a `numpy` `rng` seed.

### 5.5 `posefit.py`
- `FitResult(pose, iou, success, n_evals)`.
- `fit_pose_mono(observed_mask, mesh, camera, prior_pose) -> FitResult`: minimize `1 − IoU(render(pose), observed)` (+ optional chamfer term) over 6-DOF (rotvec 3 + translation 3) via `scipy.optimize` (Powell/Nelder-Mead), seeded from `prior_pose` with a small multi-start to dodge local minima.
- `fit_pose_stereo(observed_masks, mesh, cameras, prior_pose) -> FitResult`: minimize the **sum** of `1 − IoU` across both cameras.
- `prior_pose`: a coarse seed = true pose perturbed by a configurable amount (stand-in for the radar velocity/ball-at-tee prior). The experiment reports sensitivity to prior quality.

### 5.6 `experiment.py`
- `sample_poses(n, category, rng) -> list[ClubheadPose]`: realistic impact-zone positions + face-angle / dynamic-loft / lie / path ranges (built via 0A `groundtruth` helpers + realistic spans).
- `run_experiment(n, category, severity, baseline_mm, rng) -> results`: per pose → render clean silhouette(s) → degrade → `fit_pose_mono` and `fit_pose_stereo` → pose error (rotation °, translation mm split into **depth (+X)** vs **in-plane**) → 0A `compute_metrics(true)` vs `compute_metrics(recovered)` → metric error (face angle, dynamic loft, impact offset/height). Aggregate distributions mono vs stereo; sweep `severity`.
- `verdict(results) -> dict`: does **mono** meet the single-camera tier (face/loft **±3–5°**, impact **~5–15 mm**)? does **stereo** meet the stretch tier (**±2°**, **±3–5 mm**)? With the geometric-bound caveat.

## 6. Data flow
```
GT pose ─► render silhouette(s) [camera] ─► degrade ─► fit_pose (mono | stereo) ─► recovered pose
                                                                                      │
  0A compute_metrics(GT) vs compute_metrics(recovered) ◄──────────────────────────────┘
                                                  │
                          metric error ─► aggregate over poses × severities ─► verdict
```

## 7. Validation strategy (TDD)
- **camera:** on-axis point → principal point; a point world-+Z (higher) → smaller `v` (up in image); world-+Y (player-left) → smaller `u` (left in image) — guards the `R_wc` convention.
- **headmesh:** procedural mesh is **not** mirror-symmetric about any world plane (asymmetry check); finite verts; iron blade is thin, driver bulky.
- **silhouette:** identity-pose mesh renders expected area/centroid; `iou(m,m)=1`; disjoint masks `iou=0`.
- **Clean-recovery sanity (the critical gate):** with **no degradation**, `fit_pose_mono` recovers the true pose to **≤0.5° / ≤1 mm** from a perturbed seed — proves the mesh is pose-informative and the optimizer is correct. *If this fails, the mesh is too symmetric/ambiguous — fix the mesh, not the tolerance.*
- **degrade:** deterministic given seed; severity monotonic (more blur → lower IoU vs clean).
- **posefit:** on a deliberately depth-ambiguous pose, **stereo translation error ≤ mono** (stereo resolves +X depth).
- **experiment:** produces distributions + a `verdict` dict; zero-degradation → near-zero metric error.
- All green under `uv run pytest research/club_pose/tests/`.

## 8. Success criteria (0B-1 gate)
1. Clean-recovery sanity passes (mesh pose-informative; optimizer correct).
2. The experiment produces **mono-vs-stereo metric-error distributions** (face angle, dynamic loft, impact offset/height) across realistic poses and degradation severities.
3. A clear, evidence-based **verdict**: whether a single behind-ball camera can hit the single-camera tier (±3–5° face/loft, ~5–15 mm impact) and whether stereo reaches the stretch tier (±2°, ±3–5 mm) — i.e., the hardware recommendation, explicitly flagged as the **geometric/optimistic bound** (real-frame segmentation tested in 0B-2/3).

## 9. Error handling
- Optimizer non-convergence → `FitResult(success=False)`; excluded from stats with a **logged count** (no silent drop).
- Behind-camera / off-frame projections → clipped/flagged, not crashing.
- Empty/degenerate masks → `iou` defined (0 if exactly one empty; both empty → skipped with a count).
- `opencv-python` missing → clear `ImportError` at import.

## 10. Open questions / documented assumptions
- **Mesh fidelity caveat:** the procedural mesh must be realistically asymmetric; the clean-recovery gate guards correctness, but a too-simple mesh could bias the verdict. Real CAD meshes (via `load_obj`, and 0B-2) are the higher-fidelity check. Flagged in the verdict output.
- **Degradation realism:** modeled, not measured; severities are estimates that 0B-2/3 (real rendering + segmentation) will calibrate.
- **Optimizer local minima:** mitigated by prior seeding + multi-start; residual failures counted, not hidden.
- **Geometric verdict only:** excludes real-segmentation error, which can only worsen the mono case — so 0B-1's single-camera result is an **upper bound** on single-camera viability.
