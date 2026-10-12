# System-level fusion, time synchronization, synthetic ground truth and data flywheel for OpenFlight

Scope: system-level techniques for OpenFlight (Pi 5 + OPS243-A CW Doppler + IWR6843 FMCW MIMO + OV9281 behind-ball camera + SEN-14262 sound trigger + LIS3DH), with concrete recommendations. Project facts used as inputs come from the repo's `docs/development/fusion-master-plan.md` (read in this pass) and the project memory index. Web research budget was ~20 calls, so several classic references are cited from search-result summaries rather than full-text reads; this is flagged where it applies.

Evidence-quality legend used below: **[primary]** = paper/official doc/issue tracker read directly; **[summary]** = from a search-result abstract/summary, full text not read; **[secondary]** = blog/vendor marketing; **[project]** = OpenFlight's own docs/measurements; **[derived]** = my own first-order calculation, shown so it can be checked.

---

## Q1. Time synchronization across heterogeneous sensors

### Takeaway
On a single Pi 5 host the clocks are not the problem: kernel GPIO edge timestamps are good to roughly 5-12 µs and sensor clocks drift by only tens of µs over a 200 ms capture. The millisecond-level error comes from how each sensor defines the *physical event*: acoustic delay, Doppler FFT windows, frame period and UART/CLI request latency. The highest-leverage fix costs nothing extra: every sensor can observe the ball leaving the tee, so extrapolate each sensor's own ball track back to the tee to get a sub-millisecond contact time in that sensor's clock. Then estimate the per-sensor offsets per shot, keep them as a per-unit calibration and validate them against an independent LED/contact bench. PTP is irrelevant because there is one host, and it is flaky on the Pi 5.

