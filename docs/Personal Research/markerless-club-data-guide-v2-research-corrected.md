# Markerless Camera-Based Club Data for OpenFlight — Implementation Guide (v2, research-corrected)

> **Status:** This supersedes `compass_artifact_wf-e4f007fc-...md` (v1, preserved). v2 folds in (a) an audit of v1's claims against the **live OpenFlight repo**, (b) fact-checking of every external citation/spec/patent/benchmark, and (c) deep competitive research into how Trackman, FlightScope, Foresight, Uneekor, Rapsodo, and the PiTrac open-source project actually solve each problem. Claims are tagged **[CONFIRMED]** (primary/authoritative source), **[INFERRED]** (reasoned), or **[UNKNOWN]** (proprietary/undisclosed).
>
> Constraints driving v2 (from the maintainer): **cheap-first (~$120–300 capture hardware)**, **single camera first** (stereo as the documented fallback), **must work BOTH indoors and outdoors**, markerless, behind-the-ball.

---

## 0. What changed from v1 (the deltas that matter)

1. **No shipping launch monitor derives face angle / dynamic loft from a single markerless behind-ball camera.** Behind-ball consumer units (Trackman 4, FlightScope, Full Swing KIT, Rapsodo, Garmin R10) are **radar-primary**; the camera only assists. Optical club-pose systems (GCQuad, Uneekor, Garmin R50, Rapsodo) are **stereo/multi-camera from the side or overhead, often with markers**. Your goal is genuinely *ahead of* every shipping product — pioneer accordingly. **[CONFIRMED]**
2. **The "ambient daylight, no IR strobe" premise is replaced.** Because you now require **indoor + outdoor** and need the camera (not radar) to carry face/loft, you need **active illumination: mono global-shutter + pulsed near-IR (940nm) + a narrowband bandpass filter.** Indoors this is mandatory; outdoors it's workable in shade/overcast and marginal in direct sun on a cheap LED. **[CONFIRMED physics + product precedent]**
3. **The XTR-trigger exposure floor is real:** exposure = (low-pulse width) **+ 14.26µs**, so v1's 4.5–9µs blur target is unreachable via that path. Freeze motion with the **light-pulse width**, not the shutter. **[CONFIRMED]**
4. **Use a MONO sensor** (IMX296LLR via Arducam/InnoMaker, or OV9281), not the color RPi GS camera — ~3× more light at short exposure + native NIR. **[CONFIRMED]**
5. **Template + user loft-override is the right mechanism** for the behind-ball constraint (it mirrors FlightScope's generic, user-adjustable clubhead-dimension calibration) — but it is a *workaround the pros engineered away by direct measurement*, not a replication of their method. Loft override should tilt the template **face plane**. **[CONFIRMED practice + INFERRED design]**
6. **FoundationPose requires RGB-D** (no official RGB-only path); for monocular RGB the realistic render-and-compare ceiling is **MegaPose**. **[CONFIRMED]**
7. **OpenFlight already has a `camera/` module** (ball tracking, rolling-shutter HQ + IR, currently disabled) — reuse its ring-buffer / Mock / HoughCircles scaffolding; the new club path is a *new global-shutter capture backend*, not greenfield. The `on_shot` shot-callback seam the integration plan needs is real. **[CONFIRMED in repo]**
8. **Realistic accuracy bar:** face angle & dynamic loft **±3–5°** single-camera (±2° likely needs stereo); impact location as a **zone**, not mm. **[INFERRED from benchmarks]**
9. **Radar spin is a dead end — and OpenFlight's own code proves it.** `spin_estimate.py` documents r ≈ +0.19 correlation with TrackMan; the repo already replaced measured radar spin with a kinematic formula. The real spin fix is **camera + marked ball, reusing the same rig** (see §6A). **[CONFIRMED in repo]**
10. **The radar subsystem itself is changing** (K-LD7 angle radars → OPS-coherent receive-only antennas; see §1A). This *helps* the camera plan: it sharpens the division of labor (radar = kinematics, camera = face/loft/impact/spin) and gives the camera a better, more reliable motion prior. **[per OpenFlight maintainer, 2026-06]**

---

## 1. How the commercial systems actually work (the evidence base)

| System | Placement | Primary club sensor | Camera role | Cameras | Illumination | Strobe? |
|---|---|---|---|---|---|---|
| **Trackman 4** (outdoor) | Behind ball, ~7ft, down-line | **Dual radar** (10GHz + 24GHz, 40k samp/s) | "4D silhouette" + impact location enhancement | **1**, 720p@60 / 1080p@45 | Ambient | **No** [CONFIRMED] |
| **Trackman iO** (indoor) | Overhead ceiling | 24GHz radar + camera | High-speed club/ball + spin at impact | **2** (≤4600fps + alignment) | **Embedded IR 810–850nm** | Active IR [CONFIRMED] |
| **FlightScope Mevo+/X3** | Behind ball, ~8ft | **3D radar ("Fusion Tracking")** | 2D position accuracy + face impact location (≥300 lux) | 1 | Ambient | **No** [CONFIRMED] |
| **Full Swing KIT** | Behind ball | Radar (24GHz) | Swing **video only** | 1 (4K) | Ambient | No [CONFIRMED] |
| **Rapsodo MLM2PRO** | Behind ball | Radar + cameras | Ball/spin (marked ball) + video | 2 (~240fps) | Ambient | No [CONFIRMED] |
| **Garmin R10 / R50** | R10 behind / R50 side | R10 radar / **R50 3-camera photometric** | R50 measures club optically | R10:0 / R50:3 | Ambient/IR | — [CONFIRMED] |
| **Foresight GCQuad** | **Side** of ball | **4-camera photometric** (6000fps) | *Is* the sensor | **4** | **Built-in IR strobe** | **Yes — works in direct sun** [CONFIRMED] |
| **Uneekor EYE XO** | **Overhead** | **2-camera photometric** (≥3000fps) | *Is* the sensor | 2 | IR | Yes [CONFIRMED] |
| **PiTrac** (open-source) | Behind ball | **Cameras only** (no radar) | *Is* the sensor (stereo) | **2** IMX296 mono | **850nm IR strobe + longpass filter** | **Yes — indoor only** [CONFIRMED] |

**The load-bearing takeaways:**
- **Behind-ball + markerless face/loft does not exist as a product.** Every behind-ball unit that reports face/loft gets it from **radar**, not the camera. **[CONFIRMED]**
- OpenFlight's radars **cannot** fill that role: the OPS243-A is a single-axis Doppler speed sensor; the two K-LD7s give ball/club *path and launch angle*, not *clubface orientation*. So for OpenFlight, **the camera must carry face/loft** — which is exactly the unproven part. **[CONFIRMED hardware roles]** (The K-LD7s are being replaced by an OPS-coherent receiver — see §1A — but the conclusion is unchanged: no OpenFlight radar, current or planned, sees clubface orientation.)
- The **closest working precedents** to your build are **(a) Trackman's patent** (single behind-ball camera + stored club model + radar timing — see §10) and **(b) PiTrac** (behind-ball, camera-only, but **stereo** + IR strobe + indoor-only). Both tell you the same thing: **single-view markerless club pose needs a strong shape prior, and depth disambiguation usually wants a second camera.** **[CONFIRMED]**

---

## 1A. Radar architecture in transition — and why it strengthens the camera plan

**[per OpenFlight maintainer, June 2026 — supersedes the dual-K-LD7 assumption in v1]**

The maintainer is replacing the angle-measurement radar. The two **K-LD7 FMCW radars** (launch angle + club path, ~±2° vs TrackMan) have a fatal hardware limit: their **maximum unambiguous Doppler speed is ~100 km/h**, so faster balls **alias ("wrap around")** — and balls at **~118–128 mph fold back to nearly *zero* measured speed**, exactly where ground/body/club/background clutter lives, so the ball signal is buried and lost. That can't be tuned away in software; it's intrinsic to that module's waveform.

**The new direction:** keep the reliable **OPS243-A as the transmitter** and add a **receive-only antenna/PCB** that listens to the *same* transmitted wave, recovering angle from the **phase difference between receivers (interferometry)**. The maintainer is designing a custom cheap PCB because no off-the-shelf part fits.

**Why this is the right move (and validates the project's direction):**
- This is **bistatic/multistatic interferometry off one coherent transmitter** — exactly how the commercial systems get angles. Trackman uses dual radar with **≥3 non-colinear receivers**; FlightScope's Mevo+/X3 use a **custom phased-array 3D Doppler radar with multiple H+V receive pairs**. **Neither uses anything like a K-LD7.** Two independent FMCW K-LD7 units — each with its own oscillator, not phase-coherent with the OPS, each with its own aliasing/clutter limit — were always a weaker stand-in for a coherent multi-receiver array. **[CONFIRMED commercial architecture]**
- A receiver phase-locked to the OPS shares its good waveform/timing, sidesteps the K-LD7 speed-fold, and **scales**: more receivers → better angle, and eventually the *possibility* of radar spin-axis via interferometry (the ≥3-receiver trick Trackman uses).

**What it means for the camera subsystem — net positive, three ways:**
1. **The division of labor sharpens toward the Trackman model.** Radar handles **kinematics** (ball/club speed, club path, launch & attack angle) — and better after the upgrade; the **camera** handles what radar fundamentally can't: **face angle, dynamic loft, impact location, spin (rate + axis)**. Disjoint — the radar change doesn't touch the camera's job.
2. **A better, more reliable motion prior for single-view pose.** §2.4 notes the camera leans on the radar velocity/angle prior to break single-view ambiguity. Better angle accuracy and **no fast-ball dropout** means that prior is sharper *and actually present on fast shots*.
3. **Shared sync backbone.** Both the new RF receiver and the camera need precise timing against the OPS impact event. The **Pico trigger/timestamping** planned for the camera is the same class of hardware the coherent-receiver approach needs — share it, don't duplicate.

**What does NOT change:** the camera still anchors to the **OPS/sound impact timestamp** (an OPS event, not a K-LD7 one). The camera never depended on the K-LD7s for its core job; it only used them as a *weak corroborator* for club path/attack — and that corroborator is being **upgraded, not removed**. Nothing in the camera plan regresses; several inputs improve.

**Honest caveat:** the radar upgrade is active R&D with TBD accuracy. Keep the camera **self-sufficient** for face/loft/impact/spin; treat improved radar kinematics as a *bonus prior*, not a dependency. **[INFERRED]**

---

## 2. The core problem: markerless 6-DOF clubhead pose from behind

You never see the face. You recover the clubhead's rigid-body **orientation**, then read the face plane off a **club-type template**. v1's pipeline framing stands; the corrections are *how the pros actually do the pose step*:

### 2.1 Trackman's method (from US 10,953,303 — the real blueprint) [CONFIRMED from patent]
- Detect **generic fix-points** (hosel, toe) and **fix-lines** (the shaft) on the *unmarked* club — NOT a full silhouette-to-CAD fit in the claims (though product marketing says "4D silhouette," so the shipping system likely **blends fix-points + silhouette/model fitting** — **[INFERRED]**).
- **Retrieve "known geometric properties" of the club** (a stored geometric model) and fit it best-as-possible to the measured fix-points/lines. *This is the template approach, in a granted Trackman patent.*
- Radar supplies the **impact instant** (velocity discontinuity) and **velocity**; the camera supplies **per-frame geometry**; the two are **synchronized**.
- **Face normal** is taken from the fitted model → face angle (vs target line) and dynamic loft (vs horizon). **Impact location** = the ball-contact point mapped into the fitted club's **face coordinate frame**.

### 2.2 Candidate estimators for OpenFlight (reprioritized)
- **(a) Segmentation → silhouette** — your BiRefNet strength; necessary precursor. *Keep first.*
- **(d) Silhouette analysis-by-synthesis** (render template at hypothesized pose, maximize mask IoU/chamfer) — **promote to co-primary.** From directly behind, visible features (crown/topline/back) are **near-coplanar**, which makes **keypoint→PnP poorly conditioned for the out-of-plane rotation that *is* face angle.** Full-silhouette fitting conditions the rotational DOFs better and is transparent/tunable for the sim-first stage. **[INFERRED — important]**
- **(b) Keypoints + PnP (PVNet-style)** — still valuable and debuggable, but treat as **co-equal with (d)**, not the guaranteed production winner. PVNet's occlusion-robust voting is real (arXiv:1812.11788, 25fps breakdown verified), but it can't manufacture geometric spread the rear view lacks.
- **(c) Render-and-compare (MegaPose / FoundationPose)** — accuracy ceiling experiment on the ROCm box. **Note: FoundationPose requires RGB-D (no official RGB-only path) — use MegaPose for monocular RGB**, or inject depth via the model-scale prior. (arXiv:2212.06870 / 2312.08344 verified.)

### 2.3 The impact-instant insight (validated by patent) [CONFIRMED]
At ≤60fps, the nearest captured frame is up to **±8ms from impact** (the head moves ~37cm in that window). You will **rarely catch a contact-instant frame.** Like Trackman, you **capture frames bracketing impact, recover pose per frame, and interpolate/extrapolate the pose to the radar-defined impact time** (sub-frame), using the radar velocity. This *relaxes* the frame-rate requirement and makes your "multi-frame kinematic consistency" idea load-bearing rather than optional.

### 2.4 Single-view ambiguity — what actually breaks it
- **Strong shape prior** (known template size → scale fixes depth via apparent size). This is what lets Trackman use *one* camera. **[CONFIRMED via patent]**
- **Radar gives velocity & angles, NOT absolute 3D position** in the camera frame. Depth comes from the scale prior; absolute position from the calibrated world frame + the **tee/ball anchor**. v1 conflated "trajectory" with "position." **[INFERRED — correction]**
- **Plan for stereo as a probable necessity for face/loft.** Rapsodo's patent needed stereo (or a shaft marker); PiTrac uses stereo. Single-view + radar prior may get impact location and path, but **±2° face/loft likely requires the 2nd camera.** Elevate this from v1's "if stuck" to "expected." **[INFERRED — strong]**

---

## 3. Capture & illumination subsystem (rewritten)

This is the section most changed by the indoor+outdoor requirement.

### 3.1 Why you need active illumination now [CONFIRMED physics]
- To freeze a 45 m/s clubhead you need ~tens-of-µs effective exposure.
- **Indoors** (~300–750 lux, ~100–300× dimmer than sun) at 15–30µs you collect ~10⁵–10⁶× fewer photons than a normal video frame → **a black frame.** Indoor short-exposure capture **requires** active light. There is no ambient-only indoor path.
- **The XTR-trigger floor:** exposure = low-pulse width **+ 14.26µs**, so you cannot reach single-µs exposures via the trigger. **Freeze motion with the *light pulse*, not the shutter** — run a longer exposure (≥~15µs) but a **microsecond IR strobe pulse** in a filtered band; blur is set by the pulse width. This is the PiTrac/GCQuad technique.

### 3.2 The strobe-vs-sun verdict (resolving the maintainer's doubt) [CONFIRMED]
- **Trackman 4 outdoors doesn't strobe — because it's radar-primary** and its camera never optically freezes the club. That is *not* available to you, because you need the camera for face/loft. So you can't copy "no strobe outdoors" without copying "radar does the club data," which your radar can't.
- **Strobe *can* work outdoors:** the GCQuad is camera-primary, IR-strobed, and rated for direct sun (degrades in glare). The recipe is **multiplicative**: short exposure (limits accumulated ambient) × **narrowband NIR filter** (~52nm FWHM rejects ~97.5% of solar flux) × **940nm** (sits in the atmospheric water-vapor absorption dip → ~2–3× less competing sun than 850nm, and invisible) × **high peak power**.
- **The cheap reality:** a commodity overdriven IR LED array beats indoor light easily but is **marginal against direct midday sun.** Beating full sun reliably needs xenon or a high-power IR array (e.g., Smart Vision Lights XR256-850, ~2000W peak) — outside the cheap budget. **Honest expectation: excellent indoors; good outdoors in shade/overcast/morning/evening; degraded in direct bright sun.**

### 3.3 Recommended cheap capture stack (~$90–280) [CONFIRMED parts/prices]
- **Camera:** **InnoMaker IMX296 *Mono* GS with trigger (~$47)** — only module marketing *both* external trigger and strobe output; native Pi `imx296` driver; 3.45µm pixel; same sensor PiTrac uses.
  - Speed-biased alt: **Arducam OV9281 mono (~$30–40)** — 1MP, NIR-optimized QE, ROI-crop to a strip for **~250fps**; trades resolution for frame rate + better NIR.
  - **Avoid the color RPi GS camera** for this (Bayer filter loses ~2–3× light at short exposure).
- **Trigger/timing:** **Raspberry Pi Pico (~$4)** generating the XTR pulse (3.3V→1.8V via 1.5kΩ series + 1.8kΩ-to-GND divider) and **timestamping the OPS243-A impact event** — officially documented by Raspberry Pi, lower latency than PiTrac's Pi-GPIO approach. This is the v1 architecture and it's **validated.** **[CONFIRMED]**
- **Illumination:** **940nm IR strobe** (overdriven LED array) + **940nm narrowband bandpass filter** on the lens. 940nm for the solar dip + invisibility (accept ~2× QE loss vs 850nm). Cheap glass filter ~$12; quality MidOpt-class ~$150+.
- **Impact detection:** reuse OpenFlight's existing **sound trigger / OPS243 HOST_INT** as the impact event into the Pico (don't add a redundant photogate).
- **Frame-rate note:** IMX296 is **60fps full-res** (ROI for more). With strobe-frozen frames, *one or a few* well-timed frames bracketing impact is the goal, not a high-fps stream.

### 3.4 IMX296 spec corrections [CONFIRMED]
1.58 MP (not 1.6), 1456×1088, 3.45µm, mono variant IMX296LLR. 60fps cap full-res. `imx296.trigger_mode=1`. The 14.26µs additive exposure offset dominates at short exposures.

---

## 4. Deriving the metrics + the club-loft/template question

Given pose `(R, t)` at the radar impact-time and the template:
- **Impact location (precise mm — see §4.4):** intersect the ball with the template's **curved face surface** (bulge & roll), express the contact point in face-local axes → **Impact Offset** (toe/heel, mm) and **Impact Height** (high/low, mm) from the **geometric face center** — matching Trackman's two-scalar format. Continuous mm, not a zone. **[CONFIRMED format]**
- **Face angle:** template face-normal rotated by `R`, projected to horizontal, vs target line.
- **Dynamic loft:** vertical (elevation) angle of the rotated face-normal at impact.
- **Club path / attack angle:** differentiate `t` across bracket frames for the head velocity vector; cross-check with the K-LD7s.

### 4.1 The loft/template verdict (maintainer's HIGH-PRIORITY question) [CONFIRMED + INFERRED]
- **The pros do not use a static-loft template.** They **measure delivered loft directly** (Trackman: 3D silhouette of the actual head, *"no assumptions of club-head design required"*; FlightScope: Fusion radar). Where loft can be entered, it is *"for reference purposes only… not used in calculations"* (FlightScope). Trackman's TPS club list (type/model/loft/shaft) is for **labeling/gapping**, not physics.
- **You cannot replicate that from behind** (can't see the face) → you *must* assume geometry. So **your "generic per-category loft + user override" plan is the correct workaround** — and it directly mirrors **FlightScope's Face-Impact-Location feature, which uses a generic, user-adjustable clubhead width/height calibration (not a per-model database).** **[CONFIRMED match]**
- **Design refinement:** dynamic loft does **not** need the club's *static* loft as a separate input — it falls out of the recovered head **orientation** + the template's **face-plane geometry**. The static loft is *baked into the template*. So the user "loft override" should **tilt the template's face plane**; that is the single biggest accuracy lever, because **template loft error feeds 1:1 into dynamic loft and face angle.**
- **Recommended UX (ties to the existing `ClubType` enum):** ship **generic per-category templates** (driver / fairway / hybrid / iron / wedge) keyed off the club the user already selects, with an **optional "exact loft / lie" override** per club for users who want best accuracy. Flag face/loft as lower-confidence when no override is set. Note within-category loft spread (e.g., 7-irons ~28–34°) **exceeds your ±2° target**, so the override is how power users reach the accuracy bar.

### 4.2 Calibration & coordinate frames (corrected) [CONFIRMED practice]
- **Don't "calibrate camera↔radar" via point correspondences** — the radar can't localize points (Doppler/broad-beam). Instead: **calibrate the camera intrinsics** (OpenCV `calibrateCamera`, checkerboard), then **calibrate the camera to a physical world frame** (target line + tee + a level/plumb reference) via a known target at the hitting area, and **mount the radars to a known pose** in that world frame by construction. You calibrate **camera→world**, not camera→radar.
- This mirrors how the commercial units hide a **factory-rigid camera/radar mount** and expose only **user world-registration** (Trackman's leveling + on-screen target/blue-square alignment; FlightScope's auto-leveling + clubhead-dimension entry). Copy that division: rigid mount + simple user alignment/leveling. **[CONFIRMED]**

---

### 4.3 Fusion seam: design face angle to accept a second (D-plane) estimate

**[INFERRED design guidance — plan for it, don't depend on it]**

The camera derives face angle *geometrically* (template face-normal rotated by the recovered head pose, §4). Keep that primary, but **structure the output so a second, independent estimate can be fused in later** — because the planned OPS-coherent receiver (§1A) plus camera spin make a **D-plane** estimate possible:
- Launch direction ≈ **85% face angle + 15% club path** → face angle ≈ (launch_dir − 0.15·path) / 0.85, from radar/camera launch direction + receiver club path.
- Spin axis is driven by **face-to-path** → face angle = club path + face-to-path, from camera+marked-ball spin axis + receiver path.

Two independent routes to the same number is how commercial systems get robustness. Practically: emit `face_angle_deg` **with a confidence and a source tag**, and leave room for a small fusion step that weights the geometric estimate against a D-plane estimate when the receiver + spin axis are available. Cheap to leave room for now; expensive to retrofit. Upside, not a dependency.

---

### 4.4 Impact location — precise mm (the Trackman spec) and the path there

**What Trackman actually outputs [CONFIRMED]:** two continuous scalars per shot, **not a zone**:
- **Impact Offset** — horizontal (toe/heel), in **mm**, from the **geometric center of the clubface**.
- **Impact Height** — vertical (high/low), in **mm**, from face center.

Measured **markerlessly via the optical OERT path** (not radar; needs adequate lighting). UI: a clubface graphic with one dot per shot, a **session dispersion heatmap** ("hot spots"), and numeric mm tiles; impact location feeds smash factor and gear-effect curve. Sign conventions are undisclosed — we define and document our own. FlightScope's "Face Impact Location" is the same idea (lateral + vertical impact, optical) and notably lets the **user enter clubhead width/height** to scale the face — a pattern worth copying.

**Honest precision reality [CONFIRMED physics + commercial precedent]:** even the gold-standard camera systems (GCQuad, Uneekor) reach ~1–3 mm only by imaging the face directly **with required fiducial stickers**. From behind the ball you never see the face, so precision is bounded by how well you recover **head pose** (+ ball position). Dominant error sources, ranked: **(1) depth / camera-axis translation** — a single behind-ball view infers depth from a scale prior, and depth error scales the whole face mapping; **(2) head rotation** — ~1° of face-angle error ≈ **0.7 mm** at a ~40 mm lever; **(3) face curvature** — modeling a driver face as flat is wrong by several mm near the edges (bulge & roll); **(4) ball-center localization** — usually sub-mm. **Net: a single markerless behind-ball camera realistically gives ~5–15 mm (coarse), not Trackman-class.**

**The realistic plan to reach precise (±3–5 mm) impact location:**
1. **Stereo** (two synced global-shutter cameras) — *measures* depth, removing the dominant error. Effectively required for mm precision; it's the same upgrade that buys spin axis + face/loft (§6B).
2. **Per-club 3-D face model with bulge & roll curvature** (woods curved, irons ~planar) — not a flat plane.
3. **User enters clubhead width/height / selects club** (FlightScope-style) to fix the face-center origin and scale.
4. **Strobed global-shutter capture** to freeze the head sharply at the impact frame (motion blur corrupts pose → corrupts the contact point).
5. **Full intrinsic + stereo-extrinsic calibration** and a fixed, known camera-to-hitting-zone geometry.
6. **~150–250 px across the clubface** + sub-pixel feature localization (~0.1 px) so 1 px ≲ ~0.5–1 mm.

Output `impact_offset_mm` + `impact_height_mm` from the geometric center (continuous, with a confidence); render as a per-shot clubface dot + session heatmap. **Stage 0 proves the error budget:** the sim quantifies how many mm of impact error result from a given depth/rotation error, converting directly into the stereo baseline / resolution / calibration spec — so you buy hardware to a number, not a guess.

---

## 5. Integration with OpenFlight (codebase-grounded) [CONFIRMED in repo]

- **Reuse the existing `src/openflight/camera/` module** — it already implements a pre/post-trigger **ring buffer** (`CaptureConfig`), a **`MockCameraCapture`** (your Stage-0 software-first harness), **`BallDetector` HoughCircles**, and `CameraCalibration`/`LaunchAngleCalculator`. It targets the **rolling-shutter HQ camera** and is **disabled in prod** (deps commented in `pyproject.toml`). The new GS club path is a **new capture backend alongside it**, reusing the buffer/mock/detector scaffolding.
- **Naming:** avoid collision with the existing `camera/`, `scripts/vision/` (YOLO training), and top-level `camera_tracker.py`. Suggest a `club_pose/` (or `camera/clubpose/`) module for segmentation→pose→metrics, designed to run **off-Pi**.
- **Hook point (verified):** subscribe to the existing shot callback — `RollingBufferMonitor.start(shot_callback=on_shot)` in `server.py`; `Shot` already carries `impact_timestamp` / `impact_timestamp_kld7`. On a shot, retain buffered frames tagged with the impact timestamp; analyze locally or ship to the offload box; merge results into `Shot` + the WebSocket payload (serialized ~`server.py:816`).
- **New `Shot` fields:** `impact_location_toe_heel_mm`, `impact_location_low_high_mm`, `face_angle_deg`, `dynamic_loft_deg` (+ confidences). Extend, don't fork.
- **GSPro/E6 caveat:** those are **fixed-schema protocols** (`gspro/`, `sim/`) — new metrics may have **no slot** to pass through. They'll surface in OpenFlight's own UI immediately; simulator passthrough needs per-protocol checking. **[CONFIRMED — correction to v1]**
- **Compute offload** (v1 still valid): Pi 5 captures/buffers/ships; ROCm box trains + runs the heavy pose ceiling; a Jetson Orin / phone is the portable inference target. PVNet/MegaPose figures verified.

---

## 6. Accuracy expectations (realistic bars) [INFERRED from benchmarks]

No vendor publishes per-metric club tolerances; the camera-based GCQuad is the de-facto truth reference, and even Trackman's **dynamic loft/spin are flagged unreliable indoors**. Honest single-camera DIY targets:

| Metric | Single-camera DIY target | Notes |
|---|---|---|
| Club head speed | **±2–3 mph** | Easiest (displacement/time across frames) |
| Attack angle | **±2–4°** | Achievable with good calibration |
| Club path | **±2–4°** | Easier than face from behind; corroborate with K-LD7 |
| **Face angle** | **±3–5°** (±2° likely needs stereo) | Hardest single-view metric |
| **Dynamic loft** | **±3–5°** | Sensitive to template loft + depth |
| Impact location | **±3–5 mm (stereo)** / ~5–15 mm (single-view) | Output is continuous mm — Impact Offset + Impact Height from face center (§4.4); stereo effectively required for Trackman-class precision |

The v1 ±2° face/loft figure is an aspirational *stretch* that **probably requires the 2nd camera**; lead with the ±3–5° single-view bar.

---

## 6A. Spin measurement — reuse the camera rig (don't chase radar spin)

**The radar-spin question is already settled in OpenFlight's own code. [CONFIRMED in repo]** `src/openflight/spin_estimate.py` documents that the OPS243 carries **no usable spin line**: dimples (~0.3 mm) are Rayleigh-smooth at 24 GHz (λ = 12.4 mm) and the specular point doesn't rotate with the ball, so an unmarked ball barely modulates the echo. Offline analysis of a 131-shot TrackMan session found **no spectral line** at the TrackMan spin frequency after dechirp, and the production envelope estimator had **~zero within-club correlation (r ≈ +0.19)**. OpenFlight therefore **replaced measured radar spin with a kinematic estimate**: `spin_rpm = 170 · ball_speed_mph · sin(LA)^1.2` (~10–11% median error on the calibration set, **~21% expected live**).

The radar DSP in `rolling_buffer/processor.py` is already near the single-CW ceiling (envelope demod + autocorrelation + phase confirmation + local-noise SNR + rail rejection). The only classic technique not present is the **cepstrum**, which helps only if a harmonic comb exists — the empirical result says it doesn't. **More radar DSP is not the path.**

Why the pros' radar spin works and yours can't: X-band (~10 GHz) + ≥3 receivers + tracking the **entire ~6 s flight at 40,000 samples/s** (thousands of revolutions). **Indoors, even Trackman/FlightScope/Garmin require a marked or RCT ball.**

**The fix — reuse the rig you're already building:**
- Measure spin **optically with a MARKED ball**, using the same mono GS camera + IR strobe planned for club pose. Fire the strobe **twice, ~0.5–2 ms apart** just after the impact trigger; locate the ball marker in both frames; **angular displacement ÷ known interval → rpm**, and the marker plane's motion → **spin axis** (which radar cannot give). At 3000 rpm the ball turns ~18°/ms — large and unambiguous across 2,000–10,000 rpm. **[CONFIRMED feasible]**
- This is the **PiTrac** recipe (open-source: HoughCircles → 3D rotate-and-match between two strobed frames; ~1.4% backspin error; spin axis is the harder part).
- A **ball marker keeps the *club* markerless** (your actual constraint) and is industry-normal (Rapsodo mandates printed dots; Mevo+ needs a sticker indoors; Foresight/Uneekor recommend dots for best spin).
- **Spin axis** is the hard part single-view (PiTrac sidespin ran ~2× off) — a marker plus **stereo** (§6B) is the route to reliable axis.
- **Cheap interim radar boost** (optional): a metallic dot / Titleist RCT ball strengthens the *radar* modulation (what Mevo+/Garmin RCT rely on) — improves rate, no axis. Stopgap only.

**Possible code bug to verify if radar spin is ever revisited:** `processor.py` assumes `seam = 1× spin` (`spin_rpm = peak_freq · 60`). FlightScope's patent states a great-circle **seam modulates at 2× spin** (a localized marker gives 1×). Academic today (the line is absent), but a factor-of-2 to check later. **[INFERRED from patent]**

---

## 6B. Hardware cost & the single-vs-stereo decision (real 2026 BOM)

Prices captured 2026-06-28 (USD), on top of OpenFlight's existing ~$540 radar build.

| Config | Camera subsystem | New project total | Δ vs single |
|---|---|---|---|
| **A — Single GS camera** | ~$92–219 (typical **$130–160**) | ~$670–700 | — |
| **B1 — Stereo, DIY CSI genlock** | ~$138–333 | ~$680–870 | **+$45 to +$115** |
| **B2 — Stereo, Arducam Camarray HAT** | ~$252–420 | ~$790–960 | +$160–200 |
| Spin add-on (marked balls/stickers) | +$5–20 | — | — |

Verified anchor prices: InnoMaker IMX296 **mono** w/ trigger **$47**; Arducam OV9281 mono **$26–36**; official RPi GS (color) **$50**; Pi Pico 2 **$6.25**; cheap 940 nm bandpass **$8–15** (premium MidOpt **$231** — skip); 940 nm IR array **$30–80**; MOSFET strobe driver **$5–10**; CSI adapter cable **$2.70**.

**The stereo gotcha is sync, not money.** The Pi 5 has **two native CSI ports** and runs two cameras, but **free-running streams are NOT exposure-aligned** — for a 67 m/s ball a 1 ms skew = ~67 mm of travel, ruining triangulation. You must **genlock**: drive **both cameras' XTR trigger from one Pico pulse through a 1.8 V level shifter** (~$5–10 + soldering; XTR/XVS/XHS are 1.8 V — 3.3 V destroys the sensor), **or** buy the **Arducam Camarray Stereo HAT** (turnkey hardware sync, but shares one CSI lane → ~halved per-eye resolution, fixed short baseline). **[CONFIRMED]**

**Recommendation:** build **Config A** first (it doubles as the spin sensor with a marked ball), but **plan for stereo (B1)** — at **+$45–115** it's the highest-leverage dollar in the project, simultaneously buying real **spin axis**, **face angle**, **dynamic loft**, and **mm-class impact location**. Stereo is your **optical substitute for the multi-receiver radar capability** Trackman has — the same "add receivers to triangulate" move the maintainer is making on the RF side (§1A).

---

## 7. Revised staged roadmap

- **Stage 0 — Simulation only (no hardware).** BlenderProc renders (driver + iron templates, behind-ball virtual camera, domain randomization incl. **motion blur and IR-like single-band imaging**) with ground-truth pose. Implement metric derivation (§4) + **both** silhouette analysis-by-synthesis *and* keypoints+PnP; validate recovered-vs-truth pose. **Gate:** ≤2–3° rotation / ≤5mm translation, face-angle ≤1° *in sim*. Also quantify how **template loft error** propagates to face/loft (informs the override design). *Start here now — no spend.*
- **Stage 1 — Capture + illumination (hardware, the new learning).** Mono IMX296 + Pico XTR trigger + **940nm IR strobe + bandpass filter**; wire OPS243-A impact → Pico; confirm strobe-frozen, timestamped frames bracketing impact. **Gate:** sharp (≤1–2px effective blur) frames on a high fraction of swings **indoors**, then characterize **outdoor** pickup in shade vs direct sun.
- **Stage 2 — Segmentation on real frames** (your strength). Confirm sim-trained models transfer. **Gate:** mask IoU ≥0.9 vs hand-labels across lighting/clubs.
- **Stage 3 — 6-DOF pose on real shots.** Silhouette fit and/or keypoints+PnP; interpolate pose to radar impact-time; check multi-frame kinematic consistency.
- **Stage 4 — Impact location** (priority #1). Continuous **Impact Offset + Impact Height (mm)** from face center (§4.4); per-shot clubface dot + session heatmap UI. Validate mm error vs a reference; expect ~5–15 mm single-view, ±3–5 mm with stereo.
- **Stage 5 — Face angle / dynamic loft.** **Gate:** ±3–5° single-camera; **decide here whether to add the 2nd camera** for ±2°.
- **Stage 6 — Integration.** Merge fields into `Shot`/UI; check GSPro/E6 passthrough feasibility.

---

## 8. Patent landscape (updated) [CONFIRMED]

- **Trackman seeds (NOT the method):** US 10,471,328, US 10,473,778 — radar ball-trajectory + 2D video overlay; no clubhead-pose method.
- **Trackman clubhead/impact family (the real blueprint):** **US 10,953,303** → 11,439,886 → 11,612,801 → 12,263,393 (Tuxen). Single behind-ball imager, markerless fix-points + stored geometric model, radar-synced impact time, face-normal/impact-location derivation.
- **Rapsodo (closest to a DIY clone):** US 11,077,351, US 11,583,746 — per-club CAD scan + **stereo** silhouette registration (<1° RMS); single-cam variant needs a shaft marker. Fusion: US 10,754,025.
- **FlightScope/EDH "Fusion Tracking":** US 10,338,209 — radar+camera Kalman fusion for 2D position accuracy; **no camera-based clubhead pose** (radar carries club geometry).
- **Risk posture (general info, not legal advice):** replicating behind-ball markerless impact location is the most patent-sensitive area; **non-commercial open-source research risk is low**; commercialization needs counsel. Independent-research/prior-art framing helps.

---

## 9. Open risks & honest caveats

1. **You are past the state of the shipping art.** No product does single-camera markerless behind-ball face/loft. Trackman's *patent* is the only close blueprint, and even Trackman backs it with dual radar + (indoors) a second camera. Budget for heavy validation and a probable **2nd camera** for face/loft.
2. **Outdoor direct-sun capture on a cheap strobe is the hard physical limit** — expect degraded outdoor performance in bright sun; design for shade/overcast and indoor as the strong cases.
3. **Template fidelity caps face/loft accuracy** — the user loft/lie override is the main lever; within-category loft spread exceeds the ±2° goal.
4. **Single-view depth/pose ambiguity** is mitigated (shape prior + radar velocity + multi-frame) but not eliminated; stereo is the clean fix.
5. **MegaPose, not FoundationPose, for monocular RGB** render-and-compare.
6. **GSPro/E6 may not carry the new metrics** — verify protocol schemas before promising passthrough.
