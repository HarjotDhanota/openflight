# Stage 0E — Ball-spin measurement error budget (dot ball, behind-ball camera) — design spec

- **Date:** 2026-07-02
- **Status:** Draft for review
- **Branch:** `feat/camera-club-data`
- **Related:** v2 guide §1F(c) (verified capture model: MLM2PRO existence proof, multi-frame over 3–30 cm, per-environment capture), §1E(b)+Stage 0D (the D-plane consumes spin axis; 0D emits the axis target), §6A/§6A.1 (earlier spin research); the repo's `spin_estimate.py` (radar spin is dead — this stage is its replacement).
- **Consumes:** the Stage 0D axis target — now known (§1H): **~2–3° for shot-shape display (the binding requirement), ~5° for the driver-face cross-check** (the sweep tiers cover both).

**Review responses (rev 2, 2026-07-02)** — addressed a Codex spec audit:
1. **Wrap rule rebuilt on the real SO(3) limit.** Relative rotations report a principal angle in **[0°, 180°]** — a 252° rotation is *indistinguishable* from 108° about the flipped axis, so the old ≥300° check tested the unobservable. New rule (§5.4): per gap, form the candidate pair `{φ, 360°−φ (axis flipped)}`; select using the **club-regime rate prior**; if **both** candidates land in the plausible range → `ok=False` (ambiguous). Tests pin the two informative regimes at Δt = 4.2 ms: **wedge 10,000 rpm (252°) disambiguates via the prior** (alias ≈ 4,286 rpm is out of range) and **iron 7,500 rpm (189°) is flagged ambiguous** (alias ≈ 6,786 rpm is in range) — i.e. 240 fps is marginal for irons, a capture-design finding the sweep must surface.
2. **Import plumbing specified:** `research/ball_spin/tests/conftest.py` inserts the `research/` root into `sys.path` (mirrors `club_pose/tests/conftest.py`) so `pytest research/ -v` works.
3. **Requirement boundaries per regime** — driver AND iron AND wedge each get their own boundary table (0D showed the binding axis need isn't driver's).
4. **Ball px defined at frame 0:** the swept "ball px" is the image diameter at `t0`; recession shrinks later frames (reproducible).
5. **FOV loss counts:** a trial with < 2 usable in-FOV frames is `ok=False` (in `ok_rate`), and the median usable-frame count is reported per cell.
6. **Outlier threshold pinned (no post-hoc tuning):** reject while `max residual > max(3·median_residual, 3·σ_dot/r̂_ball_px)` (angular, radians) and ≥ 5 dots remain.
7. **Signed axis convention:** world frame per 0A (+X downrange, +Z up); reported spin-axis tilt is signed about the flight direction, **positive = fade-tilt (RH)**, TrackMan convention; errors reported as signed-tilt error AND 3-D axis angle; a directed sign test asserts a known fade input yields positive tilt end-to-end.
8. **β added to the tornado** ({0.15, 0.25, 0.4}).

## 1. Problem & what this stage decides

Spin is the linchpin: the **D-plane cannot produce face/loft without the camera's spin axis**, spin rate is the **validated core of the Mevo tier** (~1.5 % with a marked ball), and it is the camera's unique contribution (radar spin is dead, r ≈ 0.19). The verified route (§1F(c)) is **MLM2PRO/SpinDOE-style**: a **dot-marked ball**, imaged in **multiple frames over 3–30 cm of early flight**, solving the ball's **absolute orientation per frame** from the dot pattern, then regressing rotation across frames → **rate + axis**. Nobody publishes an error budget for this from the behind-ball vantage. This stage builds it, answering:

1. **What rate/axis accuracy is achievable** vs frame count, frame spacing, ball image size (px), dot count/noise/misidentification, ball-center localization, and spin regime (driver 2,500 rpm → wedge 10,000 rpm)?
2. **The capture requirement:** what fps/frame-gap, lens/FOV (ball px), and dot pattern does OpenFlight need to hit **rate ≤ 3 %** and **axis ≤ the 0D target** (readable at any value 1–10°)?
3. **The vantage question, with numbers:** does straight-behind suffice (MLM2PRO says yes), and what does a **quartering offset** (20–40° off the target line) or **stereo** actually buy?
4. **Failure modes made visible:** rotation-wrap ambiguity at high spin × long gaps; dot correspondence errors; the receding/shrinking ball.

## 2. Goal & requirements

Monte-Carlo sim: synthetic dotted ball in early flight → honest per-frame dot detection → per-frame orientation solve → multi-frame rate/axis regression → error budget + requirement boundary, in the style of 0C/0D.

