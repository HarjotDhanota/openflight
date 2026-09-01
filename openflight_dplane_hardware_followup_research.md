# OpenFlight follow-up: can this hardware support a D-plane inversion?

**Date:** 26 August 2026  
**Scope:** One rear mono camera, one TI IWR6843LEVM FMCW radar, one OPS243-A CW radar, and the current 22-shot 7-iron/9-iron session. This report treats the earlier club-pose and shaft-axis reports as established. In particular, it does not rehabilitate silhouette-derived orientation or the shaft-perpendicular strike proxy.

## Executive conclusion

The present system can plausibly estimate ball launch, and it may estimate the translational trajectory of a selected club image/radar feature. It does **not yet measure the full pre-impact state required to regard an inverted D-plane as face-angle or dynamic-loft ground truth**. The missing distinction is between the velocity of an observed centroid or radar scattering centre and the velocity of the actual point on the clubface that contacts the ball. Those differ by the head's angular-velocity term, and neither a rear silhouette with incoherent pitch nor four or five range samples supplies that term reliably.

The apparent 7-iron/9-iron “dynamic-loft compression” is also not established. With the adopted vertical D-plane relation, a 3.5° launch-angle gap and a normal small difference in attack angle mathematically imply almost exactly the observed 4.4° delivered-loft gap. Published PGA averages give a 7-iron/9-iron vertical-launch gap of about 4.1°, although those averages are not a same-player controlled study. Static loft is not delivered loft. Therefore the current result has at least two viable explanations: this golfer really delivered only about 4–5° more loft with the 9-iron, or the IWR vertical-angle pipeline compressed the ball-launch difference. The current data do not distinguish them.

The 0.69 horizontal and 0.81 vertical coefficients are useful empirical first-order values, not universal collision constants. A full oblique-impact model is physically better, but it introduces effective club mass, club inertia, face geometry, coefficient of restitution, friction/contact regime, impact point, and club angular velocity. With no independent ball spin, no trustworthy club orientation, no club angular velocity, and no post-impact club state, fitting that model jointly would make the answer *less* identifiable, not more.

The most important immediate checks are therefore inexpensive reprocessing experiments: reconstruct an independent vertical ball trajectory from camera rays plus radar range; verify whether the OPS value passed to the ballistic model is radial or deprojected speed; jackknife every four/five-frame club fit; sweep plausible D-plane coefficients; remove the two attack-angle failures; and cross-tab all sensor/model failures by shot. These tests can falsify the current interpretation without adding hardware.

### Empirical premises carried into this report

| Item | Current observation | Interpretation used here |
|---|---|---|
| Silhouette CAD orientation | 349 frames; 37% imply impossible dynamic loft, with face-normal elevation from about -70° to +60°. Restricting loft to 5–45° removes pitch incoherence but rejects 37%. | Orientation is abandoned; a bounded answer is not an observed answer. |
| Shaft-perpendicular strike proxy | Smash-factor \(R^2=0.01\)–0.23, with inconsistent coefficient signs between clubs. | Failed. The projected perpendicular contains vertical and face-normal cross-talk, so it is not retained as a strike measurement. |
| Hardware | IWR6843LEVM, roughly 3.2 GHz sweep and 4.69 cm range bins; OPS243-A ball speed; 320×200 global-shutter rear camera at 467.6 fps and 0.296 px/mm; Raspberry Pi. | All proposed processing and limits are specific to this package. |
| Club data availability | Horizontal launch 22/22; IWR **lcmf_v1** vertical launch 22/22; fused club path 21/22; attack angle 21/22. Radar-only azimuth rejected 22/22 for insufficient phase span. | Availability is not accuracy; radar-only path has already failed its own observability gate. |
| Attack angle | Median -4.58°, with gross values at -31° and +25°. | Use robust estimates and jackknife stability before inversion. |
| D-plane inverse | Face median -1.89° (range -9.96° to +6.00°); dynamic loft median 26.9° (range 16.9° to 34.9°). | Credible central values, but no external truth. |
| Collision spin-loft check | Speed-ratio model median 32.7° versus D-plane median 31.0°; \(r=0.45\), difference SD 4.5°. | Weak consistency only; it does not validate face or strike. |
| Club separation | Dynamic-loft gap +4.4°, vertical-launch gap +3.5°, versus a nominal static-loft gap near 8°. | The static-to-dynamic comparison is a hypothesis, not ground truth. |
| “Implied COR” | Median 0.732, range 0.54–0.92. | A strong model/input stress signal, not a direct COR measurement. |
| Spin | Project formula reproduces itself exactly. | Excluded as independent validation evidence. |

---

## 1. True 3D club path and attack angle from this sensor set

### 1.1 What should be fused

All measurements first need timestamps and one calibrated Cartesian frame. Let the target frame use \(x\) to the golfer's right, \(y\) upward, and \(z\) down the intended target line. Let the calibrated camera centre be \(\mathbf C\), the IWR phase/range centre be \(\mathbf R\), and the camera ray for image coordinate \((u,v)\) be

\[
\mathbf r(u,v)=\operatorname{normalize}\!\left(K^{-1}[u,v,1]^T\right),
\]

after lens correction and application of the camera-to-target rotation. A point observed by the camera lies on

\[
\mathbf p=\mathbf C+\lambda\mathbf r.
\]

If the IWR range \(\rho\) refers to the **same physical point**, then \(\lVert\mathbf p-\mathbf R\rVert=\rho\). With \(\mathbf d=\mathbf C-\mathbf R\), the relevant positive ray/sphere intersection is

\[
\lambda=-\mathbf r^T\mathbf d+
\sqrt{(\mathbf r^T\mathbf d)^2-(\lVert\mathbf d\rVert^2-\rho^2)}.
\]

