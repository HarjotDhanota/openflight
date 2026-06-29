# Clubhead Pose → Golf Metrics Geometry Core (Stage 0A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the pure-Python geometry core that turns a clubhead 6-DOF body pose + a club template into golf metrics (impact location mm, face angle, dynamic loft, club path, attack angle), validated to numerical precision against analytic ground truth, plus a sensitivity error-budget harness.

**Architecture:** A standalone sandbox package `research/club_pose/` (outside `src/openflight/`). Pose maps body→world; the template embeds a curved face (bulge/roll) in body coords with a loft override. All rotation math uses `scipy.spatial.transform.Rotation`. Six focused modules: `types`, `frames`, `template`, `metrics`, `groundtruth`, `sensitivity`.

**Tech Stack:** Python 3.10+, numpy, scipy (both already repo base deps), pytest (dev). No new dependencies.

**Spec:** `docs/superpowers/specs/2026-06-28-club-pose-geometry-core-design.md` (rev. 4).

## Global Constraints

- **Right-handed world frame:** +X downrange, +Y player-left ("right" = −Y), +Z up.
- **Body frame:** origin = clubhead geometric center; at identity pose body axes = world axes; square zero-loft face normal = +X.
- **Output sign conventions:** face angle open/right positive; dynamic loft up positive; club path in-to-out/right positive; attack angle up positive; impact offset toe(+u) positive; impact height high(+v) positive.
- **Loft sign:** `R_loft(θ)` = rotation about `û` (=+Y canonical) by `−θ`.
- **Convex face:** `h(u,v) = −u²/(2R_b) − v²/(2R_v)`; outward normal `∝ (+u/R_b, +v/R_v, 1)`.
- **Constants:** `BALL_RADIUS_MM = 21.35`; `CONTACT_TOL_MM = 3.0` default.
- **Internal imports are relative** (`from .types import ...`) so `types.py` never shadows stdlib.
- **Test gate:** `uv run pytest research/club_pose/tests/ -v`. If `uv` is not on PATH in your shell, use the full winget path `"$env:LOCALAPPDATA\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe" run pytest ...` or open a fresh terminal.
- **Every commit message ends with:** `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>` (omitted from the per-step snippets below for brevity — always append it).

---

### Task 0: Scaffold the sandbox package

**Files:**
- Create: `research/club_pose/__init__.py`, `research/club_pose/README.md`
- Create: `research/club_pose/tests/__init__.py`, `research/club_pose/tests/conftest.py`
- Test: `research/club_pose/tests/test_smoke.py`

**Interfaces:**
- Consumes: nothing.
- Produces: an importable `club_pose` package (with `research/` on `sys.path` via conftest).

- [ ] **Step 1: Create the package files**

`research/club_pose/__init__.py`:
```python
"""Stage 0A geometry core: clubhead pose + template -> golf metrics (sandbox)."""
```

`research/club_pose/tests/__init__.py`:
```python
```

`research/club_pose/tests/conftest.py`:
```python
import pathlib
import sys

# Put research/ on sys.path so `import club_pose` resolves while the package
# lives outside src/openflight/. Internal modules use relative imports, so the
# club_pose/types.py module never shadows stdlib `types`.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
```

`research/club_pose/README.md`:
```markdown
# club_pose (Stage 0A geometry core)

Pure-Python sandbox: clubhead 6-DOF body pose + club template -> golf metrics
(impact location, face angle, dynamic loft, club path, attack angle), validated
against analytic ground truth. Spec: docs/superpowers/specs/2026-06-28-club-pose-geometry-core-design.md

Run tests: `uv run pytest research/club_pose/tests/ -v`
```

- [ ] **Step 2: Write the smoke test**

`research/club_pose/tests/test_smoke.py`:
```python
def test_package_imports():
    import club_pose

    assert club_pose.__doc__ is not None
```

- [ ] **Step 3: Run it**

Run: `uv run pytest research/club_pose/tests/test_smoke.py -v`
Expected: PASS (1 passed).

- [ ] **Step 4: Commit**

```bash
git add research/club_pose
git commit -m "feat(club_pose): scaffold Stage 0A geometry sandbox package"
```

---

### Task 1: `types.py` — Measurement, ClubheadPose, ClubMetrics

**Files:**
- Create: `research/club_pose/types.py`
- Test: `research/club_pose/tests/test_types.py`

**Interfaces:**
- Produces:
  - `BALL_RADIUS_MM: float = 21.35`
  - `Measurement(value: float | None, confidence: float, source: str)` (frozen dataclass)
  - `ClubheadPose(rotation: scipy Rotation, translation: np.ndarray)` with classmethod `from_matrix(R, t) -> ClubheadPose` (validates), and methods `body_to_world(p)`, `world_to_body(p)`, `direction_to_world(v)`.
  - `ClubMetrics(impact_offset, impact_height, face_angle, dynamic_loft, club_path, attack_angle)` — six `Measurement`.

- [ ] **Step 1: Write the failing tests**

`research/club_pose/tests/test_types.py`:
```python
import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from club_pose.types import BALL_RADIUS_MM, ClubheadPose, Measurement


def test_ball_radius_constant():
    assert BALL_RADIUS_MM == pytest.approx(21.35)


def test_measurement_holds_fields():
    m = Measurement(value=1.5, confidence=0.9, source="impact")
    assert m.value == 1.5 and m.confidence == 0.9 and m.source == "impact"


def test_identity_pose_roundtrip():
    pose = ClubheadPose(Rotation.identity(), np.zeros(3))
    p = np.array([10.0, -5.0, 3.0])
    np.testing.assert_allclose(pose.world_to_body(pose.body_to_world(p)), p, atol=1e-9)


def test_translation_then_rotation():
    pose = ClubheadPose(Rotation.from_euler("z", 90, degrees=True), np.array([100.0, 0, 0]))
    # body +X -> world +Y (90 deg about +Z), then +translation
    np.testing.assert_allclose(pose.body_to_world([1, 0, 0]), [100.0, 1.0, 0.0], atol=1e-9)


def test_direction_ignores_translation():
    pose = ClubheadPose(Rotation.identity(), np.array([100.0, 0, 0]))
    np.testing.assert_allclose(pose.direction_to_world([1, 0, 0]), [1, 0, 0], atol=1e-9)


def test_from_matrix_rejects_scaled_matrix():
    with pytest.raises(ValueError):
        ClubheadPose.from_matrix(2 * np.eye(3), np.zeros(3))


def test_from_matrix_accepts_valid_rotation():
    R = Rotation.from_euler("y", 30, degrees=True).as_matrix()
    pose = ClubheadPose.from_matrix(R, [1, 2, 3])
    np.testing.assert_allclose(pose.translation, [1, 2, 3])
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest research/club_pose/tests/test_types.py -v`
Expected: FAIL (ModuleNotFoundError: club_pose.types).