**Hard requirements (the honesty spine):**
1. **Ball center/scale is SOLVED, not given.** The per-frame orientation solve consumes a ball center + radius estimated from a noisy limb-circle fit (σ_center, σ_radius swept) — never the true values. (The 0C lesson: don't hand the solver truth it wouldn't have.)
2. **Dot visibility is physical:** a dot is detectable iff on the camera-facing hemisphere AND not limb-foreshortened (`n̂·v̂ ≥ 0.25`, swept); the visible subset **changes between frames** as the ball rotates (this is what makes high spin × long gaps hard — it must emerge, not be assumed away).
3. **Correspondence is imperfect:** dots carry IDs (coded asymmetric pattern — the MLM2PRO/SpinDOE justification), but a swept **misidentification rate** (0–5 %) assigns wrong IDs, producing gross outliers the solver must reject or honestly suffer.
4. **Wrap ambiguity is detected, not silently wrong — at the REAL limit (180°, not 360°):** an SO(3) relative rotation reports only the principal angle ∈ [0°, 180°], so any per-gap rotation > 180° aliases to its complement about the flipped axis. The solver forms both candidates `{φ, 360°−φ}` per gap, disambiguates with the club-regime rate prior, and flags `ok=False` when both are plausible. Tests force both the disambiguable case (wedge @ 4.2 ms) and the genuinely ambiguous one (iron 7,500 rpm @ 4.2 ms).
5. **All solve failures counted** (`ok_rate ≥ 0.9` gate, 0C convention); no baseline tuning — the requirement table is the deliverable.
6. **Degrees at every public boundary; radians inside trig** (0D rev-2 convention + round-trip test).

## 3. Scope

**In:** the dotted-ball model (coded pattern, parameterized dot count); flight over the capture window (positions + orientations at frame times; gravity included, drag/Magnus negligible over ≤ 20 ms — noted); the detection model (visibility, centroid noise, dropout, misID, limb fit); per-frame orientation solve + multi-frame regression; the sweep + requirement boundary + vantage comparison (behind / quartering / stereo); `RESULTS_0E.md`.

**Out:** rendering/photorealism (dots are modeled detections, like 0C's markers); the dot-pattern *detector* itself (real-image work — a later bench stage); strobe electronics (the capture *geometry* is what's simulated; §1F(b) covers hardware); club data (this is the ball only); trajectory beyond the capture window.

**Non-goal:** proving spin is easy. If wedge-speed spin needs tighter frame gaps than cheap hardware provides, that is the finding.

## 4. Affected files

