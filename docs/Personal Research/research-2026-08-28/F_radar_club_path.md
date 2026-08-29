# IWR6843 radar-only club path / attack angle: why it fails, and whether it's fixable

All file:line references are to `c:\Users\harjo\Desktop\Coding\OpenFlight\openflight` as of 2026-08-28.
No repo files were modified.

---

## 1. The computation, traced from cube to rejection

`estimate_club_path` (`src/openflight/iwr6843/club.py:610-917`) runs two largely independent
sub-pipelines on the same shot: a **range track** (used for both path and attack angle as the
"where/how fast" input) and a **phase/angle** extraction (used as the "which direction" input).
`rejected_phase_span` is the gate on the horizontal (club-path) sub-pipeline; the attack-angle
sub-pipeline is a separate function with its own, different, gate.

### 1a. Range track (shared)

- `project_tx_pair(raw, (0, 2))` (`club.py:641`, defined `dump.py:434-473`) keeps only physical
  TX1/TX3 (indices 0 and 2 — the *vertical* pair), discarding physical TX2 (index 1, the
  *horizontal* baseline element) from this cube.
- `tracking.mti_filter(cube, ...)` (`club.py:650`, `tracking.py:147-191`) reshapes the resulting
  2‑TX cube as `(frames, 2, loops, rx, bins)` (`tracking.py:166`) and range-FFTs + static-removes
  it. Because `project_tx_pair` already reduced to 2 TX, `mti_filter`'s hard-coded `// 2` reshape
  is **not** a mismatch here — this is a red herring from the facts block; the two are consistent
  for this call. Default `scope="burst"` subtracts each bin's mean over the loops of *one frame*.
- `find_club` (`club.py:335-397`) RANSACs a range-vs-time line on this MTI cube, gated to
  `(tee_range_m − 0.6, tee_range_m + 0.05)` and to a radial-speed window derived from the OPS243
  club-speed reading (`ops_speed_prior` mode) or a broad fallback. This produces a `BallTrack`
  with `slope_bins`/`quad_bins`.
- `BallTrack.speed_ms_at(t, res)` (`tracking.py:133-145`) returns a **quadratic-refit local
  radial speed** (`np.polyfit(times[inliers], bins[inliers], 2)`, `tracking.py:365-367`, capped at
  `MAX_RADIAL_ACCEL = 200 m/s²`) rather than the single track-average slope — this is the value
  used everywhere downstream as "the club's instantaneous radial speed."

### 1b. Horizontal phase (path) sub-pipeline — where `rejected_phase_span` comes from

`estimate_club_path` re-parses the **raw 3‑TX** cube (`club.py:741`, `_meta3, cube3 =
parse_dump(raw)`) and reshapes it to `(frames, loops, 3, rx, bins)` (`club.py:745`). It then does
its own, hand-rolled static-clutter removal — **not** `tracking.mti_filter`:

```
rfft = tdm if is_range_snapshot(_meta3) else np.fft.fft(tdm, axis=-1)   # club.py:750
tdm = rfft - rfft.mean(axis=1, keepdims=True)                          # club.py:751
```

