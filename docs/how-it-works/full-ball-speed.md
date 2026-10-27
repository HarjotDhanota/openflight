---
icon: lucide/gauge
---

# Full ball speed

The OPS243 measures **radial** speed: the part of the ball's velocity along the
line from the radar to the ball. The ball leaves upward and the radar sits low
and to one side, so the radial reading is always lower than the ball's full
speed. On a 7-iron it reads about 3 mph low; on a wedge, 4-8 mph low.

OpenFlight keeps that radial reading as the canonical `ball_speed_mph` (see the
[speed contract](../camera/speed-contract.md)). It also computes the **full**
ball speed in `ball_speed_full`, using the shot's own inputs. Two routes run in
order: a timed launch-based projection, then a peak-radial model if that route
refuses or raises. `input_sources.ball_speed_full` records the chosen route and
every preceding refusal. Neither route changes the canonical radial reading.

## The method

```
full speed = radial speed / (LOS . dir)
```

- **dir** is the ball's direction, from this shot's measured launch angles:
    - vertical from the IWR LCMF (or the camera);
    - horizontal from the camera, or the IWR's horizontal when its phase
      reference is calibrated.
    A target-line angle is turned into the unit's frame by the one horizontal
    offset (`horizontal_frame.py`).
- **LOS** is the unit vector from the OPS to the ball at the reading's time:
    - the OPS is where the rig file puts it (`ops_offset_mm`);
    - the ball starts where the IWR tee range and the setup's tee side offset
      put it;
    - by the reading's time it has moved along `dir`. The ball's position
      depends on its speed, so the solve iterates; it converges in a few steps.
- **The reading** is one FFT window of the OPS speed timeline, on the OPS's own
  clock:
    - the first window that starts after the measured impact (so no club return
      shares it), on the ball's track, with LOS . dir at or above the threshold;
    - only windows that start within 30 ms of impact are used.
- **The impact** is the OPS club-to-ball transition when there is one. Otherwise
  it is the sound trigger, walked back by the sound's flight time from the ball
  to the rig's mic.
- **Referred to separation:** the value adds back the speed lost between
  separation and the reading:
    - drag from Ferguson, McNally & McPhee (2022, ISEA 14,
      doi:10.5703/1288284317493), the same Cd(Sp) the flight model uses, over
      Sp 0.05-0.50, with ISA sea-level air and a USGA-limit ball;
    - gravity along the path.
    It adds about 0.03-0.04 mph per millisecond: 0.1 mph for a reading 3 ms
    after impact.

The result is **calculated** on measured inputs, **estimated** on nominal or
assumed inputs, and **experimental** on an experimental input. Camera launch
labels use the recorded lens calibration. A configured IWR tilt or a partial or
ambiguous IWR range also weakens the result. Failed horizontal gates can still
feed an experimental projection; they never become measured inputs.

## When the first route refuses

A refusal tries the peak-radial route below. Full speed is **not measured** only
when both routes refuse; their reasons are retained. The first route refuses when:

- **the geometry is ill-conditioned.** LOS . dir is below **0.866 (cos 30°)** at
  every candidate reading. The speed error grows as tan(angle) × angle error. At
  30°, a 1° launch error (the 95th-percentile launch goal) already costs
  1.0 % of speed, about 1-1.6 mph at 100-160 mph: the 95th-percentile budget
  for full ball speed. Past 30° it grows quickly (40°: 1.5 %). On the v3 rig at
  2.1 m this refuses launches steeper than about 43°;
- **a launch angle is absent or unsupported:** a table-estimated vertical,
  an unknown source, an implausible horizontal, or an unknown/mixed frame.
  Labelled estimated and experimental sensor angles are allowed with those labels;
- **a geometry source is missing:** no rig file or OPS offset, no IWR tee range
  or heights, or no tee side offset from setup or the resting-ball camera.
  The first route never supplies a missing side offset;
- **there is no OPS ball reading** within 30 ms after a measured impact;
- **the impact is a sound trigger,** and the rig has no mic offset to walk it
  back.

## The range

Every value carries `range_mph`. It is the root-sum-square of one-at-a-time
changes for stated input tolerances, and each term is named in
`uncertainty_mph`:

| Input | Tolerance |
|---|---|
| Vertical and horizontal launch | ±1° each (the 95th-percentile goal) |
| IWR tee range | ±50 mm |
| Tee side offset | ±50 mm |
| OPS position | ±3 mm per axis |
| Impact time | half the OPS transition gap; ±3 ms for a sound trigger |
| Radial reading | half the FFT output bin (0.05 mph) |
| FFT window | the projection's change across the 4.3 ms window |
| Drag | the Cd band, and air density ±10 % |

