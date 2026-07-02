# Stage 0C — Marked-clubhead pose: honest accuracy budget (behind-ball) — design spec

- **Date:** 2026-07-01
- **Status:** Draft for review
- **Branch:** `feat/camera-club-data`
- **Supersedes:** the Stage 0B-3 photoreal-iron **markerless-detection** plan (moot — the club is now marked, so the detection problem 0B-3 existed to study is largely solved by retroreflective markers).
- **Related:** 0B-2 (keypoint→PnP geometry — sound but modeled with an *unfairly kind* noise model); the meta-audit (independent review) that named the assumed-away error sources; the 0A driver + iron templates.

## 1. Problem & what's new

The maintainer has accepted **markers on the club** (retroreflective dots/bands + IR strobe + IR-pass filter → bright, unambiguous, correspondence-known blobs), as a deliberate divergence from Trackman's markerless-radar approach, to compensate for OpenFlight's weaker radar. This is the **Foresight-style bet** (optical clubhead pose), adapted to a **behind-the-ball** camera that **never sees the face**.

Markers solve the two things that killed the markerless path: **reliable detection** (retroreflective blobs survive specular/sun) and **known correspondence** (coded pattern). What they do **not** solve — and what 0B-2's sim dishonestly assumed away — are the error sources the meta-audit named:
1. **Per-club calibration error** — 0B-2 used the *same template* for truth and estimate, so template-vs-real-club mismatch (which the 0A study says caps face/loft **1:1**) was structurally invisible.
2. **Ball-center localization error** — 0B-2 held the true ball fixed for both truth and estimate; impact location *is* the ball relative to the face, and ball **depth** from behind is the degenerate axis.
3. **Impact-instant / timing error** — 0B-2 is a single static pose; reality needs the pose at the **radar impact timestamp**, extrapolated from a fast-moving head (1 ms of sync error ≈ 40–50 mm of head travel).
4. **Correlated detection bias** — 0B-2 used zero-mean *independent* Gaussian noise, which PnP averages down by ~1/√N; retroreflective centroids are far better than smooth-crown features, but any residual correlated term does **not** average out.

**This stage produces the honest per-metric accuracy budget** for the marked behind-ball architecture, modeling all four, so we learn *what precision this architecture can actually deliver* and *which error source dominates* (i.e., what to spend engineering on).

## 2. Goal & requirements

Extend the 0B-2 geometry sim to a **marked-clubhead accuracy budget**: place a realistic marker constellation, inject the four real error sources, propagate through `fit_pose_pnp` + `raw_metrics`, and report **face angle / dynamic loft / impact offset / impact height** error as a function of each source (mono + stereo), for driver and iron.

