# Stage 0B-3 — Photoreal detectability of behind-ball iron features (in-sim upper bound) — design spec

- **Date:** 2026-06-30
- **Status:** Draft for review
- **Branch:** `feat/camera-club-data`
- **Related:** Stage 0B-2 (keypoint→PnP: behind-ball is **detection-limited, not vantage-limited** — geometry recovers impact ~1 mm / **face 0.10–0.21° / loft 0.25–0.52° at σ=1–2 px**, now recorded by `experiment_kp.py` (`face_err_deg`/`loft_err_deg`) + a committed table in `RESULTS_0B2.md`); v2 guide §1D; the 0A **iron** template (`default_template("iron")`).

**Review responses (rev 2)** — addressed a Codex spec audit, verified against the code:
1. **0B-2 face/loft evidence committed** — added `face_err_deg`/`loft_err_deg` to `experiment_kp.py` + a committed table in `RESULTS_0B2.md`; the numbers above are reproducible, not chat-only.
2. **CAD orientation made exact + 0A-consistent** — align the measured face normal to the **iron template's** `face_center_normal_body()` (template loft set to the club's 36°), not to a bare "+X" (§5.1).
3. **Kill-switch semantics fixed** — a classical failure means "the cheap path is out, escalate to the learned detector," **not** a global NO-GO; a markerless NO-GO requires the learned upper bound (§2, §5.4, §5.7).
4. **Motion blur tied to physics** — sweep explicit strobe pulse widths → px streak, not arbitrary Blender settings (§5.2, §5.4).
5. **Detection metric hardened** — require a *dominant* primitive (peak-to-clutter), penalize dense/ambiguous edge maps; nearest-anything is insufficient (§5.4).
6. **Blender-absent ≠ verdict** — the final verdict cannot be produced without rendering; skips cover only the non-render scaffolding (§3, §6, §7).

## 1. Problem & the single question

0B-2 (independently verified) proved the behind-ball **geometry** recovers impact + face + loft to sub-degree/mm **if** keypoints are localizable to ~1–2 px. The sole remaining unknown is **detection**: can a real detector hit that px budget on a real club under realistic imaging — **specular metal, lighting, and motion blur**? This stage measures it, as an **in-sim upper bound** (train/evaluate on our own renders → no domain gap), on the real **Titleist 690CB 7-iron** CAD model the maintainer supplied.

**Why an iron, and why it's the strongest test:** from behind the ball the camera sees the **back of the iron** — cavity/muscle edges, topline, hosel — which is **feature-rich**, the *opposite* of a driver's smooth painted crown. So:
- **Learned upper-bound failure on a feature-rich iron back → markerless *driver* testing (smooth crown, strictly harder) is not worth pursuing yet** — this stage does not itself test the driver, so it narrows rather than closes that risk.
- **Pass → irons are viable, and the driver becomes the next (harder) test.**

## 2. Goal & requirements

Render photoreal behind-ball iron frames from the 690CB CAD with ground-truth feature pixels; measure **per-feature px localization + detection rate** (classical first, learned only if warranted); feed the **detectable subset + its measured noise** back through the 0B-2 geometry (iron template) → impact/face/loft accuracy → **GO/NO-GO**.

**Hard requirements (the honesty spine):**
1. **In-sim upper bound is a *necessary* gate.** Failing on our own renders — where the detector has every advantage and no domain gap — means real-world fails. A pass is **not sufficient** (sim-to-real is a separate future stage).
2. **GT feature pixels are computed with OUR pinhole** (`camera.project`), with the **Blender camera set to match `IMX296` + `mono_rig` exactly** — so GT stays consistent with the 0B-2 geometry, and a "camera-match" test enforces it.
3. **Detection is measured HONESTLY — no GT snapping, no clutter-crediting.** Detectors search the image/region; per-feature error = distance from GT to the detector's **dominant** primitive in the expected region (with a peak-to-clutter dominance score, per §5.4) — a feature counts as detected only if the primitive is **both close *and* dominant**, so a dense/ambiguous edge map cannot fake a hit. The GT never seeds the detector.
4. **Specular is the crux, not an obstacle to remove.** Render a **polished-chrome** material with varied lighting; the moving highlights/reflections that corrupt edges are the phenomenon under test. No matte cheat.
5. **Loop-back uses MEASURED per-feature σ + dropout** (not an assumed σ) through `fit_pose_pnp` with `default_template("iron")` (loft override 36°).
6. **Classical is a cheap LOWER-BOUND filter, not the verdict.** Classical **success** → **skip the learned detector and proceed to the loop-back (§5.6)**; **GO is declared only when the loop-back clears the impact/face/loft bar under the measured noise** (localizing features is necessary, not sufficient). Classical **failure** → **escalate to the learned detector** (the true upper bound) — a classical failure is *evidence against the cheap path, not proof markerless is impossible*. A definitive markerless **NO-GO requires the learned detector to also fail.** So a Windows/classical-only pass concludes **GO** (localized *and* loop-back clears) or **"inconclusive — learned detector required,"** never a hard NO-GO.
7. **Feature set = SHAPE features the CAD actually has and that are visible from behind** (topline + endpoints, cavity/muscle-back edges, hosel junction, trailing sole). Texture/badge decals are **out of first scope** (the CAD has no textures; edges are the honest generic case).

