# Stage 0B-2 — Keypoint→PnP impact-location feasibility — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Decide whether behind-ball impact location to ≤3–5 mm is achievable via keypoint→PnP (a CUE problem we can fix) or is fundamentally VANTAGE-limited (re-scope to spin + coarse zones), by building a structured-driver + keypoint-detection sim and **finding the detectability requirement** — never assuming perfect keypoints.

**Architecture:** Extends `research/club_pose/sim/`. A structured generic driver mesh carries labeled body-frame keypoints; a normal-based detection model gates visibility + adds Gaussian px-noise + dropout; mono `solvePnP` and stereo `triangulate+Kabsch` solvers recover pose with explicit body↔object-camera frame conversion; a sweep experiment propagates pose error to impact location through the existing 0A curved-face math and gates conclusions on `ok_rate`. A silhouette baseline on the same mesh and a real-OBJ realism check keep it honest.

**Tech Stack:** Python, numpy, scipy, OpenCV (`cv2`), pytest. Run via the winget `uv`: `uv run --group research pytest research/club_pose/tests/ -v`.

## Global Constraints

- **Behind-ball geometry is fixed** — use `mono_rig()` / `stereo_rig()` from `camera.py`; do not move the camera.
- **Reuse `default_template("driver")` exactly** for the face geometry and impact projection (face center body (50,0,0), 10.5° loft, bulge/roll 254 mm).
- **Strict normal-based visibility:** a keypoint is available to the solver **iff** `normal_world · (camera.center_world − point_world) > 0` and it projects in-front + in-frame. Occluded (face/leading-edge from behind) points are unavailable. **Pure silhouette-tangent extrema are excluded** (only camera-facing surface patches are keypoints).
- **Honesty gates:** degenerate/failed solves are recorded (`ok=False`), never silently dropped; a requirement boundary is valid only at **`ok_rate ≥ 0.9`**; keypoint detectability is **swept (σ_px), never assumed perfect**; the silhouette baseline is **reported, not asserted** to a fixed mm.
- **`uv` full path** on this box: `C:\Users\harjo\AppData\Local\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe` (tests run with `PYTHONPATH` already handled by the pytest rootdir).
- TDD, frequent commits. DRY, YAGNI.

---

### Task 1: Reframe the parked silhouette-baseline tests (translation-focused; rotation = documented dead-end)

The parked `posefit.py` (the unified silhouette fitter) is the **apples-to-apples baseline** for 0B-2. Its two failing tests assert a rotation bar the silhouette geometrically cannot meet (v2 §1D). Reframe them around **translation** (the impact-relevant quantity) so the suite is green and the fitter can be committed.

**Files:**
- Modify: `research/club_pose/tests/test_sim_posefit.py:32-53`

**Interfaces:**
- Consumes: `fit_pose_stereo`, `render_silhouette`, `distinctive_test_mesh`, `procedural` (unchanged).
- Produces: a green `test_sim_posefit.py` documenting that stereo silhouette recovers translation (not rotation).

- [ ] **Step 1: Replace the two rotation-asserting tests**

In `research/club_pose/tests/test_sim_posefit.py`, replace `test_machinery_stereo_clean_recovery` and `test_realistic_mesh_stereo_clean_recovery` with:

```python
def test_machinery_stereo_clean_recovery():
    # Stereo resolves depth, so the unified fitter recovers TRANSLATION (the impact-location-
    # relevant quantity) on the distinctive mesh. Rotation is NOT asserted tight: a silhouette is
    # information-poor for out-of-plane rotation (v2 guide §1C/§1D) — here it lands ~1 deg.
    mesh = distinctive_test_mesh()
    cams = stereo_rig()
    true = _pose([0.05, -0.1, 0.08], [3.0, -2.0, 5.0])
    prior = _pose([0.0, 0.0, 0.0], [0.0, 0.0, 0.0])
    rs = fit_pose_stereo([render_silhouette(mesh, true, c) for c in cams], mesh, cams, prior)
    assert rs.success
    assert np.linalg.norm(rs.pose.translation - true.translation) <= 4.0


def test_realistic_mesh_stereo_clean_recovery():
    # On a realistic (featureless) driver the silhouette couples rotation into translation; stereo
    # still recovers translation to a few mm. Rotation is NOT recovered (the §1D finding) and is
    # intentionally not asserted — that is the whole reason Stage 0B-2 exists.
    mesh = procedural("driver")
    cams = stereo_rig()
    true = _pose([0.02, -0.04, 0.03], [4.0, -3.0, 6.0])
    prior = _pose([0.0, 0.0, 0.0], [0.0, 0.0, 0.0])
    rs = fit_pose_stereo([render_silhouette(mesh, true, c) for c in cams], mesh, cams, prior)
    assert rs.success
    assert np.linalg.norm(rs.pose.translation - true.translation) <= 6.0
```

