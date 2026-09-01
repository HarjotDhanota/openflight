# OpenFlight D-plane, face angle, and impact-location audit

**Date:** 26 August 2026  
**Artifact reviewed:** *Measuring where the ball struck the face, with one camera and two radars*  
**Artifact URL:** <https://claude.ai/code/artifact/42a6f3f4-0b9b-4faf-bf9c-1ff45b4e94dd>  
**Evidence reviewed:** the exact cached artifact, the current OpenFlight source, the 21-shot `20260825_181734_filtered` falsification results, the earlier OpenFlight research reports, hardware documentation, and primary research literature.

## Bottom line

OpenFlight is making real progress on ball detection and on exposing its own failures. It is not yet measuring face angle, dynamic loft, or impact location. At present it can produce **experimental D-plane inferences**, but several inputs to that inverse are either uncalibrated, non-reproducible, rejected on every shot, or not the physical quantity the model assumes.

The most important conclusion is an observability one:

> Ball launch direction plus an estimated club-translation direction can suggest a face normal through a coefficient model. It cannot, by itself, locate a strike on the face. Strike coordinates require the face origin and in-plane axes, hence a reliable full clubhead pose and per-club geometry.

The six highest-priority blockers are:

1. **Camera and radar disagree by about 5 degrees on both axes.** The disagreement behaves largely like a datum offset, not random noise. Neither sensor has yet been independently validated well enough to arbitrate it.
2. **The shipped horizontal radar result cannot be reproduced offline.** The current code gives a nearly constant `+9.13 degrees` difference from the saved shipped value. Horizontal comparisons should stop until its provenance is found.
3. **The raw radar club-angle channels fail every shot.** Raw AoA is rejected 21/21 with physically impossible values around -25 to -37 degrees; radar club path and phase-span checks are also rejected on every shot.
4. **The camera/radar club fusion does not enforce same-point correspondence.** It assigns one IWR range history to every optical feature on a rotating, extended clubhead. Radar scattering centre, image feature, head centroid, and actual contact point are not interchangeable.
5. **The preferred camera interval crosses impact.** It averages pre- and post-impact motion even though collision changes head speed, direction, and rotation. D-plane requires the pre-impact contact-point velocity extrapolated to first contact.
6. **The CAD frame is anchored to the back of the club.** `detect_face_plane` rejects the real 2,950 mm² striking face because the shaft contaminates an extremity test, then selects an 863 mm² cavity rim. Any physical face normal, loft gate, or roll axis derived from that frame is invalid until the mesh is explicitly registered and downstream work is rerun.

There is also a timing implementation issue not called out in the public artifact: the capture stores accurate `SensorTimestamp` values, but production club fusion passes callback-time `host_timestamp_ns` into the estimator. That host value is taken after the frame is extracted and copied. Software delivery latency can therefore enter a motion interval only 2.138 ms long. The estimator also places contact halfway between the last unchanged-ball frame and the next frame. The artifact's “33 microseconds equals 1.5 mm” argument confuses raw OPS sample spacing with end-to-end contact-time accuracy.

The practical recommendation is to separate the project into three claims:

- **Measured ball metrics:** validate camera/radar launch datums and OPS speed first.
- **Inferred D-plane metrics:** face angle and dynamic loft may be reported only as model-dependent estimates with coefficient and input uncertainty.
- **Measured club/strike metrics:** require a validated pre-impact rigid-body state and face coordinate system. The present single-view production path does not supply them.

## What the artifact gets right

Several parts of the artifact are unusually candid and should be retained:

- The original absolute-brightness ball threshold was impossible on real 8-bit footage. Signed local contrast and temporal departure are better design choices.
- The artifact shows failed frames rather than hiding them. The polished club becoming indistinguishable from a saturated mat is a real, impact-centred failure mode.
- Synthetic imagery with a fixed club intensity could not reveal aspect-dependent specular disappearance. Real-data falsification was essential.
- A camera image supplies bearing while radar can supply range, but only when both observations refer to the same target point and are synchronized in a calibrated common frame.
- The project correctly backed away from old accuracy numbers generated at the wrong plate scale and exposure.
- The newer 21-shot work correctly refutes the “55% dynamic-loft compression” story. A small 7-iron/9-iron launch gap is genuinely present in two reconstruction routes; static loft is not delivered loft.

