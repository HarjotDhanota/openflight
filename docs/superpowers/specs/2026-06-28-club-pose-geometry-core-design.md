# Stage 0A — Clubhead Pose → Golf Metrics Geometry Core (design spec)

- **Date:** 2026-06-28 (rev. 2 — incorporates review findings)
- **Status:** Draft for review
- **Branch:** `feat/camera-club-data`
- **Related:** [v2 research guide](../../Personal%20Research/markerless-club-data-guide-v2-research-corrected.md) (esp. §2.3, §4, §4.3, §4.4, §6A, §6B). This is **Stage 0A** of the roadmap's Stage 0; **Stage 0B** (BlenderProc renderer + pose estimators) is a separate later spec.

## 1. Purpose & context

OpenFlight is adding a markerless, behind-the-ball **camera** subsystem to measure club data the radar fundamentally cannot: **impact location, face angle, dynamic loft** (plus corroborating club path / attack angle, and camera-based spin later). The hard, novel parts are the multi-view geometry and the synchronized capture hardware.

This spec covers **Stage 0A only: the pure geometry/metrics core** — no rendering, no computer vision, no pose *estimation*. It is the function that, given a clubhead **6-DOF body pose** and a **club template**, computes the golf metrics — and the test harness that proves that math against analytic ground truth. (The roadmap's "Stage 0" also includes a renderer + pose estimators; those are **Stage 0B**, separate.)

**Why this first:** everything downstream is only worth building if the pose→metrics math is correct and we understand its error sensitivity. This core is pure Python (numpy/scipy, already repo deps), runs anywhere (incl. the Windows box), and de-risks the geometry. Its **sensitivity analysis produces the quantitative error budget** that decides single-camera vs stereo and the required calibration/resolution — we size hardware to a number, not a guess.

## 2. Scope

**In scope:** coordinate frames + transforms; a parametric, per-club-category **template** that embeds a **curved face** (bulge & roll) in body coordinates with a **loft override**; **metric derivations** (impact location in mm, face angle, dynamic loft, club path, attack angle); a **ground-truth generator** and a **sensitivity harness**; a TDD suite validating all of it against analytic truth.

**Out of scope (later stages):** Blender/BlenderProc rendering, segmentation, 6-DOF pose *estimation*, camera intrinsic/extrinsic calibration code, stereo implementation, real images, OpenFlight server/UI/`Shot` integration.

**Non-goal:** matching any vendor's *undisclosed* sign conventions or certified accuracy numbers. We define and document our own.

## 3. Location, dependencies, standards

- **Location (standalone sandbox):** `research/club_pose/` at repo root, outside `src/openflight/`. Layout:
  ```
  research/club_pose/
    __init__.py
    types.py          # dataclasses
    frames.py         # frame conventions + transforms
    template.py       # parametric clubhead template (curved face, in body coords)
    metrics.py        # body pose + template -> metrics
    groundtruth.py    # analytic truth generator (test oracle)
    sensitivity.py    # error-budget experiment
    README.md
    tests/            # pytest, TDD
  ```
- **Promotion path:** once validated, the production math migrates to `src/openflight/club_pose/` as a scoped, tested PR (separate clean branch).
- **Dependencies:** `numpy`, `scipy` (both already base deps), `pytest` (dev). `matplotlib` (already an `analysis` extra) optional for sensitivity plots. **No new dependencies.**
- **Standards & test gate:** built test-first; **gate = `uv run pytest research/club_pose/tests/`**. *Environment note:* `uv` must be on PATH — open a fresh terminal after the winget install (it updates the persistent PATH but not already-open shells), or call the winget-installed `uv.exe` by full path. Keep the code ruff/pylint-clean so promotion is friction-free. Rotations rest on `scipy.spatial.transform.Rotation` (no hand-rolled SO(3)).

## 4. Coordinate frames & sign conventions (explicit)

**World / target-line frame** — right-handed, origin at the ball center at address:
- **+X** = horizontal, down the target line (toward target / "downrange").
- **+Y** = horizontal, to the player's **left** (so "rightward" = −Y). Chosen so {X, Y, Z} is right-handed.
- **+Z** = vertical up.

> **Handedness vs OpenFlight ballistics (review note):** `ballistics.py` uses a **left-handed** frame (X-fwd, **Y-right**, Z-up). We deliberately use a **right-handed** frame here because `scipy.Rotation` is strictly right-handed and doing 6-DOF pose math in a left-handed frame invites sign bugs. This is hidden behind the metric functions: all **output scalars** below use positive = right / in-to-out / up / toe / high, which **matches ballistics' lateral output sign**, so promotion is a documented mapping (flip the internal lateral world-axis if ever exposing a raw vector), not a behavioral adapter.

**Body frame** — rigidly attached to the **clubhead as a whole** (the rigid body we can observe markerless), related to world by pose `(R, t)`: `p_world = R · p_body + t`.
- **Origin = clubhead geometric center** (this is the point whose velocity defines club path / attack angle — matching Trackman's "measured at the geometric center").
- The **face is NOT a body axis.** The face plane/normal lives in the *template*, expressed in body coordinates (below). This is the key correction: we recover the body pose from observable head geometry, then the template tells us where the *unseen* face sits.

**Face geometry (defined by the template, in body coordinates):**
- `face_center_offset` — vector from body origin (head center) to the face geometric center.
- **w** = outward face normal, computed from `static_loft_deg` + `lie_deg` relative to the body frame (zero loft → w along body +X; loft tilts toward +Z; lie rotates about body X). The **loft override** re-tilts `w` (and u/v) — now meaningful because w is template-defined, not the pose's w.
- **u** = heel→toe, **v** = low→high, spanning the face, with `w = u × v`.
- bulge/roll radii define the curved face surface.

**Metric sign conventions (ours, documented):**
| Metric | Zero | Positive |
|---|---|---|
| Face angle | normal down target line | **open** (right of target) |
| Dynamic loft | normal horizontal | **up** |
| Attack angle | level club motion | **up** (ascending) |
| Club path | motion down target line | **in-to-out** (rightward) |
| Impact Offset | face center | **toe** (+u) |
| Impact Height | face center | **high** (+v) |

## 5. Components

Five small, independently testable units.

### 5.1 `types.py`
- `ClubheadPose`: `rotation` (scipy `Rotation`), `translation` (np 3-vec, mm) = **head geometric center in world**, `frame` tag. Helper to transform body↔world. Constructed preferentially from a quaternion/`Rotation`; a raw-matrix constructor **explicitly validates** finite values, 3×3 shape, `RᵀR ≈ I`, `det(R) ≈ +1` before wrapping (scipy silently orthonormalizes, so we cannot lean on it — review finding 6).
- `ClubTemplate`: category (driver/fairway/hybrid/iron/wedge); `static_loft_deg`, `lie_deg`, `face_width_mm`, `face_height_mm`, `bulge_radius_mm` (None = planar), `roll_radius_mm` (None = planar), `face_center_offset` (body coords). Derived (in **body coords**): face center, u/v/w axes, curved-face surface. `with_loft_override(loft_deg)` → new template with the face re-tilted in the body frame.
- `Measurement(value, confidence, source)` and `ClubMetrics` (impact_offset_mm, impact_height_mm, face_angle_deg, dynamic_loft_deg, club_path_deg, attack_angle_deg — each a `Measurement`, so §4.3 D-plane fusion plugs in later without signature churn).

### 5.2 `frames.py`
Pure functions over `scipy` rotations: build the world frame; build a nominal camera extrinsic (behind ball, looking +X) used only by the sensitivity harness to define the depth axis; transform points/directions body↔world; decompose a world direction into (azimuth vs +X, elevation) for the angle metrics. No state.

### 5.3 `template.py`
Given category + params, produce the **body-coordinate** face geometry: face center, u/v/w axes (w from loft+lie), face dimensions, and the **curved-face surface** (bulge horizontal, roll vertical; flat when radii are None). Key function `point_to_face_uv(p_body)` → maps a 3-D point near the face to its `(offset_u, height_v)` on the curved surface (closest-surface-point), returning also the **surface normal at that point** and the **signed distance** to the surface. `with_loft_override(loft_deg)`. Generic per-category defaults live here; exact specs can be passed to override.

### 5.4 `metrics.py`
A single pipeline plus thin wrappers (review findings 2 & 3):
- **`compute_metrics(pose, template, ball_center_world, prev_pose=None, dt=None)`** → `ClubMetrics`. Order: (1) `impact_location` first; (2) `face_angle`/`dynamic_loft` evaluated using the **surface normal at the impact point** (falls back to face-center normal if no ball given); (3) `club_path`/`attack_angle` if `prev_pose`+`dt` given.
- `impact_location(pose, template, ball_center_world)`: transform ball center to body coords, `point_to_face_uv` → `impact_offset_mm`, `impact_height_mm` from face center, plus the signed distance for contact validation (§9).
- `face_angle(pose, template, impact_point=None)` / `dynamic_loft(pose, template, impact_point=None)`: rotate the (impact-point or center) surface normal to world, decompose per §4. Loft override changes the result (the fix for finding 1).
- `club_path(pose_a, pose_b, dt)` / `attack_angle(...)`: finite-difference the **head geometric center** = `pose.translation` directly (the pose origin) → velocity → horizontal/vertical angles. No extra offset needed (resolved by the body-origin choice).

### 5.5 `groundtruth.py`
Test oracle: build a `(pose, template, ball)` by *specifying* a desired face-angle/dynamic-loft, a known impact `(offset, height)`, or a known head-center velocity, so tests assert the metric recovers exactly what was set.

### 5.6 `sensitivity.py`
The headline experiment. Sweep perturbations, record output error:
- **Impact location vs head-translation error** — separately along the camera/depth axis vs in-plane (uses the nominal camera extrinsic). mm-error per mm depth error.
- **Impact / face / loft vs body-rotation error** — deg in → mm & deg out (expected ≈0.7 mm per 1° at a ~40 mm lever).
- **Face/loft vs template-loft error** — verifies the ~1:1 propagation that motivates the override.
- **Impact vs face-curvature mismodeling** — flat-vs-curved edge error.
Output: tables (+ optional plots) and a one-page **error budget**: "to hit ±X mm impact / ±Y° face, body pose must be accurate to …" → the single-vs-stereo + resolution/calibration input.

## 6. Data flow

```
ClubTemplate (category + params [+ loft override])  → face geometry in BODY coords
        │
ClubheadPose (R,t = head center)  +  ball_center_world  ──►  compute_metrics  ──►  ClubMetrics
        │                                                        ▲
        └── groundtruth.py builds known (pose, template, ball) ──┘  (assert recovered == set)

sensitivity.py: perturb → compute_metrics(perturbed) vs truth → error tables → error budget
```

## 7. Validation strategy (TDD)

Failing test first per unit, then implement.
- **Round-trip correctness (perfect inputs):** known body pose/template/ball → each metric matches analytic truth to **≤0.01° / ≤0.01 mm**.
- **Loft-override is live:** changing the template loft override by Δ° changes dynamic loft by ≈Δ° (this test would have caught finding 1).
- **Impact-aware face/loft:** on a curved face, face angle/loft at an off-center impact differ from center per the surface normal.
- **Degenerate/edge cases:** square face → 0° face angle; pure-loft pose → dynamic loft = static loft; ball at exact center → (0,0); flat vs curved agree at center; sign checks (toe/heel, high/low, open/closed, in/out, up/down).
- **Contact validation:** a ball one ball-radius off the surface → contact OK; far off → confidence 0 / None.
- **Invariances:** global translation doesn't change angles; consistent world rotation transforms results.
- **Sensitivity sanity:** zero perturbation → zero error; monotone trends.
- All green under `uv run pytest research/club_pose/tests/`.

## 8. Success criteria (Stage-0A gate)

1. All metric math validated to numerical precision against analytic ground truth.
2. Loft-override and impact-aware face/loft proven by test (the two High findings closed).
3. Sensitivity error budget produced: mm-impact-error per mm depth/translation; mm & ° per ° rotation; face/loft per ° template-loft; flat-vs-curved edge error.
4. From that budget, an evidence-based statement of the body-pose accuracy required for ±3–5 mm impact and ±2° face/loft — feeding the single-vs-stereo and resolution/calibration decisions.

## 9. Error handling

- Invalid template params (non-positive dims, nonsensical radii) → `ValueError`.
- **Raw rotation-matrix input → explicit validation** (finite, 3×3, `RᵀR ≈ I` within tol, `det ≈ +1`); do **not** rely on `scipy` to reject bad matrices (it orthonormalizes silently). Prefer quaternion/`Rotation` construction.
- **Impact contact validation (review finding 5):** `impact_location` returns the signed distance from the ball center to the face surface. Contact is plausible only if the ball is on the **outward (front) side** and `|signed_distance − BALL_RADIUS_MM| ≤ tolerance` (BALL_RADIUS_MM = 21.35, from `ballistics.py`). Outside tolerance → degrade confidence; far off-face or behind the face → `value=None, confidence=0` (not an exception).
- Two-pose metrics with `dt ≤ 0` or identical poses → `ValueError` / zero-confidence.

## 10. Open questions / assumptions

- **Curved-face contact model:** Stage 0A uses **closest-surface-point** for the impact UV; real contact is along the ball's **approach direction** (available once velocity exists). Documented assumption; revisit when the two-pose velocity is wired in.
- **Body-frame canonical orientation:** body origin = head geometric center is fixed; the canonical zero-pose axis alignment (how a real estimator's recovered frame maps to this body frame) is a Stage 0B concern — Stage 0A only requires an internally consistent definition.
- (Resolved by rev. 2: body-vs-face frame tangle [finding 1], impact-aware signatures [finding 2], path/attack origin [finding 3].)
