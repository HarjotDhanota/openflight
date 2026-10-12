# Turn OpenFlight's test bench into a metrology lab

OpenFlight's bench tooling is already good. Deterministic replay, SHA-256 session bundles and a per-attempt review ledger match the core practice of autonomous-vehicle "resim" and openpilot's process replay. Tooling is therefore not what limits accuracy now. Three other things do. **First, the bench has no references with known uncertainty.** TrackMan publishes no accuracy tolerances for any metric, and its own driver ball speed varies shot to shot by an SEM of 2.5 to 4.4 mph, so "error vs TrackMan" mixes both devices' noise. **Second, events are timed badly.** The sound trigger's ±1.5 ms jitter alone moves attack angle by about ±1.3 to 2.2° and path by about ±0.8 to 1.3°. **Third, each sensor has a small number of physics-level errors that can be fixed.** Examples are the IWR6843's time-division (TDM) motion phase, the OPS243-A's unlocked carrier and the camera's 12 mm motion blur. The practices worth adopting come from three places. From metrology: a GUM-style uncertainty budget per metric, Bland-Altman limits of agreement with pre-registered limits, and gauge R&R run on physical artifacts rather than on golf shots. From automotive and CI teams: condition catalogues, a registry of triggering conditions, and paired regression gates on frozen data. From police-radar and sensor-production practice: cheap check artifacts (tuning forks, corner reflectors, accelerometer tumble tests, printed fiducials) re-measured every session. Most of the gain on each component comes from software, placement or parts under $100. The three hardware changes worth the effort are an 850 nm strobe, a higher IWR mount (or absorber on the floor in front of it), and a second, side-on camera used as a truth rig. One boundary to respect: no commercial vendor measures face angle from a single camera behind the ball without markers. OpenFlight should derive face angle from the D-plane and label it model-derived.

## TrackMan is a noisy comparator with no published tolerances

