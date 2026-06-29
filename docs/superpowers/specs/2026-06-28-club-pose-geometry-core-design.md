# Stage 0 — Clubhead Pose → Golf Metrics Geometry Core (design spec)

- **Date:** 2026-06-28
- **Status:** Draft for review
- **Branch:** `feat/camera-club-data`
- **Related:** [v2 research guide](../../Personal%20Research/markerless-club-data-guide-v2-research-corrected.md) (esp. §2.3, §4, §4.3, §4.4, §6A, §6B)

## 1. Purpose & context

OpenFlight is adding a markerless, behind-the-ball **camera** subsystem to measure club data the radar fundamentally cannot: **impact location, face angle, dynamic loft** (plus corroborating club path / attack angle, and camera-based spin later). The hard, novel parts are the multi-view geometry and the synchronized capture hardware.

This spec covers **Stage 0 only: the pure geometry/metrics core**, with **no rendering and no computer vision**. It is the function that, given a clubhead **6-DOF pose** and a **club template**, computes the golf metrics — and the test harness that proves that math against analytic ground truth.

**Why this first:** everything downstream (segmentation, pose estimation, stereo) is only worth building if the pose→metrics math is correct and we understand its error sensitivity. This core is pure Python (numpy/scipy, already repo deps), runs anywhere (incl. the Windows box), and directly de-risks the geometry — the maintainer's newer area. Its **sensitivity analysis produces the quantitative error budget** that decides single-camera vs stereo and the required calibration/resolution — i.e., we size hardware to a number, not a guess.

## 2. Scope

**In scope:**
- Coordinate-frame definitions and transforms (world / camera / head-local).
- A parametric, per-club-category clubhead **template** with a **curved face** (bulge & roll) and a **loft override**.
- **Metric derivations:** impact location (continuous mm), face angle, dynamic loft, club path, attack angle.
- A **ground-truth generator** and a **sensitivity harness**.
- A TDD test suite validating all of the above against analytic truth.

**Out of scope (later stages):** Blender/BlenderProc rendering, segmentation, 6-DOF pose *estimation*, camera-intrinsic/extrinsic calibration code, stereo implementation, real images, OpenFlight server/UI/`Shot` integration. Those consume this core's outputs later.

**Non-goal:** matching any vendor's *undisclosed* sign conventions or certified accuracy numbers. We define and document our own conventions.

## 3. Location, dependencies, standards

- **Location (standalone sandbox, per maintainer decision):** `research/club_pose/` at repo root, outside `src/openflight/` and its production gates. Layout:
  ```
  research/club_pose/
    __init__.py
    types.py          # dataclasses
    frames.py         # frame conventions + transforms
    template.py       # parametric clubhead template (curved face)
    metrics.py        # pose + template -> metrics
    groundtruth.py    # analytic truth generator (test oracle)
    sensitivity.py    # error-budget experiment
    README.md
    tests/            # pytest, TDD
  ```
- **Promotion path:** once validated, the production math migrates to `src/openflight/club_pose/` as a scoped, tested PR (separate clean branch).
- **Dependencies:** `numpy`, `scipy` (both already base deps), `pytest` (dev). `matplotlib` (already an `analysis` extra) optional for sensitivity plots. **No new dependencies.**
- **Standards:** built test-first; runs under `uv run pytest research/club_pose/tests/`; keep it ruff/pylint-clean so promotion is friction-free. Heavily commented as a geometry learning vehicle, but rotations rest on `scipy.spatial.transform.Rotation` (no hand-rolled SO(3)).

## 4. Coordinate frames & sign conventions (explicit, to remove ambiguity)

**World / target-line frame** — right-handed, origin at the ball center at address:
- **+X** = horizontal, down the target line (toward the target / "downrange").
- **+Y** = horizontal, to the player's **left** (so "rightward" = −Y). Chosen so {X, Y, Z} is right-handed.
- **+Z** = vertical up.

**Camera frame** — a *nominal* extrinsic used only by the sensitivity harness to define a meaningful "depth" (camera-axis) direction: camera placed behind the ball at a configurable position, looking down the target line (+X). Depth error = head-translation error along this viewing axis.

**Head-local frame** — attached to the clubhead, related to world by pose `(R, t)` (`p_world = R · p_head + t`):
- origin at the **geometric center of the clubface**;
- **u** axis = heel→toe; **v** axis = low→high; **w** = face normal (outward) = u × v.

**Metric sign conventions (ours, documented; toggleable later):**
| Metric | Zero | Positive direction |
|---|---|---|
| Face angle | normal points down target line | **open** (right of target, −Y component) |
| Dynamic loft | normal horizontal | **up** (elevation) |
| Attack angle | level club motion | **up** (ascending) |
| Club path | motion down target line | **in-to-out** (rightward, −Y) |
| Impact Offset | face center | **toe** (+u) |
| Impact Height | face center | **high** (+v) |

## 5. Components

Five small, independently testable units. Each: one purpose, a typed interface, isolated tests.

### 5.1 `types.py`
Dataclasses (frozen where sensible):
- `ClubheadPose`: `rotation` (scipy `Rotation`), `translation` (np 3-vec, mm), `frame` tag. Helper to transform points head↔world.
- `ClubTemplate`: category (driver/fairway/hybrid/iron/wedge), `static_loft_deg`, `lie_deg`, `face_width_mm`, `face_height_mm`, `bulge_radius_mm` (None/∞ = planar), `roll_radius_mm` (None/∞ = planar), and derived face geometry (center, u/v/w in head-local). A `with_loft_override(loft_deg)` returning a new template with the face plane re-tilted.
- `ClubMetrics`: `impact_offset_mm`, `impact_height_mm`, `face_angle_deg`, `dynamic_loft_deg`, `club_path_deg`, `attack_angle_deg` — each as a small `Measurement(value, confidence, source)` so the §4.3 D-plane fusion can plug in later without signature changes.

