# TrackMan metrics and OpenFlight comparison reference

This is the consolidated formula reference for testing OpenFlight against the TrackMan artifacts in `F:\Trackman`. It combines equations, units, evidence, current OpenFlight behavior, and missing capabilities. Collected 2026-10-11; OpenFlight source baseline `8d244e25e41764bdeb5665a812af40ccf2e37b16`, with pre-existing untracked work preserved.

We recovered several derived-metric equations and reconstructed spin-vector/gyro conversion from calculator probes. **We have not recovered TrackMan's sensor spin-extraction algorithm or its aerodynamic coefficient tables.** The runnable comparison below uses TrackMan measurements as reference inputs, not an invented TrackMan RPM estimator. No production formulas or defaults were changed.

## Start here

- [Executable offline reference and comparison](../../../scripts/analysis/compare_trackman_formulas.py).
- [Machine-readable formula fixtures](formula-fixtures.json): 12 cases, with evidence status and expected values.
- [Original evidence directory](../../../../exports/trackman-metrics-bfb64724/).
- [Original SharedCalculator notes](../../../../exports/trackman-metrics-bfb64724/shared-calculator-spin-evidence.txt).
- [New gyro equations and native probe observations](../../../../exports/trackman-metrics-bfb64724/gyro-spin-evidence.txt).
- [Saved baseline statistics](../../../../exports/trackman-metrics-bfb64724/openflight-baseline/stats.txt) and [controlled spin comparison JSON](../../../../exports/trackman-metrics-bfb64724/openflight-baseline/formula-comparison.json).

Evidence labels matter: **recovered** means instructions support the equation; **probe-matching** means a reconstruction matches tested calculator behavior; **derived candidate** is mathematics we could implement, not recovered TrackMan code; **opaque** means an API exists but its internal formula is unknown. Static declarations do not prove a production feature is enabled.

## Units and coordinate conventions

OpenFlight's flight coordinates are `(X downrange, Y right, Z up)`. The TrackMan calculator probes use `(X forward, Y up, Z lateral)`. Its sign convention has positive backspin along its lateral axis, while OpenFlight's zero-tilt axis is `(0,-1,0)` in its own coordinates. Do not transfer spin vectors by swapping axes alone: angular velocity is an axial vector, and the reflection changes handedness. Validate the mapped lift direction with synthetic backspin and left/right sidespin cases.

The recovered `TrackMan.Data.Golf.LaunchData` angle fields use radians. Native simulator launch/axis/gyro inputs in the probes use degrees. The offline reference deliberately accepts degrees and converts them internally. Legacy managed launch spin is multiplied by 60 before native normalization; native trajectory spin is divided by 60 when stored in managed data. That strongly supports native RPM versus legacy managed RPS at that boundary, not a universal unit assumption for every field. CSV `Spin Rate` is RPM; OpenFlight converts RPM to radians/second as `omega = RPM * 2*pi/60`.

TrackMan's curve equation uses horizontal radial carry. OpenFlight's flight output calls downrange X distance `carry_yards` and reports lateral Y separately. Before applying the curve equation to OpenFlight, use `radial_carry = hypot(carry_yards, lateral_yards)`. Audit the same distinction when comparing reference carry; the current validator compares its loaded reference length directly to OpenFlight's X carry.

## TrackMan equations we can reproduce

### Smash factor

```text
smash = ball_speed / club_speed
```

Speeds must have the same units. Recovered in `LaunchData::get_SmashFactor`, method token `0x060004a1`, division `IL_0026`. OpenFlight already has this formula in [launch_monitor.py](../../../src/openflight/launch_monitor.py), `Shot.smash_factor` around line 349. The offline reference rejects nonpositive club speed; that guard is ours, not a claim about every TrackMan caller.

Evidence: [smash-factor.il](../../../../exports/trackman-metrics-bfb64724/smash-factor.il).

### Three dimensional spin loft

Recovered legacy managed implementation, with club velocity vector `C` in TrackMan's frame:

```text
l = tan(dynamic_loft_radians)
f = tan(face_angle_radians)
N = normalize((1, l*(1 + f*f), f))
C_unit = normalize(C)
spin_loft_radians = acos(dot(C_unit, N))
```

The IL first scales `(1,l*(1+f*f),f)` by `1/sqrt((1+l*l)*(1+f*f))`, then normalizes it again; the first scalar cancels. Preserve this artifact-specific face-normal construction in reproduction tests. Do not silently replace it with a different spherical-angle convention. The offline implementation clamps the dot product for roundoff before `acos`; that is our numerical guard.

This is not generally `dynamic_loft - attack_angle`: horizontal face/path mismatch contributes too. OpenFlight has experimental club path/attack angle and offline face/loft work, but no production spin-loft output. Ball launch direction cannot substitute for face angle. TrackMan's [spin loft explanation](https://www.trackman.com/blog/spin-loft) independently describes the three-dimensional relationship and the limits of the simplified subtraction.

Evidence: [spin-loft.il](../../../../exports/trackman-metrics-bfb64724/spin-loft.il), token `0x0600048d`, tangents `IL_005d` / `IL_006e`, dot product through `IL_012f`, `acos` at `IL_0130`.

### Face to path

```text
face_to_path = face_angle - horizontal_club_path
```

Recovered managed `MeasurementConverter.GetFaceToPath` uses `FaceAngle - AttackAngleHorizontal` (the latter is the horizontal club-motion direction). Its validity becomes estimated if either input is estimated. This is a delivery metric, not a spin-axis measurement.

OpenFlight's live experimental spin-axis proxy is instead `round(ball_HLA - club_path, 1)`, gated by horizontal-launch confidence >= 0.6. The source comment calls HLA a face angle, but those are different quantities. Do not present that proxy as measured face-to-path or as recovered TrackMan spin-axis physics.

Evidence: managed member bodies in [rea-device-evidence.json](../../../../exports/trackman-metrics-bfb64724/rea-device-evidence.json); OpenFlight [server.py](../../../src/openflight/server.py) around lines 3571–3584.

### Curve relative to initial launch line

```text
curve = carry * sin(asin(carry_side / carry) - launch_direction_radians)
```

Distances retain their input units. It measures lateral displacement relative to the initial launch direction, not simply carry side relative to the target. The managed calculation propagates absent inputs; the offline reference only evaluates valid positive carry geometry. OpenFlight already has lateral flight output but no named production curve metric.

Evidence: [curve.il](../../../../exports/trackman-metrics-bfb64724/curve.il), `MeasurementExtensions.CalculateCurve`, token `0x060002c9`, `asin IL_005b`, angle conversion `IL_0078`, subtraction `IL_0086`, `sin IL_0087`, multiplication `IL_00a9`.

### Total spin from a vector

```text
spin_rate = sqrt(Wx*Wx + Wy*Wy + Wz*Wz)
```

Recovered native instruction sequence in `TrajectoryBallPosition.GetSpinRate`, export ordinal 1564, RVA `0x598D0`. Probe `(3000,4000,0)` returns `5000`. The norm preserves whatever angular-rate units the vector uses; the instruction sequence alone does not prove those units.

Evidence: [shared-calculator-spin-evidence.txt](../../../../exports/trackman-metrics-bfb64724/shared-calculator-spin-evidence.txt), native disassembly and controlled probe.

### Spin axis and gyro from a three dimensional spin vector

**Probe-matching reconstruction**, not decompiled native source. Let `a` be launch elevation and `d` launch direction, converted to radians. Construct a launch-relative orthonormal frame in TrackMan coordinates:

```text
F = ( cos(a)*cos(d), sin(a),  cos(a)*sin(d))   # along launch velocity
U = (-sin(a)*cos(d), cos(a), -sin(a)*sin(d))   # up in launch frame
L = (-sin(d),       0,       cos(d))           # lateral in launch frame

S = norm(W)
spin_axis_deg = degrees(-atan2(dot(W,U), dot(W,L)))
spin_gyro_deg = degrees(asin(clamp(dot(W,F)/S, -1, 1)))
```

