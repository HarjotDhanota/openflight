# Falsification test results — OpenFlight impact location, 2026-08-26

**Branch:** `feat/silhouette-poc`. Scripts in `research/silhouette_poc/falsification/`.
**Data:** 22-shot session `20260825_181734_filtered`, **shot_001 excluded** (old 495 µs /
gain 15 settings, 99.8 % clipped). Frames un-mirrored (`F[:, :, ::-1]`) before anything.

Covers falsification tests **1, 2, 5, 9** from `2026-08-26-agent-handoff.md` §3, and
closes **both** maintainer questions in §5. Every number below is over the full 21-shot
set unless stated; per-shot tables are in the scripts' output.

---

## 0. Headline

| Question | Verdict |
|---|---|
| Is the small 7i/9i launch gap real, or is LCMF compressing it? | **REAL.** An independent route gives a gap of **+2.91° mean / +4.22° median** against LCMF's **+2.59° / +3.60°** — if anything slightly *larger*, nowhere near the ~8° that "compressed dynamic loft" needs. |
| Does the OPS speed contract bias the grid search club-dependently? | **REFUTED.** Fixing the contract correctly moves the club gap by **−0.003°**. |
| §5 Q1 — is the ball above or below the lens; is the camera tilted 10°? | **CLOSED.** Ball is **163 mm BELOW** the lens. Camera boresight pitch **−0.185° ± 0.111°** on 21/21 — level. It does **not** share the enclosure's tilt. |
| §5 Q2 — what is the CAD face-plane-to-shaft angle? | **RESOLVED, and the model is fine.** Measured against the real striking face: **loft 33.10°, lie 61.19°** — a faithful 7-iron. `detect_face_plane` anchors the mesh frame to the **cavity rim on the BACK**, 16.4° from anti-parallel. See §5. |

Two new findings that were not on the list, both cross-checked on the full set:

- **A +5.3° camera-vs-radar disagreement in vertical launch**, on 20/20 shots, matching a
  **+5.10°** camera-vs-radar horizontal disagreement the shipped pipeline has been logging
  all along. Neither sensor can arbitrate the other from existing data.
- **LCMF's vertical launch moves +0.913°/(m/s)** with the assumed radial velocity, so a
  **1 % OPS ball-speed error is 0.41° of launch angle**.

---

## 1. Test #1 — independent vertical trajectory

`test1_vertical_trajectory.py`, `test1a_camera_pitch.py`, `test1b_reconstruct_and_sweep.py`,
`test1c_range_only.py`, `test1d_offset_character.py`

### 1.1 Method

Per shot: un-mirror the frames, find the teed ball, RANSAC the post-impact pixel track,
then fit a gravity-aware ballistic (speed, vertical, horizontal, t₀) simultaneously to

* the **camera pixels** through calibrated rays, and
* the **IWR6843 range walk** (`BallTrack`) — the range only, never the LCMF elevation
  estimator or any part of its DOA/multipath grid search.

