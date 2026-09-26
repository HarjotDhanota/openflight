# OpenFlight full-system accuracy audit — radar + camera CV and D-plane

**Date:** 2026-09-01

**Audit branch:** `audit/2026-09-01` at `20d0ce4` (`feat/rig-geometry`)

**Session:** `openflight_session_20260825_181734_filtered`

**Reference labels:** `contact_marks.jsonl`, pass 1, annotator `harjot`

There is no ground truth in this session. All accuracy language below is therefore either an internal-consistency result, a controlled sensitivity, or a physics/calibration bound. It is not an assertion that LCMF, either camera reconstruction, or the hand marks are truth.

## 1. Part 1 — why the vertical-launch estimators disagree

### Answer

The three reported means do not disagree for one reason:

1. **The ~24° independent camera+IWR result versus the promoted ~28° `ball_flight` result is primarily a camera-intrinsic error.** The promoted path estimates focal length and camera pitch anew from the segmented stationary ball. In this bright session the detected reference-ball diameters are only 10.16–12.62 px, producing focal estimates of 363.9–452.0 px. Holding the same detector, range evidence, frames, and fitter fixed while replacing those per-shot values with the earlier reconstruction's nominal `f=466.67 px`, pitch `−0.185°` changes the accepted estimates by **−3.15° mean / −2.79° median** (range −7.92…−0.94°). The 7-iron mean moves 28.60° → **24.32°** and the 9-iron mean 28.33° → **26.10°**, close to the independent 24.08° / 26.98° results. This is an A/B attribution, not ground truth. The mechanism is `_camera_model()` deriving both focal scale and pitch from the same bloom/threshold-sensitive ball (`feat/club-metrics-from-track:src/openflight/camera/ball_flight.py:154-179`). The durable closer is a calibrated, fixed `K=(fx,fy,cx,cy)` plus distortion at the production focus and crop; the ball should validate scale/pose, not redefine the lens every shot.

2. **Both camera+IWR paths also have two smaller, opposing range/time defects.** LCMF corrects the IWR range walk through `Calibration.true_range()` (`feat/club-metrics-from-track:src/openflight/iwr6843/lcmf.py:265-276`), but `BallRangeEvidence` drops the calibration and `ball_flight` consumes `track.range_at(...)` raw (`lcmf.py:177-203`; `camera/ball_flight.py:385-401`). Applying the session's `range_bias_m=0.0660069821` reduces camera launch **0.55° mean / 0.58° median**. Conversely, camera times are relative to an acoustic trigger, while the range walk is anchored at physical impact; adding the 1.524 m / 343 m/s sound-flight delay to the range query increases launch **1.63° mean / 1.67° median**. The two defects partly mask one another and must be corrected together under a single timing contract.

3. **The ~18.9° LCMF versus ~24.1° independent camera+IWR gap remains an unresolved inter-sensor elevation datum, narrowed to calibration rather than basic formula direction.** LCMF constructs the predicted direct/image angles as `atan2(world geometry) − tilt_rad` (`feat/club-metrics-from-track:src/openflight/iwr6843/lcmf.py:307-320`), so its search variable is a world launch angle and boresight tilt is included. The server uses `configured_tilt + inclinometer_pitch`; when the inclinometer is unavailable it retains the configured value. This session disabled the inclinometer and used **10.5° configured tilt**, so no measured physical IWR pitch exists. A controlled replay gives:

   | perturbation | change in LCMF vertical mean |
   |---|---:|
   | configured tilt −2° | −4.125° |
   | configured tilt +2° | +4.049° |
   | radar height ±20 mm | about ±0.77° |
   | ball height ±10 mm | about ∓0.41° |
   | tee slant range ±30 mm | about ±0.47° |
   | channel phase ±0.1 rad | −1.53° / +1.58° |

   Thus only ~2.6° of physical tilt/extrinsic error could explain the observed camera−LCMF `+5.34 ± 0.83°` offset. That is plausible enough to demand measurement but is not evidence that the configured value is wrong. The calibration loader defaults absent tilt to zero (`feat/club-metrics-from-track:src/openflight/iwr6843/calibration.py:25-81`), and runtime copies the effective per-shot tilt into LCMF (`src/openflight/iwr6843/runtime.py:254-267`). The software sign contract is covered; the physical board orientation and array phase datum are not. A surveyed launcher/trajectory plus TrackMan and a measured rig inclinometer/boresight reference must decide this gap.

