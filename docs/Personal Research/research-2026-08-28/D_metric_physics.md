# Advanced club metrics: definitions, useful precision, and what OpenFlight's sensors can actually deliver

**Scope.** For each advanced club/impact metric: (1) TrackMan-style definition and the standard collision relations, with published coefficient ranges and how they move with club/loft/strike; (2) what precision actually matters to a fitter/coach vs. a home user; (3) an observability verdict for OpenFlight's specific sensor set, with the propagated-uncertainty arithmetic done explicitly; (4) a dedicated pass on dynamic loft and on impact location; (5) a metric dependency graph for sequencing. Every claim is tagged **CONFIRMED** (documented, checked against a primary/near-primary source), **INFERRED** (a reasoned estimate — physics scaling, cross-source synthesis, or judgment), or **UNKNOWN** (I could not find or derive a load-bearing number). Passages already established in the prior research set are tagged **[PRIOR]** and cited by filename; I extend rather than repeat their derivations.

---

## 0. OpenFlight's current measured/claimed inputs (carried in from the brief, restated for reference)

| Input | Status | Stated accuracy |
|---|---|---|
| Ball speed | Reliable | Not separately quantified here; OPS243-derived — CONFIRMED [PRIOR: `openflight_claude_artifact_dplane_audit.md`] flags a radial-vs-total-speed contract risk that has not been closed |
| Ball launch angles (H, V) | Reliable from camera | Reproducible to **0.2°** camera-to-camera; an **unresolved ~5° horizontal offset** exists between the camera and radar zero reference |
| Spin rate | Sometimes (radar) | Not quantified; treated as intermittently available |
| Club speed | OPS243 | Not separately quantified; same radial/total-speed contract risk applies |
| Impact timing | Resolved | Acoustic-trigger method, ~2.11-frame correction on this rig [PRIOR: memory `openflight-impact-timing-resolved`] |
| Fused clubhead velocity vector | Radar radial + camera transverse | Validates to **0.97–1.00** vs. OPS speed magnitude — this is a **speed** validation, not a validation of the vector's *direction* |
| Club path (production estimator) | Experimental | **+3…+9°**, plausible on 17/21 shots, unvalidated against ground truth |
| Attack angle (production estimator) | Experimental | **−2.6…−6.6°**, same estimator, same caveat |
| Face orientation (direct, silhouette) | Not usable | **±11°** at best [PRIOR: memory `openflight-silhouette-cannot-see-orientation`] |
| Impact location (direct, optical) | Coarse only | **~±5 mm**, ball-vs-head-outline reading — "plausible," not validated |

These are the numbers used throughout §3's arithmetic. Where the brief gives a range (club path "2–3°"), I use the stated range explicitly rather than picking one number, and note which end of the range flips the verdict.

---

## 1. Definitions and standard relations

Primary source for wording: TrackMan's own glossary and blog pages, fetched directly. **CONFIRMED** quotes below are verbatim or near-verbatim from TrackMan.

### 1.1 Definitions table