Intrinsics come from **datasheet optics alone**: 2.8 mm lens / 3.0 µm pitch / 2× subsample
→ `fx = 466.67 px`. That deliberately keeps the §2.1 ball-centre bias out of the focal
length. Extrinsics come from the tape and the runtime config: camera 0.2032 m
(`mount_height_m`, and the maintainer's 8.1″), radar 0.1524 m, tee slant 1.575 m, ball
centre 0.040 m.

Residuals after the fix described in §1.5: **0.31–1.69 px** (median 0.50) and
**8.8–13.5 mm** of range. 20 of 21 shots reconstruct; shot_003 fails closed (no coherent
flight track).

### 1.2 Result — the gap is real

| | 7-iron | 9-iron | gap (mean) | gap (median) |
|---|---:|---:|---:|---:|
| camera rays + IWR range | 24.08° | 26.98° | **+2.91°** | **+4.22°** |
| LCMF | 18.94° | 21.53° | +2.59° | +3.60° |

The independent route reproduces the small gap. **The "dynamic loft is compressed to 55 %"
reading stays dead**, and now on evidence rather than on the algebraic argument alone.

(The mean gap is smaller than the median because shots 15 and 26 are 9-irons launching at
13.9° and 12.9° — thin strikes that drag the 9-iron mean down. The **median** +3.60° is the
figure the handoff quotes as "the measured 3.5° launch gap".)

### 1.3 The gap survives every assumption

`test1b_sweep.log`. The absolute angle swings 19.9°→36.6° across the sweep; the gap does not.

| variant | 7i | 9i | gap | Δgap |
|---|---:|---:|---:|---:|
| baseline (all measured) | 24.08 | 26.98 | 2.908 | — |
| camera pitch −3° | 19.93 | 22.81 | 2.882 | −0.026 |
| camera pitch +3° | 28.02 | 30.94 | 2.917 | +0.009 |
| **camera pitch +10° (enclosure tilt)** | 36.65 | 39.53 | 2.886 | **−0.022** |
| ball centre 21.3 mm (on mat) | 24.31 | 27.22 | 2.913 | +0.005 |
| tee range −50 mm | 23.77 | 26.59 | 2.828 | −0.080 |
| tee range +50 mm | 24.39 | 27.37 | 2.987 | +0.079 |
| camera height 7.5″ / 8.5″ | 23.88 / 24.27 | 26.77 / 27.20 | 2.888 / 2.927 | ∓0.02 |
| focal ±2 % | 24.48 / 23.69 | 27.42 / 26.56 | 2.944 / 2.873 | ±0.036 |

A **13° swing in camera pitch moves the gap by 0.03°.** This is the property that makes the
gap trustworthy while the absolute value is not, and it is measured here rather than assumed.

### 1.4 §5 Q1 — CLOSED, and the source of the old contradiction

`test1a_camera_pitch.py`, 21/21 shots, teed ball located on every one.

* Camera **0.2032 m**, ball centre **0.040 m** → the ball is **163.2 mm BELOW the lens**,
  1571 mm forward. Confirmed independently by the maintainer's tape (8.1″) and by the
  runtime's own `mount_height_m` in the kiosk log. **The "~200 mm above the lens" branch is
  dead on the tape alone.**
* Measured depression of the ball below boresight: **5.742° ± 0.111°**.
* Geometric depression from the tape: **5.926°**.
* → **camera boresight pitch = −0.185° ± 0.111°.** The camera is level to a fifth of a degree.

**Why the handoff said 2.73°:** that figure reproduces exactly at `fx = 1033 px` — the
shipped **A0 preset**, which `fit_real.py:10` already documents as "a camera we do not
have". At the A0 focal length I get **2.601° ± 0.051°**. The 2.73° was a real measurement
of the pixels divided by a focal length 2.21× too long. With the right focal length the
tape and the imagery agree and there is no contradiction left to resolve.

The §0 entry *"The camera is essentially level; the 10° tilt is the radar's — UNSETTLED"*
is now **CONFIRMED**. The 10.405° tilt in `iwr6843_calibration_reference.json` is the
radar's alone.

Corroboration: the teed ball measures 12.77 px across, which at the tape-derived camera
range of 1.581 m implies `fx = 473.1 px` — within 1.4 % of the datasheet's 466.67, and
nowhere near 1033.

### 1.5 Two bugs found and fixed in the reconstruction itself

Recorded because both produced *plausible* answers before being caught.

1. **Clipping the radar walk.** Evaluating `range_at` clipped to `[t_first, t_last]`
   flattened the earliest frames — the ones nearest launch and worth the most — to a
   constant. The walk is a fitted low-order polynomial and `impact_time_s` already relies
   on back-extrapolating it to the tee, so extrapolation is legitimate. Range RMS
   111 mm → 12.7 mm.
2. **Tee anchor mismatch.** Deriving the tee's forward distance from the pixel row put it
   21 mm inside the radar's own tee anchor, which no free parameter could absorb. Forward
   distance and height now come from the tape; only the lateral coordinate, where the tape
   says nothing, comes from the camera.

A third was caught before it produced anything: the first flight tracker latched onto a
**static background blob**, which a quadratic fits perfectly and which therefore wins a
RANSAC vote against the real ball. The tracker now builds an explicit static-background
census and requires the track to actually climb (>40 px, monotone).

### 1.6 The +5.3° camera-vs-radar offset

On **20 of 20** shots the camera reads higher than LCMF:

* camera − LCMF = **+5.34° ± 0.83°**, range +4.19° to +7.69°
* regression `camera = 0.954 × LCMF + 6.28` (r = 0.971) — a near-pure **offset**, not a gain
  error in either estimator

**The camera chain is validated**, on the one axis where an independent check exists:
my reconstruction's horizontal launch matches the **shipped** `ball_flight.py` camera
estimator to **+0.064° ± 0.199°, r = 0.998** across 18 shots. Two independently written
pipelines agreeing to 0.06° is not a chain that is 5° wrong.

And the same disagreement is already in the session log: the shipped pipeline's own
`experimental_camera_iwr_delta_deg` — its camera horizontal minus its radar horizontal —
is **+5.10° ± 1.09°** over those 18 shots. **The horizontal disagreement the project has
been logging is not horizontal-specific.**

Neither sensor can arbitrate the other:

* the camera's angular datum is *measured* (§1.4, level to 0.185° on 21/21);
* LCMF's is the 10.405° corner-reflector tilt plus the elevation DOA, and LCMF's launch
  angle scales **1:1** with that assumed tilt (`_spatial_dictionary` computes
  `direct = arctan2(…) − tilt_rad`);
* **test 1c settles nothing** — see below.

Physical plausibility points opposite ways in the two axes, which is why this is reported
as an open disagreement and not as a verdict. The radar's horizontal is negative on every
shot (mean ≈ −5.4°, i.e. 5.4° left every time) where the camera sits near zero; a mean
horizontal launch of −5.4° on 21 consecutive shots is not what a golfer does. But in the
vertical the camera's 24.1° mean for a 7-iron is high against LCMF's 18.9°.

### 1.7 Test 1c — the range walk alone cannot referee

`test1c_range_only.py`. A launch angle fitted to the **raw** slant-range detections only —
no camera pitch, no radar tilt, no DOA — is **ill-conditioned on this hardware**:

* within-club scatter **12.8° (7i) / 17.9° (9i)**
* per-shot values from −2.9° to 86°
* the formal 1σ from the Jacobian (median 1.08°) **understates the real scatter by >10×**

The curvature signal over a 40 ms window is ~18 mm against ~12 mm of range noise, exactly
as the conditioning estimate predicted. **Its club gap is not reported, because it is
noise.** The useful output is the negative result: the existing radar range data cannot
arbitrate the ±5°.

---

## 2. Test #2 — OPS speed contract audit

`test2_ops_speed_contract.py`, `test2_lcmf_ab.py`, `test2_decompose.py`,
`test2_sensitivity.py`, `test2e_horizontal_sensitivity.py`

### 2.1 The contract defect is real

`estimate_lcmf_v1` derives **two different physical quantities from one input**:

```python
phase_velocity_ms = ball_speed_mph / MPH_PER_MS          # lcmf.py:855
...
model_geometry = {"speed_ms": ball_speed_mph / MPH_PER_MS, ...}   # lcmf.py:879
```

* `phase_velocity_ms` → `tdm_phase = sign · 4π·v·τ/λ`. **Wants instantaneous radial range
  rate.** OPS radial speed is roughly right.
* `model_geometry["speed_ms"]` → `ballistic_trajectory_from_range`, which uses it as
  `vx = v·cos L`, `vz = v·sin L − g·t`. **Wants TOTAL launch speed.** OPS radial speed is
  wrong.

And LCMF does receive the raw radial value: `server.py:3269` applies `correct_ball_speed`
**after** `_ensure_user_facing_launch_angles`, so the cosine correction runs downstream of
the estimator that needed it. Confirmed against the session record — the applied factor
reproduces `radial_speed_factor` to a residual of **−0.00066 (max 0.00095)**:

| club | n | mean launch | factor | speed shortfall |
|---|---:|---:|---:|---:|
| 7-iron | 8 | 18.49° | 0.9758 ± 0.0021 | **2.42 %** |
| 9-iron | 13 | 21.53° | 0.9694 ± 0.0048 | **3.06 %** |

Between-club differential: **0.638 percentage points**.

> **Correction to the D-plane report.** It gives the projection factor as
> 0.956→0.906 across 17°→25°. Those are plain `cos(launch)`, i.e. a horizontal
> line of sight. The radar sits 4″ below the ball and 5.2 ft behind, so the LOS
> rotates up toward the velocity vector during the window and the true factors are
> **0.976 / 0.969**. The report overstated the effect by roughly 4×.

### 2.2 The decomposition — the flagged contract is inert

Three arms, all 21 shots, real dumps through the real grid search.
**Control:** arm A reproduces the shipped launch angle to **+0.096° mean, 0.47° max** on
20/21 (`tx_order=normal` confirmed from the kiosk log). Shot 5 differs by +1.98° and is
**not** explained — its shipped status is plain `accepted`, not an ops-guided rescue, and it
has the fewest frames in the set (7). Recorded as an open item rather than rationalised.

| | 7-iron | 9-iron | gap (mean) | gap (median) |
|---|---:|---:|---:|---:|
| **A** both contracts fed radial (shipped) | 18.735 | 21.683 | +2.948 | +3.474 |
| **C** phase radial, ballistic total (*the correct fix*) | 18.727 | 21.672 | +2.945 | +3.473 |
| **B** both fed total (the naive fix) | 19.920 | 22.948 | +3.028 | +3.529 |

| where the shift lives | |
|---|---:|
| ballistic contract alone (C−A) | **−0.0095° ± 0.0033°** |
| phase contract alone (B−C) | **+1.2439° ± 0.2920°** |

**Verdict: REFUTED.** The contract flagged by the report — the ballistic forward model —
is worth **one hundredth of a degree**, and fixing it correctly moves the club gap by
**−0.003°**. `speed_ms` reaches the elevation observable only through the `0.5·g·t²`
gravity term, a 1 % correction on a 1 % term.

**Arm B is a trap, not a fix.** Its +1.24° comes entirely from the TDM de-rotation, where
radial speed is the *physically correct* input. Feeding total launch speed there would make
the estimator worse while appearing to "correct" it.

The real defect is narrower than reported but should still be repaired: `speed_ms` is
documented and used as total launch speed and is being handed a radial value. It is latent
now only because the observation window is short.

### 2.3 New: LCMF's launch angle is hostage to OPS speed accuracy

`test2_sensitivity.py`, ±3 % sweep, 21/21 shots, all the same sign:

* **∂(vertical launch)/∂v = +0.913 ± 0.113 °/(m/s)**, near-linear (max non-linearity 0.30°)
* **∂(horizontal)/∂v = +0.349 ± 0.190 °/(m/s)**
* i.e. **a 1 % OPS ball-speed error moves LCMF vertical launch by 0.41°**

This belongs in the uncertainty budget and in falsification test #8. It also accounts for
part of the §1.6 offset in the right direction: LCMF is underfed by ~1.35 m/s, so it reads
**~1.2° low** in vertical and ~0.5° low in horizontal — roughly a quarter of the +5.3°, not
all of it.

### 2.4 Open: the shipped horizontal does not reproduce offline

Calling `estimate_lcmf_v1` at HEAD with the documented defaults returns a horizontal angle a
**near-constant +9.13°** from the shipped value (sd ≈ 0.007° across 21 shots — a
deterministic constant, not a data-dependent difference). Ruled out: `net_range_m`,
`tdm_sign_policy`, `azimuth_offset_deg` (0, since the CSV's `horizontal_deg` equals
`horizontal_raw_deg`), `horizontal_phase_reference_rad` (CLI and kiosk defaults are both
unset), and code drift (no commits since the capture).

The vertical reproduces to 0.1°; the horizontal does not. **A shipped angle whose
provenance cannot be reproduced offline needs a cause before it is used in any comparison.**
This does not affect §1 or §2.2 (both vertical), nor the §1.6 camera cross-check (which
compares two camera-side numbers).

---

## 3. Test #5 — attack-angle robustness

`test5_9_aoa_and_clustering.py`

The raw attack-angle candidate is **rejected on 21 of 21 shots** (`candidate_out_of_bounds`
every time). This channel does not work intermittently in this session; it never works.

| club | n | median | mean | sd | range |
|---|---:|---:|---:|---:|---|
| 7-iron | 8 | −29.25° | −29.06° | 2.09° | −31.9 … −25.3 |
| 9-iron | 13 | −33.20° | −32.48° | 2.51° | −37.3 … −29.2 |

Robust per-club median difference **−3.95°**, which at the report's `∂L/∂A = −0.235` moves
the 7i→9i launch gap by **+0.93°** — substantial against a +2.59°/+3.60° gap, but
unusable, because the underlying values are physically impossible (a 7-iron attacks at
about −4°, not −29°) and the shipped gate rejects all of them.

The *fused* attack angle is sensible (−4.2° to −6.5°) on 19 of 21, with shots **20
(−30.98°)** and **25 (+25.23°)** the outliers the report flagged.

## 4. Test #9 — failure clustering

Three flags fire on **21/21**: attack angle rejected, club path rejected,
`rejected_phase_span`. These are **universal, not clustered** — the club-path channel
fails on every shot in the session.

Among the flags that actually vary, there is **no evidence of a shared cause**:

* variance of flags-per-shot **0.662** vs **0.848** expected under independence → ratio
  **0.78** (slightly *sub*-Poisson, i.e. if anything anti-clustered)
* strongest pairwise correlation r = 0.45, between two flags that fire on 2 shots each

The question the test was designed to ask is answered "no". The question it surfaces
instead is why `rejected_phase_span` is unanimous.

---

## 5. §5 Q2 — RESOLVED: the model is fine, `detect_face_plane` is not

`test5q2*.py`, `render_mesh_views.py`, `test5q2g_face_plane_bug.py`

> **Both earlier answers in this section were wrong and are withdrawn.** First I
> reported "loft 17.5°, the shaft is fine, the face is the anomaly". Corrected once to
> "off by 14–18°, attribution unknown". Both rested on identifying the club's surfaces from
> geometry alone. The maintainer pushed back twice — the model's author states ~36° — and
> the second push prompted the obvious step neither earlier pass took: **render the mesh
> and look at it.** That settled it in one image.

### 5.1 What the render shows

`render_mesh_views.py` draws the mesh's own triangles, coloured by candidate surface, from
six viewpoints. Two views are decisive:

* Looking along the surface `detect_face_plane` anchors to: **a cavity with a perimeter
  rim.** That is the BACK of a cavity-back iron.
* Looking the other way: **a broad flat face covered in scorelines.** That is the striking
  face — and it is the surface both earlier passes had dismissed as "the cavity floor".

The surface taken for the sole is in fact the sole; it carries the stamping.

| | earlier label | what it actually is |
|---|---|---|
| `[0.998, −0.001, 0.059]` | "the face" (and what the mesh frame is anchored to) | **cavity rim, on the back** |
| `[−0.941, 0.021, −0.337]` | "the cavity floor, recessed" | **the STRIKING FACE** |
| `[0.245, 0.202, 0.948]` | "the sole" | the sole ✓ |

### 5.2 The corrected specs — the model is right

| face used | loft | lie | face-to-shaft |
|---|---:|---:|---:|
| **the real striking face** | **33.10°** | **61.19°** | **56.77°** |
| the cavity rim, i.e. what the code uses | 17.48° | 61.19° | 72.59° |
| a 36°/60° club requires | 36.00 | 60.00 | 59.40 |
| the 690CB catalogue, 34°/62° | 34.00 | 62.00 | 60.41 |

**Measured 33.1°/61.2° against a stated ~36°/60° and a catalogue ~34°/62°.** The mesh is a
faithful 7-iron to within about three degrees. There was never anything wrong with it.

### 5.3 The actual bug, and why the gate rejects the real face

Reproducing `detect_face_plane`'s own region growing shows the striking face **is** found as
a clean candidate — 2950 mm², 797 triangles, aspect 0.621 (inside the 0.35–0.65 clubface
band), flatness 0.57 mm. It passes the aspect and flatness gates and is by far the largest
region in the mesh. It is thrown away by the third gate:

```
projection = vertices @ normal            # ALL vertices, including the shaft
extremity  = min(|centroid·n − min(proj)|, |max(proj) − centroid·n|)
depth      = ptp(projection)
accept if  extremity <= max(1.0, 0.10 * depth)
```

| | value |
|---|---:|
| the striking face's extremity distance | **17.31 mm** |
| the gate's limit for it | **6.09 mm** |
| mesh extent along the face normal | 60.86 mm |
| **head-only** extent along the face normal | 36.63 mm |
| extent the SHAFT adds along that normal | **24.23 mm** |

`projection` is taken over the whole mesh, so the shaft — which protrudes 24 mm along the
face normal — sets the extremum, and the striking face is no longer at it. Nine regions
survive all three gates and the winner is `max(candidates, key=coherent_area_mm2)`: the
**863 mm² cavity rim**, chosen over a 2950 mm² striking face that was disqualified by a
gate measuring geometry the gate was never meant to include.

**The chosen rim sits 16.36° from anti-parallel to the real face** (dot −0.960). The mesh
frame's `+x` — `FACE_NORMAL = [1, 0, 0]` — points out the **back of the club**.

### 5.4 What this breaks, and what it does not

**Unaffected:** the silhouette IoU fit itself. Rendering the mesh at a pose produces the
same mask however its axes are labelled, so the 0.633 IoU and the detection/tracking
results stand.

**Broken:**
- **Any physical reading of the face normal.** Dynamic loft computed from the mesh frame
  reads **17.48° where the club is 33.10°** — 15.6° out, pointing backwards.
- **The §1.3 physical-loft gate**, which bounds world-frame face-normal elevation to 5–45°.
  It is bounding the back of the club. This is the most likely explanation for that
  section's finding that **37 % of fitted frames (128/349) imply an impossible dynamic
  loft, from −70° to +60°** — of course they do.
- **The roll parameterisation.** `_face_axes(roll)` rotates about `FACE_NORMAL`, so the roll
  axis is 16.36° off the true face normal.

### 5.5 Method note — two wrong answers, and what produced them

Both earlier attributions came from inferring surface identity from geometry on an
unlabelled triangle mesh. The specific instrument that misled me was the **convex-hull depth
test**: the striking face reads 3.1–3.8 mm "inside the hull" even with the shaft excluded,
because the hosel protrudes past the face plane and hull facets bridge from it across the
face. A recessed reading was taken as proof of "not exterior". It was not.

The fix cost one render. On an unlabelled mesh, **identification is a perception problem,
not a geometry problem** — look at it before measuring it.

## 6. What to do next

1. **Do not fix the ballistic speed contract expecting it to change anything.** Repair it
   for correctness (§2.2), and record that the club gap moves −0.003°.
2. **Do not "fix" the phase contract.** Radial speed is right there. Arm B looks like a
   1.24° improvement and is not.
3. **Put ∂LA/∂v = 0.913 °/(m/s) in the uncertainty budget.** OPS ball-speed accuracy is
   now a launch-angle spec.
4. **Resolve the ~5.3° two-axis camera/radar disagreement.** It is the largest unexplained
   quantity in the system and the existing radar data cannot settle it (§1.7). The cheapest
   decisive measurement is a target at a tape-known position visible to both sensors.
5. **Find the +9.13° horizontal reproducibility gap** (§2.4) before any camera/radar
   horizontal comparison is trusted.
6. **Fix `detect_face_plane`'s extremity gate and re-normalise the mesh (§5.3).** The
   gate projects onto `vertices @ normal` over the WHOLE mesh, so the shaft's 24 mm of
   protrusion disqualifies the real striking face and the cavity rim wins by default. The
   mesh keeps no shaft/head separation, so the minimal fix is to compute `projection` and
   `depth` over the candidate region's own connected neighbourhood, or to prefer the
   largest region outright when the aspect and flatness gates already pass. **Then re-run
   everything downstream of dynamic loft** — including the §1.3 physical-loft gate, which
   is currently bounding the back of the club and rejecting 37 % of frames.
7. **Turn the inclinometer on, and level the camera from it too.** No inclinometer ran in
   this session — the radar's 10.405° tilt was a **static constant from a 2026-07-12
   corner-reflector solve**, never checked against the kickstand angle on the day, while
   the camera's attitude *was* measured per shot from the teed ball
   (`club_delivery.py:253`). LCMF's launch angle scales 1:1 with that constant, so an
   unverified 5° of tilt drift reproduces the entire §1.6 offset. See §7.
8. Falsification tests **3, 4, 6, 7, 8, 10, 11** remain unrun; **12** still needs new data.

---

## 6b. Verified against the Codex audit (`openflight_claude_artifact_dplane_audit.md`)

That audit re-derives the results above correctly and adds four implementation defects in
the shipped club path. All four confirmed by reading the code; the magnitudes below are
measured here (`test_audit_timing_and_aoa.py`) and decide the fix order.

| Defect | Location | Measured cost |
|---|---|---|
| **Callback timestamps used for club velocity** | `server.py:2720` passes `archive["host_timestamp_ns"]`; `sensor_timestamp_ns` is in every archive | **2.04 % typical club-speed error** on the 3-frame preferred interval, **28.7 % worst**; 3.06 %/**43 %** on the 2-frame variant |
| **Mesh fitter searches the wrong depth** | `fit_real.py:59, 250, 338` pin plate scale and both `range_grid_mm` to **1425 mm** | 21-shot ball gives 12.77 px → **1560 mm**; tape gives **1581 mm**. Neither grid (1300–1550, 1325–1525) **contains** it. Local refinement is unbounded and could climb out, but it hill-climbs from a coarse pose chosen at the wrong depth — see §6d for what actually happened |
| **Preferred interval crosses impact** | `club_delivery.py:89` `PREFERRED_PATH_OFFSETS = ((-2, 1), …)` | ~6.4 ms spanning the collision. Not yet quantified |
| **Attack angle uses the wrong projection** | `club_delivery.py:283-288` returns `atan2(vertical, forward)` | **+0.052° mean, 0.347° max** at observed paths; 0.07° at 10°, 0.60° at 30° |

**Fix order: timestamps and the range grid first.** Both are cheap — the timestamps are
already recorded, the range constant is three literals — and both are large. The interval
and the AoA formula are structural and minor respectively.

Where 1425 mm came from is instructive: a 13.97 px ball, against 12.77 px measured across
21 correctly exposed shots. The earlier figure traces to the capture that turned out
99.8 % clipped, and a saturated ball blooms.

### Corrections the audit is right about, applied to the public page

- **"Impact location falls out of the fusion"** — wrong, and it survived in §06 even after
  the handoff flagged it. Tangency gives a world contact point; toe–heel and high–low need
  the face origin and axes, i.e. full 6-DoF head pose plus per-club face registration.
- **"~19° radar beam"** — the §0 understatement, still live in §09c. Twelve virtual
  channels, **two effective elevation positions**, ~58° elevation two-target resolution.
- **"OPS I/Q at 30 ksps → 33 µs impact timing, adequate"** — sample spacing is not
  timestamp accuracy. Measured camera/radar impact agreement is 1.41 ms.
- **The TrackMan analogy** — valid as commercial precedent that frame rate is not the
  binding constraint (which `openflight-comparator-set` establishes and the audit agrees
  with); invalid as evidence that *this* sensor suite suffices. TrackMan runs two radars.
- **"Measured 2.8 mm lens"** → *nominal*. No distortion model, no separately estimated
  principal point, no independent `fx`/`fy`.
- **Ball-diameter focal length as independent corroboration** → it is not independent; the
  ball boundary carries the one-sided bias. It is a consistency check agreeing to 1.4 %.
- **"One properly exposed shot re-tests almost everything"** → one shot is diagnostic, not
  validation.

### Where I would qualify the audit

- The **club-model metadata** recommendation (ship authored face surface, origin, toe/up
  axes, outward normal, shaft axis) is the right fix and better than repairing the
  extremity gate — §5.3's gate fix should be treated as a stopgap for onboarding, with the
  authored registration as the authority.
- The audit treats the two camera-side implementations agreeing to 0.064° as *not*
  validating the world datum. Correct, and §1.6 already says so — they share the geometry
  chain. Worth stating explicitly that this is why the ~5.3° needs an external target.

---

## 6c. Changes applied 2026-08-26

Three of the four defects in §6b are fixed. Each has a failing test written first, per
`CLAUDE.md`. **POC suite 144 → 147, main suite 1490 → 1495 passing; the same 36 pre-existing
Windows shell-script/serial/cloud failures, unchanged.** Pylint 9.49/10 (was 9.46).

| Fix | Change | Test |
|---|---|---|
| **Club path timed on the sensor clock** | `server.py` — new `_optical_timestamps_ns(archive)` prefers `sensor_timestamp_ns`, falls back to `host_timestamp_ns` for older archives; `_fuse_camera_club_delivery` uses it | `tests/test_server.py::TestOpticalTimestampSource` (2) |
| **Attack angle uses total horizontal speed** | `club_delivery.py::_velocity_angles` — `atan2(vertical, hypot(lateral, forward))`. Club path is unchanged; it is an azimuth and was already right | `tests/test_camera_club_delivery.py::TestVelocityAngleProjection` (3) |
| **Mesh fitter searches the real depth** | `fit_real.py` — new `CAMERA_BALL_RANGE_MM = 1581.0` from the tape chain; plate scale derived from it; both `range_grid_mm` defaults recentred (1456/1581/1706 and 1481/1581/1681). `fit_frame`'s `arange(1250, 1651, 50)` already bracketed correctly | `research/silhouette_poc/tests/test_fit_real_range_grid.py` (3) |

The range-grid test asserts the property rather than the constant: every default depth grid
must bracket the tape-derived range, which is recomputed in the test from the measured
heights. It fails if either the grid or the rig geometry drifts apart again.

**Deliberately not changed:**

- **`_fuse_camera_ball_flight` still uses host timestamps.** It is not the same case. It
  computes `relative_time = timestamps_ns[frame] − trigger_host_timestamp_ns` and feeds
  that into the radar range lookup, so its timestamps are tied to the host clock. The two
  clocks share an epoch and differ by 2.436 ms mean, so a naive swap would bias every radar
  range lookup by ~110 mm. The correct fix is the audit's affine clock map — the trigger
  must be carried into the sensor clock first. Its intervals are also longer (up to 15
  frames), so the jitter costs ~0.4 % rather than 2 %.
- **The cross-impact preferred interval** (`PREFERRED_PATH_OFFSETS`). Structural, and
  changing which frames define "delivered" needs the definition frozen first.
- **`detect_face_plane`.** §5.3's gate repair is a stopgap; the audit's authored club-model
  metadata is the right fix and is a design decision.

---

## 6d. Re-fitting the mesh — with overlays, and what they exposed

`test_meshfit_depth_ab.py`, `render_fit_overlays.py`, renders in `falsification/renders/`

### 6d.1 The first render found a bug the numbers had hidden

Drawing the fit showed the tracked outline on a small round object while the clubhead sat
plainly visible at the bottom of the frame. **The tracker had followed the departing ball
after impact** — the failure `make_overlay.track_club`'s own docstring warns about,
reintroduced in my rebuild.

Those ball frames carried the **highest IoU in the set (0.56–0.62)**, because a small round
blob is easy to cover, and the *best* temporal coherence, because a ball flies smoothly.
**The contamination flattered every metric being used to judge the fit**, and no number
gave a hint. One image did.

Fixed: stop at contact, using the ball's own image track extrapolated back to the tee row
per shot, and veto the ball explicitly. **66 pre-impact frames across 21 shots** — the
frames club delivery is defined on.

### 6d.2 The three arms, on clean masks

| | A — shipped grid | B — corrected grid | C — range pinned |
|---|---:|---:|---:|
| depth treatment | search 1300–1550 | search 1456–1706 | fixed at 1581 |
| frames fitted | 66/66 | 66/66 | 66/66 |
| **median IoU** | **0.4625** | 0.4401 | **0.3896** |
| median fitted range | 1180 mm | 1336 mm | 1581 mm |
| error vs the tape | −401 mm | −245 mm | 0 |
| settling below their own grid | 78.8 % | 81.8 % | — |
| railed on the ±240 mm refinement limit | 18.2 % | 25.8 % | — |
| **median pose jump** | **44.68°** | 37.89° | **31.83°** |
| **adjacent pairs jumping >45°** | **50.0 %** | 44.4 % | **33.3 %** |

**IoU and pose coherence move in opposite directions, monotonically.** The arm with the
best IoU has the worst poses. **The pattern held both before and after the ball
contamination was removed, with every absolute number changing in between** — which is what
makes it a finding rather than an artefact.

**Silhouette overlap is not a proxy for correctness here; over this range it is an inverse
one.** Stop reporting IoU as fit quality; report pose coherence and range agreement.

### 6d.3 The scale mismatch, now visible

`renders/scale_shot_029_9-iron.png`: **the model covers only 43–55 % of the observed pixels
at the tape-measured range.** A shorter range enlarges the projection, so the fit pulls the
club nearer to close that gap — 401 mm nearer under the shipped grid, and recentring the
grid does not stop it because the grid was never the cause.

The overlay shows a thin tail of **shaft and hosel** running out of each observed
silhouette, which a head-only mesh cannot cover at any range. So `split_head` is leaving
neck pixels in the head partition. Motion blur adds more (~9 mm of head travel per
exposure). Whether those two account for all of a 2× area excess, or whether the mesh is
genuinely smaller than the real head, is the next measurement.

**Pinning the range hides this rather than fixing it**, and the 0.46 → 0.39 IoU drop is the
size of what is hidden.

### 6d.4 Act

1. **Pin range from the radar** — costs 0.07 IoU, cuts impossible poses by a third.
2. **Retire IoU as a progress metric.**
3. **Diagnose the 2× area excess** before more pose work: quantify shaft leakage in
   `split_head`, then blur, then the mesh's own dimensions.
4. A third of poses still jump >45° with depth pinned. One degenerate DoF removed ≠ pose
   solved.

### 6d.5 Method notes

- Not comparable to the published 349 frames / IoU 0.633: that tracker no longer exists
  (`head_split` was never wired into `make_overlay.py`). Only the between-arm comparison is
  valid — all arms see byte-identical masks.
- The earlier "fail-closed violation" framing was too strong (§6b): local refinement is not
  grid-bounded (±240 mm), so both grids could reach 1581 mm in principle. Measured: neither
  contained it and neither reached it.
- A worry that `pose_jump_deg` used the wrong convention proved unfounded — the fitter's
  `Rr @ R` with `Rr = rot(R·e_x, roll)` equals `R @ Rx(roll)` since
  `rot(R a, θ) = R rot(a, θ) Rᵀ`. Identical to 2 dp. Code uses `fit_real.triad` regardless.
- Two unasserted `str.replace` calls silently no-opped, wasting a run. Every patch now
  asserts presence and uniqueness.
- **The standing lesson, twice in one session:** render it and look before trusting a
  number. It found the wrong clubface (§5) and the ball contamination here.

---

## 7. Attitude: what an accelerometer would and would not fix

Measured coefficients make this a spec rather than a preference. Camera pitch enters the
**absolute** launch angle ~1:1 (§1.3), and LCMF's launch angle scales 1:1 with the assumed
radar tilt (`_spatial_dictionary`: `direct = arctan2(…) − tilt_rad`). So attitude error is
launch-angle error, one for one, on both sensors.

**Current state, measured:**

| | attitude source in this session | checked on the day? |
|---|---|---|
| camera | derived per shot from the teed ball (`club_delivery.py:253`, `ball_flight.py:141`) | **yes** — 21/21, −0.185° ± 0.111° |
| radar | static `tilt_deg: 10.405` from the 2026-07-12 corner-reflector solve | **no** — no inclinometer ran |

**An accelerometer fixes:** pitch and roll drift, kickstand angle, uneven floor, mat
thickness — everything that varies between setups. Read static pre-shot and averaged, a
LIS3DH resolves ~0.1–0.2°, comfortably inside the 0.5° that a 0.5° launch-angle spec needs.

**An accelerometer does NOT fix:**
- **Yaw/azimuth.** Gravity carries no heading information. The ~5° *horizontal*
  camera-vs-radar disagreement (§1.6) is untouched by any accelerometer.
- **Boresight offset.** It measures the *enclosure's* attitude, not the optical axis or the
  antenna boresight. The fixed rotation from the accelerometer frame to each sensor still
  needs calibrating once. It converts an unknown absolute angle into a known angle plus a
  fixed unknown — a large gain, but not a free one.
- **Attitude during the swing**, if the unit sits on something that flexes. Read it at rest.

**Why it is worth doing anyway:** the camera and radar currently have *independently
assumed* attitudes with nothing tying them together — one measured from a ball, one a
six-week-old constant. Mount both to a single datum with one accelerometer and their
**relative** attitude becomes a mechanical constant, measurable once in CAD. That turns the
§1.6 offset from unexplained into a testable prediction.

**What the new enclosure must specify**, because the reconstruction in §1 rests on numbers
that are currently config defaults rather than measurements of that rig:
lens-centre height above the base; radar antenna-centre height; both lateral offsets;
camera optical axis and radar boresight relative to one datum face, with tolerances; the
accelerometer mounted to that same datum; and a repeatable tee position (distance and
lateral). Today these are 0.2032 m, 0.1524 m, −0.060325 m, and 1.575 m — and the tape
agrees with them, which is reassuring but is not the same as the rig being built to them.