4. **Gravity and the current timestamp source do not explain the large offset.** Accepted camera paths cover approximately 4.4–34.4 ms after physical contact, with a median midpoint of 19.4 ms. At 115 mph and 24°, gravity rotates the velocity direction by about −0.044° at 4.44 ms, −0.194° at 19.4 ms, −0.340° at 34 ms, and −0.602° at 60 ms. The current constant-velocity chord therefore slightly *understates* impact launch; it cannot create a +4–5° high bias. Replaying sensor timestamps instead of host timestamps changes accepted shots by only **+0.032° mean / +0.063° median** (range −0.522…+0.333°) on this session. Sensor time is still the correct production source.

5. **The path uses an exact off-axis ray but is not statistically independent of LCMF.** `_project()` normalizes the full `(x,y,f)` ray, rather than treating `(row−cy)/fy` as the world elevation (`camera/ball_flight.py:182-226`). Candidate scoring nevertheless includes an LCMF vertical prior (`camera/ball_flight.py:460-466`). This prior can select among optical paths but cannot validate LCMF.

6. **The two club means should not be compared across implementations without noting acceptance.** Current replay accepted 18/21 shots: 7-iron mean/median **28.60°/28.00°** (`n=8`) and 9-iron **28.33°/30.35°** (`n=10`). The older independent reconstruction reported 24.08° / 26.98° on 20 shots; LCMF reported 18.94° / 21.53°. The promoted camera path's roughly 28°/30° result and the resulting loft-band miss are already explicitly pinned (`feat/club-metrics-from-track:tests/test_track_metrics_regression.py:39-48,138-154`), not a new audit finding.

### Pairwise disposition

| pair | evidenced mechanism | what remains |
|---|---|---|
| promoted camera ~28° vs independent camera+IWR ~24° | Per-shot ball-derived focal/pitch is dominant; fixed-intrinsic A/B removes 3.15° mean. Old-vs-current rig constants explain another −0.81° mean; range bias and acoustic alignment are smaller and opposing. | Calibrate camera intrinsics/distortion at exact focus/crop, correct bias and acoustic alignment together, then freeze and replay. |
| independent camera+IWR ~24° vs LCMF ~18.9° | Nearly constant `+5.34 ± 0.83°` datum gap. LCMF tilt direction is algebraically consistent, but physical IWR pitch/phase and radar↔camera extrinsics were not measured. | Multi-position rigid radar-visible/camera-visible target, measured inclinometer/boresight datum, and TrackMan or surveyed-launcher shots. |
| promoted camera ~28° vs LCMF ~18.9° | Sum of the promoted camera intrinsic problem and the unresolved inter-sensor datum; gravity/host time cannot supply the missing magnitude. | Resolve in the order above. Do not tune either estimator to the other. |

### D-plane result

The Wood coefficient direction and RH horizontal signs are correct:

```text
V = A + k_v(DL − A)   =>   DL = A + (V − A)/k_v
H = P + k_h(F − P)    =>   F  = P + (H − P)/k_h
```

`dynamic_loft()` and `face_angle()` implement those inversions in degrees (`feat/club-metrics-from-track:src/openflight/club_metrics.py:111-134,163-189`). There is no radians/degrees defect in these functions.

Two D-plane defects remain:

- `spin_loft()` returns only `dynamic_loft − attack_angle` (`club_metrics.py:137-160`). That is a vertical-plane approximation, not the 3-D angle between face normal and clubhead velocity. Counterexample: `DL=30°`, `face=10°`, `AoA=−5°`, `path=−10°` gives 35.0° in the code but **39.904°** from `acos(cos(DL)cos(A)cos(F−P)+sin(DL)sin(A))`, an underestimate of **4.904°**.
- Withholding is not fail-closed. The D-plane result correctly keeps offered face angle `None`, but the server writes `computed_face_angle_deg` to `shot.experimental_face_angle_deg` and serializes it (`feat/club-metrics-from-track:src/openflight/server.py:3330-3351,3473-3498`). Existing tests explicitly require the leaked number (`tests/test_server_track_metrics.py:257-266,303-335`). Dynamic and spin loft are also marked `experimental` and serialized whenever camera vertical is accepted, despite Part 1 remaining unresolved (`club_metrics.py:338-354`). The audit stopped at the server payload boundary; sim/UI consumers are out of scope.

## 2. TrackMan parity table