`axis=1` is the **loop** axis, so this subtracts the per-*frame* (per-burst) mean over loops —
exactly the `scope="burst"` MTI mode that `tracking.mti_filter`'s own docstring (`tracking.py:
156-160`) says **notches** a mover whose loop-to-loop phase is ≈0 mod 2π, i.e. whose radial speed
is near a multiple of `λ/(2·T_loop)`. See §2 for why this matters here.

Per `(frame, loop)`, for every retained sample:

- `doa.tx2_phase_at` (`doa.py:296-331`) / `doa.tx2_reference_phases_at` (`doa.py:334-373`) compute
  the TX2-vs-(TX1,TX3) residual phase. Radial motion **is** compensated here, per snapshot, using
  the track's *local* speed:
  ```python
  tx2 = tdm[frame, loop, 1, :, local_bin] * np.exp(-1j * tdm_sign * 4*pi*velocity_ms*TDM_TAU_S/LAM)   # doa.py:314-316
  tx3 = tdm[frame, loop, 2, :, local_bin] * np.exp(-1j * tdm_sign * 4*pi*velocity_ms*TX2_VERTICAL_TDM_TAU_S/LAM)  # doa.py:317-319
  ```
  called with `velocity_ms=phase_track.speed_ms_at(t_s, res)` (`club.py:779, 795`) — the
  **radar's own** quadratic-refit local radial speed from §1a. **OPS243 club speed is used only
  to select/gate the track (`find_club`), never to de-rotate the TDM phase.**
- `experimental_path_candidate` (`club.py:522-607`, debug-only) and the main-loop azimuth
  (`club.py:849-875`) convert the resulting wrapped phase to an axis angle via
  `doa.tx2_phase_to_axis_angle_rad` (`doa.py:42-61`, `angle = arcsin(phase/π)` for the λ/2 TX2
  baseline) and fit `x(t)`, `y(t)` linearly for `v_x, v_y`.
- `phase_span_rad` (`club.py:315-332`) takes each frame's **circular median** of this
  already-motion-compensated phase and sums the wrapped step-to-step differences across frames —
  deliberately *not* `np.unwrap` (module docstring `club.py:1-28`, test
  `tests/test_iwr6843_club_phase.py:66-83`, because an unwrapped λ/2 baseline invents angles
  outside ±90°).
- `estimate_club_path` rejects with `rejected_phase_span` when `phase_span_rad >
  CLUB_MAX_PHASE_SPAN_RAD = π/2` (`club.py:846-847, 900-902`), the gate itself set from a physical
  ceiling: "tangential speed ≤ ~20 m/s at ~1.1 m ⇒ azimuth rate < ~18 rad/s ⇒ ≤ ~0.44 rad azimuth
  ⇒ ~1.3 rad of λ/2 phase over six 4 ms frames" (`club.py:113-118`).

**Measured:** `phase_span_rad` = 2.18–3.91 rad against an expected ceiling of ~1.3 rad (gate at
π/2 = 1.571). The measured value is 1.4–2.5× the *gate*, and 1.7–3× the *physical* ceiling.

### 1c. Vertical/attack-angle sub-pipeline — separate function, separate gate

`estimate_attack_angle_candidate` (`club.py:453-519`) is unrelated code: it uses the vertical
TX1/TX3 8‑element aperture (via `doa.angle_points` → `music.est_music_fbss_high`, the "pick the
higher of two resolved MUSIC peaks because the floor image is always below the direct arrival"
rule, `music.py:80-108`, hardware-validated **on the ball**), then fits height-vs-range with
`trajectory.fit_tee` (`club.py:506`, `trajectory.py:71-96`).

`fit_tee` **forces the line through the ball's own tee anchor**:
```python
x_0, h_0 = cal.tee_range_m, cal.tee_anchor_h_m                 # trajectory.py:86
slope = sum(d_x*(h_m-h_0)) / sum(d_x*d_x)                      # trajectory.py:89
```
`cal.tee_anchor_h_m = cal.tee_ball_height_m - cal.radar_height_m` (`calibration.py:47-49`,
defaults `0.04 m − 0.152 m = −0.112 m`) — this is the **ball's** resting height at the tee, in
radar-plane coordinates. `estimate_attack_angle_candidate` passes the club's own approach-window
height points into this same ball-anchored fit (`club.py:506`), over a window of only
`ATTACK_PRE_IMPACT_FRAMES = 4` frames plus the impact frame (`club.py:79, 441-450`) — i.e. a short
`d_x` baseline near impact.

---

## 2. Verdict on the mechanisms

### 2a. TDM/aliasing arithmetic, with the repo's real constants

`LAM = C/F_C = 299792458/62e9 = 4.8354 mm` (`music.py:10-12`). `TDM_TAU_S = 45 µs`,
`TX2_VERTICAL_TDM_TAU_S = 90 µs`, `TX2_LOOP_PERIOD_S = 3×45 µs = 135 µs` (`doa.py:31-32`,
`shot.py:24`) — this **is** the "loop period 135 µs" in the facts block, confirmed from code, not
assumed.

At v = 33 m/s (measured range-rate), the two-way range-migration phase over one TDM hop is:

- TX1→TX2 (45 µs): θ = 4π·v·τ/λ = 4π·33·45e‑6/4.8354e‑3 = **3.86 rad** (1.23π)
- TX1→TX3 (90 µs): θ = **7.72 rad** (2.46π)

**Both are already several multiples of π before any compensation is applied.** This is not
itself a bug — `tx2_phase_at`/`tx2_reference_phases_at` explicitly pre-multiply by
`exp(−j·θ)` using the *measured* local velocity to cancel this term (`doa.py:314-319, 354-359`),
and `exp(−jθ)` correctly removes any multiple of π as long as the velocity used is right. **But
the correction's sensitivity to a velocity *error* is exactly proportional to τ:**

```
dθ/dv = 4π·τ/λ
  τ=45 µs (TX1↔TX2):  0.117 rad per (m/s) of velocity error   ( 6.7°/m/s )
  τ=90 µs (TX1↔TX3):  0.234 rad per (m/s) of velocity error   ( 13.4°/m/s )
