# Launch monitor validation: published studies, reference methods, and a scoring program for OpenFlight

Research date: 2026-09-25. Evidence tiers used below:
- **T1**: peer-reviewed, independent.
- **T2**: peer-reviewed, but with possible industry ties, or a weak design.
- **T3**: manufacturer documentation or patents. These are authoritative on definitions and biased on accuracy.
- **T4**: independent reviewers and retailers (MyGolfSpy, PlayBetter and similar). They have affiliate-link conflicts and opaque methods.

Access note: ScienceDirect, Taylor & Francis, PubMed, ResearchGate, MDPI, USGA PDFs and the TrackMan help centre all returned HTTP 403 or a CAPTCHA to the fetch tool. For those papers, abstracts came through Europe PMC's REST API. The full text was read for one paper only: Bliss and Langdown 2026, which is open access in PMC. Figures below that come only from a search-engine snippet are marked "(snippet)".

---

## Q1. Published validation studies of commercial launch monitors: methods, statistics, per-metric agreement, samples, conditions

### Takeaway
The peer-reviewed literature is small. It has six core papers, and only one of them uses a true criterion reference, not another launch monitor:
- **Leach et al. 2017.** Four 5400 Hz high-speed cameras, benchmarked against TrackMan Pro IIIe and Foresight GC2+HMT.
- **Every study since then** uses TrackMan 4 as the "criterion" for a cheaper device, or tests TrackMan's reliability against itself.

The results are consistent across the studies:
- **Agree well:** ball speed, club speed, smash factor and carry.
- **Agree poorly:** spin rate, spin axis, attack angle (especially with irons), dynamic loft and swing plane.
- **Mostly a fixed offset:** club metrics between devices. Consistency ICC is high while absolute-agreement ICC is low, so the devices differ by a systematic bias rather than by noise.

No peer-reviewed validity study was found for Full Swing KIT, Mevo Gen 2, Garmin R10, Rapsodo MLM/MLM2PRO, Uneekor or GCQuad.

### Cited Findings

