# Stage 0B-3 — Photoreal detectability of behind-ball iron features — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Measure whether a detector can localize a real 7-iron's *actually-detectable* features (topline, hosel, cavity edges) to ~1–2 px on **photoreal behind-ball renders** (specular chrome + lighting + motion blur), then feed the detectable subset + its **measured** noise back through the 0B-2 geometry (iron template) → **GO / NO-GO / INCONCLUSIVE**.

**Architecture:** New `research/club_pose/detect/` package. Real Titleist 690CB CAD (STL) → oriented into the 0A body frame → Blender chrome renders (camera matched to our pinhole) with ground-truth feature pixels → classical **dominant-primitive** detection → loop-back through `fit_pose_pnp` with the loft-36 iron template. The learned detector (the true upper bound) is a **deferred** follow-on.

**Tech Stack:** Python, numpy, scipy, OpenCV, **Blender (`bpy`)**, pytest. Windows dev box. `uv run --group research`.

## Global Constraints

- **`uv` full path** (not on PATH): `C:\Users\harjo\AppData\Local\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe`. Substitute for `uv` below. Non-render tests resolve `club_pose` via the pytest rootdir; standalone scripts self-locate `sys.path` (no `PYTHONPATH` prefix, no shell heredoc — not PowerShell-safe). `git commit -m` with quoted strings, not heredocs.
- **All geometry uses `T = default_template("iron").with_loft_override(36.0)`** (this club's loft), **not** the bare 34° default. `T.face_center_normal_body()` (the loft-tilted normal, *not* +X) and `T.face_center_offset` = (30,0,0) define the canonical frame.
- **Honesty gates (from the spec):** detection uses a **dominant-primitive + peak-to-clutter** metric (never nearest-anything, never GT-seeded); the **loop-back must clear the bar** for a GO (localizing features is necessary, not sufficient); **NO-GO requires the learned detector** (§ deferred) — classical-only yields GO or INCONCLUSIVE; motion blur is a **swept physical τ** (streak = v·τ), not a Blender knob; the **verdict requires rendering** (Blender-absent skips test only scaffolding).
- **Blender is a hard dependency for the verdict.** Render tests `skipif` Blender/`bpy` is unavailable; those skips do **not** count as passing the stage.
- **Two tasks have human-in-the-loop checkpoints** (CAD handedness vs reference photos; render realism) — this stage is part-exploratory; that is expected and flagged inline.
- TDD, frequent commits. DRY, YAGNI.

---

### Task 1: CAD ingest + orientation into the 0A body frame (`cad.py`)

**Files:**
- Create: `research/club_pose/detect/__init__.py` (empty), `research/club_pose/detect/cad.py`
- Add asset: `research/club_pose/detect/assets/690CB_7iron.stl` (copy of the maintainer's STL)
- Test: `research/club_pose/tests/test_detect_cad.py`

**Interfaces:**
- Consumes: `default_template` (`..template`); `HeadMesh`, `_ArrayWithPtp` (`..sim.headmesh`); `camera.mono_rig`, `silhouette.render_silhouette` (for the 3-view).
- Produces: `load_stl(path) -> (verts (N,3), faces (M,3))`; `fit_face_plane(verts) -> (normal, centroid)`; `fit_hosel_axis(verts) -> axis`; `orient_to_body(verts, template) -> (verts_body, transform_4x4)`; `oriented_iron() -> HeadMesh` (cached from the committed asset + a stored transform).

- [ ] **Step 1: Write the failing test**

`research/club_pose/tests/test_detect_cad.py`:

```python
import os

import numpy as np
import pytest

from club_pose.detect.cad import load_stl, oriented_iron
from club_pose.template import default_template

_STL = os.path.join(os.path.dirname(__file__), "..", "detect", "assets", "690CB_7iron.stl")
T = default_template("iron").with_loft_override(36.0)


@pytest.mark.skipif(not os.path.exists(_STL), reason="690CB STL asset not present")
def test_stl_loads():
    v, f = load_stl(_STL)
    assert v.shape[1] == 3 and f.shape[1] == 3 and len(f) > 20000


@pytest.mark.skipif(not os.path.exists(_STL), reason="690CB STL asset not present")
def test_oriented_frame_matches_template():
    mesh = oriented_iron()
    # face center (the mesh point nearest body (30,0,0)) round-trips through the iron face
    proj = T.point_to_face_uv(np.array([30.0, 0.0, 0.0]))
    assert abs(proj.signed_distance_mm) < 3.0  # (30,0,0) is on/near the template face
    # extents ~ a 7-iron head+hosel (heel-toe ~70-90, tall incl. hosel ~100-130, depth ~30-45)
    ext = np.ptp(mesh.vertices, axis=0)
    assert 60 < ext.max() < 140 and mesh.vertices.shape[0] > 100
```

- [ ] **Step 2: Copy the STL asset**

Copy `C:\Users\harjo\Downloads\titleist-7-iron-golf-club-1.snapshot.5\690CB 7-iron.STL` → `research/club_pose/detect/assets/690CB_7iron.stl`. (RH head+hosel, binary, mm, 26,238 tris.)

- [ ] **Step 3: Run the test to verify it fails**

Run: `uv run --group research pytest research/club_pose/tests/test_detect_cad.py -v`
Expected: FAIL — `ModuleNotFoundError: club_pose.detect.cad`.

- [ ] **Step 4: Implement `cad.py`**

```python
"""Load the 690CB iron STL and orient it into the 0A body frame (consistent with the template)."""
from __future__ import annotations

import os
import struct

import numpy as np

from ..sim.headmesh import HeadMesh, _ArrayWithPtp
from ..template import default_template

_ASSET = os.path.join(os.path.dirname(__file__), "assets", "690CB_7iron.stl")
# Baked one-time orientation (Step 6 human calibration writes these). Identity until calibrated.
_PRE_ROT = np.eye(3)   # maps raw-CAD axes -> approximate body axes (handedness/toe fixed here)


def load_stl(path):
    with open(path, "rb") as fh:
        fh.read(80)
        n = struct.unpack("<I", fh.read(4))[0]
        raw = np.frombuffer(fh.read(n * 50), dtype=np.uint8).reshape(n, 50)
    tri = np.frombuffer(np.ascontiguousarray(raw[:, 12:48]).tobytes(), dtype="<f4").reshape(n, 3, 3)
    verts = tri.reshape(-1, 3).astype(float)
    faces = np.arange(n * 3).reshape(n, 3)
    return verts, faces


def fit_face_plane(verts):
    # The face is the dominant large planar region: RANSAC a plane, pick the largest inlier set.
    rng = np.random.default_rng(0)
    best_in, best = None, None
    for _ in range(400):
        idx = rng.choice(len(verts), 3, replace=False)
        p0, p1, p2 = verts[idx]
        nrm = np.cross(p1 - p0, p2 - p0)
        na = np.linalg.norm(nrm)
        if na < 1e-6:
            continue
        nrm = nrm / na
        d = np.abs((verts - p0) @ nrm)
        inl = d < 0.8
        if best_in is None or inl.sum() > best_in.sum():
            best_in, best = inl, (nrm, p0)
    pts = verts[best_in]
    c = pts.mean(0)
    _, _, vt = np.linalg.svd(pts - c)
    normal = vt[2]
    return normal, c


def fit_hosel_axis(verts):
    # Hosel = the thin near-cylindrical extreme; approximate its axis by the principal direction
    # of the top (max-projected) cluster. Refined during the human calibration if needed.
    z = verts[:, 2]
    top = verts[z > np.percentile(z, 85)]
    c = top.mean(0)
    _, _, vt = np.linalg.svd(top - c)
    return vt[0]


def _align(a, b):
    """Rotation mapping unit vector a onto unit vector b."""
    a = a / np.linalg.norm(a)
    b = b / np.linalg.norm(b)
    v = np.cross(a, b)
    c = float(a @ b)
    if np.linalg.norm(v) < 1e-9:
        return np.eye(3) if c > 0 else -np.eye(3)
    vx = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + vx + vx @ vx * (1.0 / (1.0 + c))


def orient_to_body(verts, template):
    v = verts @ _PRE_ROT.T                       # apply the baked coarse axis fix
    n_face, c_face = fit_face_plane(v)
    if n_face @ (v.mean(0) - c_face) > 0:        # outward normal points away from the centroid
        n_face = -n_face
    R = _align(n_face, template.face_center_normal_body())
    v = v @ R.T
    c_face_b = (c_face @ _PRE_ROT.T) @ R.T
    t = template.face_center_offset - c_face_b   # face center -> (30,0,0)
    v = v + t
    transform = np.eye(4)
    transform[:3, :3] = R @ _PRE_ROT
    transform[:3, 3] = t
    return v, transform


def oriented_iron() -> HeadMesh:
    verts, faces = load_stl(_ASSET)
    vb, _ = orient_to_body(verts, default_template("iron").with_loft_override(36.0))
    return HeadMesh(np.asarray(vb, float).view(_ArrayWithPtp), faces.astype(np.int64), "iron_690cb")
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run --group research pytest research/club_pose/tests/test_detect_cad.py -v`
Expected: 2 passed (or skipped if the STL wasn't copied — then copy it first).

- [ ] **Step 6: HUMAN CHECKPOINT — verify orientation against the reference photos**

Render the oriented mesh from behind (`mono_rig`) + the 3 canonical views (reuse the audit-viz approach) to `research/club_pose/detect/assets/orient_check.png`. Compare to the 4 reference JPGs in the Downloads folder: confirm **toe = +Y, sole down, face points downrange-and-up at ~36°, hosel up-and-heel**. If mirrored/rotated, set `_PRE_ROT` to the correcting rotation (e.g. a 90° axis swap) and re-run. This is a one-time calibration; commit `_PRE_ROT` once correct.

- [ ] **Step 7: Commit**

```bash
git add research/club_pose/detect/__init__.py research/club_pose/detect/cad.py research/club_pose/detect/assets/690CB_7iron.stl research/club_pose/detect/assets/orient_check.png research/club_pose/tests/test_detect_cad.py
git commit -m "feat(club_pose.detect): ingest + orient the 690CB iron CAD into the 0A body frame (0B-3)"
```

---

### Task 2: Iron feature keypoints + GT projection + visibility (`features.py`)

**Files:**
- Create: `research/club_pose/detect/features.py`
- Test: `research/club_pose/tests/test_detect_features.py`

**Interfaces:**
- Consumes: `oriented_iron` (Task 1); `camera` (`..sim.camera`); `Keypoint`/detect-visibility rule (mirror `..sim.keypoints`); `pose_for_delivered` (`..sim.experiment`); `T`.
- Produces: `IronFeature(name, xyz, normal)`; `iron_features() -> dict[str,IronFeature]`; `project_features(pose, camera, features) -> dict[name, uv]` (visible only, via the normal rule).

- [ ] **Step 1: Write the failing test**

`research/club_pose/tests/test_detect_features.py`:

```python
import numpy as np

from club_pose.detect.features import iron_features, project_features
from club_pose.sim.camera import mono_rig
from club_pose.sim.experiment import pose_for_delivered
from club_pose.template import default_template

T = default_template("iron").with_loft_override(36.0)


def test_features_defined_on_mesh():
    feats = iron_features()
    assert {"topline_toe", "topline_heel", "hosel_junction"} <= set(feats)


def test_behind_visibility_excludes_leading_edge():
    cam = mono_rig()
    pose = pose_for_delivered(T, 0.0, T.static_loft_deg, (0, 0, 0))
    vis = project_features(pose, cam, iron_features())
    assert "hosel_junction" in vis or "topline_toe" in vis  # back/top features visible
    # a pure front-face feature at +X normal is occluded from behind
    assert "leading_edge_mid" not in vis
```

- [ ] **Step 2: Run to verify it fails** — `ModuleNotFoundError: club_pose.detect.features`.

- [ ] **Step 3: Implement `features.py`** (body coords; **finalized against the oriented mesh in Step 4** — provisional values here, adjust to sit on the real surface):

```python
"""Named iron features (body coords + outward normal) + GT projection with normal-visibility."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class IronFeature:
    name: str
    xyz: np.ndarray
    normal: np.ndarray


# Provisional — refine to lie on oriented_iron()'s surface in Step 4 (edges/hosel visible from behind).
_F = {
    "topline_toe": ((28, 34, 22), (0.4, 0.2, 0.9)),
    "topline_heel": ((28, -30, 20), (0.4, -0.2, 0.9)),
    "topline_mid": ((30, 2, 24), (0.5, 0, 0.87)),
    "hosel_junction": ((10, -34, 30), (-0.2, -0.7, 0.68)),
    "cavity_top_toe": ((5, 32, 10), (-0.5, 0.3, 0.8)),
    "cavity_top_heel": ((5, -28, 10), (-0.5, -0.3, 0.8)),
    "trailing_sole": ((-8, 0, -22), (-0.6, 0, -0.8)),
    "leading_edge_mid": ((40, 0, -18), (0.7, 0, -0.7)),  # occluded from behind (control)
}


def iron_features() -> dict:
    out = {}
    for name, (p, n) in _F.items():
        nv = np.asarray(n, float)
        out[name] = IronFeature(name, np.asarray(p, float), nv / np.linalg.norm(nv))
    return out


def project_features(pose, camera, features) -> dict:
    w, h = camera.intrinsics.width, camera.intrinsics.height
    vis = {}
    for f in features.values():
        pw = pose.body_to_world(f.xyz)
        nw = pose.direction_to_world(f.normal)
        if float(nw @ (camera.center_world - pw)) <= 0:
            continue
        (uv,), infront = camera.project(pw[None, :])
        if infront[0] and 0 <= uv[0] < w and 0 <= uv[1] < h:
            vis[f.name] = np.asarray(uv, float)
    return vis
```

- [ ] **Step 4: Refine feature coords onto the surface + run tests**

For each feature, snap `xyz` to the nearest `oriented_iron().vertices` point in that region so it lies on the real surface; re-verify visibility. Run: `uv run --group research pytest research/club_pose/tests/test_detect_features.py -v` → 2 passed.

- [ ] **Step 5: Commit**

```bash
git add research/club_pose/detect/features.py research/club_pose/tests/test_detect_features.py
git commit -m "feat(club_pose.detect): iron feature keypoints + GT projection + behind-visibility (0B-3)"
```

---

### Task 3: Blender camera matched to our pinhole — the consistency gate (`render.py`)

**Files:**
- Create: `research/club_pose/detect/render.py`
- Test: `research/club_pose/tests/test_detect_render.py`

**Interfaces:**
- Consumes: `IMX296`, `mono_rig` (`..sim.camera`); `camera.project`.
- Produces: `blender_available() -> bool`; `setup_camera(scene, intrinsics, cam) -> bpy camera`; `world_to_px_blender(point) -> (u,v)`; `render_frame(pose, tau_us, lighting, out_path)`.

- [ ] **Step 1: Write the failing test (the critical camera-match)**

`research/club_pose/tests/test_detect_render.py`:

```python
import numpy as np
import pytest

from club_pose.detect.render import blender_available, world_to_px_blender
from club_pose.sim.camera import mono_rig

pytestmark = pytest.mark.skipif(not blender_available(), reason="Blender/bpy not available")


def test_blender_camera_matches_pinhole():
    cam = mono_rig()
    pts = np.array([[0, 0, 0], [50, 0, 0], [0, 40, 0], [0, 0, 60], [-20, 15, 10]], float)
    for p in pts:
        (uv_ours,), _ = cam.project(p[None, :])
        uv_bl = world_to_px_blender(p)
        assert np.linalg.norm(np.array(uv_bl) - uv_ours) < 1.0  # <1 px agreement
```

- [ ] **Step 2: Run to verify it fails/skips** — skips if `bpy` absent; else FAIL (`world_to_px_blender` undefined).

- [ ] **Step 3: Implement `render.py`** (camera build from intrinsics + `bpy_extras` projection; chrome material; τ→motion-blur):

```python
"""Blender scene: camera matched to IMX296+mono_rig, polished-chrome iron, tau-swept motion blur."""
from __future__ import annotations

import numpy as np

from ..sim.camera import IMX296, mono_rig


def blender_available() -> bool:
    try:
        import bpy  # noqa: F401
        return True
    except Exception:
        return False


def _look_at_matrix(cam):
    # Blender camera looks down -Z with +Y up; our R_wc rows are [right,down,forward].
    right, down, forward = cam.R_wc
    R_bl = np.column_stack([right, -down, -forward])  # world<-camera (Blender convention)
    M = np.eye(4)
    M[:3, :3] = R_bl
    M[:3, 3] = cam.center_world / 1000.0  # mm -> m
    return M


def setup_camera(scene, intrinsics=IMX296, cam=None):
    import bpy
    from mathutils import Matrix

    cam = cam or mono_rig()
    obj = bpy.data.objects["Camera"]
    obj.matrix_world = Matrix(_look_at_matrix(cam).tolist())
    sensor_w_mm = 5.02  # IMX296 1456*3.45um
    camd = obj.data
    camd.sensor_fit = "HORIZONTAL"
    camd.sensor_width = sensor_w_mm
    camd.lens = intrinsics.fx * sensor_w_mm / intrinsics.width  # f_px -> f_mm
    camd.shift_x = (intrinsics.width / 2 - intrinsics.cx) / intrinsics.width
    camd.shift_y = (intrinsics.cy - intrinsics.height / 2) / intrinsics.width
    scene.render.resolution_x = intrinsics.width
    scene.render.resolution_y = intrinsics.height
    return obj


def world_to_px_blender(point_mm, intrinsics=IMX296, cam=None):
    import bpy
    from bpy_extras.object_utils import world_to_camera_view
    from mathutils import Vector

    scene = bpy.context.scene
    obj = setup_camera(scene, intrinsics, cam)
    co = world_to_camera_view(scene, obj, Vector((np.asarray(point_mm) / 1000.0).tolist()))
    return (co.x * intrinsics.width, (1.0 - co.y) * intrinsics.height)
```

(`render_frame` — chrome Principled BSDF, HDRI/area lighting, `scene.render.motion_blur` with shutter set so streak ≈ `v·τ` — is added here; it has no unit test, it is exercised in Task 4.)

- [ ] **Step 4: Run the camera-match test**

Run: `uv run --group research pytest research/club_pose/tests/test_detect_render.py -v`
Expected: PASS (<1 px) if Blender present; else SKIP. **If it fails, the shift_x/shift_y sign or the look-at handedness is wrong — fix here; nothing downstream is valid until this passes.**

- [ ] **Step 5: Commit**

```bash
git add research/club_pose/detect/render.py research/club_pose/tests/test_detect_render.py
git commit -m "feat(club_pose.detect): Blender camera matched to pinhole + chrome scene (0B-3)"
```

---

### Task 4: Modest chrome render set (τ-swept) + GT sidecar — HUMAN CHECKPOINT

**Files:**
- Modify: `research/club_pose/detect/render.py` (`render_set(...)`)
- Create: `research/club_pose/detect/run_render_0b3.py` (committed runner)

**Interfaces:**
- Consumes: `render_frame`, `oriented_iron`, `iron_features`, `project_features`, `pose_for_delivered`, `T`.
- Produces: a folder of ~50–100 PNG renders + a `gt.jsonl` (per frame: pose, τ, lighting, `{feature: [u,v]}` from `project_features`).

- [ ] **Step 1: Implement `render_set` + the runner** — sample delivered iron poses; for `τ ∈ {~0,10,20,50} µs` (streak = `v·τ`, v≈45 m/s) and a few lighting setups, render to PNG and write the GT sidecar (GT via `project_features`, **our** pinhole — Task 3 guarantees they agree).
- [ ] **Step 2: Render a small batch** — `uv run --group research python research/club_pose/detect/run_render_0b3.py --n 60 --out research/club_pose/detect/renders/`.
- [ ] **Step 3: HUMAN CHECKPOINT — inspect the renders.** Open ~5 renders across τ/lighting: does the iron look plausibly chrome, are the topline/hosel/cavity visible, do the GT dots (overlay a debug render) land on the right features, does motion blur look physical? Fix material/lighting/GT until they do. **This is the realism gate — the whole result depends on it.**
- [ ] **Step 4: Commit** the runner + a few sample renders (not the whole set):
```bash
git add research/club_pose/detect/render.py research/club_pose/detect/run_render_0b3.py research/club_pose/detect/renders/sample_*.png
git commit -m "feat(club_pose.detect): tau-swept chrome render set + GT sidecar (0B-3)"
```

---

### Task 5: Classical detection + dominant-primitive metric — the cheap probe (`classical.py`)

**Files:**
- Create: `research/club_pose/detect/classical.py`
- Test: `research/club_pose/tests/test_detect_classical.py`

**Interfaces:**
- Consumes: OpenCV; a **committed fixture image** with a known high-contrast edge (for the CI test).
- Produces: `detect_feature(img, region, kind) -> (uv | None, dominance)`; `localization_error(img, gt_uv, kind) -> {px, dominance, detected}`; `run_classical(renders_dir) -> per-feature {median_px, dominance, detect_rate} vs tau`.

- [ ] **Step 1: Write the failing test** — the dominant-primitive metric on a synthetic control (a clean line at a known location must be localized <2 px with high dominance; a uniform noise image must report `detected=False`):

```python
import numpy as np

from club_pose.detect.classical import localization_error


def test_clean_line_is_detected_tightly():
    img = np.zeros((200, 200), np.uint8)
    img[:, 100] = 255  # vertical line at x=100
    r = localization_error(img, np.array([100.0, 100.0]), kind="line")
    assert r["detected"] and r["px"] < 2.0 and r["dominance"] > 2.0


def test_pure_noise_is_not_detected():
    rng = np.random.default_rng(0)
    img = rng.integers(0, 255, (200, 200), np.uint8)
    r = localization_error(img, np.array([100.0, 100.0]), kind="line")
    assert not r["detected"]  # low dominance -> clutter -> not a real detection
```

- [ ] **Step 2: Run to verify it fails** — `ModuleNotFoundError`.

- [ ] **Step 3: Implement `classical.py`** — the **dominant-primitive** metric: run the feature-appropriate detector in the region, take the strongest response, compute `dominance = strongest / second_strongest` (or peak / local-clutter density); `detected = (dist_to_gt <= R) and (dominance >= D_min)`. Return `{px, dominance, detected}`. `run_classical` aggregates per feature × τ from the render set + `gt.jsonl`, writes annotated overlays.

- [ ] **Step 4: Run the tests** — `uv run --group research pytest research/club_pose/tests/test_detect_classical.py -v` → 2 passed.

- [ ] **Step 5: Run the probe on the renders (artifact)** — `uv run --group research python -c "from club_pose.detect.classical import run_classical; import json; print(json.dumps(run_classical('research/club_pose/detect/renders/'), indent=2))" > research/club_pose/detect/classical_0b3.json`. Save annotated overlays for the user.

- [ ] **Step 6: Commit**

```bash
git add research/club_pose/detect/classical.py research/club_pose/tests/test_detect_classical.py research/club_pose/detect/classical_0b3.json research/club_pose/detect/renders/annotated_*.png
git commit -m "feat(club_pose.detect): classical dominant-primitive detection + per-feature px vs tau (0B-3)"
```

---

### Task 6: Loop-back to iron geometry (`loopback.py`)

**Files:**
- Create: `research/club_pose/detect/loopback.py`
- Test: `research/club_pose/tests/test_detect_loopback.py`

**Interfaces:**
- Consumes: measured per-feature `(σ_px, detect_rate)` (from Task 5); `iron_features`; `fit_pose_pnp` (`..sim.posefit_kp`); `pose_for_delivered`, `raw_metrics` (`..sim.experiment`); `mono_rig`; `T`.
- Produces: `run_loopback(feature_noise: dict[name,(sigma,rate)], n, seed) -> {impact_mm_median, face_err_deg_median, loft_err_deg_median, ok_rate}`.

- [ ] **Step 1: Write the failing test** — with tiny σ the loop-back recovers tightly; with huge σ it degrades (monotonic, same honesty shape as 0B-2):

```python
import numpy as np

from club_pose.detect.loopback import run_loopback


def test_tight_noise_clears_bar():
    feats = {"topline_toe": (0.5, 1.0), "topline_heel": (0.5, 1.0),
             "topline_mid": (0.5, 1.0), "hosel_junction": (0.5, 1.0),
             "cavity_top_toe": (0.5, 1.0), "cavity_top_heel": (0.5, 1.0)}
    r = run_loopback(feats, n=30, seed=0)
    assert r["ok_rate"] >= 0.9 and r["impact_mm_median"] < 5.0


def test_huge_noise_degrades():
    feats = {k: (8.0, 1.0) for k in ("topline_toe", "topline_heel", "topline_mid",
                                     "hosel_junction", "cavity_top_toe", "cavity_top_heel")}
    r = run_loopback(feats, n=30, seed=0)
    assert r["impact_mm_median"] > 5.0
```

- [ ] **Step 2: Run to verify it fails** — `ModuleNotFoundError`.

- [ ] **Step 3: Implement `loopback.py`** — sample delivered iron poses (`T`, loft 36); project the given features via `mono_rig`; add the **measured** per-feature Gaussian σ + Bernoulli(rate) dropout; build `Detection`s (reuse the `..sim.keypoints.Detection` dataclass or a compatible namedtuple with `name/xyz_body/uv`); `fit_pose_pnp`; `raw_metrics` → impact/face/loft errors; aggregate medians + `ok_rate` (≥0.9 gate).

- [ ] **Step 4: Run the tests** — 2 passed.

- [ ] **Step 5: Commit**

```bash
git add research/club_pose/detect/loopback.py research/club_pose/tests/test_detect_loopback.py
git commit -m "feat(club_pose.detect): loop-back measured detection noise -> impact/face/loft (iron) (0B-3)"
```

---

### Task 7: Orchestrate + verdict (`experiment_detect.py`, `RESULTS_0B3.md`)

**Files:**
- Create: `research/club_pose/detect/experiment_detect.py`, `research/club_pose/detect/RESULTS_0B3.md`
- Modify: `docs/Personal Research/markerless-club-data-guide-v2-research-corrected.md`

**Interfaces:**
- Consumes: `run_classical` (Task 5), `run_loopback` (Task 6).
- Produces: the **GO / NO-GO / INCONCLUSIVE** decision.

- [ ] **Step 1: Full non-render suite green** — `uv run --group research pytest research/club_pose/tests/ -v` (render/CAD/Blender tests skip cleanly if assets/Blender absent; the rest green).
- [ ] **Step 2: Produce the verdict** — feed the classical per-feature `(median_px → σ, detect_rate)` into `run_loopback`; combine with the detectability table (vs τ). Decide per §5.7:
  - **GO** — classical localizes enough features tightly **and** the loop-back clears the impact/face/loft bar at `ok_rate ≥ 0.9`.
  - **INCONCLUSIVE** — classical fails; learned detector not yet run (no GPU) → cheap path out, verdict pending §5.5.
  - **NO-GO** — only after the (deferred) learned detector also fails.
- [ ] **Step 3: Write `RESULTS_0B3.md`** — per-feature px + dominance + detect-rate vs τ; the loop-back impact/face/loft under measured noise; annotated render examples; the verdict + the honest caveat (in-sim upper bound; chrome fidelity; iron-specific; learned detector required for a hard NO-GO).
- [ ] **Step 4: Commit + fold into the v2 guide**

```bash
git add research/club_pose/detect/experiment_detect.py research/club_pose/detect/RESULTS_0B3.md
git commit -m "docs(club_pose.detect): Stage 0B-3 verdict — iron feature detectability + loop-back"
git add "docs/Personal Research/markerless-club-data-guide-v2-research-corrected.md"
git commit -m "docs(research): fold Stage 0B-3 detectability verdict into the v2 guide"
```

---

## Self-Review

- **Spec coverage:** §5.1 CAD→Task 1; §5.3 features→Task 2; §5.2 camera/material→Task 3; §5.2 blur/render set→Task 4; §5.4 classical + dominant metric→Task 5; §5.6 loop-back→Task 6; §5.7 verdict→Task 7. §5.5 learned detector is explicitly **deferred** (a NO-GO cannot be finalized without it — stated in Task 7 Step 2). All in-scope sections map to a task.
- **Honesty gates present:** dominant-primitive metric with a pure-noise negative test (Task 5); loop-back must clear the bar for GO (Task 7); `ok_rate` gate (Task 6); τ as physical streak (Task 4); Blender-absent skips ≠ verdict (Global Constraints + Task 7 Step 2); NO-GO requires the deferred learned detector.
- **Placeholder scan:** feature coords in Task 2 are explicitly *provisional, refined onto the surface in Step 4* (not a hidden TBD); the CAD `_PRE_ROT` is calibrated once in Task 1 Step 6. The two human checkpoints (CAD handedness, render realism) are inherent to a photoreal stage, called out inline.
- **Type consistency:** `IronFeature(name,xyz,normal)`, the `(name,xyz_body,uv)` Detection shape reused from 0B-2, `T = default_template("iron").with_loft_override(36.0)` used everywhere, `run_loopback` signature matches Task 7's call.
- **Camera-match is the load-bearing gate** (Task 3): nothing downstream is valid until the Blender camera agrees with `camera.project` to <1 px — enforced by a test.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-30-club-pose-stage0b3-photoreal-iron-detectability.md`.

**Note the two human-in-the-loop checkpoints** (Task 1 Step 6 CAD-vs-photos; Task 4 Step 3 render realism) — this stage is part-exploratory, so a fully hands-off subagent run will stall at those. Two execution options:

1. **Subagent-Driven with human checkpoints (recommended)** — fresh subagent per task; you (or I) resolve the two visual checkpoints between tasks.
2. **Inline execution** — run tasks in-session with checkpoints for review.

Which approach — and shall I commit the plan (plus the pending 0B-2 evidence artifact once its regen lands)?