- [ ] **Step 3: Implement**

`research/club_pose/types.py`:
```python
"""Core data types for the geometry core."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
from scipy.spatial.transform import Rotation

BALL_RADIUS_MM: float = 21.35  # USGA max-conforming, matches src/openflight/ballistics.py


@dataclass(frozen=True)
class Measurement:
    """A scalar metric with a confidence (0-1) and a provenance tag."""

    value: Optional[float]
    confidence: float
    source: str


@dataclass(frozen=True)
class ClubheadPose:
    """Rigid clubhead pose. translation = head geometric center in world (mm).

    Maps body coords to world: p_world = rotation.apply(p_body) + translation.
    """

    rotation: Rotation
    translation: np.ndarray

    @classmethod
    def from_matrix(cls, matrix, translation) -> "ClubheadPose":
        """Build from a raw 3x3 rotation matrix, validating it (scipy does not)."""
        m = np.asarray(matrix, dtype=float)
        if m.shape != (3, 3):
            raise ValueError(f"rotation matrix must be 3x3, got {m.shape}")
        if not np.all(np.isfinite(m)):
            raise ValueError("rotation matrix has non-finite values")
        if not np.allclose(m.T @ m, np.eye(3), atol=1e-6):
            raise ValueError("rotation matrix is not orthonormal (RᵀR != I)")
        if not np.isclose(np.linalg.det(m), 1.0, atol=1e-6):
            raise ValueError(f"rotation matrix det != +1 (got {np.linalg.det(m):.6f})")
        return cls(Rotation.from_matrix(m), np.asarray(translation, dtype=float).reshape(3))

    def body_to_world(self, p_body) -> np.ndarray:
        return self.rotation.apply(np.asarray(p_body, dtype=float)) + self.translation

    def world_to_body(self, p_world) -> np.ndarray:
        return self.rotation.inv().apply(np.asarray(p_world, dtype=float) - self.translation)

    def direction_to_world(self, v_body) -> np.ndarray:
        return self.rotation.apply(np.asarray(v_body, dtype=float))


@dataclass(frozen=True)
class ClubMetrics:
    """The six derived golf metrics, each as a Measurement."""

    impact_offset: Measurement
    impact_height: Measurement
    face_angle: Measurement
    dynamic_loft: Measurement
    club_path: Measurement
    attack_angle: Measurement
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest research/club_pose/tests/test_types.py -v`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add research/club_pose/types.py research/club_pose/tests/test_types.py
git commit -m "feat(club_pose): add core types with validated pose transforms"
```

---

### Task 2: `frames.py` — angle decompositions + nominal camera

**Files:**
- Create: `research/club_pose/frames.py`
- Test: `research/club_pose/tests/test_frames.py`

**Interfaces:**
- Produces:
  - `horizontal_angle_deg(vec) -> float` = `-atan2(y, x)` in degrees (right/in-to-out positive).
  - `elevation_angle_deg(vec) -> float` = `atan2(z, hypot(x, y))` in degrees (up positive).
  - `CameraExtrinsic(position: np.ndarray, view_axis: np.ndarray)` and `nominal_camera(distance_mm=2000.0, height_mm=300.0) -> CameraExtrinsic` (behind ball, looking +X). `view_axis` is the depth direction for the sensitivity harness.

- [ ] **Step 1: Write the failing tests**

`research/club_pose/tests/test_frames.py`:
```python
import numpy as np
import pytest

from club_pose.frames import elevation_angle_deg, horizontal_angle_deg, nominal_camera


def test_downrange_is_zero_horizontal():
    assert horizontal_angle_deg([1, 0, 0]) == pytest.approx(0.0)


def test_rightward_is_positive_horizontal():
    # right = -Y in our frame
    assert horizontal_angle_deg([1, -1, 0]) == pytest.approx(45.0)


def test_leftward_is_negative_horizontal():
    assert horizontal_angle_deg([1, 1, 0]) == pytest.approx(-45.0)


def test_up_is_positive_elevation():
    assert elevation_angle_deg([1, 0, 1]) == pytest.approx(45.0)
    assert elevation_angle_deg([1, 0, -1]) == pytest.approx(-45.0)