Those are good foundations. The problem is that the artifact then moves from “this signal exists” to “the architecture is adequate” too quickly.

## Corrections to the public artifact

| Artifact claim | Audit finding | Required correction |
|---|---|---|
| The lens is “measured” as 2.8 mm. | That is a nominal lens specification, not a calibrated camera matrix. Focus, sensor crop/scaling, principal point, unequal `fx/fy`, and lens distortion still matter. | Say “nominal 2.8 mm lens.” Calibrate `K` and distortion at the exact focus, resolution, and crop. |
| Ball size and the lens datasheet agree on plate scale/range. | This is partly circular because the detected ball boundary is itself biased. The top is cut inward by 1.85 px and sector-derived centres disagree by 2.47 px. | Do not use ball-derived focal length as independent confirmation of the ball detector. Use a calibration target and tape-known geometry. |
| Camera-to-ball distance is about 1.43 m. | The newer rig geometry and ball measurement are around 1.576 m; the artifact value belongs to an older inference chain. | Replace it with the physically measured distance and uncertainty for each enclosure. |
| Re-run accuracy at 997 microseconds exposure. | The newer 21-shot session uses roughly 247/298 microseconds and still measures about 2.97 px of blur. Re-running at 997 microseconds would evaluate a known bad operating point. | Validate at the selected production exposure and explicitly sweep blur, not just brightness. |
| A geometric circle fit fixes the partial-arc problem. | Geometric refinement removes one algebraic-fit bias but a free 3-parameter circle remains ill-conditioned on a one-sided 120-degree arc only 13 px across. The test shown proves exact recovery from exact points, not the stated -44% failure of the old real-data path. | Reproduce the old estimator on the same noisy/biased contours, then use a known-radius sphere/edge model with sector uncertainty. |
| The camera, vantage, and timing are sound. | Cadence is stable, but pose observability, calibration, exposure blur, and impact timing are not validated. The club disappears in precisely the important frames. | Limit the claim to stable sensor cadence and useful ball visibility. |
| OPS I/Q at 30 ksps yields 33 microsecond impact timing. | Sample spacing is not timestamp accuracy. Trigger detection, clock mapping, exposure timing, frame quantization, callback latency, and target definition all contribute. Measured camera/radar impact agreement has about 1.41 ms spread. | Publish a timing budget and use sensor exposure timestamps, not callback timestamps, for optical motion. |
| A 47 mm range bin and 2-3 bins/frame imply a clean club track. | Resolution-cell spacing is neither localisation accuracy nor target identity. Body, hands, shaft, multipath, and an aspect-moving scattering centre can win the track. The current selector is heavily gated by OPS speed. | Validate the selected range track against a common optical/radar target and a rigid reference swing. |
| Doppler is useless because it aliases. | Aliased Doppler is still useful as a discrete hypothesis and is already needed for TDM phase derotation. OPS speed and range continuity can assist unwrapping. | Say “ambiguous without unwrapping,” not “useless.” |
| Ray x range-sphere intersection closes clubhead 3D position. | It closes a point only if camera and radar observe the same physical point at the same time with known extrinsics. Current code gives the same radar range to many different image features. | Treat same-point association as an explicit testable assumption. |
| TrackMan's 60 fps camera shows the approach is adequate. | A proprietary dual-radar system does not validate OpenFlight's single-view geometry. Frame rate alone says nothing about exposure, spatial resolution, SNR, radar aperture, or algorithms. Independent studies find club metrics less accurate than ball metrics. | Remove the analogy as evidence. Cite it only as commercial precedent. |
| Impact location “falls out of fusion.” | A tangent plane gives a world contact point but not toe/heel or high/low coordinates. Two in-plane translations remain unknown without face origin and axes. | State that full 6-DoF head pose and per-club face registration are required. |
| One properly exposed shot retests almost everything. | One shot is diagnostic, not validation. The project's own corrections show how often one-shot conclusions failed. | Require a designed multi-shot, multi-club, deliberately off-centre, blind holdout set. |

## What the latest 21-shot evidence actually establishes