```

The velocity used is `BallTrack.speed_ms_at` — a **quadratic refit to a RANSAC-selected,
4.69-cm-quantized range walk over an 8–24 ms window that spans real club acceleration through
impact** (`tracking.py:133-145, 361-367`). A 1–2 m/s local error in that refit (plausible given
range-bin quantization noise, short span, and non-quadratic acceleration/jerk right at contact) is
not a stretch, and produces **0.23–0.47 rad (13–27°) of reference-phase error on the TX3 side per
loop**. If that error *drifts* across the ~6-frame window — which it will, because a
finite-order polynomial fit necessarily has growing residual toward the ends of a window covering
a real, non-polynomial acceleration event (impact) — it directly inflates `phase_span_rad`,
which is a sum of frame-to-frame phase *differences*. This mechanism is **INFERRED** (the
sensitivity numbers are CONFIRMED arithmetic from real constants; the actual magnitude of the
velocity-fit error on real data is not measured here) but it is large enough on its own to explain
the observed 2–4× overshoot.

### 2b. A second, independently strong candidate: the per-burst MTI notch (new finding)

The horizontal path's own static-clutter removal (§1b, `club.py:750-751`) subtracts the mean
**over loops within one frame** — the `scope="burst"` mode that `tracking.mti_filter`'s docstring
says notches a target whose loop-to-loop phase is ≈0 mod 2π, i.e. whose radial speed sits near a
multiple of:

```
v_notch = λ / (2·T_loop) = 4.8354e-3 / (2 × 135e-6) = 17.91 m/s
  → notches at 17.9, 35.8, 53.7, 71.6 m/s ...
