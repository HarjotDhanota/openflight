# OpenFlight Club-Pose and Impact-Location Research Survey

**Prepared:** August 25, 2026  
**Purpose:** Literature and industry survey for a markerless, open-source DIY golf launch monitor  
**Scope:** Monocular clubhead pose, golf-shaft mechanics, D-plane inversion, modern iron geometry, and commercial behind-ball launch monitors

## 1. Executive conclusion

OpenFlight's proposed sensor-fusion architecture is more defensible than further silhouette-only CAD fitting, but its three intended outputs do not have equal prospects:

- **Face angle:** potentially recoverable as a coarse, physics-conditioned estimate—probably degree-scale, not yet demonstrated sub-degree—if horizontal ball launch and true 3D club path are independently measured.
- **Dynamic loft:** harder, because it additionally requires attack angle and a shaft/head model. A coarse estimate may be possible, but iron-specific shaft evidence is weak.
- **Impact location in millimetres from face centre:** not supported under the present constraints. Range, shaft projection, and D-plane inversion do not independently locate the face centre. Canonical-head geometry errors alone are several millimetres, and strike location affects the same impact physics proposed to infer face orientation.

The central limitation is **identifiability**, not optimizer quality. A more sophisticated optimizer can return a stable, tightly regularized answer without the image or radar actually determining that answer.

## 2. OpenFlight system and measured operating conditions

The system uses:

- One monochrome global-shutter OV9281 camera behind the ball, looking downrange.
- Camera distance approximately 1.5 m.
- 320×200 resolution at 467.6 frames/s.
- TI IWR6843 mmWave radar.
- OPS243 Doppler radar.
- Raspberry Pi processing.

Measured over a 22-shot TrackMan-referenced session:

- Plate scale at the ball: **0.296 px/mm**, or **3.38 mm/px**.
- Clubhead width: approximately **20–40 px**.
- Clubhead-mask area: **125–880 px**.
- Exposure: **247–298 μs**.
- Clubhead motion blur: **2.97 px**, approximately 10 mm at the ball plane.
- Clubhead displacement between frames: **22.8 px**, approximately 77 mm.
- Illumination: ambient evening outdoor light only.
- Radar selected the clubhead range bin on 22/22 shots, using **4.69 cm range bins**.
- Radar angular recovery failed on 22/22 shots because the phase span over roughly four pre-impact frames was too small.

Hard constraints:

- No strobe or active illumination.
- No club or ball markers/stickers.
- Behind-ball camera only; no side or overhead view.
- Raspberry Pi execution.
- Commercial comparators: TrackMan 4, Full Swing KIT, and FlightScope Mevo Gen2.

## 3. Findings already established experimentally

OpenFlight has already fitted an iron CAD mesh to tracked club silhouettes by maximizing intersection-over-union:

- Tracking succeeded on 349/349 frames.
- The original fitting run contained a handedness error: horizontally mirrored capture frames from a right-handed golfer were compared with a right-handed CAD mesh.
- Correcting the mirror raises IoU from 0.548 to 0.633 on all 21 tested shots and collapses the formerly reported pair of basins to one.
- Recovered orientation nevertheless remains unusable: about one fifth of adjacent frames still differ by more than 45°, and the surviving optimum is a broad 60–70° basin.

The corrected experiment therefore removes the earlier apparent second mode, but it does not restore useful orientation observability. Candidate toe, heel, and hosel extrema remain unstable, while the projected shaft axis is much more coherent.

These measurements show that the silhouette reliably locates the moving club region but does not encode usable clubhead orientation at OpenFlight's scale.

---

## A. Region- and contour-based monocular 6-DoF tracking

### Established

The relevant lineage is approximately:

1. Tjaden et al.'s region-based Gauss–Newton tracker and RBOT benchmark.
2. RBGT, a sparse Gaussian region tracker.
3. SRT3D, a refined sparse probabilistic contour tracker.
4. ICG, which adds depth.
5. ICG+, Mb-ICG, and M3T, which add texture, multiple modalities, bodies, or cameras.