def test_nominal_camera_behind_and_looking_downrange():
    cam = nominal_camera(distance_mm=2000.0, height_mm=300.0)
    assert cam.position[0] < 0  # behind the ball (negative downrange)
    np.testing.assert_allclose(cam.view_axis, [1, 0, 0], atol=1e-9)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest research/club_pose/tests/test_frames.py -v`
Expected: FAIL (ModuleNotFoundError).

- [ ] **Step 3: Implement**

`research/club_pose/frames.py`:
```python
"""Coordinate-frame helpers and metric angle decompositions (right-handed world frame)."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


def horizontal_angle_deg(vec) -> float:
    """Signed horizontal angle vs +X (downrange). Positive = right/in-to-out (-Y)."""
    v = np.asarray(vec, dtype=float)
    return float(np.degrees(-np.arctan2(v[1], v[0])))


def elevation_angle_deg(vec) -> float:
    """Signed elevation above horizontal. Positive = up (+Z)."""
    v = np.asarray(vec, dtype=float)
    return float(np.degrees(np.arctan2(v[2], np.hypot(v[0], v[1]))))


@dataclass(frozen=True)
class CameraExtrinsic:
    position: np.ndarray
    view_axis: np.ndarray  # unit, optical/depth direction in world


def nominal_camera(distance_mm: float = 2000.0, height_mm: float = 300.0) -> CameraExtrinsic:
    """Behind the ball, looking down the target line (+X). Used only for the
    sensitivity harness to define the depth axis."""
    return CameraExtrinsic(
        position=np.array([-distance_mm, 0.0, height_mm]),
        view_axis=np.array([1.0, 0.0, 0.0]),
    )
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest research/club_pose/tests/test_frames.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add research/club_pose/frames.py research/club_pose/tests/test_frames.py
git commit -m "feat(club_pose): add frame angle decompositions and nominal camera"
```

---

### Task 3: `template.py` (part A) — ClubTemplate, loft axes, override

**Files:**
- Create: `research/club_pose/template.py`
- Test: `research/club_pose/tests/test_template_axes.py`

**Interfaces:**
- Produces:
  - `ClubTemplate(category, static_loft_deg, face_width_mm, face_height_mm, bulge_radius_mm, roll_radius_mm, face_center_offset, edge_tol_mm=2.0, lie_deg=None)` (validates in `__post_init__`).
  - `ClubTemplate.face_axes() -> (u_hat, v_hat, w_hat)` body-coord unit vectors at the template's loft.
  - `ClubTemplate.face_center_normal_body() -> np.ndarray` (= `w_hat`).
  - `ClubTemplate.with_loft_override(loft_deg) -> ClubTemplate`.

- [ ] **Step 1: Write the failing tests**

`research/club_pose/tests/test_template_axes.py`:
```python
import numpy as np
import pytest

from club_pose.template import ClubTemplate


def _flat_template(loft):
    return ClubTemplate(
        category="iron",
        static_loft_deg=loft,
        face_width_mm=80.0,
        face_height_mm=55.0,
        bulge_radius_mm=None,
        roll_radius_mm=None,
        face_center_offset=np.array([20.0, 0.0, 0.0]),
    )


def test_zero_loft_normal_is_downrange():
    _, _, w = _flat_template(0.0).face_axes()
    np.testing.assert_allclose(w, [1, 0, 0], atol=1e-9)


def test_positive_loft_tilts_normal_up():
    _, _, w = _flat_template(10.0).face_axes()
    # +X rotated up by +10 deg -> (cos10, 0, sin10)
    np.testing.assert_allclose(w, [np.cos(np.radians(10)), 0, np.sin(np.radians(10))], atol=1e-9)


def test_face_axes_orthonormal_right_handed():
    u, v, w = _flat_template(15.0).face_axes()
    np.testing.assert_allclose(np.cross(u, v), w, atol=1e-9)
    for a in (u, v, w):
        assert np.linalg.norm(a) == pytest.approx(1.0)


def test_loft_override_changes_loft():
    t = _flat_template(10.0).with_loft_override(20.0)
    _, _, w = t.face_axes()
    np.testing.assert_allclose(w, [np.cos(np.radians(20)), 0, np.sin(np.radians(20))], atol=1e-9)


def test_invalid_dims_raise():
    with pytest.raises(ValueError):
        ClubTemplate("iron", 30.0, -1.0, 55.0, None, None, np.zeros(3))
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest research/club_pose/tests/test_template_axes.py -v`
Expected: FAIL (ModuleNotFoundError).

- [ ] **Step 3: Implement**

`research/club_pose/template.py`:
```python
"""Parametric clubhead template: curved face geometry in body coordinates."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from scipy.spatial.transform import Rotation

# Canonical (zero-loft) face axes in body coordinates.
_CANON_U = np.array([0.0, 1.0, 0.0])  # heel->toe = +Y (+u = toe)
_CANON_V = np.array([0.0, 0.0, 1.0])  # low->high = +Z (+v = high)
_CANON_W = np.array([1.0, 0.0, 0.0])  # outward normal (zero loft) = +X


def _loft_rotation(loft_deg: float) -> Rotation:
    """Positive loft tilts the normal from +X toward +Z (up): rotate by -loft about +u."""
    return Rotation.from_rotvec(np.radians(-loft_deg) * _CANON_U)


@dataclass(frozen=True)
class ClubTemplate:
    category: str
    static_loft_deg: float
    face_width_mm: float
    face_height_mm: float
    bulge_radius_mm: Optional[float]
    roll_radius_mm: Optional[float]
    face_center_offset: np.ndarray
    edge_tol_mm: float = 2.0
    lie_deg: Optional[float] = None  # metadata only; unused in Stage 0A math

    def __post_init__(self):
        if self.face_width_mm <= 0 or self.face_height_mm <= 0:
            raise ValueError("face dimensions must be positive")
        for name, r, half in (
            ("bulge_radius_mm", self.bulge_radius_mm, self.face_width_mm / 2),
            ("roll_radius_mm", self.roll_radius_mm, self.face_height_mm / 2),
        ):
            if r is not None and (r <= half or r <= 5 * 21.35):
                raise ValueError(f"{name}={r} outside valid range (> half-dim and > 5x ball radius)")

    def face_axes(self):
        r = _loft_rotation(self.static_loft_deg)
        return r.apply(_CANON_U), r.apply(_CANON_V), r.apply(_CANON_W)

    def face_center_normal_body(self) -> np.ndarray:
        return self.face_axes()[2]

    def with_loft_override(self, loft_deg: float) -> "ClubTemplate":
        from dataclasses import replace

        return replace(self, static_loft_deg=loft_deg)
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest research/club_pose/tests/test_template_axes.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add research/club_pose/template.py research/club_pose/tests/test_template_axes.py
git commit -m "feat(club_pose): add club template with sign-correct loft axes"
```

---

### Task 4: `template.py` (part B) — curved face surface

**Files:**
- Modify: `research/club_pose/template.py`
- Test: `research/club_pose/tests/test_template_surface.py`

**Interfaces:**
- Produces (methods on `ClubTemplate`):
  - `surface_height_face(u, v) -> float` (= `h`, convex: ≤ 0).
  - `surface_normal_face(u, v) -> np.ndarray` (unit, face-coord components).
  - `face_to_body_vec(vec_face) -> np.ndarray`, `to_face_coords(p_body) -> np.ndarray`.

- [ ] **Step 1: Write the failing tests**

`research/club_pose/tests/test_template_surface.py`:
```python
import numpy as np
import pytest

from club_pose.template import ClubTemplate


def _driver():
    return ClubTemplate("driver", 10.0, 117.0, 57.0, 254.0, 254.0, np.array([20.0, 0.0, 0.0]))


def test_flat_face_height_is_zero():
    t = ClubTemplate("iron", 0.0, 80.0, 55.0, None, None, np.zeros(3))
    assert t.surface_height_face(30.0, 20.0) == pytest.approx(0.0)