- [ ] **Step 2: Run the two tests to verify they pass**

Run: `uv run --group research pytest research/club_pose/tests/test_sim_posefit.py -v`
Expected: 5 passed (translation 0.90 mm ≤ 4.0; 3.45 mm ≤ 6.0).

- [ ] **Step 3: Commit**

```bash
git add research/club_pose/sim/posefit.py research/club_pose/tests/test_sim_posefit.py
git commit -m "test(club_pose.sim): reframe silhouette-baseline tests around translation (rotation = documented §1D dead-end)"
```

---

### Task 2: Structured driver mesh + labeled keypoints (`driverhead.py`)

**Files:**
- Create: `research/club_pose/sim/driverhead.py`
- Test: `research/club_pose/tests/test_sim_driverhead.py`

**Interfaces:**
- Consumes: `HeadMesh`, `_fib_sphere`, `_ArrayWithPtp` from `headmesh.py`; `default_template` from `..template`.
- Produces: `Keypoint(name, xyz, normal)`; `StructuredHead(mesh, keypoints: dict[str,Keypoint], template)`; `structured_driver() -> StructuredHead`; `driver_keypoints() -> dict[str,Keypoint]`.

- [ ] **Step 1: Write the failing test**

`research/club_pose/tests/test_sim_driverhead.py`:

```python
import numpy as np

from club_pose.sim.camera import mono_rig
from club_pose.sim.driverhead import driver_keypoints, structured_driver


def test_face_center_lies_on_template_face():
    head = structured_driver()
    proj = head.template.point_to_face_uv(head.keypoints["face_center"].xyz)
    assert abs(proj.u) < 1e-6 and abs(proj.v) < 1e-6
    assert abs(proj.signed_distance_mm) < 1e-6


def test_visibility_matches_computed_dot_signs():
    head = structured_driver()
    cam = mono_rig()
    visible = {"crown_apex", "crown_back", "crown_toe", "crown_heel",
               "hosel_top", "hosel_base", "back_skirt"}
    occluded = {"sole_center", "face_center", "leading_edge_toe",
                "leading_edge_heel", "topline_toe"}
    for name, kp in head.keypoints.items():
        dot = float(kp.normal @ (cam.center_world - kp.xyz))  # identity pose
        if name in visible:
            assert dot > 0, name
        if name in occluded:
            assert dot < 0, name


def test_mesh_renders_driverish_silhouette():
    from club_pose.sim.silhouette import render_silhouette
    from club_pose.types import ClubheadPose
    from scipy.spatial.transform import Rotation

    head = structured_driver()
    mask = render_silhouette(head.mesh, ClubheadPose(Rotation.identity(), np.zeros(3)), mono_rig())
    assert mask.sum() > 5000  # non-empty, head-sized
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run --group research pytest research/club_pose/tests/test_sim_driverhead.py -v`
Expected: FAIL — `ModuleNotFoundError: club_pose.sim.driverhead`.

- [ ] **Step 3: Implement `driverhead.py`**