None of these inputs is validated against TrackMan yet. The tolerances are stated
assumptions.

These sources of error are not modelled, and are named in the payload:

- the OPS spectral-peak extraction bias;
- a ground-reflected path near the tee;
- gravity's bend of the path over the referral;
- the unit's tilt applied to the rig offsets.

## Synthetic results

A forward model gives the radial reading the OPS would see. The method then
recovers the full speed. Setup: the v3 rig file, a 30 mm tee side offset, and an
OPS transition impact:

| Shot | Launch V / H | Tee range | LOS . dir | Recovered | Range ± | Canonical radial |
|---|---|---|---|---|---|---|
| Driver 165 mph | 11° / 0° | 2.1 m | 0.981 | 165.000 | 0.60 | 2.6 mph low |
| 7-iron 120 mph | 16° / 2° | 2.1 m | 0.963 | 120.000 | 0.59 | 2.8 mph low |
| PW 100 mph | 24° / −2° | 2.1 m | 0.914 | 100.000 | 0.81 | 4.5 mph low |
| SW 85 mph | 32° / 0° | 2.1 m | 0.867 | 85.000 | 0.82 | 6.5 mph low |
| LW 70 mph | 38° / 0° | 2.1 m | 0.867 | 70.000 | 0.57 | 8.2 mph low |

- The vertical launch's ±1° dominates every range.
- For the SW and LW, the reading near the tee is ill-conditioned. The method
  takes the first later window that clears 0.866.

This proves the inversion, not the radar. The goal is a median error of 1 mph or
less, and a 95th percentile of 2 mph or less, graded against TrackMan shots.

## Second ball-speed route: peak radial projection

`ops_total_projection` divides the canonical radial speed by
`speed_correction.radial_speed_factor`. It predicts the peak radial share over
an assumed 70 ms window, with the historical 0.027 mph/ms drag rate. It needs
vertical launch and OPS-relative ball geometry; it assumes flight along the
boresight and does not use horizontal launch or a timed OPS track. If the setup
and resting-ball camera provide no side offset, it explicitly assumes zero and
labels the result estimated. Missing vertical launch or range still refuses.
The legacy factor's 0.5 safety clamp also refuses rather than publishing a
clipped value as a solved projection.

The uncertainty record names the launch, range, side-offset, rig-position and
radial-resolution tolerances, plus a historical 0.5 mph scatter allowance. That
allowance is not a current-rig accuracy result or a confidence interval. The
fixed drag, zero horizontal launch and peak-extraction assumptions are recorded
with the result. Experimental inputs remain experimental in both the producer
record and the published metric.

## Club speed and smash

Full club speed uses the actual timed club pick, one FFT reading retained in
`ops_speed_track.club_reading`. It anchors the OPS and camera clocks at contact,
walks sound-trigger time back by the ball-to-mic sound flight, and evaluates the
head track's position and turning direction at that reading's window centre.

The head state and rig OPS offset share a camera-centred (lateral, up, forward)
frame. Full speed is the reading divided by LOS dot direction. Projection below
cos(30 degrees), a flagged track, missing positions, a window crossing contact,
or a reading more than 30 ms before contact refuses with a reason. The value is
at the reading's time; no acceleration to contact is invented.

This route is **experimental**: depth already depends on OPS speed and contact
range, the camera tracks the hosel while the radar may see a faster toe, and the
rig offsets do not include enclosure tilt. The reported sensitivity range names
radial resolution, clock uncertainty, FFT-window variation, path/attack sigmas,
depth and OPS position. It is not a validated accuracy interval.

`smash_factor_full` divides full ball speed by full club speed under the weaker
label. A missing full speed refuses; full divided by radial is never used. The
original `smash_factor` remains the radial ratio on fusion units.

## Simulators and flight

Simulators read canonical metric records. Calculated full speed is sendable;
estimated and model-geometry values need their explicit opt-ins. Experimental
values are never sent. When full speed is not sendable, the resolver can use the
radial speed under its actual label. Optional full club speed obeys the same
policy. The flight model can consume experimental full ball speed, and outputs
built on it inherit that label.

## Code

- `src/openflight/full_speed.py`: the method, refusals, range, club and smash.
- `rolling_buffer/monitor.py`: attaches the OPS readings around impact to the
  shot (`ops_speed_track`, transient).
- `server._attach_full_speeds`: evaluates the full speeds on every finalized
  shot.
