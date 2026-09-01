# OpenFlight follow-up research: partial-ball fitting and shaft-axis strike inference

**Prepared:** 25 August 2026  
**Purpose:** Technical handoff for review by Claude  
**Scope:** Published evidence relevant to OpenFlight's proposed markerless impact-location method; no implementation design or code

## Executive conclusion

The strongest part of the proposed approach is the decision to stop asking a 20–40 px clubhead silhouette for orientation. After correcting the horizontal-mirroring bug, CAD-fit IoU improves from 0.548 to 0.633 on all 21 shots and the false second basin disappears. The substantive failure remains: about one fifth of adjacent frames still disagree by more than 45°, and the surviving optimum is 60–70° wide.

The ball and shaft offer much better image primitives. Under the standard independent, isotropic Gaussian edge-noise model, fixing the ball radius changes a short-arc circle fit from a poorly conditioned three-parameter problem to a two-parameter one. For OpenFlight's 110–160° arc range, it reduces variance along the arc-bisector direction by approximately 8.8–45 times, or standard deviation by 3.0–6.7 times. That is a material gain.

The shaft-axis proposal is promising as an image measurement, but the proposed interpretation is not yet established. A 2D perpendicular distance from projected shaft axis to projected ball center is not automatically physical toe–heel strike. It can also contain contributions from dynamic lie, loft/face-normal displacement, shaft bending, perspective and vertical strike. A club constant removes only terms that remain constant. The literature contains shaft/hosel-axis methods for pose and club-design definitions tied to shaft axis, but this search found no published method that obtains strike location by the exact subtraction OpenFlight proposes.

The evidence is asymmetric by axis. Toe–heel may be recoverable after a calibrated geometric mapping. High–low is not supported by the shaft measurement alone. Published optical validation also shows that a tangent-derived first-contact point and the center of an impact-tape mark differ vertically by millimetres on lofted irons unless ball motion on the face is modelled.

## Experimental facts supplied by OpenFlight

These are OpenFlight measurements, not claims found in the literature:

| Quantity | Residual precision on 21 shots |
|---|---:|
| Static teed-ball center | 0.18 px = 0.57 mm |
| Perpendicular shaft-axis-to-ball distance | 0.40 px = 1.51 mm |
| Clubhead silhouette centroid | 1.7–2.9 px = 5.5–9.7 mm |
| Hosel location | 4.90 px = 16.3 mm |

The plate scale is 0.296 px/mm (3.38 mm/px), and the head spans only 20–40 px. Mask area changes fivefold within a shot. Its correlation with centroid residual is statistically decisive but moderate in explanatory power: (r=0.41), (p=7\times10^{-16}), hence (R^2\approx0.17). The observed ball outline has median span about 125° (range 110–160°) and circularity 0.92. Circle fitting moves the naive blob centroid by 1.17 px, or 4.0 mm, and its fitted radius agrees with the radar-range-implied radius to 0.7%.

Residual to a smooth trajectory measures repeatability, not absolute accuracy. Any stable calibration, segmentation or projection bias can be smooth and therefore absent from these residuals.

---

## 1. Sphere center from a partial occluding contour

### 1.1 Statistical model and the optimal estimator

Let the detected boundary points be

\[
p_i=c+R u_i+\varepsilon_i,\qquad
u_i=(\cos\phi_i,\sin\phi_i),
\]

with independent, isotropic Gaussian coordinate noise of standard deviation \(\sigma\). Under this model, the maximum-likelihood estimator is the **orthogonal geometric fit**:

\[
\min_{c,R}\sum_i(\lVert p_i-c\rVert-R)^2.
\]