Rows are ordered by expected parity gain per unit effort. TrackMan 4's relevant architecture is radar-timed OERT: a calibrated camera, stored club model/fix points, and radar kinematics/impact timing are fused; the camera does not independently infer all club metrics from one frame. The research notes this at `../openflight-research/docs/Personal Research/camera-feasibility-verdict-2026-08.md:264-272` and `research-2026-08-28/B_comparators_patents.md:14-38`. TrackMan publishes the metrics but no numeric TM4 OERT absolute-accuracy tolerance (`B_comparators_patents.md:29-38`). Impact location is two continuous millimetre coordinates from geometric face center (`markerless-club-data-guide-v2-research-corrected.md:690-737`).

| rank | metric | TrackMan method | OpenFlight state and gap | named closer / verdict |
|---:|---|---|---|---|
| 1 | **Vertical launch** | Dual-radar ball tracking; camera assists OERT indoors. | LCMF and camera share no measured elevation datum; promoted camera also self-calibrates a biased focal/pitch. Absolute gap is unresolved; 7i physics bound makes the native 28.6° mean suspect, not disproven. | **Code + calibration/session:** fixed calibrated `K`/distortion; preserve range calibration; acoustic-aligned range query; sensor timestamps; multi-position radar↔camera calibration; TrackMan/surveyed-launcher session. Highest leverage because dynamic/spin loft depend on it. |
| 2 | **Club path** | Radar kinematics plus OERT club-model/fix-point tracking at impact. | Camera+IWR head delivery is experimental; same-point scattering-center association is unproven, host timestamps are used, and no external reference exists. Mirroring tests pass and no double mirror was found. | **Code + calibration/session:** sensor-time fusion, pre-impact contact-point definition, rigid common-frame calibration, then TrackMan side-by-side. Single-camera hardware is not yet proven to be the limiting factor for path. |
| 3 | **Attack angle** | Same radar/OERT contact-point velocity, evaluated at impact. | Experimental. Current newest drift branch still divides vertical velocity by forward speed only; an existing unmerged commit shows ≤0.347° session error. Same-point/timing limitations dominate. | **Code first:** merge/test `45bf74e` full-horizontal projection; then the same calibration/session as path. |
| 4 | **Impact offset (heel/toe)** | OERT locates markerless contact; continuous mm from geometric face center. | Relative 2-D zone variation is demonstrated. Absolute center is intentionally unsupported pending address/foot-spray calibration. 3-D projection is mathematically sound given inputs, but uses assumed face pose and face-plane landmarks. | **Calibration/session:** leave-one-shot-out foot spray/tape over deliberate heel/center/toe strikes, fixed first-contact definition. Single view should be treated as roughly 5–15 mm until reference proves better (`markerless…guide:761-763,833`). |
| 5 | **Dynamic loft** | OERT fitted club orientation at the ball-contact point; radar/D-plane assists. | Algebra is correct, but input vertical is unresolved and the Wood coefficient has a model floor. Numeric experimental values currently cross the shot-payload boundary. | **Code + calibration; hardware if ±2° is required:** fail-closed status now, reopen after vertical/path reference. Calibrated interior features/stored club model; stereo is the expected route for robust ±2° face/loft (`markerless…guide:655,730-734`). |
| 6 | **Face angle** | OERT fitted club orientation at the contact point, with impact-location context. | Horizontal D-plane inversion is correct, but face is explicitly withheld because launch direction/path datum is not validated. A numeric computed value nevertheless leaks into the shot payload. | **Code immediately:** clear withheld value at the shot boundary. **Then calibration/hardware:** common-frame horizontal reference and TrackMan session; likely stereo/interior features for ±2°. |
| 7 | **Impact height (high/low)** | OERT continuous mm from geometric face center. | `experimental_unvalidated`; known wrong topline on ~half the shots. A 0–20 mm landmark-depth sweep produces up to 15.5 mm vertical error under the face-plane assumption. | **Calibration + likely hardware:** support-plane datum and foot spray first; calibrated club surface/interior landmarks, then stereo if the ±3–5 mm parity target remains. This is structurally weaker than heel/toe in the current rear view. |
| — | **Ball speed** | — | `out_of_scope_this_pass`. Separate contract audit says shipped OPS ball speed is deprojected total speed (`chore/speed-contract-check:docs/superpowers/specs/2026-08-28-speed-contract-audit.md:36-70`). | Later OPS rolling-buffer audit + TrackMan speed reference. |
| — | **Club speed** | — | `out_of_scope_this_pass`. Separate audit says shipped OPS club speed remains raw radial; IWR/OPS median ratio is 0.910 and neither has TrackMan ground truth (`speed-contract-audit.md:72-123,229-279`). | Later speed-contract audit/session. |
| — | **Horizontal launch** | — | `out_of_scope_this_pass`. It is an input dependency for face angle. | Later horizontal-LCMF audit; common-frame target-line calibration. |
| — | **Spin** | — | `out_of_scope_this_pass`. Current model is not a TrackMan-like measured spin channel. | Later spin audit; marked optical or sufficiently capable radar hardware, with reference. |
| — | **Carry** | — | `out_of_scope_this_pass`. | Later ballistics/carry audit after launch and spin are validated. |

