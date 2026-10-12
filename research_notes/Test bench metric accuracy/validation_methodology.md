# Validation methodology from mature measurement fields, applied to the OpenFlight test bench

Scope: practices from metrology (GUM, MSA, method comparison), automotive and aerospace sensor validation, ML evaluation and continuous benchmarking, sensor calibration in production, and crowdsourced data quality control. Each one is turned into a concrete recommendation for OpenFlight's existing bench. The bench already has deterministic replay, SHA-256 bundles, an attempt ledger, frozen criteria with hash-bound held-out selection, session-bootstrap error intervals and Wilson availability coverage (see `openflight-agent-review-workflow/docs/development/fusion-test-bench-research.md` TB01-TB16 and `fusion-master-plan.md`). That bench already cites NIST gauge R&R and Bland-Altman. The recommendations below focus on what is **missing or under-specified** relative to those docs. Effort labels: **S** = days of software, **M** = a week or two, or cheap hardware (<$100), **L** = significant hardware or a reference-device session. **$$$** marks practices that need expensive equipment.

Evidence-quality key: [Primary] = standard or peer-reviewed paper. [Secondary] = vendor/tutorial page summarising a standard. [Snippet] = only a search-result summary was available, not the full text.

---

## 1. Metrology: uncertainty budgets, MSA, and method comparison when the reference (TrackMan) itself has error

### Takeaway
Treat each OpenFlight metric as a measurement with its own uncertainty budget, not as "error vs TrackMan". The spread of differences against any comparator contains the comparator's own noise. So plan the comparison with (a) replicate or dual-reference designs that separate the two error sources, (b) absolute-unit agreement statistics (bias, limits of agreement with CIs, Deming/Passing-Bablok) rather than correlation or ICC, (c) analysis that respects shots nested within golfers and sessions, and (d) acceptance limits frozen *before* data collection. A separate repeatable physical artifact is needed for true gauge R&R, because a golf shot cannot be repeated.