| Metric | TrackMan definition | Source |
|---|---|---|
| **Attack angle** | "The up or down movement of the club head's geometric center at the time of maximum compression." (A separate TrackMan page notes this is measured relative to the *center of gravity*, and that for drivers the center-*face* attack angle runs ~1° more upward than the CG-referenced value — i.e., "attack angle" has at least two internally-distinct reference points even in TrackMan's own literature.) | [40+ TrackMan Parameters](https://www.trackman.com/blog/golf/40-trackman-parameters) CONFIRMED; CoG-vs-face-point distinction: [Club Data Definitions](https://www.trackman.com/blog/golf/club-data-definitions) CONFIRMED |
| **Club path** | "The in-to-out or out-to-in movement of the club head's geometric center at the time of maximum compression." Same CoG-vs-face caveat: for drivers, center-face path runs ~3° more out-to-in than the CG-referenced value. | Same two sources, CONFIRMED |
| **Face angle** | "The direction the club face is pointing at the center-point of contact between the club and ball at the time of maximum compression," relative to the target line. TrackMan's older wording: "the angle between the club face and an imaginary line that extends from the club head through the ball along the target line." A 10 mm toe strike on a driver shifts the *reported* face angle ~2° more open, because it is measured **at the contact point**, not at face center. | [40+ Parameters](https://www.trackman.com/blog/golf/40-trackman-parameters); [Club Data Definitions](https://www.trackman.com/blog/golf/club-data-definitions) CONFIRMED |
| **Face-to-path** | "The angle difference between the reported face angle and club path." | [40+ Parameters] CONFIRMED |
| **Dynamic loft** | "The vertical angle of the club face at the center-point of contact between the club and ball at the time of maximum compression, measured relative to the horizon." A 10 mm low strike on a driver reduces reported dynamic loft by ~2°, for the same contact-point-vs-face-center reason as face angle. | Same sources, CONFIRMED |
| **Spin loft** | "The three-dimensional angle between the direction the club head is moving (path + attack angle) and the direction the face is pointing (face angle + dynamic loft)." TrackMan's own simplification: "since the difference between path and face angle is typically much smaller than the difference between attack angle and dynamic loft, spin loft is generally very close to dynamic loft minus attack angle." | [TrackMan Spin Loft blog](https://www.trackman.com/blog/spin-loft) CONFIRMED |
| **Spin axis** | "The angle relative to the horizon of the imaginary line the ball rotates around, measured after separation from the club face." | [40+ Parameters] CONFIRMED |
| **Launch direction** | "The horizontal angle the ball takes off at relative to the target line, measured immediately after separation from the club face." | [40+ Parameters] CONFIRMED |
| **Impact height / Impact offset** | "Impact Height: the vertical impact location distance relative to the center of the face. Impact Offset: the horizontal impact location distance relative to the center of the face." Both are continuous mm scalars from geometric face center, **not a discretized zone**. [PRIOR confirms this format independently: `markerless-club-data-guide-v2-research-corrected.md` §4.4] | [40+ Parameters] CONFIRMED |
| **Swing plane** | "The vertical angle between the ground and the circle the club head travels on during the bottom portion of the swing arc," fit over roughly **2.5 ft (75 cm) before to 2.5 ft after impact** — hundreds of position samples, not a single impact-instant reading. | [What is Swing Plane](https://www.trackman.com/blog/what-is-swing-plane) CONFIRMED |
| **Swing direction** | "The horizontal direction of the plane relative to the target line, defined by the club head's CoG movement," fit over the same ±2.5 ft window. Explicitly **not the same quantity as club path** — path is an instantaneous impact value; swing direction is a fit over hundreds of pre/post-impact samples. | [Swing Direction](https://www.trackman.com/blog/what-is-swing-direction) CONFIRMED |
| **Low point** | "Distance from the club head's geometric center to the lowest point on the swing arc, at the time of maximum compression." | [40+ Parameters] CONFIRMED |
| **Smash factor** | "The ratio between measured ball speed and measured club speed." | [40+ Parameters] CONFIRMED |

**Important internal inconsistency in TrackMan's own materials, worth carrying into OpenFlight's design (CONFIRMED, both quotes from trackman.com):** one TrackMan page defines attack angle/club path/face angle/dynamic loft all "at the club head's geometric center," while another explicitly says face angle and dynamic loft are measured "at the center-point of contact between club and ball" — and gives worked examples (10 mm toe → +2° face angle; 10 mm low → −2° dynamic loft) showing the contact-point value differs measurably from the CG/face-center value. **This means "face angle" is not one unambiguous physical quantity even in the reference implementation** — a system reporting face angle must state which reference point it uses, and OpenFlight's D-plane inversion (which uses the CG/contact-point velocity, not a face-center-referenced angle) is implicitly using the *contact-point* convention. This is a definition-freeze item, not just a units nit.

### 1.2 Standard relations and published coefficients

**Launch direction (horizontal):**
```
Launch direction ≈ k_h · Face angle + (1 − k_h) · Club path
```
- Wood, Henrikson & Broadie (157 golfers, Vicon @ 720 Hz + GC2, filtered to strikes within ~6.35 mm of face center): **7-iron player k_h = 69% ± 3%** (95% CI); **7-iron robot k_h = 63% ± 4%**; **driver k_h = 76% ± 8%**; **wedge k_h = 61% ± 7%**. [Wood et al. 2018](https://pdfs.semanticscholar.org/3d4d/e973f3d17dc5be3eb63765c3145977128630.pdf) CONFIRMED [PRIOR: `openflight_club_pose_research_survey.md` §G, `openflight_dplane_hardware_followup_research.md` §4.1]
- TrackMan's own marketing/education material states materially higher numbers: driver face-influence "~85%," mid-irons "~75%." [TrackMan blog, various](https://www.trackman.com/blog/golf/6-trackman-numbers-all-amateur-golfers-should-know) CONFIRMED-as-published, but **the 69% (academic, filtered, player-measured) and 75–85% (TrackMan blog, unspecified methodology) numbers disagree by 6–16 percentage points for irons.** UNKNOWN why — plausibly different club/strike populations, marketing rounding, or a different underlying dataset; this discrepancy alone is enough to make any single "the" coefficient unsafe to treat as a universal constant.

**Launch angle (vertical):**
```
Launch angle ≈ k_v · Dynamic loft + (1 − k_v) · Attack angle
```
- Wood et al.: **7-iron k_v = 81% ± 5%**; **driver k_v = 83% ± 8%**; **wedge k_v = 72% ± 6%**. CONFIRMED, same source.

**Spin loft:**
```
Spin loft = Dynamic loft − Attack angle    (TrackMan's own stated approximation, since the
                                             face/path horizontal difference is normally small
                                             relative to the loft/attack-angle vertical difference)
```
CONFIRMED wording from [TrackMan Spin Loft](https://www.trackman.com/blog/spin-loft).

**Spin rate vs. club speed and spin loft:** no single closed-form public equation exists; the qualitative relation is well documented and partially quantified:
- "Each additional degree of spin loft adds roughly 200–300 RPM of backspin" (general clubs); "for drivers specifically, per degree of loft, spin changes by about 400–500 rpm." INFERRED/aggregated-from-multiple-blog-sources, not a single primary formula — treat these numbers as order-of-magnitude, not calibrated coefficients.
- "Attack angle is the most powerful spin lever: each degree of upward attack angle removes ~200–300 RPM of backspin while adding launch angle." Same caveat.
- Source aggregation: [TrackMan spin rate blog](https://www.trackman.com/blog/spin-rate), [Adam Young Golf](https://www.adamyounggolf.com/spin-loft/), [exactgolf driver-loft study](https://exactgolf.com/club-shaft-tests/how-does-driver-loft-influence-the-spin-generated/). INFERRED — no vendor publishes a validated closed-form club-speed/spin-loft→spin-rate equation; the physics-based version is Penner's oblique-impact model [PRIOR: `openflight_dplane_hardware_followup_research.md` §3.2, `spin loft → ω_b` derivation], which *is* closed-form but needs restitution, effective mass, and ball inertia inputs OpenFlight does not independently measure.

### 1.3 Coefficient dependence on club, loft, and impact location

- **Club/loft dependence** (CONFIRMED, Wood et al.): the face-influence fraction rises with loft in the vertical channel (72% wedge → 81% 7-iron → 83% driver) and is highest for driver in the horizontal channel too (76% driver vs. 61–69% wedge/iron). The mechanism (Penner's model, [PRIOR §3.2, §4.2]) is that the coefficient is a nonlinear function of total 3D spin loft and effective mass/restitution — it is not a per-club constant so much as a per-*condition* value that happens to cluster by club because clubs cluster by typical spin loft.
- **Player-vs-robot dependence** (CONFIRMED, same source): the 7-iron robot horizontal coefficient (63% ± 4%) differs from the player value (69% ± 3%) by more than one combined standard error, at fixed club identity — direct evidence the coefficient is condition-dependent, not just club-dependent.
- **Strike-location dependence (gear effect on the coefficient itself):** a 5-iron robot study found horizontal strike location changes initial side angle by **~0.040°/mm (blade)** and **~0.022°/mm (cavity-back)**; after dividing by k_h≈0.69, an unmodeled 10 mm strike offset biases an *inferred* face angle by roughly **0.3–0.6°**, and 20 mm can approach or exceed 1°. [PRIOR: `openflight_club_pose_research_survey.md` §G, citing Corke et al. robot study] CONFIRMED.
- **Impact-point-reference dependence** (CONFIRMED, TrackMan's own worked examples above): 10 mm toe → +2° face angle; 10 mm low → −2° dynamic loft, purely from which point on the face is used as the angle's reference. This is a *definitional* coefficient dependence, distinct from the *physical* gear-effect dependence above, and both apply simultaneously in a real off-center strike.

---

## 2. Useful precision per metric

No manufacturer publishes a formal per-metric accuracy specification (bias ± limits of agreement) for TrackMan, FlightScope, or Full Swing KIT — repeated web searches across vendor spec sheets, manuals, and marketing pages turned up definitions and "the metric is available in the Pro Package" statements, but no stated tolerance in degrees or mm for face angle, dynamic loft, or club path on any of the three comparator devices. **CONFIRMED absence** (via search) that this isn't a lookup failure on my part; it matches the prior finding already logged: "No vendor publishes per-metric club tolerances" [PRIOR: `markerless-club-data-guide-v2-research-corrected.md` §6, tagged INFERRED there — I now treat "no public spec exists" itself as CONFIRMED, while the *implied* accuracy numbers below remain INFERRED].

What *is* published is (a) independent peer-reviewed agreement/reliability studies against a different reference, and (b) coach/fitter action thresholds — i.e., not "how accurate is the box" but "how big a number has to be before a fitter changes something."

| Metric | Published accuracy evidence | What a fitter acts on | What a home user needs | Tag |
|---|---|---|---|---|
| Ball speed, club speed, launch angle | Leach et al. (2017) vs. high-speed video: median differences −0.4 mph (club speed), 0.2 mph (ball speed), 0.0° (launch angle); TrackMan "accurate and valid for field use." [Leach et al.](https://www.researchgate.net/publication/319147118) | Sub-mph/sub-degree differences rarely change a fitting decision | Any single-digit-mph/degree accuracy is plainly sufficient | CONFIRMED (study exists; full text paywalled, so exact table values beyond the median figures above are UNKNOWN to me) |
| TrackMan 4 within/between-session reliability | ICC 0.99 (clubhead speed), 0.97–0.99 (ball speed), 2024 study | N/A (reliability, not accuracy) | N/A | CONFIRMED |
| Face angle | No vendor spec found. Practitioner threshold: "more than ±2° open/closed and dispersion explodes"; "≤1° face-to-path needed for tour-level 300-yard-drive dispersion control." | **±1° actionable, ±0.5° for tour fitting** | ±2–3° is enough to see slice/hook tendency | Fitter threshold CONFIRMED (widely repeated coaching claim, consistent across sources, but not a controlled study); INFERRED for the "tour fitting" tightening |
| Club path | Practitioner threshold: "path beyond ±4.5° struggles with curvature; beyond ±5° is extreme." | **±1–2° actionable** for shot-shape work | ±2–4° adequate for feel/direction feedback | INFERRED from coaching literature, not a device spec |
| Attack angle | No public spec. Coaching literature treats sign and rough magnitude (down for irons, up for driver) as the actionable signal, not fractional degrees. | **±1–2° actionable** when optimizing driver launch/spin | ±2–4° sufficient to confirm "hitting down/up" | INFERRED |
| Dynamic loft | Manufacturing/as-played loft tolerance is **±1–2°** even on a factory-fresh iron, and **carry sensitivity to loft is ~2–4 yards per degree**; a fitter's real-world "true loft" measurement (loft/lie machine) is itself only good to about a degree. | **±1–2°** is the credible fitting bar — tighter than that is below the noise floor of the clubs themselves | ±3–5° is enough to see a spin/launch trend across a set | Manufacturing tolerance and yards/degree CONFIRMED (multiple independent sources agree); "fitting bar" framing INFERRED |
| Spin loft / spin axis | No public device spec. TrackMan spin-axis tennis validation found the *radar* spin-axis measurement itself becomes unstable below 3,000 rpm — i.e., even the reference device has a stated low-spin failure mode. | **±2–3°** actionable for gear-effect/shot-shape diagnosis | ±5° sufficient for curve-direction feedback | Spin-axis instability at low rpm CONFIRMED (tennis validation, same measurement principle); iron/driver fitting threshold INFERRED |
| Impact location (offset/height) | No public device spec; GC-Quad/Uneekor-class systems reach **1–3 mm only with fiducial stickers imaging the face directly** [PRIOR: `markerless-club-data-guide-v2-research-corrected.md` §4.4]. | **5-zone qualitative read (heel/center-heel/center/center-toe/toe, similarly for high/low) is what most fitters actually use day to day**; mm-precision matters mainly for face-technology (bulge/roll) design work, not routine fitting | A single coarse zone (which third of the face) is enough to change a recommendation | Direct-imaging precision CONFIRMED [PRIOR]; "5-zone is what fitters use" INFERRED (consistent with how impact tape/spray and face-impact-location UIs are described, but I found no citation stating fitters formally use 5 discrete zones rather than continuous mm) |
| Swing plane / swing direction / low point | No public device spec. These are fit-over-an-arc quantities (±2.5 ft window), so their precision is bounded by how many independent, well-timed samples span that arc, not by any single-instant angular resolution. | Used more for teaching cues (swing shape) than for hard equipment fitting decisions | Coarse (few-degree) accuracy is adequate — these are diagnostic, not gapping, numbers | INFERRED |
| Smash factor | Ratio of two directly measured speeds; accuracy is whatever the two speed measurements' combined accuracy is (see ball/club speed row above) | Driver fitting: **0.02–0.04 change in smash factor ≈ 10–15 yards of carry** at fixed swing speed, so anything resolving smash factor to ~0.01–0.02 is actionable | Coarse smash factor (0.05 resolution) already useful as a mishit indicator | Yards-per-smash-factor figure INFERRED (web-aggregated, not a controlled study) |

---

## 3. Observability analysis: what OpenFlight's sensor set can support, with arithmetic

Method: for each metric, list the required inputs, propagate OpenFlight's stated input uncertainties through the standard relation from §1.2 using first-order error propagation (as already validated methodologically for this exact pipeline in [PRIOR: `openflight_dplane_hardware_followup_research.md` §1.2, §2.1]), and assign a verdict. I use the **iron coefficients** (k_h = 0.69 ± 0.03, k_v = 0.81 ± 0.05) since the brief's numeric context (attack angle range, "camera+radar" production estimator) is iron-session data.

### 3.1 Smash factor — DERIVABLE NOW (contingent on one unresolved contract risk)

`Smash factor = ball speed / club speed`. Both are already claimed reliable, so this is arithmetic on two numbers OpenFlight already produces. The one load-bearing risk carried from the prior audit is real and specific: if the OPS243 value used as "club speed" is a **line-of-sight radial speed** rather than a **deprojected total speed**, smash factor inherits an angle-dependent bias — e.g. at a 17° vs. 25° launch/approach geometry, the aligned-geometry projection factor moves from ≈0.956 to ≈0.906 [PRIOR: `openflight_dplane_hardware_followup_research.md` §7.3], a ~5% systematic shift, which is larger than the ±0.02–0.04 smash-factor resolution a fitter actually cares about (§2). **Verdict: DERIVABLE NOW for ball speed / club speed as reported, but the reported *smash factor number itself* should be flagged NEEDS-CALIBRATION until the radial-vs-total contract is verified for both speeds** — the ratio of two speeds that are each internally consistent about what "speed" means is fine; a ratio of a radial club speed to a deprojected ball speed is not a smash factor, it's an artifact of geometry.

### 3.2 Club path and attack angle — DERIVABLE AFTER CALIBRATION (current status matches this exactly)

These are already what the production estimator outputs (path +3…+9°, attack angle −2.6…−6.6°, 17/21 plausible). The brief's own framing — "unvalidated" — is the correct verdict given [PRIOR §1.3–1.4]: 4–5 pre-impact frames give a low-noise straight-line *fit*, but nothing in the current pipeline demonstrates that the fitted point is the true contact point rather than a camera centroid / radar scattering center that moves with aspect. The quantified risk from that prior work: a point 25 mm from the true rotation reference, moving at a plausible clubhead angular rate, differs from the reference by **≈1.09 m/s (2.4 mph)** before any translational error is even added [PRIOR: `openflight_dplane_hardware_followup_research.md` §1.4] — comparable in size to the whole club-speed accuracy budget. **Verdict: DERIVABLE AFTER CALIBRATION** — specifically after the same-point/jackknife/geometry-perturbation tests already specified in [PRIOR: `openflight_claude_artifact_dplane_audit.md` Gate 4] pass.

### 3.3 Face angle (via D-plane inversion) — the arithmetic

```
F = P + (H − P) / k_h ,      k_h = 0.69 ± 0.03 (7-iron player)
```

Partial derivatives:
```
∂F/∂P = 1 − 1/k_h = 1 − 1.449 = −0.449
∂F/∂H = 1/k_h = 1.449
∂F/∂k_h = −(H − P)/k_h²
```

Using OpenFlight's stated inputs: σ_H = 0.2° (camera launch direction, reproducible), σ_P = 2.5° (midpoint of the stated "2–3°" fused-path error), and taking a representative face-to-path separation of 3° (so H − P ≈ 0.69 × 3° ≈ 2.07°, since H = P + k_h·(F−P)):

```
Var(F)_random  = (−0.449)² · (2.5)²  +  (1.449)² · (0.2)²  +  [(H−P)/k_h²]² · (0.03)²
              = 0.2016 · 6.25         +  2.10 · 0.04         +  (2.07/0.4761)² · 0.0009
              = 1.260                +  0.084                +  (4.35)² · 0.0009
              = 1.260 + 0.084 + 0.017
              = 1.361 (deg²)

σ_F (random only) ≈ 1.17°
```

**That number alone would be near the ±1° fitter threshold from §2 — plausibly useful.** But it excludes the stated **unresolved ~5° horizontal camera-vs-radar offset**, which is a *systematic* bias in whichever of H or P is not on the camera's own zero reference. If path P is derived through the radar/fused chain and inherits that offset while H (camera launch direction) does not, the offset appears directly inside (H − P):

```
ΔF_bias = Δoffset / k_h = 5° / 0.69 ≈ 7.25°
```

This is **6× larger than the ±1.2° random-error budget** and roughly **7× the fitter-actionable ±1° threshold**. It is large enough to flip the sign of a borderline face angle. **Verdict: NEEDS CALIBRATION — specifically, NOT FEASIBLE NOW, but plausibly DERIVABLE AFTER CALIBRATION once the 5° offset is resolved**, since the underlying random-error arithmetic (≈1.2°) is close to useful. Until the offset is closed, face angle from this route is dominated by an unquantified systematic error nearly an order of magnitude larger than what a fitter needs.

### 3.4 Dynamic loft — the arithmetic (see also the dedicated §4 below)

```
DL = A + (V − A) / k_v ,     k_v = 0.81 ± 0.05 (7-iron)
```
```
∂DL/∂A = 1 − 1/k_v = −0.235
∂DL/∂V = 1/k_v = 1.235
∂DL/∂k_v = −(V − A)/k_v²
```

Using σ_V = 0.2° (camera vertical launch, reproducible), σ_A = 2.5° (production attack-angle estimator, same order as path), and a representative (V−A) ≈ 21° (e.g., V≈17°, A≈−4°, giving DL≈21.9°, a plausible 7-iron dynamic loft):

```
Var(DL) = (0.235)² · (2.5)²  +  (1.235)² · (0.2)²  +  [(V−A)/k_v²]² · (0.05)²
        = 0.0552 · 6.25       +  1.525 · 0.04        +  (21/0.6561)² · 0.0025
        = 0.345               +  0.061                +  (32.0)² · 0.0025
        = 0.345 + 0.061 + 2.56
        = 2.97 (deg²)

σ_DL ≈ 1.72°
```

This is **borderline-useful against the §2 fitter bar of ±1–2°**, and the coefficient-uncertainty term (2.56 of the 2.97 deg² total) dominates — not the sensor noise. That means the single highest-value fix for dynamic-loft accuracy is **narrowing the coefficient uncertainty** (calibrating k_v for this specific club/strike population instead of using the ±5% Wood et al. player-population spread), not further sensor denoising. **Verdict: DERIVABLE AFTER CALIBRATION**, with attack-angle robustness (removing the two gross ±20–30° failures noted in the production estimator, [PRIOR §2.3]) and coefficient narrowing as the two concrete gates. This is a materially more optimistic verdict than face angle, because dynamic loft's inversion has no equivalent to the 5° horizontal-offset bias in the brief's stated numbers — if a comparable unresolved *vertical* offset exists between camera and radar zero references, that risk transfers here identically and I could not find or derive its size from the given context (**UNKNOWN**; would need the same offset-characterization work done for the horizontal axis).

### 3.5 Face-to-path and spin loft — DERIVABLE AFTER CALIBRATION, inheriting the worse of their inputs

```
Face-to-path = F − P              → inherits face angle's ~7° systematic risk (§3.3) until resolved
Spin loft    = DL − A             → σ ≈ sqrt(σ_DL² + σ_A²) ≈ sqrt(1.72² + 2.5²) ≈ 3.0°
```
Spin loft's ~3° propagated uncertainty is workable as a *diagnostic* (is spin loft roughly "iron-like" ~20–24° or "driver-like" ~10–15°) but not fine enough for the sub-degree distinctions coaches use spin loft for (optimizing "less spin loft, more speed" swing changes typically targets 1–2° adjustments). **Verdict: DERIVABLE AFTER CALIBRATION for coarse classification; NEEDS further calibration for fine coaching-grade spin-loft feedback.**

### 3.6 Spin axis — NOT FEASIBLE ON THIS HARDWARE (for a confident number) / DERIVABLE only when radar spin is present

Spin axis requires actual 3D ball spin, not a value re-derived from the same D-plane kinematics that would make it circular evidence [PRIOR: `openflight_dplane_hardware_followup_research.md`, Empirical Premises table: "Project formula reproduces itself exactly. Excluded as independent validation evidence."]. OpenFlight's spin rate is "sometimes" available from radar. Even TrackMan's own radar-based spin-axis measurement — the reference-class implementation — is documented as unstable below ~3,000 rpm in an independent tennis validation using the same underlying radar-spin principle (CONFIRMED, cross-sport but same physics). Iron shots often sit near or above that threshold, drivers can sit below it in some swing profiles. **Verdict: DERIVABLE only on the subset of shots where radar spin is actually captured and above the instability floor; NOT FEASIBLE as a general per-shot metric on this hardware today.**

### 3.7 Swing plane, swing direction, low point — NEEDS OPTICAL CLUB OBSERVATION (a different kind of observation than path/AoA)

These are explicitly **not** impact-instant quantities — TrackMan fits them over roughly a 2.5-ft-before to 2.5-ft-after-impact arc, using hundreds of position samples (§1.1, CONFIRMED). OpenFlight's current club-side observation window is 4–5 frames tight around impact, built for a point velocity estimate, not an arc fit. Extending to swing plane/direction needs sustained mid-swing tracking at a distance from the ball where the head is moving fast, is farther from the fixed impact-zone camera geometry, and is not motion-blur-frozen by the impact-triggered capture window. Low point additionally needs a calibrated ground-plane reference the club-only kinematics don't currently provide. **Verdict: NEEDS OPTICAL CLUB OBSERVATION — specifically a wider capture window and (per the prior camera-feasibility work) very likely a second synchronized view, not a bigger radar aperture; this is a capture-architecture change, not a coefficient-calibration one.**

### 3.8 Impact location — see dedicated §5. Headline verdict: coarse (~±5 mm) zone is DERIVABLE NOW as already claimed; mm-precision offset/height is NEEDS OPTICAL CLUB OBSERVATION (stereo), matching [PRIOR: `markerless-club-data-guide-v2-research-corrected.md` §4.4] exactly.

### 3.9 Dynamic lie / head roll — NOT FEASIBLE ON THIS HARDWARE

No relation in §1 recovers this from ball flight; it requires full 6-DoF head-pose observation the current silhouette route cannot deliver at any useful precision (±11° face orientation ceiling already established). **Verdict: NOT FEASIBLE ON THIS HARDWARE** without a fundamentally different (stereo/marker-assisted reference) capture path.

### 3.10 Summary table

| Metric | Needed inputs | Propagated/estimated uncertainty | Verdict |
|---|---|---|---|
| Smash factor | ball speed, club speed | inherits both; ~5% systematic risk if radial/total contract unresolved | DERIVABLE NOW, flag the speed-contract risk |
| Club path, attack angle | fused clubhead velocity vector | already the production estimator's stated range; same-point assumption unproven | DERIVABLE AFTER CALIBRATION |
| Face angle | launch direction (H), club path (P), k_h | ~1.2° random; **~7.25° systematic from the unresolved 5° offset** | NEEDS CALIBRATION (offset closure) → then DERIVABLE AFTER CALIBRATION |
| Dynamic loft | vertical launch (V), attack angle (A), k_v | ~1.7°, coefficient-uncertainty-dominated | DERIVABLE AFTER CALIBRATION |
| Face-to-path | F, P | inherits face angle's systematic risk | NEEDS CALIBRATION |
| Spin loft | DL, A | ~3.0° | DERIVABLE AFTER CALIBRATION (coarse only) |
| Spin axis | independent 3D ball spin | radar spin "sometimes," unstable <3,000 rpm even on reference hardware | NOT FEASIBLE generally / DERIVABLE when radar spin present and high enough |
| Swing plane, swing direction, low point | continuous pre/post-impact head trajectory over ~1.5 m of arc | not attempted by current 4–5-frame window | NEEDS OPTICAL CLUB OBSERVATION (wider window / second view) |
| Impact location (zone) | ball-vs-head-outline | ~±5 mm | DERIVABLE NOW (coarse) |
| Impact location (mm offset/height, Trackman-format) | full 6-DoF head pose, face origin/axes | current: ill-posed (two translational DoF unresolved by tangency alone, [PRIOR §5.1]) | NEEDS OPTICAL CLUB OBSERVATION (stereo) |
| Dynamic lie / head roll | full 6-DoF head pose | silhouette ceiling ±11° | NOT FEASIBLE ON THIS HARDWARE |

---

## 4. Dynamic loft, specifically

**Can it be inferred from vertical launch + attack angle (spin loft) for irons with useful precision?**

The arithmetic in §3.4 gives σ_DL ≈ 1.7°, against a fitter-actionable bar of ±1–2° (§2) and a manufacturing/carry-sensitivity reality where clubs themselves vary ±1–2° and every degree is worth 2–4 yards of carry. **This lands right at the edge of usefulness** — not comfortably inside it. Two things would move it decisively into the useful range: (a) tightening k_v from the ±5% population spread to a calibrated, this-rig value (the dominant error term, §3.4), and (b) confirming attack angle doesn't carry the same kind of unresolved cross-sensor offset that face angle does on the horizontal axis (currently UNKNOWN for the vertical axis).

**What breaks it — strike height / gear effect on irons, quantified:**

The vertical-relation coefficient (k_v) already *is* a strike-averaged empirical value; the question is how much an individual off-center strike (not represented by the average k_v) perturbs the specific shot's true dynamic loft away from what the linear inversion would report. The load-bearing physical fact, quantified two ways:

1. **CG-to-face distance is the whole story for gear effect, and irons have almost none of it.** A 7-iron CAD model's CG sits **≈0.084 in ≈ 2.13 mm** behind the face; a modern low-CG driver's CG sits on the order of **30–40 mm** behind the face (INFERRED range — this is common driver-design knowledge, not independently re-derived here). That's roughly a **15–19× smaller moment arm** for irons. Tutelman's gear-effect spin relation, `GE_spin = 20.4 · V_b · C · x / I` (V_b = ball speed, C = CG-to-face distance, x = strike offset from CG-normal, I = clubhead MOI), is **linear in C** — so for the same strike offset x and a comparable MOI, an iron's gear-effect spin contribution is roughly **1/15 to 1/19 of a driver's**. [Tutelman formula via GolfWRX summary](https://forums.golfwrx.com/topic/1916849-gear-effect-in-irons-diagrams-and-calcs-using-tutelman-cad-model-cgmoi-mpf-data-implications-hybrids-flyers/) INFERRED (formula reported secondhand; I could not reach tutelman.com directly — its page returned 404 — so the exact formula is CONFIRMED-as-reported-elsewhere but not verified against the primary source).
2. **Direct statement from the design literature:** "most iron clubs do not impart appreciable gear-effect spin to off-center hits, since the CG of most irons... is located very close to the striking face." CONFIRMED, multiple independent sources agree qualitatively even where they don't agree on exact numbers.

Net for OpenFlight: a strike-height error of, say, 10 mm on a 7-iron produces a gear-effect-driven *dynamic loft/spin* perturbation roughly an order of magnitude smaller than the same 10 mm error would produce on a driver. This is good news for the D-plane approach specifically on irons — it means the single biggest confound that could make the linear inversion's fixed k_v wrong on a given shot (an unmodeled off-center strike) is a small effect for irons, reinforcing that **§3.4's ~1.7° estimate is not being silently corrupted by strike-height variation** the way it would be for a driver. The corresponding statement for drivers is the opposite: gear effect is large enough (§5 below) that k_v's driver-population spread (83% ± 8%, nearly double the 7-iron's ±5%) is itself partly *caused by* uncontrolled strike-location variation across the sampled drives, not just measurement noise.

---

## 5. Impact location, specifically — what ball-flight signatures could confirm or refine a coarse optical zone

**Driver — usable cross-check exists, order-of-magnitude quantified:**
- **Vertical strike offset → spin-rate change (vertical gear effect):** "each mm of vertical offset can shift spin by ~100–200 rpm at higher ball speeds," and "moving from low to high on the face changes spin by over 1,000 rpm total." INFERRED (web-aggregated, not a controlled study I can independently verify, but the direction and rough magnitude are consistent with established gear-effect physics).
- **Horizontal strike offset → spin-axis tilt (horizontal gear effect) → curvature.** Same underlying mechanism, orthogonal axis; magnitude not separately quantified in the sources found (**UNKNOWN** exact rpm-axis-tilt/mm figure for driver, though the qualitative mechanism is CONFIRMED — see Titleist's gear-effect explainer).
- **Speed loss (smash factor):** one aggregated source states "a heel or toe strike costs 5–10 mph of ball speed vs. center strike at the same swing speed," and "a 1.25 smash factor with a driver is losing 20+ yards." INFERRED, and plausibly on the high end — I could not find a controlled study pinning this to a specific mm offset, only qualitative/example figures.
- **How this could refine a coarse optical zone:** if OpenFlight's optical route says "roughly toe-side, roughly high," a driver shot showing an unusually large smash-factor loss *and* a spin-axis tilt toward the toe-side direction is corroborating evidence, and could plausibly narrow "coarse toe" toward "toe, probably 15–20 mm rather than 5 mm" using the rpm/mm figures above as a rough scale — but only as a **Bayesian nudge on an already-coarse estimate**, not an independent mm measurement, because ball speed and spin also respond to attack angle, dynamic loft, and swing-speed variation the system cannot fully separate from strike-location effects in a markerless, non-controlled-robot setting.

**Iron — the cross-check is essentially unavailable, quantified via §4's CG-distance argument:**
- Because iron gear effect is ~15–19× weaker than driver gear effect for the same strike offset (§4), the spin/launch signature a given mm of iron mis-strike produces is roughly an order of magnitude below what the same mm of driver mis-strike produces. Given that OpenFlight's own spin-rate channel is only sometimes available and, even on reference hardware, becomes unreliable below ~3,000 rpm (§3.6), an iron strike-location signal this weak is very likely **below the noise floor of the spin measurement itself**, before even reaching the harder problem of disentangling it from attack-angle/loft variation shot to shot.
- Iron speed loss from mis-strike is real (perimeter-weighted "game-improvement" iron design exists specifically to blunt it) but is not separately quantified anywhere I found in mm terms, and — per the design literature — irons "rely more on face-angle changes for shot correction" than gear effect, meaning an iron mis-strike's ball-flight signature is dominated by whatever face-angle/path error accompanied it, not by the strike location itself. This makes iron ball-flight data a poor, likely unusable, corroborator for a coarse optical impact-location read. **Verdict: for irons, ball-flight cross-checking of impact location is NOT FEASIBLE as a refinement path on this hardware; for drivers, it is a weak, order-of-magnitude corroborator only (DERIVABLE AFTER CALIBRATION, "coarse-refines-coarse," not "coarse-becomes-precise").**

---

## 6. Metric dependency graph — what unlocks what, and the sequencing that maximizes what a user sees soonest

```
[FOUNDATION — already reliable]
  Ball speed ── Ball launch (H,V), 0.2° reproducible ── Spin rate (sometimes)
                          │
                          ├─────────────► Smash factor = ball speed / club speed
                          │                 (needs: club speed; risk: radial-vs-total speed
                          │                  contract on BOTH speeds — verify before trusting
                          │                  the ratio, §3.1)
                          │
  Club speed (OPS243) ────┘
                          │
[TIER 1 — needs the fused clubhead vector, currently experimental]
                          ▼
        Fused clubhead velocity vector (radar radial + camera transverse)
                          │
                          ├──► Club path ─┐
                          │               ├──► [same-point / jackknife / geometry-perturbation
                          └──► Attack     ┘      gates from PRIOR Gate 4 — until these pass,
                               angle             everything downstream inherits their error]
                          │
[TIER 2 — needs Tier 1 PLUS the 5° horizontal offset resolved PLUS a calibrated coefficient]
                          ▼
        D-plane inversion (F = P + (H−P)/k_h ;  DL = A + (V−A)/k_v)
                          │
                          ├──► Face angle ──────────► Face-to-path
                          │        (blocked on the         (inherits face angle's
                          │         5° offset today,         blocking issue)
                          │         §3.3)
                          │
                          └──► Dynamic loft ────────► Spin loft = DL − A
                                   (closest to usable        (coarse classification
                                    today, §3.4, §4;          usable sooner than fine
                                    dominated by coefficient   coaching-grade feedback,
                                    uncertainty not sensor     §3.5)
                                    noise — cheapest fix
                                    available)
                          │
[TIER 3 — needs an independent measurement, not derivable from the above]
                          ▼
        Spin axis  (needs real 3D ball spin; only available when radar spin fires AND
                     is above the ~3,000 rpm instability floor that even reference
                     hardware shows, §3.6 — cross-checked against Face-to-path/gear-effect
                     model once both exist, but never *derived from* it alone)
                          │
[TIER 4 — needs a different capture architecture, not just calibration]
                          ▼
        Swing plane, Swing direction, Low point
             (need continuous ~2.5-ft-before/after-impact tracking — a wider capture
              window than the current 4–5-frame impact bracket, likely a second
              synchronized view; this is the same "second camera" investment the
              prior camera-feasibility work already flags for face/loft/impact-location,
              so it should be planned as one hardware decision, not four separate ones)
                          │
                          ▼
        Impact location, mm-precision (offset/height, TrackMan format)
             (needs full 6-DoF head pose + face origin/axes — NOT unlocked by
              face angle + dynamic loft + ball position alone, PRIOR §5.1;
              needs the same second-view/stereo investment as Tier 4)
                          │
                          ▼
        Dynamic lie / head roll  (NOT FEASIBLE without that same investment;
                                   lowest priority — no evidence any comparator's
                                   home-tier product ships it either)
```

**Sequencing that maximizes what a user sees soonest, in order:**

1. **Smash factor** — already have both inputs; only needs the speed-contract check (§3.1), not new capability. Ship first, with the caveat flagged.
2. **Club path / attack angle**, published as "experimental" exactly as the brief already frames them, while Gate-4-style validation runs in the background — these unlock everything in Tier 2 and are the correct next investment because they're a calibration/validation problem, not a hardware problem.
3. **Dynamic loft / spin loft (coarse)** — the arithmetic in §3.4 puts this closest to a genuinely useful number among the D-plane outputs, and its dominant error term (coefficient uncertainty) is the cheapest one to fix (calibrate k_v on this rig's own data rather than importing Wood et al.'s population spread). Ship before face angle.
4. **Face angle / face-to-path** — hold until the 5° horizontal offset is closed; publishing it before that risks a ~7° systematic error that is large enough to invert a fitting recommendation (§3.3). This is the one metric where sequencing ahead of its blocker would be actively harmful, not just premature.
5. **Impact location, coarse zone** — already independently derivable now (~±5 mm optical read); ship in parallel with the above since it doesn't depend on the D-plane chain at all. Do not attempt driver ball-flight refinement of it until face angle/spin loft are trustworthy, since the refinement itself depends on them (§5).
6. **Spin axis** — ship opportunistically whenever radar spin fires and clears the ~3,000 rpm floor; do not promise it as a per-shot guarantee.
7. **Swing plane / swing direction / low point / mm-precision impact location / dynamic lie** — bundle as one future hardware decision (wider capture window, likely second synchronized camera), since they share the same missing capability (continuous multi-point head-pose observation) rather than needing four separate solutions.

---

## Sources consulted directly in this pass

- TrackMan glossary/blog pages (fetched): [Club Data Definitions](https://www.trackman.com/blog/golf/club-data-definitions), [40+ TrackMan Parameters](https://www.trackman.com/blog/golf/40-trackman-parameters), [Spin Loft](https://www.trackman.com/blog/spin-loft), [Swing Plane](https://www.trackman.com/blog/what-is-swing-plane), [Swing Direction](https://www.trackman.com/blog/what-is-swing-direction), [6 numbers amateurs should know](https://www.trackman.com/blog/golf/6-trackman-numbers-all-amateur-golfers-should-know)
- Peer-reviewed/near-primary: Wood, Henrikson & Broadie (D-plane coefficients) — already in PRIOR set; Leach et al. 2017 (TrackMan vs. high-speed video accuracy) — [ResearchGate record](https://www.researchgate.net/publication/319147118) (full text paywalled; median-difference figures taken from search-indexed abstract/summary, tagged CONFIRMED at that level only); TrackMan tennis radar validation (spin-axis instability <3,000 rpm) — [SAGE journal record](https://journals.sagepub.com/doi/abs/10.1177/1754337120953005)
- Gear effect / CG-distance: [GolfWRX Tutelman/CAD-model thread](https://forums.golfwrx.com/topic/1916849-gear-effect-in-irons-diagrams-and-calcs-using-tutelman-cad-model-cgmoi-mpf-data-implications-hybrids-flyers/) (secondhand formula report — primary tutelman.com page 404'd), [Titleist gear-effect explainer](https://www.titleist.com/learning-lab/performance/golf-gear-effect), [Plugged In Golf ball-flight-laws #5](https://pluggedingolf.com/ball-flight-laws-5-gear-effect/)
- Fitting-tolerance/carry-sensitivity aggregation: multiple club-fitting/loft-chart pages (Hireko, MyGolfSpy, Wishon Golf, Mitchell Golf) cross-checked for the "±1–2° manufacturing tolerance, 2–4 yd/degree" figures; no single controlled study, so tagged INFERRED where used quantitatively
- No public per-metric accuracy specification was found for TrackMan 4, FlightScope Mevo Gen2, or Full Swing KIT despite direct searches of vendor spec/manual pages — confirmed absence, not a search failure
- PRIOR documents extended (not repeated) in this pass: `openflight_dplane_hardware_followup_research.md` §1–4, 7; `openflight_claude_artifact_dplane_audit.md` (D-plane usage rules, Gate sequence, impact-location DoF); `openflight_club_pose_research_survey.md` §G; `markerless-club-data-guide-v2-research-corrected.md` §4, §6