## 3. Findings, ranked by severity

### High — camera launch self-calibrates its lens from a threshold-sensitive 10–13 px ball

**Claim:** Per-shot reference-ball under-segmentation changes both focal length and pitch and is the dominant named mechanism behind the promoted camera estimator's ~28° result.

- **File:** `feat/club-metrics-from-track:src/openflight/camera/ball_flight.py:154-179`; reference-ball acceptance is only a broad diameter bound at `:520-526`.
- **Evidence:** Session anchors yield `f=363.9…452.0 px`; controlled `f=466.67 px`, pitch `−0.185°` A/B changes all else identically and shifts −3.15° mean / −2.79° median. Per-club mean changes are −4.28° (7i) and −2.23° (9i).
- **Repro:** `uv run python scripts/analysis/replay_track_metrics.py --session <session>` gives 7i/9i means 28.60°/28.33°. Monkeypatch `_camera_model` to return `(466.6667, radians(-0.185), native_radar_from_camera)` and rerun; means become 24.32°/26.10°.
- **Suggested test:** A synthetic calibrated-camera test with a known 3-D trajectory and deliberately eroded/dilated stationary-ball masks must leave launch invariant; production should consume fixed `K`/distortion and report reprojection residuals.

### High — calibrated radar bias is discarded at the LCMF-to-camera boundary

**Claim:** The LCMF samples corrected true range, but the evidence object handed to camera ball flight and club delivery exposes the raw biased track.

- **File:** `feat/club-metrics-from-track:src/openflight/iwr6843/lcmf.py:177-203,265-276`; `src/openflight/camera/ball_flight.py:395-401`; chained club delivery similarly calls raw `track.range_at` at `feat/clubpose-drift-fixes:src/openflight/camera/club_delivery.py:974-979`.
- **Evidence:** Session calibration bias is 66.0069821 mm. A proxy track subtracting it changes camera vertical by −0.55° mean / −0.58° median; 7i mean 28.60° → 28.11°, 9i 28.33° → 27.74°. The independent reconstruction explicitly calls `cal.true_range(...)` (`../openflight-research/research/silhouette_poc/falsification/test1_vertical_trajectory.py:87-100`).
- **Suggested test:** Construct `BallRangeEvidence` with a nonzero calibration bias and assert every consumer receives the same corrected physical range as LCMF. Include ball-flight and club-delivery integration cases.

### High — a withheld face-angle number and unresolved loft values reach the shot payload

**Claim:** Status and value disagree: `withheld_launch_direction_offset` accompanies a numeric face angle, while unresolved dynamic/spin loft can be marked experimental and serialized.

- **File:** `feat/club-metrics-from-track:src/openflight/club_metrics.py:228-239,338-354`; `src/openflight/server.py:3330-3351,3473-3498`.
- **Evidence:** `tests/test_server_track_metrics.py:257-266,303-335` currently asserts the partial face value is stored and serialized. This violates the brief's fail-closed requirement and the known decision to withhold face/dynamic loft pending Part 1.
- **Measured impact:** Not an angle bias; it is an exposure/contract failure. Any consumer that ignores status can present an unvalidated number. The audit stopped at serialization because UI and sim connectors are out of scope.
- **Suggested test:** Replace the existing expectation with `experimental_face_angle_deg is None` whenever status begins `withheld_`; apply the same invariant generically to every payload metric. Until Part 1 closes, assert dynamic/spin loft are also `None` with a withholding status.

### High — acoustic-trigger time is applied to camera pixels but not to radar range

