# Stage 0D — D-plane inversion error budget (radar face/loft) — design spec

- **Date:** 2026-07-02
- **Status:** Draft for review
- **Branch:** `feat/camera-club-data`
- **Related:** v2 guide §1E(b) (the D-plane), §1F(a) (verified physics: coefficient range, sensitivity flow-down, gear effect), §1G(d) roadmap step 1; Stage 0C (the marked-pose budget this complements); the 0A templates.

**Review responses (rev 2, 2026-07-02)** — addressed a Codex spec audit:
1. **ŵ defined** — measured impact is the pair `(û, ŵ)`, each `+ N(0, σ_impact)` (shared σ) (§5.2).
2. **σ_rate removed from the budget** — spin rate is a forward-model consistency *output* only; no inversion route consumes it, so sweeping it would be a dead tornado bar. A rate-based spin-loft route is explicitly out of scope (YAGNI) (§3, §5.1).
3. **Angle-unit convention locked:** all public/spec angles are **degrees**; implementations convert to radians inside every trig call; a degree/radian round-trip test is required (§5, §6).
4. **Fusion demoted to secondary/exploratory** with fixed (baseline-σ, never per-shot) variance formulas; **route-only tables are the source of truth** (§5.2, §5.4).
5. **Requirement boundaries are computed per gear mode** — primary under `gear=camera` (σ_impact = 3 mm; OpenFlight's architecture), secondary under `gear=none` (the Mevo comparison) (§5.3).
6. **Receiver frame bias added** (the 0C correlated-bias lesson applied to radar): a per-session boresight bias `b_frame` shifts launch AND path together; analytically it passes **1:1 into face-to-target** but **cancels in face-to-path** — swept, and asserted in tests (§5.2, §5.3, §6).
7. **Coefficient ranges relabeled "conservative envelope"** spanning the published sources (Wood/PING driver ≈ 0.76 h, 7-iron ≈ 0.69, wedge ≈ 0.61; TrackMan 0.85/0.75 rule; Foresight-community 0.87) — not a single published range (§5.1).
8. Open questions resolved: **`mode` param removed** — the runner signature is `run_dplane_budget(club, gear_mode, …)` and **both routes are always computed per row**; the **axis route estimates FACE only** — dynamic loft always comes from the launch route (§4, §5.2).

## 1. Problem & the questions this answers

The "match Mevo" tier gets **face angle / dynamic loft by inverting the D-plane**: measure ball **launch direction** + **spin (rate + axis)** + **club path/attack**, back out the face normal. §1F verified the physics and supplied the real parameter ranges, but nobody (including the industry) has published an **error budget** for this inversion. This stage builds it — pure math, 0A-style, days not weeks. It answers, with numbers:

1. **What face/loft accuracy can OpenFlight's D-plane route achieve** given realistic input errors (receiver launch/path, camera spin axis, model uncertainty, gear effect)?
2. **The requirement flow-down:** for face ≤ 1.5° (and ≤ 2.5°) — what launch-direction σ must the maintainer's receiver hit? what path σ? what spin-axis σ must the camera hit? *(These become the receiver's acceptance targets and the spin stage's gate.)*
3. **How much does impact-location knowledge (the marked-club camera) buy** by correcting gear effect — quantifying the §1F(a) "do both" synergy?

## 2. Goal & requirements

Monte-Carlo the forward D-plane model (with truth-level club-dependent physics) and the practical inversion (with nominal assumed parameters), per club (driver + 7-iron), sweeping each input error; emit the per-input tornado, the requirement boundary table, and the gear-correction benefit.

**Hard requirements (the honesty spine):**
1. **Truth physics ≠ inversion's assumed physics** (the 0C lesson, applied to models): the *forward* model samples the true face/launch coefficient per shot from the **published club-dependent range** (Wood/PING 2018: driver ~0.76–0.87 h; 7-iron ~0.61–0.76; strike-dependent); the *inversion* uses a fixed nominal ĉ. Model error is thereby a first-class, unavoidable error source — never assume the coefficient is known exactly.
2. **Gear effect is present in the synthetic ball data whether or not the inversion corrects it.** Impact offset is sampled from a realistic strike distribution (σ ≈ 8 mm toe-heel, 6 mm vertical); its axis-tilt and effective-face terms use TrackMan's published magnitudes. Three correction modes: **none** (Mevo-style), **camera-corrected** (impact known to σ_impact ≈ 3 mm — the 0C marked-club output), **perfect** (bound).
3. **Both face routes are budgeted:** (i) the **launch route** F̂ = (L_H − (1−ĉ)·P̂)/ĉ, and (ii) the **axis route** (spin-axis → face-to-path → F̂₂ = P̂ + FTP̂), plus a simple inverse-variance fusion — because their error characters differ (launch route is coefficient-limited; axis route is gear-limited).
4. **All-inputs-on combined cells** for the realistic verdict; **one-at-a-time tornado** for attribution. No tuning any baseline to hit a bar — the requirement table *is* the deliverable, whichever way it lands.
5. **Analytic anchors as tests:** the derived sensitivities (∂face/∂launch ≈ 1/c ≈ 1.2–1.33; ∂face/∂path ≈ (1−c)/c ≈ 0.2–0.33; axis-error → FTP shrunk by ~sin(spin loft)) must be reproduced by the Monte Carlo within tolerance — this validates the machinery against the §1F(a) derivation.

## 3. Scope

**In:** the forward model (launch, spin rate, spin axis incl. gear effect), the two-route inversion + fusion, the MC error injection + tornado + requirement boundary, driver + 7-iron, the three gear-correction modes, `RESULTS_0D.md`.

**Out:** full flight/trajectory simulation (launch quantities only); the collision physics from first principles (we use the published empirical coefficient envelopes — that's the point); radar signal modeling (input errors are abstracted as σ's + the frame-bias term); the spin *measurement* itself (that's the spin stage — this stage only consumes σ_axis); **a spin-rate-based inversion route** (no route consumes ω̂ — rate is a consistency output only; budgeting σ_rate would be a dead bar).

**Non-goal:** proving the D-plane path is good. If model+gear error floors face accuracy at, say, 2.5° uncorrected, that is the finding (and quantifies exactly what the marked "pro mode" adds).

## 4. Affected files

- Create `research/club_pose/dplane.py` — the forward model + the two inversions (pure functions, no I/O).
- Create `research/club_pose/sim/budget_dplane.py` — `run_dplane_budget(club, gear_mode, sigma_launch, sigma_path, sigma_axis, sigma_impact, b_frame, coeff_width, n, seed) -> rows` (**both routes always computed per row**); `dplane_verdict(grid)` (tornado + per-gear-mode requirement boundary; reuses the 0C verdict conventions incl. counting all attempts).
- Create `research/club_pose/sim/run_budget_0d.py` — committed self-locating runner (tornado + combined cells, driver + iron, all three gear modes).
- Create `research/club_pose/sim/RESULTS_0D.md` — the budget artifact.
- Tests: `research/club_pose/tests/test_dplane.py`, `test_budget_dplane.py`.

## 5. Method

**Unit convention (binding):** every angle in this spec, in public function signatures, and in stored rows is in **DEGREES**. Implementations MUST convert to radians inside every `sin/cos/tan/atan2` call (`np.radians`/`np.degrees` at the boundary). A degree/radian round-trip test is mandatory (§6).

### 5.1 Forward model (`dplane.py`) — truth generation
Per shot, sample delivery: F ~ U(−5°, 5°), P ~ U(−5°, 5°), driver AoA ~ U(−3°, 3°) / iron U(−6°, −1°), DL = static ± U(−2°, 6°), speed v ~ U(40, 50) m/s driver / U(33, 38) iron, impact offset u ~ N(0, 8 mm) toe-heel and w ~ N(0, 6 mm) vertical (truncate to the face).

- **True coefficients per shot — a CONSERVATIVE ENVELOPE, not a single published range:** c_h ~ U(0.76, 0.87) driver / U(0.61, 0.76) iron. The envelope spans the published sources (Wood/PING 2018 measured: driver ≈ 0.76 h, 7-iron ≈ 0.69, wedge ≈ 0.61; TrackMan's 0.85/0.75 rule; Foresight-community 0.87) and is labeled as such in code + results. c_v uses the same envelopes (published data doesn't separate vertical well; noted as a limitation).
- **Launch:** `L_H = c_h·F_eff + (1−c_h)·P`, `L_V = c_v·DL_eff + (1−c_v)·AoA`, where the **gear-effective face/loft** are `F_eff = F + k_face·u` (k_face ≈ 0.2°/mm driver — TrackMan's 2° per 10 mm; 0.05°/mm iron) and `DL_eff = DL + k_loft·w` (roll: ≈ 0.15°/mm driver, ≈0 iron).
- **Spin loft:** `SL = sqrt((DL − AoA)² + ((F − P)·cos(DL))²)` (3-D angle, standard approximation).
- **Spin rate:** `ω = k_ω · v · sin(SL)` (k_ω calibrated so driver ≈ 2,500–3,500 rpm, iron ≈ 6,000–8,000). **Rate is a forward-model consistency OUTPUT only — no inversion route consumes it and it is NOT budgeted/swept** (rev 2).
- **Spin axis:** `θ = atan2((F − P)·cos(DL), DL − AoA) + θ_gear`, with `θ_gear = −g_axis·u` (g_axis ≈ 1.57°/mm driver — TrackMan's ~20° per ½″; ≈ 0.3°/mm iron).

### 5.2 Measurement + inversion (`dplane.py`)
Measured inputs: `L̂_H = L_H + b_frame + N(0, σ_launch)`, `L̂_V = L_V + N(0, σ_launch)`, `P̂ = P + b_frame + N(0, σ_path)`, `ÂoA = AoA + N(0, σ_path)`, `θ̂ = θ + N(0, σ_axis)`, and in camera-corrected gear mode the **measured impact pair** `û = u + N(0, σ_impact)`, `ŵ = w + N(0, σ_impact)` (shared σ_impact).

- **`b_frame` — receiver frame/boresight bias** (the 0C correlated-bias lesson applied to radar): one fixed horizontal aim bias per run/session, applied to BOTH `L̂_H` and `P̂` (they share the radar frame). Analytic consequence (assert in tests): it passes **1:1 into face-to-target** — `F̂₁ = ((L_H+b) − (1−ĉ)(P+b))/ĉ = F₁ + b` — but **cancels exactly in face-to-path** (`F̂ − P̂`). So `b_frame` bounds absolute face/aim accuracy while leaving shot-shape (FTP/axis) untouched — a calibration/leveling requirement, not a sensor-noise one.
- **Launch route:** `F̂₁ = (L̂_H − (1−ĉ_h)·P̂)/ĉ_h`, `D̂L₁ = (L̂_V − (1−ĉ_v)·ÂoA)/ĉ_v`, with **nominal** ĉ (envelope midpoint). Gear-corrected mode subtracts `k_face·û` (face) and `k_loft·ŵ` (loft).
- **Axis route (estimates FACE ONLY; dynamic loft always comes from the launch route):** `FTP̂ = tan(θ̂_corr)·(D̂L₁ − ÂoA)/cos(D̂L₁)`, `F̂₂ = P̂ + FTP̂`, where `θ̂_corr = θ̂ + g_axis·û` in corrected mode (else uncorrected).
- **Fusion — SECONDARY/exploratory only; route tables are the source of truth.** If implemented: fixed inverse-variance weights computed ONCE from the cell's **baseline σ's** via `Var(F̂₁) ≈ (σ_launch² + (1−ĉ)²σ_path²)/ĉ²` and `Var(F̂₂) ≈ σ_path² + (sin(SL̄)·σ_axis)² + (g_axis·σ_gear_resid)²` (SL̄ = the club's median spin loft; σ_gear_resid = σ_impact in corrected mode, else the strike-distribution σ). Never per-shot adaptive; never tuned.
- Errors recorded per route: `|F̂ − F|`, `|D̂L − DL|` (vs the TRUE delivered values, not the gear-effective ones — the golfer wants their delivery).

### 5.3 Sweep (`run_budget_0d.py`)
Baselines: σ_launch = 0.5°, σ_path = 1.0°, σ_axis = 5°, b_frame = 0, ĉ = envelope midpoint (truth still sampled from the envelope — the coefficient error is always on), n = 2000/cell. Tornado (one at a time): σ_launch ∈ {0.1, 0.25, 0.5, 1, 2}, σ_path ∈ {0.25, 0.5, 1, 2, 3}, σ_axis ∈ {1, 2.5, 5, 10}, **b_frame ∈ {0, 0.25, 0.5, 1.0}°**, coefficient-envelope width ∈ {0 (known), half, envelope, 1.5×}. Clubs × {driver, iron}; gear modes × {none, camera (σ_impact = 3 mm), perfect}. `dplane_verdict` emits: per-route (+ secondary fused) medians per cell, the tornado, the **requirement boundary computed per gear mode — PRIMARY under `gear=camera`** (OpenFlight's architecture) **and secondary under `gear=none`** (the Mevo comparison) — i.e. the loosest σ per input holding face ≤ 1.5° / ≤ 2.5° with the others at baseline, and the **gear-correction benefit** (none − camera − perfect deltas).

### 5.4 Deliverable (`RESULTS_0D.md`)
The budget tables + four headline numbers: **(1)** the receiver's launch-direction requirement, **(2)** the camera's spin-axis requirement (expected lenient for face; reported separately for shot-shape display where axis error passes through 1:1), **(3)** the gear-effect penalty and how much σ_impact = 3 mm recovers — the quantified "do both" synergy, **(4)** the **frame-bias/leveling requirement** (b_frame bounds absolute face/aim 1:1 — an installation-calibration spec, distinct from sensor noise). Fold into the v2 guide.

## 6. Validation (TDD)
- **Units round-trip:** a known analytic case (e.g. F=2°, P=−1°, c=0.8 → L_H = 1.4°) passes through the forward model + inversion exactly — catches any degree/radian slip; plus an explicit conversion-boundary test.
- **Machinery:** zero noise + coefficient envelope width 0 + no gear + b_frame 0 → exact recovery (≈ 0 error, both routes).
- **Analytic anchors:** σ_launch-only slope ≈ 1/ĉ (±15 %); σ_path-only slope ≈ (1−ĉ)/ĉ (±15 %); σ_axis-only face error via the axis route ≈ σ_axis·sin(SL)-scaled (assert < 0.35×σ_axis for driver medians) — reproduces §1F(a).
- **Frame bias:** with only `b_frame` on — face-to-target error ≈ b_frame (1:1) on BOTH routes, and face-to-path error ≈ 0 (the cancellation) — asserts the §5.2 derivation.
- **Coefficient honesty:** with all σ = 0 but the coefficient envelope on, face error is **non-zero** and grows with |F − P| (the anti-0C-lesson test — model error is visible).
- **Gear:** uncorrected axis-route face error inflates with the strike distribution (≈ g_axis·E|u|·shrink factor); σ_impact = 3 mm recovers most of it; perfect correction returns to baseline; monotonic in σ_impact.
- **Verdict plumbing:** all attempts counted; tornado attribution picks the injected dominant source on synthetic cases.
- All green under `uv run --group research pytest research/club_pose/tests/ -v`.

## 7. Success criteria
1. Forward model + both inversion routes implemented with the §5.1/5.2 equations and published constants (cited inline).
2. Analytic anchors + coefficient-honesty + gear tests green.
3. The sweep runs; `RESULTS_0D.md` states the requirement flow-down (receiver launch σ, path σ, camera axis σ for face ≤ 1.5°/2.5°), per club, per route, with the gear-correction benefit quantified.
4. Folded into the v2 guide; the spin spec consumes the axis target.

## 8. Risks / notes
- **The model is empirical, not first-principles collision physics** — that is deliberate (the coefficient ranges *are* the state of published knowledge, and requirement 1 makes their uncertainty a budgeted error). If a better collision model emerges later, the machinery accepts it.
- **c_v (vertical) ranges are assumed equal to c_h** for lack of published vertical decompositions — flagged in results.
- **Axis-route and launch-route errors are correlated** through shared P̂/D̂L; the fusion weights are analytic and approximate — fusion results are indicative, routes are exact.
- **Shot-shape display accuracy** (spin axis shown to the user) is 1:1 with σ_axis and gear — the lenient face requirement does NOT mean axis quality doesn't matter; both numbers are reported.
- Constants (0.2°/mm, 1.57°/mm, coefficient ranges) trace to §1F(a) sources — TrackMan club-data definitions and Wood/PING 2018 — cited in code comments.
