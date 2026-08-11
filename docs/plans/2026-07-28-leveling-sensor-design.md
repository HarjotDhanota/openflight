# Auto-Leveling: Software Tilt Correction from a Gravity Reference

*Design doc. Branch `feat/leveling-sensor`. Rewritten 2026-07-28 around the actual goal.*

## What this does

**Set the launch monitor down on any slope and it corrects itself in software.** No bubble level, no phone inclinometer, no measuring the mount angle and typing it into a flag. The unit senses how it's sitting and adjusts the math.

That's the Trackman/GCQuad behaviour. It is achievable here, but the two axes are not equally easy and they matter for different reasons — see §3, which is the core of this document.

### What the user does

Nothing. Set it down, aim it, hit balls.

### What the builder does, once

One measurement with a $15 angle gauge, once, at assembly — about a minute. See §4.

### What this cannot do

**Aim.** An accelerometer measures gravity, so it knows *tilt* — but it has no idea which way the unit is *pointed*. Set it down rotated 5° off your target line and nothing detects that. A magnetometer won't rescue it either: the enclosure contains a Pi 5, a screen, and two radars, and metallized EMI shielding is in the enclosure plan.

Aim stays a manual alignment step (`--iwr6843-azimuth-offset-deg`, applied at `club.py:290`). Trackman and GCQuad have the same limitation — that's what their alignment procedures exist for. "Set it down anywhere" means **on any slope**, not in any direction.

---

## 1. The problem today

Mount tilt is a hand-measured constant with no safe default:

```
# scripts/start-kiosk.sh:336-337
# Mount tilt has no safe default (a wrong value silently biases the launch angle)

# src/openflight/server.py:3116  (--kld7-mount-tilt help text)
"... measure it with a phone inclinometer against the radar face"
```

The stored value is `"tilt_deg": 10.404948650719305` in `config/iwr6843_calibration_reference.json` — a precise corner-reflector solve, invalidated the moment the unit is set down on different ground.

Tilt error maps **1:1** into launch angle. From this repo's own `simulate()`:

| Launch-angle error | Driver carry (165/12.5°/2600) | 7-iron (120/18°/6500) |
|---|---|---|
| ±0.25° | +1.03 / −1.07 yd | +0.27 / −0.28 yd |
| ±0.50° | +2.03 / −2.17 yd | +0.52 / −0.58 yd |
| **±1.00°** | **+3.94 / −4.48 yd** | +0.97 / −1.22 yd |
| ±2.00° | +7.38 / −9.55 yd | +1.71 / −2.71 yd |

A phone inclinometer against a plastic radome on grass is a ±1-2° instrument. That's 4-10 yards of driver bias presenting as a believable number.

---

## 2. What the sensor measures

A 3-axis accelerometer reads the gravity vector. From it:

```
pitch = atan2(-a_x, hypot(a_y, a_z))     # nose up/down — boresight elevation
roll  = atan2(a_y, a_z)                  # side-to-side lean about boresight
```

Axis assignment depends on final mounting and is a config constant, not a hardcode.

**Yaw is absent from that list because gravity cannot produce it.** See "What this cannot do" above.

---

## 3. Why roll and pitch are different problems

This is the part that determines the whole design, and it is not obvious.

**The radar does not measure a 3D direction.** Each estimator measures a single angular axis, on a different antenna baseline:

- **Ball flight** — `AnglePoint` (`iwr6843/doa.py:53-61`) carries `t_s, range_m, theta_rad, snr, n_summed`. **Elevation only.** No azimuth field exists. Consumed at `trajectory.py:50` (`theta + cal.tilt_rad`) and `lcmf.py:238-241`.
- **Club path** — `club.py:247-257` converts inter-element phase to **azimuth only**, then to Cartesian `x = r·cos(az)`, `y = r·sin(az)`. There is no elevation term anywhere in `club.py`; it fits the horizontal plane assuming the array is level.

So there is nothing to apply a 3D rotation matrix *to*. Rolling the device rotates each estimator's measurement plane, and the correction has to be worked out per-estimator.

### 3.1 Roll barely touches launch angle