**Claim:** A camera sample after the microphone trigger is queried at the same offset after radar impact, omitting sound travel from ball to microphone.

- **File:** `feat/club-metrics-from-track:src/openflight/camera/ball_flight.py:385-401`; physical contact-frame correction already exists elsewhere in server wiring, but this path does not use it.
- **Evidence:** `1.524/343 = 4.443 ms`. Adding that delay to `range_evidence.impact_t_s` increases vertical launch +1.63° mean / +1.67° median (range +1.17…+1.96°). Accepted optical paths cover only ~4.4–34.4 ms after contact, so 4.44 ms is not negligible relative to the fit window.
- **Suggested test:** A synthetic constant-velocity range walk with a microphone at known distance must project camera observations at `impact_t + sound_delay + (frame_t−trigger_t)`. Perturb mic distance and assert the analytically expected range/angle shift.

### High — LCMF's physical elevation datum was configured, not measured

**Claim:** The LCMF formula includes tilt with a consistent sign, but no observation verifies that the session's configured 10.5° equals the IWR array's physical boresight datum.

- **File:** `feat/club-metrics-from-track:src/openflight/iwr6843/lcmf.py:307-320`; calibration default/load `src/openflight/iwr6843/calibration.py:25-81`; server effective-tilt wiring `src/openflight/server.py:1558-1603,2859-2910`.
- **Evidence:** Inclinometer was disabled in session metadata. Tilt ±2° moves LCMF about ±4.1°, while height/range perturbations of plausible tape size move <1°. The independent camera−LCMF difference is a nearly constant `+5.34 ± 0.83°` (`../openflight_claude_artifact_dplane_audit.md:68-81`).
- **Suggested test:** Place a radar-visible reflector/launcher at multiple surveyed elevations and ranges, solve pitch and phase/extrinsics leave-one-position-out, then freeze calibration before a TrackMan side-by-side. A unit test can preserve sign; only this physical test can establish the datum.

### High — treating silhouette landmarks as coplanar with the face can move high/low by clubhead-scale amounts

**Claim:** The 3-D projection algebra is correct given its inputs, but projecting crown/heel/toe silhouette rays onto the contact face assumes away up to ~20 mm of landmark depth.

- **File:** `feat/rig-geometry:src/openflight/camera/clubpose/impact_projection.py:123-131,136-176`.
- **Evidence:** Exhaustive synthetic offsets of the three landmarks from 0…20 mm behind the face give maximum error **1.63 mm heel/toe** and **15.50 mm high/low**; placing all three 20 mm behind moves high/low by −14.60 mm. This disproves the comment's unqualified “within a few millimetres” bound for vertical impact.
- **Suggested test:** Parameterize landmark surface depths over the real club mesh and assert either a conservative uncertainty/withholding threshold or use ray intersections with the calibrated club surface. Validate separately against foot spray at first contact.

### Medium — spin loft is a 2-D subtraction, not the 3-D face-to-velocity angle

**Claim:** Horizontal face-to-path separation is omitted from spin loft.

- **File:** `feat/club-metrics-from-track:src/openflight/club_metrics.py:137-160`.
- **Evidence:** `(DL,F,A,P)=(30°,10°,−5°,−10°)` returns 35.0°; exact 3-D result is 39.904°, a 4.904° miss.
- **Suggested test:** Add nonzero face-to-path vector cases plus the zero-horizontal-difference reduction. Compute from normalized face-normal and velocity vectors to make conventions explicit.

### Medium — `solve_setup` labels optical-axis depth as slant range and is extremely diameter-sensitive

**Claim:** `fD/d` is optical-axis depth for the pinhole approximation, but `solve_setup` uses it as Euclidean range along a normalized off-axis ray.

- **File:** `feat/rig-geometry:src/openflight/rig_geometry.py:185-240`.
- **Evidence:** At representative `(x,y,d)=(176.5,146.0,12.6 px)`, `f=466.67 px`, `D=42.67 mm`, the formula gives **1580.37 mm** depth; the corresponding slant is **1589.01 mm**, +8.64 mm. Therefore the reported ~5 mm tape agreement is partly a comparison of different range definitions. Diameter `12.6±0.5 px` changes the result by **+65.31/−60.32 mm**; 12.60→12.77 px alone is −21.04 mm.
- **Suggested test:** Generate an off-axis sphere with known center, project its tangent conic, and require recovery of Euclidean center range and XYZ. Include ±0.5 px segmentation perturbations and return propagated uncertainty rather than a point estimate.