### Ball launch gap: real; absolute datum: unresolved

An independent camera-ray plus IWR-range reconstruction gives:

| Route | 7-iron mean | 9-iron mean | mean gap | median gap |
|---|---:|---:|---:|---:|
| Camera rays + IWR range | 24.08° | 26.98° | 2.91° | 4.22° |
| LCMF | 18.94° | 21.53° | 2.59° | 3.60° |

This kills the earlier claim that LCMF must be compressing an expected 8-degree delivered-loft gap. The relative gap survives a 13-degree camera-pitch sweep within 0.03 degrees.

It does **not** validate either absolute launch angle. Camera minus LCMF is `+5.34 +/- 0.83 degrees` vertically on 20/20 shots. The shipped camera minus radar horizontal difference is `+5.10 +/- 1.09 degrees`. The vertical relation is nearly an offset: `camera = 0.954 * LCMF + 6.28`, with `r = 0.971`.

The agreement between two camera-side implementations (`+0.064 +/- 0.199 degrees`, `r = 0.998`) verifies that they compute the same image-derived angle. It does not independently validate the world datum because they still inherit related camera geometry, target localisation, and coordinate assumptions.

Range-only vertical launch cannot referee the dispute. Its per-shot estimates range from -2.9 to 86 degrees with within-club scatter of 12.8 and 17.9 degrees. The ballistic curvature signal is only about 18 mm against about 12 mm of range noise over the observed window.

### OPS speed contract: not the club-gap cause, but still in the uncertainty budget

The phase-derotation path correctly wants radial speed. A ballistic trajectory model wants total speed. Correcting only that ballistic input changes vertical launch by about `-0.0095 degrees` and the club gap by `-0.003 degrees`, so it is not the cause of the 7i/9i result.

However, measured sensitivity is `d(LCMF vertical launch)/dv = +0.913 +/- 0.113 degrees per (m/s)`. A 1% OPS ball-speed error produces about 0.41 degrees of launch error. OPS accuracy and projection therefore belong in the end-to-end uncertainty budget.

### Radar club angles are not presently measurements

- Raw radar AoA is rejected 21/21; medians are about -29 degrees for 7i and -33 degrees for 9i.
- Radar club path and `rejected_phase_span` fail on every shot.
- The fused AoA looks plausible on 19/21 shots, but two values are gross failures at about -31 and +25 degrees.
- The horizontal shipped output cannot be reproduced offline and differs by an almost constant 9.13 degrees.

A plausible-looking fused value cannot be promoted when its independent component is universally rejected and its provenance is not reproducible.

