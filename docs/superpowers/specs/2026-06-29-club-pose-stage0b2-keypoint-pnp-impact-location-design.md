# Stage 0B-2 — Keypoint→PnP impact-location feasibility (find-the-requirement) — design spec

- **Date:** 2026-06-29
- **Status:** Draft for review
- **Branch:** `feat/camera-club-data`
- **Related:** v2 research guide §1C/§1D (silhouette is the wrong primitive for precise metrics); Stage 0B-1 silhouette experiment (`research/club_pose/sim/`, falsified — stereo impact 12.7 mm light / 22.1 mm realistic vs the ≤3–5 mm bar); the 0A geometry core (curved-face template + impact-location metrics in `research/club_pose/`).

**Review responses (2026-06-29, rev 2)** — addressed an IDE code-review of rev 1, verified against the code:
1. **Visibility table corrected** — recomputed every keypoint's `n·(cam−p)` under `mono_rig()`; pure-lateral toe/heel were back-facing, replaced with genuine camera-facing rear-upper patches (§5.1).
2. **face_center aligned to the 0A template** — `default_template("driver")` is reused exactly; `face_center` moved to the real surface at body (50,0,0) (§5.1).
3. **PnP frame conversion made explicit** — body→world ↔ object→camera math spelled out + a round-trip test (§5.3, §6).
4. **Success-rate gate added** — cells record `n_attempted`/`n_ok`; a requirement boundary is only valid at `ok_rate ≥ 0.9` (guards the 0B-1 survivor-bias failure) (§5.4, §7).
5. **Stereo degeneracy + Kabsch reflection guard specified** (§5.3).
6. **Apples-to-apples baseline de-constrained** — report it on the same grid, don't assert a fixed mm (§2.4, §6).

## 1. Problem & the central question

Stage 0B-1 proved the behind-ball **silhouette** is insufficient for precise impact location and blind to face/loft. The literature's fix for rotation is a **feature-rich cue** (interior edges / keypoints), not the outline. But this stage exists to answer a deeper, decision-grade question:

> **Is the failure a CUE problem or a VANTAGE problem?**
> - **CUE problem** → richer features (named keypoints → PnP) recover pose → impact location reaches ≤3–5 mm → **build it** (this becomes the camera's impact-location method).
> - **VANTAGE problem** → from behind, the face-defining features are self-occluded and the visible features are a poorly-distributed crown/back cluster, so **no cue** — even perfect keypoints — constrains the face-pointing rotation → **re-scope to spin + coarse zones** (path B).

We decide this cheaply, in sim, using **keypoints → PnP** as the strongest realistic cue, with an **honest detection model** and a **find-the-requirement** methodology: never assume perfect keypoints; instead find the detectability needed to hit the bar, then judge whether that detectability is achievable markerless on a smooth metallic head from behind.

## 2. Goal & requirements

Build a sim that, on a **structured driver with labeled keypoints**, recovers pose via keypoint→PnP (mono) and keypoint-triangulation (stereo) under a **realistic keypoint-detection model** (visibility + localization noise + dropout), propagates the recovered pose to impact location through the 0A curved-face projection, and **sweeps** the detection/camera parameters to find the requirement to reach ≤3–5 mm — compared apples-to-apples against the silhouette baseline on the *same* mesh.

**Hard requirements (anti-self-deception — these are the point):**
1. **Realistic visibility.** A keypoint whose outward surface normal points away from the camera (e.g., the clubface and leading edge, viewed from behind) is **occluded** and unavailable to the solver. The solver may not use occluded points.
2. **Realistic noise.** Detected keypoints carry Gaussian image-plane localization noise `σ_px` (swept), not exact projections; plus an optional per-point dropout probability.
3. **Find the REQUIREMENT, not a pass/fail on idealized inputs.** The headline output is `impact_mm` as a function of `(σ_px, #visible keypoints, mono/stereo, baseline_mm, sensor resolution)`, and the **boundary** where `impact_mm ≤ 3–5 mm`.
4. **Apples-to-apples baseline.** Run the existing silhouette fitter on the **same structured mesh over the same pose/noise grid** and **report** its impact-location result (whatever it is) alongside the keypoint result — so the comparison is fair and the mesh isn't secretly doing the work. We do **not** assert a specific mm baseline (the silhouette number depends on the mesh shape); we assert only machinery validity.
5. **Honest verdict.** State the requirement **and** a feasibility judgment: is that detectability achievable markerless on a smooth, specular, near-symmetric driver from behind (per the keypoint-detector literature: textureless/specular/symmetric objects + a crown-clustered, face-occluded view)?
6. **Real-OBJ realism check.** Repeat the headline experiment on one real driver OBJ (`load_obj`) with hand-labeled keypoints; the qualitative finding must hold.

## 3. Scope

**In:** structured driver mesh + labeled keypoints; the keypoint-detection model (normal-based visibility + noise + dropout); mono PnP and stereo triangulation/Kabsch solvers; the find-the-requirement sweep + verdict; the silhouette-on-structured-mesh baseline; the real-OBJ check.

**Out:** interior-edge analysis-by-synthesis (the fallback cue for a later stage *iff* keypoints fall short); real-image segmentation or learned keypoint detectors; the face/loft D-plane fusion; camera hardware purchase; photorealism / lighting / specular rendering.

**Non-goal:** making keypoints "succeed." The outcome (a requirement + a feasibility judgment) **is** the deliverable, whichever way it lands.

## 4. Affected files

- **Create** `research/club_pose/sim/driverhead.py` — `structured_driver(params=DEFAULT) -> StructuredHead` where `StructuredHead` carries a `HeadMesh` (for silhouette parity) plus `keypoints: dict[str, Keypoint]`. `Keypoint = (name, xyz_body: (3,), normal_body: (3,))`. The **face region reuses the 0A curved face** (bulge & roll) so impact projection stays consistent with `template.point_to_face_uv`.
- **Create** `research/club_pose/sim/keypoints.py` — `detect(head, pose, camera, sigma_px, rng, dropout=0.0) -> list[Detection]` where `Detection = (name, xyz_body, uv)`. Applies: (a) **visibility** — keep iff `normal_world · (camera_center − point_world) > 0` **and** the projection is in-frame and in front; (b) **noise** — `uv += N(0, σ_px)`; (c) **dropout** — drop each surviving point with prob `dropout`.
- **Create** `research/club_pose/sim/posefit_kp.py` — `fit_pose_pnp(detections, camera, prior) -> KPFit` (mono `cv2.solvePnP`/`solvePnPRansac`) and `fit_pose_kp_stereo(det_L, det_R, cameras, prior) -> KPFit` (triangulate by matched name → reflection-guarded `Kabsch`). Both **convert explicitly** between `ClubheadPose` (body→world) and solvePnP's object→camera frame (see §5.3). `KPFit = (pose, n_used, ok)`; `ok=False` on degenerate/failed solves (reported, never silently dropped).
- **Create** `research/club_pose/sim/experiment_kp.py` — `run_kp_experiment(n, category, sigma_px, baseline_mm, intrinsics, dropout, mode, seed) -> rows` (rows carry `n_attempted`/`n_ok` per cell); `kp_verdict(grid) -> {requirement_table, impact_mm_vs_sigma, ok_rate}` with the requirement boundary **gated at `ok_rate ≥ 0.9`**. Reuses `raw_metrics` + the curved-face projection from `experiment.py`/0A for impact location, and the existing silhouette path for the apples-to-apples baseline.
- **Add asset** `research/club_pose/sim/assets/driver.obj` (sourced generic driver) + `research/club_pose/sim/assets/driver_keypoints.json` (hand-labeled body-frame keypoints + normals) for the realism check.
- **Tests** (TDD): `test_sim_driverhead.py`, `test_sim_keypoints.py`, `test_sim_posefit_kp.py`, `test_sim_experiment_kp.py`.
- **Leave unchanged:** `headmesh.py` `procedural()` (the convex-hull blob) stays as the silhouette baseline mesh; `camera.py`, `silhouette.py`, `degrade.py`, the 0A core.

## 5. Method

### 5.1 Structured driver + keypoints (`driverhead.py`)
Body frame (per `headmesh` convention): **+X ≈ face/front, +Y = toe, −Y = heel, +Z = up**, origin at head geometric center. Generic driver extents ≈ heel-toe 115 mm, face-to-back 110 mm, crown-to-sole 60 mm. **Reuse `default_template("driver")` exactly** for the face (face center at body **(50, 0, 0)**, 10.5° static loft, bulge/roll 254 mm) so impact-location projection is the identical 0A path and the apples-to-apples baseline is fair. Build a closed surface (lofted/hull over the named control points + intermediate ring points) so it renders a driver-ish silhouette; the **named control points double as the labeled keypoints** (anchored to anatomy).

Default keypoints (body mm, outward normal), with behind-visibility **computed at identity pose under `mono_rig()`** (`cam_center=(−1200,0,300)`) via the §5.2 rule `n·(cam_center − p) > 0`:

| Name | xyz (mm) | normal | dot | Visible? |
|---|---|---|---|---|
| `crown_apex` | (−10, 0, +30) | (0, 0, 1) | +270 | ✅ |
| `crown_back` | (−50, 0, +18) | (−0.6, 0, +0.8) | +916 | ✅ |
| `crown_toe` | (−15, +40, +24) | (−0.2, +0.5, +0.84) | +449 | ✅ |
| `crown_heel` | (−15, −38, +24) | (−0.2, −0.5, +0.84) | +450 | ✅ |
| `hosel_top` | (−8, −52, +52) | (−0.5, −0.5, +0.7) | +744 | ✅ |
| `hosel_base` | (−6, −48, +28) | (−0.4, −0.7, +0.6) | +607 | ✅ |
| `back_skirt` | (−50, 0, −10) | (−0.85, 0, −0.5) | +822 | ✅ |
| `sole_center` | (−10, 0, −28) | (0, 0, −1) | −328 | ❌ down |
| `face_center` | (+50, 0, 0) | ≈(0.98, 0, 0.18) | −1174 | ❌ occluded |
| `leading_edge_toe` | (+44, +40, −18) | (+0.7, 0, −0.7) | −1094 | ❌ occluded |
| `leading_edge_heel` | (+44, −38, −18) | (+0.7, 0, −0.7) | ≈−1090 | ❌ occluded |
| `topline_toe` | (+40, +35, +20) | (+0.6, 0, +0.6) | −576 | ❌ occluded |

**The crux, now self-consistent with the visibility rule:** from behind the solver gets **7 keypoints, all on the rear/crown/hosel hemisphere (x ∈ [−50, −6]), none on the +X face.** Impact lands on the face at x ≈ +50 — constrained only by rigid-body extrapolation across a ~75 mm lever from the visible cluster's centroid (~x=−25), so a small rotation error maps to a large face-point error. Whether 7 rear-clustered points pin the face-pointing rotation well enough is exactly the experiment's question. **Pure silhouette-tangent extrema** (the literal toe/heel limb, normal ⟂ view) are deliberately **excluded** — their 3D correspondence slides along the limb with pose, injecting systematic PnP error; `crown_toe`/`crown_heel` are the rear-upper corners (genuinely camera-facing), not the tangent.

### 5.2 Keypoint detection model (`keypoints.py`)
For each keypoint: transform to world via `pose`, project via `camera`. Keep iff **in front, in-frame, and front-facing** (`normal_world · (cam_center − point_world) > 0`). Add `N(0, σ_px)` to `uv`. Optionally drop with prob `dropout`. Return surviving `(name, xyz_body, uv)`. Visibility-by-normal is an approximation (ignores concave self-occlusion subtleties) — acceptable for a convex-ish head, noted as a limitation.

### 5.3 Solvers (`posefit_kp.py`)
**Frame conventions are explicit (these bugs are silent if left implicit).** `cv2.solvePnP` works in **object→camera** extrinsics; `ClubheadPose` is **body→world** (`p_world = R_wb·p_body + t_wb`); `Camera` stores **world→camera** rows `R_wc` + `center_world`. Conversions:
- body→world ⇒ object→camera (for solvePnP input/seed): `R_oc = R_wc @ R_wb`, `t_oc = R_wc @ (t_wb − center_world)`, `rvec = Rodrigues(R_oc)`.
- solvePnP output (R_oc, t_oc) ⇒ body→world (for `ClubheadPose`): `R_wb = R_wc.T @ R_oc`, `t_wb = R_wc.T @ t_oc + center_world`.

- **Mono:** `K = [[fx,0,cx],[0,fy,cy],[0,0,1]]`, `dist=0`; `cv2.solvePnP(obj_pts_body, img_pts, K, dist, useExtrinsicGuess=True, flags=SOLVEPNP_ITERATIVE, rvec0/tvec0 = converted prior)`. **ITERATIVE least-squares over ALL detections — no RANSAC** (RANSAC's inlier rejection would mask the very noise-propagation this experiment measures). Needs ≥4 points and a **non-collinearity check** — the **second-largest** singular value of the centered body points > tol (i.e. rank ≥ 2; coplanar/4-point sets are valid, only collinear sets are degenerate) → else `ok=False`. (The visible 7 are nearly a shell on the back — watch conditioning.)
- **Stereo:** per-camera projection `P = K @ [R_wc | −R_wc·center_world]`; match detections by name across L/R; `cv2.triangulatePoints` → world points; **Kabsch with reflection guard** to fit the body keypoint model → world: `H = Σ(p_body−p̄_body)(p_world−p̄_world)^T`; `U,S,Vt = svd(H)`; `d = sign(det(Vt.T @ U.T))`; `R_wb = Vt.T @ diag(1,1,d) @ U.T`; `t_wb = p̄_world − R_wb·p̄_body`. Needs ≥3 matched names **and non-collinear** triangulated points — the **second-largest** centered singular value > tol (exactly-3 non-collinear points are valid for Kabsch; only collinear sets are degenerate) → else `ok=False`.
- Output pose → `raw_metrics` (0A curved-face projection) → `(offset_mm, height_mm)` → error vs the true pose's impact point (**true ball center held fixed in world**, as in 0B-1).

### 5.4 Find-the-requirement experiment (`experiment_kp.py`)
Per cell: sample `n` realistic delivered poses (reuse `pose_for_delivered` + ball placement from `experiment.py`), run detection + solve, record `impact_mm = hypot(offset_err, height_err)`, `rot_err_deg`, `n_used`, and the camera_range/in-plane split. Sweep:
- `σ_px ∈ {0.5, 1, 2, 3, 5}`
- visible-keypoint subset size (e.g., the 6 behind-visible, then ablate to 5/4)
- `mode ∈ {mono, stereo}`, `baseline_mm ∈ {100, 150, 200}`
- `intrinsics ∈ {IMX296-1.58MP, OV9281-1MP-class, a 5MP-class}` (px/mm sweep)

Every cell records `n_attempted` and `n_ok` (degenerate/failed solves count as attempts, never silently dropped). `kp_verdict` emits: (a) `ok_rate` and the `impact_mm` median (over OK solves) per cell; (b) the **requirement boundary** — smallest detectability with median `impact_mm ≤ 3–5 mm` **at `ok_rate ≥ 0.9`** (a cell below that success floor is marked *unreliable* and never counted as meeting the bar — the explicit guard against the 0B-1 survivor-bias failure); (c) the silhouette baseline on the same mesh+grid for comparison.

### 5.5 Apples-to-apples + real-OBJ
Run the existing `fit_pose_stereo` silhouette fitter on `structured_driver().mesh` and confirm ~12–22 mm (fair baseline). Then load `assets/driver.obj` + labeled keypoints and re-run the headline `σ_px`/mode cells; the qualitative conclusion (reaches the bar / doesn't) must agree with the procedural head.

## 6. Validation strategy (TDD)
- **driverhead:** keypoints sit within the mesh AABB; `face_center` at (50,0,0) round-trips through `default_template("driver").point_to_face_uv` (`|u|,|v|,|signed_dist| ≈ 0`); the mesh renders a non-empty, driver-proportioned silhouette.
- **visibility:** from `mono_rig()`, `face_center`/`leading_edge_*`/`sole_center`/`topline_*` are **occluded** and `crown_apex`/`crown_back`/`crown_toe`/`crown_heel`/`hosel_*`/`back_skirt` are **visible** — matching the computed dot signs in §5.1 (directly asserts the central asymmetry).
- **detection noise:** with `σ_px=0`, detected `uv` equals the exact projection; with `σ_px=2`, the empirical std over many draws ≈ 2 px.
- **frame round-trip:** convert a known body→world pose to object→camera and back ⇒ identity (guards the §5.3 conversion).
- **PnP machinery (mono, σ=0, all visible non-degenerate pts):** recovers rotation/translation to a tight tolerance — proving the solver **and the frame conversion** are correct when the cue is clean.
- **stereo machinery (σ=0):** recovers depth (range error ≪ mono); the Kabsch reflection guard is exercised on a reflection-prone configuration.
- **degradation monotonicity:** median `impact_mm` increases with `σ_px` (sanity of the sweep).
- **success-rate exposed:** the verdict reports `n_attempted`/`n_ok`/`ok_rate`; a cell with `ok_rate < 0.9` is marked *unreliable* and not counted toward a requirement boundary.
- **apples-to-apples:** silhouette fitter on the structured mesh over the same grid (state `severity`, `seed`, `n`) — **report** the baseline; assert only that it is substantially worse than the σ=0 keypoint machinery (no hard mm guarantee).
- All green under `uv run --group research pytest research/club_pose/tests/ -v`.

## 7. Success criteria (gate)
1. Structured driver + labeled keypoints with correct behind-visibility (face/leading-edge occluded), face region consistent with the 0A template.
2. Honest detection model (normal-visibility + noise + dropout) and correct mono-PnP / stereo-Kabsch solvers (machinery validated at σ=0).
3. The find-the-requirement experiment runs and emits the `impact_mm`-vs-`σ_px`/mode surface + per-cell `ok_rate` + the requirement boundary (declared only at `ok_rate ≥ 0.9`) + the silhouette baseline, on both the procedural head and the real OBJ.
4. A written **verdict**: the detectability requirement to reach ≤3–5 mm (or "unreachable from behind"), plus the feasibility judgment → the explicit fork (build the keypoint path **vs** re-scope to spin + coarse zones).

## 8. Risks / notes
- **The key risk IS the finding.** Behind-visible keypoints are a crown/back/hosel cluster with limited depth spread and **none on the face**, so PnP may be poorly conditioned for the face-pointing rotation — i.e., even perfect keypoints may not reach the bar. That is a legitimate, decisive outcome (→ path B). **Do not "rescue" it by granting the solver occluded face keypoints** or by shrinking `σ_px` below what a markerless metallic head could plausibly yield.
- **Optimistic abstractions retained** from 0B-1 (perfect ball center; detection modeled as true-projection + Gaussian noise; no real segmentation/specular effects). So a **negative** result here is conservative — reality is harder.
- **Normal-based visibility** is an approximation (ignores concave self-occlusion); fine for a convex-ish head, noted.
- **Visibility is recomputed per sampled pose**, not fixed to the §5.1 identity-pose dots; the experiment poses vary (face ±5°, loft, head-center offsets), so a keypoint near the visibility boundary may flicker in/out — that is realistic and intended.
- **Pure silhouette-tangent extrema are excluded** by design (§5.1): their 3D body correspondence is pose-dependent, so feeding a fixed body point to PnP would inject systematic error. This further limits the usable behind-ball keypoint count — itself part of the finding.
- **solvePnP** needs the (known) intrinsics and ≥4 non-coplanar points; degenerate solves are reported, not hidden.
- **Real-OBJ keypoint labels** are hand-placed (some subjectivity); the realism check is qualitative corroboration, not a second precise measurement.