### Medium — the principal point and distortion are assumed, not calibrated for the shipped crop

**Claim:** `measured_camera()` records crop/ISP metadata but still fixes the principal point to image center and focal length to nominal lens geometry.

- **File:** `feat/rig-geometry:src/openflight/rig_geometry.py:75-95,125-150`; solve use at `:185-240`.
- **Evidence:** The driver requests a centered output crop, so `(w/2,h/2)` is a plausible starting convention, but `sensor_crop`, increment, and ISP offset do not prove that the optical principal point is centered after lens mounting and ISP scaling. The current 5 mm tape check shares the same ball-size model and is consistency, not calibration.
- **Suggested test:** ChArUco/checkerboard calibration across the complete production ROI at fixed focus; persist `fx,fy,cx,cy,k1…` and reject configurations whose crop/readout identity differs.

### Medium — production motion fits use callback time despite hardware exposure time being archived

**Claim:** Camera ball and club fusion receive host timestamps even though sensor exposure timestamps are available.

- **File:** `feat/clubpose-drift-fixes:src/openflight/camera/capture_runtime.py:689-695,752-775`; server passes the archive time arrays in its fusion stage; the earlier audit locates the club call at `../openflight_claude_artifact_dplane_audit.md:124-131,155-157`.
- **Evidence:** On this session sensor-time replay changes camera vertical only +0.032° mean / +0.063° median, but the host series contains an interval outlier and callback latency is not acquisition time. Impact-timed club metrics are more sensitive and were not reference-scored here.
- **Suggested test:** Inject host jitter while keeping sensor timestamps uniform; ball/path/AoA results must remain invariant. Fail closed on missing/nonmonotonic sensor time rather than silently selecting host time.

### Medium — most accepted impact carries extrapolate without reporting their distance

**Claim:** The quadratic carry allows contact outside the accepted-frame interval and omits its only disagreement diagnostic in that case.

- **File:** `feat/clubpose-impact-zone:src/openflight/camera/clubpose/impact_zone.py:360-400,520-568`.
- **Evidence:** In the 21-shot run, 20 readings are accepted and **11/20 do not bracket contact**. Their last accepted frame is as far as 0.853 frame / 1.82 ms before contact; `carry_disagreement_mm` is `None` because no post-contact accepted frame exists. The registered 0.14 px agreement applies only where both models exist.
- **Suggested test:** Assert a named maximum extrapolation in frames/ms, serialize extrapolation distance, and withhold or lower confidence beyond it. Score the threshold leave-one-shot-out; do not retune the registered gates.

### Medium — self-built toe calibration does not use the read-time acceptance population

**Claim:** Toe overhang is measured on every slow mask with any `align_frame` solution, while readings use ridge refinement plus IoU/boundary/track/USGA gates.

- **File:** `feat/clubpose-impact-zone:src/openflight/camera/clubpose/head_outline.py:766-783,828-837,841-865`; read-time gates `:1309-1337` and `impact_zone.py:520-540`.
- **Evidence:** Ridge refinement is vertical, so it should not directly change toe x; the remaining concern is population selection—rejected placements can influence the median calibration. The pinned label-template regression does not validate the self-built toe calibration and absolute toe remains a known open calibration.
- **Suggested test:** Leave one shot out of self-build and toe calibration, apply the identical read-time gates to calibration placements, and compare against per-shot foot spray. Report accepted calibration frame count and sensitivity to each gate.

### Low — a corrected 3-D attack-angle projection exists but is absent from the audited newest branch

**Claim:** `_velocity_angles()` in `feat/clubpose-drift-fixes` uses `atan2(vertical, forward)` instead of total horizontal speed.

- **File:** `feat/clubpose-drift-fixes:src/openflight/camera/club_delivery.py:283-288`.
- **Evidence:** Commit `45bf74e` changes the denominator to `hypot(lateral,forward)` and includes the failing regression first. Its measured session bias is +0.052° mean / 0.347° max, reaching 0.60° at 30° path.
- **Suggested test:** Merge that commit or reproduce its `TestVelocityAngleProjection`; keep path as `atan2(lateral,forward)` and AoA as the elevation of the full vector.

## Confirmed-correct or bounded checks