Native probes return all zeros for a zero vector. The axis has no physical meaning when the transverse component is zero; singular-angle conventions should not be overinterpreted.

The matching inverse is:

```text
W = S * (sin(gyro)*F + cos(gyro)*(-sin(axis)*U + cos(axis)*L))
gyro_component = S*sin(gyro)
transverse_spin_magnitude = S*cos(gyro)
```

**TrackMan's `SpinGyro` field is a signed angle in degrees, not gyro RPM.** A separate longitudinal-rate component can be derived using the equation above. At zero launch/direction, `(0,0,3000)` gives axis 0 / gyro 0, `(0,3000,0)` gives axis -90 / gyro 0, and `(3000,0,0)` gives axis 0 / gyro 90.

At launch 30°, direction 40°, `W=(1000,2000,3000)`, observed outputs are rate `3741.6573867739412`, axis `-13.088052777502288°`, gyro `62.985971935012032°`. Reconstruction matched 30 nonzero native probes across six launch frames to floating-point precision, plus separate zero-vector probes. This converts supplied spin; it does not discover how the hardware measured that spin.

Evidence: [gyro-spin-evidence.txt](../../../../exports/trackman-metrics-bfb64724/gyro-spin-evidence.txt), including probe setup, coverage, raw observations, and unit caveats.

### Spin evaluated along a fitted trajectory

```text
spin(t) = c0 + c1*t + c2*t*t + ... + cn*t**n
```

Recovered polynomial evaluation uses Horner's method. `BallTrajectoryEx.GetBallTrajectory` evaluates `SpinRateFit` for trajectory positions; the typed result is `RevolutionsPerMinute`. Coefficients and the fit's time origin are supplied by the fit, not recovered here. This is not evidence of a universal polynomial spin-decay law or raw sensor extraction. OpenFlight currently uses a fixed exponential decay instead.

Evidence: [spin-polynomial.il](../../../../exports/trackman-metrics-bfb64724/spin-polynomial.il), token `0x060000a3`; [spin-trajectory.il](../../../../exports/trackman-metrics-bfb64724/spin-trajectory.il), token `0x06000015`, fit evaluation near `IL_0181`.

## TrackMan routines whose internal equations are still opaque

| Routine | Evidence establishes | What we cannot reproduce yet |
| --- | --- | --- |
| `ConstrainSpin` | Supplied RPM is constrained; Premium/Driver probe returned bounds 2260.026114… and 3089.014124…; tested Iron1 bounds differed | General bounds/table/club mapping. Do not hardcode these examples as a sensor estimator |
| `CalculateSpinAndSmashIndex` | Consumes existing spin, launch and club delivery; returns indices, deltas and validity | Exact expected-spin/ball-speed equations and index scales |
| `NormalizeStroke` / `NormalizeMeasurement` | Launch/rotation and atmospheric/ball-type inputs; explicit angle and rate conversions | Exact ball conversion, weather normalization, drag/lift models |
| `SimulateBallLaunch` | Accepts gyro, axis, RPM, landing height, weather and surface settings; gyro changes simulated flight in probes | Native aerodynamic coefficients and full integration/roll behavior |
| `SimulateClubDelivery` | Accepts club speed, path, attack angle, face, loft, impact location, ball and surface type | Exact impact/gear-effect/delivery-to-launch model |
| Impact, swing-plane and optimizer routines | Managed/native entry points are present | Exact algorithms and which production modes invoke them |

For `ConstrainSpin`, the Driver probes used BallSpeed=65 and LaunchAngle=12; outputs did not change in the tested speed/angle variations. This is bounded behavior, not a claim that speed/angle never matter. API names and constructor settings alone do not prove an active production policy.

Device message handling deserializes existing measurement values and converts units. We did not find a raw-spin extraction equation in that handler. The presence of `SpinRateMethod.Estimated` / `Measured` also means reference data should retain provenance; a non-null number is not necessarily sensor-measured.