Measured elevation under roll φ is approximately `θ_true·cos φ + az_true·sin φ`. The ball leaves the tee essentially on the boresight line, so `az_true` is small, and `θ_true` off boresight is ~10° during the tracked window:

| Roll | Launch-angle error |
|---|---|
| 1° | 0.002° |
| 2° | 0.006° |
| 5° | 0.038° |
| 10° | 0.152° |

**Negligible.** Even a 10° list costs a fraction of a yard.

### 3.2 Roll wrecks club path

The club sits low and close, so it's well off boresight — with the radar at 0.152 m and tilted 10.4° up, a clubhead 1-3 m away at near-ground height is **10-18° below boresight**, call it 14°. Roll mixes that elevation straight into the azimuth channel, error ≈ `|θ_club| · sin φ`:

| Roll | Club-path error | Offline at 256 yd |
|---|---|---|
| 1° | 0.24° | 1.1 yd |
| 2° | 0.49° | 2.2 yd |
| **5°** | **1.22°** | **5.5 yd** |
| 10° | 2.43° | 10.9 yd |

Club path is spec'd in the ±1-2° class, so a 5° list eats the entire budget. **This is the axis worth engineering.**

### 3.3 The correction

**Pitch** — the hook already exists. `Calibration.tilt_rad` (`calibration.py:30`) is the single radar→ground rotation, and `clone_calibration(tilt_deg=...)` (`calibration_session.py:78-106`) already accepts an override. Feed it the measured pitch instead of a CLI flag. Small change, biggest win.

**Roll, ball** — divide measured elevation by `cos φ`. Two lines, corrects a 0.04° effect. Include it because it's free and it makes the model honest, not because anyone will notice.

**Roll, club** — subtract `θ_club · sin φ` from the measured azimuth. The catch: `club.py` doesn't measure `θ_club`. But it doesn't need to *measure* it — it can be **derived from geometry the code already has**:

```
θ_club ≈ atan2(h_club − radar_height_m, x_club) − pitch
```

`radar_height_m` is a `Calibration` property (`calibration.py:41-44`), `x_club` comes from the track's own range fit (`club.py:255`), and `h_club` is the clubhead height at the tracked instant — near the mat, bounded, and already implicitly assumed by the estimator.

That's the whole design. It is a geometric correction using known quantities, **not** a new measurement and **not** a rotation of a vector that doesn't exist.

**Sensitivity to the `h_club` assumption:** `θ_club` shifts by ~7° between a clubhead at 0.02 m and one at 0.15 m at 1 m range, but the correction scales as `sin φ`. At 2° roll a 7° error in `θ_club` produces 0.24° of residual — comparable to the error being corrected. **So the correction should only be applied above a roll threshold** (~2°), below which it is noise. That threshold falls out of §3.2's table anyway.

---

## 4. The one-time reference — a minute at assembly, then never again

Two constants are unknown until the unit is physically built, and neither is on a datasheet:

1. **The accelerometer's own zero-g offset.** A LIS3DH reads up to ±40 mg on a still axis — **±2.3° of tilt**. Feeding the raw angle into the math would be *worse* than the phone inclinometer being replaced. (Phones avoid this because Apple trims every unit on a factory fixture; the calibration happens, you just never see it. A $5 breakout gets no such treatment.)
2. **The rotation between the accelerometer and the radar boresight.** The chip is bolted somewhere; the antenna points somewhere else. **The radar mount is designed to be adjustable**, so this changes whenever it's adjusted.

Both are constant for a given assembly, and **both cancel in a single subtraction.**

### What does *not* work

**The corner-reflector calibration cannot be the reference.** `config/iwr6843_calibration_reference.json` was solved on a different board (`docs/iwr6843/README.md:432-437`: *"the array correction used by the validated radar... not a universal factory calibration"*), there is no corner-reflector solver in this repo for the IWR6843, and the calibration session's tilt-candidate sweep is documented as not working — on 2026-07-25, with the mount measured at 5.5°, a ±3° sweep returned 2.5° and 8.5° across four shots of one session. The repo's own instruction is **"Set tilt by physical measurement."**