def test_convex_edges_recede_inward():
    t = _driver()
    assert t.surface_height_face(40.0, 0.0) < 0  # toe edge behind center (h<0)
    assert t.surface_height_face(0.0, 0.0) == pytest.approx(0.0)


def test_normal_tilts_toward_toe_edge():
    t = _driver()
    n = t.surface_normal_face(40.0, 0.0)
    assert n[0] > 0  # +u component (toward toe)
    assert n[2] > 0  # still mostly outward (+w)


def test_face_to_body_roundtrip_at_zero_loft():
    t = ClubTemplate("iron", 0.0, 80.0, 55.0, None, None, np.array([20.0, 0.0, 0.0]))
    # at zero loft, face axes = body axes; (u,v,w)=(2,3,5) -> body offset (5,2,3)
    np.testing.assert_allclose(t.face_to_body_vec([2, 3, 5]), [5, 2, 3], atol=1e-9)
    p_body = t.face_center_offset + np.array([5, 2, 3])
    np.testing.assert_allclose(t.to_face_coords(p_body), [2, 3, 5], atol=1e-9)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest research/club_pose/tests/test_template_surface.py -v`
Expected: FAIL (AttributeError: surface_height_face).

- [ ] **Step 3: Implement (append methods to `ClubTemplate`)**

Add to `research/club_pose/template.py` inside `ClubTemplate`:
```python
    def surface_height_face(self, u: float, v: float) -> float:
        h = 0.0
        if self.bulge_radius_mm is not None:
            h -= (u * u) / (2.0 * self.bulge_radius_mm)
        if self.roll_radius_mm is not None:
            h -= (v * v) / (2.0 * self.roll_radius_mm)
        return h

    def surface_normal_face(self, u: float, v: float) -> np.ndarray:
        nu = u / self.bulge_radius_mm if self.bulge_radius_mm is not None else 0.0
        nv = v / self.roll_radius_mm if self.roll_radius_mm is not None else 0.0
        n = np.array([nu, nv, 1.0])
        return n / np.linalg.norm(n)

    def _face_basis(self) -> np.ndarray:
        u, v, w = self.face_axes()
        return np.column_stack([u, v, w])  # columns = face axes in body coords

    def face_to_body_vec(self, vec_face) -> np.ndarray:
        return self._face_basis() @ np.asarray(vec_face, dtype=float)

    def to_face_coords(self, p_body) -> np.ndarray:
        q = np.asarray(p_body, dtype=float) - self.face_center_offset
        return self._face_basis().T @ q  # orthonormal basis: inverse = transpose
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest research/club_pose/tests/test_template_surface.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add research/club_pose/template.py research/club_pose/tests/test_template_surface.py
git commit -m "feat(club_pose): add convex curved-face surface and face/body transforms"
```

---

### Task 5: `template.py` (part C) — projection + default templates

**Files:**
- Modify: `research/club_pose/template.py`
- Test: `research/club_pose/tests/test_template_projection.py`

**Interfaces:**
- Produces:
  - `Projection(u, v, normal_body, signed_distance_mm, in_patch)` (frozen dataclass).
  - `ClubTemplate.point_to_face_uv(p_body) -> Projection`.
  - `default_template(category: str) -> ClubTemplate` for `"driver"` and `"iron"`.

- [ ] **Step 1: Write the failing tests**

`research/club_pose/tests/test_template_projection.py`:
```python
import numpy as np
import pytest

from club_pose.template import ClubTemplate, default_template
from club_pose.types import BALL_RADIUS_MM


def _driver():
    return default_template("driver")


def test_flat_projection_is_orthogonal():
    t = ClubTemplate("iron", 0.0, 80.0, 55.0, None, None, np.array([20.0, 0.0, 0.0]))
    # point 10mm out along +X (=+w at zero loft) above face point (u=12, v=-7)
    p = t.face_center_offset + np.array([10.0, 12.0, -7.0])  # body (w,u,v)->(x,y,z) at zero loft
    proj = t.point_to_face_uv(p)
    assert proj.u == pytest.approx(12.0, abs=1e-6)
    assert proj.v == pytest.approx(-7.0, abs=1e-6)
    assert proj.signed_distance_mm == pytest.approx(10.0, abs=1e-6)
    assert proj.in_patch


def test_curved_projection_recovers_known_impact():
    t = _driver()
    u0, v0 = 15.0, -8.0
    surf_body = t.face_center_offset + t.face_to_body_vec([u0, v0, t.surface_height_face(u0, v0)])
    n_body = t.face_to_body_vec(t.surface_normal_face(u0, v0))
    ball = surf_body + BALL_RADIUS_MM * n_body
    proj = t.point_to_face_uv(ball)
    assert proj.u == pytest.approx(u0, abs=1e-4)
    assert proj.v == pytest.approx(v0, abs=1e-4)
    assert proj.signed_distance_mm == pytest.approx(BALL_RADIUS_MM, abs=1e-4)
    assert proj.in_patch


def test_off_face_point_flagged():
    t = _driver()
    far = t.face_center_offset + t.face_to_body_vec([200.0, 0.0, BALL_RADIUS_MM])  # way past toe
    proj = t.point_to_face_uv(far)
    assert not proj.in_patch


def test_default_templates_exist():
    assert default_template("driver").bulge_radius_mm is not None
    assert default_template("iron").bulge_radius_mm is None
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest research/club_pose/tests/test_template_projection.py -v`
Expected: FAIL (ImportError: Projection / default_template).

- [ ] **Step 3: Implement**

Add to the top of `research/club_pose/template.py` (after imports):
```python
@dataclass(frozen=True)
class Projection:
    u: float
    v: float
    normal_body: np.ndarray
    signed_distance_mm: float
    in_patch: bool
