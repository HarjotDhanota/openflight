---
icon: lucide/trending-up
---

# Ballistics & Flight

Carry and the rest of the flight are not measured: they are **calculated**.
OpenFlight integrates the ball's trajectory from the shot's own launch
conditions with a published aerodynamic model, then adds bounce and roll from a
published turf model. Every output says what it is, where it came from and how
uncertain it is.

Implemented in `src/openflight/ballistics.py` (the physics) and
`src/openflight/flight.py` (the labelled outputs).

## The outputs

`compute_flight` gives the eight TrackMan 4 flight outputs. Each goes on the
shot as `Shot.flight` and into the shot payload as `flight`.

| Output | Unit | What it is |
| --- | --- | --- |
| `carry` | yd | Horizontal straight-line distance from the launch point to the landing point at launch height |
| `carry_side` | yd | Signed side of the landing point from the x axis, positive right |
| `total` | yd | Straight-line distance from the launch point to where the ball stops |
| `total_side` | yd | Signed side of the rest point, positive right |
| `apex` | yd | Highest point above launch height |
| `landing_angle` | deg | Descent angle below horizontal at landing |
| `hang_time` | s | Time from launch to landing |
| `curve` | yd | Side distance of the landing point from the launch-direction line (D37's `metric_definitions.curve`), positive right |

Every output has the same fields:

| Field | Meaning |
| --- | --- |
| `value` | The number, or null |
| `unit` | As in the table above |
| `status` | `calculated`, `model_geometry`, `estimated` or `not_measured` (below) |
| `source` | The model, its citation, the air, for total the surface assumed, and any model-geometry inputs |
| `range` | `[low, high]`: ±1σ from the input uncertainties (below), or null |
| `reason` | Why the value is missing; null when there is a value |

The payload also records the five `inputs` with their values and labels (ball
speed, vertical and horizontal launch, spin, spin axis), the horizontal `frame`
the angles were measured in, the `surface`, the `range_basis`, the
`density_hook`, and `model_geometry_inputs` (the inputs built on model geometry,
named; empty when there are none).

### Status

Every output takes the weakest label among its inputs, in the order measured >
calculated > model_geometry > estimated > not_measured. A flight is a model's
output, so it is at best `calculated`.

- **`calculated`**: a fusion unit, with every input measured or calculated. The
  model is published (D38), and the inputs come from this shot alone.
- **`model_geometry`**: a fusion unit whose spin or spin axis rests on an assumed
  swing geometry (D39): E2's spin or D-plane tilt built on E1's arc prior, which
  on today's lens is every shot's club path and attack angle. The `source` ends
  with the inputs that were, e.g. "model-geometry inputs: spin (Penner 2001; club
  inputs model geometry (D39)), spin axis, club path from the arc prior, attack
  angle from the arc prior". Simulators do not get these values (D31).
