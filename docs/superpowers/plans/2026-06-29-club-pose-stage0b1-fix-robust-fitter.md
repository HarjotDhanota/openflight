# Stage 0B-1 Fix — Robust Unified Pose Fitter + Failure-Rate Gate — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the gamed two-fitter `posefit.py` with a single, validated, robust coarse-to-fine pose fitter used by *both* the tests and the experiment, and add a failure-rate gate so the mono-vs-stereo verdict is trustworthy.

**Architecture:** One `_fit` (real silhouette IoU+chamfer cost) with a `scale` coarse-to-fine speedup and depth-axis seeding; no mesh-category branch. The experiment uses the same fitter the machinery test validates. A test gates the experiment's stereo success rate at low degradation.

**Tech Stack:** Python 3.10+, numpy, scipy, opencv-python (already in the `research` dependency group), pytest.

**Spec:** `docs/superpowers/specs/2026-06-29-club-pose-stage0b1-fix-robust-unified-fitter-design.md`.

## Global Constraints

- **Existing package:** `research/club_pose/sim/` (camera, headmesh, silhouette, degrade, posefit, experiment) + `research/club_pose/` (0A core). Do NOT modify the 0A core or camera/headmesh/degrade.
- **One fitter, no `mesh.category` branch.** Delete `_fit_fast`, `_fit_precise`, `_fast_cost`, `_observed_features`, `_projected_hull_features`, `_sample_hull` and the hull-proxy approach. `fit_pose_mono`/`fit_pose_stereo` both call a single `_fit` using the real `(1−IoU)+W·chamfer` silhouette cost.
- **Coarse-to-fine** via a `scale` param on `render_silhouette`; depth seeds along `cameras[0].R_wc[2]` (the optical axis).
- **Do NOT loosen** the machinery tolerances (≤0.5°/≤1 mm) or tune meshes to make mono look better. Mono outcome in the experiment is a *result*.
- **Test gate:** `uv run --group research pytest research/club_pose/tests/ -v`. (Windows: if `uv` not on PATH, use the winget `uv.exe` full path.)
- **Commits:** conventional; **no Claude co-author footer** (Codex is the implementer). Branch: `feat/camera-club-data`.
- **Runtime note:** the real fitter is slower than the deleted proxy; keep test `n` small (≤8). The verdict artifact (n≥30) may take several minutes — acceptable (artifact, not a test).

---

### Task 1: Add `scale` to `render_silhouette`

**Files:**
- Modify: `research/club_pose/sim/silhouette.py` (the `render_silhouette` function)
- Test: `research/club_pose/tests/test_sim_silhouette.py` (add one test)

**Interfaces:**
- Produces: `render_silhouette(mesh, pose, camera, scale=1.0) -> mask (bool)` — renders at `round(width·scale) × round(height·scale)`.

- [ ] **Step 1: Write the failing test (append to the existing test file)**

Append to `research/club_pose/tests/test_sim_silhouette.py`:
```python
def test_render_scale_downsamples():
    from club_pose.sim.camera import IMX296

    mask = render_silhouette(procedural("driver"), _identity(), mono_rig(), scale=0.5)
    assert abs(mask.shape[0] - round(IMX296.height * 0.5)) <= 1
    assert abs(mask.shape[1] - round(IMX296.width * 0.5)) <= 1
    assert mask.sum() > 0
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --group research pytest research/club_pose/tests/test_sim_silhouette.py::test_render_scale_downsamples -v`
Expected: FAIL (`render_silhouette() got an unexpected keyword argument 'scale'`).

- [ ] **Step 3: Replace `render_silhouette` in `research/club_pose/sim/silhouette.py`**

```python
def render_silhouette(mesh, pose, camera, scale: float = 1.0) -> np.ndarray:
    world = mesh.transformed(pose)
    pix, in_front = camera.project(world)
    if scale != 1.0:
        pix = pix * scale
        h = int(round(camera.intrinsics.height * scale))
        w = int(round(camera.intrinsics.width * scale))
    else:
        h, w = camera.intrinsics.height, camera.intrinsics.width
    mask = np.zeros((h, w), dtype=np.uint8)
    pix_i = np.round(pix).astype(np.int32)
    for tri in mesh.faces:
        if not (in_front[tri[0]] and in_front[tri[1]] and in_front[tri[2]]):
            continue
        cv2.fillConvexPoly(mask, pix_i[tri], 1)
    return mask.astype(bool)
```