New sibling package `research/ball_spin/` (spin is not club pose):
- `research/ball_spin/__init__.py`
- `research/ball_spin/dotball.py` — the dotted ball: `dot_pattern(n_dots, seed) -> unit vectors (body frame)` (quasi-uniform, asymmetric/coded — e.g. fibonacci + a jitter that breaks all symmetries); `BALL_RADIUS_MM = 21.35`.
- `research/ball_spin/flight.py` — `ball_states(launch, spin, frame_times) -> [(center_world, R_orientation)]`: `center(t) = p0 + v·t + ½g·t²`; `R(t) = Rot(axis, ω·t)·R0`.
- `research/ball_spin/detect.py` — per frame: project dots (reuse `club_pose.sim.camera.Camera`), visibility (hemisphere + foreshortening), centroid noise σ_dot, dropout, misID swap; the **limb-circle fit** producing the noisy center/radius estimate (`σ_center`, `σ_radius` in px).
- `research/ball_spin/solve.py` — per-frame orientation: back-project detected dots through the *estimated* center/radius onto the unit sphere → directions; **Kabsch (reflection-guarded) on unit vectors** with an outlier-rejection loop (misID defense); multi-frame: relative rotations `R_iⱼ = R̂_j·R̂_iᵀ` → axis-angle → least-squares `ω·axis` fit across all pairs, with the **wrap check** (per-gap rotation ≥ the ambiguity limit → flag).
- `research/ball_spin/budget.py` + `run_budget_0e.py` — the sweep (0C/0D conventions: rows, `n_ok`, verdict with per-axis tornado + requirement boundary) + committed self-locating runner.
- `research/ball_spin/RESULTS_0E.md` — the artifact.
- Tests: `research/ball_spin/tests/test_dotball.py`, `test_flight.py`, `test_detect.py`, `test_solve.py`, `test_budget.py`, plus **`research/ball_spin/tests/conftest.py`** inserting the `research/` root into `sys.path` (mirror of `club_pose/tests/conftest.py` — without it the sibling package won't import). Run: `uv run --group research pytest research/ -v` covers both packages.

## 5. Method

### 5.1 Truth generation
Launch: speed ∈ {70 m/s driver, 55 iron, 40 wedge} (paired with spin regime), VLA ∈ U(8°, 24°), HLA ∈ U(−5°, 5°). Spin regimes: **driver** ω ~ U(2000, 3500) rpm, axis tilt U(−15°, 15°); **iron** U(5000, 7500), U(−10°, 10°); **wedge** U(8500, 11000), U(−8°, 8°). **Axis/sign convention (binding):** world frame per 0A (+X downrange, +Z up); the reported quantity is the **signed tilt about the flight direction, positive = fade-tilt for a RH golfer** (TrackMan convention); pure backspin = 0° tilt. `R0` random. Frame times: `t_i = t0 + i·Δt`, `t0 ≈ 2 ms` (clears the tee), `i < n_frames`. **Frames outside the FOV are lost, and a trial with < 2 usable frames is `ok=False`** (counted in `ok_rate`); the median usable-frame count is reported per cell — FOV loss is a capture-design outcome, not a silent exclusion.

### 5.2 Camera & vantage
Reuse `club_pose.sim.camera` (`IMX296`, `scaled_intrinsics`, `Camera.look_at`). Rigs: **behind** = `mono_rig()` geometry; **quartering** = camera at the same standoff rotated 20°/40° around the tee in yaw (still behind the ball, off the target line); **stereo** = `stereo_rig(150)` (adds an independently-solved second view per frame; orientations fused by averaging rotations, centers by triangulated scale). Ball image size follows from intrinsics + distance — **recession/shrink across frames is automatic** (the behind-vantage penalty emerges physically).

### 5.3 Detection model (`detect.py`)
Per frame: dot visible iff `n̂·(cam − p)/|…| ≥ β` (β = 0.25 baseline, swept 0.15–0.4) and inside the FOV. Detected dot uv = projection + `N(0, σ_dot)` (swept 0.2–1 px). Dropout prob per dot (0–10 %). **MisID:** with prob `p_misid` (0–5 %), a detected dot's ID is swapped with another visible dot's. **Limb fit:** estimated ball center = true projected center + `N(0, σ_center)` px; estimated radius = true + `N(0, σ_radius)` px (baselines 0.5/0.5, swept to 2/2 — models real circle-fit quality on a motion-frozen ball).

### 5.4 Solve (`solve.py`)
Per frame: each detected dot's line of sight, intersected with the *estimated* sphere (center/radius from §5.3 back-projected through the camera at the estimated depth) → unit vector in camera frame → **Kabsch** against the body-frame pattern (reflection guard; outlier loop with the **pinned threshold**: drop the worst-residual dot while `max residual > max(3·median_residual, 3·σ_dot/r̂_ball_px)` (angular, radians) and ≥ 5 dots remain — fixed before results, never retuned) → `R̂_i` + `ok_i` (≥ 5 inlier dots, non-degenerate).

Multi-frame with the **principal-angle wrap rule:** each ok-pair `(i,j)` gives `ΔR = R̂_j R̂_iᵀ` → principal angle `φ ∈ [0°, 180°]` about `â`. The true per-gap rotation is one of the **two candidates** `{(φ, â), (360°−φ, −â)}` (SO(3) cannot distinguish them). Convert both to implied rates via the gap time; keep the candidate(s) inside the **club-regime plausible range** (driver 1,500–4,500 / iron 4,000–8,500 / wedge 7,500–12,000 rpm): exactly one plausible → use it; **both plausible → `ok=False` (ambiguous)**; none → `ok=False`. Weighted least squares over the resolved pairs → `ω̂` (rpm) + `â` (unit axis). Errors: `|ω̂ − ω|/ω` (%), the **3-D axis angle** `∠(â, a_true)` (deg), and the **signed-tilt error** (deg, per §5.1's fade-positive convention).

### 5.5 Sweep (`run_budget_0e.py`)
Baseline ≈ MLM2PRO-shaped: n_frames = 4, Δt = 4.2 ms (240 fps), 27 dots, σ_dot = 0.5 px, σ_center/σ_radius = 0.5 px, dropout 5 %, misID 1 %, β = 0.25, behind vantage, mono, **ball = 100 px diameter AT FRAME 0** (intrinsics/standoff set to produce exactly this at `t0`; recession shrinks later frames), n = 500/cell. Tornado (one at a time, **run per regime**): n_frames {2,3,4,6,8}, Δt {1, 2, 4.2, 8 ms}, dots {12, 20, 27, 40}, σ_dot {0.2, 0.5, 1}, σ_center {0.3, 1, 2}, misID {0, 1, 2, 5 %}, **β {0.15, 0.25, 0.4}**, ball px@frame0 {60, 100, 150, 250}, vantage {behind, quarter-20°, quarter-40°}, mode {mono, stereo}. **Regimes {driver, iron, wedge} each get their own tornado + requirement boundary** (0D showed the binding axis need is display-driven, not driver-specific; iron × Δt = 4.2 ms is the ambiguity stress, wedge × Δt = 8 ms the hard-wrap stress). `verdict`: per-cell medians + `ok_rate` (incl. FOV- and ambiguity-failures) + median usable frames, tornado, and **per-regime requirement boundaries** — loosest settings achieving **rate ≤ 3 %** and **axis ≤ T°** for T ∈ {1, 2, 3, 5, 10} (0D's targets: 2–3° display, 5° driver-face — both readable directly).

### 5.6 Deliverable (`RESULTS_0E.md`)
The budget + headline answers: (1) the **capture spec** (fps/Δt, frame count, ball px → lens/FOV, dot count) that clears rate ≤ 3 % and each axis tier; (2) **behind vs quartering vs stereo**, quantified — is the quartering camera worth its mounting cost?; (3) the **wedge/high-spin limit** (where wrap forces shorter gaps); (4) what dot-pattern quality (misID, σ_dot) the real detector must deliver — the bench-test gate. Fold into the v2 guide.

## 6. Validation (TDD)
- **Units round-trip** (0D convention) + **machinery:** zero noise, perfect center/IDs → rate/axis errors ≈ 0 across regimes.
- **Physical visibility:** at 3,000 rpm × 4.2 ms (~76°/gap), the visible dot subset differs between frames; a dot on the receding limb is dropped by β.
- **Center honesty (anti-0C test):** σ_center-only (all else zero) → non-zero, monotonically growing axis error — proves the solver consumes the estimated center, not truth.
- **Wrap (the §5.4 principal-angle rule, three regimes):** wedge 10,000 rpm @ Δt = 4.2 ms (true 252°, principal 108°) → **disambiguated via the regime prior** (alias rate ≈ 4,286 rpm is outside the wedge range) and solved correctly; iron 7,500 rpm @ Δt = 4.2 ms (true 189°, principal 171°, alias ≈ 6,786 rpm — inside the iron range) → **flagged ambiguous (`ok=False`)**, never a confident wrong rate; iron @ Δt = 2 ms (90–135°) → solved correctly.
- **Signed tilt:** a known fade-tilt (positive) input reproduces a positive estimated tilt end-to-end (the §5.1 convention test).
- **FOV accounting:** a config whose later frames exit the FOV yields `ok=False` when < 2 usable frames remain, visible in `ok_rate`.
- **MisID defense:** misID = 5 % with the outlier loop → bounded degradation; with the loop disabled (test-only switch) → gross errors (proves the loop earns its keep, honestly).
- **Monotonicity:** error decreases with n_frames and dots; increases with σ_dot and Δt·ω (up to wrap).
- **Vantage sanity:** stereo ≤ mono on axis; quartering ≠ behind (direction reported, not presumed).
- All green under `uv run --group research pytest research/ -v`.

## 7. Success criteria
1. Dot-ball + flight + detection + solve implemented per §5 with the honesty spine (estimated center, physical visibility, misID, wrap flag).
2. All §6 tests green, including the anti-0C center test and the wrap test.
3. The sweep runs; `RESULTS_0E.md` states the capture requirement per axis tier + the vantage/stereo verdict + the wedge limit + the detector-quality gate.
4. Folded into the v2 guide; the 0D axis target read against the boundary table when 0D lands.

## 8. Risks / notes
- **The detection model is the sim's leap of faith** (as in 0C): dark dots on a white ball are contrast-based and far more benign than specular club markers, and MLM2PRO proves the class works from behind — but real σ_dot/misID must eventually come from a bench test with the chosen pattern. The sweep brackets it.
- **Orientation solve assumes the pattern is known** (our printed/stamped ball or an RPT-style purchased ball). A user's arbitrary logo ball is out of scope (that's PiTrac's harder problem).
- **Drag/Magnus ignored over ≤ 20 ms** (sub-mm effect at these windows) — noted in results.
- **Stereo fusion is simple** (rotation averaging) — indicative, not optimal; route-level (mono) numbers are the source of truth, mirroring 0D's fusion posture.
- **Strobe-multi-exposure (indoor PiTrac-style) is geometrically ≈ separate frames** for gaps ≥ ~1 ms (ball images don't overlap: 70 m/s × 1 ms = 70 mm ≫ 43 mm ball); the sim's Δt sweep covers it — the *photometric* overlap question belongs to the bench stage.