This gives one 3D point per synchronized observation. A batch fit over the pre-impact interval can then estimate \(\mathbf p_0\), velocity \(\mathbf v\), and—only if the data support it—acceleration. Range rate adds the observation

\[
\dot\rho=\mathbf v^T\frac{\mathbf p-\mathbf R}{\rho}.
\]

The fit should be weighted by each measurement's calibrated uncertainty, rather than by a nominal range-bin size. A 4.69 cm bin is a resolution-cell spacing, not a statement that the centre of a clean isolated target is uncertain by 4.69 cm. Conversely, it is not evidence for millimetre accuracy in a multipath metal-club scene; that uncertainty must be measured.

The OPS243-A ball-speed observation is unambiguous **after impact**, but it cannot constrain the pre-impact club vector merely because it is a speed measurement. It can help identify an isolated club Doppler return only if the OPS separately observes and labels that club return. Otherwise it belongs to the ball-flight fit, not the club fit.

### 1.2 Converting the velocity vector to path and attack

For the fitted velocity \(\mathbf v=(v_x,v_y,v_z)\), define

\[
P=\operatorname{atan2}(v_x,v_z),\qquad
A=\operatorname{atan2}\!\left(v_y,\sqrt{v_x^2+v_z^2}\right).
\]

Here \(P\) is horizontal club path and \(A\) is attack angle, subject to OpenFlight's sign convention. The corresponding Jacobian rows are

\[
\frac{\partial P}{\partial\mathbf v}=
\left[
\frac{v_z}{v_x^2+v_z^2},\,0,\,-\frac{v_x}{v_x^2+v_z^2}
\right],
\]

\[
\frac{\partial A}{\partial\mathbf v}=
\left[
-\frac{v_yv_x}{h\lVert\mathbf v\rVert^2},
\frac{h}{\lVert\mathbf v\rVert^2},
-\frac{v_yv_z}{h\lVert\mathbf v\rVert^2}
\right],\quad h=\sqrt{v_x^2+v_z^2}.
\]

If the fitted velocity covariance is \(\Sigma_v\), the angular covariance is approximately \(J\Sigma_vJ^T\). Near the target line and at small angles, the useful intuition is

\[
\sigma_P\simeq\frac{\sigma_{v_x}}{V},\qquad
\sigma_A\simeq\frac{\sigma_{v_y}}{V}.
\]

The uncertainty in \(\mathbf p\) should include range, image localisation, intrinsic calibration, camera/radar extrinsics, synchronization, and target-association terms. In the co-located approximation,

\[
\Sigma_p\simeq
\mathbf r\mathbf r^T\sigma_\rho^2+
\rho^2J_r\Sigma_{uv}J_r^T+\Sigma_{\text{calibration}}.
\]

The exact non-co-located expression follows by differentiating the ray/sphere intersection. Monte Carlo propagation using measured calibration residuals is likely more honest than retaining only this first-order expression.

### 1.3 Are four or five frames enough?

They are enough to define a straight line; they are not automatically enough to validate its slope or the identity of the tracked point. For \(N\) equally spaced positions with independent per-axis error \(\sigma_p\), a constant-velocity least-squares fit has

\[
\operatorname{Var}(v)=
\frac{12\sigma_p^2}{N(N^2-1)\Delta t^2}.
\]

At \(\Delta t=3\) ms, the standard-deviation multipliers are approximately \(149.1\,\sigma_p\) s\(^{-1}\) for four frames and \(105.4\,\sigma_p\) s\(^{-1}\) for five. At a 35 m/s club speed:

| Effective 3D position error | Five-frame velocity error | Approximate angular error |
|---:|---:|---:|
| 1 mm | 0.105 m/s | 0.17° |
| 5 mm | 0.527 m/s | 0.86° |
| 10 mm | 1.05 m/s | 1.72° |
| 20 mm | 2.11 m/s | 3.45° |

These are optimistic because target swaps, correlated calibration errors, aspect-dependent radar scattering, and head rotation are not independent zero-mean localisation noise. Millimetre-scale image localisation also does not imply millimetre-scale 3D localisation when depth and extrinsics dominate.

