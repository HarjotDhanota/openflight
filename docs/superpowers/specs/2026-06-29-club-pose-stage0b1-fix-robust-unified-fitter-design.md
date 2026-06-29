# Stage 0B-1 Fix — Robust Unified Pose Fitter + Failure-Rate Gate — design spec

- **Date:** 2026-06-29
- **Status:** Draft for review
- **Branch:** `feat/camera-club-data`
- **Related:** [Stage 0B-1 spec](2026-06-28-club-pose-stage0b1-mono-vs-stereo-experiment-design.md); the existing `research/club_pose/sim/` (built, 65 tests green, but the verdict is untrustworthy — see below).

## 1. Problem (why the current verdict is invalid)

The 0B-1 implementation passes all tests but produces an **untrustworthy verdict**:
- The pose fitter was split into two paths: a strong one (`_fit_precise`, full IoU+chamfer cost + many starts) used **only** for the machinery-validation test (mesh `category=="test"`), and a weak one (`_fit_fast`, a convex-hull feature proxy cost, **single start**, `maxiter=80`) used for the **actual experiment** (driver/iron meshes). So **the machinery test validates a fitter the experiment never uses.**
- On the weak experiment fitter, **70–80% of fits fail** (driver mono 22/30, iron mono 24/30; stereo 13–16/30). The `verdict` medians are computed over only the few survivors → **survivor bias** → optimistic and statistically thin. The decision cannot be made on this.

The qualitative direction is right (stereo `camera_range_error` ≪ mono — depth is resolved), but the magnitudes are not trustworthy.

## 2. Goal & requirements

Make the experiment's pose recovery trustworthy by **unifying on one validated, robust fitter** and **gating on the failure rate**, so a future verdict reflects geometry — not optimizer weakness or survivor bias.

**Hard requirements (anti-gaming — these are the point):**
1. **One fitter, no mesh-category branch.** `fit_pose_mono` / `fit_pose_stereo` use a single `_fit` for **all** meshes (distinctive, driver, iron). Delete `_fit_fast` / `_fit_precise` / the hull-feature proxy cost. The cost is the real silhouette `(1 − IoU) + W·chamfer` on the rendered vs observed masks.
2. **Coarse-to-fine for speed (legitimate, quality-preserving).** A `scale` parameter renders/compares at downsampled resolution for the global stage, then refines at full resolution — so the *real* fitter is fast enough for the experiment. No proxy cost.
3. **Depth seeding.** The coarse global starts include offsets **along the camera optical axis** (the range/depth ambiguity direction), so mono fits don't stall on depth.
4. **Failure-rate gate.** A test asserts the **experiment path** (the same `run_experiment`) achieves a **high stereo success rate (≥90%) at low degradation** — i.e. the fitter actually converges broadly, not just on a special mesh. The verdict cannot be read off survivor-biased data.
5. **Machinery validation via STEREO** (which resolves depth) on the **distinctive mesh**, clean, ≤0.7°/≤4 mm — proving the unified fitter (the one the experiment uses) is correct. Tolerances are **pixel-quantization-honest**, not sub-mm.
6. **Realistic-mesh validation:** stereo recovers the realistic driver mesh **clean** to ≤1.5°/≤5 mm.
7. **Mono is depth-ambiguous — a documented finding, not a fitter failure** (discovered during implementation: a perfect silhouette match, IoU=1.0, still left ~1.78 mm mono translation error). A single binary silhouette does **not** pin depth — a few-mm optical-axis shift is near-invisible (IoU ≈ 1). So the mono machinery test only requires a **silhouette match (`iou ≥ 0.9`)**; demanding sub-mm mono translation is geometrically impossible. A deterministic test documents the depth-blindness, and `test_stereo_beats_mono` shows stereo resolves it. **This is the experiment's central conclusion, confirmed at the unit level.**

After these pass: **mono results in the experiment are trustworthy** — if mono is inaccurate (it will be, in depth → impact/loft), that is a genuine *geometric* result (the verdict), not a fitter artifact.

## 3. Scope

**In:** add `scale` to `render_silhouette`; rewrite `posefit.py` to a single coarse-to-fine fitter with depth-axis seeds (delete the split + proxy cost); add the failure-rate gate + realistic-mesh-clean + mono-machinery tests; expose `success_rate` in `verdict`; re-run and capture the verdict artifact.
**Out:** the rest of Stage 0B (renderer 0B-2, segmentation 0B-3, …); changing the camera model, mesh shapes, degradations, or the 0A core; photorealism.
**Non-goal:** making mono *succeed* — mono outcome is a result, not a target.

