# Handoff to Codex — OpenFlight impact location, 2026-08-26 (evening)

**Branch:** `feat/silhouette-poc`. Research suite 147 passing, main suite 1495 passing
(36 pre-existing Windows shell-script/serial/cloud failures, unchanged by this work).

**Read §0 first.** Seven claims were published during this session and then found wrong.
Several were *corrections of earlier corrections*. Do not cite anything below without
checking it against §0.

Supersedes nothing; **extends** `2026-08-26-agent-handoff.md` and
`2026-08-26-falsification-results.md`. All scripts live in
`research/silhouette_poc/falsification/`. Every number is over the 21-shot session
`20260825_181734_filtered` with `shot_001` excluded and frames un-mirrored.

---

## 0. Claims I published this session and then had to retract

| Claim | Status | What was actually true |
|---|---|---|
| "The 7-iron mesh's loft is 17.5°; the shaft is fine, the **face** is the anomaly" | **WRONG** | The model is fine: **loft 33.10°, lie 61.19°**. `detect_face_plane` anchors the mesh frame to the **cavity rim on the back**. |
| "Off by 14–18°, attribution unknown" (the first correction) | **ALSO WRONG** | Same as above. Two wrong answers before rendering the mesh and looking at it. |
| "The model carries a 62 mm **shaft stub**" | **WRONG** | It is **hosel + ferrule**: 63.8 mm long, 12.9–17.5 mm diameter. An iron shaft tip is 9–10 mm and ~900 mm. **There is no shaft on this model.** |
| "The 1425 mm range grid is a **fail-closed violation** — the search cannot reach the truth" | **TOO STRONG** | Local refinement is **not** grid-bounded (`step=[60.0,…]`, 4 rounds → ±240 mm). Neither grid *contained* 1581 mm and neither *reached* it, which is the defensible statement. |
| "Foreshortening sensitivity **vanishes** at square because cos has zero slope" | **OVERSTATED** | It weakens ~4× (0.228 → 0.059 px/deg), not to zero. A clubhead is a solid body, not a flat plate. **This is what leaves the optical route open.** |
| Field of view today = 2.17 m | **WRONG** | **1.08 m.** I used the full sensor width where the capture reads half of it. |
| Mesh-fit numbers from the first A/B run (144 frames, IoU 0.4565…) | **CONTAMINATED** | My tracker followed the **ball** after impact. Ball frames scored the *highest* IoU and *best* coherence. Superseded by the 66-frame pre-impact run in §3.4. |

**The pattern, and it cost most of a session:** every one came from inferring a physical
fact from geometry or arithmetic instead of measuring the thing. The convex-hull depth test
actively misled (the hosel protrudes past the face plane, so hull facets bridge across the
face and it reads "recessed"). **Render it and look before trusting a number** — that rule
found the wrong clubface *and* the ball contamination, and is now the standing convention.

Two process notes for you specifically:
- Two unasserted `str.replace` calls silently no-opped and three processes redundantly ran
  the same arm. Assert presence **and** uniqueness on every patch.
- A worry that `pose_jump_deg` used the wrong rotation convention was **unfounded**: the
  fitter's `Rr @ R` with `Rr = rot(R·e_x, roll)` equals `R @ Rx(roll)` because
  `rot(R a, θ) = R rot(a, θ) Rᵀ`. Identical to 2 dp.

---

## 1. Code changed (production + research)

All with a failing test written first, per `CLAUDE.md`.

| Change | File | Test |
|---|---|---|
| Club path timed on the **sensor clock**, not the callback clock | `src/openflight/server.py` — new `_optical_timestamps_ns(archive)`, host fallback for old archives | `tests/test_server.py::TestOpticalTimestampSource` (2) |
| Attack angle uses **total horizontal speed** | `src/openflight/camera/club_delivery.py::_velocity_angles` → `atan2(vertical, hypot(lateral, forward))`. Club path unchanged — it is an azimuth and was right | `tests/test_camera_club_delivery.py::TestVelocityAngleProjection` (3) |
| Mesh fitter searches the real depth | `research/silhouette_poc/replay/fit_real.py` — `CAMERA_BALL_RANGE_MM = 1581.0`, plate scale derived from it, both `range_grid_mm` recentred | `research/silhouette_poc/tests/test_fit_real_range_grid.py` (3) |