There is no literature-backed universal “minimum number of frames.” Two points identify constant velocity; three can identify constant acceleration; more are required for outlier rejection and model checking. In the relevant 7-iron study, Wood, Henrikson and Broadie estimated impact quantities using high-speed Vicon data and a five-point forward-difference extrapolation, but those were five high-precision optical samples at 720 Hz from a marker-defined rigid body—not four or five centimetre-scale radar samples [Wood et al.](https://pdfs.semanticscholar.org/3d4d/e973f3d17dc5be3eb63765c3145977128630.pdf). OpenFlight's current all-shot radar-azimuth rejection for insufficient phase span is direct empirical evidence that the present four/five-frame interval is not sufficient for that radar-only path estimator. A synthetic minimum such as “four frames/24 snapshots” is a design threshold, not field validation.

### 1.4 The larger conceptual problem: which point is moving?

The D-plane collision uses the pre-impact velocity of the contact point on the face:

\[
\mathbf u_c=\mathbf v_{CG}+\boldsymbol\omega_c\times\mathbf r_{contact}.
\]

The camera centroid and IWR scattering centre need not be that point, need not coincide with each other, and may move over the head as aspect changes. Radar studies of complex metal objects explicitly treat scattering-centre motion and aspect dependence as a target-modelling issue [Saal et al., IEEE Access](https://doi.org/10.1109/ACCESS.2022.3211673). The difference is not academic: a point 25 mm from the rotation reference moving at 2,500°/s differs by about 1.09 m/s (2.4 mph) from the reference before any translational error is considered.

Therefore the correct output label at present is “trajectory of a fused observed club feature.” Calling it true contact-point path/AoA requires either a rigid head pose and angular-velocity estimate, or an empirical demonstration that the observed point-to-contact correction is negligible and stable.

**Finding:** the mathematical fusion is straightforward once measurements refer to one point, but that same-point assumption is currently unproved. Four or five frames can support a low-noise straight-line fit; the all-shot radar-only failure and two gross AoA failures show that they do not yet support a trustworthy general estimator on these data.

---

## 2. Why does dynamic loft appear compressed?

### 2.1 The 4.4° result follows from the measured launch gap

The adopted vertical relation is

\[
V=aL+(1-a)A,
\]

where \(V\) is vertical launch, \(L\) is dynamic loft, \(A\) is attack angle, and \(a=0.81\). Thus

\[
L=A+\frac{V-A}{a},
\qquad
\Delta L=\Delta A+\frac{\Delta V-\Delta A}{a}.
\]

For the measured \(\Delta V=3.5°\), a modest club-to-club attack-angle difference \(\Delta A=-0.4°\) gives

\[
\Delta L=-0.4+\frac{3.5-(-0.4)}{0.81}=4.41°.
\]

That is the reported result. To obtain an 8° dynamic-loft gap from the same 3.5° launch gap at \(a=0.81\) would require \(\Delta A\approx-15.7°\), which is not a plausible 7i-to-9i mean difference. Holding \(\Delta A=-0.4°\), the required coefficient would be \(a\approx0.464\), far below the published vertical ratios for irons and wedges.

Attack-angle noise can make individual inferred lofts noisy, especially with the \(-31°\) and \(+25°\) failures. It is unlikely to explain the **mean club gap** unless it contains a club-dependent bias. The local sensitivity is

\[
\frac{\partial L}{\partial A}=-\frac{1-a}{a}=-0.235.
\]

Even a 4° attack-angle bias changes inferred dynamic loft by only about 0.94°. The two gross failures must still be removed or robustly down-weighted, but ordinary scatter is not the leading explanation for a missing 3.6° of club separation.

### 2.2 Static loft is the wrong reference quantity

A nominal 7i/9i static-loft gap of roughly 8° does not imply an 8° delivered dynamic-loft gap for one golfer. Shaft deflection, handle position, club design, ball position, and the player's release can change both clubs differently. The current data do not independently measure delivered loft, so calling 4.4° “55% compression” assumes the conclusion.

As a weak external reality check, TrackMan's published PGA Tour averages are 16.3° vertical launch for a 7-iron and 20.4° for a 9-iron—a 4.1° gap, close to OpenFlight's 3.5° [TrackMan tour averages](https://support.trackmangolf.com/hc/en-us/articles/5089752464667-Shot-Analysis-Tour-Averages-On-PGA-LPGA-Tour). Those are aggregated tour averages, not a same-player experiment, and therefore cannot validate this golfer's measurement.

A controlled robot study by Corke and colleagues provides a useful but different comparison. Its 5-iron and 9-iron effective lofts differed by about 15.55°, attack angles by \(-1.36°\), and launch angles by about 10.13°. The implied between-club transfer is nearer 0.68 than 0.81, illustrating that a secant club-to-club ratio need not equal the local 7-iron coefficient [Corke et al., *Proceedings of the Institution of Mechanical Engineers, Part P*](https://journals.sagepub.com/doi/10.1177/1754337118792798). It is a robot 5i/9i comparison, not evidence that OpenFlight should have an 8° 7i/9i launch or dynamic-loft gap. I did not find a suitably controlled, published same-player distribution for 7i versus 9i delivered loft and launch.

### 2.3 Ranked explanations and how to distinguish them

1. **The golfer delivered a real 4–5° loft gap.** This is entirely compatible with the algebra and is not contradicted by the observed 3.5° launch gap. Only an independent face-pose reference can confirm it.
2. **The IWR vertical-launch estimator compressed the ball-launch gap.** This remains credible because the current 22 shots contain no vertical-angle truth, and because the estimator depends on sparse elevation aperture, multipath modelling, geometry, and an external speed input.
3. **A coefficient inappropriate to these clubs shifted the scale.** Plausible coefficient changes move the gap, but a coefficient near 0.46 would be needed to recover 8°; that is not credible under the published impact ratios. Coefficient uncertainty is a secondary, perhaps one-degree-scale issue here—not a complete explanation.
4. **Attack-angle error compressed the mean gap.** Random attack error does not do this systematically, and the coefficient attenuates rather than amplifies it. A club-correlated bias of roughly 16° would be needed under the current equation, although gross individual failures still invalidate those shots.

The decisive inexpensive discriminator is an independent ball vertical-trajectory estimate. Combine the rear camera's calibrated vertical ray with IWR range over multiple ball-flight frames, without using the LCMF elevation result, and fit a gravity-aware trajectory. If that estimate also gives a roughly 3.5–4° 7i/9i gap, the label “compression” is probably wrong. If it gives a substantially larger gap while LCMF remains at 3.5°, the vertical estimator is compressing the output.

**Finding:** the data show a small vertical-launch gap. They do not yet show compressed dynamic loft. Changing the 0.81 coefficient or cleaning attack-angle scatter cannot plausibly transform the current means into an 8° delivered-loft gap.

---

## 3. Is linear D-plane inversion the right formulation?

### 3.1 What the linear relation is good for

The empirical D-plane relation is an excellent diagnostic and first-order estimator near the conditions on which its coefficients were measured. It makes the main physical point: ball launch is closer to the face normal than to the club-path vector. Its inverse is stable when launch and club direction are independently known.

It is not a full impact law. Applying 0.69 horizontally and 0.81 vertically as two independent scalars hides the fact that one 3D normal/tangential collision produces both components. Total spin loft, effective mass, friction, head rotation and strike position couple the horizontal and vertical responses.

### 3.2 A compact oblique-impact model

Let \(s\) be spin loft—the angle between the incoming contact-point velocity and face normal—and \(U\) the incoming speed. In Penner's idealised two-dimensional model, resolve the outgoing ball velocity into face-normal and face-tangential components:

\[
v_{bn}=\frac{(1+e)U\cos s}{1+m/M_{eff}},
\]

\[
v_{bt}=\frac{-U\sin s}
{1+m/M_{eff}+mR^2/I_b}.
\]

Here \(e\) is normal coefficient of restitution, \(m\), \(R\), and \(I_b\) are ball mass, radius, and moment of inertia, and \(M_{eff}\) is the club's effective mass in the impact direction. Under the model's rolling/no-slip assumption,

\[
\omega_b=-\frac{mRv_{bt}}{I_b}.
\]

The launch angle relative to club path is

\[
\ell=s-\tan^{-1}(k\tan s),
\]

with

\[
k=\frac{1+m/M_{eff}}
{(1+e)(1+m/M_{eff}+mR^2/I_b)}.
\]

This form shows why a constant launch ratio is only locally valid: the relation is nonlinear in spin loft, and \(k\) changes with restitution and effective mass. The assumptions, derivation and empirical restitution model are described by Penner [“The physics of golf: The optimum loft of a driver”](https://www.raypenner.com/golf-loft.pdf).

A convenient 3D reduced form is

\[
\mathbf v_b=A(\mathbf u_c^T\mathbf n)\mathbf n+
B\left[\mathbf u_c-(\mathbf u_c^T\mathbf n)\mathbf n\right],
\]

where \(\mathbf n\) is the face normal and \(A\) and \(B\) are normal and tangential collision responses. Unlike two unrelated horizontal/vertical coefficients, this formulation uses one normal and total 3D obliqueness. But \(A\) and \(B\) are not universal constants; they contain restitution, effective mass/inertia, friction, ball properties and the contact regime.

### 3.3 Full rigid-body impulse equations

For impulse \(\mathbf J=J_n\mathbf n+\mathbf J_t\), the ball and club satisfy

\[
m_b(\mathbf v_b^+-\mathbf v_b^-)=\mathbf J,
\qquad
I_b(\boldsymbol\omega_b^+-\boldsymbol\omega_b^-)=\mathbf r_b\times\mathbf J,
\]

\[
m_c(\mathbf v_c^+-\mathbf v_c^-)=-\mathbf J,
\qquad
I_c(\boldsymbol\omega_c^+-\boldsymbol\omega_c^-)=-\mathbf r_c\times\mathbf J.
\]

The relative contact velocity includes both bodies' translations and rotations. Normal restitution imposes

\[
\mathbf v_{rel}^+\!\cdot\mathbf n=-e\,\mathbf v_{rel}^-\!\cdot\mathbf n.
\]

Tangential contact is either sticking, with final tangential relative velocity approximately zero, or sliding, with \(\lVert\mathbf J_t\rVert\leq\mu J_n\) and impulse opposite slip. More detailed models add tangential compliance/restitution, face grooves, ball deformation and finite contact duration. A modern treatment of golf impact using rigid-body impulse and compliant contact models is given by Winfield and colleagues [MDPI Engineering Proceedings](https://www.mdpi.com/2504-3900/49/1/47/htm).

Strike position enters through \(\mathbf r_c\) and the direction-dependent effective mass. For a normal impulse,

\[
\frac{1}{M_{eff,n}}=
\frac{1}{m_c}+(\mathbf r_c\times\mathbf n)^T I_c^{-1}(\mathbf r_c\times\mathbf n).
\]

An off-centre strike therefore changes ball speed, head rotation, launch and spin. This is why a single quadratic smash-factor proxy cannot isolate toe/heel position when loft, vertical offset, face normal and angular motion vary simultaneously.

### 3.4 Can the full model solve face, loft and strike jointly here?

Not uniquely. A forward impact model can predict ball launch and spin from a known club state and contact. Its inverse needs enough independent observations to estimate its additional unknowns. OpenFlight presently lacks:

- independent 3D ball spin; the current spin is computed from the same kinematics and is circular evidence;
- trustworthy face normal and full clubhead pose;
- clubhead angular velocity and true contact-point velocity;
- post-impact club velocity/rotation;
- calibrated club mass, centre of gravity, inertia tensor and face surface;
- a known or independently constrained restitution/friction/contact regime.

If independent outgoing ball velocity and spin were available, then for a stationary pre-impact ball, \(\mathbf J=m_b\mathbf v_b^+\) and \(I_b\boldsymbol\omega_b^+=(-R\mathbf n)\times\mathbf J\) would constrain the face normal and tangential impulse. That would still require assumptions for club dynamics and contact. With spin generated by the model itself, those equations add no new information.

The coefficient-of-restitution self-check has the same identifiability problem. True \(e\) is a ratio of **relative normal velocities before and after impact**, including the club's post-impact velocity. An “implied \(e\)” from ball speed, pre-impact club speed and assumed effective mass folds errors in face normal, strike, target association and speed projection into one number. USGA measurements show test-dependent driver COR values can exceed 0.85 at lower impact speed, while the conformance characteristic-time procedure uses its own specified limit; “above 0.85 is physically impossible” is therefore too strong [USGA collision explainer](https://rulesworkshops.usga.org/content/usga/home-page/articles/2025/05/what-happens-collision-between-club-ball.html), [USGA speed/flexibility study](https://www.usga.org/content/dam/usga/pdf/Equipment/R22-04%20Effects%20of%20club%20flexibility%20at%20different%20swing%20speeds.pdf). An inferred 0.92 is nonetheless a strong warning that the simplified inverse, the inputs, or both are inconsistent.

**Finding:** use the linear D-plane as a transparent, uncertainty-bounded estimator and diagnostic. A full collision model is the right forward physics but, with current observables, a joint inverse for face, loft and strike is underdetermined and would be driven by priors.

---

## 4. How the D-plane coefficients depend on the impact

### 4.1 Published empirical variation

Wood, Henrikson and Broadie measured 157 golfers using Vicon at 720 Hz and a GC2, retaining relatively central 7-iron strikes. Their fitted mean launch ratios were approximately:

| Club/condition | Vertical face-normal share | Horizontal face-normal share |
|---|---:|---:|
| Driver | 83% ± 8% | 76% ± 8% |
| 7-iron, golfers | 81% ± 5% | 69% ± 3% |
| 7-iron, robot | — | 63% ± 4% |
| Wedge | 72% ± 6% | 61% ± 7% |

The player 7-iron horizontal fit was strong, and the robot result demonstrates that the exact coefficient is condition-dependent. The study also found that the ratio decreased as impact became more oblique and stated that the relationship was nonlinear [Wood et al.](https://pdfs.semanticscholar.org/3d4d/e973f3d17dc5be3eb63765c3145977128630.pdf). The published paper did not supply a universal fitted function that can simply replace OpenFlight's constants.

The study's filtering matters. It retained strikes within about 6.35 mm of face centre and required enough separation between face and path to avoid unstable ratios. Those coefficients should not be treated as validation for large toe/heel or high/low misses, nor extrapolated to near-zero spin loft without uncertainty.

### 4.2 Mechanism-based functional form

Penner's idealised model yields a secant launch ratio

\[
a(s)=\frac{\ell}{s}=
1-\frac{\tan^{-1}(k\tan s)}{s},
\]

and a local differential ratio

\[
\frac{d\ell}{ds}=1-
\frac{k\sec^2s}{1+k^2\tan^2s}.
\]

This is a useful physical explanation, not a published all-club calibration. It predicts dependence on total 3D spin loft through \(s\), restitution and speed through \(e\), and club/strike through \(M_{eff}\). Penner used an empirical speed-dependent restitution relation of approximately \(e=0.86-0.0029U\cos s\) for the data available to that model; it should not be transplanted to modern balls and irons without calibration.

The main dependencies are:

- **Club and total spin loft:** higher obliqueness increases the tangential component and generally lowers the face-normal share. Horizontal and vertical shares cannot be varied independently without changing the same 3D collision.
- **Normal impact speed:** ball and face deformation change restitution; the effect is primarily tied to the normal component \(U\cos s\), not raw club speed alone.
- **Strike position:** off-centre contact changes directional effective mass and rotates the head, producing gear-effect spin and changing launch.
- **Head mass and inertia:** two heads with the same loft need not have the same effective mass at a given contact point.
- **Friction, face condition and ball:** wetness, roughness, grooves, cover construction and the transition between sticking and sliding affect the tangential impulse.
- **Measurement definition:** a ratio of two small angular differences is unstable near zero face-to-path or spin-loft separation, which is why study filters matter.

### 4.3 Recommended use in OpenFlight

Do not select one replacement constant from another club. Report a coefficient sensitivity envelope. For the present vertical result, reprocess at least \(a_v=0.68\) to \(0.86\); for horizontal, include the Wood robot/player spread and plausible wedge/driver values. If the sign or shot classification changes across that envelope, the inferred face quantity is not robust. If only the absolute value moves modestly, report that systematic uncertainty separately from sensor noise.

Longer term, calibrate a regularised 3D normal/tangential model against an independent reference across club, speed and strike. A fitted function must be held out by golfer and club; otherwise it will absorb errors in path, launch and face pose while appearing physically plausible.

**Finding:** 0.69/0.81 are defensible first-order 7-iron priors. They are not universal, and the available literature supports a nonlinear obliqueness-dependent response rather than one constant. No published universal coefficient function eliminates the need for calibration.

---

## 5. From face orientation to impact location

### 5.1 Tangency gives a contact point, but not face coordinates

Let the ball centre at first contact be \(\mathbf B\), ball radius \(R\), and unit face normal \(\mathbf n\) point from the face toward the ball. For a planar face tangent to the sphere, the contact point is

\[
\mathbf T=\mathbf B-R\mathbf n.
\]

The face plane satisfies

\[
\mathbf n^T\mathbf X=\mathbf n^T\mathbf B-R.
\]

This determines the tangent plane's normal and offset. It does **not** determine where the face centre, grooves, toe or crown lie within that plane. Two translational degrees of freedom remain. Consequently, face angle + dynamic loft + ball position + tangency are insufficient to produce toe/heel and high/low strike coordinates.

Let \(\mathbf O\) be a defined face origin—normally the geometric or scoreline-defined face centre—and let \(\mathbf e_t\) and \(\mathbf e_u\) be unit axes toward the toe and upward along the face. Once the club's full rigid transform is known,

\[
x_{strike}=(\mathbf T-\mathbf O)^T\mathbf e_t,
\qquad
y_{strike}=(\mathbf T-\mathbf O)^T\mathbf e_u.
\]

This is the missing step. The unknown \(\mathbf O\) cannot be recovered from the face normal. It requires the clubhead's 3D translation and orientation plus a per-club face coordinate system.

For a curved driver face or any non-planar surface \(\mathbf S(x,y)\), the calculation is a surface/sphere contact solve rather than a plane projection: the candidate point must lie a radius from \(\mathbf B\), and the surface normal at that point must align with the ball-centre direction. Bulge, roll and local normal then affect both strike position and D-plane orientation.

### 5.2 Additional geometry and measurements required

- A per-club CAD or calibrated measurement of face origin, toe/up axes and surface shape.
- The rigid shaft-to-head transform, including lie, loft and any shaft bend/deflection at impact.
- A full 6-DoF head pose at impact, not merely face angle and dynamic loft.
- Ball centre in the same 3D frame at first contact, including timing interpolation.
- A precise definition of strike coordinates and, for curved faces, a contact-surface solver.

Corke and colleagues implemented the published experimental version of this method with multiple calibrated high-speed cameras, rigid-body markers defining the face coordinate system, and a measured ball centre extrapolated to first contact. Their repeatability was sub-millimetre for strike location and sub-degree for delivered club variables [Corke et al.](https://journals.sagepub.com/doi/10.1177/1754337118792798). The critical ingredient was the measured rigid face coordinate system, not tangency alone.

### 5.3 Error propagation

Let \(E=[\mathbf e_t,\mathbf e_u]\) and \(\mathbf q=E^T(\mathbf B-R\mathbf n-\mathbf O)\). A first-order perturbation is

\[
\delta\mathbf q\simeq
E^T(\delta\mathbf B-\mathbf n\,\delta R-R\,\delta\mathbf n-\delta\mathbf O)
+\delta E^T(\mathbf T-\mathbf O).
\]

For a small rotation \(\delta\boldsymbol\theta\), \(\delta\mathbf n\simeq\delta\boldsymbol\theta\times\mathbf n\). A one-degree normal error moves the tangent point on a 21.35 mm-radius ball by only about 0.37 mm. This is not the dominant present uncertainty. The unmeasured in-plane origin \(\mathbf O\) enters strike coordinates one-for-one and is currently unbounded; the silhouette fit's rejected/incoherent orientation adds further degree-scale error; synchronization and ball-centre interpolation add translation error.

The failed shaft-perpendicular proxy is consistent with this geometry. An image-plane perpendicular is a projection containing toe/heel, vertical, depth and face-normal components. Without the head transform and face origin, it is not a strike coordinate.

**Finding:** the tangent point equation is simple. Strike *coordinates* require the missing face origin and axes from a full head pose. Current hardware/output cannot recover them from the D-plane normal alone.

---

## 6. Minimum validated sensor set

### 6.1 Exact proposed combination

I did not find a peer-reviewed validation showing quantified face-angle and dynamic-loft accuracy from exactly one rear mono camera + one mmWave FMCW radar + a separate CW ball-speed radar, markerlessly. Absence of a paper is not proof that the configuration cannot work, but it means there is no published accuracy claim OpenFlight can inherit.

A 2026 patent describes camera/radar fusion that obtains club and ball velocity vectors and derives a clubface normal, including face angle and dynamic loft [US patent application 20260195924](https://patents.justia.com/patent/20260195924). It is highly relevant prior art, but a patent's enabling description is neither an independent validation nor evidence of accuracy on OpenFlight's geometry and sampling.

Commercial systems show that compact radar/camera combinations can output related quantities, but their proprietary algorithms and public comparison studies do not establish this exact sensor minimum. Device-agreement studies also cannot identify which unit is correct without a traceable reference. For example, a recent comparison of launch monitors found poor absolute agreement for some club-delivery variables, including dynamic loft; it is an agreement study rather than a markerless sensor proof [Bliss and Langdown](https://pmc.ncbi.nlm.nih.gov/articles/PMC13227226/).

### 6.2 What has actually been demonstrated with quantified accuracy

The strongest directly relevant demonstrations use multiple calibrated high-speed cameras and rigid markers:

- Wood et al.: Vicon at 720 Hz plus a GC2, used to quantify the D-plane launch ratios [Wood et al.](https://pdfs.semanticscholar.org/3d4d/e973f3d17dc5be3eb63765c3145977128630.pdf).
- Corke et al.: three calibrated high-speed cameras and club markers, reporting repeatability around 0.2° face angle, 0.5° dynamic loft, 0.2° path, 0.1° attack angle and sub-millimetre impact location [Corke et al.](https://journals.sagepub.com/doi/10.1177/1754337118792798).
- Independent commercial validation commonly uses a multi-camera optical tracking system as the reference rather than assuming a launch monitor is truth [Leach et al., *Measurement*](https://www.sciencedirect.com/science/article/pii/S0263224117305079).

Patented lower-camera-count systems often add visible shaft/club patterns or stereo geometry. For example, a Rapsodo patent describes stereo imaging with a shaft pattern and claims roughly degree-scale orientation capability [US 11,077,351](https://uspto.report/patent/grant/11%2C077%2C351). Again, a patent claim is not an independent accuracy validation.

### 6.3 What “minimum” means for OpenFlight

There is no sensor-count theorem. The minimum is the smallest set that independently observes the required degrees of freedom at the required precision. For ball velocity, camera bearing plus synchronized range/range-rate can be enough. For face angle and dynamic loft, one needs either:

1. a direct face/head orientation observation; or
2. a sufficiently constrained inverse collision with independent 3D ball velocity **and spin**, calibrated club dynamics and strike; or
3. multiple rigidly associated club points whose 3D motion yields head pose and angular velocity.

OpenFlight currently has none of those three in validated form. A second camera with a deliberately observable shaft/head pattern would add direct geometric observability. A radar with materially larger, calibrated two-dimensional aperture may add orientation-sensitive scattering information, but that requires a new validated model rather than simply more nominal virtual channels. Independent high-speed stereo or a short marker-based calibration campaign is the clearest reference path even if the eventual consumer system remains markerless and single-camera.

**Finding:** no published validation supports the exact minimal hardware claim. The least convincing-by-analogy systems are patents and proprietary products; the least hardware with strong published quantitative evidence still uses multi-camera, marker-defined club pose.

---

## 7. Vertical launch from the IWR6843LEVM

### 7.1 “12 virtual elements” is not a 12-element elevation array

The actual OpenFlight board/configuration is the IWR6843LEVM. It has 3 TX × 4 RX = 12 virtual channels overall, but the physical virtual-array geometry does not place all 12 at distinct vertical coordinates. The ISK/LEVM-style geometry provides eight azimuth positions and only two effective elevation positions. TI's own material quotes about 15° azimuth resolution and 58° elevation resolution for the LEVM [IWR6843LEVM user guide](https://www.ti.com/lit/ug/swru585/swru585.pdf); TI further explains that the ISK has only two distinct elevation positions, whereas other layouts such as ODS/AOP provide more vertical positions [TI antenna-module comparison](https://www.ti.com/document-viewer/lit/html/SWRA758), [TI elevation-resolution discussion](https://e2e.ti.com/support/sensors-group/sensors/f/sensors-forum/784351/iwr6843isk-what-is-the-elevation-resolution).

The quoted 58° is two-target angular resolution, not the accuracy with which one high-SNR isolated target can be localised. Equally, “12 channels” cannot be inserted into a 12-element vertical-array accuracy formula for this board.

### 7.2 An optimistic information bound

For an ideal half-wavelength uniform linear array with \(N\) distinct elements, \(K\) coherent snapshots and per-snapshot SNR \(\rho\), a common single-source Cramér–Rao scaling is

\[
\sigma_\theta\gtrsim
\sqrt{\frac{6}
{\rho K N(N^2-1)\pi^2\cos^2\theta}}.
\]

Exact constants depend on whether signal amplitude, phase, noise and covariance are known. The useful message is the \(N(N^2-1)\), SNR and snapshot dependence. Treating the LEVM elevation aperture as \(N=2\) gives approximately

\[
\sigma_\theta\gtrsim\frac{1}{\pi\cos\theta\sqrt{\rho K}}.
\]

At boresight for one effective coherent snapshot, the optimistic bounds are about 5.8° at 10 dB, 1.8° at 20 dB, and 0.58° at 30 dB. Coherent snapshots can improve the bound if their phases and target identity remain valid. Multipath, calibration error, motion within a chirp/frame, target extent and steering-model error create biases that this bound does not include.

A 3.5° club-to-club difference corresponds to only about 0.19 rad of phase change over a half-wavelength vertical baseline near boresight. It is measurable at high SNR with excellent calibration, but small enough for board tilt, phase calibration and floor multipath to bias materially. TI recommends controlled point-target calibration; clutter or target-model error corrupts the result [TI calibration guidance](https://e2e.ti.com/support/sensors-group/sensors/f/sensors-forum/892202/iwr6843-calibration-best-practice).

### 7.3 What the current `lcmf_v1` is actually doing

The current implementation is better described as a **late-flight complex multipath fusion grid search** than as a conventional 12-element elevation beamformer. It combines selected complex channels/snapshots with range, known geometry, a ballistic trajectory and the external OPS speed, evaluating direct/floor-image path models. The current decision channels use two model families (`two8` and `four4_path_tdm`); merely counting all raw TX/RX products overstates independent elevation information. See the local [LCMF implementation](openflight/src/openflight/iwr6843/lcmf.py), [multipath/ballistic model](openflight/src/openflight/iwr6843/multipath.py), and [board calibration reference](openflight/config/iwr6843_calibration_reference.json).

That model can beat a single-snapshot two-element angle estimate by exploiting motion, range, floor multipath and an external speed constraint. It can also be very precise but biased when the fixed geometry or speed contract is wrong. OpenFlight's earlier internal TrackMan sessions reportedly achieved roughly 0.7–0.8° error after setup-specific tilt calibration; that is encouraging internal evidence, not validation of the present 22-shot session, which has no external truth.

One implementation detail deserves immediate audit. The source labels the OPS value as a radial/phase velocity in one place, while the ballistic forward model appears to use its `speed_ms` input as the total launch-speed magnitude:

\[
v_x=V\cos VLA,\qquad v_y=V\sin VLA-gt.
\]

If the supplied OPS value is still line-of-sight speed rather than a deprojected total speed, then the model contains an angle-dependent error. A launch change from 17° to 25° changes the line-of-sight projection factor from approximately 0.956 to 0.906 in the simplest aligned geometry. That is large enough to affect a grid-search angle and could create or alter club-dependent spread. This is a pipeline-contract hypothesis to verify, not a conclusion that the current result is wrong.

Gravity also matters over the stated 40 ms ball-flight interval: vertical speed changes by about 0.39 m/s, roughly 0.45° at 50 m/s. The current ballistic model includes gravity, which is appropriate; any independent trajectory check must include it too.

### 7.4 Is compressed angular spread a known beamformer failure?

“Compression between clubs” is not an intrinsic or universal beamformer failure mode. A correctly calibrated, identifiable single-target estimator is not expected to shrink angle distributions by definition. However, output shrinkage or bias can arise from:

- incorrect fixed radar height, tilt, time zero or floor plane;
- using radial speed as total speed in a ballistic constraint;
- direct/image-path multipath mismatch;
- too few independent snapshots or correlated snapshots;
- phase-channel calibration errors and steering-vector mismatch;
- grid limits, priors, regularisation or winner-selection logic;
- club-correlated speed/range/frame selection;
- floor, mat, net or other static clutter; and
- different ball trajectories selecting different model families.

Classic adaptive beamformers are especially sensitive to steering-vector mismatch and small-sample covariance estimates [Cox et al., robust adaptive beamforming](https://www.systems.caltech.edu/dsp/students/cyc/rbf.pdf). That citation is mechanistic background, not a claim that OpenFlight's custom LCMF is MVDR. The relevant validation is empirical: compare raw inter-channel phase and each component model against an independent trajectory, then perturb the assumed geometry.

**Finding:** sub-degree isolated-target accuracy is possible in favourable SNR and calibrated conditions, despite coarse two-target elevation resolution. The present board does not have a 12-position elevation aperture, and the custom multipath estimator's accuracy is dominated by model/calibration validity. The current session cannot validate its 3.5° club separation internally.

---

## What would break the D-plane approach on this hardware

The D-plane inversion should be considered broken—not merely noisy—if any of the following inexpensive checks fails. They are ordered roughly by value and can all be run on existing data unless noted.

1. **Independent vertical-trajectory disagreement.** Reconstruct ball elevation from calibrated camera rays plus IWR range, explicitly excluding LCMF phase elevation, and fit a gravity-aware trajectory. Compare per-shot values and the 7i/9i gap. A materially larger independent club gap would directly falsify the LCMF input to dynamic loft. Agreement around 3.5–4° would instead falsify the claim that the small gap proves loft compression.

2. **OPS radial/total-speed contract sensitivity.** Trace the value passed from OPS through `lcmf_v1`. Re-run the grid search once as raw line-of-sight speed and once with the geometrically correct deprojection for each candidate trajectory. If several degrees of VLA or the club gap depend on this choice, the current elevation result is model-contract dominated.

3. **Four/five-frame jackknife failure.** Refit club path and attack angle after deleting each pre-impact frame in turn, and on every meaningful early/late subset. Report the full jackknife range, not just standard error. If one sample moves path or AoA by more than the intended accuracy—approximately 1–2°—the estimator is not supported by that shot. The \(-31°\) and \(+25°\) AoA cases should fail visibly; if they do not, the uncertainty model is hiding the failure.

4. **Impact-time perturbation failure.** Shift the selected impact time by ±1 camera frame and by the measured camera/radar timing uncertainty (about 1.4 ms), then recompute path, AoA, face and loft. A contact result that changes qualitatively under a plausible time shift is not an impact measurement.

5. **Attack-angle robustness failure.** Replace every per-shot AoA with a robust per-club median; separately remove/winsorise the two gross failures. Because \(\partial L/\partial A=-0.235\), the club loft gap should barely move. If the headline conclusion changes, it was being driven by bad AoA values rather than a systematic loft signal.

6. **Coefficient-envelope failure.** Sweep vertical coefficients from at least 0.68 to 0.86 and horizontal values across the published wedge/iron/driver and robot/player range. Bootstrap shots within club and report confidence intervals. If face sign, shot classification or club separation is not stable, report the D-plane output as coefficient-prior dependent. This sweep will also demonstrate that no plausible vertical coefficient turns the current 3.5° launch gap into an 8° delivered-loft gap.

7. **Component-model disagreement.** Save the raw elevation phase slope and the separate `two8` and `four4_path_tdm` angle/score outputs before winner selection. Compare their club gaps, range dependence and residuals. If raw phase carries a club separation that disappears only after physics fusion, the model is compressing it. If the raw observables contain no separation, the final angle cannot independently prove one.

8. **Geometry perturbation failure.** Re-run LCMF across calibration uncertainty in radar tilt, radar/ball height, tee distance, floor plane and channel phase. The reported uncertainty must include the induced between-club bias. A narrow optimiser peak does not imply accuracy if the peak walks with plausible geometry.

9. **Failure-clustering test.** Cross-tab by shot: CAD-pose physical-loft rejection, AoA jackknife instability, radar phase-span rejection, camera/radar association fallback, LCMF component disagreement, inferred-COR extremes and collision spin-loft disagreement. If the same shots concentrate the failures, the likely common cause is timing/target association or an unmodelled head/ball trajectory—not independent benign noise.

10. **Same-point consistency test.** Compare the camera club centroid's predicted radial motion with IWR range/range-rate over the four/five pre-impact frames. A stable offset is calibratable; a changing residual with aspect indicates that camera and radar are tracking different points. In that case, fusing them as one 3D contact-point trajectory is invalid.

11. **Physical impulse feasibility grid.** Recalculate each shot over plausible face normals, effective masses, restitution and strike offsets without using project-derived spin as evidence. If no plausible parameter set can reproduce measured ball/club speed and launch, flag a sensor/model inconsistency. Do not interpret the resulting fitted strike or restitution as measured; this is a rejection test only.

12. **Blind holdout against a reference session.** Before further tuning, freeze calibration, coefficient ranges, rejection thresholds and model selection. Then collect a small marker-based multi-camera or trusted-reference session spanning both clubs, deliberately including toe/heel and high/low strikes. Report bias, spread and failure rate separately. The earlier setup-specific TrackMan results are encouraging but cannot serve as a blind validation after the model has been tuned around them.

The adversarial bottom line is simple. The method fails as a face/loft measurement if the ball-launch vector is not independently correct, if club direction is the motion of a changing feature rather than the contact point, or if plausible coefficient/model choices determine the result. At present all three risks remain open. The central values being believable is not evidence: bounded silhouette pitch also became believable only after discarding 37% of frames, the shaft-perpendicular strike estimate looked geometrically appealing until smash-factor testing killed it, and the current spin check exactly reproduces a project formula.

Until at least tests 1–10 pass and a frozen pipeline survives independent reference data, OpenFlight should publish these quantities as **experimental D-plane estimates with explicit sensor, coefficient and model uncertainty**, not as measured face angle, measured dynamic loft or measured strike location.