```python
"""Structured generic driver: a driver-proportioned mesh + labeled body-frame keypoints."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.spatial import ConvexHull

from ..template import default_template
from .headmesh import _ArrayWithPtp, _fib_sphere, HeadMesh


@dataclass(frozen=True)
class Keypoint:
    name: str
    xyz: np.ndarray      # body coords (mm)
    normal: np.ndarray   # unit outward normal (body)


@dataclass(frozen=True)
class StructuredHead:
    mesh: HeadMesh
    keypoints: dict
    template: object


# body frame: +X face/front, +Y toe, -Y heel, +Z up. (name, xyz, normal)
_KP = {
    "crown_apex": ((-10, 0, 30), (0, 0, 1)),
    "crown_back": ((-50, 0, 18), (-0.6, 0, 0.8)),
    "crown_toe": ((-15, 40, 24), (-0.2, 0.5, 0.84)),
    "crown_heel": ((-15, -38, 24), (-0.2, -0.5, 0.84)),
    "hosel_top": ((-8, -52, 52), (-0.5, -0.5, 0.7)),
    "hosel_base": ((-6, -48, 28), (-0.4, -0.7, 0.6)),
    "back_skirt": ((-50, 0, -10), (-0.85, 0, -0.5)),
    "sole_center": ((-10, 0, -28), (0, 0, -1)),
    "face_center": ((50, 0, 0), (0.983, 0, 0.182)),
    "leading_edge_toe": ((44, 40, -18), (0.7, 0, -0.7)),
    "leading_edge_heel": ((44, -38, -18), (0.7, 0, -0.7)),
    "topline_toe": ((40, 35, 20), (0.6, 0, 0.6)),
}


def driver_keypoints() -> dict:
    out = {}
    for name, (p, n) in _KP.items():
        nv = np.asarray(n, dtype=float)
        out[name] = Keypoint(name, np.asarray(p, dtype=float), nv / np.linalg.norm(nv))
    return out


def structured_driver() -> StructuredHead:
    template = default_template("driver")
    kps = driver_keypoints()
    # driver-proportioned ellipsoid (face reaches ~+50 in X), with the keypoints forced onto the
    # hull so they are genuine surface points; the mesh is for the silhouette baseline only.
    body = _fib_sphere(140) * np.array([55.0, 58.0, 30.0]) + np.array([-5.0, 0.0, 0.0])
    anchors = np.array([k.xyz for k in kps.values()], dtype=float)
    pts = np.vstack([body, anchors])
    hull = ConvexHull(pts)
    verts = np.asarray(pts, dtype=float).view(_ArrayWithPtp)
    mesh = HeadMesh(verts, hull.simplices.astype(np.int64), "driver_structured")
    return StructuredHead(mesh=mesh, keypoints=kps, template=template)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group research pytest research/club_pose/tests/test_sim_driverhead.py -v`
Expected: 3 passed. (If `face_center` round-trip fails, the template offset is (50,0,0) at zero u/v — `face_center.xyz` must equal the template face center; it does.)

- [ ] **Step 5: Commit**

```bash
git add research/club_pose/sim/driverhead.py research/club_pose/tests/test_sim_driverhead.py
git commit -m "feat(club_pose.sim): structured driver mesh + labeled keypoints (0B-2)"
```

---

### Task 3: Keypoint detection model (`keypoints.py`)

**Files:**
- Create: `research/club_pose/sim/keypoints.py`
- Test: `research/club_pose/tests/test_sim_keypoints.py`

**Interfaces:**
- Consumes: `StructuredHead`/`Keypoint` (Task 2); `Camera` (`camera.py`); `ClubheadPose` (`..types`).
- Produces: `Detection(name, xyz_body, uv)`; `detect(head, pose, camera, sigma_px, rng, dropout=0.0) -> list[Detection]`.

- [ ] **Step 1: Write the failing test**

`research/club_pose/tests/test_sim_keypoints.py`:

```python
import numpy as np
from scipy.spatial.transform import Rotation

from club_pose.sim.camera import mono_rig
from club_pose.sim.driverhead import structured_driver
from club_pose.sim.keypoints import detect
from club_pose.types import ClubheadPose


def _identity():
    return ClubheadPose(Rotation.identity(), np.zeros(3))


def test_occluded_keypoints_are_dropped():
    head, cam = structured_driver(), mono_rig()
    names = {d.name for d in detect(head, _identity(), cam, 0.0, np.random.default_rng(0))}
    assert "face_center" not in names and "leading_edge_toe" not in names
    assert {"crown_apex", "crown_back", "hosel_top"} <= names


def test_zero_noise_is_exact_projection():
    head, cam = structured_driver(), mono_rig()
    dets = detect(head, _identity(), cam, 0.0, np.random.default_rng(0))
    kp = head.keypoints[dets[0].name]
    (uv,), _ = cam.project(kp.xyz[None, :])
    assert np.allclose(dets[0].uv, uv)


def test_noise_has_expected_std():
    head, cam = structured_driver(), mono_rig()
    rng = np.random.default_rng(1)
    name = "crown_apex"
    kp = head.keypoints[name]
    (uv0,), _ = cam.project(kp.xyz[None, :])
    samples = []
    for _ in range(2000):
        d = {x.name: x for x in detect(head, _identity(), cam, 2.0, rng)}[name]
        samples.append(d.uv - uv0)
    assert abs(np.std(samples) - 2.0) < 0.2
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run --group research pytest research/club_pose/tests/test_sim_keypoints.py -v`
Expected: FAIL — `ModuleNotFoundError: club_pose.sim.keypoints`.

- [ ] **Step 3: Implement `keypoints.py`**