- **`estimated`**: an OPS-only install (its inputs include the club-typical spin
  and a neutral 0° for any missing horizontal launch or spin axis; the `inputs`
  block names each one it used), or a fusion flight with an estimated input. The
  main one: when E3 refuses the full ball speed, the flight flies the OPS243's
  line-of-sight (radial) speed, a different and lower quantity. Every output, raw
  and normalized, is then `estimated` with the reason "ball speed is the OPS
  line-of-sight speed (full speed refused: <E3's reason>), about 3-5% low", and
  `estimated_inputs` lists it. `Shot.carry_status` is then `estimated` too.
- **`not_measured`**: nothing is filled in. A fusion shot without a measured,
  calculated or model-geometry spin, a spin axis, or both launch angles has no
  flight, and every
  output gives the reason ("spin not measured", "spin axis not measured", …).
  Spin and spin axis may be calculated values (E2's collision model); they keep
  their labels in `inputs`. `--no-ballistics` gives "flight model disabled".

`Shot.carry_status` is the flight's label on a fusion unit: `calculated`, or
`model_geometry`, never `measured` (a simulated carry is a model's output).
`measured` remains only in older logs.

### Raw and normalized

Two flights fly from the same launch values; only the air differs (TrackMan
exports the same split).

| Flight | Air density | Label |
| --- | --- | --- |
| `as_measured` | `Shot.air_density_kg_m3` when supplied; otherwise ISA sea level, 1.225 kg/m³ | `supplied` or `ISA default` |
| `normalized` | Sea level (101,325 Pa), 25 °C (77 °F), calm, dry air: 1.1839 kg/m³ | `normalized: …` |

The normalized conditions are the ones the owner's TrackMan export states: "Data
are normalized to no wind conditions at 0 ft altitude, 77 °F". The export does
not give humidity, so dry air is our stated assumption. The carry the kiosk
shows (`carry_spin_adjusted`) is the as-measured carry.

!!! note "The density hook"

    OpenFlight does not measure the weather here. A density source (the owner's
    BME280 work) sets `Shot.air_density_kg_m3`, in kg/m³, on each shot before
    finalization. Unset, the as-measured flight uses ISA and says so. A supplied
    value that is not finite or lies outside 0.6–1.6 kg/m³ (a units or wiring
    error, not weather) is never replaced by ISA: that flight is not measured,
    with the reason, and the normalized flight is unaffected.

### Range

The `range` is a ±1σ band. Each input is perturbed by one assumed uncertainty,
one at a time, and the changes are summed in quadrature. A sweep flies 6 shots
at a 20 ms step, takes about 18 ms on a desktop, and runs once per shot.

| Input | Assumed σ | Source |
| --- | --- | --- |
| Ball speed | 1 mph | D32 goal |
| Vertical and horizontal launch | 0.5° each | D32 goal |
| Spin | 10 % | Part E calculated-spin goal |
| Spin axis | 3° | Part E spin-axis goal |

These are goals, not measured per-shot uncertainties. Air density is not varied.
The same σ is applied to both flights.

## Coordinate frames (W7)

- **World frame:** x is downrange along the horizontal-launch zero, y is
  lateral (positive right) and z is up. The x axis is the target line once D34's
  aim point is set; otherwise it is the unit's boresight. The flight's `frame`
  field carries `Shot.launch_angle_horizontal_frame`.
- **Carry** is the straight-line horizontal distance to the landing point,
  `hypot(x, y)`. This is TrackMan's public definition: "the straight-line
  distance between where the golf ball was launched from and where it crosses
  a point that has the same elevation". Carry used to be the x component alone,
  which made a ball launched 10° right fly 5 yd "shorter".
- **Spin axis** is a tilt against the launch direction. At 0° (pure backspin)
  the spin vector is horizontal and perpendicular to the horizontal launch
  direction, so it turns with the horizontal launch angle h:

$$
\hat\omega = (\cos a \sin h,\; -\cos a \cos h,\; \sin a)
$$

  Positive *a* tilts the top of the axis right (fade/slice). Before this fix the
  axis stayed fixed to the world y axis, so a pure-backspin shot launched right
  also curved.

The effect on a synthetic driver (165 mph, 11°, 2,700 rpm, ISA):

| Launch direction, axis | Before: carry, side | After: carry, side, curve |
| --- | --- | --- |
| 0°, 0° | 274.66, 0.00 | 274.66, 0.00, 0.00 |
| 5° right, 0° | 273.47, +25.31 | 274.66, +23.94, 0.00 |
| 10° right, 0° | 269.92, +50.40 | 274.66, +47.69, 0.00 |
| 30° right, 0° | 232.48, +144.67 | 274.66, +137.33, 0.00 |
| 5° right, +10° | 269.07, +45.48 | 273.07, +44.12, +20.47 |
| 0°, +10° | 272.30, +20.47 | 273.07, +20.47, +20.47 |

The committed TrackMan export (24 shots, normalized air, TrackMan's own launch
conditions as inputs) was used to check the fixes. Carry RMSE fell from 2.90 to
2.29 yd, and carry side RMSE from 1.69 to 1.36 yd. No coefficient changed.

## The flight model

Three forces act on the ball: gravity, aerodynamic drag opposing motion, and
the Magnus lift generated by spin.

$$
\vec{F} = m\vec{g}
- \tfrac{1}{2}\rho A C_d \lvert\vec{v}\rvert \vec{v}
+ \tfrac{1}{2}\rho A C_l \lvert\vec{v}\rvert^2 \hat{n}
$$

The coefficients depend on the **spin parameter** $S_p = r\omega / v$, the
ratio of surface speed to translational speed:

$$
C_d = a + b\,S_p + c\,S_p^2
\qquad
C_l = \max\!\left(0,\; d + e\,S_p + f\,S_p^2\right)
$$

- **The form and coefficients:** second-order polynomials, as **Ferguson, McNally
  & McPhee (2022)** fitted them to 1,040 measured shots (ISEA 14,
  doi:10.5703/1288284317493). The coefficients are their published values, not
  re-fitted here. The owner kept this model on 12 Oct (upstream #266).
- **Shape:** lift rises with spin, peaks near $S_p \approx 0.52$, then falls.
- **Past the fit:** above $S_p = 0.75$, the top of the fitted range, both curves
  are held at their end value rather than extrapolated.

These forms are consistent with **Bearman & Harvey (1976)** and **Kensrud &
Smith (2018)** for dimpled balls past the drag crisis (Re ≈ 5×10⁴–2×10⁵).

Spin decays exponentially over the flight, at roughly 4 %/s per **Kiratidis &
Leinweber (2018)**:

$$
\omega(t) = \omega_0 \, e^{-\lambda t}
$$

## Total: bounce and roll

Total is carry plus bounce and roll from **USGA & R&A Rules Ltd (9 March 2021),
"Proposed Bounce Model for Use in Evaluating Optimum Overall Distance"**, Eq. 1.
It was published with the R&A's "Notice to Golf Ball Manufacturers –
Publication of Technical Documents" (16 March 2021):

$$
d_\text{bounce} = 79.1 - 1.6\,\alpha_f \quad\text{(yards; } \alpha_f \text{ = landing angle in degrees)}
$$

- **What it models:** fairways. It was fitted to USGA/R&A ball-launcher tests
  (USGA test range, Pinehurst No. 2, Philadelphia CC, Rutgers turf plots, Lehigh
  CC) and to 2016–2018 PGA TOUR ShotLink/TrackMan bounce-and-roll data.
- **The slope, −1.6 yd/°,** sits between the USGA test range (−1.8 to −1.6) and
  the tour (−1.5).
- **The intercept** keeps the 2020 Overall Distance Standard test balls' mean
  total unchanged.
- **Worked figure:** at their mean landing angle, 37.6°, the model gives
  18.94 yd; at the typical 39° it gives 16.7 yd.

OpenFlight applies it as follows:

- **Surface:** assumed "fairway, normal firmness" unless one is set. The source
  says `surface assumed: fairway, normal firmness`. No other surface has a
  published model in the code yet, so any other surface gives total "not
  measured".
- **Range of use:** only for landing angles inside the paper's data, 10–50°.
  A steeper landing (a high wedge) or a flatter one has no total, with the reason.
- **Clamp at zero:** Eq. 1 reaches 0 yd at 49.4° (79.1 / 1.6) and is negative
  from there to 50°, the top of its data. A fairway ball does not roll backwards,
  so the roll is clamped at 0 there and `total` / `total_side` carry the note
  "roll clamped at 0: Eq. 1 is negative above 49.4°". Tour irons land at
  48–52° (TrackMan's 2023 Tour averages), so this edge is reached in practice.
- **Direction:** the model gives a distance, not a direction. The ball is
  assumed to roll on along its landing heading, with no sideways kick, which
  gives `total_side`.

As a sanity check, not a gate: fed the TrackMan export's own landing angles, the
model's roll is within +0.8 yd (bias) and 3.8 yd (RMSE) of TrackMan's estimated
roll on the 21 shots inside its range. TrackMan's total is itself modelled, so
this is not ground truth.

Penner, "The run of a golf ball" (*Can. J. Phys.* 80, 931–940, 2002) and Haake's
turf measurements model the bounce on **greens**. The USGA/R&A paper sets them
aside for fairway distance for that reason. A green model would be a separate
`BOUNCE_MODELS` entry.

## Inputs

| Input | Fusion unit | OPS-only install |
| --- | --- | --- |
| Ball speed | Full ball speed when the shot has one (`ball_speed_full_mph`, or `ball_speed_contract` "full"); otherwise the OPS243 radial speed, labelled as such | Same |
| Vertical launch | Measured, or no flight | Measured, or the labelled club-table estimate |
| Horizontal launch | Measured, or no flight | Measured, or a substituted 0° |
| Spin | Measured (confidence ≥ 0.7), calculated, or model geometry (D39), or no flight | Measured, calculated, model geometry, or the club-typical estimate |
| Spin axis | Measured, calculated, or model geometry (D39), or no flight | Measured, or a substituted 0° |

`inputs.ball_speed.label` records which ball speed was used. A spin axis with no
`spin_axis_source` on the shot is labelled as the kiosk's proxy (horizontal
launch minus club path).

### The club-typical spin table

There is one table, `clubs.physics.CLUB_TYPICAL_SPIN_RPM`, used by both the
kiosk's ballistics and the simulator connectors. Its values were first described
as "TrackMan PGA Tour averages". They match no published TrackMan table (7-iron:
6,500 rpm here, against 7,124 rpm on TrackMan's 2023 PGA Tour spin-rate page).
They are therefore labelled `club-typical estimate (source: OpenFlight club
table, …)`. A fusion unit never uses them.

## Simulators

The simulator connectors send a fusion shot's carry only when its
`carry_status` is `measured` or `calculated`, i.e. flown on the shot's own
measured or calculated inputs (D31); its provenance is `measured` when it was
flown on a measured spin, as before, else `estimated`. A `model_geometry` carry
is not sent (see `SIMULATORS_ACCEPT_MODEL_GEOMETRY` in the collision-model
page). A fusion shot missing a required input is not sent, as before. An OPS-only shot's carry goes out labelled `estimated`,
whatever its launch angle. Simulators get the as-measured carry; they fly the
ball themselves from speed, launch and spin.

## Constants

| Constant | Value | Note |
| --- | --- | --- |
| `BALL_MASS_KG` | 0.04593 | USGA **maximum**-conforming ball, 45.93 g |
| `BALL_RADIUS_M` | 0.02135 | 42.7 mm diameter |
| `AIR_DENSITY_STD` | 1.225 kg/m³ | ISA sea level, 15 °C: the as-measured default |
| `AIR_DENSITY_NORMALIZED` | 1.1839 kg/m³ | Sea level, 25 °C, dry: the normalized flight |
| `CD_POLY` | (0.1304, 0.9287, -0.8259) | $C_d$ polynomial $(a, b, c)$ |
| `CL_POLY` | (0.0504, 1.2031, -1.1490) | $C_l$ polynomial $(d, e, f)$ |
| `SP_FIT_MAX` | 0.75 | Top of the fitted $S_p$ range; curves held beyond it |
| `SPIN_DECAY_RATE` | 0.04 /s | ≈4 %/s |
| `GRAVITY` | 9.81 m/s² | |
| `DT_SECONDS` | 0.002 | 500 Hz integration |
| `RANGE_DT_SECONDS` | 0.02 | Range sweep only |
| `MAX_FLIGHT_SECONDS` | 15.0 | Safety cap |
| `SAMPLE_INTERVAL_S` | 0.05 | Cadence of returned trajectory points |
| `USGA_RA_2021_BOUNCE_MODEL` | 79.1 yd, −1.6 yd/°, 10–50° | Fairway bounce and roll |

!!! note "Why the maximum-conforming ball"

    Mass and radius use the USGA maximum rather than an average, so carry is
    bounded by the rules rather than by a guess about which ball is in play.

## Integration

The integrator is fourth-order Runge–Kutta at 500 Hz. RK4 error is
$O(\Delta t^5)$, so at this step it is effectively exact for these timescales;
noticeably larger steps start to shorten long drives. The range sweep alone uses
20 ms, which moves a driver's carry by under 0.01 yd.

The solver returns a point every 50 ms rather than every step. Integration still
runs at the full rate; the sparser list keeps WebSocket payloads and session
logs a reasonable size. Real shots land in 5–9 s, and the 15 s cap makes a
solver instability fail loudly instead of looping.

## Shot finalization

`_finalize_shot_detected` in the server is the only place that writes
`carry_spin_adjusted` and `flight` for a live shot.

- **The flight:** it calls `compute_flight` once.
- **Carry:** it stores the as-measured carry in `carry_spin_adjusted`.
- **Fusion unit without a flight:** carry is `not_measured`, and the log names
  the reason.
- **OPS-only install without a flight** (ballistics disabled, no launch angle,
  or an invalid supplied density): carry falls back to the labelled ball-speed
  spin table.
- **Mock shots** have no flight.

## Disabling it

```bash
scripts/start-kiosk.sh --no-ballistics
```

This uses the legacy carry-table estimator for every OPS-only shot. On a fusion
unit, carry is not measured, and every flight output says "flight model
disabled".

## Related

- [Rolling buffer & spin detection](rolling-buffer.md): where measured spin
  comes from
- [Constants](../reference/constants.md): the full constant set
- `metric_definitions.py`: D37's public definitions, including `curve`
