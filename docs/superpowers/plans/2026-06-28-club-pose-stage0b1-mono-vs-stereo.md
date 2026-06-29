# Stage 0B-1 — Mono-vs-Stereo Silhouette Experiment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a pure-Python silhouette analysis-by-synthesis experiment that recovers clubhead pose from one camera vs two, propagates the recovered-pose error through the Stage-0A metric budget, and outputs the single-vs-stereo verdict for face angle / dynamic loft / impact location.

**Architecture:** A new `research/club_pose/sim/` subpackage reusing the 0A core (`types`, `template`, `metrics`, `groundtruth`). A pinhole look-at camera projects a procedural clubhead mesh to a binary silhouette; modeled degradations are applied; pose is recovered by maximizing silhouette overlap (mono and stereo); error is run through 0A's ungated metric path.

**Tech Stack:** Python 3.10+, numpy, scipy (both present), **opencv-python** (new, in a dependency group), pytest.

**Spec:** `docs/superpowers/specs/2026-06-28-club-pose-stage0b1-mono-vs-stereo-experiment-design.md` (rev. 3).

## Global Constraints

- **Reuses Stage 0A** (`research/club_pose/`): `ClubheadPose`, `ClubTemplate`, `default_template`, `face_angle`, `dynamic_loft`, `point_to_face_uv`, `ball_for_impact`. Do not modify 0A files.
- **World frame (0A):** +X downrange, +Y player-left, +Z up; right-handed; origin = ball at address.
- **Camera = look-at** (NOT a level axis): rows `right=normalize(forward×up)`, `down=forward×right`, `forward=normalize(target−center)`; project `p_cam=R_wc@(p−C)`, `u=fx·x/z+cx`, `v=fy·y/z+cy`, valid iff `z>0`.
- **Default intrinsics:** IMX296 1456×1088, fx=fy=4638, cx=728, cy=544.
- **Metric error uses the UNGATED, IMPACT-AWARE path** (`face_angle`/`dynamic_loft` with `normal_body=proj.normal_body`; `point_to_face_uv` for impact) — never `compute_metrics` (its contact gate returns None for perturbed poses).
- **Translation error** is reported as `camera_range_error_mm` (along the optical axis) + `inplane_error_mm` — not "+X".
- **Mesh is frozen before the verdict**; realistic-mesh mono recovery is a *result*, not a gate. Machinery is validated with stereo / a distinctive mesh.
- **Dependency:** `opencv-python` goes under the **existing** `[dependency-groups]` table as `research`. Test gate: `uv run --group research pytest research/club_pose/tests/ -v`. (If `uv` isn't on PATH, use the winget `uv.exe` full path or a fresh terminal.)
- **Commits:** conventional messages; **do NOT add a Claude co-author footer** (Codex is the implementer).

---

### Task 0: Dependency group + `sim` package scaffold

**Files:**
- Modify: `pyproject.toml` (under the existing `[dependency-groups]` table)
- Create: `research/club_pose/sim/__init__.py`
- Test: `research/club_pose/tests/test_sim_smoke.py`

**Interfaces:**
- Produces: an importable `club_pose.sim` package; `opencv-python` available via `--group research`.

- [ ] **Step 1: Add the dependency group**

In `pyproject.toml`, under the **existing** `[dependency-groups]` table (which already has `dev = [...]`), add a sibling entry (do NOT add a second `[dependency-groups]` header):
```toml
research = [
    "opencv-python>=4.8",
]
```

- [ ] **Step 2: Install the group**

Run: `uv sync --group research`
Expected: resolves and installs `opencv-python`.

- [ ] **Step 3: Create the package + smoke test**

`research/club_pose/sim/__init__.py`:
```python
"""Stage 0B-1: silhouette analysis-by-synthesis mono-vs-stereo experiment."""
```

`research/club_pose/tests/test_sim_smoke.py`:
```python
def test_sim_imports_and_cv2_available():
    import club_pose.sim  # noqa: F401
    import cv2

    assert cv2.__version__
```

- [ ] **Step 4: Run it**

Run: `uv run --group research pytest research/club_pose/tests/test_sim_smoke.py -v`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml research/club_pose/sim/__init__.py research/club_pose/tests/test_sim_smoke.py
git commit -m "feat(club_pose.sim): scaffold sim subpackage and opencv research dep group"
```

---

### Task 1: `camera.py` — pinhole look-at camera + rigs

**Files:**
- Create: `research/club_pose/sim/camera.py`
- Test: `research/club_pose/tests/test_sim_camera.py`

**Interfaces:**
- Produces:
  - `CameraIntrinsics(fx, fy, cx, cy, width, height)`; module const `IMX296`.
  - `Camera(intrinsics, center_world, R_wc)`; `Camera.look_at(intrinsics, center, target, up=(0,0,1)) -> Camera`; `project(points_world) -> (pixels Nx2, in_front N-bool)`.
  - `mono_rig() -> Camera`; `stereo_rig(baseline_mm=150.0) -> (Camera, Camera)`; const `IMPACT_TARGET`.

- [ ] **Step 1: Write the failing tests**

`research/club_pose/tests/test_sim_camera.py`:
```python
import numpy as np
import pytest

from club_pose.sim.camera import IMX296, mono_rig, stereo_rig


def test_target_projects_to_principal_point():
    cam = mono_rig()
    px, in_front = cam.project([[0.0, 0.0, 0.0]])  # the look-at target
    assert in_front[0]
    assert px[0, 0] == pytest.approx(IMX296.cx, abs=1.0)
    assert px[0, 1] == pytest.approx(IMX296.cy, abs=3.0)


def test_player_left_moves_image_left():
    cam = mono_rig()
    px, _ = cam.project([[0.0, 50.0, 0.0]])  # +Y = player left
    assert px[0, 0] < IMX296.cx  # smaller u = left in image


def test_higher_moves_image_up():
    cam = mono_rig()
    px, _ = cam.project([[0.0, 0.0, 50.0]])  # +Z = up
    assert px[0, 1] < IMX296.cy  # smaller v = up in image


def test_ball_and_clubhead_box_in_frame():
    # in-frame requirement for the default rigs
    box = np.array([[x, y, z] for x in (-60, 60) for y in (-60, 60) for z in (-30, 30)], float)
    pts = np.vstack([[0, 0, 0], box])
    for cam in (mono_rig(), *stereo_rig()):
        px, in_front = cam.project(pts)
        assert in_front.all()
        assert (px[:, 0] >= 0).all() and (px[:, 0] < IMX296.width).all()
        assert (px[:, 1] >= 0).all() and (px[:, 1] < IMX296.height).all()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --group research pytest research/club_pose/tests/test_sim_camera.py -v`
Expected: FAIL (ModuleNotFoundError: club_pose.sim.camera).

- [ ] **Step 3: Implement**

`research/club_pose/sim/camera.py`:
```python
"""Pinhole look-at camera and the mono/stereo rigs (0A world frame)."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class CameraIntrinsics:
    fx: float
    fy: float
    cx: float
    cy: float
    width: int
    height: int


IMX296 = CameraIntrinsics(fx=4638.0, fy=4638.0, cx=728.0, cy=544.0, width=1456, height=1088)
IMPACT_TARGET = np.array([0.0, 0.0, 0.0])  # impact-zone center the cameras aim at


def _look_at_rows(center, target, up) -> np.ndarray:
    center = np.asarray(center, dtype=float)
    target = np.asarray(target, dtype=float)
    up = np.asarray(up, dtype=float)
    forward = target - center
    forward = forward / np.linalg.norm(forward)
    right = np.cross(forward, up)
    right = right / np.linalg.norm(right)
    down = np.cross(forward, right)
    return np.array([right, down, forward])


@dataclass(frozen=True)
class Camera:
    intrinsics: CameraIntrinsics
    center_world: np.ndarray
    R_wc: np.ndarray

    @classmethod
    def look_at(cls, intrinsics, center, target, up=(0.0, 0.0, 1.0)) -> "Camera":
        return cls(intrinsics, np.asarray(center, dtype=float), _look_at_rows(center, target, up))

    def project(self, points_world):
        pts = np.asarray(points_world, dtype=float).reshape(-1, 3)
        cam = (self.R_wc @ (pts - self.center_world).T).T  # (N, 3)
        z = cam[:, 2]
        in_front = z > 1e-9
        zc = np.where(in_front, z, 1.0)
        u = self.intrinsics.fx * cam[:, 0] / zc + self.intrinsics.cx
        v = self.intrinsics.fy * cam[:, 1] / zc + self.intrinsics.cy
        return np.column_stack([u, v]), in_front


def mono_rig() -> Camera:
    return Camera.look_at(IMX296, center=(-1200.0, 0.0, 300.0), target=IMPACT_TARGET)


def stereo_rig(baseline_mm: float = 150.0):
    b = baseline_mm / 2.0
    left = Camera.look_at(IMX296, center=(-1200.0, b, 300.0), target=IMPACT_TARGET)
    right = Camera.look_at(IMX296, center=(-1200.0, -b, 300.0), target=IMPACT_TARGET)
    return left, right
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run --group research pytest research/club_pose/tests/test_sim_camera.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add research/club_pose/sim/camera.py research/club_pose/tests/test_sim_camera.py
git commit -m "feat(club_pose.sim): add pinhole look-at camera and mono/stereo rigs"
```

---

### Task 2: `headmesh.py` — procedural clubhead mesh + distinctive test mesh

**Files:**
- Create: `research/club_pose/sim/headmesh.py`
- Test: `research/club_pose/tests/test_sim_headmesh.py`

**Interfaces:**
- Consumes: `club_pose.types.ClubheadPose`.
- Produces:
  - `HeadMesh(vertices Nx3, faces Mx3, category)`; `transformed(pose) -> world verts (N,3)`.
  - `procedural(category) -> HeadMesh` (driver/iron, anchored in body frame, hosel at the heel = −Y).
  - `distinctive_test_mesh() -> HeadMesh` (machinery-validation mesh, asymmetric on all axes).
  - `load_obj(path) -> HeadMesh`.

- [ ] **Step 1: Write the failing tests**

`research/club_pose/tests/test_sim_headmesh.py`:
```python
import numpy as np
from scipy.spatial.transform import Rotation

from club_pose.sim.camera import mono_rig
from club_pose.sim.headmesh import distinctive_test_mesh, procedural
from club_pose.sim.silhouette import iou, render_silhouette
from club_pose.types import ClubheadPose


def test_procedural_meshes_have_faces():
    for cat in ("driver", "iron"):
        m = procedural(cat)
        assert m.vertices.shape[1] == 3 and m.faces.shape[1] == 3
        assert np.isfinite(m.vertices).all()


def test_driver_is_bulkier_than_iron_in_depth():
    drv = procedural("driver").vertices
    iron = procedural("iron").vertices
    assert drv[:, 0].ptp() > iron[:, 0].ptp()  # driver deeper front-to-back


def test_distinctive_mesh_pose_is_unambiguous_under_180_flips():
    # a distinctive mesh's silhouette must change under 180-deg flips about each axis
    m = distinctive_test_mesh()
    cam = mono_rig()
    base = render_silhouette(m, ClubheadPose(Rotation.identity(), np.zeros(3)), cam)
    for axis in (np.array([1.0, 0, 0]), np.array([0, 1.0, 0]), np.array([0, 0, 1.0])):
        flipped = ClubheadPose(Rotation.from_rotvec(np.pi * axis), np.zeros(3))
        assert iou(base, render_silhouette(m, flipped, cam)) < 0.98
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --group research pytest research/club_pose/tests/test_sim_headmesh.py -v`
Expected: FAIL (ModuleNotFoundError).

- [ ] **Step 3: Implement**

`research/club_pose/sim/headmesh.py`:
```python
"""Procedural generic clubhead meshes (body-frame), for silhouette experiments."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.spatial import ConvexHull

from ..types import ClubheadPose


@dataclass(frozen=True)
class HeadMesh:
    vertices: np.ndarray  # (N, 3) body coords
    faces: np.ndarray     # (M, 3) int indices
    category: str

    def transformed(self, pose: ClubheadPose) -> np.ndarray:
        return pose.body_to_world(self.vertices)


def _fib_sphere(n: int) -> np.ndarray:
    i = np.arange(n) + 0.5
    phi = np.arccos(1.0 - 2.0 * i / n)
    theta = np.pi * (1.0 + 5.0 ** 0.5) * i
    return np.column_stack(
        [np.cos(theta) * np.sin(phi), np.sin(theta) * np.sin(phi), np.cos(phi)]
    )


def _hull_mesh(points: np.ndarray, category: str) -> HeadMesh:
    hull = ConvexHull(points)
    return HeadMesh(vertices=points, faces=hull.simplices.astype(np.int64), category=category)


def procedural(category: str) -> HeadMesh:
    """Body frame: +X≈face/front, +Y=toe, -Y=heel, +Z=up. Hosel at the heel (-Y), up (+Z)."""
    sphere = _fib_sphere(120)
    if category == "driver":
        body = sphere * np.array([45.0, 58.0, 28.0]) + np.array([-10.0, 0.0, 0.0])
        hosel = np.array([[0, -58, 28], [5, -60, 55], [-5, -60, 55], [0, -62, 80]], dtype=float)
    elif category == "iron":
        body = sphere * np.array([10.0, 40.0, 27.0]) + np.array([-3.0, 0.0, 0.0])
        hosel = np.array([[0, -40, 27], [4, -42, 50], [-4, -42, 50], [0, -44, 72]], dtype=float)
    else:
        raise ValueError(f"unknown category {category!r}")
    return _hull_mesh(np.vstack([body, hosel]), category)


def distinctive_test_mesh() -> HeadMesh:
    """A deliberately asymmetric mesh (3 different spikes) — machinery validation only."""
    corners = np.array(
        [[x, y, z] for x in (-20, 20) for y in (-30, 30) for z in (-15, 15)], dtype=float
    )
    spikes = np.array([[60, 0, 0], [0, 45, 0], [0, 0, 40]], dtype=float)
    return _hull_mesh(np.vstack([corners, spikes]), "test")


def load_obj(path: str) -> HeadMesh:
    verts, faces = [], []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            parts = line.split()
            if not parts:
                continue
            if parts[0] == "v":
                verts.append([float(c) for c in parts[1:4]])
            elif parts[0] == "f":
                idx = [int(p.split("/")[0]) - 1 for p in parts[1:4]]
                faces.append(idx)
    return HeadMesh(np.asarray(verts, dtype=float), np.asarray(faces, dtype=np.int64), "obj")
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run --group research pytest research/club_pose/tests/test_sim_headmesh.py -v`
Expected: PASS (3 passed). (Depends on `silhouette.py` from Task 3 — if running tasks strictly in order, implement Task 3 first or run this test after Task 3. The two are mutually referencing in tests only; ordering note: do Task 3 before re-running this test.)

- [ ] **Step 5: Commit**

```bash
git add research/club_pose/sim/headmesh.py research/club_pose/tests/test_sim_headmesh.py
git commit -m "feat(club_pose.sim): add procedural clubhead and distinctive test meshes"
```

---

### Task 3: `silhouette.py` — rasterizer + IoU/chamfer

**Files:**
- Create: `research/club_pose/sim/silhouette.py`
- Test: `research/club_pose/tests/test_sim_silhouette.py`

**Interfaces:**
- Consumes: `HeadMesh`, `Camera`, `ClubheadPose`.
- Produces:
  - `render_silhouette(mesh, pose, camera) -> mask (H,W bool)`.
  - `iou(a, b) -> float`; `chamfer(a, b) -> float` (capped at image diagonal; symmetric mean boundary distance).

- [ ] **Step 1: Write the failing tests**

`research/club_pose/tests/test_sim_silhouette.py`:
```python
import numpy as np
from scipy.spatial.transform import Rotation

from club_pose.sim.camera import mono_rig
from club_pose.sim.headmesh import procedural
from club_pose.sim.silhouette import chamfer, iou, render_silhouette
from club_pose.types import ClubheadPose


def _identity():
    return ClubheadPose(Rotation.identity(), np.zeros(3))


def test_render_is_nonempty_and_in_frame():
    mask = render_silhouette(procedural("driver"), _identity(), mono_rig())
    assert mask.dtype == bool
    assert 0 < mask.sum() < mask.size


def test_iou_self_is_one_disjoint_is_zero():
    mask = render_silhouette(procedural("driver"), _identity(), mono_rig())
    assert iou(mask, mask) == 1.0
    empty = np.zeros_like(mask)
    assert iou(mask, empty) == 0.0


def test_chamfer_self_is_zero():
    mask = render_silhouette(procedural("driver"), _identity(), mono_rig())
    assert chamfer(mask, mask) == 0.0
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --group research pytest research/club_pose/tests/test_sim_silhouette.py -v`
Expected: FAIL (ModuleNotFoundError).

- [ ] **Step 3: Implement**

`research/club_pose/sim/silhouette.py`:
```python
"""Silhouette rasterizer and mask similarity (IoU, chamfer)."""
from __future__ import annotations

import cv2
import numpy as np


def render_silhouette(mesh, pose, camera) -> np.ndarray:
    world = mesh.transformed(pose)
    pix, in_front = camera.project(world)
    h, w = camera.intrinsics.height, camera.intrinsics.width
    mask = np.zeros((h, w), dtype=np.uint8)
    pix_i = np.round(pix).astype(np.int32)
    for tri in mesh.faces:
        if not (in_front[tri[0]] and in_front[tri[1]] and in_front[tri[2]]):
            continue
        cv2.fillConvexPoly(mask, pix_i[tri], 1)
    return mask.astype(bool)


def iou(a: np.ndarray, b: np.ndarray) -> float:
    a = a.astype(bool)
    b = b.astype(bool)
    union = np.logical_or(a, b).sum()
    if union == 0:
        return 1.0  # both empty == identical
    return float(np.logical_and(a, b).sum() / union)


def _boundary(mask: np.ndarray) -> np.ndarray:
    m = mask.astype(np.uint8)
    eroded = cv2.erode(m, np.ones((3, 3), np.uint8))
    return (m - eroded).astype(bool)


def chamfer(a: np.ndarray, b: np.ndarray) -> float:
    diag = float(np.hypot(*a.shape))
    ba, bb = _boundary(a), _boundary(b)
    if ba.sum() == 0 and bb.sum() == 0:
        return 0.0
    if ba.sum() == 0 or bb.sum() == 0:
        return diag
    dt_to_b = cv2.distanceTransform((~bb).astype(np.uint8), cv2.DIST_L2, 3)
    dt_to_a = cv2.distanceTransform((~ba).astype(np.uint8), cv2.DIST_L2, 3)
    d = 0.5 * (float(dt_to_b[ba].mean()) + float(dt_to_a[bb].mean()))
    return min(d, diag)
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run --group research pytest research/club_pose/tests/test_sim_silhouette.py research/club_pose/tests/test_sim_headmesh.py -v`
Expected: PASS (3 + 3 = 6 passed; Task-2 tests now resolve too).

- [ ] **Step 5: Commit**

```bash
git add research/club_pose/sim/silhouette.py research/club_pose/tests/test_sim_silhouette.py
git commit -m "feat(club_pose.sim): add silhouette rasterizer and IoU/chamfer"
```

---

### Task 4: `degrade.py` — modeled mask degradations

**Files:**
- Create: `research/club_pose/sim/degrade.py`
- Test: `research/club_pose/tests/test_sim_degrade.py`

**Interfaces:**
- Produces:
  - `DegradationParams(blur_px, blur_dir_deg, boundary_sigma_px, truncate_frac, occlude_ball, occlude_shaft)`; dict `PRESETS` with keys `none/light/realistic/severe`.
  - `degrade(mask, params, rng) -> mask (bool)` — deterministic given a numpy `rng`.

- [ ] **Step 1: Write the failing tests**

`research/club_pose/tests/test_sim_degrade.py`:
```python
import numpy as np
from scipy.spatial.transform import Rotation

from club_pose.sim.camera import mono_rig
from club_pose.sim.degrade import PRESETS, degrade
from club_pose.sim.headmesh import procedural
from club_pose.sim.silhouette import iou, render_silhouette
from club_pose.types import ClubheadPose


def _mask():
    return render_silhouette(procedural("driver"), ClubheadPose(Rotation.identity(), np.zeros(3)), mono_rig())


def test_none_preset_is_identity():
    m = _mask()
    out = degrade(m, PRESETS["none"], np.random.default_rng(0))
    assert iou(m, out) == 1.0


def test_deterministic_given_seed():
    m = _mask()
    a = degrade(m, PRESETS["realistic"], np.random.default_rng(7))
    b = degrade(m, PRESETS["realistic"], np.random.default_rng(7))
    assert np.array_equal(a, b)


def test_severity_is_monotonic():
    m = _mask()
    light = iou(m, degrade(m, PRESETS["light"], np.random.default_rng(1)))
    severe = iou(m, degrade(m, PRESETS["severe"], np.random.default_rng(1)))
    assert severe < light <= 1.0
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --group research pytest research/club_pose/tests/test_sim_degrade.py -v`
Expected: FAIL (ModuleNotFoundError).

- [ ] **Step 3: Implement**

`research/club_pose/sim/degrade.py`:
```python
"""Modeled silhouette degradations (motion blur, segmentation noise, truncation, occlusion)."""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class DegradationParams:
    blur_px: float = 0.0
    blur_dir_deg: float = 0.0
    boundary_sigma_px: float = 0.0
    truncate_frac: float = 0.0
    occlude_ball: bool = False
    occlude_shaft: bool = False


PRESETS = {
    "none": DegradationParams(),
    "light": DegradationParams(blur_px=3.0, boundary_sigma_px=1.5),
    "realistic": DegradationParams(blur_px=8.0, boundary_sigma_px=3.0, occlude_shaft=True),
    "severe": DegradationParams(
        blur_px=20.0, boundary_sigma_px=6.0, truncate_frac=0.1, occlude_ball=True, occlude_shaft=True
    ),
}


def _motion_kernel(length_px: float, dir_deg: float) -> np.ndarray:
    n = max(1, int(round(length_px)))
    k = np.zeros((n, n), dtype=np.float32)
    cv2.line(k, (0, n // 2), (n - 1, n // 2), 1.0, 1)
    rot = cv2.getRotationMatrix2D((n / 2 - 0.5, n / 2 - 0.5), dir_deg, 1.0)
    k = cv2.warpAffine(k, rot, (n, n))
    s = k.sum()
    return k / s if s > 0 else k


def degrade(mask: np.ndarray, params: DegradationParams, rng: np.random.Generator) -> np.ndarray:
    m = mask.astype(np.uint8)
    h, w = m.shape
    if params.blur_px > 0:
        k = _motion_kernel(params.blur_px, params.blur_dir_deg)
        m = (cv2.filter2D(m.astype(np.float32), -1, k) > 0.3).astype(np.uint8)
    if params.boundary_sigma_px > 0:
        r = max(1, int(abs(rng.normal(0.0, params.boundary_sigma_px))))
        kern = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
        m = cv2.dilate(m, kern) if rng.random() < 0.5 else cv2.erode(m, kern)
    if params.truncate_frac > 0:
        cut = int(params.truncate_frac * w)
        edge = int(rng.integers(0, 4))
        if edge == 0:
            m[:, :cut] = 0
        elif edge == 1:
            m[:, w - cut :] = 0
        elif edge == 2:
            m[:cut, :] = 0
        else:
            m[h - cut :, :] = 0
    ys, xs = np.nonzero(m)
    if len(xs) > 0:
        if params.occlude_ball:
            cy, cx = int(ys.mean()), int(xs.min())
            cv2.circle(m, (cx, cy), max(5, (xs.ptp()) // 8), 0, -1)
        if params.occlude_shaft:
            x0 = int(xs.min() + 0.5 * xs.ptp())
            cv2.line(m, (x0, int(ys.min())), (x0, 0), 0, max(2, w // 200))
    return m.astype(bool)
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run --group research pytest research/club_pose/tests/test_sim_degrade.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add research/club_pose/sim/degrade.py research/club_pose/tests/test_sim_degrade.py
git commit -m "feat(club_pose.sim): add modeled silhouette degradations"
```

---

### Task 5: `posefit.py` — analysis-by-synthesis pose recovery (mono + stereo)

**Files:**
- Create: `research/club_pose/sim/posefit.py`
- Test: `research/club_pose/tests/test_sim_posefit.py`

**Interfaces:**
- Consumes: `render_silhouette`, `iou`, `chamfer`, `HeadMesh`, `Camera`, `ClubheadPose`.
- Produces:
  - `FitResult(pose, iou, success, n_evals)`.
  - `fit_pose_mono(observed_mask, mesh, camera, prior_pose) -> FitResult`.
  - `fit_pose_stereo(observed_masks, mesh, cameras, prior_pose) -> FitResult` (`observed_masks`/`cameras` are length-2 sequences).

- [ ] **Step 1: Write the failing tests**

`research/club_pose/tests/test_sim_posefit.py`:
```python
import numpy as np
from scipy.spatial.transform import Rotation

from club_pose.sim.camera import mono_rig, stereo_rig
from club_pose.sim.headmesh import distinctive_test_mesh
from club_pose.sim.posefit import fit_pose_mono, fit_pose_stereo
from club_pose.sim.silhouette import render_silhouette
from club_pose.types import ClubheadPose


def _pose(rotvec, t):
    return ClubheadPose(Rotation.from_rotvec(rotvec), np.array(t, float))


def _rot_err_deg(a, b):
    return np.degrees((a.rotation.inv() * b.rotation).magnitude())


def test_machinery_clean_recovery_stereo():
    # distinctive mesh + stereo: clean recovery must be near-exact (validates rasterizer+optimizer)
    mesh = distinctive_test_mesh()
    cams = stereo_rig()
    true = _pose([0.05, -0.1, 0.08], [3.0, -2.0, 5.0])
    obs = [render_silhouette(mesh, true, c) for c in cams]
    prior = _pose([0.0, 0.0, 0.0], [0.0, 0.0, 0.0])
    res = fit_pose_stereo(obs, mesh, cams, prior)
    assert res.success
    assert _rot_err_deg(true, res.pose) <= 0.5
    assert np.linalg.norm(res.pose.translation - true.translation) <= 1.0


def test_stereo_beats_mono_on_depth_ambiguity():
    # a depth (range) offset: stereo should recover translation better than mono
    mesh = distinctive_test_mesh()
    mono = mono_rig()
    cams = stereo_rig()
    true = _pose([0.0, 0.0, 0.0], [0.0, 0.0, 0.0])
    prior = _pose([0.02, 0.02, 0.02], [10.0, 10.0, 10.0])  # offset incl. along range
    obs_mono = render_silhouette(mesh, true, mono)
    obs_stereo = [render_silhouette(mesh, true, c) for c in cams]
    rm = fit_pose_mono(obs_mono, mesh, mono, prior)
    rs = fit_pose_stereo(obs_stereo, mesh, cams, prior)
    err_mono = np.linalg.norm(rm.pose.translation - true.translation)
    err_stereo = np.linalg.norm(rs.pose.translation - true.translation)
    assert err_stereo <= err_mono + 1e-6
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --group research pytest research/club_pose/tests/test_sim_posefit.py -v`
Expected: FAIL (ModuleNotFoundError).

- [ ] **Step 3: Implement**

`research/club_pose/sim/posefit.py`:
```python
"""Analysis-by-synthesis pose recovery from silhouette(s)."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.optimize import minimize
from scipy.spatial.transform import Rotation

from ..types import ClubheadPose
from .silhouette import chamfer, iou, render_silhouette

_CHAMFER_WEIGHT = 0.5


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


def _cost(x, observed_masks, mesh, cameras) -> float:
    pose = _pose_from_x(x)
    total = 0.0
    for observed, cam in zip(observed_masks, cameras):
        rendered = render_silhouette(mesh, pose, cam)
        diag = float(np.hypot(*observed.shape))
        total += (1.0 - iou(rendered, observed)) + _CHAMFER_WEIGHT * chamfer(rendered, observed) / diag
    return total


def _fit(observed_masks, mesh, cameras, prior_pose) -> FitResult:
    x0 = _x_from_pose(prior_pose)
    starts = [x0]
    rng = np.random.default_rng(0)
    for _ in range(3):  # small multi-start to dodge local minima
        starts.append(x0 + rng.normal(0, [0.05, 0.05, 0.05, 5.0, 5.0, 5.0]))
    best, best_cost, n_evals = None, np.inf, 0
    for s in starts:
        res = minimize(_cost, s, args=(observed_masks, mesh, cameras), method="Powell",
                       options={"xtol": 1e-3, "ftol": 1e-4, "maxiter": 2000})
        n_evals += int(res.nfev)
        if res.fun < best_cost:
            best, best_cost = res.x, float(res.fun)
    pose = _pose_from_x(best)
    final_iou = float(np.mean([
        iou(render_silhouette(mesh, pose, c), o) for o, c in zip(observed_masks, cameras)
    ]))
    return FitResult(pose=pose, iou=final_iou, success=final_iou >= 0.9, n_evals=n_evals)


def fit_pose_mono(observed_mask, mesh, camera, prior_pose) -> FitResult:
    return _fit([observed_mask], mesh, [camera], prior_pose)


def fit_pose_stereo(observed_masks, mesh, cameras, prior_pose) -> FitResult:
    return _fit(list(observed_masks), mesh, list(cameras), prior_pose)
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run --group research pytest research/club_pose/tests/test_sim_posefit.py -v`
Expected: PASS (2 passed). (Powell on IoU+chamfer is derivative-free; the multi-start + chamfer signal handle the coarse seed. If `test_machinery_clean_recovery_stereo` is flaky, raise `maxiter` or add starts — do NOT loosen the 0.5°/1mm tolerance, which guards rasterizer/optimizer correctness.)

- [ ] **Step 5: Commit**

```bash
git add research/club_pose/sim/posefit.py research/club_pose/tests/test_sim_posefit.py
git commit -m "feat(club_pose.sim): add mono/stereo analysis-by-synthesis pose recovery"
```

---

### Task 6: `experiment.py` — sampling, ungated metrics, run + verdict

**Files:**
- Create: `research/club_pose/sim/experiment.py`
- Test: `research/club_pose/tests/test_sim_experiment.py`

**Interfaces:**
- Consumes: 0A `ClubTemplate`, `default_template`, `face_angle`, `dynamic_loft`, `ball_for_impact`; `HeadMesh`, rigs, `render_silhouette`, `degrade`, `PRESETS`, `fit_pose_mono`, `fit_pose_stereo`.
- Produces:
  - `pose_for_delivered(template, face_angle_deg, dynamic_loft_deg, head_center=(0,0,0)) -> ClubheadPose`.
  - `raw_metrics(pose, template, ball_center_world) -> (offset_mm, height_mm, face_angle_deg, dynamic_loft_deg)`.
  - `run_experiment(n, category, severity, baseline_mm, seed) -> dict`; `verdict(results) -> dict`.

- [ ] **Step 1: Write the failing tests**

`research/club_pose/tests/test_sim_experiment.py`:
```python
import numpy as np
import pytest

from club_pose.metrics import dynamic_loft, face_angle
from club_pose.sim.experiment import pose_for_delivered, raw_metrics, run_experiment, verdict
from club_pose.template import default_template


def test_pose_for_delivered_roundtrips_on_driver():
    t = default_template("driver")  # static loft 10.5
    pose = pose_for_delivered(t, face_angle_deg=3.0, dynamic_loft_deg=14.0)
    assert face_angle(pose, t) == pytest.approx(3.0, abs=1e-4)
    assert dynamic_loft(pose, t) == pytest.approx(14.0, abs=1e-4)


def test_raw_metrics_is_impact_aware_and_ungated():
    t = default_template("driver")
    pose = pose_for_delivered(t, 0.0, t.static_loft_deg)
    # ball far off the face would make compute_metrics return None; raw_metrics still returns numbers
    from club_pose.groundtruth import ball_for_impact
    ball = ball_for_impact(pose, t, 12.0, -6.0)
    off, hgt, fa, dl = raw_metrics(pose, t, ball)
    assert off == pytest.approx(12.0, abs=1e-3)
    assert hgt == pytest.approx(-6.0, abs=1e-3)
    assert np.isfinite(fa) and np.isfinite(dl)


def test_run_experiment_produces_verdict():
    res = run_experiment(n=3, category="iron", severity="light", baseline_mm=150.0, seed=0)
    v = verdict(res)
    assert "mono" in v and "stereo" in v
    assert "face_loft_deg_median" in v["mono"]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --group research pytest research/club_pose/tests/test_sim_experiment.py -v`
Expected: FAIL (ModuleNotFoundError).

- [ ] **Step 3: Implement**

`research/club_pose/sim/experiment.py`:
```python
"""The mono-vs-stereo experiment: sample poses, recover, propagate error, verdict."""
from __future__ import annotations

import numpy as np
from scipy.spatial.transform import Rotation

from ..groundtruth import ball_for_impact
from ..metrics import dynamic_loft, face_angle
from ..template import ClubTemplate, default_template
from ..types import ClubheadPose
from .camera import mono_rig, stereo_rig
from .degrade import PRESETS, degrade
from .headmesh import procedural
from .posefit import fit_pose_mono, fit_pose_stereo
from .silhouette import render_silhouette


def _r_loft(theta_deg: float) -> Rotation:
    return Rotation.from_rotvec(np.radians(-theta_deg) * np.array([0.0, 1.0, 0.0]))


def _r_face(angle_deg: float) -> Rotation:
    return Rotation.from_rotvec(np.radians(-angle_deg) * np.array([0.0, 0.0, 1.0]))


def pose_for_delivered(template, face_angle_deg, dynamic_loft_deg, head_center=(0.0, 0.0, 0.0)):
    rot = _r_face(face_angle_deg) * _r_loft(dynamic_loft_deg - template.static_loft_deg)
    return ClubheadPose(rot, np.asarray(head_center, dtype=float))


def raw_metrics(pose, template, ball_center_world):
    proj = template.point_to_face_uv(pose.world_to_body(ball_center_world))
    fa = face_angle(pose, template, normal_body=proj.normal_body)
    dl = dynamic_loft(pose, template, normal_body=proj.normal_body)
    return proj.u, proj.v, fa, dl


def _range_inplane_error(true_t, rec_t, camera):
    forward = camera.R_wc[2]  # optical axis in world
    d = rec_t - true_t
    rng = abs(float(np.dot(d, forward)))
    inplane = float(np.linalg.norm(d - np.dot(d, forward) * forward))
    return rng, inplane


def run_experiment(n=20, category="driver", severity="realistic", baseline_mm=150.0, seed=0) -> dict:
    rng = np.random.default_rng(seed)
    template = default_template(category)
    mesh = procedural(category)
    mono = mono_rig()
    cams = stereo_rig(baseline_mm)
    params = PRESETS[severity]
    rows = {"mono": [], "stereo": [], "n_fail_mono": 0, "n_fail_stereo": 0}

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
        obs_mono = degrade(render_silhouette(mesh, true, mono), params, rng)
        obs_stereo = [degrade(render_silhouette(mesh, true, c), params, rng) for c in cams]

        rm = fit_pose_mono(obs_mono, mesh, mono, prior)
        rs = fit_pose_stereo(obs_stereo, mesh, cams, prior)
        for tag, fit, cam in (("mono", rm, mono), ("stereo", rs, cams[0])):
            if not fit.success:
                rows[f"n_fail_{tag}"] += 1
                continue
            rot_err = float(np.degrees((true.rotation.inv() * fit.pose.rotation).magnitude()))
            rng_err, inplane_err = _range_inplane_error(true.translation, fit.pose.translation, cam)
            t_rec = raw_metrics(fit.pose, template, ball)
            rows[tag].append({
                "rot_err_deg": rot_err,
                "camera_range_error_mm": rng_err,
                "inplane_error_mm": inplane_err,
                "offset_err_mm": abs(t_rec[0] - t_true[0]),
                "height_err_mm": abs(t_rec[1] - t_true[1]),
                "face_err_deg": abs(t_rec[2] - t_true[2]),
                "loft_err_deg": abs(t_rec[3] - t_true[3]),
            })
    return rows


def _median(rows, key):
    vals = [r[key] for r in rows]
    return float(np.median(vals)) if vals else float("nan")


def verdict(results) -> dict:
    out = {}
    for tag in ("mono", "stereo"):
        rows = results[tag]
        face_loft = [max(r["face_err_deg"], r["loft_err_deg"]) for r in rows]
        impact = [np.hypot(r["offset_err_mm"], r["height_err_mm"]) for r in rows]
        out[tag] = {
            "n": len(rows),
            "n_fail": results[f"n_fail_{tag}"],
            "face_loft_deg_median": float(np.median(face_loft)) if face_loft else float("nan"),
            "impact_mm_median": float(np.median(impact)) if impact else float("nan"),
            "camera_range_error_mm_median": _median(rows, "camera_range_error_mm"),
        }
    out["note"] = "Geometric/optimistic bound (no real-frame segmentation). Single-camera result is an upper bound."
    return out
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run --group research pytest research/club_pose/tests/test_sim_experiment.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Run the full suite + record the verdict**

Run: `uv run --group research pytest research/club_pose/tests/ -v`
Expected: PASS (all 0A + 0B-1 tests green).

Then capture the headline result (not a test — an artifact for the design decision):
```bash
uv run --group research python -c "import sys; sys.path.insert(0,'research'); from club_pose.sim.experiment import run_experiment, verdict; import json; print(json.dumps(verdict(run_experiment(n=30, category='driver', severity='realistic', seed=1)), indent=2)); print(json.dumps(verdict(run_experiment(n=30, category='iron', severity='realistic', seed=1)), indent=2))"
```
Expected: two verdict dicts (driver, iron) with `mono` vs `stereo` face/loft and impact medians — **the single-vs-stereo answer**.

- [ ] **Step 6: Commit**

```bash
git add research/club_pose/sim/experiment.py research/club_pose/tests/test_sim_experiment.py
git commit -m "feat(club_pose.sim): add mono-vs-stereo experiment, ungated metrics, verdict"
```

---

## Self-Review (completed by plan author)

**Spec coverage:** camera look-at + rigs + in-frame (§4, §5.1, §7) → Task 1; procedural + distinctive + frozen mesh (§5.2) → Task 2; silhouette + IoU/chamfer (§5.3) → Task 3; degradations + presets (§5.4) → Task 4; analysis-by-synthesis mono/stereo + machinery validation + stereo-beats-mono (§5.5, §7) → Task 5; `pose_for_delivered` (review #4), ungated impact-aware `raw_metrics` (review #1/#2), `run_experiment` with camera-range/in-plane split (review #2), `verdict` tiers (§5.6, §8) → Task 6. Dependency group under existing table (§3, review #3) → Task 0. Mesh-bias handling (review #3): machinery validated via distinctive mesh/stereo (Task 5), realistic-mesh mono recovery reported as a result by the experiment (Task 6), not gated.

**Placeholder scan:** every code/test/command step is concrete; no TBD/TODO/"similar to".

**Type consistency:** names consistent across tasks — `CameraIntrinsics`, `Camera.look_at`, `project`, `mono_rig`, `stereo_rig`, `HeadMesh`, `procedural`, `distinctive_test_mesh`, `render_silhouette`, `iou`, `chamfer`, `DegradationParams`, `PRESETS`, `degrade`, `FitResult`, `fit_pose_mono`, `fit_pose_stereo`, `pose_for_delivered`, `raw_metrics`, `run_experiment`, `verdict`. `fit_*` return `FitResult(pose, iou, success, n_evals)` used by Task 6. `camera.R_wc[2]` (forward row) used for the range axis matches Task 1's `_look_at_rows` ordering `[right, down, forward]`.

**Note on task ordering:** Task 2's tests import `silhouette` (Task 3). Implement Task 3 before re-running Task 2's tests (called out in Task 2 Step 4). All other tasks are strictly ordered.