```python
"""Honest keypoint-detection model: normal visibility + in-frame + Gaussian noise + dropout."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Detection:
    name: str
    xyz_body: np.ndarray
    uv: np.ndarray


def detect(head, pose, camera, sigma_px, rng, dropout=0.0):
    dets = []
    w, h = camera.intrinsics.width, camera.intrinsics.height
    for kp in head.keypoints.values():
        p_world = pose.body_to_world(kp.xyz)
        n_world = pose.direction_to_world(kp.normal)
        if float(n_world @ (camera.center_world - p_world)) <= 0.0:  # back-facing
            continue
        (uv,), in_front = camera.project(p_world[None, :])
        if not in_front[0] or not (0 <= uv[0] < w and 0 <= uv[1] < h):
            continue
        if dropout > 0.0 and rng.random() < dropout:
            continue
        out = uv + rng.normal(0.0, sigma_px, 2) if sigma_px > 0 else uv
        dets.append(Detection(kp.name, np.asarray(kp.xyz, dtype=float), np.asarray(out, dtype=float)))
    return dets
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group research pytest research/club_pose/tests/test_sim_keypoints.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add research/club_pose/sim/keypoints.py research/club_pose/tests/test_sim_keypoints.py
git commit -m "feat(club_pose.sim): normal-visibility keypoint detection model (0B-2)"
```

---

### Task 4: Pose solvers — mono PnP + stereo Kabsch (`posefit_kp.py`)

**Files:**
- Create: `research/club_pose/sim/posefit_kp.py`
- Test: `research/club_pose/tests/test_sim_posefit_kp.py`

**Interfaces:**
- Consumes: `Detection` (Task 3); `Camera` (`camera.py`); `ClubheadPose` (`..types`).
- Produces: `KPFit(pose, n_used, ok)`; `fit_pose_pnp(detections, camera, prior) -> KPFit`; `fit_pose_kp_stereo(det_L, det_R, cameras, prior) -> KPFit`.

- [ ] **Step 1: Write the failing test**

`research/club_pose/tests/test_sim_posefit_kp.py`:

```python
import numpy as np
from scipy.spatial.transform import Rotation

from club_pose.sim.camera import mono_rig, stereo_rig
from club_pose.sim.driverhead import structured_driver
from club_pose.sim.keypoints import detect
from club_pose.sim.posefit_kp import fit_pose_kp_stereo, fit_pose_pnp
from club_pose.types import ClubheadPose


def _pose(rv, t):
    return ClubheadPose(Rotation.from_rotvec(rv), np.array(t, float))


def _rot_err_deg(a, b):
    return float(np.degrees((a.rotation.inv() * b.rotation).magnitude()))


def test_mono_pnp_recovers_clean_pose():
    head, cam = structured_driver(), mono_rig()
    true = _pose([0.03, -0.05, 0.02], [4.0, -3.0, 6.0])
    dets = detect(head, true, cam, 0.0, np.random.default_rng(0))
    prior = _pose([0.0, 0.0, 0.0], [0.0, 0.0, 0.0])
    fit = fit_pose_pnp(dets, cam, prior)
    assert fit.ok
    assert _rot_err_deg(true, fit.pose) <= 0.5
    assert np.linalg.norm(fit.pose.translation - true.translation) <= 1.0


def test_stereo_kabsch_recovers_clean_pose():
    head, cams = structured_driver(), stereo_rig()
    true = _pose([0.03, -0.05, 0.02], [4.0, -3.0, 6.0])
    dL = detect(head, true, cams[0], 0.0, np.random.default_rng(0))
    dR = detect(head, true, cams[1], 0.0, np.random.default_rng(1))
    fit = fit_pose_kp_stereo(dL, dR, cams, _pose([0, 0, 0], [0, 0, 0]))
    assert fit.ok
    assert _rot_err_deg(true, fit.pose) <= 0.5
    assert np.linalg.norm(fit.pose.translation - true.translation) <= 1.0


def test_too_few_points_returns_not_ok():
    head, cam = structured_driver(), mono_rig()
    true = _pose([0, 0, 0], [0, 0, 0])
    dets = detect(head, true, cam, 0.0, np.random.default_rng(0))[:2]
    fit = fit_pose_pnp(dets, cam, _pose([0, 0, 0], [0, 0, 0]))
    assert not fit.ok
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run --group research pytest research/club_pose/tests/test_sim_posefit_kp.py -v`
Expected: FAIL — `ModuleNotFoundError: club_pose.sim.posefit_kp`.

- [ ] **Step 3: Implement `posefit_kp.py`**