```

**The second notch, 35.8 m/s, sits inside the measured club radial-speed band (24.7–37.9 m/s,
and specifically the 33–39 m/s range reported for this 7i/9i session).** Any shot whose radial
speed lands within a few m/s of 35.8 m/s has its own return partially cancelled by this MTI step,
along with the static clutter — leaving a phase estimate dominated by residual clutter/noise
rather than the clubhead. This is **CONFIRMED by arithmetic from the actual code constants** and
sits squarely in the failure regime; it was not one of the two hypotheses in the facts block and
is a concrete, cheap thing to check (§4). It is a plausible *sole* explanation for why the
horizontal path is broken on essentially every shot in a narrow club-speed session, independent of
any assumption about clubhead rotation.

### 2c. Scatterer-migration / rotation hypothesis

Cross-range cell at 1.25 m is 277 mm (19.3 mm aperture, `doc: docs/clubface-impact-location-
report.md:243-266`); the clubhead is ~90 mm, 0.33 cells — it cannot be resolved as an extended
object by the real aperture. The report's own ISAR check found the clubhead's Doppler width
(1.95 bins) **is** broader than a point target (1.27 bins) by close to the rotation-predicted
amount (1.36 bins), but **the speed-scaling discriminator has no statistical power on this
7-iron/9-iron-only session** (predicted effect 0.154 bins vs 0.49 bins of scatter, club type
perfectly confounded with speed) — this is stated directly in
`docs/clubface-impact-location-report.md:243-276` and is **UNKNOWN**, neither confirmed nor
refuted. It remains a live but *unproven* contributor, additive to (not exclusive of) §2a/§2b.

### 2d. What a fixed processing bug should look like — and does the data match?

A systematic, swing-independent bias is the classic signature of a fixed geometric/model error,
not of noise. `phase_span_rad` (the path gate) is a *magnitude*, not a signed bias, so it cannot
by itself show this; but **attack angle can**, and does: −25.3° to −37.3° (sd 2.8°) on every shot
regardless of true swing (true ≈ −4° for a 7-iron), per `docs/clubface-impact-location-
report.md:229-241` and the per-club breakdown in `camera-club-path-aoa-accuracy-2026-08.md`
(7‑iron median −29.1°, 9‑iron median −32.5°, only 3.4° apart despite being different clubs/swings).
**This is CONFIRMED to have a structural cause, independent of §2a/§2b**: `fit_tee` forces the
club's height fit through **`cal.tee_anchor_h_m`, the ball's own tee-height anchor**
(`calibration.py:33-49`, `trajectory.py:86-89`), reused unmodified from the ball's launch-angle
estimator for club data (`club.py:506`). The clubhead's true height during the ~8–16 ms
attack-angle window is set by swing mechanics near the ground, not by the ball's resting height —
any near-constant offset between the two, divided by the necessarily short `d_x` baseline this
close to impact, is *amplified* into a large, nearly shot-invariant slope by construction of the
forced-intercept fit (`slope = Σ(d_x·(h_m−h_0))/Σd_x²`, with both numerator and denominator small
and comparable in size near impact). This alone plausibly explains the tight clustering and the
sign (club height below the ball's tee anchor); the exact magnitude cannot be pinned down without
replaying real `.l3dump` data (not available in this session — see §4), so it is tagged
**INFERRED** (structural mismatch is CONFIRMED from code; magnitude match is a hypothesis).

**Bottom line:** the phase-span rejection (path) most likely stems from §2a and/or §2b — both
CONFIRMED-arithmetic, testable without new hardware. The attack-angle pinning has a *different*,
also CONFIRMED-structural, cause (§2d) that has nothing to do with phase aliasing. Scatterer
migration (§2c) is real signal in the data but is not shown to be the dominant cause of either
failure.

---

## 3. Can the ball's `hlcmf_v1` pipeline (accepted 22/22) be reused for the club?

No, and the reason is structural, not a missing wire-up. `hlcmf_v1`/LCMF (`lcmf.py`) fuses
**many range/time samples over the ball's much longer, near-ballistic flight** (tens of ms,
larger geometric baseline, and a trajectory model — gravity, floor multipath, tee anchor — that is
*correct* for a flying ball) against an **external, unambiguous speed reference** it is designed
to trust. The club's pre-impact window is short by comparison (`PRE_IMPACT_FRAMES = 6`,
`ATTACK_PRE_IMPACT_FRAMES = 4`) precisely because the clubhead is only inside the tee-region gate
for a few ms before it must stop being tracked (it cannot be seen beyond the ball,
`CLUB_APPROACH_DEPTH_M` comment, `club.py:53-58`). Two direct consequences:

1. `fit_tee`'s ball-height anchor (used by `lcmf_v1`'s attack-angle sibling call and reused as-is
   by the club estimator, §2d) is *correct* for the ball (whose height at the tee **is** the
   anchor) and *wrong* for the club (whose height is not the ball's height). This is exactly why
   reusing it, rather than porting the underlying idea, breaks: the shared function assumes the
   thing being fit passes through the tee point, which is true of ball flight and false of club
   approach.
2. `hlcmf_v1`'s coherence/robust-combine machinery (the `two8`/`four4_path_tdm` channel selection
   described in `docs/superpowers/specs/2026-07-25-iwr6843-club-path-design.md`) depends on having
   enough independent snapshots and enough angular travel to check channel agreement; the club's
   4–6 frame window and sub-cell azimuth motion (§1b's own ~1.3 rad *physical* ceiling) leaves too
   little travel for an analogous agreement check to be meaningful.

The right move is not "call `hlcmf_v1` on club data" but to **port the specific idea that does
generalize** — anchoring the fit to a known point rather than using an unconstrained slope — using
an anchor that is actually true for the club (e.g., the *impact* range/bin from the shared
`ClubRangeEvidence.impact_t_s`, which the range-track pipeline already computes to ≤0.20 m
error, `CLUB_MAX_IMPACT_ERROR_M`, `club.py:108-111`) instead of the ball's tee-height anchor.

---

## 4. Concrete, existing-data fix candidates and their expected precision

All four can be evaluated **offline on the 22 `.l3dump` files with no new hardware**, by adding
diagnostic instrumentation to a replay script (not shipped in this pass, since repo files were not
to be modified).

| # | Fix | Mechanism addressed | Test on existing 22 dumps | Expected precision if it works |
|---|---|---|---|---|
| 1 | **Stop per-burst MTI on the horizontal path**: change `club.py:751` from `rfft.mean(axis=1, ...)` to a `scope="window"` mean (subtract the mean over *all* loops/frames in the pre-impact window, as `tracking.mti_filter(scope="window")` already does for exactly this reason, `tracking.py:161-163, 189-190`) — or skip static removal for this fast-closing, high-SNR (60–1000× frame-median power per the design spec) target entirely. | §2b notch at 35.8 m/s | Re-run `estimate_club_path` on all 22 dumps with only this line changed; compare `phase_span_rad` before/after. **Falsifiable in one afternoon.** | If §2b is the dominant cause: phase span should drop toward the ~1.3 rad physical ceiling on most shots, i.e. potentially into the accept gate. |
| 2 | **OPS-speed-assisted / range-rate-based de-rotation sanity check**: recompute the TDM correction using the *linear* (non-quadratic) `track.speed_ms` and separately using an OPS-anchored speed (deprojected via the already-measured radial/OPS speed ratio, `CLUB_SPEED_PROJECTION_RANGE = (0.70, 1.20)`), and compare `phase_span_rad` and the fitted path across all three velocity sources. | §2a velocity-error sensitivity | Three replays per dump, same 22 dumps; look at whether `phase_span_rad`'s sensitivity to the velocity source tracks the `dθ/dv` numbers in §2a. | If velocity error dominates, span should shrink noticeably when a more stable (e.g. OPS-anchored) velocity replaces the quadratic radar-only refit; if it doesn't move, §2a is not the dominant term and §2b/§2c should be weighted higher. |
| 3 | **3‑TX processing / drop the wasted TX2 vertical redundancy**: no code change needed to test — the raw 3‑TX cube is already parsed (`club.py:741`); confirm the vertical range-track uses only TX1/TX3 (`project_tx_pair(raw,(0,2))`, `club.py:641`) while TX2 carries the sole horizontal information, so there is no unused-TX headroom to add here beyond what already ships. This closes out the facts block's "hard-coded 2‑TX" concern as a non-issue for this call path (§1a). | Clears a false lead | n/a — code-reading result, not a data test | n/a |
| 4 | **Anchor the attack-angle fit at impact range, not the ball's tee height**: replace the `fit_tee` call in `estimate_attack_angle_candidate` (`club.py:506`) with a fit anchored at the club's own measured impact range/height (from `ClubRangeEvidence`), analogous in spirit to `fit_tee` but with a club-correct anchor, or fall back to `fit_free` (`trajectory.py:55-68`, unconstrained) restricted to the same 4–5 points and sanity-checked against the range track's own extrapolated impact position. | §2d ball-anchor mismatch | Re-run `estimate_attack_angle_candidate` on all 22 dumps with the anchor swapped; check whether the −25…−37° cluster moves toward the −2…−9° band the *fused* camera+radar attack angle already reports on 19/21 shots. | If §2d is the dominant cause, this alone could bring attack angle into a physically plausible band; residual noise floor is still set by the vertical aperture's precision (see below). |

### The physical floor, even after all four fixes

Aperture = 19.3 mm (8 virtual elements at λ/2, `docs/clubface-impact-location-report.md:243-266`).
A λ/2-baseline azimuth measurement's angular sensitivity near boresight is
`dθ_phys ≈ (λ/(2πD))·dφ` for a two-element-equivalent baseline `D`; for the horizontal path the
*effective* baseline is one λ/2 element pair (TX2 vs. TX1/TX3 midpoint), not the full 19.3 mm
8‑element aperture — the horizontal estimate is fundamentally an **interferometric single-baseline**
measurement (`tx2_phase_to_axis_angle_rad`, `doa.py:42-61`: `angle = arcsin(phase/(π))` for
`D=λ/2`), so its noise floor is set by per-snapshot phase SNR, not by the 8-element aperture at
all. Over the observed **~1.3 rad of *true* physical phase span across ~5–6 frames**, if
per-snapshot phase noise is, say, 0.05–0.1 rad (a plausible per-RX circular-median residual at the
SNRs quoted for this target, 60–1000× frame-median power), a weighted linear fit over ~24–72
snapshots (`CLUB_MIN_SNAPSHOTS = 24`) reduces that by roughly `√N ≈ 5–8×`, giving a *velocity*
(hence path-angle) precision in the same ballpark as the module's own synthetic-fixture number:
**±0.3° across ±12°** (`docs/superpowers/specs/2026-07-25-iwr6843-club-path-design.md`, "Measured
precision... is ±0.3° across ±12°"). That figure is a **noise floor under the idealized synthetic
fixture** (clean single point-target motion, no notch, no velocity-compensation error, no
scatterer extent) — it is **not** achievable until §2a/§2b/§2c are resolved or shown negligible.
Attack angle inherits the same interferometric floor plus whatever residual floor-multipath
ambiguity remains once §2d's anchor is fixed; expect a floor closer to **1–2°**, similar to the
ball's own vertical estimator's demonstrated 0.7–0.8° after per-installation tilt calibration
(cited in `openflight_dplane_hardware_followup_research.md §7.3`), since both draw on the same
sparse 2-effective-position elevation geometry.

---

## 5. Comparison to the camera-fused estimator, and recommendation

`camera/club_delivery.py::estimate_chained_delivery` (`club_delivery.py:889ff`) fuses camera
transverse motion with the **same radar range/range-rate evidence** (`range_evidence:
ClubRangeEvidence`, produced by `estimate_club_path` regardless of its own path/attack-angle
verdict — see `club.py:679-683`) rather than radar phase. It currently gives plausible attack
angle (−2.6…−6.6°, 19/21 shots per `2026-08-26-falsification-results.md:299`) and path (+3…+9°,
17/21 per the task facts). Its known, quantified error sources (`camera-club-path-aoa-
accuracy-2026-08.md`):

| Source | Type | Magnitude | Fixable how |
|---|---|---|---|
| Lens distortion, uncorrected | systematic | ~2.7° (mode-dependent, corrected estimate pending in that doc's §5) | one-time checkerboard `k1/k2` calibration + undistort |
| Two-point velocity differencing in some call sites | random | ~0.65° (vs. 0.43° with a window fit) | fit over the pre-impact window as `club.py` already does — "the radar side already learned this" (`camera-club-path-aoa-accuracy-2026-08.md §2`) |
| Requires ambient light ≥ SCENE_P995_MIN | availability | fails in low light (camera-dependent, not swing-dependent) | none — physical limit of a passive camera |

**Radar-only, uncorrected today**: fails 21–22/22 (path) and produces a physically impossible,
systematic attack angle on every shot. **Radar contributes real, validated information the camera
alone lacks** — range and range-rate (fused |v| matches OPS243 club speed to a mean ratio of 0.970,
sd 0.029, per `docs/clubface-impact-location-report.md §07`) — and that contribution is *already*
what the camera-fused estimator uses; the radar's *phase/angle* channel is the only piece that is
currently unusable.

**Recommendation:**

- **Short term (now):** the camera+radar chained estimator should keep carrying club path and
  attack angle for the D-plane face-angle derivation. It already works on 17–19/21 shots at
  plausible magnitudes, its error sources are quantified and each has a known, cheap fix (lens
  calibration, window fit), and it does not depend on resolving an open radar-DSP question.
- **Radar-only path/attack angle should not be promoted to primary status until:**
  1. Fix candidate #1 (§4) is tried and `phase_span_rad` is re-measured on the 22 dumps — this is
     the cheapest, highest-leverage single test and should be done first;
  2. Fix candidate #4 (§4) is tried on attack angle specifically, since its cause (§2d) is
     independent of the phase-span question and currently the more clearly diagnosed of the two;
  3. If both close the gap to something near the ≤π/2 gate and the −2…−9° physical band
     respectively, the scatterer-migration question (§2c) becomes worth resolving with a
     wide-club-speed-range session (driver + wedge, no speed confound) before trusting any
     rotation-sensitive derivative of the signal.
- **Radar-only would only become clearly *preferable* to the camera path** if it both (a) clears
  the fixes above and lands near its ±0.3–2° theoretical floor, and (b) the lighting-independence
  argument becomes operationally decisive (e.g. a low-light or outdoor-glare use case where the
  camera's `SCENE_P995_MIN` gate fails routinely) — at that point radar-only's *availability*
  advantage would outweigh the camera path's currently-lower, already-quantified error budget.
  Nothing in the current data suggests radar-only would ever *individually* beat the camera-fused
  path's precision, since both ultimately bottleneck on the same short pre-impact window and a
  comparable few-frame sample count; the radar's advantage is exclusively **operating in the
  dark**, not superior geometry.

---

## Claim tags

- **CONFIRMED** (from code/data, no execution needed): the full computation trace (§1); TDM
  constants and `dθ/dv` sensitivities (§2a arithmetic); the per-burst MTI notch at 35.8 m/s and its
  overlap with measured club speed (§2b); `fit_tee`'s ball-tee-height anchor and its reuse for
  club data (§2d structural fact); `find_club`'s 2-TX projection matching `mti_filter`'s hardcode
  (§1a, clears a false lead); `hlcmf_v1` cannot be reused as-is (§3); the camera-fused estimator's
  two quantified error sources (§5 table).
- **INFERRED** (plausible, code-grounded, not verified against real capture replay in this pass):
  §2a's velocity-fit-error magnitude actually driving the observed phase span; §2b's notch actually
  being the dominant/sole cause versus a contributor; §2d's magnitude match to the observed
  −25…−37° cluster (direction and structural cause are solid; the exact number needs real data).
- **UNKNOWN**: whether scatterer migration/rotation (§2c) is real and how much it contributes,
  pending a wide-speed-range session with statistical power; the actual `phase_span_rad` result of
  any of the four fix candidates, since no `.l3dump` files or the referenced
  `20260825_181734` session were present in this working copy to execute against (only
  historical `session_logs/*.pkl`/`*.jsonl` from unrelated sessions are on disk).