- [ ] **Step 4: Run to verify pass (whole silhouette file)**

Run: `uv run --group research pytest research/club_pose/tests/test_sim_silhouette.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add research/club_pose/sim/silhouette.py research/club_pose/tests/test_sim_silhouette.py
git commit -m "feat(club_pose.sim): add coarse-to-fine scale param to render_silhouette"
```

---

### Task 2: Unified robust pose fitter (delete the gamed split)

**Files:**
- Rewrite: `research/club_pose/sim/posefit.py`
- Rewrite: `research/club_pose/tests/test_sim_posefit.py`

**Interfaces:**
- Consumes: `render_silhouette(…, scale=)`, `iou`, `chamfer`, `HeadMesh`, `Camera`, `ClubheadPose`.
- Produces: `FitResult(pose, iou, success, n_evals)`; `fit_pose_mono(observed_mask, mesh, camera, prior_pose)`; `fit_pose_stereo(observed_masks, mesh, cameras, prior_pose)` — both via a single `_fit` with **no** `mesh.category` branch.

- [ ] **Step 1: Write the failing tests (replace the whole file)**

`research/club_pose/tests/test_sim_posefit.py`:
```python
import numpy as np
from scipy.spatial.transform import Rotation

from club_pose.sim.camera import mono_rig, stereo_rig
from club_pose.sim.headmesh import distinctive_test_mesh, procedural
from club_pose.sim.posefit import fit_pose_mono, fit_pose_stereo
from club_pose.sim.silhouette import render_silhouette
from club_pose.types import ClubheadPose


def _pose(rotvec, t):
    return ClubheadPose(Rotation.from_rotvec(rotvec), np.array(t, float))


def _rot_err_deg(a, b):
    return float(np.degrees((a.rotation.inv() * b.rotation).magnitude()))


def test_machinery_clean_recovery_mono_and_stereo():
    # The SAME fitter the experiment uses must recover the distinctive mesh near-exactly,
    # clean, for BOTH rigs (validates the unified optimizer).
    mesh = distinctive_test_mesh()
    true = _pose([0.05, -0.1, 0.08], [3.0, -2.0, 5.0])
    prior = _pose([0.0, 0.0, 0.0], [0.0, 0.0, 0.0])

    mono = mono_rig()
    rm = fit_pose_mono(render_silhouette(mesh, true, mono), mesh, mono, prior)
    assert rm.success
    assert _rot_err_deg(true, rm.pose) <= 0.5
    assert np.linalg.norm(rm.pose.translation - true.translation) <= 1.0

    cams = stereo_rig()
    rs = fit_pose_stereo([render_silhouette(mesh, true, c) for c in cams], mesh, cams, prior)
    assert rs.success
    assert _rot_err_deg(true, rs.pose) <= 0.5
    assert np.linalg.norm(rs.pose.translation - true.translation) <= 1.0


def test_realistic_mesh_clean_stereo_recovery():
    # the fitter must also work on the realistic (less distinctive) driver mesh, clean, in stereo
    mesh = procedural("driver")
    cams = stereo_rig()
    true = _pose([0.02, -0.04, 0.03], [4.0, -3.0, 6.0])
    prior = _pose([0.0, 0.0, 0.0], [0.0, 0.0, 0.0])
    rs = fit_pose_stereo([render_silhouette(mesh, true, c) for c in cams], mesh, cams, prior)
    assert rs.success
    assert _rot_err_deg(true, rs.pose) <= 1.5
    assert np.linalg.norm(rs.pose.translation - true.translation) <= 3.0


def test_stereo_beats_mono_on_depth_ambiguity():
    mesh = distinctive_test_mesh()
    mono = mono_rig()
    cams = stereo_rig()
    true = _pose([0.0, 0.0, 0.0], [0.0, 0.0, 0.0])
    prior = _pose([0.02, 0.02, 0.02], [10.0, 10.0, 10.0])
    rm = fit_pose_mono(render_silhouette(mesh, true, mono), mesh, mono, prior)
    rs = fit_pose_stereo([render_silhouette(mesh, true, c) for c in cams], mesh, cams, prior)
    err_mono = np.linalg.norm(rm.pose.translation - true.translation)
    err_stereo = np.linalg.norm(rs.pose.translation - true.translation)
    assert err_stereo <= err_mono + 1e-6
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --group research pytest research/club_pose/tests/test_sim_posefit.py -v`
Expected: FAIL (the new `test_realistic_mesh_clean_stereo_recovery` and/or `test_machinery_clean_recovery_mono_and_stereo` fail against the current gamed fitter, or import errors after the rewrite).