```python
"""Keypoint pose solvers: mono solvePnP and stereo triangulate+Kabsch, with explicit frames."""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
from scipy.spatial.transform import Rotation

from ..types import ClubheadPose


@dataclass(frozen=True)
class KPFit:
    pose: ClubheadPose
    n_used: int
    ok: bool


def _K(intr):
    return np.array([[intr.fx, 0, intr.cx], [0, intr.fy, intr.cy], [0, 0, 1.0]])


def _to_object_camera(pose, camera):
    R_oc = camera.R_wc @ pose.rotation.as_matrix()
    t_oc = camera.R_wc @ (pose.translation - camera.center_world)
    return R_oc, t_oc


def _from_object_camera(R_oc, t_oc, camera):
    R_wb = camera.R_wc.T @ R_oc
    t_wb = camera.R_wc.T @ np.asarray(t_oc, dtype=float).reshape(3) + camera.center_world
    return ClubheadPose(Rotation.from_matrix(R_wb), t_wb)


def _degenerate(pts, tol=1e-3):
    c = np.asarray(pts, dtype=float) - np.asarray(pts, dtype=float).mean(0)
    s = np.linalg.svd(c, compute_uv=False)
    return s[-1] < tol * max(s[0], 1e-9)


def fit_pose_pnp(detections, camera, prior) -> KPFit:
    n = len(detections)
    if n < 4:
        return KPFit(prior, n, False)
    obj = np.ascontiguousarray([d.xyz_body for d in detections], dtype=np.float64)
    img = np.ascontiguousarray([d.uv for d in detections], dtype=np.float64)
    if _degenerate(obj):
        return KPFit(prior, n, False)
    R_oc0, t_oc0 = _to_object_camera(prior, camera)
    rvec0, _ = cv2.Rodrigues(R_oc0)
    ok, rvec, tvec = cv2.solvePnP(
        obj, img, _K(camera.intrinsics), None, rvec0.copy(),
        t_oc0.reshape(3, 1).copy(), True, cv2.SOLVEPNP_ITERATIVE,
    )
    if not ok:
        return KPFit(prior, n, False)
    R_oc, _ = cv2.Rodrigues(rvec)
    return KPFit(_from_object_camera(R_oc, tvec, camera), n, True)


def _proj_matrix(camera):
    return _K(camera.intrinsics) @ np.hstack(
        [camera.R_wc, (-camera.R_wc @ camera.center_world).reshape(3, 1)]
    )


def _kabsch(body, world):
    cb, cw = body.mean(0), world.mean(0)
    H = (body - cb).T @ (world - cw)
    U, _, Vt = np.linalg.svd(H)
    d = np.sign(np.linalg.det(Vt.T @ U.T))
    R = Vt.T @ np.diag([1.0, 1.0, d]) @ U.T
    return ClubheadPose(Rotation.from_matrix(R), cw - R @ cb)


def fit_pose_kp_stereo(det_L, det_R, cameras, prior) -> KPFit:
    left, right = cameras
    mL = {d.name: d for d in det_L}
    mR = {d.name: d for d in det_R}
    names = [n for n in mL if n in mR]
    if len(names) < 3:
        return KPFit(prior, len(names), False)
    ptsL = np.ascontiguousarray([mL[n].uv for n in names], dtype=np.float64).T
    ptsR = np.ascontiguousarray([mR[n].uv for n in names], dtype=np.float64).T
    Xh = cv2.triangulatePoints(_proj_matrix(left), _proj_matrix(right), ptsL, ptsR)
    world = (Xh[:3] / Xh[3]).T
    body = np.array([mL[n].xyz_body for n in names], dtype=float)
    if _degenerate(world):
        return KPFit(prior, len(names), False)
    return KPFit(_kabsch(body, world), len(names), True)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group research pytest research/club_pose/tests/test_sim_posefit_kp.py -v`
Expected: 3 passed. (If rotation error is large/inverted, the frame conversion is transposed — check `_to/_from_object_camera`.)

- [ ] **Step 5: Commit**

```bash
git add research/club_pose/sim/posefit_kp.py research/club_pose/tests/test_sim_posefit_kp.py
git commit -m "feat(club_pose.sim): mono PnP + stereo Kabsch keypoint solvers w/ explicit frames (0B-2)"
```

---

### Task 5: Find-the-requirement experiment + verdict (`experiment_kp.py`)

**Files:**
- Create: `research/club_pose/sim/experiment_kp.py`
- Test: `research/club_pose/tests/test_sim_experiment_kp.py`