The first change is conceptual. Treat every OpenFlight metric as a measurement with its own uncertainty budget, and treat the comparator as another noisy instrument, not as truth. TrackMan's tech-spec page lists the parameters it measures but gives **no accuracy tolerance for any of them** ([TrackMan tech specs](https://www.trackman.com/golf/launch-monitors/tech-specs)). Its reliability is also far from perfect. In Bishop et al.'s two-session study of high-level golfers, TrackMan 4 club speed had ICC 0.99 (SEM ≈ 1.65 mph) and ball speed ICC 0.97 to 0.99 (SEM 2.46 to 4.42 mph), but **spin rate managed only ICC 0.02 to 0.60** and differed significantly between sessions ([Bishop et al., J Sports Sci](https://www.tandfonline.com/doi/full/10.1080/02640414.2024.2314864)). Those figures mix golfer variability with instrument noise, so they cannot serve directly as TrackMan's instrument uncertainty. They do show that the reference noise is the same size as the tolerances OpenFlight would want to claim. If the two instruments' errors are independent, the variance of the differences is the sum of both variances. Any spread measured against TrackMan therefore overstates OpenFlight's own error.

Metrology already handles this. The GUM separates Type A uncertainty (statistics of repeated readings) from Type B (specifications, resolution, drift, calibration certificates). It combines them with sensitivity coefficients into a combined standard uncertainty and reports an expanded uncertainty U = k·u_c, with k ≈ 2 for about 95% coverage ([JCGM 100:2008](https://ncc.nesdis.noaa.gov/documents/documentation/JCGM_100_2008_E.pdf)). OpenFlight should keep a machine-readable budget for each reported metric and show U next to every number in the review UI.

- **Ball speed budget:** OPS carrier tolerance, FFT bin and interpolator bias, cosine error from the unknown line-of-sight angle, and choice of timing window.
- **Vertical launch budget:** IWR angle noise, LIS3DH mount tilt, and the radar-camera registration offset.

The budget's main value is diagnostic: whichever input dominates is where engineering effort should go. Calibration practice adds a decision rule. ANSI/NCSL Z540.3 defines the test uncertainty ratio (TUR) and accepts TUR ≥ 4:1 as a fallback when false-accept risk cannot be computed. It also guard-bands acceptance limits inside the tolerance to hold false-accept probability at or below 2% ([Morehouse on Z540.3](https://mhforce.com/why-a-41-t-u-r-is-not-enough-the-importance-of-analyzing-the-probability-of-false-accept-risk/)). Against a comparator with unknown tolerances, OpenFlight has no documented TUR for any metric. Spin, attack angle and dynamic loft are where TrackMan and Mevo+ disagree most, and for those a tight tolerance may be impossible to validate against TrackMan at all.

The statistics should report absolute agreement, not correlation. Ordinary least squares (OLS) assumes the reference has no error. Deming regression allows error in both instruments but needs the ratio of their error variances. Assuming that ratio is 1 "on a narrow range … reintroduces the very bias Deming exists to remove", and a single club's ball-speed band is exactly such a narrow range. Passing-Bablok is the outlier-resistant, assumption-light alternative ([Analyse-it](https://analyse-it.com/learn/choosing-a-regression-for-method-comparison)). Bland and Altman's 1999 extensions cover three situations OpenFlight will meet: differences that grow with magnitude, replicates, and comparing each instrument's repeatability with the limits of agreement ([Bland & Altman 1999](https://journals.sagepub.com/doi/10.1177/096228029900800204)). Their later paper shows that treating repeated shots from the same golfer as independent pairs **makes the limits of agreement too narrow** ([Bland & Altman, multiple observations](https://eprints.whiterose.ac.uk/id/eprint/3411/1/Bland_2006_Agreement.pdf)). Ten shots from each of 29 golfers is not 290 independent pairs.

ICC should be secondary. It comes in ten forms that must be named ([Koo & Li 2016](https://pmc.ncbi.nlm.nih.gov/articles/PMC4913118/)), and it rewards heterogeneous samples, which is why TrackMan's spin ICC collapses when all the drives spin alike. Lu et al. give the protocol that turns "frozen criteria" into a pass/fail test. State an agreement limit Δ for each metric in advance. Conclude agreement only if the **confidence interval of both limits of agreement** lies inside ±Δ. Size n for about 80% power ([Lu et al. 2016](https://www.degruyterbrill.com/document/doi/10.1515/ijb-2015-0039/html?lang=en)). OpenFlight's bench already freezes criteria and bootstraps by session. What it lacks is the numerical Δ, the power calculation, and the rule that success means the confidence interval of the limits of agreement, not the limits themselves.

The golf literature supplies both a realistic target and a list of traps. Only one peer-reviewed study used a true criterion reference. Leach et al. filmed 240 shots with four 5400 Hz cameras processed in GOM Inspect, with a stated reference uncertainty of **0.06 mph for ball speed, 0.01° for launch angles and 14 rpm for spin**. They found ball parameters trustworthy and club parameters in need of caution ([Roberts project page](https://jonroberts.info/?page_id=110)). Every study since has used TrackMan 4 as the criterion. The one study read in full, Bliss and Langdown's comparison of Mevo+ with TrackMan 4, shows what a Mevo-class radar achieves and how much data is lost in pairing. Only 58 of 118 driver shots and 69 of 174 wedge shots survived exclusions: TrackMan lacked club data on 60 wedge shots, and many shots carried "estimated" spin. Five 7-iron shots flagged as *measured* read 11,000 to 13,500 rpm, **about double the true spin, from seam-harmonic doubling** ([Bliss & Langdown 2026](https://www.ebi.ac.uk/europepmc/webservices/rest/PMC13227226/fullTextXML)). That is the same failure a CW Doppler spin estimator such as OpenFlight's OPS path can produce. It argues for scoring the gross-error rate separately from RMS error.

| Metric (driver) | Mevo+ vs TM4, bias ± SD | Proposed OpenFlight first gate |
|---|---|---|
| Ball speed | −1.49 ± 0.82 mph | SD ≤ 1 mph, \|bias\| ≤ 1.5 mph |
| Launch angle | −0.75 ± 0.84° | SD ≤ 1°, \|bias\| ≤ 1° |
| Launch direction | −0.30 ± 1.99° | SD ≤ 2° |
| Club speed | −0.40 ± 1.00 mph | SD ≤ 1.5 mph |
| Club path | −1.50 ± 2.35° | SD ≤ 2.5° |
| Attack angle | 1.20 ± 0.96° | SD ≤ 1.5° (Mevo+ fails on irons: 7-iron ICC 0.02) |
| Spin rate | −55 ± 513 rpm | SD ≤ 500 rpm, gross-error rate < 2% (later gate) |

The comparator bands come from a single golfer ([Bliss & Langdown 2026](https://www.ebi.ac.uk/europepmc/webservices/rest/PMC13227226/fullTextXML)). Treat them as a starting anchor, not a consensus standard, because no such standard exists. Every metric also needs a coverage floor, for example 90%, so that withholding hard shots cannot buy accuracy.

Some of the disagreement between devices is definitional rather than noise. TrackMan measures club speed, path and attack angle at the head's geometric centre, within about 6 mm of the centre of gravity, at maximum compression. Face-centre definitions, which optical monitors use, read **about 3° more outside-in on path and about 1° more upward on attack** for a driver. TrackMan takes face angle at the impact location, where 10 mm of toe impact reads about 2° more open ([TrackMan club data definitions](https://www.trackman.com/blog/club-data-definitions)). OpenFlight must therefore declare the measurement point and the moment for each club metric. It should apply a documented offset to the centre of gravity before comparing with TrackMan, and publish the uncorrected numbers alongside.

The remaining protocol rules follow from what the studies got wrong:

- **Pair by timestamp, not order.** Every study paired shots by export order. One dropped shot then shifts every later pair. Use the Pi's timestamps plus a clock offset found by cross-correlating the two ball-speed series.
- **Exclude estimated values up front.** Drop comparator shots that are "estimated" for the metric in question, and pre-register that rule.
- **Count withholding against coverage.** Never exclude shots where OpenFlight withheld a value; count them against its coverage instead.
- **Label every metric by the kind of evidence behind it.** Use "validated vs physical reference", "agreement vs comparator" or "self-consistency only". Ball speed, club speed, smash and vertical launch keep their validated status from the July TrackMan baseline.

TrackMan 4 remains the primary comparator. If Mevo Gen 2 or Full Swing KIT joins as a second simultaneous comparator, a three-instrument design can in principle separate each device's error variance without a gold standard. That estimator comes from time-frequency metrology and was not sourced in this research, so confirm it before relying on it. OpenFlight would also be producing the first published agreement data for either device.

## Cheap artifacts supply the repeatable parts a golf shot cannot

Gauge R&R needs "parts" that can be measured repeatedly, and a golf shot happens once. The AIAG acceptance rules are strict. %GRR below 10% is acceptable, 10 to 30% is conditional, and 30% or more is rejected. The number of distinct categories must also reach at least 5 ([AIAG MSA summary](https://app.qualityengineer.ai/blog/gauge-rr-acceptance-criteria)). Sensor vendors meet this by calibrating against artifacts with traceable values and re-checking them on a schedule.

Police practice is the closest template. The operator checks the radar with a calibrated tuning fork held in front of the antenna. A 1,669.54 Hz fork, for example, reads as 50 mph at 10.525 GHz ([NIST, Calibration of Police Radar](https://tf.nist.gov/general/pdf/87.pdf)). The fork test runs at the start of each shift, internal self-tests run every 5 to 15 minutes, and a certified shop recalibrates the radar and the forks once or twice a year ([CopRadar](https://copradar.com/chapts/chapt3/ch3d4.html)). Accelerometers are calibrated with a static tumble through six or more orientations, which fits bias, scale factor and axis misalignment against gravity ([VectorNav](https://www.vectornav.com/resources/inertial-navigation-primer/specifications--and--error-budgets/specs-imucal); [PMC6718990](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC6718990/)). Automotive radar end-of-line testing uses corner reflectors and radar target simulators inside compact antenna test ranges ([dSPACE EOL CATR](https://www.dspace.com/en/pub/home/products/hw/darts/eol_catr_system.cfm)). Those are expensive, but a rotating constant-RCS reflector is the patented cheap way to produce a precisely controlled Doppler ([US 4972192](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/4972192)). The golf industry has its own artifact as well: a patented calibrator mounts marked balls at a known radius on a motor-driven wheel, so a launch monitor sees a known speed and a known spin. It was built because portable monitors drift out of calibration when moved ([US20070135223A1](https://patents.google.com/patent/US20070135223A1/en)).

The bench should assemble a ladder of such artifacts. Each isolates one sensor's bias before any comparator session, and each becomes a replayable, hash-verified bundle, so code changes can be re-scored against physical truth.

| Artifact | Ground truth it provides | Sensor | Approx. cost | Caveat |
|---|---|---|---|---|
| Certified K-band fork, read in raw-Hz (`UH`) mode | Doppler-to-speed scale; carrier offset | OPS243-A | Tens of dollars (unverified) | Only reaches about 65 mph; forks assume 24.15 GHz |
| Trihedral corner reflector on a stepper turntable at 1–2 m, radome fitted | Per-angle complex steering table, range bias, radar-camera azimuth if it carries a printed tag | IWR6843 | < $50 plus printed parts | Static target, so it cannot test TDM compensation; put foam under it |
| Belt or rail carriage with a reflector | Radial vs total speed at known aspect (the cosine correction) | Both radars | Medium | A few m/s only |
| Motor-spun disc or arm with a reflector at radius r, plus an optical tachometer | Ball-speed Doppler (r = 0.25 m at 3,000 rpm ≈ 78 m/s) | OPS243-A | Medium | **Stored-energy hazard**: needs a guard and a balanced hub |
| Golf ball spun on a spindle, marked and unmarked, ideally translating | Known spin rate vs sideband spacing; seam 2× test | OPS, IWR, camera | Low | A stationary ball sits in the DC/MTI notch |
| Ball drop (v = √(2gh)) and pendulum | Absolute direction, speed and time; camera frame timing and pixel scale | IWR, camera | Near zero | Low speed; floor multipath at its worst |
| Six-position LIS3DH tumble | Bias, scale, misalignment | LIS3DH | Free | Re-run on a schedule |
| Printed ChArUco/AprilTag on the mat or enclosure | Intrinsics drift, camera pitch/roll, ground plane per session | Camera | ~$5 | Must stay rigid and flat |
| LED on the trigger edge in view, plus a piezo or break-beam at the tee | Independent contact time on the Pi clock | Trigger, camera, radars | < $20 | Closes the timing loop without circularity |
| Static face jig (known 0, ±2, ±5° face) and impact tape photographed against the grooves | Face-angle estimator at rest; impact location | Camera | ~$20 | Tape can alter launch; spray gives blurry marks |

The fork needs care in use. OmniPreSense's own application note calls a certified fork "the simplest means of validating the speed accuracy" of the OPS243-A. It reports a 35 mph fork reading 35.27 to 35.34 mph (within 0.9%) and a 2,048 Hz fork peaking at 2,056 Hz ([OmniPreSense AN-026](https://omnipresense.com/wp-content/uploads/2025/03/AN-026-A_Speed-Accuracy-Using-Tuning-Fork-1.pdf)). The same note is internally inconsistent: its prose gives "1024 Hz → 28.5 mph" and a "65-mph fork (2,338 Hz)", but its own formula v = f·c/(2f₀) gives 14.2 mph for 1,024 Hz. CopRadar's 4,671.9 Hz for 65 mph agrees with the formula ([CopRadar](https://copradar.com/chapts/chapt3/ch3d4.html)). Compute expected readings from the formula.

The Doppler relation is linear in carrier frequency, so a low-speed fork validates the scale at every speed. It does not validate the processing chain at 10 to 13 kHz of ball Doppler. That is the job of the spun reflector.

Three disciplines turn these artifacts into a measurement system:

- **Pre/post-session checks.** Run each artifact before and after every session, as police do per shift.
- **Control charts.** Plot each artifact reading over time on a Shewhart or EWMA chart with limits set from its first readings. That is MSA "stability". A session whose artifact drifted out of limits is barred from the sealed accuracy tier.
- **Gauge R&R on artifact settings, not shots.** Use five to ten rotor speeds or reflector positions, with rigs or testers as "operators" and two or three trials each. This answers "can the instrument tell speeds 1 mph apart?" without golfer variability in the way.

For attack angle, face and path, no cheap artifact reaches a true criterion. A swing robot is the vendor-grade answer, and it is expensive. A pendulum club or swing arm is the hobby substitute: it tests the tracker, not biomechanics.

The camera can be its own reference for ball launch and spin, provided it is validated first on the spin and drop fixtures. It works on the same principle as Leach's criterion system, using a marked ball, short illuminated exposures and calibrated pose ([Roberts project page](https://jonroberts.info/?page_id=110)), with fewer cameras and lower frame rates. Radar still owns ball metrics in the product. The camera-derived ball values are a bench and diagnostic reference, not a shipped output.

## Timing and event definition dominate club-metric error

On a single Pi 5 host, clock error is negligible. What matters is how each sensor defines the physical event. The Pi 5 routes GPIO through the external RP1 chip, which has no hardware edge timestamping ([Raspberry Pi Forums](https://forums.raspberrypi.com/viewtopic.php?p=2378082)). Even so, a careful measurement found GPIO interrupt timestamps biased by only about 11.7 µs. That fell to about 5.2 µs after disabling L1 ASPM and pinning the CPU clock ([SatPulse 2026](https://satpulse.net/2026/09/06/measuring-systematic-pps-bias-on-the-raspberry-pi-5.html)). Hardware PTP on the Pi 5 Ethernet has been reported broken ([raspberrypi/linux #5904](https://github.com/raspberrypi/linux/issues/5904)), and with one host it is irrelevant anyway.

Compare the other time scales:

- oscillator drift of 100 ppm over a 200 ms capture: 20 µs
- OPS sample spacing: 33 µs
- the measured spread from contact to sound trigger: 0.5 to 4 ms, with ±1.5 ms jitter

**PTP, GPS PPS and hardware timestamping are wasted effort. The work is in defining the event.**

The error chain is simple to derive. Near impact the clubhead's velocity rotates at ω ≈ v/r, about 1.0 to 1.7°/ms for 35 to 45 m/s on a 1.5 to 2 m arc. With the project's measured swing plane of about 59°, that splits into roughly 0.9 to 1.5°/ms of attack angle and 0.5 to 0.9°/ms of path. **A ±1.5 ms contact-time error is therefore worth ±1.3 to 2.2° of attack angle and ±0.8 to 1.3° of path.** That is larger than most geometric error terms, and it matches the project's observation that attack angle moves about 3° per camera frame. Sound itself costs 2.9 ms per metre of path, and a microphone's threshold crossing depends on how loud the strike is, so latency varies with strike quality. **Keep the SEN-14262 as a trigger and never use it as a timestamp.** Measure its latency against strike amplitude once on the bench, using the tee contact sensor, and store that distribution in the per-unit calibration record.

The better timestamp comes from the ball, and every sensor sees the ball leave. Each sensor can estimate contact on its own clock:

- **Camera:** the first frame showing ball motion, minus displacement divided by the radar ball speed. Reference to the exposure midpoint. 5 mm of position error at 60 m/s is only 0.08 ms.
- **IWR:** fit the range track r(t) = r_tee + v·(t − t_c) and solve for t_c. 1 cm of range error is 0.17 ms. Prefer this in-data t_c over host arrival times through the CLI.
- **OPS:** detect the onset of the ball's Doppler line with a short STFT. The result is sample-exact relative to the `HOST_INT` trigger index. TrackMan reportedly samples at 40,000 samples per second per receiver precisely to pin the time of impact ([TrackMan guide](https://www.trackman.com/blog/the-ultimate-guide-to-understanding-trackman)); that figure was not verified directly.

Store the per-shot offsets between sensors as a per-unit calibration (median and MAD) and flag outliers. Then close the loop with independent hardware. The project already found that its earlier "ball clock" validation was circular. An LED driven from the trigger edge and visible to the camera, plus a piezo or break-beam at the tee sampled on a Pi GPIO, together cost under $20. They give an external truth for camera-to-Pi timing and for physics-to-Pi timing.

Visual-inertial odometry offers a model for the residual offset. Qin and Shen estimate the camera-IMU time offset inside the optimisation by shifting each observation along its image velocity ([arXiv 1808.00692](https://arxiv.org/pdf/1808.00692)). The clubhead's large image velocity makes a per-shot offset well observed in the same way. The right estimator is a per-shot batch least-squares fit with robust losses, not a Kalman filter. Inputs:

- camera reprojection
- IWR range, angles and radial velocity
- OPS *radial* speed
- the acoustic edge as a weak prior on contact time

Parameters to estimate:

- one rigid club motion (a constant-curvature arc plus one rotation rate)
- the ball launch state
- contact time
- per-sensor offsets
- the camera-radar rotation

A joint radar-camera batch optimisation of this kind already works on IWR6843-class radars ([arXiv 2211.01871](https://arxiv.org/html/2211.01871v4)), and the problem is small enough to solve on a Pi 5 in well under 100 ms. Physics relations should enter as switchable soft factors: smash bounds, a D-plane cone, and a flight model built on Bearman-Harvey lift and drag ([aerodynamic drag study](https://www.researchgate.net/publication/251716774_A_study_of_golf_ball_aerodynamic_drag)). Report the unconstrained fit as the measurement and the constrained fit as the model-derived value. A large gap between them becomes a diagnostic, and no metric ends up scored against a value the solver forced on it.

Every shot should also carry the same set of cross-sensor self-checks in its bundle, for use in triage and later data selection:

- OPS speed vs IWR speed projected onto the OPS line of sight
- launch angles from camera bearings plus radar range vs IWR angles alone
- agreement between the sensors' contact times
- smash factor within bounds for the club class
- horizontal launch between face and path, nearer the face
- consistency of ω = v/r in the club fit
- resting-ball position vs the rig prior
- LIS3DH tilt vs the rig file

Registration needs its own method. Automotive targetless radar-camera calibration reaches only about **3° per axis in rotation** ([arXiv 2211.01871](https://arxiv.org/html/2211.01871v4)). That is the same size as the ~5° azimuth and ~6° elevation offsets OpenFlight needs to resolve to a few tenths of a degree. OpenFlight has a better signal: every shot is a moving point target seen by both sensors. Accumulate ball detections across shots of varied launch direction. Solve the 3-DOF rotation with translation fixed at the measured rig value. Keep a posterior per unit, and apply a new value only after it improves held-out sessions.

Two confounders must be removed first, or they fold into the rotation. The first is IWR antenna phase bias. TI's corner-reflector procedure (`measureRangeBiasAndRxChanPhase`) outputs a range bias and a complex gain and phase per virtual antenna ([TI E2E](https://e2e.ti.com/support/sensors-group/sensors/f/sensors-forum/1095432/iwr6843aopevm-how-to-do-the-range-bias-and-rx-channel-gain-phase-measurement-and-compensation)). The second is camera intrinsics, calibrated on a ChArUco board.

Synthetic data should serve as unit and integration truth, never as accuracy evidence. A kinematic generator samples club delivery, applies a D-plane and COR impact model, flies the ball, and emits perturbed observations through the real replay path. That catches sign and frame bugs and produces sensitivity curves in seconds. RadarSimPy can synthesise CW and FMCW MIMO baseband data from point and mesh targets, including rotating micro-Doppler scatterers ([radarsimpy](https://github.com/radarsimx/radarsimpy)). Simulated radar "rarely matches real sensor measurements at the signal or point-cloud level" ([Sim2Radar](https://arxiv.org/html/2602.13314v3)), so treat its results as upper bounds. The most defensible test available before any reference data exists is **injection-recovery on real captures**. Inject a known time offset, a rotation of the radar frame or a shift of the camera principal point, and confirm that the estimator recovers it. That measures estimator bias with no domain gap.

## Each sensor has one dominant fixable error

### OPS243-A: carrier scale, interpolator bias and spin detection

Noise does not limit OPS speed. At 128 samples and 20 dB SNR, the single-tone Cramér-Rao bound is about 5 mm/s. The real error terms are three:

- **Carrier scale.** The -A variant is not PLL-locked. It defaults to 24.125 GHz anywhere in a 24.0 to 24.25 GHz band, whereas the OPS243-C locks "to within 1MHz" ([AN-026](https://omnipresense.com/wp-content/uploads/2025/03/AN-026-A_Speed-Accuracy-Using-Tuning-Fork-1.pdf)). Worst case, that is ±0.5% of scale, or about **±0.35 m/s (0.8 mph) at 70 m/s**. A fork read in `UH` mode measures it for tens of dollars.
- **Estimator bias.** The native bin is 234 Hz (about 1.46 m/s). Zero-padding to 4,096 points gives a finer grid but no new resolution. Bias-corrected three-bin interpolators (Candan, Quinn, Jacobsen) remove the residual in closed form, and comparisons favour Improved Quinn ([JUCS comparison](https://lib.jucs.org/article/156911)). Add 50 to 75% window overlap and a low-order drag fit of the ball ridge, evaluated at contact time.
- **Geometry.** A single CW channel sees only v·cos α. A second OPS at a known offset, or the IWR's angle, turns the cosine correction from an assumption into a measurement. Validate it first on the rail fixture.

Spin needs a different signal model, not just more SNR. TrackMan's patent computes spin from "equally spaced sidebands … spaced exactly at the spin frequency" by qualifying harmonic tracks, and it derives spin *axis* from the lift acceleration of the trajectory, not from micro-Doppler ([US8845442B2](https://patents.google.com/patent/US8845442B2/en)). FlightScope's patent describes three cues. The seam modulates the return at **twice** the spin rate: a 3,300 rpm ball oscillated at about 110 Hz. The ball acts as a dielectric lens that is "only active for … 10-25% of the overall spin period". And both phase and amplitude are modulated ([US20140191896A1](https://patents.google.com/patent/US20140191896)).

A pulse train with 10 to 25% duty spreads its energy across many harmonics, so harmonic-comb or cepstral detection should beat a single peak on an envelope spectrum. Demodulating the complex residual after removing the ball ridge adds the phase cue that the current amplitude-only multitaper path discards. A physical prior from loft and speed, or the D-plane spin estimate, should settle the 1×/2× ambiguity. The spun-ball fixture is where to prove each step. Two other points to test there: the OPS buffer (about 136 ms), not frame rate, limits dwell, and a radar-visible mark is the most plausible SNR lever, consistent with the earlier bench finding that a stickered ball worked while stationary.

### IWR6843: TDM motion phase, per-angle calibration and the floor image

The largest IWR error is not the angle estimator. It is the phase that motion adds between transmit slots. With M transmitters, each TX slot picks up an extra phase from Doppler that is indistinguishable from direction-of-arrival phase. Bechter et al. showed a target at 15° moving at 18 m/s estimated at 18.5° with conventional processing, **a 3.5° error that their compensation removed** ([Bechter et al. 2017](https://d-nb.info/1161008624/34)).

OpenFlight's constants make the problem much worse. At λ = 4.84 mm, a 135 µs three-TX loop gives an unambiguous velocity of only about 9 m/s. A 70 m/s ball therefore aliases many times over ([TI SWRA553A](https://www.ti.com/lit/pdf/swra553) gives v_max = λ/(4·N·T_c)). The sensitivity is about 2.1° of angle per 1 m/s of radial-velocity error at a 45 µs chirp offset, and 4.3° per m/s at 90 µs. The repo currently takes that velocity from the slope of a range track with 37.5 mm bins, so an error of a few degrees is the expected outcome. Before anything else, confirm which LEVM axis combines phases across TX slots. If it is horizontal, that alone plausibly explains why horizontal launch is withheld while vertical is accepted.

The fix follows the literature:

1. Estimate the loop-to-loop Doppler phase of the same TX, averaged over RX channels. That gives the velocity modulo the unambiguous interval with mm/s precision.
2. Pick the integer ambiguity from the coarse range rate, which needs only about ±3 to 4 m/s of accuracy.
3. Check the choice by maximum array coherence, or by phase continuity on a redundant virtual element.

The existing `measure_tdm_sign` code already computes most of this. Phase-based disambiguation methods can recover the unambiguous velocity of a single transmitter ([arXiv 2208.03231](https://arxiv.org/abs/2208.03231)).

Configuration helps too:

- **Faster chirps.** Shorter chirps (TI parts ramp at up to 100 MHz/µs with idle time as low as 2 µs) raise v_max and cut the phase per m/s by the same factor.
- **Fewer TX.** Fewer transmitters per loop help when only one axis needs inter-TX phase.
- **CW test mode.** The IWR's continuous-wave test mode would make the board a coherent four-receiver CW interferometer, the architecture TrackMan and FlightScope use. That lead is unverified.
- **AWR2944 with DDMA.** The AWR2944 EVM (about $786) may support Doppler-division MIMO, which removes TDM phase in silicon. That is also unverified ([DigiKey](https://www.digikey.com/en/products/detail/texas-instruments/AWR2944EVM/21298576)).
- **Not the IWR6843AOP.** The AOP board ($196) is not an accuracy upgrade.

Array calibration should happen in the enclosure, at 1 to 2 m, with the radome fitted, and across angle. The standard TI corner-reflector procedure outputs one boresight phase vector ([TI E2E](https://e2e.ti.com/support/sensors-group/sensors/f/sensors-forum/1098114/iwr1642boost-implementation-of-range-bias-and-rx-channel-gain-phase-measurement-and-compensation-with-raw-adc-data)). Coupling from the radome and enclosure varies with angle, so a turntable sweep producing a per-angle steering table is the right upgrade.

The floor image needs placement or a model, not more bandwidth. At a 51 mm radar height, a ball 0.3 m up at 1.5 m range produces a path difference of only about 2 cm. That is below the 3.75 cm range resolution at the maximum 4 GHz sweep, so range-domain separation ([Sensors 2020](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC7308873/)) cannot work. The options are:

- **Two-ray maximum-likelihood fit.** This is the classic low-angle answer; it exploits the symmetry of the direct and floor-reflected paths ([IEE Proc. F 1982](https://digital-library.theiet.org/doi/10.1049/ip-f-1.1982.0039)). Solve the floor reflection coefficient per session. Prefer this to MUSIC, which fails on coherent paths without spatial smoothing.
- **Raise the radar.** Separation scales with radar height.
- **Absorber or a non-specular mat** between the radar and the tee.

Club tracking has a separate time-base problem. At a 12 ms fallback frame period, a 40 m/s head moves about 0.5 m per frame. Requiring four frames of club presence asks for more dwell than a clubhead gives. Tracking the club on a chirp or loop time base within the burst is the likely fix, but check the actual frame period in the v3 headers first.

### OV9281 camera: light, calibration and the face-angle boundary

Every commercial camera system that reports face angle and impact location uses **multiple viewpoints, IR flash and, in most cases, fiducial stickers**. Foresight's patent specifies at least three retro-reflective face markers, calibrated stereo cameras and lights "arranged in a radial pattern around the lenses" ([US20130190098A1](https://patents.google.com/patent/US20130190098A1/en)). The GC3's single-marker mode gives speed, attack angle and path, but not face angle ([My Golf Simulator](https://mygolfsimulator.com/foresight-gc3-data/)). Uneekor shows club data "only … if both stickers are visible" ([Uneekor sticker guide](https://hubspot.carlofet.com/hubfs/Product%20Documents/Launch%20Monitor/Uneekor/Uneekor%20EYE%20XO%20Club%20Sticker%20Guide.pdf)).

The geometry explains why. Seen from behind, a 1° face yaw moves a feature laterally only by its lever arm along the line of sight times sin 1°: about 0.4 mm on an iron and 1.7 mm on a driver. That is consistent with the ±11° silhouette spread the project measured. Face angle should come from D-plane inversion, face ≈ (launch direction − (1−k)·path)/k. Launch direction lies about 61 to 76% of the way from path toward face ([Wood et al. 2018](https://www.researchgate.net/publication/323373897_The_Influence_of_Face_Angle_and_Club_Path_on_the_Resultant_Launch_Angle_of_a_Golf_Ball)). A 0.5° launch-direction error then becomes roughly 0.6 to 0.8° of face error, plus model error. Label the result model-derived.

The single hardware change with the most leverage is light. Blur is v × exposure. At 40 m/s, a 300 µs exposure blurs 12 mm, matching the measured ~10 mm; a 30 µs pulse blurs 1.2 mm. The OV9281 has STROBE and FSIN pins and built-in strobe control ([OV9281 brief](https://www.ovt.com/wp-content/uploads/2024/05/OV9281-PB-v1.4-WEB.pdf)). PiTrac already uses IR strobes on Pi global-shutter cameras ([PiTrac](https://hackaday.io/project/195042-pitrac-the-diy-golf-launch-monitor)).

A four- to eight-emitter 850 nm array is enough. An SFH 4715AS delivers 1330 mW/sr at 1.5 A and is rated for 3 A pulses of up to 800 µs at duty ≤ 0.005 ([SFH 4715AS datasheet](https://look.ams-osram.com/m/3a147ddac0391319/original/SFH-4715AS.pdf)). Add an 850 nm band-pass filter. Back-of-envelope, that gives about 5 to 10× today's ambient signal at the club, so a 30 µs pulse can match today's 300 µs exposure. The filter also removes mains flicker and the lighting differences between testers. Confirm the numbers with a bench exposure test, because the OV9281's absolute quantum efficiency at 850 nm was not found.

Eye safety needs a hardware guard. Under IEC 62471 as summarised by Lumileds, the exempt cornea IR limit is 100 W/m² ([Lumileds AB191-4](https://lumileds.com/AB191-4-LUXEON-IR-Family-Eye-Safety-Application-Brief)). Strobed at 0.6% duty, the array averages far below that. A bare chip stuck on at DC, however, can exceed the exempt retinal radiance at close range. Fit a monostable that caps pulse width regardless of GPIO state, add a diffuser, and get a formal assessment before any tester ships. This change reopens the earlier no-strobe decision. At minimum, the strobe belongs on the bench truth camera.

Calibration should stop relying on the ball. The repo infers focal length from the resting ball's apparent diameter. A ±1 px ambiguity on a 20 px ball is ±5% in focal length and range, and saturation biases the diameter upward. Calibrate each unit once at 1280×800 with a ChArUco board ([OpenCV](https://docs.opencv.org/4.13.0/da/d13/tutorial_aruco_calibration.html)) and derive the binned-mode intrinsics analytically. Verify each mode with a short board capture, since the 320×200 mode probably includes a crop offset. Lock the M12 focus thread and record a calibration hash per session.

Thermal drift is real. One study measured image drift of 8.2 px from warm-up and cut it to under 1 px with compensation ([Sensors 2022](https://pmc.ncbi.nlm.nih.gov/articles/PMC9786690/)). So warm up for 10 to 15 minutes before calibration and log temperature. A printed tag on the mat then gives per-session camera pitch, roll and ground plane with no manual input, and alarms on intrinsics drift.

The resting-ball detector's failures are mostly a priors problem. The hard-coded 9 to 30 px gate appears in four files. It breaks at 1:1, where a ball at 1 m is about 40 px. Four changes fix most of it:

- derive the gate from calibrated focal length and the radar impact range
- require the ball bottom to sit on the tag-derived mat plane
- require the ball to be stationary across the pre-trigger frames
- fit the circle to edge points while excluding saturated pixels

For club tracking, marker-based research shows what cheap fiducials achieve. Corke et al. tracked iron heads on a robot with **face-angle repeatability SD ≤ 0.2° and path ≤ 0.2°**. Those are precision figures, not accuracy against truth ([Corke et al. 2019](https://journals.sagepub.com/doi/abs/10.1177/1754337118792798)).

The bench truth rig should be a second, strobed, side-on OV9281 on the Pi 5's second camera port, with three or four retro-reflective dots on a bench club, calibrated jointly with the production camera. Add impact tape photographed against the grooves, and use BlenderProc renders with exact pose for algorithm development ([BlenderProc](https://www.semanticscholar.org/paper/BlenderProc:-Reducing-the-Reality-Gap-with-Denninger-Sundermeyer/87d2a16af38839f4970d5e75882ce293a3b45854)). Tape beats foot spray, which "gives you a blurry mark rather than a precise one" ([InkPact](https://inkpact.golf/blogs/news/the-best-golf-impact-tape-alternatives-and-why-spray-wins-every-time); a vendor with a bias).

### Sound trigger and LIS3DH: calibrate once, then verify every session

The SEN-14262's job is arming, and its failure modes belong in the bench's triggering-condition registry: false triggers from an adjacent bay, and latency that varies with strike loudness. Its latency distribution against the tee contact sensor is a per-unit calibration, not a constant. The OPS ball-onset time is the natural radar-native replacement for the microphone as a timing reference.

The LIS3DH needs a six-position tumble at commissioning, with bias, scale and misalignment stored in the rig file with a date ([VectorNav](https://www.vectornav.com/resources/inertial-navigation-primer/specifications--and--error-budgets/specs-imucal)). Repeat the tumble on a schedule to chart drift. Every shot should also carry a tilt-vs-rig-file check. That guards against the tilt double-count that the architecture audit found live in earlier builds.

| Priority | Change | Component | Cost | Metrics helped |
|---|---|---|---|---|
| 1 | Ball-defined contact time per sensor, validated by LED plus tee contact sensor | System, trigger | < $20 | Attack, path (±1–2°) |
| 2 | TDM phase from loop-to-loop Doppler phase with integer-ambiguity test | IWR | $0 | Horizontal/vertical launch (degrees) |
| 3 | ChArUco intrinsics, mat tag, mode-aware ball gate | Camera | ~$15 | Every camera-scaled metric |
| 4 | Fork scale check, bias-corrected interpolator, ridge-at-contact | OPS | Tens of $ | Ball speed (up to 0.8 mph) |
| 5 | Per-angle corner-reflector calibration with radome; shot-accumulated registration | IWR, system | < $50 | Launch direction, registration |
| 6 | D-plane face angle labelled model-derived | Camera, fusion | $0 | Face (±11° → sub-degree plus model error) |
| 7 | Raise IWR or add floor absorber; two-ray ML fit | IWR | Low | Vertical launch |
| 8 | 850 nm strobe with pulse limiter and band-pass | Camera | $40–100 | Impact location, path, ball saturation |
| 9 | Harmonic-comb plus phase spin detector, proven on spun-ball fixture | OPS | $0 plus fixture | Spin |
| 10 | Second strobed camera with markers as truth rig | Bench | $60–120 | Face, impact location truth |

## Autonomous-vehicle and CI discipline keeps accuracy claims honest

Replay makes regressions cheap to detect. With determinism, two code versions run on the same bundles differ only because of code, so regressions should be judged with **paired** per-shot comparisons, which are far more sensitive than the unpaired tests used for noisy performance benchmarks. Mozilla's t-test-based Perfherder shows the cost of the unpaired approach: 12.5% of its alert groups were false positives and about 6.8% missed real regressions ([arXiv 2606.18377](https://arxiv.org/html/2606.18377)).

Each PR should replay a frozen regression set on base and head. Gate on a paired bootstrap of |error| per metric and condition cell, plus a practical-significance threshold. Gate on **availability** too, so that an estimator cannot "improve" by rejecting hard shots. openpilot's process replay compares every PR's outputs with stored reference logs and updates the references only through explicit PRs ([openpilot PR #39004](https://github.com/commaai/openpilot/pull/39004)). ArduPilot's `check_replay_branch.py` confirms that a refactor leaves EKF output unchanged ([ArduPilot Replay](https://ardupilot.org/dev/docs/testing-with-replay.html)). OpenFlight should adopt both: estimator reference outputs, and a one-command check that a refactor changes nothing. Store per-metric summaries from every CI run and scan them for change points, as MongoDB's Hunter does with E-Divisive means ([arXiv 2301.03034](https://arxiv.org/pdf/2301.03034)). That catches slow drift that no single gate trips, and names the commit that caused it.

Leakage and distribution shift are larger risks than overfitting to the test set. When Recht et al. rebuilt the CIFAR-10 and ImageNet test sets, accuracy dropped 3 to 15%. The drops came from harder images, not from adaptive reuse of the old test sets ([Recht et al. 2019](https://proceedings.mlr.press/v97/recht19a.html)). Kapoor and Narayanan found leakage in 294 papers across 17 fields ([arXiv 2207.07048](https://arxiv.org/abs/2207.07048)).

OpenFlight should partition its data into three tiers by rig, tester and day, never by shot:

- **Development/regression:** free to inspect and tune on.
- **Validation:** used for selecting models and thresholds; reported, but not the published claim.
- **Sealed test:** results revealed only at release milestones, with a counter of how many times it has been queried.

Keep at least one whole rig out of development as a leave-one-rig-out test. Put a leakage checklist in the PR template:

- Were constants fitted on sessions in the test tier?
- Were radar windows tuned against TrackMan values from the same shots?
- Does any session-wide normalisation see test shots?

Pin each accuracy claim to a hashed evaluation manifest: the bundle hashes, the criteria version and the reference-match file. Then every number can be re-derived.

Autonomous-vehicle practice also bounds where a claim applies. ISO 21448 (SOTIF) defines the operational domain and identifies **triggering conditions**. Validation exists to turn unknown hazardous scenarios into known ones ([arXiv 2308.02273](https://arxiv.org/pdf/2308.02273)). Waymo reports metrics per difficulty level ([arXiv 1912.04838](https://ar5iv.labs.arxiv.org/html/1912.04838)). For OpenFlight this means three things:

- **Mandatory tags on every bundle:** club, handedness, speed band, lux band (from logged exposure and gain), camera mode, surface, ball type, rig version.
- **Claims only where the data supports them:** accuracy is claimed only in cells with enough reference-matched shots, and every other cell is labelled outside the domain.
- **A triggering-condition registry:** saturated resting ball, low light at short exposure, false acoustic trigger, MTI-notch loss near the tee, left-handed mirroring, and so on. Each entry links to a frozen bundle that reproduces it. Every rejected attempt in the review ledger either maps to an entry or opens a new one. The tester field-issues log is the seed.

Add an automatically computed difficulty level per shot (radar SNR, blur in pixels, saturation fraction, IWR point count), so that a change that helps easy shots but hurts hard ones shows up.

Community data needs layered, automated quality control, and the QC itself must be validated. CrowdQC+ runs weather-station data through sequential levels: metadata, outliers at each time step, validity fraction, correlation with the network, and a neighbour ("buddy") check. It keeps only **20.7% of Amsterdam data and 29.5% of Toulouse data** after the optional levels, and it proves itself against professional reference stations ([CrowdQC+, Frontiers 2021](https://www.frontiersin.org/journals/environmental-science/articles/10.3389/fenvs.2021.720747/full)). The OpenFlight equivalents:

1. Provenance: measured rig geometry, consent scope, verified hashes.
2. Per-shot plausibility: smash bounds, the timing contract.
3. Per-session validity fraction.
4. Artifact checks before and after the session.
5. A buddy check of each session's per-club distributions against the pooled community distribution.

Then show that sessions passing QC have smaller error against reference than sessions failing it. Without that test, the thresholds are opinions.

PX4's Flight Review, where users upload logs and receive automatic reports ([PX4 docs](https://docs.px4.io/main/en/log/flight_log_analysis)), is the model for giving testers per-session verdicts. Tesla's data engine, where deployment errors trigger targeted collection and labelling ([Kargar summary](https://kargarisaac.medium.com/active-learning-data-selection-data-auto-labeling-and-simulation-in-autonomous-driving-part-4-dc985e2c83f9)), is the model for using cross-sensor disagreement to decide which shots get labels or reference sessions. Community sessions establish availability, robustness and new triggering conditions. They never establish accuracy, because they have no reference. Ship each benchmark snapshot with a Gebru-style datasheet generated from bundle metadata ([Datasheets for Datasets](https://arxiv.org/abs/1803.09010)).

## Conclusion

These findings reverse the usual order of work. Deterministic replay makes estimator changes free to evaluate, and that makes the *references* the scarce resource, not the algorithms. An hour spent building a spun-ball fixture or a corner-reflector turntable will outlast any single estimator tweak, because every future commit gets re-scored against it. Fixed physics dominate the error budgets: a 2.9 ms/m acoustic delay, a velocity-dependent TDM phase, 12 mm of motion blur and a 0.5% carrier tolerance. The largest single gain, ball-defined contact timing, needs no new hardware at all.

This also changes what OpenFlight should claim. Against a comparator that publishes no tolerances and whose spin reliability can fall to ICC 0.02, "within X of TrackMan" is a statement of agreement, not accuracy. A monocular behind-ball face angle is a model output, not a measurement. The most credible position for an open-source monitor is one no commercial device takes: publish per-metric uncertainty budgets, coverage, per-condition limits of agreement and the artifact control charts behind them. That transparency is a product feature. It would make OpenFlight the first launch monitor whose accuracy claims anyone can audit and reproduce from hashed bundles.