The range-grid test asserts a **property** (every default grid must bracket the range
recomputed from measured rig heights), not a constant — which is how 1425 mm went stale.

**Deliberately not changed, with reasons:**
- `_fuse_camera_ball_flight` still uses host timestamps. It computes
  `relative_time = ts[frame] − trigger_host_timestamp_ns` and feeds that into the radar
  range lookup. The clocks share an epoch but differ by **2.436 ms** mean, so a naive swap
  biases every range lookup by ~110 mm. Needs the affine clock map, not a substitution.
  Its intervals are longer, so jitter costs ~0.4 % there vs 2 % on the club path.
- `PREFERRED_PATH_OFFSETS = ((-2, 1), …)` still crosses impact. Changing which frames define
  "delivered" needs the definition frozen first.
- `detect_face_plane` unfixed — see §2.3.

---

## 2. What is established

### 2.1 Camera geometry — CLOSED

- Camera lens **203.2 mm** above the floor (`mount_height_m` in the kiosk log; maintainer's
  tape 8.1″). Ball centre 40 mm. **The ball is 163 mm BELOW the lens.**
- Measured depression of the ball below boresight: **5.742° ± 0.111°** (21/21 shots).
  Geometric depression from the tape: 5.926°. → **camera boresight pitch −0.185° ± 0.111°.
  The camera is level.** The enclosure's 10.405° tilt is the radar's alone.
- The old "2.73° below boresight" reproduces exactly at **fx = 1033 px**, the A0 preset that
  `fit_real.py:10` already documents as "a camera we do not have". Correct fx is **466.67**
  (nominal 2.8 mm lens / 3.0 µm pitch / 2× subsample). **Not a calibrated camera matrix** —
  no distortion model, no separately estimated principal point, no independent fx/fy.

### 2.2 The 7i/9i launch gap is REAL (falsification test #1)

Camera rays + IWR range walk, gravity-aware, LCMF excluded entirely. Residuals 0.50 px and
12 mm. 20/21 shots (shot_003 fails closed).

| | 7-iron | 9-iron | gap mean | gap median |
|---|---:|---:|---:|---:|
| camera + IWR range | 24.08° | 26.98° | **+2.91°** | **+4.22°** |
| LCMF | 18.94° | 21.53° | +2.59° | +3.60° |

**Gap invariant to a 13° swing in assumed camera pitch (≤0.03°)** and to tee range ±50 mm,
camera height ±½″, ball height, focal ±2 % (≤0.08°). "Dynamic loft compressed to 55 %" stays
dead, now on evidence.

### 2.3 The OPS speed contract — REFUTED as a club-dependent bias (test #2)

`estimate_lcmf_v1` derives two quantities from one input:
- `phase_velocity_ms` → TDM de-rotation. **Wants radial. Gets radial. Correct.**
- `model_geometry["speed_ms"]` → `ballistic_trajectory_from_range` (`vx = v cos L`).
  **Wants total. Gets radial. Wrong.**

Three arms, all 21 dumps, real grid search (control: arm A reproduces the shipped angle to
+0.096° mean / 0.47° max on 20/21; shot 5 differs by +1.98° and is **unexplained**):

| | shift in launch | shift in club gap |
|---|---:|---:|
| ballistic contract alone (the one flagged) | **−0.0095° ± 0.0033°** | **−0.003°** |
| phase contract alone | +1.2439° ± 0.2920° | +0.080° |

**Do not "fix" the phase contract.** It looks like a 1.24° improvement and makes the
estimator worse. The D-plane report's "0.956→0.906 projection factor" is plain `cos(launch)`;
the real factors are **0.976 / 0.969** — overstated ~4×.

**New sensitivity: ∂(LCMF vertical launch)/∂v = +0.913 ± 0.113 °/(m/s)** (21/21, near-linear;
horizontal +0.349 ± 0.190). **A 1 % OPS ball-speed error is 0.41° of launch.** Belongs in the
uncertainty budget.