Evidence: [shared-calculator notes](../../../../exports/trackman-metrics-bfb64724/shared-calculator-spin-evidence.txt), [normalization.il](../../../../exports/trackman-metrics-bfb64724/normalization.il), [spin-smash-inputs.il](../../../../exports/trackman-metrics-bfb64724/spin-smash-inputs.il), [device-measurements.il](../../../../exports/trackman-metrics-bfb64724/device-measurements.il).

## Current OpenFlight equations to keep as the control

| Component | Current formula or rule | Source |
| --- | --- | --- |
| Optional calculated spin | `min(13000,170*speed_mph*sin(radians(VLA))**1.2)`; speed positive, VLA 2–60° | [spin_estimate.py](../../../src/openflight/spin_estimate.py), lines 38–75 |
| Radar modulation candidates | `RPM=60*modulation_frequency_hz`; confidence gates, phase/autocorrelation checks | [rolling_buffer/processor.py](../../../src/openflight/rolling_buffer/processor.py), around 911, 1026, 1356 |
| Experimental axis | `round(HLA-club_path,1)`; not measured face angle or 3D spin | [server.py](../../../src/openflight/server.py), around 3571 |
| Exported back/side spin | `back=RPM*cos(axis)`, `side=RPM*sin(axis)`; no gyro component | [sim/resolver.py](../../../src/openflight/sim/resolver.py), around 104 |
| Spin parameter | `Sp=ball_radius*omega/speed`, radius 0.02135 m | [ballistics.py](../../../src/openflight/ballistics.py), around 200 |
| Drag coefficient | `Cd=0.205+0.18*Sp` | Same, around 174 |
| Lift coefficient | `Cl=0.32*Sp/(0.15+Sp)` for positive Sp | Same, around 178 |
| Acceleration | `q=0.5*rho*speed**2*area/mass`; drag `-q*Cd*vhat`; lift `q*Cl*normalize(axis cross velocity)`; gravity Z `-9.81` | Same, around 182–230 |
| Spin decay | `omega(t)=omega0*exp(-0.04*t)` | Same, around 296 |
| Integration | RK4, 0.002 s; fixed axis `(0,-cos(tilt),sin(tilt))`; ground intersection at Z=0 | Same, around 233–325 |
| Density | Scalar argument, production default 1.225 kg/m³; no wind-relative velocity | Same, `simulate`; [server.py](../../../src/openflight/server.py), around 3776 |
| Total distance | `carry + max(0,30*cos(radians(landing_angle)))` yards | Same, around 127; a rollout display heuristic |

Calculated spin is **off by default**. When enabled, the server applies it after speed cosine correction, saves the previous RPM, marks source `calculated`, and sets confidence 0.7. The docstring reports calibration from one 131-shot session; that is not broad validation or a recovered TrackMan formula.

Flight and simulator export have separate club-typical spin tables (for example driver 2700 versus 2500 RPM). Both accept confidence >= 0.7, and can call calculated RPM `measured` because the resolver checks confidence rather than the source label. Export also treats any present axis as measured even if it was the experimental HLA/path proxy. Keep provenance explicit in research results.

There are older speed-table/fallback carry corrections too: [launch_monitor.py](../../../src/openflight/launch_monitor.py) around 72–187 and [rolling_buffer/monitor.py](../../../src/openflight/rolling_buffer/monitor.py) around 25–203. The server's physics path is gated when a spin-adjusted carry already exists. Therefore a displayed production carry is not always the same computation as a direct `simulate` benchmark.

## Capabilities OpenFlight still lacks or only has experimentally