### Cited Findings
**GUM (uncertainty budgets)**
- JCGM 100:2008 (GUM) splits uncertainty evaluation into Type A (statistical analysis of repeated observations) and Type B (any other information: calibration certificates, manufacturer specs, resolution, drift, expert judgement). Standard uncertainties are combined with sensitivity coefficients in root-sum-square to give the combined standard uncertainty u_c. The expanded uncertainty is U = k·u_c, with k typically 2 to 3 (k≈2 for ~95% coverage; strictly the Student-t quantile for the effective degrees of freedom). — [JCGM 100:2008 PDF](https://ncc.nesdis.noaa.gov/documents/documentation/JCGM_100_2008_E.pdf) [Primary]; [GUM §6 expanded uncertainty, ISO-hosted](https://www.iso.org/sites/JCGM/GUM/JCGM100/C045315e-html/C045315e_FILES/MAIN_C045315e/06_e.html) [Primary]; summary of budget practice in [CalProof guide](https://calproof.co.uk/guides/measurement-uncertainty-guide/) [Secondary]

**MSA / gauge R&R (AIAG)**
- AIAG MSA (4th ed.) acceptance: %GRR < 10% acceptable; 10-30% conditionally acceptable (documented approval, non-safety-critical, capability margin); ≥ 30% unacceptable. The number of distinct categories ndc = 1.41 × (part variation / GRR), truncated, must be ≥ 5, and both criteria must be met. — [QualityEngineer.ai summary of AIAG MSA](https://app.qualityengineer.ai/blog/gauge-rr-acceptance-criteria) [Secondary]; the dual requirement is also analysed in [Measurement (ScienceDirect) 2013, "Number of distinct data categories and gage R&R"](https://www.sciencedirect.com/science/article/abs/pii/S0263224113001760) [Primary, abstract only]
- MSA separates variation that shifts the mean (bias, linearity = bias as a function of magnitude, stability = bias drift over time) from variation that widens the spread (repeatability, reproducibility). Bias should not differ significantly from zero, and any lack of linearity or stability should be analysed and controlled or corrected. — [Measurement 2013 abstract](https://www.sciencedirect.com/science/article/abs/pii/S0263224113001760) [Primary, abstract]

**Method comparison when both methods have error**
- OLS regression of test on reference assumes the X method has no error. Even with a true reference, OLS standard errors are underestimated and CIs run too narrow. Deming regression allows error in both methods but needs the ratio of their error variances. Assuming equal variances is fine over a wide measuring interval, but "on a narrow range it reintroduces the very bias Deming exists to remove". Weighted Deming handles error that grows with magnitude (constant CV). Passing-Bablok is non-parametric, outlier-resistant and needs the fewest assumptions. Linearity and residuals should be checked before any fit. — [Analyse-it: choosing a regression for method comparison](https://analyse-it.com/learn/choosing-a-regression-for-method-comparison) [Secondary, written by a CLSI-software vendor]
- Passing-Bablok is reported to work well on highly correlated data but less well at lower correlation or when the two assays have different precisions. When Deming and Passing-Bablok agree that is reassuring, and when they diverge it is a signal to investigate. — [ConductScience method-comparison tool notes](https://conductscience.com/tools/method-comparison-analyzer) [Secondary]; primary tutorial: [Bilić-Zulle, Biochemia Medica 2011, Passing-Bablok](https://www.biochemia-medica.com/en/journal/21/1/10.11613/BM.2011.010/fullArticle) [Primary, not fetched]
- CLSI EP09 (3rd ed., EP09c, 2018) is the lab-medicine protocol for measurement-procedure comparison and bias estimation. It covers experiment design, scatter and difference plots, regression for quantifying the relationship, bias estimation at decision points, and claims reporting. — [CLSI EP09 page](https://clsi.org/shop/standards/ep09/) [Primary, catalogue text only]. A clinical SAP that follows EP09 uses ≥ 40 specimens per site across ≥ 3 sites (≥ 120 total) covering the measuring interval. — [ClinicalTrials.gov SAP NCT02897154](https://cdn.clinicaltrials.gov/large-docs/54/NCT02897154/SAP_000.pdf) [Snippet; this is one study's application of EP09, not the standard's own minimum]

**Bland-Altman specifics**
- Bland & Altman (1999, *Stat Methods Med Res* 8:135-160) extend limits of agreement (LoA) in several ways. They handle a difference that depends on magnitude (log transform, or regressing the difference on the mean). They compare each method's own repeatability with the LoA. They cover replicates (equal and unequal numbers) and paired replicates where the true value changes, and they give a non-parametric alternative. — [SAGE abstract](https://journals.sagepub.com/doi/10.1177/096228029900800204) [Primary, abstract]
- With multiple observations per subject, pairs from the same subject are not independent. The variance of differences splits into between-subject and within-subject parts, and treating repeated pairs as independent gives LoA that are too narrow. — [Bland & Altman, "Agreement between methods of measurement with multiple observations per individual" (White Rose eprint)](https://eprints.whiterose.ac.uk/id/eprint/3411/1/Bland_2006_Agreement.pdf) [Primary; fetched but summarised by a tool, so exact formulas were not verified]
- Lu et al. (2016, *Int J Biostat*) give a power and sample-size method for Bland-Altman studies. Agreement is concluded when the CI of the LoA falls inside predefined clinical agreement limits, and Type II error is controlled explicitly. The required n depends on α, β, the mean and SD of differences, and the predefined limit. — [De Gruyter abstract](https://www.degruyterbrill.com/document/doi/10.1515/ijb-2015-0039/html?lang=en) [Primary, abstract]; implemented in [MedCalc](https://www.medcalc.org/en/manual/sample-size-bland-altman.php) and [NCSS PASS](https://www.ncss.com/wp-content/themes/ncss/pdf/Procedures/PASS/Bland-Altman_Method_for_Assessing_Agreement_in_Method_Comparison_Studies.pdf) [Secondary]
- Approximate Bland-Altman CIs for the LoA can be unreliable at small n; exact methods are preferable. — [Appropriateness of approximate CIs for LoA, PMC5964973](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC5964973/) [Primary, snippet]
- A review of reporting standards for Bland-Altman agreement analyses (PMC7278016) exists and can serve as a reporting checklist. — [Reporting Standards for a Bland-Altman Agreement Analysis](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC7278016/) [Primary, not fetched]

**ICC**
- Koo & Li (2016): there are 10 forms of ICC with different assumptions, so the form (model, type, definition) must be reported. Interpretation: < 0.50 poor, 0.50-0.75 moderate, 0.75-0.90 good, > 0.90 excellent. — [Koo & Li 2016, PMC4913118](https://pmc.ncbi.nlm.nih.gov/articles/PMC4913118/) [Primary]

**Launch-monitor literature (what the comparator itself does)**
- TrackMan's official tech-spec page lists the metrics measured but publishes **no accuracy tolerances** for any metric. — [TrackMan tech specs](https://www.trackman.com/golf/launch-monitors/tech-specs) [Primary, fetched]
- TrackMan 4 within- and between-session reliability (Bishop et al., *J Sports Sci*, 10 driver shots per golfer, two sessions, high-level golfers): club head speed ICC 0.99 (SEM 1.64-1.67 mph); ball speed ICC 0.97-0.99 (SEM 2.46-4.42 mph); carry ICC 0.91-0.97. Spin rate was poorest (ICC 0.02-0.60; SEM 240.93-454.62, units printed as "º/s" in the abstract, probably rpm). — [Europe PMC record, DOI 10.1080/02640414.2024.2314864](https://www.tandfonline.com/doi/full/10.1080/02640414.2024.2314864) [Primary, abstract; the abstract's unit labels look garbled, e.g. carry SEM given in "mph"]
- Brennan et al. 2024 (*JSCR*): 29 youth golfers × 10 shots with driver and 6-iron, Mevo+ vs TM4 concurrently. Pearson r 0.254-0.985 (≥ 0.92 for club and ball speed). Bland-Altman agreement was best for smash factor (bias ≤ −0.016) and worst for spin (bias ≤ 1,238 rpm; 95% range −2,628 to 5,103). Mevo+ ICC −0.22 to 0.99 and CV 1.46-72.7%. TM4's own within-session ICC 0.60-0.99 and CV 1.31-230%. — [Europe PMC / DOI 10.1519/JSC.0000000000004685](https://pubmed.ncbi.nlm.nih.gov/38090982/) [Primary, abstract]
- Bliss & Langdown 2026 (*JSAMS Plus*): Mevo+ vs TM4 indoors, driver n=118 shots. Consistency ICC 0.66-0.996 for driver, 0.50-0.996 for 7-iron and 0.55-1.00 for PW. Angle of attack was poor (ICC 0.06 and 0.03 for 7-iron and PW). Driver swing-plane absolute-agreement ICC was 0.24. — [ScienceDirect, DOI 10.1016/j.jsampl.2025.100128](https://www.sciencedirect.com/science/article/pii/S2772696725000420) [Primary, abstract]

**Reference-vs-tolerance ratio (calibration decision rules)**
- ANSI/NCSL Z540.3 defines the Test Uncertainty Ratio (TUR) as the span of the tolerance divided by twice the 95% expanded uncertainty of the calibrating process. It allows TUR ≥ 4:1 as a fallback when a false-accept probability (PFA) ≤ 2% cannot be computed. Guard-banding sets acceptance limits inside the tolerance to hold PFA ≤ 2%. — [NCSLI Measure / Morehouse summary](https://mhforce.com/why-a-41-t-u-r-is-not-enough-the-importance-of-analyzing-the-probability-of-false-accept-risk/) [Secondary]; [American Gage on TUR and decision rules](https://www.americangage.com/blog/tur-tar-and-decision-rules) [Secondary]

### Inferences
- **The comparator is noisy, and its noise inflates OpenFlight's apparent error.** If differences d = OF − TM and the two instruments' errors are independent, Var(d) = σ²_OF + σ²_TM. TM4's between-shot SEM for ball speed (2.5-4.4 mph) and spin (~240-450) in Bishop et al. mixes golfer variability with instrument noise. That means **published sports-science "reliability" is not instrument repeatability** and cannot be used directly as TM4's Type B uncertainty. With TM4 publishing no tolerances, OpenFlight has no documented TUR for any metric. For spin, launch angle and attack angle, where even TM4 vs Mevo+ agreement is poor, the reference may be too noisy to validate tight tolerances at all.
- **R1. Per-metric GUM uncertainty budget file (S).** Add a machine-readable budget for each reported metric (ball speed, club speed, VLA, HLA, club path, AoA, spin) that lists input quantities with Type A/B, sensitivity coefficients and the source of each value. Examples for ball speed:
  - OPS243 carrier frequency tolerance
  - FFT bin width at 30 kS/s
  - cosine error from the unknown line-of-sight angle, which is a function of launch angle and the measured radar origin
  - timing-window choice
  - For VLA: IWR angle resolution, mount tilt from the LIS3DH, and the registration offset (+6.18° / +5.19° measured on the August rig).

  Report U (k=2) next to every number in the review UI. Doing this also shows which input dominates, which is where engineering effort should go. It fits TB10 (same-quantity contracts) and TB13.
- **R2. Freeze acceptance criteria as agreement limits before collection (S).** Lu et al. style: for each metric, state the clinically meaningful agreement limit Δ (for example ball speed ±1 mph, VLA ±1°). Success means the 95% CI of both LoA falls inside ±Δ. Use Lu's method to compute the n that gives 80% power to show it. TB13 already has "frozen criteria"; what is missing is (a) the numerical Δ per metric, (b) the power calculation, and (c) the rule that success is the *CI of the LoA*, not the point LoA.
- **R3. Report absolute agreement, not correlation or ICC, as the headline (S).** For each metric report bias with 95% CI, LoA with exact CIs, proportional bias (regress difference on mean per Bland & Altman 1999), and Deming *and* Passing-Bablok slope/intercept. ICC (form stated per Koo & Li) should be secondary only. The golf literature shows why: TM4's own spin ICC ranges 0.02-0.60 purely from sample homogeneity (driver spins vary little between golfers). ICC rewards heterogeneous samples and is not an accuracy measure.
- **R4. Handle clustering (S).** Shots are nested in golfer × session × rig. Use the Bland & Altman multiple-observations LoA (or a mixed model) and bootstrap by session or golfer (TB13 already bootstraps by session, which is good). Report the effective n. 10 shots × 29 golfers is not n=290 independent pairs.
- **R5. Estimate the reference's own error with a dual-reference or replicate design (L, needs comparator access).** Options:
  - (a) Run two comparators at once (for example TM4 + Mevo Gen 2 + OpenFlight). A three-instrument design lets each instrument's error variance be estimated without a gold standard (Grubbs-type / "three-cornered-hat" estimators; not sourced in this pass, see Gaps).
  - (b) Record TM4's own repeatability against an artifact (see §4).
  - (c) Use the within-comparator variance to set the Deming error ratio λ instead of assuming 1. The Analyse-it guidance warns that assuming λ=1 on a narrow range (a single club's ball-speed band is narrow) reintroduces bias.
- **R6. Linearity and stratified bias (S).** MSA "linearity" here means bias as a function of speed, club and launch. Compute bias per speed band and club and fit bias vs magnitude. Radar cosine error and camera blur both scale with speed, so proportional bias is expected and correctable (a calibration line), while random error is not.
- **R7. Guard-banded pass/fail (S).** When a per-shot or per-session check has a tolerance T and known uncertainty U, set the acceptance limit at T − guard band, so a marginal result isn't reported as "validated" (Z540.3 logic). The bench's "accepted / experimental / rejected" statuses can map to guard-banded zones.
- Evidence quality is good for the statistics (primary standards and papers) and moderate for golf-specific numbers (abstracts only).

### Gaps
- Did not obtain the EP09c full text, so the standard's own minimum sample counts (commonly quoted as ≥ 40 for labs and ≥ 100 for manufacturer claims) and duplicate-measurement requirements are **unverified** here.
- No primary source was fetched for Grubbs / three-cornered-hat estimation of each instrument's precision from three simultaneous instruments. It is well established in time-frequency metrology but should be sourced before it is relied on.
- The exact multiple-observation LoA formulas from Bland & Altman 2007 were not verified verbatim (tool summary only).
- No published accuracy specification from TrackMan, Full Swing or FlightScope (Mevo Gen 2) was found. Independent agreement studies exist only for Mevo+ vs TM4, not for Mevo Gen 2 or Full Swing KIT.

---

## 2. Automotive ADAS / AV and aerospace: re-simulation, HIL/SIL, scenario catalogues, KPI dashboards, ground-truth rigs

### Takeaway
OpenFlight's deterministic replay is already the AV industry's core practice ("resim" or open-loop log replay). The transferable parts are organisational rather than hardware:
- a tagged scenario/condition catalogue
- a SOTIF-style catalogue of triggering conditions, where every failure becomes a "known" condition with a regression recording
- per-condition KPI breakdowns
- a dedicated ground-truth instrument whose accuracy is an order of magnitude better than the system under test

Target simulators, CATR chambers and RTK-GNSS/INS reference vehicles are **$$$**, but they have cheap analogues.

### Cited Findings
- Resimulation ("recompute", "data reprocessing", "data replay") replays recorded sensor data through the processing graph to validate AD functions. It is a large parallel compute job, and heterogeneous streams (sensor plus bus data) must be re-synchronised during replay. — [Microsoft ValOps reference architecture](https://learn.microsoft.com/en-us/industry/mobility/architecture/autonomous-vehicle-validation-operations-content) [Secondary, vendor architecture doc]
- Open-loop testing replays predefined inputs with no feedback. Engineers use open-loop log replay to play back disengagement events and fix perception or localisation. Closed-loop re-simulation lets the stack's outputs affect the simulated world. — [Applied Intuition: closed-loop log replay](https://www.appliedintuition.com/blog/closed-loop-log-replay) [Secondary, vendor blog]
- The most common ground-truth reference for ADAS is a GNSS-aided inertial navigation system (survey-grade GNSS + IMU) giving cm-level position accuracy. — [OxTS open-road ground-truthing](https://www.oxts.com/industry/open-road-groundtruthing/) [Secondary, vendor] ($$$)
- Dedicated reference sensor platforms are built to provide reference data for validating ADAS cameras. — [MDPI Sensors 25(6):1690, "Reference Platform for ADAS Camera System Evaluation"](https://www.mdpi.com/1424-8220/25/6/1690) [Primary, snippet only]
- ISO 21448 (SOTIF) sorts scenarios into four classes: known/unknown × hazardous/not hazardous. The process defines intended functionality and the operational design domain (ODD) and identifies **triggering conditions**. Verification shows requirements are met on known hazardous scenarios. Validation *explores* for unknown hazardous scenarios, which then become known ones. The goal is to shrink the unknown-hazardous set. — [arXiv 2308.02273, SOTIF scenario-generation SLR](https://arxiv.org/pdf/2308.02273) [Primary, snippet]; [dSPACE on SOTIF](https://www.dspace.com/en/inc/home/news/engineers-insights/navigating-the-road-to-iso-214.cfm) [Secondary]
- The Waymo Open Dataset stratifies ground truth by difficulty: LEVEL_1 objects have > 5 lidar points, and LEVEL_2 objects have 1-5 points or are manually flagged hard. Metrics are reported per level. Labels come from experienced annotators with multiple verification phases. — [Waymo Open Dataset paper, arXiv 1912.04838](https://ar5iv.labs.arxiv.org/html/1912.04838) [Primary, snippet]
- Production radar end-of-line (EOL) testing uses compact antenna test ranges (a parabolic reflector creating a plane wave in a small footprint) with integrated radar target simulators. The simulators imprint synthetic targets (range, Doppler) onto the returned wave inside an absorber chamber. — [dSPACE EOL CATR system](https://www.dspace.com/en/pub/home/products/hw/darts/eol_catr_system.cfm) [Primary vendor]; [Electronic Specifier on automated radar EOL](https://www.electronicspecifier.com/products/sensors/automated-validation-and-calibration-of-radar-sensors-in-production/) [Secondary] ($$$)
- Scenario-based ADAS testing uses standard formats (ASAM OpenSCENARIO, Open Simulation Interface) to define scenario catalogues. — [SciTePress 2019, scenario-based ADAS testing with OSI](https://www.scitepress.org/Papers/2019/78383/78383.pdf) [Primary, snippet]

### Inferences
- **R8. Condition/scenario catalogue with mandatory tags (S).** The OpenFlight equivalent of an ODD is the set of conditions under which accuracy is claimed. Every bundle should carry controlled-vocabulary tags:
  - club (D / FW / iron number / wedge)
  - handedness
  - ball speed band
  - lux band (from logged exposure/gain)
  - camera mode (1280×800 / 640×400 / 320×200)
  - surface (mat / grass)
  - ball type (marked / unmarked)
  - rig version
  - indoor / outdoor
  - net distance

  Accuracy is then only *claimed* within cells that have enough reference-matched samples. Cells with none are explicitly "outside ODD". This goes beyond TB14's blocked mode screen and makes the claim scope visible.
- **R9. Triggering-condition registry, SOTIF style (S).** Keep a registry of known triggering conditions, for example saturated resting ball, low light at 75 µs exposure, dark club head, sun glint, false acoustic trigger from an adjacent bay, IWR MTI notch near-tee clutter, and left-handed golfer mirror. Each entry links to at least one frozen regression bundle that reproduces it, plus the status of the mitigation. Every rejected attempt in the review ledger should either map to a registry entry or open a new one. That is the SOTIF "unknown becomes known" loop. The existing tester field-issues log is the seed.
- **R10. Difficulty levels à la Waymo (S).** Tag each shot with an automatically computed difficulty, for example radar SNR, blur length in pixels, ball-peak saturation fraction, and IWR point count. Report metrics per level so an estimator change that helps easy shots but hurts hard ones is visible.
- **R11. Processor-in-the-loop replay on the Pi (M).** The AV "HIL" analogue: replay raw bundles *on the actual Raspberry Pi 5* at real-time pacing, through the live acquisition-to-estimator path (not just the offline estimator), and measure latency, dropped frames and CPU. This targets the open TB09/TB11 gaps (asynchronous acquisition equivalence, diagnostics not degrading acquisition), which offline deterministic replay cannot close. A true radar target simulator is $$$ and unnecessary.
- **R12. Dedicated ground-truth rig distinct from the comparator (M-L).** AV teams separate the *reference system* (RTK-INS, survey lidar) from *comparator products*. For OpenFlight that means low-cost reference instruments with traceable accuracy for individual quantities, independent of TrackMan:
  - light gates (two IR beam breaks a known distance apart, timed on one clock) for ball speed and launch angle near the tee
  - a high-fps reference camera with a checkerboard-calibrated side view for VLA and AoA
  - marked-ball stationary spin references

  These are cheaper than TM4 and have computable Type B uncertainty, unlike TM4. Flag: most of these are only valid over the first ~0.5 m of flight.
- **R13. KPI dashboard per commit (S-M).** See §3. The AV practice is per-KPI, per-scenario-cell trend lines after every resim run.

### Gaps
- No primary Waymo/Bosch/Continental paper describing their sensor-validation *statistics* (as opposed to dataset labelling) was found in this pass. Evidence for AV practice here is mostly vendor/architecture material (moderate quality).
- No sourced aerospace (DO-178C/DO-254, flight-test instrumentation) material was gathered, so the aerospace side is thin.
- The light-gate and side-camera reference accuracies proposed in R12 are engineering suggestions and have not been characterised.

---

## 3. Continuous benchmarking and ML evaluation: frozen test sets, versioning, leakage, stratification, CI regression gates

### Takeaway
Because OpenFlight's replay is deterministic, estimator "noise" between two code versions is zero on the same bundles. So regression should be judged with **paired** comparisons on the same frozen shots, which are far more sensitive than the unpaired t-tests used for noisy performance benchmarks. Beyond the held-out partitioning the bench already has, the main risks are leakage through tuning on evaluation sessions, and distribution shift (new rigs, testers, lighting). Adaptive reuse of a test set turns out to be the smaller risk.

### Cited Findings
- Kapoor & Narayanan (*Patterns*, 2023) found data leakage in 17 fields affecting 294 papers, sometimes with "wildly overoptimistic conclusions". They give an 8-type leakage taxonomy (from textbook errors to open problems) and propose "model info sheets" to test for each type. — [arXiv 2207.07048](https://arxiv.org/abs/2207.07048) [Primary]
- Recht et al. (ICML 2019) rebuilt CIFAR-10 and ImageNet test sets following the original process. Accuracy dropped 3-15% (CIFAR-10) and 11-14% (ImageNet), but the drops were attributed **not to adaptive overfitting** from test-set reuse but to failure to generalise to slightly "harder" images, which is distribution shift. Gains on the old test set carried over to the new one (factor 1.7 on CIFAR-10, 1.1 on ImageNet). — [PMLR v97 recht19a](https://proceedings.mlr.press/v97/recht19a.html) [Primary]
- Gebru et al., "Datasheets for Datasets": 57 questions in 7 categories (motivation, composition, collection process, preprocessing/cleaning/labelling, uses, distribution, maintenance), modelled on electronic-component datasheets. — [arXiv 1803.09010](https://arxiv.org/abs/1803.09010) [Primary]
- DVC stores data in a content-addressable cache (files keyed by hash, deduplicated), with lightweight pointer files tracked in Git. Metrics are key/value files, and `dvc metrics diff` shows each metric's value and delta between commits and experiments. — [DVC glossary](https://doc.dvc.org/user-guide/glossary) [Primary docs]; [dvc metrics diff](https://dvc.org/doc/command-reference/metrics/diff) [Primary docs]
- Mozilla's Perfherder flags performance regressions with a Student's t-test. 12.5% of alert groups were false positives, and about 6.8% contained regressions the system missed. A study evaluated 25 change-point detection methods and 15 ensembles as alternatives. — [arXiv 2606.18377](https://arxiv.org/html/2606.18377) [Primary, snippet]
- MongoDB's Hunter uses the E-Divisive means change-point algorithm on performance histories, with a configurable p-value (for example 0.001) and a minimum magnitude threshold to limit false positives. — [arXiv 2301.03034](https://arxiv.org/pdf/2301.03034) [Primary, snippet]; [ACM/SPEC ICPE 2020 paper](https://dl.acm.org/doi/10.1145/3358960.3375791) [Primary]

### Inferences
- **R14. Paired regression gate in CI (S).** For each PR, replay the frozen dev-regression set with base and head. For each metric and each condition cell, compute the per-shot paired difference in |error vs reference|, or in output value where no reference exists. Gate on a paired bootstrap CI or a sign/Wilcoxon test plus a practical-significance threshold (for example median |error| worsens by > 0.2 mph, or > 5% of shots change status). Also gate on **availability** (no-read count), so an estimator can't "improve" accuracy by rejecting hard shots. This matches TB13's availability-plus-uncertainty principle.
- **R15. Three-tier data partition with access control (S).**
  - (a) *dev/regression*: free to inspect and tune on.
  - (b) *validation*: used for model/threshold selection, reported but not published as the accuracy claim.
  - (c) *sealed test*: reference-matched sessions, results revealed only at release milestones, with a count of how many times the set has been queried.

  Partition by **rig and tester and day**, not by shot. Recht's result says the bigger danger is shift, so keep at least one entire rig or tester as a "leave-one-rig-out" test. The master plan already partitions by session/day; adding rig/tester grouping and a query counter is the delta.
- **R16. Leakage checklist per estimator change (S).** Adapt Kapoor & Narayanan's model info sheet into a PR template:
  - Were thresholds or constants fitted on sessions in the test tier?
  - Did the fit use reference values, for example tuning radar windows against TM data from the same shots?
  - Does preprocessing use statistics computed across the whole session, including test shots (session-wide normalisation)? The master plan mentions "session-wide normalized candidates", which is a potential leakage path if the normalisation sees reference-labelled test shots.
  - Are the same golfer's shots in both tuning and test?
- **R17. Per-metric error time series with change-point detection (S-M).** Store each CI run's per-metric, per-cell summary (bias, SD of differences, p95 |error|, availability) as a versioned metrics file (DVC-style `metrics diff`, or a JSON history in the repo). Run E-Divisive or similar over the history to catch slow drift that no single PR gate trips. Because replay is deterministic, any change in results on the frozen set comes from code, config or calibration. A change-point tells you which commit.
- **R18. Dataset datasheet per contribution release (S).** Generate a Gebru-style datasheet automatically from bundle metadata (rig geometry provenance, mode, lux, consent scope, known limitations such as "August rig unmeasured") and ship it with every published benchmark snapshot.
- **R19. Hash-pinned evaluation manifests (S).** The bench already SHA-256-inventories bundles. Also hash the *evaluation manifest* (list of bundle hashes + criteria version + reference-match file) and record that hash in every reported accuracy number, so a claim can always be re-derived. TB07 covers processing identity but not claim identity.

### Gaps
- No golf- or sensor-specific literature on regression gating was found; the recommendations are adapted from software-performance and ML practice.
- The Mozilla study's final recommendation (which CPD method won) was not read in full.

---

## 4. How sensor vendors calibrate and validate in production, and low-cost analogues

### Takeaway
Vendors calibrate against **physical artifacts with known, traceable values**: tuning forks for Doppler radar, target simulators and corner reflectors for FMCW radar, multi-position tumble tests for accelerometers, and checkerboards for cameras. They then re-check those artifacts periodically: start and end of shift for police radar, with certified recalibration once or twice a year. OpenFlight can copy the pattern cheaply with a spinning-reflector Doppler artifact, a corner reflector, a 6-position LIS3DH tumble and a pre/post-session optical check target. These give the repeatable "parts" that a gauge R&R study needs and that golf shots cannot provide.

### Cited Findings
- Police Doppler radar is checked with a calibrated tuning fork held in front of the antenna. The fork's vibration produces a Doppler return the gun reads as a known speed. Example: at 10.525 GHz, a 50 mph check point uses a fork vibrating at 1,669.54 Hz. Fork frequency was historically verified against NBS time/frequency broadcasts. — [NIST (NBS) "Calibration of Police Radar Instruments"](https://tf.nist.gov/general/pdf/87.pdf) [Primary, snippet]; [OJP abstract of 1975 NCWM report](https://www.ojp.gov/ncjrs/virtual-library/abstracts/calibration-police-radar-instruments-report-60th-national) [Primary, snippet]
- Operational practice: a full tuning-fork test at the start of each shift, with each antenna tested separately. Internal self-tests (including transmit frequency and supply voltage) run at power-up and automatically every 5-15 minutes. Radar and forks are calibrated by a certified shop once or twice a year. — [copradar.com Speed Radar Test and Calibration](https://copradar.com/chapts/chapt3/ch3d4.html) [Secondary, practitioner reference]. Snippets also mention a 36-month NHTSA-recommended testing interval and start/end-of-shift fork tests. — [CHP HPM 100.4 ch.5](https://www.chp.ca.gov/siteassets/policy/hpm/hpm-100.4/hpm-100.4-ch-5.pdf) [Snippet; not verified]
- Automotive radar EOL: compact-antenna-test-range chambers with radar target simulators (**$$$**). Dihedral/corner reflectors are standard calibration targets, and their misalignment effects are an active research topic at 77 GHz. — [dSPACE EOL CATR](https://www.dspace.com/en/pub/home/products/hw/darts/eol_catr_system.cfm) [Primary vendor]; [Cambridge IJMWT, dihedral misalignment](https://www.cambridge.org/core/journals/international-journal-of-microwave-and-wireless-technologies/article/impact-of-misalignments-with-rotatable-dihedral-calibration-target-in-mmwave-polarimetric-mimo-automotive-radar/02E49E6D90C0263AC638F6820A0132A6) [Primary, snippet]; [Rohde & Schwarz radar production testing](https://www.rohde-schwarz.com/us/solutions/automotive-testing/automotive-radar/automotive-radar-production-testing/automotive-radar-production-testing_254600.html) [Primary vendor]
- Accelerometers are typically calibrated with a multi-position static "tumble" test (six or more orientations) that estimates bias, scale factor and axis non-orthogonality/misalignment against gravity. More advanced schemes also fit non-linear scale factors. — [VectorNav: calibration and characterization of IMUs](https://www.vectornav.com/resources/inertial-navigation-primer/specifications--and--error-budgets/specs-imucal) [Primary vendor]; [PMC6718990, optimized multi-position calibration with nonlinear scale factor](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC6718990/) [Primary]

### Inferences
- **R20. Doppler "tuning fork" for the OPS243 (M, < $50).** Compute the OPS243's expected reading from f_d = 2v/λ with λ ≈ 12.4 mm at 24.125 GHz, so ~1 kHz corresponds to ~6.2 m/s. The artifact is a rotating reflector (a metal tab or small corner reflector on a drill, fan or bicycle wheel at radius r, with rpm measured by an optical tachometer or by the OV9281 itself). It gives a known tangential speed v = 2πr·rpm/60 across the golf range, so 40-80 m/s needs high rpm or a large radius; flag the safety issue. A loudspeaker-driven plate or a real tuning fork covers the low-speed end. Used as (a) a pre/post-session check (police-radar shift practice), which feeds TB03 stability, and (b) the repeatable "part" for OPS repeatability and linearity. The *reference value* comes from rpm and radius, both with easy Type B uncertainty.
- **R21. Corner-reflector angle artifact for the IWR6843 (M, < $50).** A trihedral corner reflector at surveyed positions (tape plus laser level) across the field of view gives known azimuth/elevation and range. Use it for the per-session registration check (the ~5° IWR-vs-camera offsets measured earlier) and as a stability chart over time. A slowly moving reflector (pendulum or linear rail) adds Doppler.
- **R22. LIS3DH 6-position tumble at commissioning (S).** Fit bias, scale and misalignment per unit and store them in the rig file with the date. Re-run on a schedule to track drift. This directly supports the tilt-correction path and the "tilt double-count" risk noted in project history.
- **R23. Pre/post-session optical check target (S-M).** A printed target at a known pose (or the resting ball as the calibration object, which the project already uses) captured at session start and end. Change beyond a threshold flags the session as "setup moved" (TB03). This is the vendor EOL "golden unit" idea applied per session.
- **R24. Control chart per artifact (S).** Plot each artifact reading over time (Shewhart / EWMA) with limits from its first N readings. That is MSA *stability*. Drift beyond limits blocks sessions from the sealed-test tier.
- **R25. Gauge R&R design on artifacts, not shots (M).** Parts = artifact settings (for example 5-10 rotor speeds or reflector positions), operators = testers or rigs, and 2-3 trials each. Compute %GRR against the tolerance and ndc per AIAG. This answers "can the instrument distinguish speeds 1 mph apart?" separately from golfer variability. For AoA, face and path, no cheap artifact exists; a swing robot (Iron-Byron-type) or pendulum club is the vendor/USGA-grade answer and is **$$$** (unsourced here).

### Gaps
- No OEM application note (TI for the IWR6843, OmniPreSense for the OPS243, OmniVision/Arducam for the OV9281, ST for the LIS3DH) was retrieved. Vendor-specific EOL calibration procedures, such as TI's antenna phase/gain compensation with a corner reflector at a known range (believed to exist in TI mmWave SDK documentation), should be sourced directly.
- The NHTSA/IACP numerical radar accuracy spec (commonly cited as about ±1 mph) was not found in fetched material.
- No sourced cost or availability data for golf swing robots or pendulum rigs.

---

## 5. Crowdsourced / community data: quality control, consent, and keeping bad data out of benchmarks

### Takeaway
Mature crowdsourced sensing networks (for example citizen weather stations) use *layered, automated* QC:
1. metadata checks
2. per-time-step distribution outlier checks
3. validity-fraction thresholds
4. correlation with the network
5. neighbour ("buddy") checks

They evaluate the QC itself against professional reference stations and accept that aggressive QC discards most data (CrowdQC+ kept about 21-30%). For OpenFlight, community packages should feed development and robustness tiers, with automated QC levels and contributor trust. Only reference-matched, reviewed sessions should ever enter the sealed accuracy benchmark.

### Cited Findings
- Citizen-science QC approaches fall into three stages: before collection (training), during (filtering on plausibility, data-entry constraints, real-time outlier detection) and after (bias correction, expert review). — [Kosmala et al. 2016, "Assessing data quality in citizen science", Front Ecol Environ](https://esajournals.onlinelibrary.wiley.com/doi/10.1002/fee.1436) [Primary, snippet]
- CrowdQC+ (Fenner et al. 2021) runs sequential QC levels:
  - m1: metadata (duplicate or invalid coordinates)
  - m2: distribution outliers at each time step using robust median/quantile statistics
  - m3: a station is invalid if more than 20% of its values fail m2
  - m4: temporal correlation with the network median (catches indoor installations)
  - m5: spatial buddy check against neighbours
  - optional o1-o4: interpolation, ≥ 80% daily availability, validity windows, sensor time-constant correction

  Data retained in Amsterdam: 92.0% after m1, 69.2% after m5, 20.7% after o3 (Toulouse final 29.5%). The QC was **evaluated against professional reference stations** (24 with < 0.5 K accuracy in Amsterdam, 38 with 0.3 K in Toulouse), comparing crowd stations within 2 km in the same local-climate-zone type using MD, MAD, RMSD and centred RMSD. — [Frontiers in Environmental Science 2021, CrowdQC+](https://www.frontiersin.org/journals/environmental-science/articles/10.3389/fenvs.2021.720747/full) [Primary, fetched]
- Community trust in a contributor can be computed by aggregating direct and inferred trust with role, expertise, quality of past data and contribution history. Low-trust data can then be filtered. — [Alabri & Hunter, "Enhancing the Quality and Trust of Citizen Science Data", TCDL Bulletin 2010](https://bulletin.jcdl.org/Bulletin/v6n2/Alabri/alabri.html) [Primary, snippet]; [Hunter et al. 2013, Concurrency & Computation](https://onlinelibrary.wiley.com/doi/full/10.1002/cpe.2923) [Primary, snippet]
- Citizen-science data programmes face explicit tradeoffs among data quality, privacy, transparency and trust. — [Biological Conservation 2019, "Tradeoffs and tools for data quality, privacy, transparency, and trust in citizen science"](https://www.sciencedirect.com/science/article/abs/pii/S0006320719301958) [Primary, abstract]

### Inferences
- **R26. OpenFlight QC levels for contribution packages (S-M), mirroring CrowdQC+:**
  - **Q1 metadata/provenance**: bundle hashes verify; rig geometry is present and measured (not assumed; the "August rig unmeasured" lesson); firmware and config are known; consent scope is present.
  - **Q2 per-shot plausibility**: smash factor within physical bounds per club; ball speed vs club speed consistent; timing contract (contact before trigger) satisfied.
  - **Q3 per-session validity fraction**: flag the session if more than X% of shots fail Q2.
  - **Q4 setup stability**: pre/post artifact check (R23/R24) within limits; LIS3DH tilt stable.
  - **Q5 "buddy" check**: the session's per-club distributions (ball speed, VLA, smash) compared with the pooled community distribution for that club and speed band. A rig whose 7-iron VLA runs 5° low relative to its ball speed is suspect, like an indoor weather station.

  Report retention per level. As with CrowdQC+, expect low retention and accept it.
- **R27. Evaluate the QC itself against reference sessions (M).** CrowdQC+'s credibility comes from checking QC outcomes against professional stations. OpenFlight's analogue: run the QC pipeline on the reference-matched sessions and show that sessions passing QC have smaller error vs reference than those that fail. Without this, QC thresholds are just opinions.
- **R28. Tiered use of community data (S).** Community data can inform (a) availability and robustness statistics, (b) triggering-condition discovery (R9), and (c) self-consistency checks. It cannot establish *accuracy*, because there is no reference. Community sessions enter the sealed accuracy tier only if they include a qualified comparator export, pass Q1-Q5, and are reviewed. Label every reported number with the tier it came from.
- **R29. Contributor trust score (S).** Track per-contributor history: fraction of packages passing QC, provenance completeness, and agreement with the buddy distribution. Use it to prioritise review, not to silently weight data. Silent weighting is hard to audit; keep review decisions explicit, as the bench's accepted/experimental/rejected statuses already do.
- **R30. Consent and privacy scoping (S).** Keep consent machine-readable per package (already consent-bound) and include it in the datasheet (R18). Camera frames can identify people or places, so benchmark releases could default to derived features plus hashes, with raw frames only under a narrower consent scope. This follows the privacy-vs-transparency tradeoff literature. It is an inference; the specific policy is not sourced.

### Gaps
- No literature was found on crowdsourced validation of *measurement devices* specifically, as opposed to environmental sensing networks. The golf/sports-tech analogue is undocumented.
- The Kosmala 2016 and Alabri/Hunter papers were available as snippets only.
- No sourced guidance was found on consent frameworks for community sensor data beyond general citizen-science ethics (for example the scoping review at [arXiv 2007.09416](https://arxiv.org/pdf/2007.09416), not read).