### 2.4 The mesh frame points out the BACK of the club

`detect_face_plane` (`research/silhouette_poc/generator/mesh_truth.py:342`) **finds** the real
striking face — 2950 mm², aspect 0.621, flatness 0.57 mm, the largest region in the mesh —
then rejects it on the **extremity gate**:

```
projection = vertices @ normal      # ALL vertices, including the hosel
extremity  = min(|centroid·n − min(proj)|, |max(proj) − centroid·n|)
accept if extremity <= max(1.0, 0.10 * ptp(projection))
```

Face extremity **17.31 mm** against a **6.09 mm** limit, because the hosel protrudes 24.23 mm
along the face normal and sets the extremum. Nine regions survive; the winner is the largest
of *those* — the **863 mm² cavity rim**, chosen over a 2950 mm² clubface. It sits **16.36°
from anti-parallel** to the real face.

**Consequences.** Silhouette IoU is unaffected (a mask does not care how axes are labelled),
so detection/tracking stand. **Broken:** every physical reading — dynamic loft reads 17.48°
where the club is 33.10°; the §1.3 physical-loft gate is bounding the *back* of the club
(almost certainly why 37 % of frames "imply impossible loft"); the roll axis is 16.36° off.

**The right fix is not another heuristic.** Ship authored per-club metadata: striking-face
triangle set, face origin, toe/up axes, outward normal, shaft axis. Auto-detection can assist
onboarding; a human-visible render must be the authority.

### 2.5 IoU is anti-correlated with pose correctness

66 pre-impact frames, 21 shots, **byte-identical masks across arms**:

| | A: grid 1300–1550 | B: grid 1456–1706 | C: range pinned 1581 |
|---|---:|---:|---:|
| median IoU | **0.4625** | 0.4401 | **0.3896** |
| median fitted range | 1180 mm | 1336 mm | 1581 mm |
| error vs tape | −401 mm | −245 mm | 0 |
| below their own grid | 78.8 % | 81.8 % | — |
| railed on the ±240 mm refinement | 18.2 % | 25.8 % | — |
| **adjacent pairs jumping >45°** | **50.0 %** | 44.4 % | **33.3 %** |

**The arm with the best IoU has the worst poses.** The pattern held on the earlier
contaminated 144-frame set too, with every absolute number different — that robustness is
why it is a finding. **Stop using IoU as a progress metric**; use pose coherence and range
agreement. Not comparable to the published "349 frames / IoU 0.633": that tracker no longer
exists (`head_split` was never wired into `make_overlay.py`).

### 2.5b The scale degree of freedom exists, is correct, and is driven by noise

Worth stating explicitly because it looks like a missing feature and is not. `_project`
does a proper per-vertex perspective divide (`fx * cam_x / cam_z + cx`), so the mesh scales
with depth automatically, and `range_mm` — refitted independently on every frame — IS the
scale parameter. Nothing about the club growing and shrinking through the swing is
unmodelled.

The problem is that the parameter carries no information. Fitted range across consecutive
pre-impact frames, arm A:

```
shot 5, frames 66 67 70 71 72:  1270 → 1120 → 1550 → 1180 → 1490 mm
```

Over all shots: frame-to-frame range change **sd 185 mm, |max| 430 mm, and 67 % of
consecutive steps change sign** (arm B: 164 mm, 370 mm, 64 %). A clubhead approaching the
ball changes range smoothly and largely in one direction over 3–6 frames. **Sixty-seven
percent sign changes is a coin flip — the fitted depth is uncorrelated frame to frame.**

That ties the section together: the ±138 mm flat basin in range (§2.6a), 79–82 % of frames
landing outside their own grid, 18–26 % railing on the refinement limit, the systematic
245–401 mm shortfall from the 2× area excess (§3.3), and now zero temporal coherence, are
all one fact. **The scale DoF is unconstrained and is absorbing noise.** Pinning it from the
radar (sd 30 mm) is the fix, and arm C shows what that buys: incoherent poses 50 % → 33 %.