**Interfaces:**
- Consumes: `structured_driver` (Task 2); `detect` (Task 3); `fit_pose_pnp`/`fit_pose_kp_stereo` (Task 4); `pose_for_delivered`, `raw_metrics` from `experiment.py`; `ball_for_impact` from `..groundtruth`; `mono_rig`/`stereo_rig`.
- Produces: `run_kp_experiment(n, sigma_px, baseline_mm, mode, dropout, seed) -> dict` (with `rows`, `n_attempted`, `n_ok`); `kp_verdict(grid) -> dict` (per-cell `ok_rate` + `impact_mm_median`, the gated `requirement` boundary).

- [ ] **Step 1: Write the failing test**

`research/club_pose/tests/test_sim_experiment_kp.py`:

```python
import numpy as np

from club_pose.sim.experiment_kp import kp_verdict, run_kp_experiment


def test_clean_stereo_has_high_ok_rate():
    res = run_kp_experiment(n=10, sigma_px=0.0, baseline_mm=150.0, mode="stereo", seed=0)
    assert res["n_attempted"] == 10
    assert res["n_ok"] >= 9


def test_impact_mm_increases_with_noise():
    lo = run_kp_experiment(n=12, sigma_px=0.5, baseline_mm=150.0, mode="stereo", seed=1)
    hi = run_kp_experiment(n=12, sigma_px=5.0, baseline_mm=150.0, mode="stereo", seed=1)

    def med(res):
        vals = [r["impact_mm"] for r in res["rows"] if r["ok"]]
        return float(np.median(vals))

    assert med(hi) > med(lo)


def test_verdict_gates_on_ok_rate():
    grid = [run_kp_experiment(n=10, sigma_px=s, baseline_mm=150.0, mode="stereo", seed=2)
            for s in (0.5, 1.0, 2.0)]
    v = kp_verdict(grid)
    for cell in v["cells"]:
        assert "ok_rate" in cell and "impact_mm_median" in cell
        if cell["ok_rate"] < 0.9:
            assert cell["meets_bar"] is False
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run --group research pytest research/club_pose/tests/test_sim_experiment_kp.py -v`
Expected: FAIL — `ModuleNotFoundError: club_pose.sim.experiment_kp`.

- [ ] **Step 3: Implement `experiment_kp.py`**

```python
"""Find-the-requirement sweep: keypoint pose -> impact location, gated on ok_rate."""
from __future__ import annotations

import numpy as np
from scipy.spatial.transform import Rotation

from ..groundtruth import ball_for_impact
from ..types import ClubheadPose
from .camera import mono_rig, stereo_rig
from .driverhead import structured_driver
from .experiment import pose_for_delivered, raw_metrics
from .keypoints import detect
from .posefit_kp import fit_pose_kp_stereo, fit_pose_pnp

_BAR_MM = 5.0


def run_kp_experiment(n=20, sigma_px=1.0, baseline_mm=150.0, mode="stereo", dropout=0.0, seed=0):
    rng = np.random.default_rng(seed)
    head = structured_driver()
    template = head.template
    mono = mono_rig()
    cams = stereo_rig(baseline_mm)
    rows = []
    n_ok = 0
    for _ in range(n):
        fa = float(rng.uniform(-5, 5))
        dl = float(template.static_loft_deg + rng.uniform(-3, 8))
        head_center = np.array([rng.uniform(-10, 10), rng.uniform(-10, 10), rng.uniform(0, 40)])
        true = pose_for_delivered(template, fa, dl, head_center)
        u0, v0 = float(rng.uniform(-15, 15)), float(rng.uniform(-12, 12))
        ball = ball_for_impact(true, template, u0, v0)
        t_true = raw_metrics(true, template, ball)
        prior = ClubheadPose(
            true.rotation * Rotation.from_rotvec(rng.normal(0, 0.05, 3)),
            true.translation + rng.normal(0, 8, 3),
        )
        if mode == "mono":
            fit = fit_pose_pnp(detect(head, true, mono, sigma_px, rng, dropout), mono, prior)
        else:
            dL = detect(head, true, cams[0], sigma_px, rng, dropout)
            dR = detect(head, true, cams[1], sigma_px, rng, dropout)
            fit = fit_pose_kp_stereo(dL, dR, cams, prior)
        row = {"ok": bool(fit.ok), "n_used": fit.n_used}
        if fit.ok:
            t_rec = raw_metrics(fit.pose, template, ball)
            row["impact_mm"] = float(np.hypot(t_rec[0] - t_true[0], t_rec[1] - t_true[1]))
            row["rot_err_deg"] = float(np.degrees((true.rotation.inv() * fit.pose.rotation).magnitude()))
            n_ok += 1
        rows.append(row)
    return {"rows": rows, "n_attempted": n, "n_ok": n_ok,
            "sigma_px": sigma_px, "mode": mode, "baseline_mm": baseline_mm}


def kp_verdict(grid, bar_mm=_BAR_MM):
    cells = []
    for res in grid:
        oks = [r["impact_mm"] for r in res["rows"] if r["ok"]]
        ok_rate = res["n_ok"] / max(1, res["n_attempted"])
        median = float(np.median(oks)) if oks else float("nan")
        cells.append({
            "sigma_px": res["sigma_px"], "mode": res["mode"], "baseline_mm": res["baseline_mm"],
            "ok_rate": ok_rate, "impact_mm_median": median,
            "meets_bar": bool(ok_rate >= 0.9 and oks and median <= bar_mm),
        })
    meeting = [c for c in cells if c["meets_bar"]]
    requirement = max(meeting, key=lambda c: c["sigma_px"]) if meeting else None
    return {"cells": cells, "requirement": requirement, "bar_mm": bar_mm,
            "note": "requirement = loosest sigma_px reaching the bar at ok_rate>=0.9; None => unreachable"}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group research pytest research/club_pose/tests/test_sim_experiment_kp.py -v`