The hardware description also needs precision. The IWR6843LEVM has 3 TX x 4 RX = 12 virtual channels overall, but they are not 12 distinct vertical elements. TI's board documentation quotes approximately 15-degree azimuth and 58-degree elevation two-target resolution for the LEVM geometry [TI LEVM user guide](https://www.ti.com/lit/ug/swru585/swru585.pdf), and TI's antenna comparison explains why ISK/LEVM-style layouts have only two effective elevation positions [TI antenna-module comparison](https://www.ti.com/document-viewer/lit/html/SWRA758). Two-target resolution is not the same as single-target angular accuracy, but neither does a high-SNR target turn the layout into a 12-element elevation array. Phase calibration, floor multipath, sparse snapshots, and target scattering therefore have to be validated empirically.

### The CAD result must be reset, not patched around

The real striking face has normal approximately `[-0.941, 0.021, -0.337]` and measures about 33.10 degrees loft and 61.19 degrees lie. The auto-selected cavity rim has normal approximately `[0.998, -0.001, 0.059]` and reads 17.48 degrees loft.

The detector finds the real striking face, but rejects it because it computes extremity using **all mesh vertices**, including a shaft that adds 24.23 mm along the face normal. It then chooses the smaller back rim. This breaks physical face-normal interpretation, the loft gate, and the roll axis. Silhouette IoU is unchanged because a binary outline does not care how mesh axes are labelled.

The robust fix is not another heuristic threshold. Every supported club model should ship with authored metadata:

- striking-face surface or triangle set;
- face origin;
- toe and upward axes;
- outward normal;
- shaft axis and shaft-to-head transform;
- face surface model, including bulge/roll where applicable;
- club mass, centre of gravity, and inertia only if collision modelling will use them.

Automatic inference can assist model onboarding, but a human-visible render and explicit registration must be the authority.

## Architecture and implementation risks

Key code locations in the reviewed checkout:

| Issue | Current source |
|---|---|
| Host callback timestamps passed to club fusion | `src/openflight/server.py:2718-2721`; host time is sampled after image extraction in `camera/capture_runtime.py:515-540` |
| Cross-impact interval preferred over pre-impact interval | `camera/club_delivery.py:89-90` and `:419-424` |
| Ball diameter used to derive focal length | `camera/club_delivery.py:236-256` and `:626-658` |
| One radar range applied to all tracked features | `camera/club_delivery.py:546-573` |
| OPS scalar treated as total velocity magnitude in fallback | `camera/club_delivery.py:600-715` |
| LK features and forward/backward checks | `camera/club_delivery.py:721-886` |
| Contact assigned between adjacent frame timestamps | `camera/club_delivery.py:928-974` |
| AoA uses forward rather than total horizontal speed | `camera/club_delivery.py:283-288` |
| Static exposure score barely penalizes blur | `scripts/hardware-test/calibrate_camera_exposure.py:158-175` |
| Whole-mesh extremity gate and largest-area winner | `research/silhouette_poc/generator/mesh_truth.py:401-426` |

### 1. Camera calibration is being inferred from the object being measured

`club_delivery.py` derives one scalar focal length as:

```text
focal_px = detected_ball_diameter_px * assumed_camera_ball_range / physical_ball_diameter
```

It then assumes the principal point is the image centre and derives pitch/yaw from the same ball. This conflates:

- ball-boundary bias with focal scale;
- principal-point error with camera yaw/pitch;
- lateral-offset error with yaw;
- camera-height or tee-distance error with pitch;
- unmodelled lens distortion with all of the above.

The top-edge loss is therefore not just a few millimetres of ball error; it contaminates focal scale and every reconstructed club point. A calibrated camera should instead use a fixed `K = (fx, fy, cx, cy)` and distortion model estimated at the actual focus, crop, and readout. Zhang's planar-target method explicitly estimates intrinsics and radial distortion and refines them by reprojection likelihood [Zhang 2000](https://doi.org/10.1109/34.888718).

### 2. Accurate sensor timestamps are recorded, then not used for club velocity

The camera archive contains both `sensor_timestamp_ns` and `host_timestamp_ns`. The sensor timestamp is hardware metadata for the start of exposure; libcamera defines it as the time the first row of the active array is exposed [libcamera controls](https://docs.libcamera.org/master/public-api/control__ids_8h.html). Yet `server.py` passes `archive["host_timestamp_ns"]` to `estimate_chained_delivery`.

That host value is captured only after `make_array` and image unpacking. It is a delivery/callback timestamp, not a photon-acquisition timestamp. Picamera2 itself uses `SensorTimestamp` for accurate video timing, and Raspberry Pi notes that frame delivery is heavily pipelined.

Recommended timing model:

1. Use `sensor_timestamp + exposure_time/2` as the global-shutter optical measurement time, after verifying the sensor metadata convention.
2. Robustly fit an affine mapping between sensor clock and the host/radar clock from the paired timestamps saved in every frame.
3. Calibrate fixed trigger and processing latency with an event visible to both modalities.
4. Treat first contact as a latent time inside the last-present/first-absent interval, not as an exact frame midpoint.
5. Propagate the resulting timing posterior into path, AoA, and strike uncertainty.

### 3. The fusion assigns one radar range to every optical feature

For each frame, `_delivery_from_feature_pair` intersects every tracked image ray with the same radar range sphere. A clubhead is an extended rotating body: optical features occupy different depths, and the dominant radar scattering centre can migrate with face angle. The actual D-plane velocity is the velocity of the contacting face point:

```text
u_contact = v_CG + omega x r_contact
```

Neither the radar scattering centre nor the median LK feature velocity is guaranteed to equal it. Multiple internally consistent image features are also not independent evidence when they share the same two frames, range history, focal estimate, and calibration.

Do the existing same-point falsification test before further tuning: for every candidate image track, predict radar radial motion from the optical model and compare it with IWR range/range-rate. A stable constant offset may be calibrated. An aspect-dependent or time-varying residual means the modalities are following different points and must not be fused as one 3D trajectory.

### 4. The preferred interval crosses the collision

`PREFERRED_PATH_OFFSETS = ((-2, +1), (-4, -1))` makes the first choice span from two frames before contact to one after it. At 467.6 fps that is roughly a 6.4 ms interval containing the collision and post-impact club motion. The collision changes club linear and angular velocity; commercial definitions likewise distinguish delivery at contact from collision-induced motion [TrackMan club-data definitions](https://www.trackmangolf.jp/blog/club-data-definitions).

The primary estimate should use only pre-impact observations, fit a physically modest motion model, and extrapolate to first contact. Cross-impact and post-impact intervals are valuable as collision diagnostics, not as the preferred delivered path.

### 5. Sparse Lucas-Kanade flow is fragile on this target

The shipped path detects generic corners near a segmented head and tracks them with pyramidal Lucas-Kanade plus a forward/backward check. LK relies on approximate brightness constancy in a local patch. Specular highlights on polished metal are view-dependent and explicitly violate that assumption; occlusion and changing silhouette create the same problem [MIT optical-flow chapter](https://visionbook.mit.edu/optical_flow.html).

Forward/backward consistency only says the numerical tracker can return. It does not prove that a highlight is attached to the same material point. Better choices include:

- a sequence-level rigid head model rather than independent feature velocities;
- edge-distance or gradient-orientation likelihoods that tolerate intensity changes;
- explicit exposure-integrated rendering;
- sector/feature residuals checked against rigid-body optical flow;
- a calibrated second view if full pose is required.

### 6. The AoA formula is not the exact 3D elevation

The code computes attack as `atan2(vertical, forward)`. The exact elevation of a 3D velocity is:

```text
atan2(vertical, sqrt(lateral^2 + forward^2))
```

The difference is small near zero path, but it is a systematic, path-dependent bias and easy to correct once conventions are frozen.

## Robust ball detection under one-sided contrast loss

The observed failure is severe relative to the target: a 1.85 px missing top on a radius of about 6.5 px is 28% of the radius. At the current 0.296 px/mm scale it is about 6.1 mm. A free circle fit cannot manufacture information that the image never captured.

The recommended estimator is a **calibrated known-radius sphere edge model**, not a generic free circle:

1. **Undistort first.** A sphere's perspective silhouette is generally a conic/ellipse, not an exact circle in raw distorted pixels. With known radius and calibrated camera, use the sphere tangent-cone geometry directly. Tóth and Hajder show that a calibrated sphere projection is a constrained ellipse and that known radius permits recovery of sphere position [IJCV 2023](https://doi.org/10.1007/s11263-023-01766-1).
2. **Fix or tightly constrain radius/depth.** At address, tee geometry and radar range provide scale. Reducing centre-plus-radius to a two-parameter centre solve greatly improves conditioning for a short arc.
3. **Fit the raw edge likelihood, not a threshold contour.** For each candidate sphere, sample intensity or gradient normal to the predicted boundary. Use a robust loss and allow contrast polarity to vary by sector. A gradient-directed fixed-radius Hough vote is a good seed; robust nonlinear refinement should produce the final estimate.
4. **Weight angular sectors, not pixels.** Dense contour runs are correlated and should not dominate. Give comparable weight to each visible sector, report the largest missing contiguous arc, and bootstrap by sector.
5. **Model visibility explicitly.** Saturated or low-gradient sectors are censored observations, not zero-radius evidence. Exclude them from the likelihood while retaining a prior over centre/radius.
6. **Use time.** Register and average the stationary pre-impact ball to improve SNR. After departure, use the newly exposed background at the tee to estimate what lay behind the ball, provided this occurs before the known lighting change. Across flight frames, fit one ballistic trajectory and known sphere rather than unrelated circles.
7. **Replace the local median when it crosses scene boundaries.** A median window about three ball diameters wide can span mat, wall, shadow, and ball. Fit a robust local background surface outside the candidate sphere or use an empty-scene/background-after-departure model.
8. **Return asymmetric uncertainty.** A top-missing detection should have much larger vertical than horizontal centre covariance. Downstream range, pitch, and impact calculations must consume that covariance rather than one radius-tolerance gate.

Circle-fitting research agrees that small arcs amplify bias and variance, and that algebraic methods differ materially under noise [Al-Sharadqah and Chernov](https://arxiv.org/abs/0907.0421). The current exact-point unit test is necessary but not sufficient. Build synthetic tests that include pixel integration, PSF blur, saturation, mat gradients, one-sided erasure, and correlated contour sampling, then validate on a physical ball translated by known subpixel-equivalent increments.

At only 12.6 px ball diameter, resolution is a real hardware constraint. A 1:1 readout roughly doubling the ball to 25 px is likely more valuable for centre accuracy than another unconstrained fitter, although its lower frame rate must be traded against motion sampling. A controlled A/B capture should compare final angular and millimetre uncertainty, not detector pass rate.

## D-plane inversion: how it should and should not be used

The empirical first-order relations can be written as:

```text
horizontal launch = path + kh * (face - path)
vertical launch   = AoA  + kv * (dynamic loft - AoA)
```

and inverted transparently. Wood, Henrikson, and Broadie's controlled work found the ball launched 61-83% toward the face quantity, with the ratio varying by club and impact obliqueness; their player and robot values were not universal constants [Wood et al. 2018](https://doi.org/10.3390/proceedings2060249). The study also filtered strikes near face centre, so its coefficients are not validation for off-centre impact inference.

Use this model as an estimator only when:

- ball launch and club direction are independently calibrated;
- the club vector is the pre-impact contact-point vector or a validated proxy;
- coefficient uncertainty is included and varies with club/spin loft;
- gear effect and off-centre strike are either negligible, measured, or included as uncertainty;
- face/path separation is large enough that the inverse is not dominated by noise.

Do not call its output a direct measurement. A full collision inverse introduces effective mass/inertia, restitution, friction regime, face geometry, strike, angular velocity, and ball spin. With those quantities unobserved, a more sophisticated model can fit the data while becoming less identifiable.

The current project-derived spin cannot validate the same D-plane inverse that generated it. Likewise, the “implied COR” is not COR: true restitution uses relative face-normal velocity before and after impact, including post-impact club state. A value such as 0.92 is a useful inconsistency flag, not a measured material property.

## Why face angle still needs a different measurement path

There are two honest routes:

### Route A: inferred face normal

Validate ball launch, pre-impact club path/AoA, and coefficient envelopes, then publish:

- `inferred_face_angle_dplane`;
- `inferred_dynamic_loft_dplane`;
- confidence interval split into sensor, calibration, timing, and coefficient components;
- explicit rejection when any input fails.

This route can be useful to golfers before impact location is solved. It does not provide face origin, dynamic lie, head roll, or strike coordinates.

### Route B: measured rigid head pose

Track enough club geometry to estimate the head's 6-DoF pose and angular velocity before impact. A single rear silhouette is poorly conditioned for out-of-plane orientation and becomes invisible under the observed specular conditions. The lowest-risk development route is a second synchronized global-shutter view for validation and probably for production-grade strike location. Temporary markers are entirely appropriate for the reference rig even if the shipped product remains markerless.

Corke and colleagues achieved repeatability of roughly 0.2 degrees for face/path, 0.5 degrees for loft, 0.1 degrees for AoA, and 0.6 mm for strike using multiple calibrated high-speed cameras and a rigid face coordinate system—not merely a ball tangent plane [Corke et al. 2019](https://doi.org/10.1177/1754337118792798). Their vertical tape comparison also differed systematically by up to 4 mm because “first contact” and the final contact patch are different measurands. OpenFlight must define which one it reports.

## Impact location: the missing degrees of freedom

For ball centre `B`, radius `R`, and outward face normal `n`, a flat-face tangent point is:

```text
T = B - R*n
```

But strike coordinates are:

```text
toe_heel = (T - O) dot e_toe
high_low = (T - O) dot e_up
```

`O`, `e_toe`, and `e_up` come from registered club geometry transformed by full head pose. Face angle plus loft determines a normal but leaves two translations within the tangent plane unresolved. For drivers, bulge and roll make contact a sphere-to-curved-surface solve and couple local normal to strike location.

The quickest truth source is foot spray or impact tape, read per shot to about 1-2 mm, paired with a frozen pipeline. It should span deliberate toe, centre, heel, high, and low strikes. Report horizontal and vertical bias separately; do not fit and evaluate on the same shots.

## More metrics: realistic readiness

| Metric | Present status | What is missing |
|---|---|---|
| Ball speed | Promising, but OPS projection/accuracy affects launch reconstruction. | Traceable speed check and radial/total contract. |
| Horizontal/vertical launch | Relative 7i/9i gap is credible; absolute datum is not. | Resolve the two-axis ~5° camera/radar offset and 9.13° provenance gap. |
| Club speed | OPS-selected signal is useful but should not automatically be treated as total contact-point speed. | Deprojection and reference validation. |
| Club path / AoA | Experimental; independent radar channels fail all shots and camera fusion has timing/same-point issues. | Pre-impact sensor timing, same-point validation, rigid reference. |
| Face angle / dynamic loft | Model-derived only. | Valid launch and contact-point direction plus coefficient calibration, or direct head pose. |
| Face-to-path / spin loft | Not trustworthy until both operands are. | Same as above, with covariance including correlation. |
| Impact location | Not observable from current face normal/tangency alone. | Face origin/axes, full pose, contact definition, tape truth. |
| Dynamic lie / head roll | Not measured. | Full orientation and correctly registered CAD. |
| Smash factor | Can be a ratio after both speeds are validated. | Do not label it COR or use it alone to infer strike. |
| Low point / turf contact | At best model-derived from AoA and head/ground geometry. | Ground-plane calibration, sole/leading-edge pose, trajectory curvature. |
| Markerless ball spin | Likely resolution-limited at a 12.6 px ball. Dimples are near pixel scale and high spin rotates tens of degrees per frame. | Higher spatial resolution/closer view, photometric texture, alias handling, and independent spin truth. |

## Recommended validation sequence

Do these in order. Later stages should not be used to compensate for a failed earlier stage.

### Gate 0: freeze definitions and coordinates

- Define handedness, signs, target frame, face angle, dynamic loft, AoA, path, and whether “impact” means first tangency, exposure-centred apparent contact, or tape contact patch.
- Version every calibration and output provenance in each shot archive.
- Keep direct measurements, fused measurements, and model-derived values in distinct fields.

### Gate 1: calibrate the instruments

1. Calibrate camera intrinsics/distortion with a ChArUco or checkerboard across the complete image at production focus/readout.
2. Build a camera-visible radar target whose optical reference point is mechanically registered to its corner reflector.
3. Capture that target at many positions spanning range, lateral position, and height. A paper demonstrating single-reflector radar-camera calibration moves the reflector through multiple observations and solves with PnP/RANSAC/nonlinear refinement; one reflector at one position is not a full SE(3) calibration [Cheng et al. 2023](https://doi.org/10.1109/RadarConf2351548.2023.10149669).
4. Reserve target positions as a holdout. Report angular/range residuals and calibration covariance.
5. Calibrate camera/radar boresight to an enclosure datum. Use an accelerometer for pitch/roll drift only; gravity cannot determine yaw.

The LIS3DH can help, but averaging noise is not the same as absolute accuracy. Its datasheet gives typical zero-g offset accuracy of +/-40 mg, roughly +/-2.3 degrees if uncalibrated, and 220 micro-g/sqrt(Hz) noise density in the stated high-resolution condition [ST LIS3DH datasheet](https://www.st.com/resource/en/datasheet/lis3dh.pdf). Perform a multi-orientation installed-board bias/scale calibration. The accelerometer still will not identify optical/radar boresight offset or yaw.

### Gate 2: fix time and exposure

- Use sensor exposure timestamps for optical intervals and map clocks explicitly.
- Measure impact-time bias and spread with a common optical/electrical event.
- Replace the exposure selector's weak static penalty with a hard motion-blur constraint.

At 0.296 px/mm, a 35-50 m/s club moves about 10.4-14.8 px per millisecond. A 250 microsecond exposure smears roughly 2.6-3.7 px; 997 microseconds smears about 10.3-14.7 px. To hold translation blur near 2 px requires roughly 135-193 microseconds, before rotational blur. Achieving that under ambient light may require more continuous NIR illumination, a controlled backdrop, an optical bandpass filter, or a higher-sensitivity/readout trade. Validate eye safety for any added NIR source.

### Gate 3: validate ball state independently

- Test the known-radius sphere estimator on physical translations and known launch angles.
- Use a ball launcher/rail or a carefully surveyed trajectory so camera and radar angular biases can be separated.
- Pass criteria should be defined in degrees and millimetres, not detector hit rate.
- Resolve the current 5-degree absolute disagreement before face inversion.

### Gate 4: validate club translation and association

- Use a rigid pendulum or robot with temporary optical markers and, during calibration, a registered radar reflector.
- Compare optical material-point motion, radar scattering-centre motion, OPS speed, and true contact-point motion.
- Use only pre-impact samples and jackknife frames.
- Re-run timing shifts of +/- one frame and the measured clock uncertainty.
- Reject results whose full jackknife/perturbation range exceeds the desired accuracy.

### Gate 5: choose the face-angle product claim

- For an inferred product, freeze the D-plane coefficient model and publish its sensitivity envelope.
- For a measured product, add/validate full head pose. Do not rely on binary silhouette IoU alone.
- In both cases, compare against an independent multi-camera or trusted reference after freezing the pipeline.

Independent launch-monitor validation literature supports this caution: ball parameters generally agree better than clubhead parameters, and commercial output is not a substitute for a traceable reference [Leach et al. 2017](https://doi.org/10.1016/j.measurement.2017.08.009).

### Gate 6: validate strike location last

- Explicitly register every club face.
- Collect blinded foot-spray/tape truth with deliberate strike variation.
- Report bias, standard deviation, 95% limits of agreement, failure rate, and separate toe/heel versus high/low performance.
- Do not tune on the holdout.
- Treat first-contact and tape-patch vertical offsets as different measurands.

## Concrete next work, ranked

1. **Switch club-motion timing analysis to sensor timestamps and audit the contact-time convention.** This is an existing-data/code audit with potentially large effect.
2. **Find the deterministic +9.13-degree horizontal provenance error.** Until then, stop using shipped horizontal radar values for validation.
3. **Build the multi-position camera/radar calibration target dataset.** This is the cleanest way to resolve the roughly 5-degree two-axis disagreement.
4. **Replace heuristic face discovery with explicit authored CAD face metadata, then rerun every orientation result.** Do not merely relax the current extremity gate.
5. **Run the same-point residual test.** If optical and radar club points drift relative to each other, redesign the fusion state around an extended rigid body rather than one point.
6. **Make the primary club estimate pre-impact only.** Preserve cross-impact motion as a collision diagnostic.
7. **Implement and validate the known-radius, sector-weighted sphere likelihood.** Use calibrated intrinsics and propagate asymmetric centre covariance.
8. **Complete falsification tests 3, 4, 6, 7, 8, 10, and 11 on the frozen 21-shot set.** Test 12 remains the blind reference session.
9. **Choose explicitly whether production impact location is allowed a second camera.** If not, run an observability experiment before investing further in a single-view pose optimiser.
10. **Collect the blind truth session.** Temporary markers and impact spray are validation instruments, not a violation of the final markerless product goal.

## Recommended wording for Claude or public documentation

Until the gates above pass, the defensible status is:

> OpenFlight detects the ball and club in real high-speed footage and has experimental camera/radar estimates of ball launch and club delivery. A 21-shot falsification set confirms the relative 7-iron/9-iron launch gap but reveals an unresolved approximately 5-degree camera/radar datum disagreement, universally rejected radar-only club angles, and an incorrectly registered CAD face. Face angle and dynamic loft are therefore model-dependent D-plane estimates, not validated measurements. Impact location remains unobserved because the system does not yet recover the face origin and axes at first contact. Work is focused on camera/radar calibration, sensor-time fusion, same-point association, known-radius ball fitting, explicit club registration, and blind impact-tape/reference validation.

That wording is less dramatic than “impact location falls out of fusion,” but it is technically accurate and gives contributors a much clearer path to a real result.
