# Stage 0A — Clubhead Pose → Golf Metrics Geometry Core (design spec)

- **Date:** 2026-06-28 (rev. 3 — pins down the math per 2nd review)
- **Status:** Draft for review
- **Branch:** `feat/camera-club-data`
- **Related:** [v2 research guide](../../Personal%20Research/markerless-club-data-guide-v2-research-corrected.md) (esp. §2.3, §4, §4.3, §4.4, §6A, §6B). This is **Stage 0A**; **Stage 0B** (BlenderProc renderer + pose estimators) is a separate later spec.

## 1. Purpose & context

OpenFlight is adding a markerless, behind-the-ball **camera** subsystem to measure club data the radar cannot: **impact location, face angle, dynamic loft** (plus corroborating club path / attack angle; camera spin later).

This spec covers **Stage 0A: the pure geometry/metrics core** — no rendering, no CV, no pose *estimation*. Given a clubhead **6-DOF body pose** + a **club template**, compute the golf metrics, and prove that math against analytic ground truth. It is pure Python (numpy/scipy), runs anywhere, de-risks the geometry, and its **sensitivity analysis is the error budget** that decides single-camera vs stereo.

## 2. Scope

**In:** coordinate frames + transforms; a parametric per-category **template** embedding a **curved face** (bulge/roll) in body coords with a **loft override**; metric math (impact location mm, face angle, dynamic loft, club path, attack angle); ground-truth generator; sensitivity harness; TDD suite.
**Out (later):** rendering, segmentation, pose *estimation*, calibration code, stereo, real images, `Shot`/server/UI integration.
**Non-goal:** matching vendors' undisclosed sign conventions / certified accuracy. We define our own, explicitly.

## 3. Location, dependencies, standards

- **Location (standalone sandbox):** `research/club_pose/` (outside `src/openflight/`): `types.py`, `frames.py`, `template.py`, `metrics.py`, `groundtruth.py`, `sensitivity.py`, `README.md`, `tests/`.
- **Promotion path:** validated math later migrates to `src/openflight/club_pose/` as a scoped tested PR.
- **Deps:** `numpy`, `scipy` (base deps), `pytest` (dev); `matplotlib` optional. **No new deps.**
- **Test gate:** `uv run pytest research/club_pose/tests/`. *Env note:* `uv` must be on PATH — open a fresh terminal after the winget install (it updated the persistent PATH, not already-open shells) or call the winget `uv.exe` by full path.

## 4. Coordinate frames, conventions, and exact metric formulas

### 4.1 World frame (right-handed, origin = ball center at address)
- **+X** = downrange (toward target); **+Y** = player **left** ("right" = −Y); **+Z** = up.
- **Handedness vs ballistics:** `ballistics.py` is **left-handed** (X-fwd, **Y-right**, Z-up). We use right-handed here because `scipy.Rotation` is right-handed and pose math in a left-handed frame invites sign bugs. Output scalars (below) are right/in-to-out/up positive, matching ballistics' lateral sign — promotion is a documented note, not a behavioral adapter.