- **Boresight wiring:** LCMF tilt is present; no “forgot to add 10.5°” code defect was found. Physical tilt remains unverified.
- **Off-axis camera ray:** normalized 3-D ray math is present and sign tests cover mirrored capture.
- **Gravity:** <0.35° over the accepted window; negligible relative to the current disagreement, but should be named or modeled once sub-degree work begins.
- **3-D contact/plane algebra:** `contact = ball_center − radius·normal`, ray-plane intersection, heel→toe axis, and up-face sign are internally correct. At 30° loft, synthetic camera pitch ±1° changes high/low by about −0.54/+0.55 mm and heel/toe by ~0.001 mm; roll ±1° changes heel/toe by about +0.215/−0.220 mm and high/low by <0.01 mm. Thus the known −0.22° pitch is <0.13 mm, while unresolved 3.2° roll is roughly 0.7 mm heel/toe for this geometry.
- **Mic geometry:** the Euclidean microphone-to-ball expression and solved-distance wiring are algebraically consistent. The fixed 343 m/s temperature issue is known and not re-reported.
- **Mirroring/units:** stored mirrored frames are restored once through `horizontal_pixel_sign=-1`; targeted sign tests pass. Fusion conversions between range metres, velocity m/s, and output mm are explicit; no second mph/mm-frame defect was found in scope.
- **Known topline failure:** not re-reported as a discovery. Quantification for context: shots `003,005,006,008,009,011,016,017,018,023,024,025` produce physically impossible negative 2-D high/low under the known anchor failure. The specular ridge improves placement consistency but cannot establish the correct anatomical/support-plane datum.

## Verification performed

All commands used `uv`; no registered thresholds were changed.

```text
uv run python scripts/analysis/replay_track_metrics.py --session <session>
  18/21 accepted; 7i mean 28.603°, 9i mean 28.333°

uv run pytest -q tests/test_camera_ball_flight.py tests/test_club_metrics.py \
  tests/test_server_track_metrics.py tests/test_track_metrics_regression.py
  83 passed

uv run pytest -q tests/test_rig_geometry.py tests/test_clubpose_impact_projection.py \
  tests/test_camera_impact_zone_wiring.py
  46 passed

uv run pytest -q tests/test_clubpose_fusion.py tests/test_camera_club_delivery.py \
  tests/test_inclinometer_server.py
  88 passed

uv run pytest -q tests/test_clubpose_impact_zone_regression.py
  20 passed, 1 xfailed (the registered strict-availability miss; not retuned)
```

## 4. What I could not check and why

- **Absolute accuracy:** no TrackMan, calibrated launcher, motion-capture system, impact tape/foot spray, or other ground truth is present. The audit can isolate internal mechanisms but cannot crown an estimator.
- **Physical IWR boresight and channel phase:** the session has no inclinometer observation and no surveyed radar target. Code-level sign tests cannot validate board mounting or array phase datum.
- **Full radar↔camera SE(3):** tape geometry is single-configuration and the sensors do not observe a mechanically registered common target at multiple positions. In-situ multi-shot registration is the missing comparator-style calibration (`../openflight-research/docs/Personal Research/markerless-club-data-guide-v2-research-corrected.md:868-877` in the reviewed revision).
- **Optical intrinsics/distortion:** no calibration-board images at the production focus/crop exist. Nominal focal length, centered principal point, and stationary-ball scale cannot independently validate one another.
- **First-contact versus final tape patch:** the session lacks per-shot physical impact marks. Prior research bounds their possible vertical difference at roughly 1.94–3.66 mm; the desired measurand must be fixed before scoring high/low.
- **Self-built-template absolute toe accuracy:** pass-1 labels test placement consistency, while the self-built calibration needs leave-one-shot-out foot-spray scoring. The known absolute-center limitation remains.
- **Consumer leakage after server serialization:** sim connectors and UI are explicitly out of scope. The payload already violates fail-closed value/status semantics, so downstream inspection is not needed to establish the boundary defect.
- **Out-of-scope chains:** OPS speed extraction, horizontal-launch LCMF, spin model, ballistics/carry, upstream trigger/clock generation, sim connectors, club-data tables, UI, cloud, power, and deprecated K-LD7 were not audited. Dependencies are named above and the review stopped at their boundaries.
- **Product fixes:** no estimator was retuned and no ambiguous calibration-dependent source change was made. The status schema fix affects out-of-scope consumers; range-bias and acoustic fixes must land together under a tested timing/range contract; geometry changes would move pre-registered results and therefore require new tests without changing the registered reference numbers.