Expected: 3 passed. (These use small `n`; the full sweep is Task 8.)

- [ ] **Step 5: Commit**

```bash
git add research/club_pose/sim/experiment_kp.py research/club_pose/tests/test_sim_experiment_kp.py
git commit -m "feat(club_pose.sim): find-the-requirement keypoint experiment + ok_rate-gated verdict (0B-2)"
```

---

### Task 6: Real-OBJ realism check

**Files:**
- Create: `research/club_pose/sim/assets/driver.obj` (sourced generic driver, triangulated)
- Create: `research/club_pose/sim/assets/driver_keypoints.json` (hand-labeled body-frame keypoints + normals, same names as `_KP`)
- Modify: `research/club_pose/sim/driverhead.py` (add `structured_driver_from_obj(obj_path, kp_path) -> StructuredHead`)
- Test: `research/club_pose/tests/test_sim_driverhead.py` (add a guarded realism test)

**Interfaces:**
- Consumes: `load_obj` from `headmesh.py`; the same `Keypoint`/`StructuredHead` types.
- Produces: `structured_driver_from_obj(obj_path, kp_path)` returning a `StructuredHead` whose mesh is the real OBJ (recentred so its face center sits at the template's (50,0,0)) and whose keypoints come from the JSON.

- [ ] **Step 1: Add the loader + a guarded test**

Add to `driverhead.py`:

```python
import json
import os


def structured_driver_from_obj(obj_path, kp_path) -> StructuredHead:
    from .headmesh import load_obj

    mesh = load_obj(obj_path)
    with open(kp_path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    kps = {}
    for name, rec in raw.items():
        nv = np.asarray(rec["normal"], dtype=float)
        kps[name] = Keypoint(name, np.asarray(rec["xyz"], dtype=float), nv / np.linalg.norm(nv))
    return StructuredHead(mesh=mesh, keypoints=kps, template=default_template("driver"))
```

Add to `test_sim_driverhead.py`:

```python
import os

import pytest


_ASSETS = os.path.join(os.path.dirname(__file__), "..", "sim", "assets")


@pytest.mark.skipif(not os.path.exists(os.path.join(_ASSETS, "driver.obj")),
                    reason="real driver OBJ asset not present")
def test_real_obj_loads_with_keypoints():
    from club_pose.sim.driverhead import structured_driver_from_obj

    head = structured_driver_from_obj(
        os.path.join(_ASSETS, "driver.obj"), os.path.join(_ASSETS, "driver_keypoints.json")
    )
    assert len(head.mesh.faces) > 50
    assert "face_center" in head.keypoints and "crown_apex" in head.keypoints
```

- [ ] **Step 2: Source + place the asset**

Obtain a generic driver OBJ (triangulated, body-frame: +X face, +Y toe, +Z up; ~115×110×60 mm), recentre it so the face-center vertex is at (50,0,0), and save to `assets/driver.obj`. Hand-label the 12 keypoints (xyz + outward normal) into `assets/driver_keypoints.json` using the same names as `_KP`. **If no asset is available at execution time, leave the test skipped and note it in the Task 8 verdict** (the procedural head remains the primary testbed — the real-OBJ check is corroboration).

- [ ] **Step 3: Run the tests**

Run: `uv run --group research pytest research/club_pose/tests/test_sim_driverhead.py -v`
Expected: prior 3 pass; the real-OBJ test passes (asset present) or is skipped (asset absent).

- [ ] **Step 4: Commit**

```bash
git add research/club_pose/sim/driverhead.py research/club_pose/tests/test_sim_driverhead.py research/club_pose/sim/assets/
git commit -m "feat(club_pose.sim): real-OBJ realism-check loader for the keypoint experiment (0B-2)"
```

---

### Task 7: Full suite green + run the sweep, capture the verdict artifact, write the decision

**Files:**
- Create: `research/club_pose/sim/RESULTS_0B2.md` (the verdict artifact + decision)

**Interfaces:**
- Consumes: everything above.
- Produces: the written cue-vs-vantage decision feeding the v2 guide.

- [ ] **Step 1: Run the whole sim test suite**

Run: `uv run --group research pytest research/club_pose/tests/ -v`
Expected: all green (0B-1 baseline + the 4 new test modules).

- [ ] **Step 2: Run the requirement sweep (artifact, not a test)**

Run this once and capture stdout (it is an artifact; a few minutes is fine):

```bash
PYTHONPATH=research uv run --group research python - <<'PY'
import json
from club_pose.sim.experiment_kp import kp_verdict, run_kp_experiment
grid = []
for mode in ("mono", "stereo"):
    for s in (0.5, 1.0, 2.0, 3.0, 5.0):
        grid.append(run_kp_experiment(n=30, sigma_px=s, baseline_mm=150.0, mode=mode, seed=0))
v = kp_verdict(grid)
print(json.dumps({"requirement": v["requirement"], "cells": v["cells"]}, indent=2))
PY
```

- [ ] **Step 3: Write `RESULTS_0B2.md`**

Record: the `impact_mm`-vs-σ table per mode, each cell's `ok_rate`, the gated requirement boundary (or "unreachable"), the silhouette baseline on the structured mesh, and the **decision**:
- If a realistic σ (≤ ~1–2 px) reaches ≤3–5 mm at `ok_rate ≥ 0.9` → **CUE problem; build the keypoint path** (next: real detector feasibility).
- If even σ=0.5 px / stereo cannot reach the bar → **VANTAGE problem; re-scope** the behind-ball camera to spin (marked ball) + coarse impact zones (path B), and record that face/loft + precise impact need a marker and/or side/overhead vantage.

- [ ] **Step 4: Commit**

```bash
git add research/club_pose/sim/RESULTS_0B2.md
git commit -m "docs(club_pose.sim): Stage 0B-2 verdict — keypoint impact-location requirement + cue-vs-vantage decision"
```

- [ ] **Step 5: Fold the decision into the v2 guide (§1D follow-up)**

Update `docs/Personal Research/markerless-club-data-guide-v2-research-corrected.md` with the 0B-2 outcome (one short subsection: the requirement found, and which fork it selected). Commit:

```bash
git add "docs/Personal Research/markerless-club-data-guide-v2-research-corrected.md"
git commit -m "docs(research): fold Stage 0B-2 keypoint verdict into the v2 guide"
```

---

## Self-Review

- **Spec coverage:** Task 1 (baseline green) ← §2.4/§6; Task 2 (structured driver + keypoints, template reuse, visibility) ← §5.1; Task 3 (detection model) ← §5.2; Task 4 (PnP + Kabsch + explicit frames + degeneracy) ← §5.3; Task 5 (sweep + ok_rate gate) ← §5.4/§7; Task 6 (real-OBJ check) ← §2.6/§5.5; Task 7 (verdict + decision) ← §7. All spec sections map to a task.
- **Placeholder scan:** every code step contains the full implementation; the only deferred concrete is the sourced `driver.obj` binary (Task 6), explicitly handled by a `skipif` so the suite stays green if it is absent.
- **Type consistency:** `Keypoint(name,xyz,normal)`, `StructuredHead(mesh,keypoints,template)`, `Detection(name,xyz_body,uv)`, `KPFit(pose,n_used,ok)` are defined once and used with the same fields throughout. `fit_pose_pnp`/`fit_pose_kp_stereo` signatures match their call sites in `experiment_kp.py`. Frame conversion helpers are the single source of truth for body↔object-camera.
- **Honesty gates present:** ok_rate gate (Task 5), degenerate→`ok=False` (Task 4), σ-sweep not perfect keypoints (Task 5), baseline reported not asserted (Task 1/7), real-OBJ corroboration (Task 6).

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-29-club-pose-stage0b2-keypoint-pnp-impact-location.md`. Two execution options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — execute tasks in this session with checkpoints for review.

Which approach?