The maintained [DLR tracking repository](https://github.com/DLR-RM/3DObjectTracking) documents this relationship. For OpenFlight, **SRT3D—not ICG—is the closest match**, because ICG's strongest results fuse RGB regions with depth.

[SRT3D](https://link.springer.com/article/10.1007/s11263-022-01579-8) uses approximately 200 contour samples from one of 2,562 pre-rendered views, probabilistic foreground/background correspondence lines, seven pose-refinement iterations, Newton optimization, and Tikhonov regularization.

Reported runtime:

- 1.1 ms/frame without modeled occlusion.
- 5.1 ms/frame while jointly tracking two objects with occlusion handling.
- One Intel Xeon E5-1630 v4 CPU core; standard tracking did not require the listed Quadro P600 GPU.

This is computational throughput—roughly 900 updates/s in the simplest case—not the benchmark camera rate and not evidence that the tracker converges under an entire-object-width displacement per frame.

### What rotational accuracy is actually reported?

The main RBOT success condition is:

\[
e_t < 5\text{ cm}, \qquad e_R < 5^\circ.
\]

After a failure, the tracker is reinitialized using ground truth. Thus the success percentage answers whether tracking remained within a coarse 5 cm/5° boundary. It does **not** establish median angular error, sub-degree accuracy, or uninterrupted sequence reliability.

RBOT frames are 640×512; OPT uses substantially larger images. Neither the SRT3D nor RBOT results stratify orientation error by projected object size in pixels. Their success rates therefore cannot be transferred to a 20–40 px clubhead.

[DeepAC](https://openaccess.thecvf.com/content/ICCV2023/papers/Wang_Deep_Active_Contours_for_Real-time_6-DoF_Object_Tracking_ICCV_2023_paper.pdf) is a newer learned active-contour method designed for mobile deployment. Its main pose metric is still the coarse 5 cm/5° success criterion. M3T is a broader software framework, not evidence of tiny, monocular, region-only angular accuracy.

### Documented failure modes

- Initialization outside the local convergence basin.
- Similar silhouettes at substantially different poses.
- Symmetric objects.
- Insufficient foreground/background color separation.
- Fast inter-frame motion.
- Motion blur and image noise.
- Dynamic lighting.
- Clutter or backgrounds similar to the target.
- Unknown occlusion.
- CAD or camera-calibration error.

These conditions map closely to OpenFlight: the 22.8 px inter-frame displacement is comparable with the complete 20–40 px target width, while the blur is 7–15% of that width.

### OpenFlight implication

SRT3D could be useful as a fast **position or mask tracker**, but the literature does not establish usable orientation from an iron silhouette at this scale. In OpenFlight's corrected experiment, IoU improves to 0.633, yet roughly one fifth of adjacent frames still disagree by more than 45° and the remaining optimum spans 60–70°.

**Confidence:** High for benchmark interpretation and documented failure modes; high that the published work does not demonstrate OpenFlight's required orientation accuracy.

---

## B. Small-object pose below 50 px

### Smallest relevant results found

The most directly relevant contour experiment is the [scale-adaptive region tracker](https://journals.sagepub.com/doi/10.1177/17298806221076978). It explicitly varies the short side of an object's projected minimum bounding box from **40 to 200 px** in 800×800 images.

Limitations:

- Objects are synthetic or semisynthetic RBOT targets, 100–200 mm in physical size.
- Success means no more than 5° final rotation error and 10 mm translation error.
- It reports convergence success, not median or percentile angular error at 40 px.
- Runtime is 31.5–46.8 ms/frame on an AMD Ryzen 7-4800H, with GPU rendering.
- Real examples are generally 50–200 px and often have favorable texture or foreground/background contrast.

A more extreme but geometrically different example is a [PocketQube spacecraft study](https://spaceguy-price.github.io/pdfs/acta2024.pdf). The target averaged 28.9 px at 4 km, but usable attitude meant errors below **20°**, reached 70% of the time only in an idealized closer-range condition. Motion and focus blur degraded performance.

No credible study was found demonstrating sub-degree, millimetric monocular 6-DoF pose of a textureless known object at 20–40 px.

### Error scaling

There is no universal empirical pose-error law versus apparent object width. Shape, symmetry, view direction, blur, calibration, and keypoint geometry dominate.

A useful first-order sensitivity is:

\[
\sigma_\theta \sim \frac{\sigma_{\rm image}}{r_{\rm effective}},
\]

where the effective projected rotational lever arm is measured in pixels. This is the same Jacobian/Fisher-information principle used in [uncertainty-aware PnP](https://openaccess.thecvf.com/content/CVPR2021/papers/Vakhitov_Uncertainty-Aware_Camera_Pose_Estimation_From_Points_and_Lines_CVPR_2021_paper.pdf).

For a 20–40 px target and an effective lever arm between half and all its width:

- A 1 px systematic contour displacement corresponds dimensionally to about **1.4–5.7°**.
- Treating the full 3 px blur as localization uncertainty would correspond to several to tens of degrees.

These are sensitivity calculations, not statistical bounds. Multiple independent contour samples can average down random noise, but not symmetry, model mismatch, segmentation bias, or asymmetric blur.

At OpenFlight's scale, 1 px is already 3.38 mm. A millimetric impact result requires repeatable subpixel measurement and an accurately defined face centre.

**Confidence:** Medium-high. Positive evidence is sparse, so the conclusion partly rests on the absence of size-stratified results in the major pose literature.

---

## C. Differentiable and soft rasterization

### What differentiability provides

[SoftRas](https://openaccess.thecvf.com/content_ICCV_2019/html/Liu_Soft_Rasterizer_A_Differentiable_Renderer_for_Image-Based_3D_Reasoning_ICCV_2019_paper.html), [DIB-R](https://arxiv.org/abs/1908.01210), and [nvdiffrast](https://nvlabs.github.io/nvdiffrast/) provide gradients of rendered pixels or silhouettes with respect to pose and scene parameters. This enables gradient, Gauss–Newton, or Levenberg–Marquardt fitting.

They do **not automatically provide a calibrated covariance**.

A local covariance approximation such as

\[
\Sigma \approx (J^\top WJ+\Lambda_{\rm prior})^{-1}
\]

is defensible only if:

- The residual is a calibrated negative log-likelihood.
- Pixel noise and correlations are modeled correctly.
- The pose is locally identifiable.
- Soft-rasterization temperatures have statistical meaning.
- The posterior is approximately unimodal and locally Gaussian.

For a silhouette with a broad 60–70° optimum, a local inverse Hessian near its numerical maximum can substantially overstate certainty. Rasterizer smoothing parameters also change the loss curvature and therefore the apparent uncertainty.

A grid search is not inherently worse for uncertainty. Given a likelihood instead of raw IoU, it can expose multiple modes and normalize them into an approximate posterior. Differentiability improves optimization efficiency; it does not create information absent from the image.

### Compute cost

The available pose-fitting benchmarks are overwhelmingly GPU-based:

- [nvdiffrast](https://nvlabs.github.io/nvdiffrast/) implements primitives and gradients in CUDA and has no Raspberry Pi CPU path.
- In an ICCV silhouette-pose comparison, [RtS](https://openaccess.thecvf.com/content/ICCV2021/papers/Cole_Differentiable_Surface_Rendering_via_Non-Differentiable_Sampling_ICCV_2021_paper.pdf) reported the following V100 times per iteration:

| Mesh | nvdiffrast | PyTorch3D/SoftRas style | Redner | RtS-pose |
|---|---:|---:|---:|---:|
| 2.5k triangles | 7 ms | 21 ms | 240 ms | 16 ms |
| 326k triangles | 9 ms | 47 ms | 247 ms | 16 ms |
| 2m triangles | 19 ms | 588 ms | 306 ms | 17 ms |

The compared LM/Adam pose task took approximately four seconds to converge. No relevant Raspberry Pi CPU benchmark was found.

### OpenFlight implication

A simple analytic contour Jacobian, finite-difference profile, or low-dimensional grid is more appropriate for the Pi and makes a flat or multimodal objective easier to inspect. Differentiable rendering would mainly make an underdetermined fit converge more efficiently.

**Confidence:** High.

---

## D. Physical priors and detecting prior domination

### Established practice

Pose should be parameterized on \(SE(3)\), normally using local Lie-algebra increments in \(\mathfrak{se}(3)\). This preserves valid rotations and gives a well-behaved local linearization. It is a parameterization, not new evidence.

Soft constraints can be represented as residual terms:

\[
L_{\rm total}
= L_{\rm image}
+ L_{\rm range}
+ L_{\rm shaft}
+ L_{\rm dynamics}
+ L_{\rm club}.
\]

SRT3D itself uses Tikhonov regularization. Motion, kinematic, multi-body, and physical constraints are established in robotic tracking. They improve stability when correct but can conceal non-observability.

### Detecting whether priors rather than data determine the answer

There is no universal object-pose "prior domination score." Established practice is sensitivity and observability analysis:

1. **Profile the data term alone.** Plot one- and two-dimensional likelihood slices, especially over roll around the shaft, face angle, pitch, and depth.
2. **Separate data and prior curvature.** Inspect eigenvalues and eigenvectors of \(H_{\rm data}\) and \(H_{\rm prior}\), not only the combined Hessian.
3. **Sweep prior strength and centre.** Refit with plausible shifted means and, for example, 0.5×, 1×, 2×, and 4× prior standard deviations.
4. **Remove each prior.** If pose moves with a prior while the image/radar loss barely changes, the result is prior-determined.
5. **Power-scale prior and likelihood independently.** Kallioinen et al.'s [power-scaling diagnostic](https://link.springer.com/article/10.1007/s11222-023-10366-5) distinguishes likelihood noninformativity from prior–data conflict.
6. **Perform simulated parameter recovery.** Use realistic blur, masks, club variation, shaft bending, and calibration error. Check interval coverage, not only RMSE.
7. **Use external validation.** Test players, clubs, and shots not used to set priors.

Posterior re-rendering is insufficient: multiple materially different poses can reproduce the same silhouette.

### Recommended output diagnostics

For each pose axis, report:

- Data-only profile width and modes.
- Estimates under each prior ablation.
- Estimates under prior-strength sweeps.
- Separate data/prior Hessian spectra.
- An abstention flag when different poses remain data-equivalent.

**Confidence:** High.

---

## E. Golf shaft mechanics through impact

The literature is weak for **irons specifically**. Most quantitative cohort work uses drivers.

### Toe-down deflection and dynamic lie

A 33-player driver study found at impact:

- Toe-down deflection: 78.3 mm for R-flex versus 59.7 mm for X-flex.
- Bend-lie contribution: 10.1° versus 8.0°.
- Lead deflection: 42.8 mm versus 32.6 mm.

Players altered grip delivery, and actual delivered lie did not differ significantly between shaft conditions. These are driver shaft-bending contributions, not iron static-lie minus dynamic-lie measurements. See [MacKenzie and Boucher](https://people.stfx.ca/smackenz/Publications/MacKenzie%202016%20The%20influence%20of%20golf%20shaft%20stiffness%20on%20grip%20and%20clubhead%20kinematics.pdf).

A robot experiment using two 5-irons found a 0.6° difference in effective lie between a blade and cavity-back under nominally identical swings, attributed to CG-dependent shaft bending. It did not establish a human static-to-dynamic lie distribution. See [Wallace et al.](https://journals.sagepub.com/doi/pdf/10.1177/1754337120969357).

**Unknown:** No robust iron cohort was found reporting shaft-only droop angle, player-to-player spread, and static-to-dynamic lie at impact. Launch-monitor dynamic lie also includes handle delivery and cannot be treated as shaft droop alone.

### Shaft twist around its own axis

The best direct measurement remains an old three-golfer driver experiment summarized by [MacKenzie et al.](https://people.stfx.ca/smackenz/Publications/MacKenzie%202018%20The%20influence%20of%20golf%20shaft%20torque%20on%20clubhead%20kinematics%20and%20ball%20flight.pdf):

- Approximately +0.4° open for two golfers at impact.
- Approximately −0.4° closed for one.
- Maximum observed twist during the swing about 1°.
- Only one strain gauge near the tip and one reported swing per golfer.

MacKenzie's later 40-player experiment compared extreme nominal torque shafts, approximately 2° versus 8°. The high-torque shaft delivered the face about 1° more open, but twist was inferred rather than directly measured.

A reasonable order of magnitude is around one degree, but neither the sign nor iron-specific distribution is established.

### Lead deflection, kick, and dynamic loft

The driver stiffness study found roughly 4° of bend loft, with the flexible shaft adding about 2° more than the stiff shaft. The actual delivered-loft difference was only 0.4° because golfers modified grip delivery.

The 5-iron robot study found a 1.8° effective-loft difference between two head designs under the same robot motion and shaft specification, consistent with head-CG-dependent shaft bending.

**Unknown:** There is no defensible canonical value for how many degrees shaft kick adds to dynamic loft for irons across players, shafts, and heads.

### Shaft tangent at the hosel

The shaft tip is inserted and bonded inside the hosel bore, so the tangent at the exact bonded boundary is mechanically constrained by the bore. Club-construction patents commonly show 20–40 mm insertion depths.

A Rapsodo patent treats the line segment between hosel and ferrule as one that "does not bend" during a swing and uses it for stereo pose recovery, but it provides no independent deformation measurement validating that assumption; see [US20230364468A1](https://patents.google.com/patent/US20230364468A1/en).

No published measurement was found of bending within the first exposed few millimetres above an iron hosel. A line fitted over a longer visible shaft section measures the chord or tangent of a bent segment, not necessarily the bore axis.

### OpenFlight implication

- Bore geometry supports a local alignment assumption at the boundary.
- It does not validate the 3D direction recovered from a visible, bent, monocular shaft segment.
- Shaft direction cannot determine rotation around the shaft axis.
- Iron-specific dynamic offsets are insufficiently measured to justify a narrow prior.

**Confidence:** Low for iron droop/kick magnitudes; medium that torsional twist is generally order-one-degree rather than tens of degrees; high that exposed-segment bending error is unmeasured.

---

## F. Average modern iron specifications

The following is an unweighted snapshot of one mainstream set from six OEMs using standard—not power or retro—specifications.

Players sample: [Titleist T100](https://www.titleist.com/golf-clubs/specifications), [PING Blueprint S](https://ping.com/en-us/golf-clubs/irons/blueprint-s-iron), [TaylorMade P7CB](https://www.taylormadegolf.com/P%E2%88%997CB-Irons/V9874609.html), [Callaway Apex Pro](https://www.callawaygolf.com/golf-clubs/irons/irons-2024-apex-pro.html), [Mizuno Pro S-3](https://mizunogolf.com/ca/golf-clubs/mp-series/mizuno-pro-s3/), and [Srixon ZXi7](https://us.dunlopsports.com/srixon/clubs/irons/zxi7-irons/zxi7-irons/MZXI7IRN.html).

Game-improvement sample: Titleist T350 from the [Titleist specification page](https://www.titleist.com/golf-clubs/specifications), [PING G440](https://ping.com/en-us/golf-clubs/irons/g440-iron), [TaylorMade Qi](https://www.taylormadegolf.com/Qi-Irons/N2797407.html), [Callaway Elyte](https://www.callawaygolf.com/golf-clubs/iron-sets/irons-2025-elyte.html), [Mizuno JPX925 Hot Metal](https://mizunogolf.com/us/golf-clubs/jpx925-series/jpx925-hot-metals/), and [Srixon ZXiR](https://us.dunlopsports.com/srixon/clubs/irons/zxir-irons/zxir-irons/11254482.html).

Values are mean ± sample standard deviation, followed by range:

| Category | 7i loft | 7i lie | 9i loft | 9i lie |
|---|---:|---:|---:|---:|
| Players, n=6 | 33.00° ± 0.63° [32–34] | 62.25° ± 0.42° [62–63] | 41.17° ± 0.41° [41–42] | 63.33° ± 0.41° [63–64] |
| Game improvement, n=6 | 28.58° ± 0.49° [28–29] | 62.42° ± 0.49° [62–63] | 37.33° ± 0.52° [37–38] | 63.54° ± 0.46° [63–64] |

Category separation:

- 7-iron loft: approximately **4.4°**.
- 9-iron loft: approximately **3.8°**.
- Lie: approximately 0.2° in this sample.

A single cross-category loft template would be badly biased. Lie is more stable, though individual fitting adjustments can exceed the OEM spread.

### Clubhead dimensions

Comparable OEM geometry is poorly published. Two useful sources are:

- [Mizuno's 7-iron comparison](https://jpn.mizuno.com/cm/cluborder/static/function/about_iron.html), which gives head length and sole width.
- [Srixon's iron-shape comparison](https://us.dunlopsports.com/srixon/srixon-irons-2026b.html), which gives blade length, sole width, topline thickness, and offset.

A limited two-OEM subset gives:

| Category | 7i heel-toe/head length | 7i sole width |
|---|---:|---:|
| Players subset | 76.2 mm mean [74.5–77.2] | approximately 17 mm [15.6–19.5] |
| Game-improvement subset | 84.7 mm mean [82.0–86.6] | 23.4 mm [22.9–23.9] |

This is not a population average. Two live Srixon comparison pages also disagree on ZXi7 sole width and topline—15.6/4.0 mm on one and 18.9/6.0 mm on another—possibly because of product revision or differing measurement definitions. A more precise aggregate is unjustified.

No broad, comparable OEM data was found for:

- Sole-to-topline or face height.
- Overall face-to-back head depth.
- Face-centre coordinates relative to hosel.
- Hosel bore axis and offset in a common CAD coordinate convention.

### OpenFlight implication

A category template may support coarse classification, but it cannot support trustworthy millimetres from face centre. GI and players heads differ by roughly 8–9 mm in length in the limited geometry sample, and hosel offsets differ by millimetres.

**Confidence:** High for the loft/lie sample; low-medium for dimension averages; high that the required height, depth, and face-centre fields are not broadly published.

---

## G. D-plane coefficients and inverse face estimation

The most directly applicable experiment is Wood, Henrikson, and Broadie's [player and robot study](https://pdfs.semanticscholar.org/3d4d/e973f3d17dc5be3eb63765c3145977128630.pdf). It used 157 golfers, Vicon club tracking, a GC2 for ball launch, and commercially available drivers, 7-irons, and wedges.

For filtered **7-iron** shots:

| Plane | Face-oriented contribution | Path-oriented contribution |
|---|---:|---:|
| Horizontal | 69% ± 3% toward face angle | 31% toward club path |
| Vertical | 81% ± 5% toward dynamic loft | 19% toward attack angle |

Intervals are reported 95% confidence intervals. A controlled 7-iron robot experiment produced only **63% ± 4%** horizontal face influence, showing that the coefficient is not universal.

Approximate inversion is:

\[
F \approx P+\frac{H-P}{0.69},
\]

\[
DL \approx AoA+\frac{V-AoA}{0.81},
\]

where \(H\) and \(V\) are horizontal and vertical launch direction, \(P\) is club path, \(F\) is face angle, and \(DL\) is dynamic loft.

### Idealized propagated accuracy

With approximately:

- 0.2° launch-direction uncertainty;
- 0.5° path uncertainty;
- 0.03 coefficient uncertainty;
- 5° face-to-path separation;

ordinary propagation gives a face-angle uncertainty around **0.4–0.5° before impact-model error**.

This is not achieved accuracy because:

- The paper filtered strikes to within ±0.25 in/6.35 mm of face centre.
- It excluded face-to-path differences below 1.5°, where ratios become unstable.
- The coefficient varies with collision obliqueness and dynamic loft.
- Friction, grooves, moisture, face condition, and ball construction matter.
- Club path must be true 3D path in the same coordinate system as ball launch.
- The model residual is not a calibrated per-shot inverse uncertainty.

### Dependence on strike location

The 5-iron robot study found that horizontal strike location changed initial side angle by approximately:

- 0.040°/mm for a blade.
- 0.022°/mm for a cavity-back.

After division by the 0.69 D-plane coefficient, an unmodeled 10 mm strike offset can bias inferred face by roughly **0.3–0.6°**; 20 mm can approach or exceed one degree.

The intended validation is therefore partly circular:

1. Infer face angle from launch using an impact model.
2. That impact model depends on strike location.
3. Infer strike location using the resulting face pose.

D-plane inversion remains useful as a coarse consistency check or weak likelihood term, but not as independent millimetric ground truth.

**Confidence:** High for the 69/31 and 81/19 measurements; medium for per-shot inverse accuracy; high that unknown strike prevents independent validation.

---

## H. Commercial behind-ball launch monitors

| System | Public method | Camera contribution | Accuracy evidence |
|---|---|---|---|
| TrackMan 4 | Dual radar plus OERT; 4D silhouette clubhead tracking; 40,000 radar samples/s around impact | Synchronized club/ball position and impact-location refinement | Marketing says "exact"; no numerical markerless impact-location tolerance published |
| Full Swing KIT | CW/FMCW and machine-learning-enhanced radar; patent calculates face from club and ball paths | Public material mainly describes swing video | No independent orientation validation found; impact location is not one of its 16 outputs |
| Mevo Gen2 | 3D Doppler radar plus synchronized image processing/Fusion Tracking | Used for face angle and paid Face Impact Location | "Precise," but no public mm tolerance or independent Gen2 impact validation found |

### TrackMan 4

TrackMan says OERT synchronizes a full-HD camera with dual radar. Radar operates at 40,000 samples/s around impact; camera and radar jointly provide [4D silhouette clubhead tracking](https://www.trackman.com/blog/two-radars-one-camera-zero-doubt), markerless impact location, and over 90% club-data pickup.

TrackMan's [club-data definitions](https://www.trackman.com/blog/club-data-definitions) say it directly measures the head's 3D silhouette/geometric centre, reports pre-impact path about the geometric centre, and reports face angle/dynamic loft at the impact point near maximum compression.

There is a detail discrepancy:

- The general description says no club-design assumption is needed to find the geometric centre.
- Technical descriptions say groove or face centre is established using an adjustable offset from the hosel.

Those can coexist because tracking head centre and locating face centre are separate operations, but the second step still needs a club-geometry convention.

A 2017 independent benchmark tested the older TrackMan Pro IIIe, not TrackMan 4. More than 65% of its face-angle and dynamic-loft results were within ±1° of a traceable optical reference, while club parameters were less accurate than ball parameters; see [Leach et al.](https://www.sciencedirect.com/science/article/pii/S0263224117305079). Recent TrackMan 4 studies establish repeatability rather than absolute impact-location accuracy. No independent markerless millimetric TrackMan 4 impact validation was found.

### Full Swing KIT

Full Swing describes the KIT as [machine-learning-enhanced radar](https://www.fullswinggolf.com/kit-launch-monitor-technology/) and reports face angle, face-to-path, attack angle, and club path among its 16 outputs.

The most informative disclosure is Full Swing's [US11311789B2 patent](https://patents.google.com/patent/US11311789B2/en):

- Club path is measured from radar frequency and phase shifts.
- Face angle is labeled **calculated** from club and ball paths.
- Attack and launch angles combine CW/FMCW information.
- Face-impact location is not described.

The built-in camera is publicly presented for video capture. No evidence was found that KIT resolves a CAD silhouette or directly measures face orientation optically. Exact machine-learning features remain proprietary.

Full Swing publishes no numerical face-angle tolerance, and no peer-reviewed or traceably benchmarked KIT face-angle study was found.

### FlightScope Mevo Gen2

FlightScope says Mevo Gen2 uses [Fusion Tracking](https://flightscope.com/products/pro-package): 3D Doppler radar plus synchronized image processing. Its Pro Package unlocks face-to-target, face-to-path, dynamic loft, and club path.

Face Impact Location is a separate add-on. FlightScope states that [Advanced Fusion Tracking](https://flightscope.com/collections/software/products/mevo-club-face-impact-location) combines radar and camera, returns lateral and vertical strike coordinates, and requires at least **300 lux** around the hitting area. That requirement confirms material dependence on optical evidence and may conflict with OpenFlight's evening ambient-light operation.

FlightScope lists applicable patents, but the public patent set does not disclose a clear, reproducible face-impact reconstruction equation. No numerical millimetre tolerance is published. Available Mevo+ versus TrackMan 4 comparisons test inter-device agreement rather than truth and do not validate Mevo Gen2 face-impact location.

**Confidence:** Medium. The broad architectures are documented; exact implementations and absolute accuracy remain proprietary.

---

## 4. What would break the approach OpenFlight is considering

The proposed approach is:

1. Constrain head range using radar.
2. Constrain orientation using shaft tangent at the hosel and the fact that the face points away from the camera.
3. Recover face angle by fusion with ball-launch data rather than silhouette orientation.

### 4.1 Radar range may constrain the wrong point too coarsely

A 4.69 cm bin is nearly 14 OpenFlight pixels at the ball and much larger than the intended millimetric output. Even with 22/22 correct bin selections:

- The return may correspond to a changing radar scattering centre, not the face centre.
- Hosel, shaft, sole, and head contributions can vary with orientation.
- Half-bin depth uncertainty is about 23 mm.
- Range does not resolve rotation.

Range remains useful, but its scattering centre must be calibrated against a defined CAD point. A selected clubhead bin is not a face-centre measurement.

### 4.2 A monocular shaft line is not a 3D hosel tangent

The measured 0.87°/frame shaft direction is a stable **2D projected line**. One image line represents a family of 3D directions with different depth components.

Unless another measurement provides depth at two shaft locations, the shaft does not fully determine 3D pitch or lie. Head range supplies only one depth anchor.

Additional risks:

- The visible line may cover a bent segment above the hosel.
- The local interface tangent may be hidden by head or ferrule.
- A long line fit estimates a chord, not the boundary tangent.
- Droop and lead deflection depend on player, shaft, and head.
- Rotation around the shaft axis remains unobserved.
- "Face away from the camera" removes a gross branch but not continuous face-angle ambiguity.

### 4.3 A canonical club can be wrong by more than the target accuracy

Players and GI 7-irons differ by approximately 4.4° in static loft and roughly 8–9 mm in head length in the limited geometry sample. Hosel offset, face height, sole shape, and face-centre definition also vary.

Even perfect shaft orientation with the wrong canonical model can move the face centre by several millimetres. Repeated frames do not remove this systematic error.

A minimum defensible system would need category-level or per-club calibration. A single canonical mesh cannot support claims of absolute millimetres.

### 4.4 D-plane face recovery is not independent of impact location

The best iron coefficient is 69/31, while the controlled robot result is 63/37. It varies with collision obliqueness and dynamic loft.

More importantly, off-centre impact changes launch and spin. Inferring face from the ball and then using that face to locate impact forms a coupled inverse problem. It requires an additional independent observation or a validated joint collision model.

When face-to-path is small, the inversion is also dominated by launch and path noise—the reason the source study excluded differences below 1.5°.

### 4.5 Club path and attack angle may only be projected quantities

D-plane inversion requires true 3D club path and attack angle in the same calibrated target frame as ball launch. A rear monocular camera directly measures image-plane motion. Radar range at a few samples may help recover depth velocity, but the measured radar angular result failed on all shots.

If OpenFlight supplies only projected path, the D-plane face result is systematically biased even with perfect ball launch.

### 4.6 Impact occurs between sparse, blurred head observations

At 467.6 frames/s:

- Frames are 2.14 ms apart.
- The head travels about 77 mm between frames.
- Exposure blur is about 10 mm.
- Impact pose must be interpolated or extrapolated from a few samples.

A stable shaft path does not guarantee stable rotation around the shaft. Timing error multiplied by face-closure rate can create material angular error.

### 4.7 The solution can become confidently prior-driven

Range, canonical loft/lie, shaft alignment, smooth motion, face-away constraints, and D-plane physics can produce a unique and plausible pose even when none of the sensors distinguishes it.

The approach should abstain or be rejected if:

- Weakening priors materially changes face angle or impact location.
- The data-only likelihood is flat or multimodal.
- Different canonical heads fit equally but move face centre materially.
- The inferred strike changes materially when the D-plane coefficient moves from 0.63 to 0.69.
- Reported covariance shrinks mainly because prior curvature was added.
- Simulated interval coverage fails under realistic blur, model variation, and calibration errors.

## 5. Recommended interpretation and next validation gate

The architecture is worth testing as a **coarse, uncertainty-aware club-delivery estimator**, with the following provisional output contract:

- **Radar-constrained head position/range:** feasible after scattering-centre calibration.
- **Projected shaft direction:** feasible and supported by OpenFlight's measurements.
- **Face angle:** experimental, likely degree-scale, and must include sensitivity to D-plane coefficient and assumed strike.
- **Dynamic loft:** experimental and lower confidence.
- **Absolute impact location in millimetres:** presently unsupported.

The critical next experiment is not another optimizer comparison. It is a blinded parameter-recovery study across multiple physically different irons, with an independently measured impact point and pose reference.

The evaluation must report:

- Errors by apparent clubhead size.
- Errors by club category and model mismatch.
- Results with each prior removed.
- Sensitivity to D-plane coefficients and assumed impact point.
- Data-only likelihood profiles.
- Confidence-interval coverage and abstention rate.
- Failure rate, not only median error on successful shots.

If the no-marker rule also forbids one-off validation labels and no alternative independent reference is available, millimetric impact-location accuracy cannot be demonstrated scientifically even if the estimator produces stable numbers.

## 6. Bottom line

The evidence supports abandoning silhouette-derived head orientation at 20–40 px. The shaft is a much more stable visual feature, and radar range plus ball launch provide complementary information, but the resulting fusion must be treated as a constrained physical inference rather than direct pose measurement.

Face angle may be reachable at coarse, degree-scale accuracy. Dynamic loft is less certain. Absolute impact location from a generic iron template is not presently identifiable or validatable to millimetres under the stated constraints.