**A pure CAD constant is not enough on its own.** The designed sensor-to-antenna angle is known, but it doesn't cancel the sensor's ±2.3° zero-g offset — that error lands directly in `tilt = sensor_reading + cad_constant`. A CAD constant only works with a factory-trimmed part (ADXL355, $47.06; SCL3300, $37.64 bare / $77.37 as a board), which is $42 to remove a one-minute step.

### The procedure — bench zero, before the plate goes in

The two unknowns get separated rather than solved together, which is what makes this need no special tools:

**Unknown 1, the chip's offset — cancelled by levelling the plate on a bench.**

1. Bolt the LIS3DH to the radar mount plate. (Radar can be off; only the sensor-to-plate relationship matters here.)
2. Lay the plate flat on a level surface. Check both axes with an ordinary spirit level.
3. Run `--leveling-zero`. It takes a stable averaged reading and stores it as the bias — the plate is truly at 0°/0°, so whatever the chip reports is pure offset.
4. Install the plate in the unit.

**Unknown 2, sensor plane to antenna boresight — supplied by CAD.** Both parts bolt to the same plate, so the angle between them is a design constant. It goes in `config/leveling.json` with a comment naming the CAD file and revision it came from.

Then: `antenna_pitch = (sensor_pitch − pitch_bias) + cad_offset_deg`.

Doing the zero *before* installation is deliberate. It sidesteps the question of whether an adjustable mount can reach horizontal — the plate is loose in your hand, so getting it level is trivial.

**No angle gauge required.** A $5 torpedo level is enough. An angle gauge is an optional upgrade (§5) because it measures the antenna face directly and so also cancels the print tolerance, but nothing depends on owning one.

### After that

The governing spec is **repeatability, not absolute accuracy** — repeatability is noise-limited at ~0.05° with a second of averaging. The sensor reports orientation relative to the calibrated pose forever, and the user does nothing.

### Three tiers, all optional above the first

| Tier | What the builder does | Absolute accuracy | "Set it down anywhere" |
|---|---|---|---|
| 1 | Nothing — ship the CAD constant, no zero captured | ±2.3° (chip offset uncancelled) | ✅ works perfectly |
| 2 | Auto-reference on first run against an existing `--iwr6843-tilt-deg` | inherits that number, ±1-2° | ✅ |
| **3** | **Bench zero with a spirit level (above)** | **±0.6°** | ✅ |
| 4 | Re-zero on screen against a digital angle gauge | ±0.25° | ✅ |

**Relative tracking is perfect at every tier** — the bias is a constant and cancels out of "how much has it moved since calibration." The tier only sets the absolute offset. That is why the feature is useful even at tier 1, and why nobody is forced to buy a tool.

Tier 2 is worth implementing because it is free: the software already receives a tilt value from the user. On the first run with `--leveling` enabled it records the difference between that value and the live sensor reading, silently, and thereafter the flag can be dropped.

Tier 4 lives in the UI (§10) — a **Re-zero** control in the setup view. Note that a spirit level can only supply *roll* at this stage (roll should read 0° when the unit sits square); *pitch* needs an instrument that reads an actual number, because the antenna sits at ~10° and a bubble only tells you when something is at zero.

---

## 5. Error budget after zeroing

At tier 3 (bench zero with a spirit level), which is the shipped procedure:

| Source | Magnitude | Handling |
|---|---|---|
| **CAD constant vs as-printed sensor-plane-to-antenna angle** | **~±0.5°** | **Dominant. Reduce it in CAD — §7.3.** |
| Reading the spirit level | ~±0.3° | Use a decent level; a longer vial reads finer |
| Noise | ~0.05° with 1 s vector averaging | Accept |
| Cross-axis sensitivity | ~1% of applied → ~0.1° at 10° | Accept |
| Offset projection change when re-tilted | second-order over ±10° | Accept |
| `h_club` assumption (roll correction only) | ~0.24° residual at 2° roll | Roll threshold, §3.3 |
| **Offset temperature drift** | **UNKNOWN** | **See below** |