- [ ] **Step 3: Replace the whole file `research/club_pose/sim/posefit.py`**

```python
"""Analysis-by-synthesis pose recovery — ONE unified coarse-to-fine fitter (tests AND experiment)."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.optimize import minimize
from scipy.spatial.transform import Rotation

import cv2

from ..types import ClubheadPose
from .silhouette import chamfer, iou, render_silhouette

_CHAMFER_WEIGHT = 0.5
_COARSE_SCALE = 0.25
_SUCCESS_IOU = 0.9


@dataclass
class FitResult:
    pose: ClubheadPose
    iou: float
    success: bool
    n_evals: int


def _pose_from_x(x: np.ndarray) -> ClubheadPose:
    return ClubheadPose(Rotation.from_rotvec(x[:3]), np.asarray(x[3:6], dtype=float))


def _x_from_pose(pose: ClubheadPose) -> np.ndarray:
    return np.concatenate([pose.rotation.as_rotvec(), pose.translation])


def _resize_mask(mask: np.ndarray, scale: float) -> np.ndarray:
    h = int(round(mask.shape[0] * scale))
    w = int(round(mask.shape[1] * scale))
    return cv2.resize(mask.astype(np.uint8), (w, h), interpolation=cv2.INTER_NEAREST).astype(bool)


def _cost(x, observed, mesh, cameras, scale) -> float:
    pose = _pose_from_x(x)
    total = 0.0
    for obs, cam in zip(observed, cameras):
        rendered = render_silhouette(mesh, pose, cam, scale=scale)
        diag = float(np.hypot(*obs.shape))
        total += (1.0 - iou(rendered, obs)) + _CHAMFER_WEIGHT * chamfer(rendered, obs) / diag
    return total


def _coarse_starts(x0: np.ndarray, cameras) -> list[np.ndarray]:
    forward = np.asarray(cameras[0].R_wc[2], dtype=float)  # optical axis = depth direction
    rot_jitter = [np.zeros(3)]
    for axis in range(3):
        for s in (-0.1, 0.1):
            v = np.zeros(3)
            v[axis] = s
            rot_jitter.append(v)
    range_offsets = [0.0, 20.0, -20.0, 40.0, -40.0]
    base_rot = Rotation.from_rotvec(x0[:3])
    starts = []
    for rj in rot_jitter:
        rot = (Rotation.from_rotvec(rj) * base_rot).as_rotvec()
        for d in range_offsets:
            s = np.empty(6)
            s[:3] = rot
            s[3:6] = x0[3:6] + d * forward
            starts.append(s)
    unique = {}
    for s in starts:
        unique[tuple(np.round(s, 6))] = s
    return list(unique.values())


def _pattern_refine(x, observed, mesh, cameras):
    best = np.asarray(x, dtype=float).copy()
    best_cost = _cost(best, observed, mesh, cameras, 1.0)
    n_evals = 1
    steps = np.array([0.02, 0.02, 0.02, 2.0, 2.0, 2.0])
    while steps.max() > 5e-4:
        improved = False
        for j in range(6):
            for sign in (1.0, -1.0):
                cand = best.copy()
                cand[j] += sign * steps[j]
                c = _cost(cand, observed, mesh, cameras, 1.0)
                n_evals += 1
                if c < best_cost:
                    best, best_cost, improved = cand, c, True
        if not improved:
            steps *= 0.5
    return best, best_cost, n_evals


def _fit(observed_masks, mesh, cameras, prior_pose) -> FitResult:
    cameras = list(cameras)
    x0 = _x_from_pose(prior_pose)
    obs_coarse = [_resize_mask(m, _COARSE_SCALE) for m in observed_masks]
    starts = _coarse_starts(x0, cameras)
    ranked = sorted((_cost(s, obs_coarse, mesh, cameras, _COARSE_SCALE), s) for s in starts)
    n_evals = len(starts)
    best, best_cost = ranked[0][1], np.inf
    for _coarse_c, s in ranked[:4]:
        rc = minimize(_cost, s, args=(obs_coarse, mesh, cameras, _COARSE_SCALE),
                      method="Powell", options={"xtol": 2e-3, "ftol": 1e-4, "maxiter": 200})
        n_evals += int(rc.nfev)
        rf = minimize(_cost, rc.x, args=(observed_masks, mesh, cameras, 1.0),
                      method="Powell", options={"xtol": 1e-4, "ftol": 1e-6, "maxiter": 400})
        n_evals += int(rf.nfev)
        x_ref, c_ref, pe = _pattern_refine(rf.x, observed_masks, mesh, cameras)
        n_evals += pe
        if c_ref < best_cost:
            best, best_cost = x_ref, c_ref
    pose = _pose_from_x(best)
    final_iou = float(np.mean([
        iou(render_silhouette(mesh, pose, c), o) for o, c in zip(observed_masks, cameras)
    ]))
    return FitResult(pose=pose, iou=final_iou, success=final_iou >= _SUCCESS_IOU, n_evals=n_evals)


def fit_pose_mono(observed_mask, mesh, camera, prior_pose) -> FitResult:
    return _fit([observed_mask], mesh, [camera], prior_pose)


def fit_pose_stereo(observed_masks, mesh, cameras, prior_pose) -> FitResult:
    return _fit(list(observed_masks), mesh, list(cameras), prior_pose)
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run --group research pytest research/club_pose/tests/test_sim_posefit.py -v`
Expected: PASS (3 passed). If `test_machinery_clean_recovery_mono_and_stereo` is flaky, raise the full-res Powell `maxiter` to 600 or add range offsets to `_coarse_starts` — do NOT loosen the 0.5°/1 mm tolerance.