**Hard requirements (the honesty spine — these directly fix 0B-2's flaws):**
1. **Truth template ≠ fitter template.** The simulated "true" club geometry and the fitter's *calibrated* geometry must be **separate objects** that differ by a calibration error; otherwise calibration fidelity is invisible (0B-2's core dishonesty). Sweep `σ_cal`.
2. **Ball localization is a modeled error, not perfect.** Perturb the ball center with a realistic error, **anisotropic** (depth σ ≫ lateral σ for mono; stereo shrinks depth σ). Impact metrics must include it.
3. **Impact-instant timing is modeled.** The recovered frame-pose is extrapolated to `t_impact` using velocity; error = `sync_jitter · v + Δt · v · ε_v`. Sweep `sync_jitter` and `ε_v`. Report its contribution to impact position explicitly.
4. **Detection error = small independent centroid noise `σ_c` PLUS a swept correlated-bias term `δ`.** Do not assume `δ=0`; report how much correlated bias the constellation tolerates before face/loft blow past the bar (this is the reviewer's point, tested not assumed).
5. **Correspondence is known** (markers are coded) — no correspondence ambiguity; that is the point of marking.
6. **Mono and stereo**, with the stereo baseline swept; expect stereo to be required for impact **height** and ball depth.
7. **`ok_rate ≥ 0.9` gate** (reused) and **per-error-source sensitivity** reported — the deliverable is the dominant term + the hardware requirement it implies, not a single number.

## 3. Scope

**In:** a realistic marker constellation (driver + iron) on behind-visible non-wear surfaces; the four error models + the truth/fitter template split; the sensitivity sweep; the accuracy-budget artifact + hardware implications; mono + stereo.

**Out:** the photorealistic renderer and any *detection* modeling (markers make detection a solved, near-ideal-centroid problem — we model the residual centroid error, not the pixels); the physical marker/IR hardware BOM (a separate doc); the D-plane/radar fusion (this stage is the camera's marked-pose contribution only); real images.

**Non-goal:** making the numbers look good. If timing or calibration dominates and caps impact position at, say, 6–10 mm, that is the finding.

## 4. Affected files / new components

New in `research/club_pose/sim/`:
- `markers.py` — `MarkerRig(name, markers: dict[name -> (xyz, normal)])`; `driver_markers()`, `iron_markers()`; `calibrated_copy(rig, sigma_cal, rng)` (returns a rig perturbed by calibration error — the *fitter's* knowledge); visibility via the normal rule (reuse `keypoints.detect`'s gating).
- `budget.py` — `run_budget(club, mode, sigma_c, delta_bias, sigma_cal, ball_depth_sigma, sync_jitter_us, vel_err_frac, baseline_mm, n, seed) -> rows`; `budget_verdict(grid) -> {per-metric medians vs each swept axis, dominant_source, ok_rate}`. Reuses `pose_for_delivered`, `raw_metrics`, `ball_for_impact` (perturbed), `fit_pose_pnp`/`fit_pose_kp_stereo`, both templates.
- `run_budget_0c.py` — committed runner (self-locating `sys.path`) that sweeps each error source one-at-a-time (tornado) + a realistic combined cell, for driver + iron, mono + stereo.
- Tests under `research/club_pose/tests/`.

## 5. Method

### 5.1 Marker constellation (`markers.py`)
Behind-visible, non-wear surfaces only (crown/back/hosel/shaft for the driver; cavity-back/topline/hosel/shaft for the iron), spread in **toe–heel** (→ face angle), **front–back** (→ loft + depth), and **up** (hosel/shaft), asymmetric for coded correspondence. Provisional driver constellation (body mm; +X face-front, +Y toe, +Z up; face at +50):

| Marker | xyz (mm) | captures |
|---|---|---|
| `crown_toe` | (−5, 45, 25) | toe–heel (face angle) |
| `crown_heel` | (−5, −40, 25) | toe–heel (face angle) |
| `crown_front` | (20, 5, 28) | front (short lever to face) |
| `crown_back` | (−45, 0, 20) | front–back (loft, depth) |
| `hosel` | (−8, −50, 45) | up + heel (asymmetry) |
| `shaft_low` | (−8, −55, 90) | long lever (path/lean) |
| `shaft_high` | (−8, −60, 140) | long lever |

(Iron: `cavity_toe`, `cavity_heel`, `topline_mid`, `hosel`, `shaft_low` — the thin head puts the topline close to the face.) **Refine to lie on the actual mesh surfaces + verify behind-visibility at sampled impact poses.** The face is inferred by extrapolating from these markers via the template; note the lever (crown_front is ~30 mm from the face, crown_back ~95 mm).

### 5.2 The four error models (`budget.py`)
Per trial (a sampled delivered pose from `pose_for_delivered`):
- **Detection:** project each visible marker via the camera; add `N(0, σ_c)` per marker (independent, ~0.3–1 px for retroreflective) **plus** a per-frame correlated shift `δ · û` shared across markers (û a random unit direction) — the swept correlated-bias term.
- **Calibration:** the *fitter* solves PnP against a `calibrated_copy` of the rig whose marker body coords differ from truth by `N(0, σ_cal)` mm (per-club calibration residual); the *truth* projection uses the true rig. Also perturb the fitter template's `face_center_offset`/loft by a small calibration residual so the inferred face plane carries calibration error.
- **Ball localization:** the true impact uses `ball_for_impact(true_pose,…)`; the *estimated* impact projects a **perturbed** ball center — `N(0, σ_lat)` laterally and `N(0, σ_depth)` along the camera axis, with `σ_depth ≫ σ_lat` for mono and `σ_depth` reduced ∝ baseline for stereo.
- **Timing:** treat the recovered pose as at `t_frame = t_impact − Δt`; extrapolate to `t_impact` with the clubhead velocity `v` (≈ 40 m/s downrange + swing arc). The estimate uses `Δt_est = Δt + N(0, sync_jitter)` and `v_est = v·(1 + N(0, vel_err_frac))`, so the extrapolated head translation error `= v·sync_jitter + Δt·v·vel_err_frac`. Apply to the recovered pose before computing impact metrics.

Then: `fit_pose_pnp`/`fit_pose_kp_stereo` on the noisy markers against the calibrated rig → extrapolate (timing) → `raw_metrics` with the perturbed ball → face/loft/impact errors vs the true-pose/true-ball/true-template metrics.

### 5.3 Sensitivity sweep (`run_budget_0c.py`)
A **tornado**: hold all sources at a realistic baseline, then sweep each **one at a time** across a plausible range, for driver+iron × mono+stereo. Baselines (starting estimates — the sweep refines): `σ_c = 0.5 px`, `δ = 0`, `σ_cal = 0.5 mm`, `σ_depth(mono) = 15 mm / stereo(150mm) ≈ 3 mm`, `sync_jitter = 100 µs`, `vel_err_frac = 0.03`. Plus one **realistic-combined** cell per club/mode. `budget_verdict` reports, per metric, the median error at baseline, the sensitivity to each source, and the **dominant source**.

### 5.4 Deliverable
`RESULTS_0C.md`: per-metric (face angle, dynamic loft, impact offset, impact height) accuracy budget for driver + iron, mono + stereo; the dominant error source for each; and the **hardware requirements it implies** — e.g. "impact height needs stereo + camera-radar sync < X µs + per-club calibration < Y mm." Fold the realistic budget into the v2 guide (replacing the idealized 0B-2 numbers as the *honest* expectation).

## 6. Validation (TDD)
- **markers:** constellation is non-coplanar (2nd *and* 3rd singular values non-trivial), behind-visible at a neutral impact pose, face_center consistent with the template.
- **calibration visibility (the key anti-0B-2 test):** with `σ_cal > 0` and *zero* pixel noise, face/loft error is **non-zero** and scales with `σ_cal` — proving the truth/fitter split works (0B-2 would return zero here).
- **timing:** with only `sync_jitter > 0`, impact-position error ≈ `v · sync_jitter` (first-order check).
- **ball depth:** mono impact-height error scales with `σ_depth`; stereo reduces it.
- **correlated bias:** face/loft error grows faster with `δ` (correlated) than with the same magnitude of `σ_c` (independent) — demonstrating PnP cannot average out `δ`.
- **monotonicity + ok_rate gate** reused; all non-artifact tests green under `uv run --group research pytest research/club_pose/tests/ -v`.

## 7. Success criteria
1. Realistic driver + iron marker constellations (behind-visible, non-coplanar) committed + verified.
2. Truth/fitter template split proven (calibration test non-zero); all four error sources modeled + individually tested.
3. The tornado sweep runs and emits the per-metric budget + dominant source (driver/iron × mono/stereo) with the `ok_rate` gate.
4. `RESULTS_0C.md` states the honest per-metric accuracy + the hardware requirements + which metrics clear their bars; folded into the v2 guide.

## 8. Risks / notes
- **The point of this stage is a possibly-sobering number.** If timing or ball-depth dominates and caps impact position well above ±3–5 mm, that is the honest finding and it redirects effort (tighter sync, stereo, better ball tracking) — do not tune the error baselines to make the bar.
- **Baselines are estimates**; the sweep is what matters. Cite the source for each baseline in `RESULTS_0C.md` (e.g. retroreflective centroid σ from mocap literature; sync jitter from the trigger design).
- **Correspondence-known is an assumption the markers earn** (coded pattern) — state it; if the coding is ambiguous under occlusion, that is a separate detection risk not modeled here.
- **This is the camera's marked-pose contribution only.** Face/loft will *also* come from the radar D-plane; the two cross-check. Spin (the D-plane's input) remains a separate, still-unstarted stage — and per the audit is the higher priority; this stage does not displace it, it quantifies the *other* camera output (impact position + a face/loft cross-check).