## 3. Scope

**In:** CAD ingest + orientation to the body frame; a Blender scene/material/camera that matches our pinhole; GT feature projection; a **modest specular render set + classical detection (the kill-switch)**; **[gated]** a larger domain-randomized dataset + a small learned detector; the loop-back to iron geometry; the verdict.

**Out:** the **driver** (only if the iron passes); **sim-to-real** / real photos; texture/badge **decals** (first pass); the **left-handed** iron (a mirror, later); the learned detector is **optional/deferred**.

**Non-goal:** making detection "succeed." The measured px budget + the GO/NO-GO **is** the deliverable.

## 4. Affected files / new components

New package `research/club_pose/detect/`:
- `assets/` — the **690CB source STL copied into the repo** (canonical, version-controlled) + the derived **oriented mesh + body-frame transform + feature-keypoint JSON**.
- `cad.py` — load the binary STL; derive + apply the canonical body-frame transform; expose the oriented mesh; a render of the 3 canonical views (like the 0B audit).
- `features.py` — the ~6–8 named iron feature keypoints (body coords + outward normals) + GT projection via `camera.project` + the normal-visibility rule (reused from `keypoints.py`).
- `render.py` — Blender (`bpy`) scene: camera built from `IMX296` + `mono_rig`, polished-chrome material, lighting/domain-randomization, motion blur; render frames to disk.
- `classical.py` — Canny/Hough (lines: topline, sole), Harris/Shi-Tomasi (corners: endpoints, hosel junction), template match; the **dominant-primitive** localization error + dominance + detection rate per feature (§5.4).
- `loopback.py` — measured per-feature (σ, dropout) → `fit_pose_pnp` with the **iron** template → impact/face/loft (reuses the 0B-2 `raw_metrics` / `pose_for_delivered`).
- `experiment_detect.py` — orchestrate render → detect → measure → loop-back → verdict; `RESULTS_0B3.md`.
- Tests under `research/club_pose/tests/`.

**Blender dependency** (decided in the plan): either `bpy` as a pip module or a system Blender invoked headless. Render-dependent tests **skip with a clear message** if Blender is absent (same pattern as the 0B-2 OBJ skip), so the non-render *scaffolding* (CAD orientation, feature GT, classical-on-a-committed-fixture-image, loop-back logic) stays CI-green without Blender. **But the stage VERDICT requires rendering:** a Blender-absent run can exercise/greenlight only the scaffolding — it **cannot** produce the GO/NO-GO (skipping ≠ passing the stage).

## 5. Method (phases)

### 5.1 CAD ingest + orientation (`cad.py`)
Copy the maintainer's `690CB 7-iron.STL` (binary, 26,238 tris, mm, **head + hosel**, extents ≈ 119×79×38 mm) into `assets/`. **The orientation must land in 0A's zero-loft body frame or it silently poisons GT features, PnP, and `raw_metrics`.** 0A represents loft by *rotating the face axes*, so the template's face normal is **not** +X — it is +X rotated up by the static loft. Therefore align to the **template normal**, not to +X:

- Working template `T = default_template("iron").with_loft_override(36.0)` (this club's real loft; the 0A default is 34°). Note `T.face_center_normal_body()` = +X rotated up 36° toward +Z, and `T.face_center_offset` = (30, 0, 0).
- Derive from geometry: RANSAC-fit the dominant large planar region → the **face** (measured normal `n_face`, centroid `c_face`); fit the thin near-cylindrical region → the **hosel axis** `a_hosel`.
- Rotate the mesh so **`n_face` aligns with `T.face_center_normal_body()`** (the loft-tilted normal, *not* bare +X), `a_hosel` matches the **lie** (60° from horizontal, heel-up), and **+Y = toe** (RH).
- Translate so `c_face` maps to `T.face_center_offset` = **(30, 0, 0)**.

**Verify (asserts, not eyeballing):** `T.point_to_face_uv(oriented c_face)` round-trips to `|u|,|v|,|signed_dist| ≈ 0`; the oriented face normal ≈ `T.face_center_normal_body()` within a few degrees; sole at min projected-down extent; extents ≈ a 7-iron. A **one-time human check** against the 4 reference photos fixes the handedness/toe sign (RH). Emit the oriented mesh + transform + feature JSON + a 3-view render. **All downstream stages use `T` (the loft-36 iron template), not the bare default.**

### 5.2 Blender scene, material, camera (`render.py`)
Build the Blender camera from `IMX296` intrinsics (focal length + sensor width + principal-point shift) and the `mono_rig` extrinsic (center (−1200, 0, 300), look-at origin), so a render aligns with `camera.project`. Material: Principled BSDF **polished chrome** (metallic=1, low roughness). Lighting: HDRI / area lights, varied. Pose the iron at the ball via the reused delivered-pose sampling (iron template, loft 36°).

**Motion blur is a swept PHYSICAL parameter, not a Blender knob.** Model the strobe/exposure as a pulse of width `τ`; the head moves at `v ≈ 40–50 m/s`, so the streak length ≈ `v·τ` (τ=10 µs → ~0.4–0.5 mm; 20 µs → ~0.9 mm; 50 µs → ~2.3 mm), projected to px via the camera. Configure Blender's object motion blur so the rendered streak matches `v·τ` for each swept **`τ ∈ {≈0 (sharp control), 10, 20, 50} µs`**. Detectability is reported **vs τ**, so the result is pinned to physics (the achievable strobe pulse width) rather than tunable by arbitrary shutter settings.

### 5.3 Feature set + GT (`features.py`)
~6–8 named features (body coords + outward normal), keeping only those **visible from behind at ~36° loft** via the normal rule: `topline_toe`, `topline_heel`, `topline_mid`, `hosel_junction`, `cavity_top_toe`, `cavity_top_heel`, `trailing_sole`, (`leading_edge_*` likely **occluded** at 36° — confirmed by the rule, not assumed). GT 2D = `camera.project(pose.body_to_world(feature))`.

### 5.4 Modest render set + classical detection — the cheap LOWER-BOUND probe (`classical.py`)
Render ~50–100 frames across a few lighting/pose conditions and the swept blur `τ`. Feature-appropriate classical detectors search each image (Hough lines for topline/sole, Harris/Shi-Tomasi for corners/junction, template match for the hosel). **Honest metric — a DOMINANT primitive, not nearest-anything:** for each feature, take the detector's *strongest* response in the expected region and record (a) its **distance to GT**, (b) a **dominance score** (peak-to-second-peak ratio, or edge-density/clutter near GT), and (c) whether it is both **close** (≤ a few px) *and* **dominant**. A dense/ambiguous edge map (many specular edges near GT) scores as **low-dominance → not detected**, even if some edge is coincidentally close — this stops clutter from faking a hit. Aggregate per feature: median px error (of dominant detections), dominance, detection rate — all **vs τ and lighting**. Save **annotated example renders** (GT vs detected) for the user.

**Cheap-path call (this is NOT the NO-GO gate):** classical **succeeds** (dominant primitives localized ≤ a few px across features) → **skip §5.5 and go straight to the loop-back (§5.6)**; the **GO is confirmed only when the loop-back clears the bar** under the measured per-feature σ. Classical **fails** (no dominant primitive / specular clutter wins) → **escalate to §5.5**; a classical failure does *not* conclude NO-GO.

### 5.5 Learned detector — the UPPER BOUND (required for any NO-GO)
The learned detector — **not** the classical pass — defines whether markerless detection is *possible*; it is the best case. It runs when classical fails (to reach a verdict), and may be skipped only when classical already succeeds (GO). A larger domain-randomized dataset (lighting/pose/blur-τ/background) → a small heatmap keypoint net, trained on synthetic, evaluated on **held-out synthetic** → per-feature px error = the **best-case localization**. (Compute: deferred to a GPU box; a modest net/dataset can run on Windows CPU.) **Only a learned-detector failure — no feature reaches ~1–2 px even in-sim — justifies a NO-GO.**

### 5.6 Loop-back to iron geometry (`loopback.py`)
Per detectable feature: measured `(σ_px, dropout)`. Sample delivered iron poses (`pose_for_delivered`, iron template, loft-36 override), project the features, add the **measured** per-feature noise + dropout, `fit_pose_pnp`, `raw_metrics` → impact/face/loft error medians + `ok_rate` (the 0B-2 honesty gate).

### 5.7 Verdict (`experiment_detect.py`, `RESULTS_0B3.md`)
Combine per-feature detectability (px + dominance + rate, **vs τ**) with the loop-back impact/face/loft accuracy **under measured noise**. Three outcomes:
- **GO** — classical (or, if needed, learned) localizes enough features tightly → the loop-back clears the bar → irons viable; build the driver test next.
- **NO-GO** — the **learned (upper-bound)** detector also fails (no feature reaches ~1–2 px even in-sim) → commit to the D-plane + marked-ball-spin architecture.
- **INCONCLUSIVE** — classical failed and the learned detector has not been run (e.g., no GPU yet) → the cheap path is out; a definitive call is pending §5.5.

**The verdict requires rendering** (§6): with no Blender there is no measurement and therefore no GO/NO-GO.

## 6. Validation strategy (TDD)
- **CAD:** oriented mesh extents ≈ a 7-iron; **oriented face normal ≈ `T.face_center_normal_body()`** (loft-36 iron template — *not* bare +X); `T.point_to_face_uv(oriented c_face)` round-trips ≈ 0; sole at min projected-down extent; hosel at max-Z/heel; a `mono_rig` silhouette is iron-shaped/non-empty.
- **Camera match (the critical consistency gate):** a known 3D point projects to the same pixel via `camera.project` **and** the Blender camera, within ~1 px.
- **Feature visibility:** from behind at 36° loft, the expected features are visible/occluded per the normal rule (asserts which survive).
- **Classical works when signal exists:** on a synthetic **high-contrast matte** control render, the topline edge is localized to <2 px (proves the detector is sound); on the **chrome+specular** render, **report** the number (no assertion).
- **Loop-back:** with measured σ, `run_detect_experiment` returns impact/face/loft medians + `ok_rate`; the `ok_rate ≥ 0.9` gate is honored.
- **Blender guard:** render tests skip cleanly when Blender/`bpy` is absent — but per §3 the *verdict* is not producible then (skipping is not passing the stage).
- All non-render tests green under `uv run --group research pytest research/club_pose/tests/ -v`.

## 7. Success criteria (gate)
1. Canonical oriented iron asset + feature keypoints committed and verified (extents/normals/visibility).
2. **Blender render matches `camera.project` to ~1 px** (the consistency gate) — without it, GT is meaningless.
3. The classical probe runs, emits per-feature px error + **dominance** + detection rate + annotated examples, and a clear **GO** (classical succeeds) / **escalate-to-learned** (classical fails) call — a NO-GO requires §5.5.
4. The loop-back produces impact/face/loft accuracy **under measured noise** (loft-36 iron template) and feeds the three-way **GO / NO-GO / INCONCLUSIVE** verdict (§5.7) — which requires rendering to have run.
5. Honest artifact `RESULTS_0B3.md` + folded into the v2 guide.

## 8. Risks / notes
- **Blender-as-dependency** (bpy module vs system Blender; headless on Windows) — the plan pins one and guards tests.
- **CAD orientation is fiddly** — one-time human/photo check, backed by geometric asserts.
- **Chrome specular fidelity is the crux** — approximate materials could be *easier* or *harder* than real chrome; flag prominently in the verdict (this is why in-sim is only a necessary gate).
- **690CB is a clean players' cavity-back** (moderate features) — a game-improvement iron would be an easier case, a blade harder; the verdict is club-specific.
- **The honest detection metric (dominant-primitive + peak-to-clutter, no GT snap) is essential** — a GT-seeded *or* nearest-anything metric would fabricate success; guard it.
- **In-sim pass ≠ real-world.** Sim-to-real (real photos, domain gap) is a separate future stage, only earned if this passes.