### 5.2 `frames.py`
Pure functions over `scipy` rotations: build the world frame, build a nominal camera extrinsic, convert points/directions between frames, and decompose a world-space direction into (azimuth vs +X, elevation) used by the angle metrics. No state.

### 5.3 `template.py`
Given category + parameters, produce the head-local face geometry:
- face center, u/v/w axes, face dimensions;
- a **curved-face surface model**: face point as a function of (u, v) using bulge (horizontal) and roll (vertical) radii — a flat plane when radii are ∞;
- `point_to_face_uv(p_head)`: map a 3-D point near the face (in head-local) to its **(offset_u, height_v)** on the curved surface (projection along the local surface normal / closest-surface-point), plus the surface normal there;
- `with_loft_override(loft_deg)`.
Generic per-category defaults (realistic lofts/lies/dims/bulge-roll) live here; an override path lets a caller pass exact specs.

### 5.4 `metrics.py`
Pure functions:
- `face_angle(pose, template)` and `dynamic_loft(pose, template)`: rotate the face normal `w` to world, decompose per §4 conventions. (For impact-aware loft/face, evaluate the surface normal **at the impact point**, not just the center — matches Trackman's "at the impact location.")
- `impact_location(pose, template, ball_center_world)`: transform ball center to head-local, call `point_to_face_uv` → `impact_offset_mm`, `impact_height_mm` from face center.
- `club_path(pose_a, pose_b, dt)` and `attack_angle(...)`: finite-difference the head **geometric-center** position across two poses → velocity vector → horizontal/vertical angles.
Each returns a `Measurement` (value + confidence + source="camera-geom").

### 5.5 `groundtruth.py`
The test oracle: construct a pose by *specifying* a desired face-normal orientation / impact point / velocity, so tests can assert the metric recovers exactly what was set. Includes builders like "pose with known face angle θ and dynamic loft φ" and "ball placed at known (offset,height) on the face."

### 5.6 `sensitivity.py`
The headline experiment. Sweep controlled perturbations and record output error:
- **Impact location vs head-translation error** — separately along the camera/depth axis vs in-plane (uses the nominal camera extrinsic). Produces mm-error-per-mm-depth-error.
- **Impact / face / loft vs head-rotation error** — degrees in → mm/deg out (expected ≈0.7 mm per 1° at a ~40 mm lever).
- **Face angle / dynamic loft vs template-loft error** — verifies the ~1:1 propagation that motivates the loft override.
- **Impact vs face-curvature mismodeling** — flat-vs-curved face error at the edges.
Output: tables (and optional plots) + a one-page **error budget**: "to hit ±X mm impact / ±Y° face, pose must be accurate to …" → the input to the single-vs-stereo and resolution/calibration decisions.

## 6. Data flow

```
ClubTemplate (category + params [+ loft override])
        │
        ▼
ClubheadPose (R,t)  +  ball_center_world  ──►  metrics.py  ──►  ClubMetrics
        │                                          ▲
        └── groundtruth.py builds known (pose, ball) ┘  (tests assert recovered == set)

sensitivity.py: for each perturbation δ → metrics(perturbed) vs metrics(truth) → error tables → error budget
```

## 7. Validation strategy (TDD)

Write the failing test first for each unit, then implement.
- **Round-trip correctness (perfect inputs):** set a known pose/template/ball → assert each metric matches the analytic truth to **≤0.01° / ≤0.01 mm** (numerical-precision gate proving the math is right).
- **Degenerate / edge cases:** square face → 0° face angle; pure-loft pose → dynamic loft = static loft; ball at exact center → (0, 0); flat face (∞ radii) and curved face agree at the center; toe/heel and high/low signs correct.
- **Invariances:** translating the whole scene doesn't change angles; rotating world frame consistently transforms results.
- **Sensitivity outputs exist and are monotonic/sane** (e.g., zero perturbation → zero error).
- All tests run green under `uv run pytest research/club_pose/tests/`.

## 8. Success criteria (Stage-0 gate)

1. All metric math validated to numerical precision against analytic ground truth (above).
2. Sensitivity error budget produced, quantifying: mm-impact-error per mm depth/translation error; mm & ° error per ° rotation error; face/loft error per ° template-loft error; flat-vs-curved face error.
3. From that budget, a clear, evidence-based statement of the pose accuracy required for ±3–5 mm impact and ±2° face/loft — feeding the single-vs-stereo and resolution/calibration decisions before any hardware spend.

## 9. Error handling

- Invalid template params (non-positive dimensions, nonsensical radii) → raise `ValueError`.
- Non-orthonormal rotation input → rely on `scipy.Rotation` validation; normalize where appropriate.
- Ball with no valid projection onto the face (behind the face / off the face by a wide margin) → return `impact_location` with `confidence=0` and value `None`, not an exception.
- Two-pose metrics with `dt<=0` or identical poses → `ValueError` / zero-confidence.

## 10. Risks & open questions

- **Curved-face contact model:** "closest surface point" vs "ray along incoming ball path" can differ by sub-mm near the edges; Stage 0 will implement closest-surface-point and note the assumption (real contact uses the ball's approach direction — revisit when velocity is available).
- **Head-local origin choice:** face geometric center is the metric origin; the *head* reference point used for path/attack velocity may differ slightly (geometric center of the head vs face). Document and keep consistent with Trackman's "geometric center" definitions.
- **Template realism:** generic per-category defaults are placeholders for the sensitivity study; real per-club specs come via the override. The study itself will show how much template fidelity actually matters.