### 4.2 Body frame (the rigid clubhead we observe), pose `(R,t)`: `p_world = R·p_body + t`
- **Origin = clubhead geometric center** (the point whose velocity gives club path/attack).
- **Canonical axes (Stage-0A definition, not deferred):** at **identity pose** (`R = I`) the body axes coincide with the world axes — body **+X** downrange, **+Y** left, **+Z** up. So a square, zero-loft club at identity has its face normal along **+X**. (Mapping a real estimator's recovered orientation onto this convention is the Stage-0B *registration* problem; the canonical axes themselves are defined here.)

### 4.3 Face frame (defined by the template, in body coords)
- **Face center** at `face_center_offset` (body coords).
- **Canonical face axes (before loft):** `û` = heel→toe = body **+Y** (`+u` = toe); `v̂` = low→high = body **+Z** (`+v` = high); `ŵ` = outward normal = body **+X** = `û × v̂`.
- **Loft:** rotate `{v̂, ŵ}` about `û` by `static_loft_deg` (normal tilts from +X toward +Z = up). **Lie is NOT applied** to the Stage-0A face normal (review finding 3): delivered lie lives in the recovered body pose; static lie would only enter via a shaft-referenced frame, deferred. `lie_deg` is not used in 0A math.

### 4.4 Exact metric formulas (sign-explicit — review finding 4)
Let `nw` = world-space outward face normal at the evaluation point = `R · n_body`. Let `vel` = world head-center velocity = `(t_b − t_a)/dt`. Angles in degrees.
- **Face angle** = `−atan2(nw_y, nw_x)` → open/right positive.
- **Dynamic loft** = `atan2(nw_z, hypot(nw_x, nw_y))` → up positive.
- **Club path** = `−atan2(vel_y, vel_x)` → in-to-out/right positive.
- **Attack angle** = `atan2(vel_z, hypot(vel_x, vel_y))` → up/ascending positive.
- **Impact Offset (mm)** = `u*` (toe positive); **Impact Height (mm)** = `v*` (high positive), where `(u*,v*)` is the impact point in face coords.

## 5. Components

### 5.1 `types.py`
- `ClubheadPose`: `rotation` (scipy `Rotation`), `translation` (3-vec mm, = head center in world). Prefer quaternion/`Rotation` construction; a raw-matrix path **explicitly validates** finite, 3×3, `RᵀR ≈ I` (tol), `det(R) ≈ +1` before wrapping (scipy silently orthonormalizes — finding 6).
- `ClubTemplate`: category; `static_loft_deg`, `face_width_mm`, `face_height_mm`, `bulge_radius_mm` (None=flat), `roll_radius_mm` (None=flat), `face_center_offset` (body coords). (`lie_deg` may be stored as metadata but is unused in 0A math.) `with_loft_override(loft_deg)`.
- `Measurement(value: float|None, confidence: float, source: str)`; `ClubMetrics` = six `Measurement`s.

### 5.2 `frames.py`
Pure functions over `scipy` rotations: world frame; nominal camera extrinsic (behind ball, looking +X) for the sensitivity depth axis; body↔world transforms; and the angle decompositions of §4.4. No state.

### 5.3 `template.py` — exact face geometry (review finding 2)
In the face frame (origin = face center; axes `û,v̂,ŵ`), a face point at lateral coords `(u,v)`:
```
h(u,v) = u²/(2·R_b) + v²/(2·R_v)          # outward sag; term = 0 when radius is None (flat)
P(u,v) = u·û + v·v̂ + h(u,v)·ŵ            # surface point, face coords
n(u,v) ∝ (−u/R_b)·û + (−v/R_v)·v̂ + 1·ŵ   # outward normal, normalized
```
- `R_b` = bulge (horizontal), `R_v` = roll (vertical), **positive = convex/outward**. **Valid range:** each radius `>` face half-dimension in that axis and `> 5×` ball radius, so the cap is single-valued and the closest surface point to a near-axis ball is unique. This is a **small-curvature paraboloid approximation** of true bulge/roll (sub-mm vs a sphere across a clubface).
- `point_to_face_uv(p_face)` → `(u*, v*)` = `argmin‖P(u,v) − p_face‖` (Newton, seeded at `(p_u, p_v)`), returning also the surface normal at `(u*,v*)` and the **signed distance** `d` (positive = ball on outward side). For a flat face this is exact orthogonal projection.
- `with_loft_override(L)`: rotate the face axes `{û,v̂,ŵ}` about `û` **through the face center** by `(L − static_loft_deg)`; preserves face center, dimensions, and radii. Returns a new template.

### 5.4 `metrics.py` — one pipeline (review findings 2 & 5)
**`compute_metrics(pose, template, ball_center_world=None, prev_pose=None, dt=None) -> ClubMetrics`:**
1. `impact_location` (if `ball_center_world` given): transform ball center world→body→face; `point_to_face_uv` → `(u*,v*)`, signed distance `d`. Contact validity (§9) decides confidence/None.
2. `face_angle` / `dynamic_loft`: normal source per the contact-state table (§9) — impact-point normal on valid contact, else face-center normal; rotate to world `nw`; apply §4.4.
3. `club_path` / `attack_angle`: only if `prev_pose` + `dt` given; `vel` from head-center translations; apply §4.4.
Thin wrappers `impact_location(...)`, `face_angle(pose, template, impact_point=None)`, `dynamic_loft(...)`, `club_path(pose_a, pose_b, dt)`, `attack_angle(...)` exist for unit testing.

### 5.5 `groundtruth.py`
Build `(pose, template, ball)` from a *specified* face-angle/dynamic-loft, a known impact `(u0,v0)` (ball center = `P(u0,v0) + r·n(u0,v0)` in world, so recovery must return `(u0,v0)`, `d=r`), or a known head-center velocity.

### 5.6 `sensitivity.py`
Sweep perturbations, record output error: impact vs head-translation error (depth axis vs in-plane, via the nominal camera); impact/face/loft vs body-rotation error; face/loft vs template-loft error (≈1:1 check); impact vs flat-vs-curved mismodel. Output tables + the **error budget** (§8).

## 6. Data flow
```
ClubTemplate (+loft override) → face geometry in BODY coords
ClubheadPose (R,t=head center) + ball_center_world → compute_metrics → ClubMetrics
groundtruth builds known (pose,template,ball) → assert recovered == set
sensitivity: perturb → compute_metrics vs truth → error tables → budget
```

## 7. Validation strategy (TDD)
Failing test first per unit.
- **Round-trip (perfect inputs):** each metric matches analytic truth to **≤0.01° / ≤0.01 mm**.
- **Loft-override live:** Δ° override → ≈Δ° dynamic-loft change (would have caught finding 1).
- **Impact-aware face/loft:** off-center impact on a curved face differs from center per `n(u*,v*)`.
- **Sign tests:** toe/heel(+/−u), high/low(+/−v), open/closed, in/out, up/down each verified against §4.4.
- **Contact states:** ball at exactly `r` off surface → valid; far/behind → None/conf 0; no ball → center-normal.
- **Degenerate:** square face → 0° face angle; pure loft → dynamic loft = static loft; ball at center → (0,0); flat==curved at center.
- **Invariances:** global translation leaves angles unchanged.
- **Sensitivity sanity:** zero perturbation → zero error; monotone.
- Green under `uv run pytest research/club_pose/tests/`.

## 8. Success criteria (Stage-0A gate)
1. All metric math validated to numerical precision against analytic ground truth.
2. Loft-override + impact-aware face/loft proven by test.
3. Sensitivity error budget produced for **two** target tiers (review finding 6):
   - **Single-camera realistic:** face/loft **±3–5°**, impact **~5–15 mm**.
   - **Stereo / stretch:** face/loft **±2°**, impact **±3–5 mm** (flagged as needing stereo, not the default).
   Each tier states the body-pose translation/rotation accuracy required — the input to the single-vs-stereo and resolution/calibration decisions.

## 9. Error handling & contact states

**Contact-state behavior (review finding 5):** `BALL_RADIUS_MM = 21.35` (from `ballistics.py`); `CONTACT_TOL_MM` configurable.
| Case | Condition | `impact_*` | face/loft normal source | confidence |
|---|---|---|---|---|
| `no_ball` | `ball_center_world is None` | `None`, conf 0 | **face center** | nominal; source `"center"` |
| `valid_contact` | on outward side AND `|d − r| ≤ tol` | `(u*, v*)` | **impact point** | scaled by `|d − r|`; source `"impact"` |
| `invalid_contact` | behind face, off-face, or `|d − r| > tol` | `None`, conf 0 | **face center** | reduced; source `"center_fallback"` |

**Other:** invalid template params (non-positive dims; radius outside valid range) → `ValueError`. Raw rotation matrix → explicit validation (above), do not rely on scipy. Two-pose metrics with `dt ≤ 0` or identical poses → `ValueError` / conf 0.

## 10. Open questions / documented assumptions
- **Face surface = paraboloid approximation** of bulge/roll (sub-mm vs sphere over a clubface). Exact toroidal/spherical form is a future option if the sensitivity study shows it matters.
- **Contact model = closest-surface-point.** Real contact is along the ball's approach direction; revisit once two-pose velocity is wired in.
- **Lie deferred:** delivered lie is in the recovered pose; static-lie modeling needs a shaft-referenced body frame (out of 0A scope).
- (Resolved in rev. 2–3: body/face-frame tangle, impact-aware signatures, path/attack origin, canonical body axes, exact face/loft math, sign formulas, contact states, success-tier split.)