For the **ball**, apparent size is a genuine depth cue but currently a poor one: per-shot
implied ranges run 1471–1667 mm against a tape 1581 mm, and the one-sided top-edge bias
(§2.1 of the prior handoff) corrupts it directly. That is why falsification test #1 used the
IWR range walk instead. At 1:1 the ball goes 12.8 → 25.6 px, which would materially improve
it — and unlike the clubhead the ball is slow enough before impact not to pay the blur
penalty.

### 2.5c A swing motion prior — and a free validator the fit has never had

The maintainer's point, and it is the right one: a clubhead does not take an arbitrary new
orientation every 2.1 ms. Through the impact zone it is in rigid rotation, sweeping from
open toward closed at a roughly steady rate about a roughly fixed axis. So do not let the
fit choose three fresh angles per frame.

**`fit_real.fit_sequence` already exists and is a weaker version of this** — physical bounds
(`YAW_BOUND_DEG=60`, `PITCH_RANGE_DEG=(-40,90)`, `ROLL_BOUND_DEG=70`) plus a first-order
smoothness penalty. Two problems. It charges IoU for *any* change between adjacent poses,
which biases toward a club that is **not moving** — the wrong prior for an object whose job
is to rotate. And it still carries 3 free angles per frame. **Note also that every A/B/C
number in §2.5 used `fit_frame_6dof`, the independent-frame fitter. `fit_sequence` was never
tested.** That is a gap.

The stronger form fits one initial orientation and one angular velocity for the whole run,
`R(t) = expm([ω]×(t−t₀)) · R₀` — **6 parameters instead of 3N**, and pose coherence is 100 %
by construction because the motion *is* the model. Tested on the 6 shots with 4+ tracked
pre-impact frames, range pinned:

| | mean IoU |
|---|---:|
| per-frame free orientation (3 angles × n frames) | 0.4280 |
| one rigid rotation (6 parameters total) | 0.3805 |
| **IoU given up for the constraint** | **−0.0475** |

Costing 0.048 of IoU to buy perfect coherence is the same trade arms A→B→C already showed,
taken to its limit — and §2.5 established that IoU is the metric pointing the wrong way, so
paying it is not obviously bad.

**But the fitted rotation rate is the real result, and it fails a check that costs nothing.**
The clubhead's bulk rotation rate is predictable from geometry: it swings on an arc, so
`ω = v / r`. At the session's measured club speed of **36.6 ± 1.5 m/s** on a 1.4–1.8 m arc
that is **1170–1500 °/s**. The rigid fit recovers a median of **508 °/s** — a ratio of
**0.39**.

So the prior finds *a* self-consistent motion, but only about 40 % of the rotation the club
must physically be doing. **That is a truth-free external validator the silhouette fit has
never had**, in the same spirit as the smash-factor test in §10c of the public page: it
needs no reference instrument, it runs on data already in hand, and the current fit fails
it by a factor of 2.5. It should gate any future pose work — and unlike IoU, it cannot be
gamed by choosing a better-overlapping wrong pose.

Recommended next step on this thread: fit the sequence with **ω constrained to the
geometric prediction** (magnitude from `v/r` with the measured club speed, axis free), so
the remaining freedom is the orientation and the swing-plane direction. That is 5 parameters
for a whole run, and it converts a validator into a constraint.

### 2.6 The silhouette cannot see face angle — three independent routes agree

**(a) Objective shape**, 66 frames swept around each frame's own optimum:

| DoF | half-width within 5 % of peak | IoU left at ±10° |
|---|---:|---:|
| yaw = **face angle** | **±11.3°** | 96.0 % |
| pitch = **dynamic loft** | **±13.8°** | 96.1 % |
| roll = lie/toe-up | ±10.0° | 95.1 % |
| range | ±138 mm | 94.1 % at ±100 mm |

Rotating the clubface 11° costs 4 % of the objective — less than frame-to-frame IoU noise.
**It is not the fitter; the cue does not contain the information.** This is what the 45° pose
jumps *are*, and why the last few percent of IoU are bought by moving along blind directions.