```

Add to `ClubTemplate`:
```python
    def point_to_face_uv(self, p_body) -> Projection:
        a, b, c = self.to_face_coords(p_body)
        u, v = float(a), float(b)
        rb = self.bulge_radius_mm
        rv = self.roll_radius_mm
        for _ in range(25):  # Newton on (u-a)^2 + (v-b)^2 + (h-c)^2
            h = self.surface_height_face(u, v)
            hu = -(u / rb) if rb is not None else 0.0
            hv = -(v / rv) if rv is not None else 0.0
            huu = -(1.0 / rb) if rb is not None else 0.0
            hvv = -(1.0 / rv) if rv is not None else 0.0
            gu = (u - a) + (h - c) * hu
            gv = (v - b) + (h - c) * hv
            huu_t = 1.0 + hu * hu + (h - c) * huu
            hvv_t = 1.0 + hv * hv + (h - c) * hvv
            huv_t = hu * hv
            det = huu_t * hvv_t - huv_t * huv_t
            if abs(det) < 1e-12:
                break
            du = (hvv_t * gu - huv_t * gv) / det
            dv = (-huv_t * gu + huu_t * gv) / det
            u -= du
            v -= dv
            if abs(du) + abs(dv) < 1e-12:
                break
        surf = self.face_center_offset + self.face_to_body_vec([u, v, self.surface_height_face(u, v)])
        normal_body = self.face_to_body_vec(self.surface_normal_face(u, v))
        diff = np.asarray(p_body, dtype=float) - surf
        signed = float(np.sign(np.dot(diff, normal_body)) * np.linalg.norm(diff))
        in_patch = (abs(u) <= self.face_width_mm / 2 + self.edge_tol_mm) and (
            abs(v) <= self.face_height_mm / 2 + self.edge_tol_mm
        )
        return Projection(u=float(u), v=float(v), normal_body=normal_body, signed_distance_mm=signed, in_patch=in_patch)
```

Add module-level factory at the bottom of `research/club_pose/template.py`:
```python
def default_template(category: str) -> ClubTemplate:
    """Generic per-category templates (placeholders for the sensitivity study)."""
    if category == "driver":
        return ClubTemplate("driver", 10.5, 117.0, 57.0, 254.0, 254.0, np.array([50.0, 0.0, 0.0]))
    if category == "iron":
        return ClubTemplate("iron", 34.0, 80.0, 55.0, None, None, np.array([30.0, 0.0, 0.0]))
    raise ValueError(f"no default template for category {category!r}")
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest research/club_pose/tests/test_template_projection.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add research/club_pose/template.py research/club_pose/tests/test_template_projection.py
git commit -m "feat(club_pose): add face projection, signed distance, default templates"
```

---

### Task 6: `metrics.py` (part A) — impact location + contact state

**Files:**
- Create: `research/club_pose/metrics.py`
- Test: `research/club_pose/tests/test_metrics_impact.py`

**Interfaces:**
- Produces:
  - `CONTACT_TOL_MM: float = 3.0`
  - `impact_location(pose, template, ball_center_world) -> tuple[Measurement, Measurement, Projection, str]` returning `(offset, height, projection, contact_state)` where `contact_state in {"valid_contact", "invalid_contact"}`.

- [ ] **Step 1: Write the failing tests**

`research/club_pose/tests/test_metrics_impact.py`:
```python
import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from club_pose.metrics import impact_location
from club_pose.template import default_template
from club_pose.types import BALL_RADIUS_MM, ClubheadPose


def _ball_at(template, pose, u0, v0):
    surf = template.face_center_offset + template.face_to_body_vec(
        [u0, v0, template.surface_height_face(u0, v0)]
    )
    n = template.face_to_body_vec(template.surface_normal_face(u0, v0))
    return pose.body_to_world(surf + BALL_RADIUS_MM * n)


def test_valid_contact_recovers_offset_height():
    t = default_template("driver")
    pose = ClubheadPose(Rotation.identity(), np.zeros(3))
    ball = _ball_at(t, pose, 12.0, -6.0)
    off, hgt, proj, state = impact_location(pose, t, ball)
    assert state == "valid_contact"
    assert off.value == pytest.approx(12.0, abs=1e-3)
    assert hgt.value == pytest.approx(-6.0, abs=1e-3)
    assert off.confidence > 0.9


def test_off_face_is_invalid():
    t = default_template("driver")
    pose = ClubheadPose(Rotation.identity(), np.zeros(3))
    ball = _ball_at(t, pose, 200.0, 0.0)  # past the toe
    off, hgt, proj, state = impact_location(pose, t, ball)
    assert state == "invalid_contact"
    assert off.value is None and off.confidence == 0.0


def test_far_off_surface_is_invalid():
    t = default_template("driver")
    pose = ClubheadPose(Rotation.identity(), np.zeros(3))
    # ball centered far in front of the face (distance >> ball radius + tol)
    ball = pose.body_to_world(t.face_center_offset + np.array([100.0, 0.0, 0.0]))
    off, hgt, proj, state = impact_location(pose, t, ball)
    assert state == "invalid_contact"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest research/club_pose/tests/test_metrics_impact.py -v`
Expected: FAIL (ModuleNotFoundError).

- [ ] **Step 3: Implement**

`research/club_pose/metrics.py`:
```python
"""Derive golf metrics from a clubhead body pose + template."""
from __future__ import annotations

from typing import Optional

import numpy as np

from .frames import elevation_angle_deg, horizontal_angle_deg
from .template import ClubTemplate, Projection
from .types import BALL_RADIUS_MM, ClubheadPose, ClubMetrics, Measurement

CONTACT_TOL_MM: float = 3.0


def impact_location(pose: ClubheadPose, template: ClubTemplate, ball_center_world):
    """Return (offset, height, projection, contact_state)."""
    p_body = pose.world_to_body(ball_center_world)
    proj = template.point_to_face_uv(p_body)
    outward = proj.signed_distance_mm > 0
    on_surface = abs(proj.signed_distance_mm - BALL_RADIUS_MM) <= CONTACT_TOL_MM
    if proj.in_patch and outward and on_surface:
        conf = max(0.0, 1.0 - abs(proj.signed_distance_mm - BALL_RADIUS_MM) / CONTACT_TOL_MM)
        return (
            Measurement(proj.u, conf, "impact"),
            Measurement(proj.v, conf, "impact"),
            proj,
            "valid_contact",
        )
    return (
        Measurement(None, 0.0, "invalid"),
        Measurement(None, 0.0, "invalid"),
        proj,
        "invalid_contact",
    )
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest research/club_pose/tests/test_metrics_impact.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add research/club_pose/metrics.py research/club_pose/tests/test_metrics_impact.py
git commit -m "feat(club_pose): add impact location with contact-state validation"
```

---

### Task 7: `metrics.py` (part B) — angles + compute_metrics pipeline

**Files:**
- Modify: `research/club_pose/metrics.py`
- Test: `research/club_pose/tests/test_metrics_angles.py`

**Interfaces:**
- Consumes: `impact_location`, `horizontal_angle_deg`, `elevation_angle_deg`, `ClubheadPose`, `ClubTemplate`, `ClubMetrics`, `Measurement`.
- Produces:
  - `face_angle(pose, template, normal_body=None) -> float`
  - `dynamic_loft(pose, template, normal_body=None) -> float`
  - `club_path(pose_a, pose_b, dt) -> float`, `attack_angle(pose_a, pose_b, dt) -> float`
  - `compute_metrics(pose, template, ball_center_world=None, prev_pose=None, dt=None) -> ClubMetrics`

- [ ] **Step 1: Write the failing tests**

`research/club_pose/tests/test_metrics_angles.py`:
```python
import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from club_pose.metrics import attack_angle, club_path, compute_metrics, dynamic_loft, face_angle
from club_pose.template import ClubTemplate, default_template
from club_pose.types import BALL_RADIUS_MM, ClubheadPose