| Capability | OpenFlight status | TrackMan evidence and useful next experiment |
| --- | --- | --- |
| Gyro and full 3D angular velocity | Missing independent gyro field/measurement/model | Verified native conversion and flight sensitivity; first implement offline vector fixtures, then a gyro-aware flight variant |
| Measured face angle and dynamic loft | Offline clubpose code exists; not live Shot outputs | Club-delivery inputs exist; evaluate [clubpose/angles.py](../../../src/openflight/camera/clubpose/angles.py) around 95 on labeled captures |
| 3D spin loft | Missing in production | Recovered managed equation; needs face normal and club velocity, not HLA |
| Attack angle and club path | Experimental live IWR/camera fusion exists | Not a total gap; [club_delivery.py](../../../src/openflight/camera/club_delivery.py) around 283 uses path `atan2(lateral,forward)`, attack `atan2(vertical,forward)` |
| Face strike position / gear effect | No public face-impact coordinates or equivalent estimator found | TrackMan delivery API accepts `ImpactLocation`; contact timestamps are not face strike location |
| Ball type and weather normalization | Scalar density only in current flight; no equivalent normalization path found | Native inputs support ball types and atmospheric fields; coefficients remain opaque |
| Bounce/roll with surface and slope | Current total-distance heuristic only | Native simulator exposes surface hardness/type, slope and stimp; exact model remains opaque |
| Named curve and spin-over-flight fit | Lateral trajectory and exponential decay only | Recovered curve and polynomial evaluation can be offline checks now |