## 4. Affected files
- Modify: `research/club_pose/sim/silhouette.py` (add `scale=` to `render_silhouette`).
- Rewrite: `research/club_pose/sim/posefit.py` (single unified coarse-to-fine `_fit`; delete `_fit_fast`/`_fit_precise`/`_fast_cost`/hull helpers).
- Modify: `research/club_pose/sim/experiment.py` (add `success_rate` to `verdict`; no behavior change to `run_experiment` other than already returning `n`/`n_fail`).
- Tests: rewrite `test_sim_posefit.py` (unified-fitter machinery mono+stereo, realistic-mesh-clean stereo, stereo-beats-mono); extend `test_sim_experiment.py` (failure-rate gate, verdict has success_rate). Update `test_sim_silhouette.py` for the `scale` arg.

## 5. Method (unified `_fit`)
1. `x0` = prior pose as `[rotvec(3), translation(3)]`.
2. **Coarse starts:** `x0` × {a small rotation jitter set (identity + ±0.1 rad on each axis)} × {range offsets along `cameras[0].R_wc[2]` (the optical axis): 0, ±20, ±40 mm}. (~35 starts; deduped.)
3. **Coarse rank:** evaluate every start's cost at `scale=0.25` (downsampled observed masks). Cheap.
4. **Refine top 4:** Powell at `scale=0.25` (maxiter 200) → Powell at `scale=1.0` (maxiter 400) → a coordinate `_pattern_refine` at full res. Keep the lowest full-res cost.
5. `final_iou` = mean IoU at full res; `success = final_iou ≥ 0.9`.
6. `fit_pose_mono`/`fit_pose_stereo` both call `_fit` (1 or 2 cameras).

## 6. Validation strategy (TDD)
- **silhouette scale:** `render_silhouette(mesh,pose,cam,scale=0.5)` returns a mask of `≈ half` the dimensions; identity-pose nonempty.
- **mono depth-blindness (deterministic finding):** a ~3 mm optical-axis shift keeps the mono silhouette IoU ≥ 0.985 (mono can't pin depth).
- **machinery (stereo, distinctive mesh, clean):** ≤0.7°/≤4 mm from a perturbed prior.
- **realistic-mesh clean (stereo):** driver mesh → ≤1.5°/≤5 mm.
- **mono reaches a silhouette match:** mono distinctive clean → `success` (`iou ≥ 0.9`); its pose depth stays ambiguous — expected.
- **stereo-beats-mono:** on a depth-ambiguous case, stereo translation error ≤ mono.
- **failure-rate gate:** `run_experiment(n=10, severity="light")` → `n_fail_stereo ≤ 1` (≥90% stereo success). (Guards the survivor-bias problem; uses the experiment path.)
- **verdict has success_rate:** `verdict(...)` exposes `success_rate` per tag.
- All green under `uv run --group research pytest research/club_pose/tests/ -v`.

## 7. Success criteria (gate)
1. Single unified fitter (no category branch); proxy-cost path deleted.
2. Mono depth-blindness documented; stereo machinery (distinctive, clean) ≤0.7°/≤4 mm; realistic-mesh stereo clean ≤1.5°/≤5 mm; mono reaches `iou ≥ 0.9`.
3. Experiment stereo success ≥90% at low severity (failure-rate gate green).
4. Re-run `run_experiment` (n≥30, driver+iron, realistic) and capture the verdict artifact — now trustworthy (high stereo success; mono outcome = result). This is the input to the single-vs-stereo decision.

## 8. Risks / notes
- **Runtime:** the real fitter is slower than the proxy. Coarse-to-fine keeps it tractable; keep **test `n` small** (≤10). The full verdict artifact (n≥30) may take several minutes — acceptable (it is an artifact, not a test).
- **Mono may still fail/be inaccurate on realistic meshes** — that is now an honest geometric result (the fitter is validated), not a bug. Report it; do not tune the mesh or loosen tolerances to hide it.
- **Mono silhouette pose is depth-ambiguous (confirmed during implementation):** even with a perfect silhouette match (IoU=1.0), mono translation was ~1.78 mm off (a few-mm optical-axis shift is invisible). This is geometric — do NOT add tie-breakers / upsampled rendering / differential-evolution / giant start grids to "fix" it (they can't), and do NOT demand sub-mm mono translation. **Keep the fitter LEAN** (coarse-to-fine + depth-axis seeds + pattern refine) so the experiment stays tractable.