**Total ≈ ±0.6°**, dominated by the CAD-to-print stack-up. Tier 4 (gauge on the antenna face) drops this to ≈±0.25° because it measures the antenna directly and so cancels the print term as well as the chip offset — that is the *only* reason to buy a gauge.

Note what this means for part selection: **the sensor is not the limiting error in any configuration.** A LIS3DH resolves 0.057°/LSB — an order of magnitude below both the spirit level and the print tolerance. Paying $33-72 more for a factory-trimmed part buys precision nothing downstream can use, and buys it in the wrong place: per Murata's own table the SCL3300 still carries **±1.15° of offset error** (±20 mg, X/Z) after factory calibration, so it needs the same zeroing step regardless.

**Temperature drift is the one real unknown.** The zero reference is captured at one temperature; if the accelerometer's offset drifts as the unit warms, the "change since calibration" reading drifts with it and reports movement that didn't happen.

ST does not publish a zero-g offset temperature-drift figure for the LIS3DH in any accessible datasheet. The nearest reference point is the ADXL335 at ~1 mg/°C typical, which over a 30 °C garage-to-range swing would be ~1.7° — larger than the entire budget.

**This is measured, not designed around.** A bench soak (hold the unit rigidly fixed, log pitch/roll across a ≥25 °C excursion) produces the number in an afternoon. Decision gate:

- **< 0.25° over 30 °C** → nothing needed. Ship as designed.
- **0.25-1.0°** → record die temperature in the zero reference and warn on a large delta. The accelerometer's own on-chip temperature sensor is the right source — offset drift is driven by *die* temperature, and drift compensation only needs a *relative* reading, which is precisely what that uncalibrated sensor provides.
- **> 1.0°** → wrong part. Escalate to an ADXL355 (industrial, specified drift, ~$25).

**Do not build the temperature path before this measurement.** If drift is small it is dead code.

---

## 6. Part choice