The gap audit covers current production source and the inspected offline modules, not every untracked experiment or device firmware. For current product context, TrackMan [documents iO measuring spin axis and gyro](https://www.trackman.com/blog/radar-and-cameras-for-superior-indoor-golf-accuracy); that is an iO hardware claim, not proof that every older TrackMan unit or TPS mode measures gyro.

### Why adding gyro requires more than another field

At each flight timestep, a general decomposition is:

```text
vhat = velocity / norm(velocity)
omega_parallel = dot(omega,vhat)*vhat
omega_perpendicular = omega - omega_parallel
active_spin_fraction = norm(omega_perpendicular)/norm(omega)
```

At the initial launch this fraction is `cos(gyro)` for gyro in [-90°,90°]. The parallel component contributes nothing to `omega cross velocity` at that instant; as velocity turns during flight, the decomposition changes. Pure gyro at launch therefore does not imply no aerodynamic effect throughout the trajectory.

OpenFlight currently calculates `Cl` from **total** RPM and normalizes the axis/velocity cross product. Merely adding a longitudinal axis component would keep full lift for nearly every nonparallel angle. An offline candidate could use transverse spin in the lift coefficient, or apply a calibrated angular dependence. These are **derived candidates**, not recovered TrackMan aerodynamic formulas. Keep drag treatment and spin decay separately configurable and compare with independent captures.

Native probes show actual gyro sensitivity: with BallSpeed=65, launch 12°, total spin 3000 and axis 0, default native settings, carry changed from 215.789799 at gyro 0° to 215.222015 at 30°, and 191.984272 at 90°; lateral output also changed. These are raw API distance units, not independently established metres/yards. Full inputs and observations are in the gyro evidence file. The committed CSV and slim benchmark loader have no gyro measurement input, so they cannot validate a gyro correction on real shots.

## Run the comparisons

From the nested repository `C:\Users\harjo\Desktop\Coding\OpenFlight\openflight`:

```powershell
# Check recovered/probe-matching reference cases without proprietary code.
uv run python scripts/analysis/compare_trackman_formulas.py --check-only

# Change only RPM: same TM speed, launch, axis, density and current OF aero.
uv run python scripts/analysis/compare_trackman_formulas.py --output ../exports/trackman-metrics-bfb64724/openflight-baseline/formula-comparison.json

# Existing model-only and paired-input baseline.
uv run python scripts/analysis/validate_ballistics.py --trackman session_logs/OpenFlight-Test.Normalized.csv --comparison session_logs/comparison_20260506.csv --output-dir ../exports/trackman-metrics-bfb64724/openflight-baseline

uv run pytest tests/test_trackman_formula_reference.py tests/test_ballistics_trackman_regression.py tests/test_spin_estimate.py -q
```

The commands write only research results at the selected paths; repeated runs replace those result files. The comparison JSON records every matched shot and metric provenance. It does not invoke the server or a TrackMan DLL. Missing HLA/axis default to zero, as in the existing validator. The report explicitly labels the optional calculated-spin variant rather than pretending its forced input is sensor-measured.

### Baseline measured on 2026 10 11

24 matched shots, normalized reference, density 1.184 kg/m³ (existing validator's flat 25°C sea-level assumption):

| Variant | Carry bias | Carry RMSE | Meaning |
| --- | --- | --- | --- |
| Current OF aero with TM reference RPM | -5.05 yd | 24.52 yd | Model error with reference launch inputs |
| Current OF aero with OF calculated RPM | +3.51 yd | 14.98 yd | Controlled optional-RPM swap, not a TrackMan formula |
| Existing paired OF-input validator | -31.02 yd | 43.91 yd | 22 usable rows; partially borrows TM axis, not clean independent end-to-end validation |

Calculated RPM error versus TM reference is **2388.78 RPM RMSE**, **2023.62 RPM MAE**, and **-999.51 RPM bias**. Its lower carry RMSE despite substantial spin error can reflect compensating errors in the aero model; it does **not** establish that the spin formula is more accurate. Judge spin against spin, and flight against flight.

Model-only carry RMSE by club: driver 38.60 yd (8), 7-iron 11.64 yd (9), pitching wedge 13.56 yd (7). This small historical capture is a baseline, not a general accuracy claim. The regression test has budgets around the existing errors. Keep a held-out session when tuning constants.

Verification: 12 reference fixtures checked; 18 targeted tests passed, covering the new reference, existing TrackMan carry regression and calculated-spin tests. Unit fixtures validate our transcription, not every native input or the measurement pipeline.

## Evidence identity and remaining work

All raw files below are in [exports/trackman-metrics-bfb64724](../../../../exports/trackman-metrics-bfb64724/). Original TrackMan artifacts were not modified. REA metadata/IL evidence is separate from supplemental Microsoft ILDasm/dumpbin excerpts and controlled calculator execution. Native REA deep analysis was unavailable because Ghidra was not configured; no claim here depends on nonexistent native decompilation.

| Artifact | SHA256 | REA evidence |
| --- | --- | --- |
| `TrackMan.Data.dll` | `cc9d82bb4c38b6113e405fae76f7e7ddb301bc05b1a50c73a0c192b939086672` | `rea-evidence.json`: artifact `ev_61a612e28676e62bc30e025adddf8fa8dc2fdb63248e364516639dbc5a13e3d6`; members `ev_856466ef3b4331e718ea6d744e8c3477d55610a8dda9e8f7ec34f1649ce8a08c` |
| `TrackMan.Api.Measures.dll` | `d8410e7b30218c7fd671a8d097ced4de5d1ae94a4f9ca85c10b894492bc22fbf` | `rea-device-evidence.json`: `ev_a92d15e8f313d62b3d8c42b6d17b91b3de1304b74abb4130978c9b1c65c98458` |
| `TrackMan.SharedCalculator.dll` | `73c6e8b020fde0abb1b67f594fccba0aa9cd0a42a59918cb9b9b4449530f53ab` | `rea-calculator-evidence.json`: native boundary `ev_e9d0d3438da900302afed05b3ff3fb5d236ed11184f3f2f524b0842bd0b554fa` |
| Native `shared_calculator_net.dll` | `13355d777bf1980e79efe8a10cad6950278bdb50fd0d8febcd51f79cf409f948` | Supplemental disassembly and calculator probes in the two evidence text files |
| `TrackMan.Device.dll` | `131bbbc74d1aa96e84d366bc370ea1f5556e60ea09ffa861d5eaf973c09aeac9` | `rea-device-evidence.json`: `ev_35a70511d128bf1f4d5e546aaf6de7371694164ccf7dc98f4ed7a9fc97bd89f7` |

Tokens and RVAs are build-local; match hashes before reusing them. Some managed signature coverage was partial for unsupported generic forms; the target formula IL excerpts are available. Negative findings are limited to this inspected desktop artifact/source scope, not hardware firmware.

Ready now: compare scalar spin models on independent references; add curve/spin-loft checks where valid input metrics exist; exercise gyro reconstruction with saved synthetic vectors. Still unresolved: raw radar/camera spin extraction, complete aero coefficients, constraint/index tables, delivery/impact model and normalization internals. A faithful TrackMan flight engine cannot yet be reconstructed from this evidence alone.