**(b) Shaft leverage** (hypothetical 900 mm shaft; the model has none):

| rotation | shaft image direction | face normal |
|---|---:|---:|
| roll (lie) | **0.96 °/°** | 0.34 |
| pitch (loft) | 0.27 | **0.99** |
| yaw (face angle) | 0.19 | **0.95** |
| about the shaft axis | **0.00** | 0.84 |

The rotations the shaft sees best move the face least, and it is blind by construction to
rotation about its own axis — which swings the face normal on a 56.8° cone, i.e. *is* face
angle and loft. Still worth building: a 900 mm line's direction is measurable to ~0.5°, so
0.19 °/° gives ~±3° on face angle, and it collapses an ill-conditioned 3-D search to 1-D.

**(c) Foreshortening** (second-moment ellipse of the projection):

| rotation | major axis | ellipse orientation |
|---|---:|---:|
| yaw (face angle) | 0.089 px/deg | 0.11 °/° |
| pitch (loft) | 0.053 px/deg | 0.17 °/° |
| roll (lie) | 0.022 px/deg | **1.00 °/°** |

Head is 23.6 px heel-toe, edge noise ~0.5 px → **6–11° of yaw resolution**, matching (a)
independently. Also sign-ambiguous (cos is even). **All three routes say the same thing:
this vantage gives lie well and face angle badly.**

**The toe-tip idea is a trap.** The outermost silhouette point is a *tangent extremum*, not a
body point — it slides across the surface as the club rotates, injecting error that grows
with the rotation being solved. The June spec
`2026-06-29-club-pose-stage0b2-keypoint-pnp-impact-location-design.md` excludes such extrema
for exactly this reason and predicted this outcome. The hosel **endpoint** measured *worst of
four* reference points (4.90 px vs 1.71 px centroid). The shaft's **direction** is stable;
its **endpoint** is not.

### 2.7 Resolution, and the coupling that decides it

Face-angle leverage does **not** vanish at square — it weakens ~4×:

| face angle open | 0° | 2° | 5° | 10° | 20° | 30° |
|---|---:|---:|---:|---:|---:|---:|
| px per degree | 0.059 | 0.074 | 0.113 | 0.154 | 0.199 | 0.228 |
| resolution @0.5 px | 8.5° | 6.8° | 4.4° | 3.3° | 2.5° | 2.2° |

Requirement (5° open, 0.5 px floor), and what the hardware reaches:

| configuration | px/mm | head | face angle | field |
|---|---:|---:|---:|---:|
| today — 320×200, 2× subsampled (¼ of sensor area) | 0.295 | 23.6 px | 4.41° | 1.08 m |
| **1:1 full frame 1280×800, same lens** | 0.590 | 47.2 px | 2.21° | **2.17 m** |
| 1:1 + 6 mm lens | 1.265 | 101 px | 1.03° | 1.01 m |
| 1:1 + 12 mm lens | 2.530 | 202 px | 0.52° | 0.51 m |

**1:1 full frame doubles plate scale AND field of view at once**, because today's mode
discards three quarters of the sensor. Cost: 16× the data → ~144 fps instead of 468.

**But blur scales with plate scale, so pixels cost light one-for-one** (model validated:
predicts 2.7 px today vs 2.97 px measured):

| configuration | blur @300 µs | exposure for 1 px | light needed |
|---|---:|---:|---:|
| today | 2.7 px | 113 µs | 2.7× |
| 1:1, same lens | 5.3 px | 56 µs | 5.3× |
| 1:1 + 6 mm | **11.4 px** | 26 µs | **11.4×** |

**Under the no-strobe rule the binding constraint is the light budget, not pixels.**

### 2.8 Radar club angles are not measurements

- Raw attack angle **rejected 21/21** (`candidate_out_of_bounds`); medians −29.3° (7i),
  −33.2° (9i) — physically impossible.