[Al-Sharadqah and Chernov's error analysis](https://arxiv.org/abs/0907.0421) shows that the geometric fit reaches the Kanatani–Cramér–Rao lower bound to leading order. Pratt, Taubin and Hyper are algebraic approximations. They share the same leading covariance but differ in second-order bias; Hyper was designed to remove the leading “essential” bias. [Kanatani and Rangarajan](https://iim.cs.tut.ac.jp/member/kanatani/papers/csdellipse3.pdf) give the corresponding hyperaccurate algebraic formulation.

For OpenFlight, the appropriate estimator is therefore:

- orthogonal geometric maximum likelihood when the contour samples are credible and approximately Gaussian;
- the same fit with robust loss or outlier rejection when mat, grass, shadow or club pixels contaminate the arc;
- an exact fixed-radius fit if radar and calibration provide a genuinely accurate radius; or a radius-prior fit if the radar radius has non-negligible uncertainty;
- Hyper as a good fast initializer, not as a substitute for a correctly specified observation model.

Real segmented contours violate the ideal model: neighboring pixels are correlated, quantization is non-Gaussian, and the missing lower arc is censored rather than missing at random. Consequently, the formulas below are a conditioning/noise floor, not a complete error budget.

### 1.2 Variance as a function of visible arc span

The following is a direct specialization of the leading covariance in [Al-Sharadqah and Chernov](https://people.cas.uab.edu/~mosya/cl/AC1c.pdf), not a formula quoted verbatim from the paper. Assume \(n\) effectively independent points uniformly cover a symmetric arc of total angle \(\theta\). Define

\[
\alpha=\theta/2,\quad
m=\frac{\sin\alpha}{\alpha},\quad
A=\frac12+\frac{\sin\theta}{2\theta},\quad
B=\frac12-\frac{\sin\theta}{2\theta}.
\]

Let \(c_\parallel\) be center error along the arc bisector and \(c_\perp\) be error transverse to it. When radius is estimated from the same arc,

\[
\operatorname{Var}(c_\parallel)\simeq
\frac{\sigma^2}{n(A-m^2)},\qquad
\operatorname{Var}(c_\perp)\simeq
\frac{\sigma^2}{nB}.
\]

When radius is fixed exactly,

\[
\operatorname{Var}(c_\parallel)\simeq
\frac{\sigma^2}{nA},\qquad
\operatorname{Var}(c_\perp)\simeq
\frac{\sigma^2}{nB}.
\]

Thus fixing radius helps the weak arc-bisector direction and leaves the transverse direction unchanged to leading order. The standard-deviation improvement is

\[
G_{SD}=\sqrt{\frac{A}{A-m^2}}.
\]

| Visible arc | SD improvement from exact radius | Variance reduction |
|---:|---:|---:|
| 110° | 6.71× | 45.0× |
| 120° | 5.56× | 30.9× |
| 125° | 5.09× | 25.9× |
| 130° | 4.68× | 21.9× |
| 140° | 3.98× | 15.8× |
| 160° | 2.97× | 8.8× |

At OpenFlight's median 125°, the fixed-radius standard deviations are approximately

\[
SD(c_\parallel)=1.206\frac{\sigma}{\sqrt n},\qquad
SD(c_\perp)=1.790\frac{\sigma}{\sqrt n},
\]

whereas estimating radius from the arc gives

\[
SD(c_\parallel)=6.141\frac{\sigma}{\sqrt n}.
\]

Pixel count is not effective sample count. The ball radius is only about 6.3 px at the stated scale, so a 125° contour is roughly 14 px long. Subpixel oversampling cannot manufacture independent observations; edge correlation can make \(n_{eff}\) much smaller than the number of samples passed to the optimizer.

If the independent radius has standard deviation \(\tau_R\), a Gaussian radius prior gives the intermediate result

\[
\operatorname{Var}(c_\parallel)\simeq
\frac{\sigma^2/n}
{A-\dfrac{m^2}{1+\sigma^2/(n\tau_R^2)}}.
\]

It tends to the fixed-radius formula as \(\tau_R\to0\) and the free-radius formula as \(\tau_R\to\infty\). The reported 0.7% agreement is encouraging, but agreement on the same shots is not yet a calibrated value of \(\tau_R\), and range/calibration errors may be correlated with image scale.

### 1.3 Bias formulas

For a true radius \(R\), the leading “essential” parameter biases under the same small-noise model are [derived and tabulated by Al-Sharadqah and Chernov](https://arxiv.org/abs/0907.0421):

\[
E[\Delta c_x,\Delta c_y,\Delta R]_{geometric}
\simeq \frac{\sigma^2}{2R}[0,0,1],
\]

\[
E[\Delta]_{Pratt}\simeq \frac{2\sigma^2}{R}[0,0,1],\quad
E[\Delta]_{Taubin}\simeq \frac{\sigma^2}{R}[0,0,1],\quad
E[\Delta]_{Hyper}\simeq [0,0,0].
\]

Those are the leading essential terms. They are independent of arc position, but smaller \(O(\sigma^2/n)\) terms can become important when short-arc conditioning is poor. Circle-parameter estimates can also have pathological tails; Chernov notes that several standard circle estimates do not possess finite moments in the exact mathematical sense ([paper](https://arxiv.org/abs/0907.0429)).

Kåsa's simple algebraic fit has an arc-dependent leading bias. For a symmetric uniform arc, using the definitions above,

\[
E[\Delta c_\parallel]_{K\mathring{a}sa}
\simeq \frac{\sigma^2}{R}\frac{m}{A-m^2},\qquad
E[\Delta c_\perp]_{K\mathring{a}sa}\simeq0,
\]

\[
E[\Delta R]_{K\mathring{a}sa}
\simeq\frac{\sigma^2}{R}\left(2-\frac{A}{A-m^2}\right).
\]

The denominator tends to zero for a short arc, exposing why Kåsa is unsuitable here.

A fixed but wrong radius transfers its error into the center. If \(\delta R=R_{fixed}-R_{true}\), first-order least squares gives

\[
\Delta c_\parallel\simeq-\frac{m}{A}\delta R.
\]

At 125°, \(m/A\approx1.18\). A 0.7% error on a 21.335 mm golf-ball radius is 0.149 mm and would shift the fitted center about 0.176 mm (0.052 px) along the arc bisector. The sign depends on whether radius is over- or underestimated and which arc is visible.

### 1.4 Circle versus projected sphere

Under a calibrated pinhole camera, a sphere's apparent contour is generally a conic/ellipse, not an exact image-plane circle when it is off the optical axis. Sphere-estimation papers explicitly model this perspective geometry ([Hajder, Tóth and Pusztai](https://arxiv.org/abs/2002.10217); [Tóth and Hajder](https://link.springer.com/article/10.1007/s11263-023-01766-1)). At only ~12.6 px diameter, ellipse eccentricity may be smaller than segmentation error, but subpixel claims require checking it rather than assuming it away. Lens distortion is a separate source of contour bias. A calibrated known-radius sphere/cone fit is geometrically preferable; an unconstrained five-parameter ellipse fitted to a 120° arc would be much less stable.

**Answer:** Yes, fixing radius materially improves a 120–140° fit: approximately 4.0–5.6× in center SD in the weak direction under the ideal model. It does not correct a systematically displaced contour.

**Confidence:** High for the Gaussian small-noise formulas and relative conditioning gain; medium for their numerical applicability to OpenFlight contours; low until radar-radius uncertainty, effective edge noise and segmentation bias are measured independently.

---

## 2. Ball center under partial occlusion in practice

Published launch-monitor material is much less specific than the circle-fitting literature.

- [US8016688B2](https://patents.google.com/patent/US8016688B2/en), “Method and apparatus for measuring ball launch conditions,” describes background subtraction, a high-contrast background, perimeter-edge detection and solving the three circle unknowns from ball-edge points. It concentrates on balls in flight; it does not quantify resting-ball contact-shadow bias.
- [US7641565B2](https://patents.google.com/patent/US7641565B2/en) locates a ball in a strike zone by comparing size, dimples, reflectivity, roundness, shading, gradient and position. It does not publish a lower-contour recovery method or error budget.
- [CN112184808A](https://patents.google.com/patent/CN112184808A/en), a top-mounted golf-detection patent, explicitly says a ball outline can be incomplete because of shadow and uses a Hough transform to complete the circle and obtain its center. It provides neither bias/variance validation nor evidence that a commercial product uses the method, and its top-down geometry differs from OpenFlight's behind-ball view.

The recurring practical pattern is to use controlled backgrounds and illumination, detect a reliable arc, fit the known shape, or postpone measurement to unobscured in-flight frames. Patents establish that these strategies were contemplated; they are not accuracy validation.

This search found **no public TrackMan, Foresight or FlightScope paper, patent example, or specification that quantifies ball-center bias caused specifically by a ball–mat contact shadow**. Proprietary implementations may address it, but the public literature is silent.

For OpenFlight, the lower contour should be treated as a censored/contaminated region, not as ordinary noisy circle data. If shadow or grass pixels are admitted as “edge” points, a robust estimator can still converge tightly to the wrong center. A defensible validation changes mat color/texture, lighting direction, tee height and ball elevation while holding the physical ball center fixed. Stable trajectory residuals alone will not reveal a stable contact-shadow bias.

**Confidence:** High that circle/edge/Hough approaches appear in the cited patents; medium that this captures the main commercial strategies; high that no numerical commercial treatment was found, but absence in public documents is not proof that none exists internally.

---

## 3. Shaft axis as a measurement primitive

### 3.1 Prior art located

[Rapsodo US20200282283A1](https://patents.google.com/patent/US20200282283A1/en) uses a stereo-reconstructed hosel–ferrule line segment, silhouettes and a scanned 3D club model to estimate pose, with a claimed sub-degree RMS result. [US20230364468A1](https://patents.google.com/patent/US20230364468A1/en) continues related pose/deep-learning concepts and lists impact location among possible learned outputs. Neither document discloses the specific scalar construction “projected shaft-axis-to-ball distance minus a club constant” as the strike-position estimator.

[US9168431B2](https://patents.google.com/patent/US9168431B2/en), a club-design patent, defines center-face progression using distance from the shaft axis to an engineered impact point. That supports the existence of a club-specific shaft-axis/face-center geometric offset, but it is not an image measurement method.

Corke, Wallace and Otto's optical method instead uses club markers, a reconstructed head/face, and the ball/face tangency geometry ([journal paper](https://journals.sagepub.com/doi/abs/10.1177/1754337118792798), [author manuscript](https://pure.ulster.ac.uk/ws/files/12610269/Wallace_12601983_.pdf)). It does not infer strike from a shaft-to-ball image distance.

**Search result:** No paper or patent was found using the projected shaft axis specifically to infer strike position in the way proposed. That is evidence of a public-literature gap, not proof of novelty or freedom to operate; this was not an exhaustive legal patent search.

### 3.2 What the silhouette centerline represents

For an ideal straight circular cylinder under orthographic projection, the two silhouette generators are parallel and symmetric, and their midpoint line is exactly the projected 3D axis. A straight, rotationally symmetric conical frustum remains symmetric under orthographic projection: taper changes apparent width along the shaft but not the transverse midpoint. Thus taper by itself does not create a first-order centerline error in the weak-perspective idealization.

Under pinhole projection at an oblique view, naive Euclidean midpoints of opposite image edges need not be the calibrated projection of the 3D axis. The offset from symmetric radial depth is second order in radius/range, on the scale of \(r_s^2/Z\). With shaft radius at most about 5 mm and range around 1.5 m, that scale is under 0.017 mm, or about 0.005 px at OpenFlight's plate scale. This is a first-order geometric scale estimate, not a measured golf-shaft error or a universal bound. Lens distortion, edge threshold, motion blur, highlights, shadows and partial occlusion can be much larger. A calibrated cylinder/frustum projection removes the ideal perspective term.

The literature was **silent on a validated error budget for recovering a golf shaft's physical axis from the two unmarked silhouette edges**. Rapsodo's patents demonstrate use of the hosel–ferrule segment, but do not establish the centerline theorem or quantify edge-derived axis bias in OpenFlight's geometry.

### 3.3 The larger problem: the shaft is not straight during a swing

Dynamic bending is more important than taper. A continuous analytical shaft model validated over 200 swings reported model RMSE of 1.14° in droop, 0.83° in lag and 0.85° in twist, and observed golfer-specific bending patterns ([MacKenzie et al.](https://doi.org/10.1007/s12283-019-0314-5)). Earlier work also derives clubhead-tip angle from changing shaft curvature ([Suzuki et al.](https://www.jstage.jst.go.jp/article/kikaic1979/65/630/65_630_572/_article/-char/ja/)). These are model errors, not total deflection magnitudes, but they establish that the active shaft is curved and dynamic.

Therefore “shaft axis” must mean the local tip/hosel tangent relevant to the head, not a long chord fitted farther up the shaft. Extrapolating a remote straight-line fit through a bent shaft can create millimetre-scale errors at the ball even when its image residual is excellent. The supplied 4.90 px hosel residual also argues against using the visible entry point as a stable substitute.

### 3.4 Does perpendicular image distance equal toe–heel strike?

Not without a calibrated mapping. In a local weak-perspective model, write the ball center relative to the shaft as

\[
B=C_0+x e_{toe}+y e_{vertical}+r n_{face}.
\]

Taking signed distance to the projected shaft line gives an image scalar of the form

\[
d=d_0+k_xx+k_yy+k_nr.
\]

The proposed subtraction returns toe–heel \(x\) only if:

1. \(k_y\) is negligible or vertical strike is known;
2. the face-normal term \(k_nr\) is known from a sufficiently accurate face normal;
3. \(k_x\) is calibrated for the current perspective and pose;
4. the shaft-to-face transform, including dynamic lie and local bending, matches the club constant; and
5. both measurements refer to the same instant.

The ball-tangent constraint can remove the face-normal component if D-plane fusion really supplies the correct normal. It does not by itself remove vertical/lie coupling or recover the 3D local shaft-to-face transform. Full calibrated projection is preferable to converting pixels with one local plate scale when shaft and ball lie at different depths.

**Confidence:** High in the ideal cylinder and projection geometry; medium that existing patents do not disclose this exact strike estimator; low-to-medium on real OpenFlight error because the dominant bending, edge and pose-coupling terms lack a relevant published validation.

---

## 4. Iron face curvature

Corke et al. explicitly distinguish a convex driver face from irons with an “absence of bulge and roll” and fit a plane to their 5-iron and 9-iron faces ([author manuscript](https://pure.ulster.ac.uk/ws/files/12610269/Wallace_12601983_.pdf)). That supports planar modelling for those test clubs, not a population-wide manufacturing tolerance.

The [USGA equipment rule](https://www.usga.org/equipment-standards/equipment-rules-2019/equipment-rules/part-2-rule-6.html) requires the striking face to be hard and rigid and generally prohibits concavity, but it does not require every iron face to be mathematically planar or publish a flatness tolerance/radius. Public club specifications generally list loft, lie and groove geometry, not measured bulge/roll radii.

There are deliberate exceptions. A [2026 patent application](https://patents.justia.com/patent/20260241242) proposes horizontal bulge on irons/wedges, with bulge radius in inches approximately equal to loft in degrees (within ±3 inches) and optionally zero vertical roll. Its example implies about a 30-inch (762 mm) radius for a 30° iron. This is a proposed/specialized design, not a survey of clubs in use.

For comparison, an older wood-club patent gives typical driver/wood bulge radii of 8–16 inches and roll radii of 12–18 inches ([US20030013544A1](https://patents.google.com/patent/US20030013544A1/en)). A 10 mm offset on radius \(R\) changes local normal by approximately

\[
\Delta\gamma\simeq\tan^{-1}(10/R).
\]

This is about 2.0° at 286 mm (11.3 inches), consistent with the stated driver effect, but only 0.75° at the specialized 762 mm iron radius. For an actually planar face, the geometric coupling is zero.

This search found **no peer-reviewed 3D-scan survey or manufacturer tolerance dataset reporting bulge and roll radii across modern production 7-irons and 9-irons**. It is therefore unjustified both to assign drivers' 2°/10 mm coupling to irons and to assume every iron is perfectly flat. OpenFlight should store a per-club surface model or at least perform a flatness check; a plane is well supported as the default for conventional irons, not as a universal law.

**Confidence:** High that ordinary irons are conventionally modelled as planar and that specialized curved designs exist; low on population-wide radius/tolerance values because the requested measurements do not appear to be published.

---

## 5. Dynamic-loft distributions for irons

The requested population distributions are surprisingly sparse. Two useful datasets were found, both limited to one golfer:

| Study | Club / player / shots | Dynamic loft result | What it supports |
|---|---|---:|---|
| Leach, Forrester and Roberts, 3D optical tracking ([WSCG 2016 proceedings](https://www.golfsciencejournal.org/api/v1/articles/4973-world-scientific-congress-of-golf-2016.pdf)) | 7-iron; one scratch golfer; 10 shots | 24.4° mean, 1.2° SD | Within-player optical spread |
| Bliss and Langdown, TrackMan vs Mevo+ ([author PDF](https://oro.open.ac.uk/107777/1/1-s2.0-S2772696725000420-main.pdf)) | 7-iron; one 8.7-handicap golfer; 80 retained shots | TrackMan 26.94° ±2.00°, range 22.37–32.36°; Mevo+ 34.71° ±3.24°, range 26.5–40.7° | Within-player spread and strong device dependence |

The disagreement between devices in the second study means “dynamic-loft distribution” is partly a measurement-system distribution. TrackMan publishes professional-tour **means** such as 20.2° for PGA and 23.6° for LPGA 6-irons ([TrackMan](https://www.trackman.com/blog/dynamic-loft)), but those are neither 7-/9-iron distributions nor within/between-player spreads.

At a standard ball radius of 21.335 mm, one degree of normal rotation moves the tangent point by

\[
r\pi/180=0.372\text{ mm/degree}.
\]

Accordingly:

- 1.2° within-player SD corresponds to 0.45 mm tangent-point SD;
- 2.0° corresponds to 0.74 mm;
- the approximately 10° TrackMan range in the Bliss/Langdown sample spans about 3.7 mm;
- a 5° systematic loft assumption error shifts the inferred tangent point about 1.86 mm.

This displacement is along the ball surface in the loft plane. It should not be automatically charged to toe–heel error; its projection into the shaft-perpendicular measurement depends on camera geometry, dynamic lie and the definition of the face axes.

This search found **no credible public dataset giving separate between-player and within-player dynamic-loft distributions for both 7-irons and 9-irons**. Stamped club number also mixes different static lofts across manufacturers and models. A defensible cross-player prior therefore cannot be derived from the cited evidence.

**Confidence:** High in the quoted single-player results and conversion to millimetres; low for generalization to 7-iron populations; very low for 9-iron population spread because no suitable distribution was found.

---

## 6. Vertical impact location specifically

### 6.1 Published method and a real horizontal/vertical asymmetry

Corke, Wallace and Otto provide the most directly relevant validation. Their multi-camera optical system reconstructs the face, obtains the ball center from a marker on top of the ball, and extrapolates to first intersection/tangency. Repeatability for impact position was at most 0.6 mm SD. Against impact tape they reported ([paper](https://journals.sagepub.com/doi/abs/10.1177/1754337118792798), [manuscript](https://pure.ulster.ac.uk/ws/files/12610269/Wallace_12601983_.pdf)):

| Club | Horizontal difference vs tape | Vertical difference vs tape |
|---|---:|---:|
| 5-iron | 0.12 ± 0.43 mm | 1.94 ± 0.53 mm |
| 9-iron | 0.57 ± 0.39 mm | 3.66 ± 0.54 mm |

The authors attribute the vertical difference to **different measurands**: their geometry estimates first contact, while impact tape records the center of the finite contact patch after the ball slides and may roll upward on the lofted face. A loft-based correction reduced mean vertical differences to 0.14 and 0.02 mm, although they considered practical tape comparison nearer ±1 mm because of tape-reading resolution.

This is decisive for OpenFlight. The formula \(c-rn\) estimates the point of first tangency under a rigid sphere/plane model. It does not necessarily estimate the “impact location” shown by tape or by a commercial system near maximum compression. The distinction is small horizontally in the cited iron tests and large vertically.

### 6.2 Commercial systems

Commercial products report both axes. TrackMan defines [Impact Height](https://support.trackmangolf.com/hc/en-us/articles/39724519074843-Parameters-Impact-Height-Tee-to-Green) relative to the geometric center of the face. FlightScope's [X3C material](https://20033507.fs1.hubspotusercontent-na1.net/hubfs/20033507/Product%20Documents/Launch%20Monitor/Flightscope/FlightScope%20X3C%20Sell%20Sheet.pdf) advertises lateral and vertical face-impact coordinates/heat maps. Public Foresight material similarly presents a 2D face-impact point using its camera/marker workflow.

This search found **no public per-axis accuracy specification** from TrackMan, FlightScope or Foresight, and no disclosed evidence that their vertical coordinate is obtained by a materially different sensor route from the horizontal coordinate. Product outputs do not establish equal accuracy or reveal whether the reported point represents first contact, maximum compression, or a calibrated display convention.

### 6.3 Routes materially different from shaft perpendicular

Published or physically distinct vertical cues include:

- reconstructing the whole face plane/mesh and ball trajectory with multiple calibrated cameras, as Corke et al. did;
- detecting the leading edge and ground plane to classify thin versus heavy contact;
- directly observing an impact-tape/spray mark, which measures the contact patch but is no longer markerless and post-impact automated;
- using a dedicated face view or very high-speed contact imagery;
- inferring high/low contact from launch/spin dynamics, which is indirect and coupled to loft, attack, friction, face flex and gear effect.

No published method was found that obtains accurate high–low strike from the shaft axis alone. The along-shaft direction is intrinsically weak for OpenFlight's image primitive, and the contact mechanics make the target definition itself more ambiguous vertically.

**Confidence:** High in the Corke et al. asymmetry and its first-contact/contact-patch explanation; medium that the listed commercial products report both axes; high that their public numerical per-axis accuracy is silent, subject to the usual limitation that proprietary documents were unavailable.

---

## What would break the shaft-axis approach

The shaft-axis method fails as a strike estimator if its low-noise image scalar is stable but is not uniquely caused by toe–heel displacement. That is the central risk. The following are concrete failure modes, ordered roughly by seriousness.

1. **The scalar mixes axes.** Dynamic lie and vertical strike can project into the direction perpendicular to the shaft. If \(k_y\) in the measurement equation is not negligible, two different 2D face strikes produce the same shaft-to-ball distance. A single club constant cannot separate them.

2. **The visible “axis” is not the head's local axis.** Shaft droop/lag makes a long fitted chord differ from the local tip/hosel tangent. A line can be temporally smooth to 0.40 px yet have a pose-dependent absolute offset at the face.

3. **D-plane fusion supplies the wrong face normal.** The tangent construction inherits every face-angle and dynamic-loft error. D-plane inversion is a ball-flight model involving spin loft, friction, gear effect and measurement timing; it is not a direct observation of a rigid face normal. Curved faces make the normal depend on the strike being solved for.

4. **A “club constant” is not constant dynamically.** Shaft deflection, dynamic lie, face closure, shaft-to-face manufacturing geometry, club length and perspective can change the projected shaft-to-sweet-spot offset. A per-club static calibration is insufficient if that mapping varies with delivery.

5. **The ball center is precisely wrong.** Known radius fixes short-arc conditioning, not a contour displaced by mat shadow, grass, ball markings, blur, lens distortion or an inappropriate circle model. Smooth residuals hide repeatable bias.

6. **The target has the wrong semantics.** \(c-rn\) is first tangency. Tape and some commercial displays may represent the center of the compressed/sliding contact patch. Corke et al. observed a 1.94–3.66 mm vertical difference before correction on irons.

7. **2D distance is converted as though the geometry were planar.** One plate scale is exact only in a chosen plane under an appropriate camera model. Shaft and ball depth differences, radial distortion and rolling-shutter timing can create systematic millimetre errors.

8. **The wrong pixels define the shaft.** Specular edges, motion blur, occlusion by the head, taper, ferrule steps and shadows can shift both fitted edges together. A narrow line-fit confidence interval measures consistency, not physical-axis accuracy.

9. **Timing is mismatched.** Radar ball launch, D-plane normal, ball-center frame and shaft line must correspond to a common impact state. At golf-club speed, a small timestamp or rolling-shutter error can overwhelm subpixel spatial precision.

10. **The method only works for a restricted club family.** Offset hosels, unusual shaft insertion, curved-face irons, hybrids, woods and user-specific shaft bending may each require a different mapping. “Shaft axis minus constant” may remain useful for conventional irons without generalizing.

### Falsification tests before treating the output as strike location

The approach should be considered validated only if it survives all of these tests on held-out shots:

- use a calibrated mechanical or optical ground truth and independently vary toe–heel, high–low, dynamic lie and dynamic loft; fit the full cross-sensitivity matrix rather than only correlation with toe–heel;
- refit the shaft using several segment lengths and positions; extrapolated impact must not move materially when the chosen shaft segment changes;
- repeat a fixed physical ball center across mats, lighting directions, tee heights and off-axis image positions to expose contour bias;
- test multiple samples of each club and shafts of different stiffness, not only repeated shots with one club/golfer;
- specify whether ground truth is first tangency or contact-patch center and compare like with like;
- report absolute error and bias by face axis, not only smooth-trajectory residual or total 2D distance;
- require the toe–heel mapping to remain stable after conditioning on high–low strike, lie, loft, speed and camera position.

The current 0.40 px shaft-perpendicular residual establishes that OpenFlight has found a much more repeatable image feature than the head centroid or hosel. It does **not yet establish that the feature is an unbiased toe–heel strike measurement**. The key next experiment is not a larger correlation study; it is an intervention study that moves each geometric variable independently and measures cross-talk.