def _flat(loft):
    return ClubTemplate("iron", loft, 80.0, 55.0, None, None, np.array([20.0, 0.0, 0.0]))


def test_dynamic_loft_equals_static_at_identity():
    pose = ClubheadPose(Rotation.identity(), np.zeros(3))
    assert dynamic_loft(pose, _flat(25.0)) == pytest.approx(25.0, abs=1e-6)


def test_square_face_is_zero_face_angle():
    pose = ClubheadPose(Rotation.identity(), np.zeros(3))
    assert face_angle(pose, _flat(10.0)) == pytest.approx(0.0, abs=1e-6)


def test_open_face_is_positive():
    # rotate clubhead 3 deg about +Z (world up): face normal swings toward -Y (right) = open
    pose = ClubheadPose(Rotation.from_euler("z", -3, degrees=True), np.zeros(3))
    assert face_angle(pose, _flat(10.0)) == pytest.approx(3.0, abs=1e-6)


def test_club_path_in_to_out_positive():
    a = ClubheadPose(Rotation.identity(), np.zeros(3))
    b = ClubheadPose(Rotation.identity(), np.array([1000.0, -50.0, 0.0]))  # moving downrange + right
    assert club_path(a, b, dt=0.001) == pytest.approx(np.degrees(np.arctan2(50.0, 1000.0)), abs=1e-6)


def test_attack_angle_descending_negative():
    a = ClubheadPose(Rotation.identity(), np.zeros(3))
    b = ClubheadPose(Rotation.identity(), np.array([1000.0, 0.0, -50.0]))  # moving down
    assert attack_angle(a, b, dt=0.001) < 0


def test_compute_metrics_no_ball_uses_center_normal():
    pose = ClubheadPose(Rotation.identity(), np.zeros(3))
    m = compute_metrics(pose, _flat(20.0))
    assert m.impact_offset.value is None
    assert m.dynamic_loft.value == pytest.approx(20.0, abs=1e-6)
    assert m.dynamic_loft.source == "center"


def test_compute_metrics_dt_zero_raises():
    a = ClubheadPose(Rotation.identity(), np.zeros(3))
    with pytest.raises(ValueError):
        club_path(a, a, dt=0.0)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest research/club_pose/tests/test_metrics_angles.py -v`
Expected: FAIL (ImportError).

- [ ] **Step 3: Implement (append to `research/club_pose/metrics.py`)**

```python
def face_angle(pose: ClubheadPose, template: ClubTemplate, normal_body=None) -> float:
    if normal_body is None:
        normal_body = template.face_center_normal_body()
    return horizontal_angle_deg(pose.direction_to_world(normal_body))


def dynamic_loft(pose: ClubheadPose, template: ClubTemplate, normal_body=None) -> float:
    if normal_body is None:
        normal_body = template.face_center_normal_body()
    return elevation_angle_deg(pose.direction_to_world(normal_body))


def _head_velocity(pose_a: ClubheadPose, pose_b: ClubheadPose, dt: float) -> np.ndarray:
    if dt <= 0:
        raise ValueError("dt must be positive")
    return (pose_b.translation - pose_a.translation) / dt


def club_path(pose_a: ClubheadPose, pose_b: ClubheadPose, dt: float) -> float:
    return horizontal_angle_deg(_head_velocity(pose_a, pose_b, dt))


def attack_angle(pose_a: ClubheadPose, pose_b: ClubheadPose, dt: float) -> float:
    return elevation_angle_deg(_head_velocity(pose_a, pose_b, dt))


def compute_metrics(
    pose: ClubheadPose,
    template: ClubTemplate,
    ball_center_world=None,
    prev_pose: Optional[ClubheadPose] = None,
    dt: Optional[float] = None,
) -> ClubMetrics:
    if ball_center_world is not None:
        off, hgt, proj, state = impact_location(pose, template, ball_center_world)
        if state == "valid_contact":
            normal_body, src, conf = proj.normal_body, "impact", off.confidence
        else:
            normal_body, src, conf = template.face_center_normal_body(), "center_fallback", 0.5
    else:
        off = Measurement(None, 0.0, "no_ball")
        hgt = Measurement(None, 0.0, "no_ball")
        normal_body, src, conf = template.face_center_normal_body(), "center", 1.0

    fa = Measurement(face_angle(pose, template, normal_body), conf, src)
    dl = Measurement(dynamic_loft(pose, template, normal_body), conf, src)

    if prev_pose is not None and dt is not None:
        cp = Measurement(club_path(prev_pose, pose, dt), 1.0, "two_pose")
        aa = Measurement(attack_angle(prev_pose, pose, dt), 1.0, "two_pose")
    else:
        cp = Measurement(None, 0.0, "insufficient")
        aa = Measurement(None, 0.0, "insufficient")

    return ClubMetrics(off, hgt, fa, dl, cp, aa)
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest research/club_pose/tests/test_metrics_angles.py -v`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add research/club_pose/metrics.py research/club_pose/tests/test_metrics_angles.py
git commit -m "feat(club_pose): add angle metrics and compute_metrics pipeline"
```

---

### Task 8: `groundtruth.py` — oracle builders + end-to-end recovery