**Leach, Forrester, Mears & Roberts (2017), *Measurement* 112:125–136. T1. This is the only criterion study.**
- **Devices and reference.** TrackMan Pro IIIe (radar) and Foresight GC2 + HMT (stereo optical) were compared against a high-speed video system, the criterion method. It used four cameras, each running at 5400 Hz. — [ResearchGate record](https://www.researchgate.net/publication/319147118_How_valid_and_accurate_are_measurements_of_golf_impact_parameters_obtained_using_commercially_available_radar_and_stereoscopic_optical_launch_monitors); [ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S0263224117305079)
- **Sample.** 240 shots across driver, 7-iron and utility wedge: eight golfers × 10 shots per club. All three systems were captured simultaneously. — [Dr Jon Roberts project page](https://jonroberts.info/?page_id=110)
- **Reference uncertainty.** The benchmark was processed in GOM Inspect. Its stated measurement uncertainty (95% CI) is:
  - ball velocity 0.06 mph
  - launch angle and direction 0.01°
  - spin rate 14 rpm

  — [jonroberts.info](https://jonroberts.info/?page_id=110)
- **Results.**
  - Ball parameters agreed better than clubhead parameters for both launch monitors.
  - Spin rate, club direction, attack angle and dynamic loft agreed more closely for 7-iron and wedge than for driver.
  - The authors' conclusion: the ball parameters from both devices can be trusted for research, clubhead data needs caution, and both are adequate for coaches and fitters.

  — [jonroberts.info](https://jonroberts.info/?page_id=110); [Loughborough repository](https://repository.lboro.ac.uk/articles/journal_contribution/How_valid_and_accurate_are_measurements_of_golf_impact_parameters_obtained_using_commercially_available_radar_and_stereoscopic_optical_launch_monitors_/9562799)
- **Gap:** per-metric bias and limits-of-agreement numbers could not be retrieved (paywalled).

**Bliss & Langdown (2026), *JSAMS Plus*, doi:10.1016/j.jsampl.2025.100128. T2: one golfer, and the reference is TrackMan. Open access; full text read.**
- **Design.**
  - One golfer (handicap 8.7) in an indoor lab.
  - TrackMan 4 at 2.5 m from the ball; Mevo+ at 2.4 m, placed in front and parallel.
  - Sensors were aligned with a plumb line, a carpenter's square and a spirit level, and the positions were taped for repeatability.
  - TrackMan was set to "normalised" (0 ft altitude, no wind, premium ball), with matching Mevo+ settings.
  - Titleist Pro V1x balls carried a metallic sticker for Mevo+ indoor spin.
  - Drives were hit from a 40 mm castle tee; irons from a mat.

  — [Europe PMC full text PMC13227226](https://www.ebi.ac.uk/europepmc/webservices/rest/PMC13227226/fullTextXML); [ScienceDirect](https://www.sciencedirect.com/science/article/pii/S2772696725000420)
- **Statistics.**
  - ICC from a two-way mixed, *mean-rating* model, reporting both consistency and absolute agreement.
  - Bland–Altman plots with ±1.96 SD limits.
  - MAPE and Cohen's d.
  - A priori acceptance: 95% of points inside the limits of agreement.

  — [PMC13227226](https://www.ebi.ac.uk/europepmc/webservices/rest/PMC13227226/fullTextXML)
- **Attrition. This is critical for pairing.**
  - Of 118 driver shots, 58 were analysed:
    - 15 had no TrackMan club data
    - 42 were removed because TrackMan or Mevo+ reported *estimated* spin
    - 3 were mishits under 150 yd carry
  - 7-iron: 80 of 118 kept (7 had no TrackMan club data; 31 had estimated Mevo+ spin).
  - Pitching wedge: 69 of 174 kept (60 had no TrackMan club data; 4 had no Mevo+ club data; 41 had estimated spin).

  — [PMC13227226](https://www.ebi.ac.uk/europepmc/webservices/rest/PMC13227226/fullTextXML)
- **Driver results (n=58; mean difference is TrackMan − Mevo+, ± SD).**

  | Metric | Mean diff ± SD | ICC consistency | ICC absolute |
  |---|---|---|---|
  | Ball speed | −1.49 ± 0.82 mph | 0.996 | 0.98 |
  | Club speed | −0.40 ± 1.00 mph | 0.98 | 0.97 |
  | Launch angle | −0.75 ± 0.84° | 0.98 | 0.96 |
  | Launch direction | −0.30 ± 1.99° | 0.92 | 0.92 |
  | Attack angle | 1.20 ± 0.96° (MAPE 44%) | 0.95 | 0.87 |
  | Club path | −1.50 ± 2.35° | 0.66 | 0.58 |
  | Face angle | −0.24 ± 2.00° | 0.94 | 0.94 |
  | Face to path | 1.34 ± 1.25° | — | 0.96 |
  | Dynamic loft | −1.23 ± 1.82° | — | 0.90 |
  | Spin rate | −55 ± 513 rpm | 0.80 | 0.80 |
  | Spin axis | 3.24 ± 12.28° | 0.77 | 0.76 |
  | Swing plane | −5.24 ± 2.31° | — | 0.24 |
  | Carry | 1.63 ± 6.72 yd | — | 0.95 |

  — [PMC13227226](https://www.ebi.ac.uk/europepmc/webservices/rest/PMC13227226/fullTextXML)
- **7-iron results (n=80).**

  | Metric | Mean diff ± SD | ICC consistency | ICC absolute |
  |---|---|---|---|
  | Ball speed | −3.03 ± 0.57 mph | 0.996 | 0.90 |
  | Club speed | −4.44 ± 1.64 mph | — | 0.44 |
  | Launch angle | −0.84 ± 0.48° | — | — |
  | Attack angle | 2.73 ± 2.16° | 0.06 | 0.02 |
  | Dynamic loft | −7.77 ± 2.16° | — | 0.23 |
  | Club path | 0.28 ± 2.21° | — | 0.83 |
  | Face angle | −0.69 ± 1.51° | — | 0.94 |
  | Spin rate | −405 ± 1512 rpm | — | 0.49 |

  — [PMC13227226](https://www.ebi.ac.uk/europepmc/webservices/rest/PMC13227226/fullTextXML)
- **Spin doubling.**
  - Five of the 80 7-iron shots, all flagged "measured", reported 11,000–13,500 rpm. That is about 6,000 rpm above TrackMan.
  - The authors attribute this to a radar seam error: the ball's seam modulates the Doppler signal at twice the rotation rate.

  — [PMC13227226](https://www.ebi.ac.uk/europepmc/webservices/rest/PMC13227226/fullTextXML)

**Brennan et al. (2024), *J Strength Cond Res* 38(4):e174–e181. T2: reference is TrackMan; the co-authors include Mark Broadie. No funding conflict was found in the abstract.**
- **Design.** 29 youth golfers × 10 shots each with a 6-iron and a driver. Eight metrics were captured concurrently from Mevo+ and TrackMan 4 in an indoor lab.
- **Results.**
  - Pearson r ranged from 0.254 to 0.985 and was highest for club speed and ball speed (r ≥ 0.92).
  - Bland–Altman agreement for smash factor had bias ≤ −0.016 (95% CI −0.112 to 0.079).
  - Spin rate agreed worst: bias ≤ 1,238 rpm (95% CI −2,628 to 5,103).
- **Reliability.**
  - TrackMan: ICC 0.60–0.99, CV 1.31–230%.
  - Mevo+: ICC −0.22 to 0.99.
- **Significant device differences:**
  - spin rate (driver d=1.27; 6-iron d=0.90)
  - launch angle (driver d=0.54)
  - attack angle (driver d=−0.51)

— [Europe PMC abstract, doi:10.1519/jsc.0000000000004685](https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=%22launch%20monitor%22%20golf&resultType=core&format=json&pageSize=40)
- **Critique.** Bliss & Langdown argue that Brennan's poor spin agreement is partly an artefact of including shots whose spin was "estimated". Some of Brennan's driver shots showed about 12,000 rpm. — [PMC13227226](https://www.ebi.ac.uk/europepmc/webservices/rest/PMC13227226/fullTextXML)

**Bishop et al. (2023/2024), *J Sports Sci*, doi:10.1080/02640414.2024.2314864. T2: a reliability study only, with no reference.**
- **Design.** High-level golfers, 10 driver shots in each of two sessions, at an indoor academy.
- **Reliability.**
  - Club speed: ICC 0.99, SEM 1.64–1.67 mph.
  - Ball speed: ICC 0.97–0.99, SEM 2.46–4.42 mph.
  - Carry: ICC 0.91–0.97.
  - Spin rate was worst: ICC 0.02–0.60, SEM ≈ 241–455 (units as printed in the abstract), with a significant between-session difference.
- **Inter-metric correlation.** Launch angle vs dynamic loft r=0.98–0.99.

— [Europe PMC](https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=%22launch%20monitor%22%20golf&resultType=core&format=json&pageSize=40); [T&F](https://www.tandfonline.com/doi/full/10.1080/02640414.2024.2314864)

**Shaw, Gould, Oliver & Lloyd (2023), *J Strength Cond Res* 37(12):2431–2437. T1 for reliability.**
- **Design.** 21 talented golfers, two sessions, 3 × 6-iron and 3 × driver shots per session.
- **Within-session.** Club speed, ball speed, carry and total were reliable (CV ≤ 5.8%, ICC ≥ 0.87). 6-iron launch angle was also reliable (CV 6.7–9.3%, ICC 0.87–0.92).
- **Between-session.** Club speed, ball speed, smash, launch, carry and total were reliable (CV 0.7–9.1%, ICC 0.5–0.99). 6-iron spin rate was reliable (CV 9.4%, ICC 0.89).
- **Not supported.** Attack angle, club path and spin axis were measured but did not appear among the reliable variables.

— [Europe PMC](https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=%22launch%20monitor%22%20golf&resultType=core&format=json&pageSize=40)

**Adjacent / methodological**
- **Mackenzie et al. (2018), *J Sports Sci*.** Clubhead kinematics came from optical motion capture, with ball flight from a Doppler radar launch monitor. 40 golfers × 14 swings per shaft. This is an example of running motion capture and radar side by side for club data; it is not a validation study. — [Europe PMC](https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=%22launch%20monitor%22%20golf&resultType=core&format=json&pageSize=40)
- **Wood et al. (2018), ISEA *Proceedings* 2(6):249.** Horizontal launch lay on average 61–76% of the way from club path toward face angle. Vertical launch lay 72–83% of the way from attack angle toward dynamic loft, depending on club. A Hertzian impact model with Coulomb friction predicted launch direction adequately. (snippet) — [ResearchGate](https://www.researchgate.net/publication/323373897_The_Influence_of_Face_Angle_and_Club_Path_on_the_Resultant_Launch_Angle_of_a_Golf_Ball)
- **Menke-Zumbrägel et al. (2023), icSPORTS.** Measures vertical launch angle of a golf ball from audio plus monocular video. This is directly analogous to OpenFlight's sound trigger plus camera. — [Crossref DOI 10.5220/0012163200003587](https://api.crossref.org/works?query=golf+launch+monitor+validity+accuracy&rows=40&select=title,author,container-title,issued,DOI)

**Industry and reviewer comparisons (T3/T4, low evidence)**
- **Rapsodo (manufacturer).** Claims the MLM2PRO is within 0.97° of a Foresight GCQuad on attack angle and 0.76° on swing path, over more than 10,000 development shots. (snippet, via MyGolfSpy) — [MyGolfSpy](https://mygolfspy.com/news-opinion/rapsodos-latest-move-has-expensive-launch-monitors-sweating/)
- **Rapsodo white paper.** Compares its ball-flight *algorithm* to GC3 and TM4 by feeding in shared launch parameters. It tests the flight model, not the sensor. — [Rapsodo blog](https://rapsodo.com/blogs/golf/mlm2pro-ball-flight-algorithm-vs-foresight-gc3-trackman-tm4-accuracy-performance-comparison)
- **Foresight marketing, using Golf Laboratories robot tests. T3 with a conflict of interest.**
  - Centre-strike spin rate standard deviation: GCQuad 82 rpm vs TrackMan 4 about 175 rpm.
  - Club-speed standard deviation on toe strikes: Uneekor EYE MINI 10.4 mph vs GC3 0.4 mph.

  Each figure comes from 10 identical robot swings. (snippet) — [Foresight blog](https://www.foresightsports.com/blogs/golf-tips/comparing-golf-launch-monitor-foresight-sports-gcquad-and-gc3-stand-above-the-rest)
- **MyGolfSpy method. T4, with affiliate links.**
  - Indoor accuracy is measured against "the industry standard camera-based" monitor, and outdoor accuracy against "the industry standard Doppler" monitor.
  - Balls were Pro V1 RCT where applicable; Rapsodo was tested with its own RPT balls.
  - They do not publish their statistics.

  — [MyGolfSpy best launch monitors (fetched via curl)](https://mygolfspy.com/buyers-guides/golf-technology/best-golf-launch-monitors/)
- **Full Swing KIT.** Retailer copy claims "ball speed accuracy within 0.31%, spin within 0.47%". No method or reference device is given. T4, unverifiable. — [Golf Sim Depot / search summary](https://golfsimdepot.com/blogs/knowledge-center/how-accurate-is-the-full-swing-kit-launch-monitor)
- **Mevo Gen 2.** Only reviewer impressions were found, for example "within 1–2 yd of TrackMan" and lower spin accuracy below 70 mph. No quantitative specification. T4. — [PlayBetter](https://www.playbetter.com/blogs/golf-simulator-reviews/flightscope-mevo-gen2-review)
- **Garmin R10.** Only reviewer tests were found. They report ball speed, launch and carry within about 3–5% of premium units, with weak face, path and spin. T4. — [PlayBetter R10](https://www.playbetter.com/blogs/golf-simulator-reviews/garmin-approach-r10-review-golf-launch-monitor-under-1000); [MyGolfSpy R10](https://mygolfspy.com/we-tried-it/garmin-r10-review/)
- **TrackMan published specifications.** The TrackMan 4 / iO tech-spec page states **no accuracy tolerances** for any parameter. — [TrackMan tech specs](https://www.trackman.com/golf/launch-monitors/tech-specs)

### Inferences
- **The standard statistics bundle** in this field is:
  - Bland–Altman bias and 95% limits of agreement
  - ICC(2,1) or ICC(3,1), reported as both consistency and absolute agreement
  - MAPE or CV
  - Cohen's d for systematic difference

  OpenFlight should report the same set so its numbers can sit next to the published Mevo+ figures.
- **Use single-measure ICC.** Bliss and Langdown used a *mean-rating* ICC, which inflates agreement relative to single-measure ICC. For a device that reports one value per shot, the single-measure form is the right one.
- **What a Mevo-class device achieves against TrackMan 4** (driver, Bliss and Langdown), as a realistic "good enough" band for comparator-class radar:
  - ball speed bias about 1.5 mph with SD about 0.8 mph
  - launch angle bias under 1° with SD about 0.8°
  - launch direction SD about 2°
  - club path SD about 2.3°
  - attack angle bias 1–3°

  The source is a single golfer, so treat this as indicative.
- **Separate bias from noise.** Across all studies, club speed, attack angle, dynamic loft and swing plane disagree mainly by *systematic bias*, which is a definition problem. Launch direction, club path and spin disagree mainly by *random error*. OpenFlight's scoring should report the two separately.
- **The Mevo+ spin doubling matches OpenFlight's OPS spin problem.** A 2× seam-harmonic error flagged as "measured" is exactly the failure a CW Doppler spin estimator can produce. A gross-error screen (spin more than 1.6× the reference) should be scored separately from the RMS error.

### Gaps
- Leach 2017's per-metric bias and limits-of-agreement values are paywalled. Worth getting through Loughborough's repository PDF manually.
- No peer-reviewed validation was found for GCQuad, Full Swing KIT, Mevo Gen 2, Garmin R10, Rapsodo MLM2PRO, Uneekor or SkyTrak. Europe PMC and Crossref searches for these returned nothing relevant.
- No study was found that validated TrackMan face angle or club path against motion capture with a stated uncertainty. Leach covered club parameters, but the numbers are unavailable.
- Brennan et al.'s funding or conflict statement was not visible in the abstract.

---

## Q2. Reference ("ground truth") methods per metric: cost, accuracy, practicality

### Takeaway
There are four tiers of reference:
1. **Metrology.** Multi-camera high-speed photogrammetry: 4 × 5400 fps plus GOM-class processing, with uncertainty around 0.06 mph and 0.01°. USGA/R&A instrumented indoor ranges also sit here.
2. **Controlled launchers.** Robots, ball cannons with servo pre-spin, and calibration spin wheels.
3. **A second launch monitor.** TrackMan or GCQuad. This is itself a model-based estimate.
4. **Cheap physical checks** of single metrics (see Q5).

A hobby project can realistically reach tier 4 plus tier 3. It can reach a *partial* tier 1 by building its own marked-ball, high-fps stereo or mono rig for ball launch and spin only.

### Cited Findings

**High-speed video / photogrammetry**
- Four synchronised 5400 Hz cameras with GOM Inspect processing gave the stated uncertainty (95% CI):
  - ball speed 0.06 mph
  - launch angle and direction 0.01°
  - spin 14 rpm

  — [jonroberts.info](https://jonroberts.info/?page_id=110)
- Biomechanics work treats Vicon/Qualisys motion capture or high-speed video as the "gold standard" for criterion validation. (snippet) — [ScienceDirect Bliss & Langdown landing page](https://www.sciencedirect.com/science/article/pii/S2772696725000420)
- Low-cost 2D video analysis tools (Kinovea, SiliconCoach) on a high-speed camera gave ICC > 0.929 for club *angle* and timing parameters, compared with 3D analysis. — [Hunter et al. 2022, Sci Rep, PMC9385988](https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=%22launch%20monitor%22%20golf&resultType=core&format=json&pageSize=40)

**USGA/R&A test protocols**
- **Current Overall Distance Standard (ODS) launch conditions:** 120 mph club speed, 10° launch, 2520 rpm.
- **From the 2028 testing cycle:** 125 mph, 11°, 2220 rpm. The standard stays at 317 yd + 3 yd tolerance.

  — [USGA FAQ (search summary)](https://www.usga.org/content/usga/home-page/advancing-the-game/distance-insights/golf-ball-revised-testing-guidelines--frequently-asked-questions.html); [USGA ITR 2028 conditions PDF](https://www.usga.org/content/dam/usga/images/equipment-standards/ITR-test-conditions-2028-ODS.pdf)
- The USGA Indoor Test Range is a roughly 70 ft tunnel with a pitching-machine-style ball launcher, used for aerodynamics. It is kept at 75 ± 3 °F, and balls are conditioned at 75 ± 1 °F for at least 3 hours. — [USGA R&T Center Primer](https://www.usga.org/equipment-standards/research-and-test-center-primer.html); [TPX3006 ODS protocol](https://www.usga.org/content/dam/usga/pdf/2019/equipment-standards/TPX3006%20Overall%20Distance%20and%20Symmetry%20Test%20Protocol.pdf)
- The USGA PDFs were blocked (403). The tolerances on launch conditions and the instrumentation used to verify them could not be read.

**Robots and launchers (patents T3)**
- **Golf Laboratories robot** (Gene Parente). It is used by the major club makers and by governing bodies for repeatable swings, and is the basis of the Foresight / MyGolfSpy repeatability comparisons (10 identical swings). — [Foresight blog](https://www.foresightsports.com/blogs/golf-tips/comparing-golf-launch-monitor-foresight-sports-gcquad-and-gc3-stand-above-the-rest)
- **Spin-wheel calibrator, US 7,441,438 / US20070135223A1.**
  - Marked golf balls are embedded at a known radius on a motor-driven wheel. This gives a known speed and spin at the launch monitor's field of view.
  - The monitor's readings are compared with those known values to check accuracy and repeatability.
  - It was motivated by portable monitors going out of calibration when moved.

  — [Google Patents US20070135223A1](https://patents.google.com/patent/US20070135223A1/en)
- **Air cannon with servo pre-spin, US 7,849,725.** A ball is clamped, spun by a servo about a chosen axis, then launched by an air valve. — [Google Patents](https://patents.google.com/patent/US7849725B1/en)
- **Automated launcher, US20110056473.** Air cannon plus belt-drive spin and velocity assemblies, with computer control of spin, velocity and launch angle. — [Google Patents](https://patents.google.com/patent/US20110056473)
- A launch monitor patent reports spin averaging 3021 rpm against a rotation-rate-sensor reference of 3018 rpm, about 1 part in 1000. (snippet; exact patent number unverified among US 9697613 / 10223797 / 10902612) — [USPTO search result list](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/9697613)

**Radar speed reference**
- **Certified K-band tuning forks.** OmniPreSense (maker of the OPS243-A) calls them "the simplest means of validating the speed accuracy of the radar sensor".
  - The OPS243-A's default carrier is 24.125 GHz. K-band forks assume 24.15 GHz, giving a 1–4 Hz error. The carrier can be shifted in 40 MHz steps with the `T=n` command.
  - A 35 mph fork read 35.27–35.34 mph, "within 0.9%".
  - The default resolution is 0.0607 m/s (0.136 mph). A 1024-sample / 20 ksps / 4096-point FFT setting gives 0.0303 m/s.
  - Forks above about 65 mph are hard for the sensor to pick up.
  - The `UH` command outputs raw Doppler frequency for a direct comparison.
  - OPS242-A, OPS243-A and OPS243-C have police-radar-lab certification letters (appendices).

  — [OmniPreSense AN-026 (Mar 2025)](https://omnipresense.com/wp-content/uploads/2025/03/AN-026-A_Speed-Accuracy-Using-Tuning-Fork-1.pdf)
- **AN-026 is internally inconsistent.**
  - Its table follows v = Δf·c/(2f₀): 2518 Hz = 35 mph and 1024 Hz = 14.2 mph.
  - The prose says "1024 → 28.5 mph" and "65-mph fork (2,338 Hz)", which disagree with the formula.
  - CopRadar gives 4671.9 Hz for 65 mph at 24.150 GHz, which is consistent with the formula.
  - Use the formula, not the prose.

  — [AN-026](https://omnipresense.com/wp-content/uploads/2025/03/AN-026-A_Speed-Accuracy-Using-Tuning-Fork-1.pdf); [CopRadar calibration chapter](https://copradar.com/chapts/chapt3/ch3d4.html)
- **Pocket Radar** sells a calibrated fork for accuracy verification. NIST published the foundational police-radar calibration method (older, foundational). — [Pocket Radar](https://www.pocketradar.com/products/calibrated-tuning-fork-for-accuracy-verification); [NIST TN](https://tf.nist.gov/general/pdf/87.pdf)
- **Traceable calibration of a 24 GHz CW Doppler sensor** in a train-borne context. A methodological template. — [Sensors, PMC7070750](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC7070750/)

**Per-metric reference options**

| Metric | Gold reference | Hobby-feasible reference | Approx. cost | Expected reference accuracy |
|---|---|---|---|---|
| Ball speed | Multi-cam high-speed photogrammetry | TrackMan session (rental/fitter); tuning fork for the Doppler chain; own high-fps camera with ruler scale | Fork about $50–150; TM session about $50–100/h (price not sourced) | Photogrammetry 0.06 mph ([Roberts](https://jonroberts.info/?page_id=110)); fork within 0.9% ([AN-026](https://omnipresense.com/wp-content/uploads/2025/03/AN-026-A_Speed-Accuracy-Using-Tuning-Fork-1.pdf)) |
| Vertical / horizontal launch | Photogrammetry (0.01°) | TrackMan; own calibrated camera tracking the first 0.5 m of flight; laser-aligned target line | Low | Depends on camera calibration; no source |
| Spin rate / axis | Photogrammetry with marked ball (14 rpm) | Marked ball + OV9281 at high fps; spin wheel or drill fixture with tachometer | Low | Tachometer about 0.1% ([patent snippet](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/9697613)) |
| Club speed / path / attack | Motion capture (Vicon/Qualisys) with club markers | TrackMan (definition caveats, Q3); high-fps video of a marked head | Mocap is lab-only | Not found for TrackMan vs mocap |
| Face angle | Photogrammetry of face markers (Foresight HMT-style) | Foresight GCQuad/HMT session; face-line markers + own camera | GCQuad access via fitter | None published |
| Impact location | Face tape / spray (physical trace) | Impact tape, foot spray | Very low | Direct physical evidence |

### Inferences
- **Ball metrics can get a genuine tier-1 reference cheaply.** The OV9281 plus a marked ball, a short-exposure strobe or IR flash, and a calibrated checkerboard pose is the same method family as Leach's criterion, only with fewer cameras and lower fps. This is where OpenFlight's own camera could serve as the reference for radar launch and spin, provided it is *validated first* on a static or spin-wheel fixture.
- **For club metrics, a hobby team cannot reach a true criterion.** It must use TrackMan or GCQuad as a comparator and report *agreement*, not *accuracy*.

### Gaps
- USGA launch-condition tolerances and verification instrumentation. The PDFs were blocked; retrieve them manually.
- The price of TrackMan or GCQuad rental sessions was not sourced.
- No published accuracy for Golf Laboratories robot delivery (club speed and path repeatability).

---

## Q3. The reference is itself a model; reconciling "point on the clubhead" and face-angle definitions (D-plane)

### Takeaway
TrackMan and Foresight measure club metrics at *different points* on the head, and disagree even on the *moment*:
- **TrackMan:** geometric centre of the head (near the CoG), at maximum compression, pre-impact data only.
- **Foresight:** the face-plane centre via markers, just before contact.

For a driver, the two definitions differ by about 3° in club path and about 1° in attack angle. Face angle follows the bulge and roll of the face, about 2° per 10 mm of toe impact.

TrackMan chose its definitions specifically so its D-plane model closes. Any other definition breaks that model.

Researchers have handled this mostly by *not* handling it: they report consistency ICC (bias-tolerant) next to absolute ICC and warn users about systematic offsets.

### Cited Findings
- **TrackMan measurement point and path/attack differences.** TrackMan reports Club Speed, Attack Angle and Club Path "relative to the CoG — or more precisely, at the geometric center of the club head". This point is found from the measured 3D silhouette and lies within about 6 mm of the CoG. For a driver, centre-face path is about 3° more outside-in than CoG path, and centre-face attack angle about 1° more upward. — [TrackMan club data definitions](https://www.trackman.com/blog/club-data-definitions)
- **Optical monitors use the face centre.** "Optical-based launch monitors almost always measure Club Speed, Attack Angle and Club Path relative to the center of the club face, since this is where the reflective markers are positioned… Often, the numbers… aren't comparable." — [TrackMan](https://www.trackman.com/blog/club-data-definitions)
- **Face angle and dynamic loft are taken at the impact location, not face centre.** For a standard driver:
  - a 10 mm toe impact reads about 2° more open than face centre
  - a 10 mm low impact reads about 2° less dynamic loft

  — [TrackMan](https://www.trackman.com/blog/club-data-definitions)
- **Timing.**
  - Club speed, attack angle and path are referenced to maximum compression, using only pre-impact data. TrackMan says choosing maximum compression rather than first touch changes values by less than 0.05 mph and 0.4°.
  - Face angle and dynamic loft are averaged over the impact interval, because the face can rotate 1–2° during contact. For a driver, attack angle can be 4–6° steeper at separation than at first touch.
  - The time reference was chosen "to simplify the use of the D-plane".

  — [TrackMan](https://www.trackman.com/blog/club-data-definitions)
- **Foresight definitions.**
  - Club speed is "measured just prior to ball contact".
  - Face angle is taken from the measured face plane.
  - Impact location is reported in mm from face centre.
  - Foresight also reports face-to-target, face-to-path, lie, loft, closure rate and F-axis.

  — [Foresight Club Head Data definitions](https://help.foresightsports.com/hc/en-us/articles/47214673873811-Club-Head-Data-Measurements-Definitions)
- **Conflicting claim.** Third-party explainers say TrackMan measures club speed "after contact… slowing down", so Foresight always reads higher club speed and lower smash. TrackMan's own document says only pre-impact data is used. Treat the third-party claim as unreliable. — [AmateurGolf.com](https://www.amateurgolf.com/golf-tournament-news/33314/Foresight-vs-Trackman--Unpacking-the-key-differences-for-amateur-golfers) contradicted by [TrackMan](https://www.trackman.com/blog/club-data-definitions)
- **Empirical sign of definition bias.** Mevo+ vs TrackMan 4 differed systematically on:
  - 7-iron club speed: −4.44 mph
  - 7-iron dynamic loft: −7.77°
  - driver swing plane: −5.24°

  — while consistency ICC stayed high. — [Bliss & Langdown PMC13227226](https://www.ebi.ac.uk/europepmc/webservices/rest/PMC13227226/fullTextXML)
- **Physics check on the D-plane.** Launch direction lies 61–76% of the way from path toward face. Vertical launch lies 72–83% of the way from attack angle toward dynamic loft. (snippet) — [Wood et al. 2018](https://www.researchgate.net/publication/323373897_The_Influence_of_Face_Angle_and_Club_Path_on_the_Resultant_Launch_Angle_of_a_Golf_Ball)
- **Launch correlates tightly with dynamic loft.** TrackMan's own launch angle vs dynamic loft correlation is r = 0.98–0.99. This is partly model coupling, not independent confirmation. — [Bishop et al. via Europe PMC](https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=%22launch%20monitor%22%20golf&resultType=core&format=json&pageSize=40)

### Inferences
- **OpenFlight must declare its definition per club metric** and should match TrackMan's where TrackMan is the comparator:
  - geometric centre / CoG path
  - maximum-compression time
  - face angle at the impact location, relative to the target line

  The camera tracks a face-plane point. Without a CoG offset correction (about 25–50 mm behind the face for a driver, per TrackMan), OpenFlight should *expect* a roughly 3° path bias against TrackMan on drivers and much less on irons.
- **Treat TrackMan-derived club values as model outputs.** Face angle, dynamic loft and spin axis (indoors) are partly model-coupled, so high correlation among them does not prove each one independently. Check at least one metric per group against a physical reference, for example face angle against a static face-line jig.
- **Use D-plane closure as a self-consistency check.** Predicted launch direction from OpenFlight's own face and path should land within about ±1–2° of the radar or camera horizontal launch on centre strikes. This needs no external reference, which matters because IWR horizontal launch is currently withheld.

### Gaps
- TrackMan's help-centre parameter-definitions page returned 403. The definitions above come from the 2017 TrackMan blog, and newer firmware may differ.
- No published quantification of CoG-vs-face-centre path bias measured *across devices*. The ~3° figure is TrackMan's own.
- Full Swing KIT and Mevo Gen 2 definitions of club path and face angle (measurement point and moment) were not found.

---

## Q4. Matching shots between devices, and common pitfalls

### Takeaway
Published studies pair shots by concurrent capture plus shot order in exported sessions. None describes timestamp-based matching. The largest source of lost data is *device dropout*:
- TrackMan 4 lacked club data on 60 of 174 pitching-wedge shots indoors.
- "Estimated" spin shots made up 25–40% of samples.

The key pitfalls:
- including estimated values
- normalised vs raw carry settings
- ball type and markers
- definition offsets
- the sub-100% intersection of shots each device captures
- gross errors such as spin doubling being averaged into the RMS

### Cited Findings
- **Bliss & Langdown pairing and exclusions.**
  - Both devices were captured concurrently and exported (TrackMan export; FS Golf app/cloud), then organised in Excel.
  - A shot was dropped from *both* datasets if either device was missing any variable, or if either reported estimated spin.
  - Mishits below a carry threshold were dropped.
  - Driver kept 58 of 118 shots; PW kept 69 of 174.

  — [PMC13227226](https://www.ebi.ac.uk/europepmc/webservices/rest/PMC13227226/fullTextXML)
- **Setup matching.**
  - Devices were aligned with plumb line, square and spirit level; floor tape preserved positions between sessions.
  - TrackMan was calibrated first, then Mevo+ was aligned to the same target via the simulator screen.
  - Both devices were set to normalised or standard sea level with a standard ball.

  — [PMC13227226](https://www.ebi.ac.uk/europepmc/webservices/rest/PMC13227226/fullTextXML)
- **Estimated vs measured.** Brennan et al. included estimated-spin shots. Bliss & Langdown say this explains Brennan's poor spin agreement (some drivers at about 12,000 rpm). — [PMC13227226](https://www.ebi.ac.uk/europepmc/webservices/rest/PMC13227226/fullTextXML)
- **"Measured" is not always measured.** Mevo+ labelled five 11,000–13,500 rpm 7-iron shots as measured. The cause is seam harmonic doubling. — [PMC13227226](https://www.ebi.ac.uk/europepmc/webservices/rest/PMC13227226/fullTextXML)
- **Indoor ball markers.** Mevo+ needs a metallic foil sticker in one dimple for indoor spin. MyGolfSpy used each device's required ball (Rapsodo RPT, GolfJoy marked balls, Pro V1 RCT). — [PMC13227226](https://www.ebi.ac.uk/europepmc/webservices/rest/PMC13227226/fullTextXML); [MyGolfSpy](https://mygolfspy.com/buyers-guides/golf-technology/best-golf-launch-monitors/)
- **Indoor vs outdoor.** Foresight maintains a help article, "Indoor vs Outdoor: Why Your Numbers Differ" (title only seen). MyGolfSpy splits its testing: camera reference indoors, Doppler reference outdoors. — [Foresight help (related-articles list)](https://help.foresightsports.com/hc/en-us/articles/47214673873811-Club-Head-Data-Measurements-Definitions); [MyGolfSpy](https://mygolfspy.com/buyers-guides/golf-technology/best-golf-launch-monitors/)
- **Session effects.** TrackMan spin rate showed a significant between-session difference (g = −0.41) with ICC 0.02–0.60. A single-session comparison can therefore understate reference spin noise. — [Bishop et al.](https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=%22launch%20monitor%22%20golf&resultType=core&format=json&pageSize=40)
- **Reference noise floor.** TrackMan 4's own within-session SEM for driver ball speed is 2.46–4.42 mph. This mixes golfer variability with device noise, so it is not a pure device error. — [Bishop et al.](https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=%22launch%20monitor%22%20golf&resultType=core&format=json&pageSize=40)

### Inferences
- **Match shots by time, not order.** OpenFlight already timestamps every shot. It should pair shots by wall-clock time with a ±1–2 s window and validate pairs by ball-speed agreement, rather than by index. One dropped shot on either device otherwise shifts every later pair. Recommended procedure:
  - NTP-sync the Pi.
  - Record the TrackMan/Mevo session start time.
  - Before matching, align the two series with a one-parameter clock offset found by cross-correlating ball-speed sequences.
- **Pre-register exclusion rules and report attrition per device and per metric.** Reporting how often OpenFlight withholds a metric, such as horizontal launch or club path, is part of the score. Coverage should be a first-class metric next to error, because a monitor can look accurate by withholding its hard shots.
- **Use raw (non-normalised) mode on the comparator** for launch parameters, which are unaffected either way. Always record the comparator's normalisation setting and ball model when carry is compared.

### Gaps
- No published protocol describes timestamp-level pairing between launch monitors. All studies rely on concurrent sessions and exports.
- TrackMan's shot-export format and timestamp precision were not examined.

---

## Q5. Cheap physical references for individual metrics

### Takeaway
Several single-metric checks cost under about $200 and are traceable or first-principles:
- **K-band tuning forks** for the OPS243-A Doppler chain.
- **A motor-driven spin wheel** with marked balls and a tachometer: known spin and known tangential speed. This is the patented calibrator concept.
- **Static geometric jigs** for launch angle, azimuth and face angle: an inclinometer, a laser line, and a known-angle ramp or target.
- **Drop and pendulum tests** for camera timing and scale.
- **Impact tape and foot spray** for impact location.

None checks the full dynamic chain. They isolate sensor bias before comparator sessions.

### Cited Findings
- **Tuning forks.** Procedure, OPS commands (`US`/`UH`, `T=n`, `M>10`, `O3`) and the 0.9% result at 35 mph as in Q2. Forks above about 65 mph are hard to detect. — [OmniPreSense AN-026](https://omnipresense.com/wp-content/uploads/2025/03/AN-026-A_Speed-Accuracy-Using-Tuning-Fork-1.pdf)
- **Police practice.** Radar and forks are recertified by a certified shop once or twice a year, with records kept. — [search summary of police radar sources](https://copradar.com/chapts/chapt3/ch3d4.html)
- **Spin/speed wheel calibrator** with marked balls at a known radius, driven at a known speed. The patent frames portable monitors drifting when moved as the problem it solves. — [US20070135223A1](https://patents.google.com/patent/US20070135223A1/en)
- **Servo pre-spin air cannon** for controlled spin axis and rate. — [US7849725B1](https://patents.google.com/patent/US7849725B1/en)
- **Alignment kit.** Bliss & Langdown aligned the devices with plumb line, carpenter's square, spirit level and floor tape. — [PMC13227226](https://www.ebi.ac.uk/europepmc/webservices/rest/PMC13227226/fullTextXML)

### Inferences (engineering reasoning, not sourced)
- **Doppler chain beyond fork speeds.** Forks top out around 65 mph, which is about 4.7 kHz at 24.125 GHz; a 160 mph ball is about 11.5 kHz. Two ways to cover golf speeds:
  - A spinning target such as a bicycle wheel or drill-driven disc, with a reflector at radius r and a known RPM (optical tachometer). It gives v = ωr at golf speeds, but the geometry must be controlled because radial velocity follows a cosine.
  - Injecting a known audio-frequency tone into the IF chain, if the OPS exposes it. It does not.
- **Spin.** A drill or brushless motor turning a marked ball at 2,000–10,000 rpm, checked by an optical tachometer (±0.1% class), exercises both the OPS spin estimator and the camera spin estimator. This is precisely the class of test that would catch Mevo-style 2× seam errors.
- **Launch angle and azimuth.** Roll or slide a ball down a ramp at a known angle, set with the inclinometer, and read it with the camera and IWR. Put a laser line on the target line to check horizontal launch zero, and compare with the known camera and radar mounting yaw.
- **Timing and scale.** Drop a ball from a known height h: v = √(2gh), so 1 m gives 4.43 m/s. This tests camera frame timing and pixel scale. A pendulum-mounted clubhead swung from a known release angle gives a repeatable, known head speed at the bottom (minus losses) for checking club-path zero.
- **Face angle.** Photograph a static clubhead set at known face angles (0, ±2, ±5°) with a protractor or laser square, to check the camera face-angle estimator at rest. This isolates segmentation error (1 px ≈ 10°) from motion blur.
- **Impact location.** Impact tape or foot spray gives a physical per-shot record. Photograph it after each shot.

### Gaps
- No academic evaluation was found of hobby-grade spin fixtures, drop tests or pendulum rigs for launch monitors specifically. They are standard metrology practice, not golf literature.
- Whether the OPS243-A can detect a spinning-wheel reflector at golf speeds given its FFT and sample-rate settings is untested.

---

## Q6. Recommendations: how OpenFlight should pair its captures with a reference and score itself

### Takeaway
Adopt a three-layer program, and report every metric with its tier:
1. **Bench physical references**, run before every hardware revision: fork, spin fixture, static jigs.
2. **Concurrent comparator sessions** against TrackMan 4 as the primary comparator, with GCQuad as a secondary for face and impact where accessible.
3. **Own-criterion cross-checks.** Marked-ball camera for launch and spin; D-plane closure between face, path and launch.

Score each metric with:
- bias, SD of differences and 95% limits of agreement
- single-measure ICC, both consistency and absolute agreement
- MAE
- gross-error rate
- coverage (the fraction of shots with a reported value)

Pre-register pass thresholds calibrated to what Mevo+ achieves against TrackMan 4.

### Cited Findings (basis for the recommendations)
- The field's accepted statistics are Bland–Altman plus dual ICC, MAPE and Cohen's d, with a priori agreement limits. — [Bliss & Langdown](https://www.ebi.ac.uk/europepmc/webservices/rest/PMC13227226/fullTextXML)
- Comparator-class agreement bands for driver (Mevo+ vs TrackMan 4), as bias ± SD:
  - ball speed −1.5 ± 0.8 mph
  - launch angle −0.75 ± 0.84°
  - launch direction −0.3 ± 2.0°
  - club speed −0.4 ± 1.0 mph
  - club path −1.5 ± 2.35°
  - attack angle 1.2 ± 0.96°
  - face angle −0.24 ± 2.0°
  - spin −55 ± 513 rpm

  — [Bliss & Langdown](https://www.ebi.ac.uk/europepmc/webservices/rest/PMC13227226/fullTextXML)
- TrackMan's own noise (SEM) and dropout are non-trivial: club data was missing on 34% of PW shots and 13% of driver shots. — [Bliss & Langdown](https://www.ebi.ac.uk/europepmc/webservices/rest/PMC13227226/fullTextXML); [Bishop et al.](https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=%22launch%20monitor%22%20golf&resultType=core&format=json&pageSize=40)
- TrackMan publishes no accuracy tolerances, so "within X of TrackMan" is agreement, not accuracy. — [TrackMan tech specs](https://www.trackman.com/golf/launch-monitors/tech-specs)
- Definition differences create systematic club-metric bias of about 3° path and 1° attack on drivers. — [TrackMan](https://www.trackman.com/blog/club-data-definitions)

### Inferences: a concrete program

**1. Bench layer (per hardware or firmware revision; about $200)**
- **OPS243-A.** Test K-band forks at 2–3 speeds in `UH` (raw Hz) and `US` (mph) modes. Log the carrier setting (`T=n`). Pass criterion: within 0.5% after the carrier correction.
- **Spin fixture.** A marked ball on a motor with a tachometer at about 2,500, 5,000 and 8,000 rpm. Score the OPS spin and the camera spin separately. Report the gross-error rate: a factor of 2 or 0.5, which catches seam doubling.
- **Static geometry.** Inclinometer-set ramp launches at 10°, 20° and 30°, plus a laser target line, for vertical and horizontal launch zero on the camera and IWR.
- **Static face jig.** Known face angles to characterise the camera face-angle estimator at rest.
- **Replay bundles.** Save every bench run as a hash-verified bundle, as already supported, so later code changes can be re-scored deterministically.

**2. Comparator layer (per release gate)**
- **Protocol.**
  - At least 3 golfers (a single golfer is Bliss and Langdown's main weakness), or 1 golfer × 3 sessions as a minimum.
  - Driver, 7-iron and wedge.
  - At least 30 *paired, retained* shots per club per metric; the a priori power in Bliss & Langdown required 29.
  - Plan for about 50% attrition, so capture about 60 shots per club.
- **Setup.**
  - TrackMan 4 behind the ball at its recommended distance, with OpenFlight beside or in front and aligned by plumb line, square and laser to the same target line.
  - Record the comparator's normalisation setting, ball model and firmware.
  - Use the same ball for both devices (Pro V1-class). Record any marked or stickered balls.
- **Pairing.**
  - NTP-synced Pi clock; note the TrackMan start time.
  - Pair shots by timestamp with a cross-correlation offset on ball speed.
  - Reject pairs whose ball speeds differ by more than a gross threshold, and log each rejection.
  - Never pair by index alone.
- **Exclusions (pre-registered).**
  - Drop shots where the comparator reports an estimated value for *that metric*.
  - Drop mishits by a carry or smash threshold.
  - *Do not* drop shots where OpenFlight withheld a value. Count them against coverage instead.
- **Definitions.**
  - For each club metric, document OpenFlight's measurement point and moment.
  - Apply a documented CoG-offset correction before comparing path and attack with TrackMan. Also report the uncorrected numbers.
  - Compare face angle at the impact location, because TrackMan uses bulge- and roll-adjusted face angle.

**3. Scoring (per metric × club)**
- Report:
  - n paired
  - coverage (% of OpenFlight shots with a value)
  - bias
  - SD of differences
  - 95% limits of agreement
  - MAE
  - single-measure ICC(3,1) for consistency and ICC(2,1) for absolute agreement
  - gross-error rate (for example spin off by a factor of 2 or 0.5, launch more than 5° off)
- **Show bias separately from noise.** A stable bias is a calibration or definition issue, correctable with a published offset. The SD of differences is the real precision.
- **Pass thresholds (a starting proposal).** Match or beat the Mevo+ vs TM4 driver bands above for the first release gate:

  | Metric | Pass: SD of differences | Pass: \|bias\| | Other |
  |---|---|---|---|
  | Ball speed | ≤ 1 mph | ≤ 1.5 mph | — |
  | Vertical launch | ≤ 1° | ≤ 1° | — |
  | Horizontal launch | ≤ 2° | — | — |
  | Club speed | ≤ 1.5 mph | — | — |
  | Club path | ≤ 2.5° | — | — |
  | Attack angle | ≤ 1.5° | — | Note that Mevo+ fails on irons |
  | Spin (later gate) | ≤ 500 rpm (driver) | — | Gross-error rate < 2% |

  Each metric also needs coverage above a floor, for example 90%, so that withholding cannot buy accuracy.
- **Label every metric** "validated vs physical reference", "agreement vs TrackMan" or "self-consistency only". Keep ball speed, club speed, smash and vertical launch labelled as validated; they already have the TrackMan baseline.

**4. Own-criterion layer**
- **Marked-ball camera spin and launch.** Validate on the spin fixture first; it can then act as an internal reference for the OPS spin and IWR horizontal launch in every shot. This is a continuous accuracy monitor without TrackMan.
- **D-plane closure.** Predicted horizontal launch from the camera face and fused path should match the measured horizontal launch within about 2° on centre strikes. Wood 2018: launch lies 61–76% of the way from path toward face. Track the residual per session as a drift alarm.
- **Physical impact evidence.** Use impact tape or spray for impact location, and photograph it per shot as the reference for the later impact-location gate.

### Gaps
- No published consensus exists on pass thresholds per metric for launch monitors. The thresholds above are a proposal anchored to one single-golfer study.
- Whether TrackMan rental or fitter access allows raw per-shot export with timestamps was not verified.
- No peer-reviewed data exists for Full Swing KIT or Mevo Gen 2 as a comparator. If either becomes the secondary comparator, OpenFlight's program would effectively produce the first published agreement data for it.