- [ ] **Step 5: Commit**

```bash
git add research/club_pose/sim/posefit.py research/club_pose/tests/test_sim_posefit.py
git commit -m "fix(club_pose.sim): unify on one validated coarse-to-fine pose fitter"
```

---

### Task 3: Failure-rate gate + `success_rate` in the verdict

**Files:**
- Modify: `research/club_pose/sim/experiment.py` (the `verdict` function only)
- Test: `research/club_pose/tests/test_sim_experiment.py` (add one test)

**Interfaces:**
- Consumes: `run_experiment(n, category, severity, baseline_mm, seed) -> dict` (unchanged; already returns `mono`, `stereo`, `n_fail_mono`, `n_fail_stereo`).
- Produces: `verdict(results)` output now includes `success_rate` per tag.

- [ ] **Step 1: Write the failing tests (append to the existing file)**

Append to `research/club_pose/tests/test_sim_experiment.py`:
```python
def test_verdict_reports_success_rate():
    res = run_experiment(n=3, category="iron", severity="none", baseline_mm=150.0, seed=0)
    v = verdict(res)
    assert "success_rate" in v["mono"] and "success_rate" in v["stereo"]


def test_experiment_stereo_success_rate_high_at_low_severity():
    # The anti-gaming gate: the experiment's stereo path must converge broadly (>=~90%),
    # so the verdict is not read off a survivor-biased subset.
    res = run_experiment(n=8, category="driver", severity="light", baseline_mm=150.0, seed=3)
    assert res["n_fail_stereo"] <= 1
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --group research pytest research/club_pose/tests/test_sim_experiment.py::test_verdict_reports_success_rate -v`
Expected: FAIL (`success_rate` not in verdict output).

