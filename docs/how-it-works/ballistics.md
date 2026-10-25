---
icon: lucide/trending-up
---

# Ballistics & Flight

Carry and the rest of the flight are not measured: they are **calculated**.
OpenFlight integrates the ball's trajectory from the shot's own launch
conditions with a published aerodynamic model form whose constants are fitted to
TrackMan 4 exports (flight-fit-v1), then adds bounce and roll from a published
turf-bounce form (Penner 2002) with fitted constants. Every output says what it
is, where it came from and how uncertain it is.

Implemented in `src/openflight/flight_sim.py` (the physics) and
`src/openflight/flight.py` (the labelled outputs). Without them the kiosk's carry
comes from upstream's `src/openflight/ballistics.py`.

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
- **`estimated`**: a flight with an estimated input. Nothing is filled in on any
  install (owner decision 1, 25 Oct 2026): the club-typical spin and the neutral
  0° launch and spin axis an OPS-only install used to fly are gone. The main one: when E3 refuses the full ball speed, the flight flies the OPS243's
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
| `as_measured` | `Shot.air_density_kg_m3` from the BME280 when it has a fresh reading; otherwise ISA sea level, 1.225 kg/m³ | `bme280` (the shot's `air_density_source`), `supplied` or `ISA default` |
| `normalized` | 25 °C (77 °F), 1013.25 hPa (sea level), RH 50 %, calm: 1.1769 kg/m³ | `normalized: …` |

**The normalized reference is TrackMan's.** TrackMan's normalization page says
"Default values are 77°F and sea level (0 feet altitude)", its help pages give
"77°F (25°C) Sea level No wind", and a TM4 normalized export says "normalized to no
wind conditions at 0 ft altitude, 77 °F". Sea level is taken as the standard
atmosphere's 1013.25 hPa (29.92 inHg).

**Humidity is not published.** No public TrackMan page gives the humidity it
normalizes to, so it is a named assumption, `NORMALIZED_REFERENCE_RH_PCT` = 50 %
(as earlier OpenFlight research took it). The density is computed, not typed:
dry air and water vapour as ideal gases (287.05 and 461.495 J/(kg·K)), with the
vapour pressure RH × e_s(T) from **Buck (1981)**, *J. Appl. Meteorol.* 20,
1527–1532: e_s = 6.1121 exp(17.502 T / (240.97 + T)) hPa (his enhancement
factor, about 1.004, is left out; it moves the density by under 0.003 %).

| RH | Density | PGA driver carry | PGA 7-iron carry |
| --- | --- | --- | --- |
| 0 % (dry) | 1.1839 kg/m³ | 287.9 yd | 165.7 yd |
| 50 % (used) | 1.1769 kg/m³ | 288.6 yd | 166.2 yd |
| 100 % | 1.1699 kg/m³ | 289.2 yd | 166.8 yd |

(flight-fit-v1, flying TrackMan's 2023 PGA Tour average driver and 7-iron
launch.) So the unknown humidity is worth about ±0.6 yd of driver carry and ±0.5 yd of
7-iron carry, and under 0.2° of landing angle. Every normalized output's
`source` records the reference (T, p, RH and ρ).

The TrackMan-export regression (`tests/test_ballistics_trackman_regression.py`)
flies the same reference, so it compares normalized with normalized. TrackMan's
published Tour averages are **not** normalized (its older table: "the location
(altitude) and weather conditions have not been taken into consideration"), so
they are anchors only, not a calibration.

The carry the kiosk shows (`carry_spin_adjusted`) is the as-measured carry.

!!! note "The density hook"

    This module does not measure the weather. The server sets
    `Shot.air_density_kg_m3`, in kg/m³, and `Shot.air_density_source` on each shot
    before finalization: the [BME280's](../build/air-sensor.md) humid-air density
    (the same Buck formula as the normalized reference) as `bme280`, or ISA as
    `isa_default` with the reason in `Shot.air_conditions`. Unset, the as-measured
    flight uses ISA and says so. A supplied
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

The effect on a synthetic driver (165 mph, 11°, 2,700 rpm, ISA; flown with the
Ferguson constants in use at the time):

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
ratio of surface speed to translational speed. The form is **Ferguson, McNally &
McPhee (2022)**, "Predicting the Flight of a Golf Ball: Comparing a
Physics-Based Aerodynamic Model to a Neural Network", ISEA Engineering of Sport
14, doi:10.5703/1288284317493. Their Eq. (1) is $S_p$; Eqs. (2) and (3) make
drag and lift second-order in $S_p$; Eq. (4) makes the spin-down moment linear
in it:

$$
C_d = a + b\,S_p + c\,S_p^2
\qquad
C_l = \max\!\left(0,\; d + e\,S_p + f\,S_p^2\right)
\qquad
C_M = g\,S_p
$$

- **Shape:** lift rises with spin, peaks, then falls, as in Bearman & Harvey
  (1976) and Lyu, Kensrud & Smith (2018) for dimpled balls.
- **Past the fit:** above $S_p = 0.75$, the top of Ferguson's fitted range, both
  curves are held at their end value rather than extrapolated. A lob wedge
  reaches $S_p \approx 1.5$ near its apex, where the raw parabolas give negative
  lift.
- **No spin, no lift:** $C_l(0) = 0$.

The active model is `flight-fit-v1` (`flight_sim.FLIGHT_FIT_V1`). Ferguson's
published constants and flight-fit-v2 are kept for comparison in
`flight_models_superseded` (research).

| Model | $a, b, c$ ($C_d$) | $d, e, f$ ($C_l$) | $g$ |
| --- | --- | --- | --- |
| `ferguson-2022` (published) | 0.1304, 0.9287, −0.8259 | 0.0504, 1.2031, −1.1490 | 0.010 |
| **`flight-fit-v1`** (active) | 0.1459, 0.8331, −0.5549 | 0.0646, 1.1261, −0.8869 | 0.010 |
| `flight-fit-v2` (selectable, plus a descent term) | 0.1510, 0.8274, −0.5778 | 0.0715, 1.1250, −0.9015 | 0.0117 |

Ferguson fitted their constants to 1,040 shots: GCQuad launch conditions;
FlightScope X3 carry, offline and apex; 2021 Pro V1; 190 m above sea level.
Flown with TM4's own launch numbers, those constants got carry roughly right.
But apex came out about 2 ft low and the landing angle 2–4° shallow (see below),
because no landing angle or hang time constrained the fit. flight-fit-v1 keeps
their form and refits the seven constants to TM4, on all four outputs at once.

### flight-fit-v1: the refit to TrackMan 4

**What it is fitted to.** TrackMan 4 reports carry, apex, landing angle and hang
time normalized to 77 °F, sea level, no wind and a premium ball.

- **Inputs:** each shot is flown from TM4's own launch numbers: ball speed,
  vertical and horizontal launch, total spin and spin axis.
- **Conditions:** the normalized reference density (1.1769 kg/m³, above), with
  the production gravity, RK4 integrator and frames.
- **Residuals:** model minus TM4, each divided by a scale per output: carry 1 yd,
  apex 1 ft, landing angle 0.5°, hang time 0.1 s.
- **Fit:** the sum of squares is minimised over the seven constants
  (`scipy.optimize.least_squares`), starting from Ferguson's.
- **Spin decay:** $g$ is held to Ferguson's published 0.010–0.015. It landed on
  0.010, the lower bound.

**Data.**

| Export | Shots | In the fit | sha256 |
| --- | --- | --- | --- |
| `session_logs/OpenFlight-Test.Normalized.csv`: a TM4 normalized export, 6 May 2026, TrackMan format, in the repo | 24: 9 7-iron, 8 driver, 7 PW | 24 | `df84641f…f3726e072` |
| `tm4-public-swingsync-2026-10-12.csv`: a second, public TM4 session, 12 Oct 2026 (GitHub release `trackman_session_10_12_26`), SwingSync format | 43: 22 PW, 15 7-iron, 6 6-iron | 36 | `d4dbf8e6…bcb000ee60` |

The second export is someone else's data. It is read from `../tm-exports/`
beside the checkout and never copied into the repo; its path and full hash are
in the calibration record.

- **Spin conversion:** SwingSync gives back and side spin. Total spin is
  $\sqrt{\text{back}^2 + \text{side}^2}$ and the axis is
  $\operatorname{atan2}(\text{side}, \text{back})$.
- **Normalization:** its flight values are taken as normalized to the same
  reference. The per-source tables below would show a mismatch.

**Domain.** The domain is decided on inputs alone, before any residual is
looked at: ball speed ≥ 50 mph, vertical launch ≥ 5°, no topspin.

- **Out:** 7 shots, all tops and grounders from the public session (carry 0.5–31 yd). Both
  models fly them within 0.5 yd, so they carry no aerodynamic information. They
  are reported separately so they do not flatter the RMSE.
- **In:** 60 shots, ball speed 56–163 mph, $S_p$ at launch 0.05–0.63.

**Reynolds number.** Smits & Smith (1994) give a drag coefficient that also
varies with Reynolds number, through a sinusoidal term.

- **Tested:** that term (amplitude free, centre and width as reproduced from
  their paper by later authors), and a linear Re term in $C_d$ or in $C_l$, each
  as an eighth constant.
- **Result:** none improved the held-out error. The 10-fold standardised sum of
  squares was 217.7 for the 7-constant form and 211.0–216.8 with an extra term.
  The data do not support speed dependence beyond what $S_p$ already carries, so
  the form stays Ferguson's.
- **Also tried:** a Smits & Smith power-law lift, $C_l = k S_p^n$. It fitted
  worse in-sample (cost 166 against 101).

**Validation.** Each cell is bias / RMSE / max of model minus TM4, over the 60
in-domain shots unless the row says otherwise. "Held out" means the shot was not
in the fit that flew it.

| Split | Carry (yd) | Apex (ft) | Landing (°) | Hang (s) |
| --- | --- | --- | --- | --- |
| ferguson-2022, all 60 | +0.86 / 1.73 / 4.99 | −1.68 / 2.09 / 5.63 | −1.94 / 2.20 / 4.34 | −0.11 / 0.14 / 0.32 |
| flight-fit-v1 in-sample, all 60 | −0.46 / 0.89 / 1.87 | +0.24 / 0.74 / 1.59 | −0.41 / 0.64 / 1.73 | +0.02 / 0.06 / 0.15 |
| 10-fold held out, all 60 | −0.44 / 0.93 / 2.00 | +0.25 / 0.79 / 1.73 | −0.41 / 0.65 / 1.75 | +0.02 / 0.06 / 0.16 |
| leave-one-shot-out, all 60 | −0.43 / 0.94 / 1.98 | +0.25 / 0.78 / 1.67 | −0.41 / 0.65 / 1.75 | +0.02 / 0.06 / 0.16 |
| fit on 6 May, flown on 12 Oct (36) | −0.38 / 0.73 / 1.33 | +0.05 / 0.62 / 1.13 | −0.25 / 0.46 / 0.99 | +0.01 / 0.05 / 0.08 |
| ferguson-2022 on 12 Oct (36) | +0.28 / 0.71 / 2.17 | −0.97 / 1.16 / 2.53 | −1.47 / 1.69 / 3.41 | −0.07 / 0.10 / 0.24 |
| fit on 12 Oct, flown on 6 May (24) | +4.12 / 10.13 / 30.19 | +9.06 / 15.34 / 33.37 | +1.80 / 4.55 / 10.28 | +0.35 / 0.57 / 1.40 |
| ferguson-2022 on 6 May (24) | +1.72 / 2.60 / 4.99 | −2.75 / 2.99 / 5.63 | −2.65 / 2.80 / 4.34 | −0.15 / 0.18 / 0.32 |

Per club, leave-one-shot-out RMSE, with ferguson-2022 in brackets:

| Source / club | n | Carry (yd) | Apex (ft) | Landing (°) | Hang (s) |
| --- | --- | --- | --- | --- | --- |
| 6 May / driver | 8 | 1.41 (1.54) | 0.85 (4.08) | 0.39 (1.96) | 0.08 (0.23) |
| 6 May / 7-iron | 9 | 0.39 (2.25) | 1.12 (1.74) | 0.76 (2.26) | 0.10 (0.04) |
| 6 May / PW | 7 | 1.65 (3.73) | 0.80 (2.77) | 1.25 (4.00) | 0.04 (0.22) |
| 12 Oct / 6-iron | 2 | 0.48 (0.85) | 1.12 (1.36) | 0.65 (1.15) | 0.06 (0.07) |
| 12 Oct / 7-iron | 14 | 0.44 (0.53) | 0.67 (1.03) | 0.45 (1.00) | 0.05 (0.05) |
| 12 Oct / PW | 20 | 0.82 (0.80) | 0.54 (1.23) | 0.46 (2.08) | 0.04 (0.12) |

What these say:

- **Held out, the refit beats the published constants on every output.** Pooled
  leave-one-shot-out: carry RMSE 1.73 → 0.94 yd, apex 2.09 → 0.78 ft, landing
  angle 2.20 → 0.65°, hang time 0.14 → 0.06 s. The held-out error is within
  0.05 of the in-sample error, so seven constants are not over-fitting 240
  residuals.
- **Across sources.** Fitted to the 6 May export alone, it flies the 12 Oct
  shots (a different player, 56–98 mph, a different export path) with the
  carry error of Ferguson's constants (0.73 against 0.71 yd). Its apex,
  landing-angle and hang-time errors are about half of Ferguson's. The
  per-source tables show no offset between the two sources, which supports
  treating the 12 Oct values as normalized.
- **The reverse split fails on the driver, as it should.** The 12 Oct session
  has no driver. Fitted to its irons alone, the model never sees $S_p$ below 0.13,
  and it flies the 6 May drivers ($S_p$ 0.05–0.08) 13 yd long. Its irons
  transfer: 6 May 7-iron carry RMSE 0.17 yd, PW 1.07 yd. A fit is only good
  over the $S_p$ range its data cover.
- **What is left.** In every split, the 6 May PW is 1.6 yd short and lands
  1.2° shallow, and the 6 May 7-iron peaks 1.0 ft high and hangs 0.10 s long.
  That 7-iron hang time is the one per-club number that got worse
  (0.04 → 0.10 s).
- **Driver confidence: moderate at best.** The low-$S_p$ end of both curves is
  set by 8 driver shots from one player in one session; TM4 marked the spin of
  two of them "Estimated". Held out, the driver numbers are good: carry 1.4 yd,
  apex 0.9 ft, landing 0.4°, hang 0.08 s. A second player's drivers, plus woods
  and hybrids, are needed before the driver result can be called validated.

**Out-of-range sanity: TrackMan's 2023 Tour averages.** TrackMan published PGA
and LPGA Tour averages for 2023 ("Trackman Tour Averages", 2 May 2024,
trackman.com/blog/golf/introducing-updated-tour-averages; tables at
trackman.media/tour-averages). Each row is flown from its ball speed, launch and
spin (axis 0) at the normalized density:

| Tour, club | Published carry / max height (yd) / land (°) | ferguson-2022 | flight-fit-v1 |
| --- | --- | --- | --- |
| PGA driver | 282 / 35 / 39 | 291.3 / 32.0 / 35.4 | 288.6 / 33.7 / 37.1 |
| PGA 7-iron | 176 / 34 / 51 | 171.3 / 32.1 / 46.0 | 166.2 / 33.3 / 48.2 |
| PGA PW | 142 / 32 / 52 | 138.1 / 30.8 / 47.9 | 129.8 / 32.0 / 50.6 |
| LPGA driver | 223 / 26 / 36 | 232.4 / 24.7 / 32.2 | 231.4 / 25.7 / 33.6 |
| LPGA 7-iron | 143 / 26 / 47 | 144.5 / 25.3 / 43.0 | 141.2 / 26.2 / 45.2 |
| LPGA PW | 111 / 27 / 48 | 112.2 / 22.4 / 44.1 | 107.3 / 23.4 / 46.9 |

The refit is plausible here, with one caveat:

- **Closer:** heights and landing angles move toward the published values on
  all six rows, and both driver carries move closer.
- **Further:** the PGA iron carries move away (7-iron 166 against 176, PW 130
  against 142).
- **Why that is not a calibration failure:** TrackMan does not say these
  averages are normalized (its older table said the conditions "have not been
  taken into consideration"), and an average of carries is not the carry of the
  average launch. TM4's own normalized export disagrees with them as well. The
  6 May PW at 105.3 mph, 21.9° and 8,468 rpm carries 135.4 yd normalized,
  yet the Tour PW, slower and with more spin and launch, is listed at 142 yd.

**Fitted coefficients against the published curve.**

| $S_p$ | ferguson-2022 $C_d$ | flight-fit-v1 $C_d$ | ferguson-2022 $C_l$ | flight-fit-v1 $C_l$ |
| --- | --- | --- | --- | --- |
| 0.05 | 0.175 | 0.186 | 0.108 | 0.119 |
| 0.10 | 0.215 | 0.224 | 0.159 | 0.168 |
| 0.15 | 0.251 | 0.258 | 0.205 | 0.214 |
| 0.20 | 0.283 | 0.290 | 0.245 | 0.254 |
| 0.25 | 0.311 | 0.319 | 0.279 | 0.291 |
| 0.30 | 0.335 | 0.346 | 0.308 | 0.323 |
| 0.35 | 0.354 | 0.370 | 0.331 | 0.350 |
| 0.40 | 0.370 | 0.390 | 0.348 | 0.373 |
| 0.45 | 0.381 | 0.408 | 0.359 | 0.392 |
| 0.60 | 0.390 | 0.446 | 0.359 | 0.421 |
| 0.75 | 0.362 | 0.459 | 0.306 | 0.410 |

- **Over $S_p$ 0.05–0.45:** the refit's drag is 2–7 % above Ferguson's and its
  lift 4–10 % above.
- **Lift peak:** now near $S_p \approx 0.63$, against 0.52.
- **Guards:** $C_d > 0$ and $C_l \ge 0$ everywhere (tested from $S_p$ 0 to 3).
  Flights over ball speeds 50–190 mph at $S_p$ 0.05–0.45 are tested for
  plausible carry, apex, landing angle and hang time.

**Provenance (D38).** `FLIGHT_FIT_V1.calibration` records:

- the version and the source ("fitted to TM4 normalized exports: a TM4 normalized
  export, 6 May 2026 … and a second, public TM4 session …");
- the form and its citation, and the date (2026-10-13);
- each export's path and sha256, and the domain;
- the reference density, the residual scales and the method;
- the held-out metrics above.

Every flight output's `source` names the model ("flight-fit-v1: Ferguson,
McNally & McPhee 2022 Eqs. (1)-(4) … refitted to TM4 …"). Carry stays
**calculated**: a fitted model is still a model.

**Refitting.** One command takes any number of exports in either format,
detected from the header:

```bash
uv run python scripts/analysis/fit_flight_model.py                 # the two exports above
uv run python scripts/analysis/fit_flight_model.py more/*.csv      # plus new ones
uv run python scripts/analysis/fit_flight_model.py --loo --plots --output-dir out/
```

- **What it prints:** the constants for `FLIGHT_FIT_V1`; tables for both models
  in-sample, leave-one-source-out, K-fold and (with `--loo`) leave-one-shot-out;
  the Tour check; and the coefficient table. It never edits the code.
- **Missing files:** a missing external export is skipped with a message.
- **Speed:** the fit flies a numpy copy of `simulate`, tested equal to it
  within 1e-6 on every output, at a 5 ms step. Evaluations use the production
  2 ms step. Held-out folds run in parallel (`--jobs`).
- **A new version:** gets a new id (`flight-fit-v2`), its own calibration
  record, and re-derived budgets in `tests/test_ballistics_trackman_regression.py`.
  `flight-fit-v1` is kept for comparison.
- **The active model on any export:** `scripts/analysis/validate_ballistics.py`.

### flight-fit-v2: a descent term (research)

flight-fit-v2 keeps Ferguson's quadratics on the way up. While the ball
descends, both coefficients are scaled by a smooth factor of the descent angle
$\gamma$:

$$
C_d \to C_d\,e^{-k_d \sin^n\gamma}
\qquad
C_l \to C_l\,e^{-k_l \sin^n\gamma}
\qquad
k_d = 4.99,\; k_l = 4.90,\; n = 8.03
$$

The term is empirical. It is motivated by the published dependence of a sphere's
force coefficients on whether the flow around it accelerates (Odar & Hamilton,
"Forces on a sphere accelerating in a viscous fluid", *J. Fluid Mech.* 18,
302–314, 1964): a falling ball speeds up, and $\sin\gamma$ is the share of
gravity along its path. Under 30° of descent it changes little; by 60° it takes
most of both coefficients away, so a steep wedge lands steeper and faster.

Its constants were fitted by the maintainer (flight-fit-v2) over 11,264
launches (ball speed 40–190 mph, launch 0–45°, spin 500–12,000 rpm, spin axis
±30°, launch direction ±10°) on carry, apex, apex distance, landing angle,
landing speed, hang time, curve and landing spin. RMSE on the held-out 20 % and
on the 67 export shots (all held out for flight-fit-v2; in-sample for
flight-fit-v1):

| | flight-fit-v1 | flight-fit-v2 |
| --- | --- | --- |
| Grid hold-out: carry yd / apex ft / landing ° / hang s | 5.75 / 2.09 / 5.98 / 0.25 | 1.72 / 1.81 / 1.10 / 0.06 |
| Grid hold-out: curve yd / landing speed mph | 0.96 / 10.52 | 0.57 / 2.13 |
| Export shots: carry yd / apex ft / landing ° / hang s | 0.84 / 0.68 / 0.63 / 0.06 | 1.06 / 1.40 / 0.46 / 0.07 |
| Export shots: driver carry yd / apex ft (8 shots) | 1.20 / 0.80 | 2.71 / 2.76 |

flight-fit-v2 is far ahead across the launch grid, but behind flight-fit-v1 on
carry and apex on the export shots, mostly on the low-spin drives.

**A fair comparison.** The export rows above are in-sample for flight-fit-v1 and
held out for flight-fit-v2. Re-scored so that every shot is predicted by a model
that never saw it, for both forms: the 12 Oct session's 36 shots by a model fitted
without that session, the 6 May session's 16 irons and wedges by one fitted without
the 6 May session, and each of its 8 drives by one fitted without that drive (the
12 Oct session has no driver). RMSE, carry yd / apex ft:

| Held out | flight-fit-v1 recipe (exports) | flight-fit-v2 (grid) | v2 form, exports | v2 form, grid + exports, drives weighted |
| --- | --- | --- | --- | --- |
| Driver (8) | **1.46 / 0.92** | 2.71 / 2.76 | 1.64 / 1.11 | 1.35 / 0.83 |
| Mid iron (25) | 0.33 / 0.62 | 0.40 / 1.32 | 0.40 / 0.58 | 0.52 / 1.36 |
| Wedge (27) | 0.98 / 0.58 | 0.66 / 0.97 | 0.32 / 0.47 | 0.70 / 1.01 |
| All (60) | 0.87 / 0.65 | 1.12 / 1.48 | 0.69 / 0.64 | 0.76 / 1.15 |
| Landing angle, all (°) | 0.69 | 0.48 | 0.35 | 0.39 |

flight-fit-v1's lead on the drives is not only its in-sample advantage: held out,
it is still ahead of flight-fit-v2. Refitting the v2 form to the exports alone
wins overall but not on the drives; refitting it to the grid and the exports with
the drives weighted 500 times a grid launch wins on the drives but leaves the irons' apex
0.7 ft high, because the form cannot follow the low-spin drives and the rest of
the grid at once. No candidate is ahead on carry and apex both overall and on
drives without losing more than 0.3 yd carry or 0.5 ft apex on irons or wedges,
so `flight-fit-v1` stays active.

### Spin decay

Spin decays by Ferguson's moment coefficient, Eq. (4), $C_M = g\,S_p$, with
$g = 0.010$ in both models: Ferguson's published value, and the refit's own at
the bottom of their 0.010–0.015 bounds. The abstract does not print the torque.
It is taken in Smits & Smith's (1994) convention, its cited source, with $d$ the
diameter and $I = \tfrac{2}{5} m r^2$:

$$
\frac{d\omega}{dt} = -\frac{C_M \, \tfrac{1}{2}\rho v^2 A \, d}{I}
= -\lambda \frac{\omega v}{r}
$$

At 1.204 kg/m³ and $g = 0.010$ this is $\lambda = 2.0\times10^{-5}$: Penner
2001's Eq. (16), "α = −(0.00002)(ω_b v_b / r)", from the same Smits & Smith
data, to 0.2 %. The spin is integrated with the flight (RK4). It replaces a
4 %/s exponential that was attributed to Kiratidis & Leinweber (2018), a paper
on FIFA footballs with no spin decay in it.

## Total: bounce and roll

Total is carry plus bounce and roll from **turf-roll-v2**
(`src/openflight/bounce_roll.py`), from the landing angle, landing speed and
landing backspin (the spin times the cosine of the spin axis tilt):

- **Bounce:** the ball lands on turf that dents under it. Penner, "The run of a
  golf ball" (*Can. J. Phys.* 80, 931–940, 2002), models the dent as a tilt of
  the impact plane toward the incoming ball; here the tilt grows with the landing
  speed, \(\theta_c = \theta_\max (1 - e^{-v/v_\theta})\, e^{-((\alpha-\alpha_p)/w)^2}\),
  and peaks at a landing angle \(\alpha_p\).
- **Each impact** is a rigid ball with grip-slip friction (Cross, "Grip-slip
  behavior of a bouncing ball", *Am. J. Phys.* 70, 1093–1102, 2002): it slides
  with friction \(\mu\) unless friction can stop the contact point, in which case
  it leaves rolling. The normal restitution falls with the normal impact speed,
  \(e = e_0 / (1 + v_n / v_e)\), and the turf caps the speed kept along the
  surface, \(v_t / \sqrt{1 + (v_t / v_c)^2}\) with \(v_c\) falling with \(v_n\).
- **Hops** between bounces fly through the flight model (`flight_sim.hop_flight`):
  drag, Magnus lift and spin decay as in flight, in the flight's own air,
  carrying the spin out of each bounce (backspin lifts a hop, topspin pushes it
  down). RK4 at 10 ms lands a hop within 2 mm of the 2 ms flight step. Once a hop
  is under 1 cm the ball slides until it rolls, then rolls out against a constant
  resistance plus a speed-squared turf drag, \(\dot v = -(g c_0 + k v^2)\),
  which integrates in closed form. Heavy backspin on a steep landing can bring the
  ball back past where it landed.

The twelve constants were fitted by the maintainer (turf-roll-v2) for fairway
turf of normal firmness, with the hops flown by flight-fit-v1 in normalized air.
The fit covered 4,096 landing states (landing speeds 18–100 mph, landing angles
5–65°, backspin 0–11,000 rpm) plus the landings of 3,072 club-typical launches
(driver and woods, mid irons, wedges), with a random 20 % of each held out.

turf-roll-v1, the same bounce with vacuum hops, stays selectable. A vacuum hop
flies too far after a fast, shallow landing, which is where drives land. RMSE
(yd), turf-roll-v1 → turf-roll-v2:

| | All | Driver / wood | Mid iron | Wedge |
| --- | --- | --- | --- | --- |
| Roll, held-out landing states | 1.765 → 1.759 | | | |
| Roll, held-out club-typical landings | 1.23 → 1.08 | 1.50 → 1.23 | 0.96 → 0.82 | 1.16 → 1.16 |
| Total, held-out club-typical launches (flight-fit-v1 carry) | | 3.55 → 3.40 | 2.08 → 2.13 | 1.78 → 1.84 |
| Total, 62 export shots vs their estimated total (flight-fit-v1 carry) | 1.70 → 1.31 | 3.48 → 2.08 | 1.56 → 1.34 | 0.80 → 0.94 |

The export shots are the 67 flight-fit-v1 was fitted to, less the five that land
outside 5–65°. On the 60 that land inside both models' ranges, the USGA / R&A
line below is at 5.90 yd (driver 5.58, mid irons 4.42, wedges 7.07). Part of the
remaining total error is the flight's: flight-fit-v1 carries wedges 0.8 yd short
on the export shots, which turf-roll-v1's longer roll had partly hidden.

OpenFlight applies it as follows:

- **Surface:** assumed "fairway, normal firmness" unless one is set. The source
  says `surface assumed: fairway, normal firmness`. Any other surface gives total
  "not measured".
- **Range of use:** landing angles 5–65°; outside them there is no total, with
  the reason.
- **Direction:** the model gives a distance, not a direction. The ball is
  assumed to roll on along its landing heading, with no sideways kick, which
  gives `total_side`.

The **USGA & R&A Rules Ltd (9 March 2021), "Proposed Bounce Model for Use in
Evaluating Optimum Overall Distance"**, Eq. 1, is kept for comparison with
turf-roll-v1 (`flight_models_superseded`, research; `model=` on
`bounce_and_roll_yards` and `Trajectory.rest_point`):

$$
d_\text{bounce} = 79.1 - 1.6\,\alpha_f \quad\text{(yards; } \alpha_f \text{ = landing angle in degrees)}
$$

It was fitted to USGA/R&A ball-launcher tests and 2016–2018 tour bounce-and-roll
data on fairways, over landing angles of 10–50°. It reaches 0 yd at 49.4° and is
negative from there to 50°; a fairway ball does not roll backwards on it, so the
roll is clamped at 0 there and `total` / `total_side` carry the note "roll
clamped at 0: Eq. 1 is negative above 49.4°". At 37.6° it gives 18.94 yd.

## Inputs

The same on every install (owner decision 1, 25 Oct 2026: OPS-only installs no
longer fill anything in):

| Input | Used |
| --- | --- |
| Ball speed | Full ball speed when the shot has one (`ball_speed_full_mph`, or `ball_speed_contract` "full"); otherwise the OPS243 radial speed, labelled as such |
| Vertical launch | Measured (or a labelled camera or model route), or no flight |
| Horizontal launch | Measured (or a labelled camera route), or no flight |
| Spin | Measured (confidence ≥ 0.7), or a model's under its label (calculated, model geometry (D39), estimated), or no flight |
| Spin axis | Measured, calculated, model geometry (D39) or estimated, or no flight |

`inputs.ball_speed.label` records which ball speed was used. A spin axis with no
`spin_axis_source` on the shot is labelled as the kiosk's proxy (horizontal
launch minus club path).

### The club-typical spin table

There is one table, `clubs.physics.CLUB_TYPICAL_SPIN_RPM`. Its values were first
described as "TrackMan PGA Tour averages". They match no published TrackMan table
(7-iron: 6,500 rpm here, against 7,124 rpm on TrackMan's 2023 PGA Tour spin-rate
page), so they are labelled `club-typical estimate (source: OpenFlight club
table, …)`. Neither the flight nor the simulator connectors use it any more
(owner decision 1, 25 Oct 2026); only research scripts read it.

## Simulators

The simulator connectors send a shot's carry only when its `carry_status` is
`measured` or `calculated`, i.e. flown on the shot's own measured or calculated
inputs (D31). Its provenance is `calculated`: a simulated carry is the flight
model's output, never `measured`, even when flown on a measured spin. A
`model_geometry` carry is not sent (see `SIMULATORS_ACCEPT_MODEL_GEOMETRY` in the
collision-model page), nor an `estimated` one (`SIMULATORS_ACCEPT_ESTIMATED`). A
shot missing a required input, or having it only as model geometry or an
estimate, is not sent, on every install: an OPS-only shot is no longer sent with
club-table launch angles and a typical spin (owner decision 1, 25 Oct 2026). A
spin axis is never `measured` (no sensor measures one). These labels are the provenance badges only; the wire
format is unchanged. Simulators get the as-measured carry; they fly the ball
themselves from speed, launch and spin.

## Constants

| Constant | Value | Note |
| --- | --- | --- |
| `BALL_MASS_KG` | 0.04593 | USGA **maximum**-conforming ball, 45.93 g |
| `BALL_RADIUS_M` | 0.02135 | 42.7 mm diameter |
| `AIR_DENSITY_STD` | 1.225 kg/m³ | ISA sea level, 15 °C: the as-measured default |
| `AIR_DENSITY_NORMALIZED` | 1.1769 kg/m³ | Computed: 25 °C, 1013.25 hPa, RH 50 % (Buck 1981): the normalized flight |
| `NORMALIZED_REFERENCE_RH_PCT` | 50 % | TrackMan publishes none: a named assumption |
| `ACTIVE_AERO_MODEL` | `FLIGHT_FIT_V1` | The aerodynamic model `simulate` flies by default |
| `CD_POLY` | (0.1459, 0.8331, -0.5549) | The active model's $C_d$ polynomial $(a, b, c)$ |
| `CL_POLY` | (0.0646, 1.1261, -0.8869) | The active model's $C_l$ polynomial $(d, e, f)$ |
| `SP_FIT_MAX` | 0.75 | Top of the fitted $S_p$ range; curves held beyond it |
| `CM_PER_SP` | 0.010 | The active model's spin-down moment per unit $S_p$ (Eq. 4) |
| `FERGUSON_2022` | (0.1304, 0.9287, -0.8259), (0.0504, 1.2031, -1.1490), 0.01 | Ferguson's published constants (research) |
| `FLIGHT_FIT_V2` | (0.1510, 0.8274, -0.5778), (0.0715, 1.1250, -0.9015), 0.0117; descent 4.99, 4.90, 8.03 | Not active (research) |
| `GRAVITY` | 9.81 m/s² | |
| `DT_SECONDS` | 0.002 | 500 Hz integration |
| `RANGE_DT_SECONDS` | 0.02 | Range sweep only |
| `MAX_FLIGHT_SECONDS` | 15.0 | Safety cap |
| `SAMPLE_INTERVAL_S` | 0.05 | Cadence of returned trajectory points |
| `HOP_DT_SECONDS` | 0.01 | Step for the hops between bounces |
| `TURF_ROLL_V2` | twelve fitted constants, landing angle 5–65°, hops through the air | Fairway bounce and roll (active) |
| `TURF_ROLL_V1` | twelve fitted constants, landing angle 5–65°, vacuum hops | Fairway bounce and roll (research) |
| `USGA_RA_2021_BOUNCE_MODEL` | 79.1 yd, −1.6 yd/°, 10–50° | Fairway bounce and roll (research) |

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
- **Without a flight** (ballistics disabled, a missing launch angle, spin or spin
  axis, or an invalid supplied density): carry is `not_measured` on every
  install, and the log names the reason. The ball-speed spin table no longer
  stands in on an OPS-only install (owner decision 1, 25 Oct 2026).
- **Mock shots** have no flight, so no carry.

## Disabling it

```bash
scripts/start-kiosk.sh --no-ballistics
```

Carry is then not measured on every install, and every flight output says
"flight model disabled".

## Related

- [Rolling buffer & spin detection](rolling-buffer.md): where measured spin
  comes from
- [Constants](../reference/constants.md): the full constant set
- `metric_definitions.py`: D37's public definitions, including `curve`