**LIS3DH** — [Adafruit 2809](https://www.adafruit.com/product/2809), $4.95, I²C 0x18/0x19, `WHO_AM_I` 0x0F = 0x33, STEMMA QT on both ends. High-resolution mode gives 1 mg/LSB = **0.057°/LSB**, an order of magnitude below every other term in the budget. Shares the I²C bus with the BME280 from `feat/weather-sensor` — same two pins, no address collision, one `smbus2` dependency.

### Why an accelerometer rather than an inclinometer

**An inclinometer *is* an accelerometer.** The SCL3300 is a 3-axis MEMS accelerometer with an ASIC that outputs degrees and applies factory trim. Same sensing element, same physics. What the extra $33-72 buys:

- Degrees straight out — but that is two lines of `atan2`
- Tighter sensitivity and cross-axis trim — irrelevant; the spirit level and the print tolerance dominate
- Lower noise — the LIS3DH is already an order below the limiting term
- Better offset — **still ±1.15°, and we cancel it by zeroing either way**

Every advantage lands in the part of the error budget that is not limiting. Prices checked July 2026:

| Part | Interface | Offset error (datasheet) | What you'd buy | Price |
|---|---|---|---|---|
| **LIS3DH** | I²C/SPI | ±40 mg = ±2.3° | Adafruit 2809 | **$4.95** |
| ADXL355 | I²C/SPI | trimmed; 0.15 mg/°C max drift | EVAL-ADXL355-PMDZ | $47.06 |
| SCL3300 | **SPI only** | **±20 mg = ±1.15° (X,Z); −25/+20 mg = −1.45/+1.15° (Y)** | bare LGA $37.64 / board $77.37 | $38-77 |

The SCL3300 row is Murata's own specification table, quoted in degrees by Murata. Its datasheet also states: *"Assembly can cause offset/bias errors to the sensor output. If best possible accuracy is required, system level offset/bias calibration (zeroing) after assembly is recommended."* **No part in this class removes the zeroing step.** It also happens to be SPI-only, which would change the pinout and break bus-sharing with the BME280, and the affordable version is a bare LGA requiring reflow.

**The one place the inclinometer is genuinely better — and it is the open risk.** Murata specifies SCL3300 offset temperature dependency at ±10 mg (±0.57°) across its *entire* −40 to +125 °C range, roughly 0.1° over a realistic 30 °C swing. ST publishes no equivalent figure for the LIS3DH. See §5: measure it on the bench, escalate only if it fails.

Also rejected: **ADXL345** (±150 mg = ±8.6°, 3.9 mg/LSB) — strictly worse than the LIS3DH at the same price. **BNO085** — magnetometer unusable in this enclosure, gyro irrelevant for a stationary box, SHTP stack is a large driver surface for capability we cannot use. **MPU-6050** — EOL, clone-infested.

Keep the part behind a thin interface (`whoami()`, `configure()`, `read_acceleration_g()`) so a swap is ~40 lines. Given the open drift question that seam is an escape hatch, not speculative generality.

### Tools

**Required: an ordinary spirit level.** That is the whole tool list.

**Optional: a magnetic digital angle gauge**, ±0.1-0.2°, ~$12-25. Buys tier 4 (±0.25° instead of ±0.6°) and nothing else. Buy it later or never.

---

## 7. What the enclosure needs to provide

*Written for CAD that isn't finalised. These are cheap to design in now and expensive to retrofit.*

**7.1 Mount the accelerometer on the radar's mount plate, not on the case.**
This is the load-bearing requirement. The mount is adjustable; if the sensor is fixed to the case and the radar tilts independently, the sensor-to-antenna relationship changes every time the mount moves and the zero reference is invalidated. On the plate, the relationship is rigid forever and adjusting the mount is free.

**7.2 The plate must sit flat and stable on a bench, on its own, with the sensor fitted.**
This is what makes the tier-3 bench zero work without tools: lay the plate down, level it with a spirit level, zero. Requirements: a flat underside (or three defined feet) in a plane with a *known* relationship to the sensor's mounting face — ideally parallel — and enough footprint that it doesn't rock. If the plate can't sit flat, the whole no-tools procedure collapses back to needing an angle gauge.

Optionally also provide a flat pad coplanar with the antenna face, ≥40 mm across and accessible when assembled, for tier-4 gauge re-zeroing. Nice to have, not required.

**7.3 Print the pad and the radar's mounting datum off the same feature, in the same orientation.**
Layer-line and warp errors then affect both surfaces similarly and partially cancel. This is free at design time and is the difference between ~±0.5° and ~±0.2° of residual stack-up.

**7.4 Keep the sensor board's own mounting rigid and repeatable.**
Two dowel/boss locators plus screws, not a single screw the board can rotate about. Any rotation the board can take after zeroing is error the software cannot see.

**7.5 Keep magnets away from the radar apertures.**
If the reference pad gets a steel insert for a magnetic gauge, place it clear of the antenna apertures — `docs/hardware-integration.md` and the enclosure notes already flag metallic material near the radome as a radar killer.

**7.6 Nice to have: a level-surface fallback.**
If the mount is ever made fixed rather than adjustable, a set of feet coplanar with the antenna face would allow tool-free zeroing ("set it on a level table, run one command"). Not achievable with an adjustable mount, but worth knowing the option exists if the mount design settles.

---

## 8. What changes in the code

| File | Change | Size |
|---|---|---|
| `iwr6843/calibration.py:25-49` | `Calibration` gains `roll_rad`; `load()` reads optional `roll_deg` defaulting to 0.0 | Small |
| `iwr6843/calibration_session.py:78-106` | `clone_calibration()` accepts `roll_deg=` alongside the existing `tilt_deg=` | Small |
| `iwr6843/trajectory.py:50` | `theta + cal.tilt_rad` → also divide by `cos(roll)` | 1 line |
| `iwr6843/lcmf.py:238-241,715` | Same treatment inside the two-ray model; `geometry` dict gains `roll_rad` | Small |
| `iwr6843/multipath.py:88-102` | Same | Small |
| `iwr6843/club.py:247-257` | Subtract `θ_club·sin(roll)` from `azimuth_rad`, gated by the roll threshold; needs `radar_height_m` and pitch passed in | **The real work** |
| `server.py` | `init_leveling()` per the `init_kld7()` pattern (`:1094-1179`), CLI flags, socket events, tilt/roll precedence | Medium |
| new `leveling/` package | `tilt.py` (pure math), `sensor.py` (driver), `zero.py` (reference file) | Medium |
| new shared `i2c.py` | Bus open + import seam + chip-ID helper, shared with `feat/weather-sensor` | Small |

**Backward compatibility is provable:** with `roll = 0` every formula reduces exactly to today's behaviour. Existing calibration files (which have no `roll_deg` key) load with `roll_rad = 0.0`, and every existing test must pass unchanged. That property is worth an explicit test.

---

## 9. Trust model

A wrong tilt silently biases the primary measurement, so the sensor is not allowed to write the calibration unsupervised:

- **Within ±2° of the calibrated pose** — apply silently. This is the common case: the same unit on slightly different ground.
- **Beyond ±2°** — apply, but surface it. The user sees what was detected and confirms.
- **Beyond ±10°, or no valid zero reference, or an unstable reading** — refuse. That's a stale reference or a unit on its side; it's a fault, not a measurement.

Precedence, logged at INFO on every startup: **explicit `--iwr6843-tilt-deg` > sensor-measured > calibration-file value.** A user who passes the flag has overridden the sensor deliberately.

**Never act on a single sample.** Average the gravity *vectors* (not the angles — they wrap) over ≥1 s, reject the window if its variance exceeds a threshold or if `|g|` is outside 1.0 ± 0.1. A reading taken mid-bump is worse than no reading.

**Free validation:** `tilt_consistency_sweep()` (`calibration_session.py:227-268`) already searches ±3° for the tilt that minimises `component_std_deg`, and `CalibrationShotRecord` carries `tilt_candidate_deg` (`:149-150`). Log the sensor's pitch beside it. Agreement within ~0.5° across a session proves the subsystem; divergence means something is wrong and the user should hear about it. Costs almost nothing.

---

## 10. UI

The on-screen bubble level is a **diagnostic, not a requirement** — the correction happens whether or not anyone looks at it. Its jobs are to show that the unit knows it's tilted, and to warn when tilt exceeds what software can fix.

- New `'setup'` view in `ui/src/App.tsx:33` (the `View` union), not buried in `DebugPanel` — a bubble level nobody opens during setup is pointless.
- Show **deviation from the calibrated pose**, not absolute angle. "1.8° steeper, 3.1° left of calibration" is actionable; "12.2°" is not.
- Inline SVG, house style (`App.tsx:36-67`, `logo/Logo.tsx`).
- New `ui/src/stores/useTiltStore.ts`, listener registered in `socketService.setupListeners()` (`services/socketService.ts:32`).

---

## 11. Non-goals

- **Aim / yaw / heading.** Not observable from gravity. No magnetometer.
- **Motorised leveling.** Correction is in software. Nothing moves.
- **Correcting arbitrary orientations.** Beyond ~10° the geometry assumptions (`h_club`, small-angle azimuth at `club.py:248`) stop holding. The unit refuses rather than reporting confidently wrong numbers.
- **Camera pose.** `CameraCalibration` (`camera/launch_angle.py:30-68`) has no tilt term at all. Future work for the camera branch.
- **K-LD7 paths.** `mount_deg` / `--kld7-mount-tilt` left alone — documented as deprecated hardware.

---

## 12. Open questions

1. **Accelerometer offset temperature drift.** The one blocking unknown. Bench soak before committing to unsupervised correction.
2. **`h_club` assumption in the roll correction (§3.3).** Is a fixed clubhead-height assumption good enough, or should it come from the tee geometry already in `Calibration` (`tee_ball_height_m`, `radar_height_m`)? The latter is probably better and nearly free.
3. **Roll threshold.** 2° is derived from §3.2 plus the `h_club` sensitivity in §3.3. Worth confirming against real club-path repeatability once there's hardware.
4. Does `lcmf.py`'s two-ray model survive a roll term cleanly, or does the direct/image geometry need restructuring? **Needs a closer read before estimating the work.**
5. Should the ±2° "confirm" tier block shot capture, or just annotate the shots? Recommend annotate — never stop someone mid-session.