- [ ] **Step 3: Replace the `verdict` function in `research/club_pose/sim/experiment.py`**

```python
def verdict(results) -> dict:
    out = {}
    for tag in ("mono", "stereo"):
        rows = results[tag]
        n_fail = results[f"n_fail_{tag}"]
        attempted = len(rows) + n_fail
        face_loft = [max(r["face_err_deg"], r["loft_err_deg"]) for r in rows]
        impact = [np.hypot(r["offset_err_mm"], r["height_err_mm"]) for r in rows]
        out[tag] = {
            "n": len(rows),
            "n_fail": n_fail,
            "success_rate": float(len(rows) / attempted) if attempted else float("nan"),
            "face_loft_deg_median": float(np.median(face_loft)) if face_loft else float("nan"),
            "impact_mm_median": float(np.median(impact)) if impact else float("nan"),
            "camera_range_error_mm_median": _median(rows, "camera_range_error_mm"),
        }
    out["note"] = (
        "Geometric/optimistic bound (no real-frame segmentation). Single-camera result is an upper bound. "
        "Trust the verdict only when success_rate is high (esp. stereo)."
    )
    return out
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run --group research pytest research/club_pose/tests/test_sim_experiment.py -v`
Expected: PASS (5 passed — the 3 original + 2 new). The success-rate gate exercises the real fitter, so this test is slower (~1–3 min); that's expected.

- [ ] **Step 5: Commit**

```bash
git add research/club_pose/sim/experiment.py research/club_pose/tests/test_sim_experiment.py
git commit -m "feat(club_pose.sim): add failure-rate gate and success_rate to verdict"
```

---

### Task 4: Full suite + trustworthy verdict artifact

**Files:** none (verification + artifact).

- [ ] **Step 1: Run the full suite**

Run: `uv run --group research pytest research/club_pose/tests/ -v`
Expected: PASS (all 0A + 0B-1 tests green, including the new machinery mono+stereo, realistic-mesh, and failure-rate gate tests).

- [ ] **Step 2: Capture the now-trustworthy verdict**

```bash
uv run --group research python -c "import sys; sys.path.insert(0,'research'); from club_pose.sim.experiment import run_experiment, verdict; import json; print('DRIVER', json.dumps(verdict(run_experiment(n=30, category='driver', severity='realistic', seed=1)), indent=2)); print('IRON', json.dumps(verdict(run_experiment(n=30, category='iron', severity='realistic', seed=1)), indent=2))"
```
Expected: two verdict dicts. **Check `success_rate` (especially stereo) is high (≥~0.85);** if it is, the face/loft and impact medians are the trustworthy single-vs-stereo answer. (May take several minutes — it is an artifact, not a test.)

- [ ] **Step 3: Commit (record the artifact if you save it)**

No code change; if you write the verdict output to a file, commit it. Otherwise report the printed dicts.

---

## Self-Review (completed by plan author)

**Spec coverage:** unified fitter, no category branch, real cost (§2.1, §5) → Task 2; coarse-to-fine `scale` (§2.2) → Task 1; depth-axis seeds (§2.3) → Task 2 `_coarse_starts`; failure-rate gate (§2.4) → Task 3; machinery mono+stereo on distinctive mesh (§2.5) + realistic-mesh stereo clean (§2.6) → Task 2 tests; `success_rate` in verdict (§4 files) → Task 3; re-run artifact (§7.4) → Task 4. Deleted-code requirement (`_fit_fast`/`_fit_precise`/proxy) → Task 2 replaces the whole file.

**Placeholder scan:** every step has concrete code/commands; no TBD/"similar to".

**Type consistency:** `FitResult(pose, iou, success, n_evals)`, `fit_pose_mono`/`fit_pose_stereo`, `render_silhouette(..., scale=)`, `verdict(...)["mono"]["success_rate"]` are consistent across tasks and match the existing `run_experiment` return keys (`mono`, `stereo`, `n_fail_mono`, `n_fail_stereo`, and per-row `face_err_deg`/`loft_err_deg`/`offset_err_mm`/`height_err_mm`/`camera_range_error_mm`).