### Cited Findings
- The Pi 5's RP1 pinctrl driver has no hardware-timestamp (HTE) callbacks. No RP1 support captures a timestamp on a GPIO edge; edges are timestamped in the IRQ path. — [Raspberry Pi Forums: RP1 GPIO HTE](https://forums.raspberrypi.com/viewtopic.php?p=2378082) [summary]
- A measurement of Pi 5 GPIO interrupt timestamps found a systematic bias of ~11.7 µs. Most of it is RP1 wake latency from its L1 low-power state. Disabling L1 ASPM cut it by ~5.5 µs, and fixing the CPU at 2.4 GHz removed ~1 µs more, for a final bias of ~5.2 µs. Methods were a PPS echo measured with a time-interval counter plus eBPF probes, and GPIO polling. — [SatPulse, "Measuring systematic PPS bias on the Raspberry Pi 5", 2026-09-06](https://satpulse.net/2026/09/06/measuring-systematic-pps-bias-on-the-raspberry-pi-5.html) [primary]
- Pi 5 GPIO passes through the external RP1 over PCIe, which one vendor article says adds 10-50 µs of load-dependent latency variance to GPIO operations. — [Industrial Monitor Direct KB](https://industrialmonitordirect.com/blogs/knowledgebase/raspberry-pi-5-rp1-gpio-timing-issues-workarounds-and-solutions) [secondary; the SatPulse numbers are more trustworthy]
- Pi 5 Ethernet advertises hardware PTP timestamping, but a raspberrypi/linux issue reports it broken on kernels 6.1/6.6.13 (ptp4l logs "received SYNC without timestamp"). Software timestamping worked. — [raspberrypi/linux #5904](https://github.com/raspberrypi/linux/issues/5904) [primary; fix status not established]; see also [Pi Forums: Pi 5 Ethernet PTP](https://forums.raspberrypi.com/viewtopic.php?t=358275) and [jclark rpi-cm4-ptp-guide](https://github.com/jclark/rpi-cm4-ptp-guide) (CM4/CM5 have PHY-level PTP with external pins).
- Online temporal calibration is standard in visual-inertial estimation. Li & Mourikis (IJRR 2014) treat the camera-IMU time offset as a state estimated online and analyse when it is identifiable. — [Li & Mourikis 2014, SAGE](https://journals.sagepub.com/doi/10.1177/0278364913515286) [summary]
- Qin & Shen (IROS 2018, best student paper) estimate the camera-IMU offset `td` inside the optimisation by shifting each feature observation along its image-plane velocity (`observation(t - td) ~ observation(t) - velocity·td`). It ships in VINS-Mono and VINS-Fusion. — [arXiv 1808.00692](https://arxiv.org/pdf/1808.00692), [VINS-Fusion](https://github.com/HKUST-Aerial-Robotics/VINS-Fusion) [summary]
- A targetless 3D mmWave radar-camera method on TI AWR1843/IWR6843AOP recovers the temporal offset to within 6 ms of a target-based method on a handheld rig, and to <10 ms in simulation. It uses continuous-time B-splines and Doppler ego-velocity. — [Spatiotemporal Calibration of 3D mmWave Radar-Camera Pairs, arXiv 2211.01871](https://arxiv.org/html/2211.01871v4) [primary]. Note: ms-level offset accuracy from slow ego-motion is far coarser than OpenFlight needs. Impulsive golf events are much more informative.
- [project] The current trigger is one SEN-14262 edge fanned to OPS `HOST_INT` and Pi BCM17 and forwarded to the camera. The OPS buffer is 4,096 samples at 30 ksps = 136.53 ms, split by `S#16` into 68.27 ms before and after the trigger. IWR frames are 3 ms (wide) or 2 ms (dense), with 27-28 ms kept before the CLI request. Camera keeps 150 ms before and 50 ms after. The plan requires an independent LED/GATE/contact bench and warns against inferring contact from a clock that both estimates already use. — `openflight-agent-review-workflow/docs/development/fusion-master-plan.md` (sections "Timing and acquisition work" and "Soundless trigger architecture").
- [project] The plan cites the [Picamera2 manual](https://datasheets.raspberrypi.com/camera/picamera2-manual.pdf) for SensorTimestamp being taken at first-pixel readout, and says the actual mode/driver must be verified. I did not independently re-read the manual in this pass.

### Inferences
- **[derived] Clock arithmetic.** At 100 ppm, drift over a 200 ms window is 20 µs. GPIO IRQ bias is ~5-12 µs (SatPulse). OPS sample spacing is 33 µs. All three are one to two orders of magnitude below the measured contact-to-trigger spread (0.5-4 ms, ±1.5 ms jitter, from project memory). **Do not spend effort on PTP, a GPS PPS or hardware timestamping.** Spend it on defining the event.
- **[derived] Why the acoustic trigger is inherently ms-level.** At 343 m/s, every metre of path costs 2.9 ms, and the mic's threshold crossing depends on strike loudness (rise time). Off-centre strikes, turf versus mat and club type change amplitude, so trigger latency varies by strike. That is consistent with the ±1.5 ms jitter. Keep the mic as a *trigger*, never as a *timestamp*.
- **[derived] How timing error becomes angle error for the club.** Near impact the clubhead velocity rotates in the swing plane at ω ≈ v/r. With v = 35-45 m/s and an effective radius of curvature r ≈ 1.5-2 m, ω ≈ 17-30 rad/s ≈ 1.0-1.7°/ms. With the measured swing plane of ~59° (project memory), the vertical part (attack angle) changes at about ω·sin 59° ≈ 0.9-1.5°/ms. The horizontal part (path) changes at about ω·cos 59° ≈ 0.5-0.9°/ms. **So ±1.5 ms of contact-time error gives roughly ±1.3-2.2° of attack angle and ±0.8-1.3° of path.** This matches the project's measured "attack angle moves ~3°/frame" if the frame period is ~3 ms (288 fps = 3.47 ms). It is the dominant error term for the club metrics, bigger than most geometric errors. Speed (smash, club speed) is far less sensitive: dv/dt near the bottom is small.
- **[derived] Timing error mixed across sensors (camera bearing plus radar range).** If camera bearings are paired with IWR ranges sampled at a mismatched time, the range error is Δr = v_radial·Δt. That is 60 mm at 40 m/s and 1.5 ms (club) and 105 mm at 70 m/s (ball). For the ball, a constant offset mostly slides points along a straight track and leaves the direction nearly unchanged. For the club on a curved arc, and for any fit that mixes both sensors' points, it biases direction. **Estimate Δt as a free parameter in the per-shot fit** (the Qin & Shen idea): the club's large image velocity makes it well observed.
- **[derived] Recommended timing architecture ("ball clock per sensor" with independent validation):**
  1. *Camera:* contact time = time of the first frame showing ball motion, minus d/v_ball. Here d is the ball's displacement from the rest position (resting-ball calibration) and v_ball is the radar ball speed. At 60 m/s, 5 mm of position error is 0.08 ms. Exposure midpoint, not frame start, is the time reference.
  2. *IWR:* fit the ball range track r(t) = r_tee + v·(t − t_c) over the first frames and solve for t_c. At 60 m/s, 1 cm of range-fit error is 0.17 ms. The IWR anchor to host time goes through the CLI request, which is the weakest link, so prefer the IWR's in-data t_c over host arrival times.
  3. *OPS:* the ball's Doppler line appears abruptly. Use a short-window (e.g., 64-128 sample) STFT or a matched onset detector. Its time is sample-exact relative to the `HOST_INT` trigger index.
  4. Compute the per-shot offsets between sensors. Store their distribution per unit as a calibration record with median and MAD. Flag shots with outliers, and flag drift across sessions.
  5. **Validate independently.** This per-shot method uses the ball in every sensor, so it is self-consistent but still needs an external truth (the project memory records that the earlier "ball clock" validation was circular). Use an LED driven by the same GPIO edge and visible in the camera frame, plus a contact/piezo or break-beam sensor at the tee sampled on a Pi GPIO. Both are hobby-budget parts (under $20). The LED closes camera-to-Pi, and the contact sensor closes physics-to-Pi.
- Hardware-triggered camera frames (OV9281 FSYNC from a Pi PWM) are optional. Sensor timestamps plus the LED check are enough. Hardware triggering helps mainly if the dead gap between exposures (±0.67 ms at 800 µs, per project memory) has to be placed deliberately around impact.

### Gaps
- Whether raspberrypi/linux #5904 (Pi 5 PTP) is fixed; not established. It is irrelevant for a single-host system.
- I did not find a public measurement of the SEN-14262's envelope-threshold latency versus strike amplitude. It should be measured on the bench (mic output vs contact sensor).
- IWR6843 sync-in/GPIO frame-trigger options for the user's firmware were not researched here. TI docs would be needed.
- No published golf-specific study relating sync error to launch-monitor metric error was found. The numbers above are first-order derivations.

---

## Q2. Fusion: joint estimation of ball launch and club delivery with physics priors and self-diagnostics

### Takeaway
The data are short (~100-200 ms), per-shot and offline-tolerant, so the right estimator is a **small per-shot batch least-squares / factor-graph smoother**: "bundle adjustment of the trajectory" with robust losses. A Kalman or particle filter is the wrong fit. Pre-contact club motion and post-contact ball motion are separate segments, joined by a contact-time variable and soft impact-physics factors. Physics relations should serve mainly as **gates and diagnostics**, not as hard constraints, so metrics stay independently scoreable. The plan already says: never score agreement with a speed imposed as a solver constraint.

### Cited Findings
- A joint radar-camera batch optimisation (Ceres, continuous-time B-splines, residuals from radar range-rate plus camera poses, solving extrinsic rotation, translation, scale and time offset together) is a working pattern on TI IWR6843-class radars. — [arXiv 2211.01871](https://arxiv.org/html/2211.01871v4) [primary]
- Ball aerodynamics (usable as a flight prior): Bearman & Harvey (1976) measured a spinning-ball model over Re 40,000-240,000 and spin ratio 0.02-0.3. The lift coefficient rose monotonically from ~0.08 to 0.25 with spin ratio. Drag was ~0.27, rising to ~0.32 above spin ratio 0.1 (induced drag), and the trends were Re-independent for Re 126k-238k. Smits & Smith extended the range to spin ratio 0.04-1.4, measured spin decay and proposed a flight model, with slightly higher C_L and stronger C_D dependence on spin. — [A study of golf ball aerodynamic drag](https://www.researchgate.net/publication/251716774_A_study_of_golf_ball_aerodynamic_drag) [summary]; see also [Aerodynamics of Golf Balls in Still Air](https://doi.org/10.3390/proceedings2060238) [summary]
- Impact models: Daish first described launch physics, and Penner generalised it to a curved driver face. Later work showed the ball *grips* the face rather than rolling, giving more spin than earlier estimates. Penner also found that for a fixed club speed, more loft gives lower ball speed, higher launch and more backspin. A 2024 Sports Engineering paper gives 3-D clubhead-ball impact models for drivers *and irons*. — [Three-dimensional golf clubhead-ball impact models for drivers and irons, Sports Engineering (2024)](https://link.springer.com/article/10.1007/s12283-024-00456-6) [summary; full text paywalled/redirected, authors and validation not confirmed]; [Penner, "The physics of golf"](https://www.researchgate.net/publication/230951973_The_physics_of_golf) [summary]
- The D-plane comes from Jorgensen's *The Physics of Golf* and was popularised by Trackman. It is spanned by the club-path direction and the face normal at impact. Secondary sources say face angle sets ~85% of start direction for a driver and ~75% for short irons. — [The DIY Golfer: D-Plane](https://www.thediygolfer.com/blog/d-plane-golf) [secondary]; [Launchpoint Golf](https://launchpointgolf.com/articles/club-path-vs-face-angle-launch-monitor/) [secondary; percentages are rules of thumb, not measurements]. Trackman's own definitions: [Trackman club data definitions](https://www.trackman.com/blog/club-data-definitions), [Trackman face-to-path](https://www.trackman.com/blog/face-to-path) [vendor primary].
- Doppler spin sidebands: a spinning ball's return has sidebands symmetric about the bulk Doppler line, spaced at the spin frequency. They come from non-spherical features, strengthened by the ball's dielectric lens effect. — [Trackman patent US20140191896A1 "Ball spin rate measurement"](https://patents.google.com/patent/US20140191896) [primary patent]; related patent family [US 10,393,870 "Determination of spin parameters"](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/10393870) and [US 10,775,492 "Golf ball spin axis measurement"](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/10775492).
- [project] The plan's architecture keeps the OPS/IWR ball baseline, develops camera-bearing plus radar range/radial-velocity trajectories alongside it, reuses one implementation for live and replay, and starts with "short per-shot robust fits", adding complex estimators "only when a measured failure justifies them". — fusion-master-plan.md, "Architecture decisions".

### Inferences
- **Recommended per-shot estimator (Pi-feasible, CPU-only, milliseconds):** `scipy.optimize.least_squares` with a `soft_l1`/`cauchy` loss, or GTSAM/Ceres if a factor-graph API is wanted. The problem has a few dozen parameters, so a Pi 5 solves it in well under 100 ms.
  - *Variables:* club segment as one rigid-body motion over about ±10 ms: constant-curvature arc plus one rotation rate (project memory: "fit one rigid rotation, not 3 angles/frame"; ω = v/r is a free validator). Ball segment as launch position at the tee, velocity vector and spin (optional). Contact time t_c. Per-sensor time offsets (priors from the Q1 unit calibration). Camera-radar rotation (prior from Q3 registration).
  - *Measurement factors:* camera bearings (reprojection in pixels), IWR range, IWR azimuth/elevation, IWR radial velocity, OPS radial speed (radial, not total: the plan already flags the radial-versus-total model), and the acoustic edge as a weak prior on t_c (0.5-4 ms before trigger).
  - *Physics factors (soft, switchable):* the ball starts at the tee rest position (the camera's resting-ball calibration). Smash factor inside bounds per club class. Ball start direction inside the D-plane cone between face and path. Early IWR points consistent with a flight model with Bearman-Harvey/Smits-Smith coefficients (only matters beyond ~1-2 m).
- **Keep physics priors out of scored outputs.** Run each metric twice, *with* and *without* each physics factor. Report the unconstrained estimate as the measurement and the constrained one as the model-derived estimate. A large difference becomes a diagnostic flag. This follows the plan's rule that D-plane output is model-derived and has distinct provenance.
- **Built-in cross-sensor self-diagnostics.** Compute each check on every shot, store it in the session bundle, and use it to select samples for the flywheel (Q5):
  1. OPS radial speed vs IWR speed projected onto the OPS line of sight (geometry from the rig file).
  2. Ball launch azimuth/elevation from camera bearings plus radar range vs from IWR angles alone. A persistent mean difference is an extrinsic error (the ~5° azimuth); per-shot scatter is noise.
  3. Contact-time agreement between sensors (Q1) after the unit offsets are applied.
  4. Smash factor inside club-class bounds.
  5. D-plane consistency: horizontal launch should sit between face and path, nearer the face.
  6. ω = v/r consistency of the club fit.
  7. Ball rest position vs the rig/tee prior, and inclinometer tilt vs the rig file (tilt double-count guard).
  8. Reprojection residual RMS and IWR track residuals as fit-quality scalars.
- A Kalman filter only makes sense for *live* tracking, i.e. guiding the IWR tracker from the OPS speed. For metrics, a batch smoother over the whole window uses future samples too, and strictly dominates it.
- **[unsourced, well known] Smash-factor bounds.** The USGA/R&A driver COR limit of 0.830 caps ideal centred driver smash at about 1.50, and irons run lower (roughly 1.3-1.4 for mid-irons). Verify against an official source before hard-coding. Better still, fit per-club bounds from the project's own July TM baseline.

### Gaps
- Full text of the 2024 Sports Engineering 3-D impact paper (authors, validation data, accuracy) was not accessible. Worth obtaining, since it covers irons, which is OpenFlight's current club type.
- No published sports-tech description of a joint radar-plus-camera club/ball factor graph was found. Commercial systems (Trackman, FlightScope) do not publish their fusion internals beyond patents.
- Quantitative D-plane start-direction percentages come only from secondary coaching sources.

---

## Q3. Self-calibration / in-situ registration of radar-camera extrinsics (the ~5° azimuth offset)

### Takeaway
The automotive targetless methods (common learned features, ego-motion B-splines) assume a moving platform or rich scenes, and neither fits a static launch monitor. OpenFlight has something better: **every shot is a moving point target seen by both sensors.** Accumulate ball, and later club, detections across shots and solve camera-in-radar rotation as a robust PnP problem, with the translation held at the measured rig value. Keep a running per-unit posterior on it, and treat IWR antenna phase/angle bias as a separate calibration that the registration cannot separate from mounting rotation.

### Cited Findings
- Radar-camera extrinsic methods divide into target-based (corner reflectors, special targets: accurate but impractical in the field) and targetless (natural-scene features). One online targetless method learns a feature common to raw radar Range-Doppler-Angle data and camera images, then estimates the extrinsic with RANSAC plus Levenberg-Marquardt. Code is public. — [arXiv 2309.00787](https://arxiv.org/abs/2309.00787), [code](https://github.com/radar-lab/Online-Targetless-Radar-Camera-Extrinsic-Calibration) [summary]
- A targetless spatiotemporal radar-camera calibration (TI AWR1843BOOST, IWR6843AOP, Flea3 cameras) reached translation within 1.6 cm per axis and rotation within 3° per axis of a target-based method on a handheld rig. The method needs rig motion: radar ego-velocity from Doppler to stationary landmarks, plus monocular SLAM. — [arXiv 2211.01871](https://arxiv.org/html/2211.01871v4) [primary]
- A 4D-radar-camera extrinsic tool exists (2025). — [arXiv 2507.19829](https://arxiv.org/pdf/2507.19829) [not read; listed for completeness]
- [project, from memory index] Earlier in-situ registration from live shots measured an elevation offset of +6.18° and an azimuth offset of +5.19° (IWR ~5° left of camera), not yet applied. TrackMan's alignment combines a tap-target on the camera image with in-situ registration from live tracks. The enclosure v3 geometry is measured to ~0.3 mm, and the August rig was not measured. These are project records, not external sources.

### Inferences
- **Why a targetless automotive method gives a <3° result that is still not good enough:** OpenFlight's ~5° offset has to be resolved to maybe 0.2-0.5° to support the path/launch-direction goals. Rotation is where most of the error goes, and the rotation error of those methods is about the same size as the offset being corrected.
- **Recommended registration: shot-accumulated PnP with robust weighting.**
  1. For each shot, pair IWR 3D ball positions p_r(t) (radar frame) with camera ball-centre pixels at the same time (after Q1 offsets). Interpolate the IWR track to camera exposure midpoints.
  2. With translation fixed from the measured rig (or given a tight prior), solve the 3-DOF rotation by minimising reprojection error, e.g. OpenCV `solvePnPRansac` with a fixed translation, or a small least-squares problem over SO(3). Many shots at different launch directions are needed. A single straight track leaves roll about the track direction poorly constrained, so draws/fades, low and high launches and club points help.
  3. Update a per-unit posterior after each session (mean, covariance, n shots) and store it in the calibration record. Do not auto-apply a new value until it has converged and passed a hold-out check, for example: the Q2 check #2 residual shrinks on held-out sessions.
- **Confounders to model separately:**
  - IWR angle-of-arrival bias from RX channel phase mismatch. [unsourced; TI mmWave SDK provides a range-bias and RX-channel-phase compensation procedure with a corner reflector; verify in TI docs.] A mounting rotation and an AoA bias look nearly identical over a small angular field. A one-time corner-reflector phase calibration per unit, or the TI factory calibration if present, should come first.
  - Camera intrinsics: the plan says they are uncalibrated, and crop/binning mapping matters. Calibrate them independently (checkerboard/ChArUco) *before* registration. Otherwise the focal-length error folds into rotation.
  - Handedness and frame conventions: the project memory records a left-handed world frame and a mirrored mesh. Unit tests on synthetic points (Q4) should lock the convention.
- **Resting ball:** the camera sees it, but the IWR MTI/static clutter removal hides it (per project memory). Use it for camera-to-tee geometry, not for radar-camera registration.
- **Club as a second target:** clubhead scatter centre ≠ image centroid ≠ face centre (the plan's Architecture decision 4). Use club points for registration only with a scatter-centre offset parameter, or leave them out.

### Gaps
- No golf-specific literature on in-situ radar-camera registration from ball tracks was found. The TrackMan approach is known only from patents (project memory, not re-verified here).
- IWR6843LEVM phase-calibration status (factory vs user) was not verified in this pass.

---

## Q4. Synthetic and semi-synthetic ground truth ("digital twin")

### Takeaway
Before real reference data exists, build a **physics-consistent synthetic shot generator** that runs through the *same replay path* as real captures. It samples club delivery, applies an impact model, flies the ball and emits OPS time series, IWR data and camera frames with a realistic timing and geometry perturbation model. Its first job is not training. It is (a) catching contract/sign/frame bugs, (b) measuring estimator bias and observability, and (c) producing sensitivity curves (metric error versus timing, extrinsic or intrinsic error). Treat it as unit/integration truth, never as accuracy evidence. Sim-to-real gaps in radar and imaging are well documented.

### Cited Findings
- RadarSimPy (Python/C++) simulates baseband data for arbitrary waveforms (CW, FMCW, PMCW, pulse) from point targets and 3-D mesh models, with MIMO arrays and CPU/GPU acceleration. Its micro-Doppler example simulates rotating targets through MTI, range-time and Doppler-time maps. — [radarsimpy GitHub](https://github.com/radarsimx/radarsimpy), [RadarSimX micro-Doppler example](https://radarsimx.com/2021/05/10/micro-doppler/) [primary docs]. Licence and cost of the mesh-model (ray-tracing) backend were not checked. The point-target path is enough for a golf ball.
- RadSimReal (CVPR 2024): a physical radar simulation produced annotated synthetic data, and detectors trained on it and tested on real data performed comparably to real-trained ones, and better across datasets. — [arXiv 2404.18150](https://arxiv.org/abs/2404.18150) [summary]
- Contrary evidence: simulated radar "rarely matches real sensor measurements at the signal or point-cloud level", with systematic differences in point density, noise statistics and coverage. — [Sim2Radar, arXiv 2602.13314](https://arxiv.org/html/2602.13314v3) [summary]. NVIDIA also publishes radar-model validation for DRIVE Sim: [NVIDIA blog](https://developer.nvidia.com/blog/validating-nvidia-drive-sim-radar-models) [not read].
- Rendered synthetic data for 6-DoF pose: BlenderProc produces BOP-format annotated renders. Domain randomization (poses, lighting, textures, *sensor* effects) narrows the gap. "Sensor-based domain randomization" specifically targets the reality gap for pose networks. — [Hugging Face CV course: BlenderProc](https://huggingface.co/learn/computer-vision-course/en/unit10/blenderProc), [arXiv 2011.08517](https://arxiv.org/pdf/2011.08517), [CAD2Real arXiv 2009.12312](https://arxiv.org/pdf/2009.12312) [summaries]. No golf-club-specific synthetic pose dataset was found.
- Physics of the generated shot: the ball flight coefficients (Bearman & Harvey; Smits & Smith) and the impact models (Penner; 2024 3-D driver/iron models) listed in Q2 give the causal chain from club state to launch to flight.
- Doppler spin signature model: sidebands at the spin frequency come from surface non-uniformities. — [US20140191896A1](https://patents.google.com/patent/US20140191896) [primary patent]

### Inferences
- **Recommended generator: three tiers, cheapest first, all on a laptop. The Pi only replays.**
  1. **Geometric/kinematic tier** (numpy, no rendering). Sample club speed, path, attack, face, dynamic loft, impact offset and club class. Map to ball launch with a D-plane plus COR/gear-effect-lite impact model (Penner-style), and fly the ball with the lift/drag model. Emit *ideal observations*: IWR range/az/el/radial velocity at the configured frame times, OPS radial speed at 30 ksps windows, and camera pixel coordinates of the ball centre, clubhead features and resting ball via the calibrated camera model. Then perturb with the *project's own measured error model*: contact-to-trigger offset drawn from 0.5-4 ms, ±1.5 ms jitter, ~5° az / ~6° el extrinsic error, intrinsics error, tilt, IWR angle noise and range quantisation, frame dead gap. This tier gives sensitivity curves and observability studies in seconds per 10k shots. The project's earlier Stage 0C/0D/0E simulation budgets (guide §1G-§1I) are this tier. Extend them with the new timing and registration models instead of starting over.
  2. **Signal tier** (RadarSimPy). Synthesize IWR ADC cubes with the *actual chirp config* (profile/frame from the firmware) for a ball, a point scatterer plus one or two off-centre scatterers rotating at the spin rate to create sidebands, and a clubhead as a small rigid set of scatterers on the swing arc. Synthesize the OPS as a CW I/Q stream. Run these through the production DSP (MTI notch, CFAR, tracker) to test tracker association, MTI-notch loss near the tee (project memory flags this), and whether spin sidebands are resolvable at the real dwell. It is CPU-heavy but fine offline. A GPU is optional.
  3. **Image tier** (Blender Cycles or BlenderProc). Render clubhead and ball on the *real background* at the OV9281's resolution/mode, mono, with the IR illumination direction. Include motion blur integrated over the real exposure time, the global-shutter/readout model, lens distortion from the calibrated intrinsics, sensor noise/gain, and a saturated resting ball (project memory: the resting ball is saturated). Use the official head-dimension sources only (project feedback rule: Maltby/OEM, no community meshes).
- **Semi-synthetic beats fully synthetic for the camera.** Composite rendered clubheads with known pose into *real* recorded background frames and real noise. Or take a real capture and apply known synthetic perturbations: inject a known time offset between sensors, rotate the radar frame by a known angle, shift the camera principal point. Then check that the estimator recovers the injected value. **Injection-recovery on real data** is the most defensible pre-reference test: it measures bias for exactly the parameters Q1 and Q3 estimate, and has no domain gap in the nuisance factors.
- **Metamorphic/invariance tests** (the plan's M2 already names some). Mirror a right-handed shot to left-handed and expect sign-flipped path/face. Shift all timestamps by a constant and expect unchanged metrics. Rotate the rig about the target line and expect a compensated result after the tilt input. These catch frame and sign bugs cheaply.
- **Domain-gap caveats specific to OpenFlight:**
  - Radar RCS fluctuation, multipath off the floor/mat and enclosure (the two-ray phase separation in project memory), and IWR antenna coupling are hard to simulate. Treat signal-tier results as upper bounds on performance.
  - Motion blur, specular highlights on chrome/satin heads and the saturated ball drive camera error. The project's measured "1 px mask error ≈ 10° face" sensitivity means rendered-image accuracy numbers are *not* transferable. Use the image tier for pipeline correctness and qualitative failure discovery only.
  - The impact model is itself approximate (roll vs grip debate). Any synthetic "truth" for spin and face-from-ball is model-derived.
- **How to use synthetic truth in the bench:** tag every synthetic session in the bundle as `synthetic` or `semi_synthetic` with generator version, seed and perturbation parameters. Keep synthetic sessions in the regression suite (determinism, sign conventions, fault injection) and **never** in accuracy/acceptance statistics.

### Gaps
- No published golf-specific radar or camera simulation used for launch-monitor validation was found. Commercial vendors do not publish their sim practices.
- RadarSimPy's licensing/free tier for the mesh ray-tracing backend and its ARM support were not verified.
- No public data on OV9281 noise/PRNU parameters were gathered. These should be measured from dark and flat frames on the unit.

---

## Q5. Data flywheel and continuous improvement with community testers

### Takeaway
The open-source robotics precedents converge on one pattern, and OpenFlight already has most of it: **replayable raw logs, deterministic replay through the production code, reference-output diffs in CI, and a shared place where users upload logs and get automatic reports.** What OpenFlight should add is the *selection loop* (a Tesla-style data engine): cross-sensor disagreement flags pick which shots get labels or reference captures. Add **stratified, versioned benchmarks** with a frozen hold-out, and **per-unit calibration records** so community data from different rigs is comparable.

### Cited Findings
- openpilot "process replay" replays historical logs through the individual processes and checks outputs against stored reference logs. CI replays driving segments for each PR and compares behaviour to master, and reference outputs are updated through explicit PRs. — [openpilot PR #39004 "process replay: push new refs"](https://github.com/commaai/openpilot/pull/39004), [PR #33989 "faster process_replay"](https://github.com/commaai/openpilot/pull/33989), [PR #39000 "new route"](https://github.com/commaai/openpilot/pull/39000) [primary PRs; README fetch returned 404 in this pass, details from search summaries]
- ArduPilot Replay runs a recorded dataflash log through any branch's EKF code. Logs must be recorded with `LOG_REPLAY=1` (and preferably `LOG_DISARMED=1`), and the log structure must match between firmware and branch. `Tools/Replay/check_replay_branch.py` generates a log on master, replays it on the modified branch and confirms "the EKF output has not changed". It is meant for refactors and non-functional changes. — [ArduPilot dev docs: Testing with Replay](https://ardupilot.org/dev/docs/testing-with-replay.html) [primary]
- ArduPilot's AutoTest suite, built on SITL, runs repeatable regression tests on most master commits. — [ArduPilot Autotest Framework](https://ardupilot.org/dev/docs/the-ardupilot-autotest-framework.html) [summary]
- PX4 Flight Review is a web service where users upload ULog files and share auto-generated analysis reports. PlotJuggler and Foxglove are used for deeper replay/inspection. — [PX4 Flight Log Analysis](https://docs.px4.io/main/en/log/flight_log_analysis), [review.px4.io](https://review.px4.io/) [primary docs]
- Tesla's "Data Engine" (Karpathy, 2019): deployment errors (disengagements, interventions) flag problems, engineers write "triggers" to pull similar examples from the fleet, humans label them and the model retrains, in a loop. — [Isaac Kargar summary](https://kargarisaac.medium.com/active-learning-data-selection-data-auto-labeling-and-simulation-in-autonomous-driving-part-4-dc985e2c83f9), [gradientdescent thread](https://gradientdescent.co/t/the-tesla-ai-team-s-data-engine/48) [secondary; original talk not re-watched]. The Mcity Data Engine paper describes an open iterative data-selection loop: [arXiv 2504.21614](https://arxiv.org/pdf/2504.21614) [summary].
- [project] OpenFlight already has deterministic replay through production estimators, immutable hashed session bundles, per-attempt status review, a rig fingerprint in session metadata (M0 done), and a plan requirement to freeze acceptance limits *before* collecting acceptance data. The plan also requires scoring all attempts including no-reads, holding out complete sessions (another day, eventually another rig), and separating regression data from acceptance data. — fusion-master-plan.md ("Metrics and acceptance policy", M0, M2).

### Inferences
- **Map the precedents onto OpenFlight concretely:**
  1. *openpilot process replay → "estimator replay refs".* For a pinned set of regression sessions, store the per-shot outputs (all metrics plus diagnostics plus provenance) as reference JSON with the bundle hash and estimator version. CI fails on any unexplained diff. A PR that intends to change outputs must update refs explicitly and attach a diff report (per-metric bias/MAE shift, shots that changed status).
  2. *ArduPilot check_replay_branch → "no-functional-change" mode.* A one-command check that a refactor leaves outputs bit-identical, or equal within stated float tolerances. This matches the plan's live/replay equivalence requirement.
  3. *PX4 Flight Review → tester upload with an automatic report.* Testers upload a session bundle and get back a static HTML report: per-shot metrics, the Q2 consistency flags, the Q1 timing-offset histogram and the Q3 registration residual. This makes bad rigs and bad setups visible to the tester and gives maintainers triage data. It fits the tester ease-of-use feedback in project memory (per-shot verdicts).
  4. *Tesla data engine → disagreement-triggered selection.* The "disengagement" equivalent is a **cross-sensor disagreement or physics-gate violation** (Q2 checks 1-8). Rank incoming community shots by disagreement, rarity of stratum and estimator uncertainty. Route the top of the list to (a) human labelling (impact location, contact frame, face/leading-edge marks) and (b) requests for reference captures (a TM/Mevo-paired session in that stratum).
- **Stratified benchmark design.** Report every metric per stratum, with bootstrap CIs on bias, MAE, P90 error and read rate:
  - club class (driver / wood / iron / wedge);
  - club-speed band;
  - handedness;
  - strike zone (heel / centre / toe; high / low);
  - lighting (lux band from logged exposure/gain; project memory: pool testers by lux);
  - camera mode;
  - rig/unit ID and enclosure version;
  - surface (mat / turf);
  - tester.

  Watch the *minimum* stratum, not the pooled mean. Keep a **frozen hold-out** of whole sessions per the plan, and add new hold-out sessions only with a version bump.
- **Versioned datasets.** A dataset version is a manifest: a list of bundle hashes plus their labels-file hashes, estimator refs and split assignment (regression / dev / hold-out / acceptance). Plain JSON in git is enough. DVC or Hugging Face datasets are optional for the blobs. Never edit a bundle. Labels live in separate versioned files that point at bundle hashes and record the labeller and method. The plan's distinction between same-image hand annotations (consistency) and calibrated physical strike labels (accuracy) should be a field in the label schema.
- **Labelling workflow for impact location and face angle (cheap, hobby-feasible):**
  - Physical impact labels: face spray or impact tape photographed rectified after each shot, with a face-fixed scale and fiducials. That gives an independent mm-scale label with its own measured uncertainty. Image marks are consistency labels only.
  - Contact-frame labels: two labellers per shot on a subset to measure inter-rater agreement. Adjudicate disagreements, and treat inter-rater spread as the label noise floor.
  - Face angle: no cheap independent truth exists without a reference monitor. Label it "model-derived (D-plane)" until TM/Mevo-paired data arrive, consistent with the plan's M4 gate.
- **Per-unit calibration record** (one JSON per unit, versioned, hashed into each session):
  - rig geometry fingerprint;
  - intrinsics with RMS reprojection error and date;
  - IWR phase calibration;
  - camera-radar rotation posterior (mean, covariance, n shots, sessions used);
  - per-sensor timing offsets (median/MAD per mode);
  - mic trigger-latency distribution;
  - inclinometer zero;
  - which validation bench checks passed, and when.

  Sessions from units without a converged record go to regression or triage, never into accuracy statistics.
- **Community-data hygiene.** Tester sessions are *not* truth. Use them for availability, robustness, stratum coverage and disagreement mining. Accuracy claims come only from reference-paired sessions (a borrowed Trackman/Mevo, or physical labels), per the plan's pairing rules (physical shot identity, auditable review, keep bad matches).

### Gaps
- The openpilot process_replay README could not be fetched (404 at the path tried). The exact tolerance handling and ref-storage mechanics come from PR titles and search summaries.
- No public example was found of a consumer launch-monitor project running community data collection for accuracy improvement.
- How comma.ai and PX4 handle consent/licensing for community logs was not researched. It is relevant before OpenFlight hosts tester data publicly.

---

## Concrete recommendations (ordered by leverage vs cost)
These synthesize the sections above. Each item is an inference unless a citation is shown in the section it comes from.

1. **Per-sensor ball-derived contact time, offsets as per-unit calibration** (Q1). Nearly free, sub-ms, and removes the dominant ±1-2° attack/path error. Validate once with an LED-on-GATE plus a tee contact/break-beam sensor, which is the plan's M1 hardware gate.
2. **Per-shot batch fit with time offsets and extrinsic rotation as parameters, physics as switchable soft factors** (Q2). Report unconstrained and constrained variants separately.
3. **Shot-accumulated PnP registration** after independent intrinsic and IWR phase calibration (Q3). Keep a posterior per unit and apply only after a hold-out check.
4. **Injection-recovery on real captures plus a kinematic-tier synthetic generator in the replay path** (Q4). Leave signal and image tiers until a specific question needs them.
5. **Replay refs in CI (openpilot style), a no-functional-change check (ArduPilot style), tester upload with an auto report (PX4 style), and disagreement-ranked labelling/reference requests (data-engine style)** (Q5). Use stratified, versioned, hold-out-protected benchmarks and a per-unit calibration record.

Hardware and computing cost flags:
- PTP, GPS PPS and CM5 PHY timestamping are unnecessary.
- LED, piezo/break-beam and impact tape cost under $30.
- RadarSimPy signal-tier and Blender image-tier generation need a desktop, and optionally a GPU. The Pi only runs replay.
- The only expensive item is borrowed reference-monitor sessions (TM4/Mevo Gen 2). They are the sole route to accuracy claims for face, path and spin.