- Club path and `rejected_phase_span` fail **21/21**.
- Fused AoA looks sane on 19/21; shots 20 (−30.98°) and 25 (+25.23°) are gross failures.
- **Failure clustering (test #9): no shared cause.** Variance of flags-per-shot 0.662 vs
  0.848 expected under independence (ratio 0.78, slightly sub-Poisson); strongest pairwise
  correlation 0.45 between two flags firing on two shots each. Three flags are *universal*,
  not clustered.

### 2.9 The two-axis camera/radar disagreement — the largest unexplained quantity

- **Vertical:** camera − LCMF = **+5.34° ± 0.83°** on 20/20 shots; regression
  `camera = 0.954 × LCMF + 6.28` (r = 0.971) — a near-pure offset.
- **Horizontal:** the shipped pipeline's own `experimental_camera_iwr_delta_deg` =
  **+5.10° ± 1.09°** (18 shots).
- My camera chain is validated: it matches the **shipped** `ball_flight.py` estimator to
  **+0.064° ± 0.199°, r = 0.998**.
- **Neither sensor can arbitrate the other.** The camera's datum is measured (§2.1); LCMF's
  is a corner-reflector tilt plus elevation DOA, and its launch scales **1:1** with that
  tilt. Test 1c (launch angle from raw slant-range curvature alone) is **too
  ill-conditioned to referee**: within-club scatter 12.8°/17.9°, formal σ understates by
  >10×. Not reported as a result.
- **No inclinometer ran in this session.** The radar's `tilt_deg: 10.405` is a static
  constant from a 2026-07-12 corner-reflector solve, never checked against the kickstand
  angle on the day — while the camera's pitch *is* derived per shot from the teed ball
  (`club_delivery.py:253`). A few degrees of tilt drift reproduces the whole offset.

---

## 3. Open problems, ranked

1. **Club path / attack angle rejected 21/21.** This is the unlock. It blocks the D-plane,
   blocks the ball-derived face angle (below), and is the channel whose `rejected_phase_span`
   is unanimous. Nothing else in the system supplies a path.
2. **The ~5.3° two-axis camera/radar disagreement.** Cheapest decisive measurement: a target
   at a tape-known position visible to both sensors. Turn the inclinometer on and level the
   camera from it as well; note it fixes pitch/roll only — **gravity carries no yaw**, so the
   horizontal 5° is untouched by it.
3. **The 2× area excess in the mesh fit**, and the unconstrained scale DoF behind it (§2.5b). The model covers only **43–55 %** of observed
   pixels at true range, which is why the fit pulls range in by 245–401 mm. Quantify in
   order: shaft leakage in `split_head` (the observed mask runs far past where the hosel
   ends, and **the model has no shaft**), then motion blur (~9 mm of head travel per
   exposure), then the mesh's own dimensions.
4. **`detect_face_plane` anchors to the cavity rim** (§2.4). Fix via authored club metadata.
5. **The +9.13° horizontal reproducibility gap.** `estimate_lcmf_v1` at HEAD with documented
   defaults returns a horizontal angle a near-constant **+9.13°** (sd 0.007° across 21 shots)
   from the shipped value. Ruled out: `net_range_m`, `tdm_sign_policy`, `azimuth_offset_deg`
   (0), `horizontal_phase_reference_rad` (unset in CLI and kiosk defaults), code drift (no
   commits since capture). Vertical reproduces to 0.1 %; horizontal does not. **Do not trust
   any camera/radar horizontal comparison until this has a cause.**
6. **The light budget is unmeasured.** How many lux in the bay, and what exposure does usable
   SNR need at 1:1? That number decides whether the optical route is a 1° or a 4° instrument.
   No further analysis of existing footage can answer it.
7. **Test the sequence fitters properly** (§2.5c). `fit_sequence` has never been run in
   this session's harness, and the rigid-rotation prior currently recovers only 39 % of the
   geometrically required rotation rate. Gate future pose work on that ratio, not on IoU.
8. Falsification tests **3, 4, 6, 7, 8, 10, 11** unrun; **12** needs new data. #10 (same-point
   consistency) bears directly on the "one radar range applied to every optical feature"
   defect and is the one the Codex audit independently asked for.

---

## 4. The strategic fork

Two routes to face angle, and they are complementary rather than competing.