**Files:**
- Create: `research/club_pose/groundtruth.py`
- Test: `research/club_pose/tests/test_groundtruth.py`

**Interfaces:**
- Produces:
  - `ball_for_impact(pose, template, u0, v0) -> np.ndarray` (world ball center for a known face impact).
  - `pose_for_face_angle_loft(face_angle_deg, dynamic_loft_deg, head_center=(0,0,0)) -> ClubheadPose` (flat-face convention: pose that yields these on a zero-loft template).
  - `two_poses_for_velocity(vel_world, dt, start=(0,0,0)) -> tuple[ClubheadPose, ClubheadPose]`.

- [ ] **Step 1: Write the failing tests**

`research/club_pose/tests/test_groundtruth.py`:
```python
import numpy as np
import pytest

from club_pose.groundtruth import ball_for_impact, pose_for_face_angle_loft, two_poses_for_velocity
from club_pose.metrics import compute_metrics
from club_pose.template import ClubTemplate, default_template


def _flat():
    return ClubTemplate("iron", 0.0, 80.0, 55.0, None, None, np.array([20.0, 0.0, 0.0]))


def test_ball_for_impact_recovers_via_metrics():
    t = default_template("driver")
    pose = pose_for_face_angle_loft(0.0, t.static_loft_deg)
    ball = ball_for_impact(pose, t, 14.0, -9.0)
    m = compute_metrics(pose, t, ball_center_world=ball)
    assert m.impact_offset.value == pytest.approx(14.0, abs=1e-3)
    assert m.impact_height.value == pytest.approx(-9.0, abs=1e-3)


def test_pose_for_face_angle_loft_roundtrips():
    t = _flat()
    pose = pose_for_face_angle_loft(4.0, 12.0)
    m = compute_metrics(pose, t)
    assert m.face_angle.value == pytest.approx(4.0, abs=1e-4)
    assert m.dynamic_loft.value == pytest.approx(12.0, abs=1e-4)


def test_two_poses_recover_velocity_angles():
    a, b = two_poses_for_velocity([1000.0, -50.0, 30.0], dt=0.001)
    t = _flat()
    m = compute_metrics(b, t, prev_pose=a, dt=0.001)
    assert m.club_path.value == pytest.approx(np.degrees(np.arctan2(50.0, 1000.0)), abs=1e-4)
    assert m.attack_angle.value == pytest.approx(np.degrees(np.arctan2(30.0, np.hypot(1000.0, 50.0))), abs=1e-4)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest research/club_pose/tests/test_groundtruth.py -v`
Expected: FAIL (ModuleNotFoundError).

- [ ] **Step 3: Implement**

`research/club_pose/groundtruth.py`:
```python
"""Analytic ground-truth builders used as the test oracle."""
from __future__ import annotations

import numpy as np
from scipy.spatial.transform import Rotation

from .template import ClubTemplate
from .types import BALL_RADIUS_MM, ClubheadPose


def ball_for_impact(pose: ClubheadPose, template: ClubTemplate, u0: float, v0: float) -> np.ndarray:
    surf = template.face_center_offset + template.face_to_body_vec(
        [u0, v0, template.surface_height_face(u0, v0)]
    )
    n = template.face_to_body_vec(template.surface_normal_face(u0, v0))
    return pose.body_to_world(surf + BALL_RADIUS_MM * n)


def pose_for_face_angle_loft(
    face_angle_deg: float, dynamic_loft_deg: float, head_center=(0.0, 0.0, 0.0)
) -> ClubheadPose:
    """Pose that yields the given face angle + dynamic loft on a ZERO-loft flat template.

    The template's zero-loft normal is +X. Apply elevation (loft, +Z up) then azimuth
    (face angle, right/-Y positive) in the WORLD frame so the metric decompositions invert it.
    """
    # elevation: tilt +X up by dynamic_loft about +Y is -loft (see R_loft); in world we want
    # normal -> (cos L, 0, sin L): rotate about +Y by -L.
    r_loft = Rotation.from_rotvec(np.radians(-dynamic_loft_deg) * np.array([0.0, 1.0, 0.0]))
    # azimuth: open (right/-Y) positive -> rotate about +Z by -face_angle.
    r_face = Rotation.from_rotvec(np.radians(-face_angle_deg) * np.array([0.0, 0.0, 1.0]))
    return ClubheadPose(r_face * r_loft, np.asarray(head_center, dtype=float))


def two_poses_for_velocity(vel_world, dt: float, start=(0.0, 0.0, 0.0)):
    a = ClubheadPose(Rotation.identity(), np.asarray(start, dtype=float))
    b = ClubheadPose(Rotation.identity(), np.asarray(start, dtype=float) + np.asarray(vel_world, dtype=float) * dt)
    return a, b
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest research/club_pose/tests/test_groundtruth.py -v`
Expected: PASS (3 passed). If `test_pose_for_face_angle_loft_roundtrips` fails on the combined rotation order, the fix is rotation composition order (`r_face * r_loft` applies loft first); verify face angle/loft signs against §4.4 and adjust the multiplication order, not the metric code.

- [ ] **Step 5: Commit**

```bash
git add research/club_pose/groundtruth.py research/club_pose/tests/test_groundtruth.py
git commit -m "feat(club_pose): add analytic ground-truth oracle builders"
```

---

### Task 9: `sensitivity.py` — error budget + README + full suite

**Files:**
- Create: `research/club_pose/sensitivity.py`
- Modify: `research/club_pose/README.md`
- Test: `research/club_pose/tests/test_sensitivity.py`

**Interfaces:**
- Produces:
  - `loft_error_to_loft_deg(template, loft_errors_deg) -> list[tuple[float, float]]` — input template-loft error vs resulting dynamic-loft error (expected ≈1:1).
  - `depth_error_to_impact_mm(template, u0, v0, depth_errors_mm) -> list[tuple[float, float]]` — head translation error along the camera depth axis (+X) vs impact (u) error.
  - `rotation_error_to_face_deg(template, rot_errors_deg) -> list[tuple[float, float]]` — body yaw error vs face-angle error.
  - `error_budget(template) -> dict` — summarizes per-tier (single-camera ±3–5°, stereo ±2°) the pose accuracy implied by the above slopes.

- [ ] **Step 1: Write the failing tests**