| route | reaches | cost |
|---|---:|---|
| **ball direction + club path** | **0.9–2.3°** | no new hardware. `face = (ball_dir − 0.31·path)/0.69`, the project's own 69/31 split. Camera ball direction is reproducible to 0.199°, so path known to 5° → 2.26°, to 2° → 0.94°. **Blocked on #1 and #2.** |
| **silhouette at 1:1 + 6 mm lens** | **1.0°** | a readout mode and a lens — plus **11× light**, FOV → 1.01 m, ~144 fps. Needs no club path. |

**Even a 5° club-path error beats the silhouette by 5×.** The optical route reads a quantity
the image barely encodes; the ball route reads one it encodes extremely well and converts it
with a known coefficient.

**Face angle is not impact location.** Toe–heel and high–low need the face origin and axes,
i.e. full 6-DoF head pose plus per-club face registration. From ball data alone the strike
can only come from speed loss (a radial distance, no direction), gear effect (an iron's CG is
~5 mm behind the face vs ~35 mm on a driver, and spin is not measured at all — `spin_rpm`
reproduces the project's own kinematic formula to 0.00 rpm on all 22 shots), or
launch-vs-dynamic-loft (needs attack angle, rejected 21/21). **Scope it as face angle + path
+ coarse zones, not a millimetric strike map.** The June spec reached this fork from
simulation; this session reached it from measurement.

---

## 5. Unfinished — one experiment in flight

`test_resolution_scaling_check.py` was still running when this was written. It tests the one
assumption every resolution number in §2.7 rests on: that leverage scales linearly with plate
scale and the edge-noise floor stays put. Both were asserted from clean mesh renders, never
checked against real segmented edges.

It halves the resolution of the existing footage and re-measures the flat basin in yaw on
real masks. **A ratio near 2.0 confirms the extrapolation upward; a ratio near 1.0 means
something other than pixel count sets the floor and more pixels will buy less than
predicted.** Worth finishing before any lens is bought — the answer is a genuine risk to the
§2.7 recommendation, and blur landing on the very axis the measurement uses is the specific
mechanism to worry about.

---

## 6. Conventions that must survive

- **Render it and look before trusting a number.** Twice this session it was decisive.
- Overlays draw the model's own output. Never pad an outline. If it looks wrong, the fit is
  wrong.
- **Fail closed.** Never conclude "X does not exist" from a truncated search.
- Comparators are **Trackman 4, Full Swing KIT, Mevo Gen 2. Never Trackman iO.** TM4 does
  markerless impact location on a 60 fps camera — but it runs *two* radars, so it is
  commercial precedent for the architecture's shape, **not** evidence that this sensor suite
  suffices.
- **No strobe.** Ambient light only.
- Verify a geometric assumption on the mesh or the data before building on it.
- One shot is diagnostic, not validation. Run the cross-set check before reporting.

## 7. Files

- `research/silhouette_poc/falsification/` — every script and log from this session
  - `test1*_` falsification test 1; `test2*_` test 2; `test5_9_` tests 5 and 9
  - `test5q2*_` the mesh face-plane investigation; `render_mesh_views.py` the render that
    settled it
  - `test_meshfit_depth_ab.py` + `meshfit_arm_{A,B,C}.json` the three-arm fit
  - `render_fit_overlays.py` + `renders/` the fit overlays
  - `test_dof_sensitivity.py`, `test_shaft_leverage.py`, `test_stub_and_foreshortening.py`,
    `test_resolution_requirement.py`, `test_resolution_scaling_check.py`
- `docs/superpowers/specs/2026-08-26-falsification-results.md` — the full write-up
- `docs/superpowers/specs/2026-08-26-agent-handoff.md` — prior handoff, §0 still applies
- `openflight_claude_artifact_dplane_audit.md` (repo parent) — the Codex audit; its four code
  defects were verified and three fixed (§1)
- Public page: <https://claude.ai/code/artifact/42a6f3f4-0b9b-4faf-bf9c-1ff45b4e94dd>
  — sections 11c–11i are this session's work, with the §0 retractions applied in place