`research/club_pose/tests/test_sensitivity.py`:
```python
import numpy as np
import pytest

from club_pose.sensitivity import (
    depth_error_to_impact_mm,
    error_budget,
    loft_error_to_loft_deg,
    rotation_error_to_face_deg,
)
from club_pose.template import default_template


def test_zero_perturbation_zero_error():
    t = default_template("driver")
    assert loft_error_to_loft_deg(t, [0.0])[0][1] == pytest.approx(0.0, abs=1e-9)
    assert rotation_error_to_face_deg(t, [0.0])[0][1] == pytest.approx(0.0, abs=1e-9)


def test_template_loft_error_is_one_to_one():
    t = default_template("driver")
    out = dict(loft_error_to_loft_deg(t, [1.0, 2.0, 3.0]))
    assert out[2.0] == pytest.approx(2.0, abs=1e-6)


def test_rotation_error_monotonic():
    t = default_template("driver")
    out = rotation_error_to_face_deg(t, [0.0, 1.0, 2.0])
    errs = [e for _, e in out]
    assert errs[0] < errs[1] < errs[2]


def test_error_budget_has_two_tiers():
    b = error_budget(default_template("driver"))
    assert "single_camera" in b and "stereo" in b
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest research/club_pose/tests/test_sensitivity.py -v`
Expected: FAIL (ModuleNotFoundError).

- [ ] **Step 3: Implement**

`research/club_pose/sensitivity.py`:
```python
"""Sensitivity sweeps: how pose/template error propagates to metric error.

Produces the error budget that decides single-camera vs stereo.
"""
from __future__ import annotations

import numpy as np
from scipy.spatial.transform import Rotation

from .frames import nominal_camera
from .groundtruth import ball_for_impact, pose_for_face_angle_loft
from .metrics import compute_metrics, dynamic_loft, face_angle, impact_location
from .template import ClubTemplate
from .types import ClubheadPose


def loft_error_to_loft_deg(template: ClubTemplate, loft_errors_deg):
    """(template loft error, resulting dynamic-loft error) — expect ~1:1."""
    pose = ClubheadPose(Rotation.identity(), np.zeros(3))
    truth = dynamic_loft(pose, template)
    out = []
    for e in loft_errors_deg:
        perturbed = template.with_loft_override(template.static_loft_deg + e)
        out.append((e, dynamic_loft(pose, perturbed) - truth))
    return out


def depth_error_to_impact_mm(template: ClubTemplate, u0: float, v0: float, depth_errors_mm):
    """(depth/translation error along camera axis, resulting impact-offset error mm)."""
    cam = nominal_camera()
    pose = pose_for_face_angle_loft(0.0, template.static_loft_deg)
    ball = ball_for_impact(pose, template, u0, v0)
    out = []
    for e in depth_errors_mm:
        moved = ClubheadPose(pose.rotation, pose.translation + e * cam.view_axis)
        off, _, _, state = impact_location(moved, template, ball)
        err = abs(off.value - u0) if off.value is not None else float("nan")
        out.append((e, err))
    return out


def rotation_error_to_face_deg(template: ClubTemplate, rot_errors_deg):
    """(body yaw error about +Z, resulting face-angle error deg)."""
    base = pose_for_face_angle_loft(0.0, template.static_loft_deg)
    truth = face_angle(base, template)
    out = []
    for e in rot_errors_deg:
        perturbed = ClubheadPose(
            Rotation.from_rotvec(np.radians(-e) * np.array([0.0, 0.0, 1.0])) * base.rotation,
            base.translation,
        )
        out.append((e, abs(face_angle(perturbed, template) - truth)))
    return out


def error_budget(template: ClubTemplate) -> dict:
    """Pose accuracy implied for each target tier, from the local slopes."""
    rot = rotation_error_to_face_deg(template, [1.0])[0][1]  # deg face per deg yaw (~1:1)
    slope = rot if rot > 1e-9 else 1.0
    return {
        "single_camera": {
            "face_loft_target_deg": "3-5",
            "max_body_rotation_deg": round(3.0 / slope, 2),
        },
        "stereo": {
            "face_loft_target_deg": "2",
            "max_body_rotation_deg": round(2.0 / slope, 2),
        },
    }
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest research/club_pose/tests/test_sensitivity.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Run the full suite + update README**

Run: `uv run pytest research/club_pose/tests/ -v`
Expected: PASS (all tasks' tests green).

Append to `research/club_pose/README.md`:
```markdown

## Modules
- `types` — Measurement, ClubheadPose (validated), ClubMetrics
- `frames` — angle decompositions (right/up positive), nominal camera
- `template` — parametric curved-face template, loft override, projection
- `metrics` — impact location, face angle, dynamic loft, club path, attack angle, compute_metrics
- `groundtruth` — analytic oracle builders
- `sensitivity` — error-budget sweeps (single-camera vs stereo)
```

- [ ] **Step 6: Commit**

```bash
git add research/club_pose
git commit -m "feat(club_pose): add sensitivity error-budget harness and docs"
```

---

## Self-Review (completed by plan author)

**Spec coverage:** types (§5.1) → Task 1; frames + §4.4 formulas (§5.2) → Task 2; template loft/axes (§4.3) → Task 3; curved surface (§5.3) → Task 4; projection + patch + defaults (§5.3) → Task 5; impact + contact states (§5.4, §9) → Task 6; angles + pipeline (§4.4, §5.4) → Task 7; ground-truth (§5.5) → Task 8; sensitivity + two-tier budget (§5.6, §8) → Task 9. Validation strategy (§7) is realized as the per-task tests. Rotation-matrix validation (§9) → Task 1. No spec section is unaddressed.

**Placeholder scan:** every code/test/command step contains concrete content; no TBD/TODO/"similar to".

**Type consistency:** names are consistent across tasks — `ClubheadPose`, `ClubTemplate`, `Projection`, `Measurement`, `ClubMetrics`, `face_center_normal_body()`, `face_to_body_vec()`, `surface_height_face()`, `surface_normal_face()`, `point_to_face_uv()`, `impact_location()`, `face_angle()`, `dynamic_loft()`, `club_path()`, `attack_angle()`, `compute_metrics()`, `ball_for_impact()`, `pose_for_face_angle_loft()`, `two_poses_for_velocity()`. `impact_location` returns the 4-tuple consistently used by Task 7's pipeline.
