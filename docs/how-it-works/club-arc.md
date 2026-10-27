---
icon: lucide/circle-dashed
---

# Club delivery arc

How the kiosk gets **club path, attack angle, swing plane, swing direction and
low point** from one shot. It uses published sources first, and assumed swing
geometry, labelled "model geometry", until the 6 mm and 8 mm lenses give enough
frames to measure the arc. The code is `src/openflight/club_arc.py`.

## The inputs: this shot's own head positions

The camera's chained club delivery (`camera/club_delivery.py`) tracks club-head
features across short frame pairs around impact. For each tracked frame it
intersects the features' camera rays with the **IWR club range sphere** at that
frame's time. The median of the plausible features is that frame's head
position. Each position carries its time on the camera clock, and the delivery
also records:

- the contact time on the same clock (midway between the last frame with the
  ball at rest and the next);
- the horizontal offset;
- the resting ball's position.

Only positions **before contact** go into the fit, because TrackMan measures club
delivery from pre-impact motion only. The camera + OPS fallback has no depth, so
it gives no positions.

### Hosel-track fallback

When the feature/range intersection fails, `camera/head_track.py` now supplies
its two or three pre-impact hosel positions. It reconstructs transverse position
from normalized image offsets and walks depth back from contact using OPS speed.
These are experimental positions. A rejected or flagged direction withholds the
positions too, so the arc cannot bypass the direction gate.

The positions move from the camera origin into the radar-centred frame before
the arc fit. Their timestamps and contact share the camera clock; the target-line
rotation is applied once. The head track's original path and attack remain
selected when the competing arc value is also experimental.

For a prior-assisted fit, `arc.prior_share` splits each output's linearized
variance between position rows and the two prior rows. `prior_sensitivity` gives
the output change per degree of assumed plane and per 0.1 m of assumed radius.
A prior share of at least 90% is explicitly labelled model geometry at the
producer, with the percentage in its note; other outputs inherit experimental.
Published records can weaken these further through their input labels. A plane
near the prior is not an independent measurement of that golfer's swing plane.

These ranges condition on the reconstructed depths and the stated pixel noise.
They omit correlated reconstruction bias, so they are not accuracy claims.
`iwr6843_club_range_evidence` alone supplies range versus time, not 3D positions:
its angles are not stored in that object. It already supplies depth for camera
feature intersections; no independent 3D arc is fabricated from its scalar range.

## The model: a circle in a plane

The arc is a circle of radius `R` in a plane tilted `θ` from the horizon. The
plane's base line has bearing `ψ` from the target line. The head moves at a
constant angular speed `ω` through the arc's lowest point `L` at time `t_L`:

```
P(t) = L + R sin φ e1 + R (1 − cos φ) e2,    φ = ω (t − t_L)
e1 = base line (downrange),  e2 = cos θ · (toward the golfer) + sin θ · up
```

"Toward the golfer" is target-left for a right-handed golfer, which is the
default. A left-handed golfer must be set explicitly: in the club picker (kept per
profile), or by a connected simulator's `handed` setting, which wins. Each shot keeps
the hand it was detected with. The model has eight parameters, and every output is read
from the arc at the contact time.

Why a circle: high-speed measurements (2,000 fps) fit the head's path near
impact with a circle to about 1 mm RMS (Kraśna et al. 2019). The head's motion
from mid-downswing to mid-follow-through is planar to 0.3 cm RMS (Kwon et al.
2012).

## The labels

| Label | When |
|---|---|
| **calculated** | At least 3 pre-impact positions, and the shot's own points fix the arc with **no prior**. Each 1-sigma, data-only uncertainty must be within its limit: path and attack 0.5°, plane and direction 2°, each low-point component 25 mm, radius 0.10 m. The RMS residual must be within 3 noise floors, and impact can be at most 2 frame intervals past the last position. |
| **model geometry** | 2 positions, or a fit that fails any of those limits. A maximum a posteriori fit then constrains the radius and plane angle with the published per-club prior. Everything else (direction, low point, speed, timing) still comes from this shot. The prior and its source are recorded on the shot. |
| **not measured** | No head positions, or only one. Also: no contact time, a last position more than 10 ms before impact, or an unknown club (no prior) when the shot's points alone do not fix the arc. The reason is recorded and nothing is filled in. |

The position noise floor is 5 mm, about one pixel across the head at 2 m with
the stock lens at 640×400. It is an engineering assumption, to be replaced by
the measured scatter. The arc is never labelled "measured". A measured tangent
needs 4 to 6 frames from the new lenses.

## The published priors

Swing plane, from Kwon, Como, Singhal, Lee & Han (2012), "Assessment of
planarity of the golf swing based on the functional swing plane of the
clubhead and motion planes of the body points", *Sports Biomechanics*
11(2):127–148, doi:10.1080/14763141.2012.660799, Table III:

- The source: the clubhead's functional-swing-plane slope, measured on 14
  skilled golfers at 250 Hz from mid-downswing to mid-follow-through.
- **Driver 47.2 ± 2.3°, 5-iron 55.8 ± 1.5°, pitching wedge 59.1 ± 1.9°.**
- TrackMan's own guidance agrees for the driver: "A driver typically has a
  Swing Plane between 45–50 deg" (support article "Parameters | Swing Plane
  (Tee to Green)").

Radius, from Kraśna, Čoh, Prebil & Mackała (2019), "Comparative analysis of golf
clubhead motion at impact", *Facta Universitatis: Physical Education and Sport*
17(3):437–452, doi:10.22190/FUPES191205039K, Table 3:

- The source: the radius of a circle fitted to the clubhead path near impact,
  from 3 scratch golfers filmed at 2,000 fps.
- Mean ± SD over the trials, computed from the table: **driver 1.077 ± 0.044 m
  (8 trials), 6-iron 1.002 ± 0.014 m (5), pitching wedge 0.993 ± 0.039 m (5).**
- The near-impact radius is about 1 m, well under arm-plus-club length,
  because the swing's hub moves. Nesbit & McGinnis (2009), *J Sports Sci Med*
  8:235–246, found that its centre of curvature keeps moving through the
  downswing.

Other clubs are interpolated linearly in nominal loft between these anchors and
clamped outside them, never extrapolated. That interpolation is our choice, not
a published value. An unknown club has no prior.

## The outputs and their definitions

Each output follows TrackMan's public definition. TrackMan measures at the
head's geometric centre at impact.

| Output | TrackMan's public definition | Here | Sign |
|---|---|---|---|
| Club path | "The in-to-out or out-to-in movement of the club head's geometric center at the time of maximum compression" ([40 TrackMan parameters](https://www.trackman.com/blog/40-trackman-parameters), [club path](https://www.trackman.com/blog/club-path)) | Azimuth of the arc's tangent at impact | + right of the target line, regardless of dexterity. A right-hander's in-to-out is + |
| Attack angle | "The up or down movement of the club head's geometric center at the time of maximum compression" (same pages, [attack angle](https://www.trackman.com/blog/attack-angle)) | Elevation of that tangent | + up |
| Swing plane | "The vertical angle between the plane created by the club head's geometric center movement and the horizon" (40 TrackMan parameters) | θ | 0 to 90° |
| Swing direction | "The angle between the base of the plane created by the club head's geometric center movement and the target line" (40 TrackMan parameters; [what is swing direction](https://www.trackman.com/blog/what-is-swing-direction): negative is "over the top" for a right-hander) | ψ | + right |
| Low point distance | "Distance from the club head's geometric center to the lowest point on the swing arc at the time of maximum compression" (40 TrackMan parameters). A negative attack angle puts it after impact ([low point](https://www.trackman.com/blog/low-point)) | `L − P(impact)` along the target line | + after the ball |
| Low point height | The vertical distance from the head's position at impact to "where the swing would bottom out" (support article "Parameters \| Low Point Height") | Vertical component | + above the head at impact |
| Low point side | The same distance measured perpendicular to the target line (support article "Parameters \| Low Point Side (Tee to Green)") | Side component | + right |

TrackMan's support pages refused automated access, so their wording above comes
from search-result quotations and should be checked by hand.

**Per-club low point offset.** On a TM4 normalized export, 6 May 2026, the reported low
point distance is the arc's geometry plus a constant per club, fitted as:
+2.3 mm 7-iron (9 shots), −28.3 mm driver (8), +5.5 mm pitching wedge
(7); 1.5 mm RMS after the offset. `club_delivery_payload` adds it to
`low_point_distance_mm` and keeps the geometry (`geometry_value`) and the offset
(`offset_mm`), with a note. Other clubs have no fitted offset yet and report the
geometry alone, with the note "arc geometry only: no per-club offset fitted for
this club".

The fit also records the low point relative to the resting ball
(`arc.low_point_from_ball_mm`). It differs from TrackMan's figure by the head
point's offset from the ball.

**Frame.** Positions arrive in the unit-boresight frame. The one
horizontal offset turns them about the vertical before the fit, so path, swing
direction, low-point distance and low-point side are reported in
`target_line` (with an offset) or `unit_boresight` (without one). Attack, plane
and low-point height have no horizontal frame.

## On the shot: `Shot.club_delivery`

`club_delivery_payload` builds it, and the kiosk attaches it to every camera
shot (`server._attach_club_arc`). It goes to the UI in the shot payload. No UI
shows it yet.

```
{
  "schema": "openflight.club_delivery", "version": 1, "handedness": "RH",
  "metrics": {
    "club_path_deg":         {"value", "status", "source", "frame", "range", "reason"},
    "attack_angle_deg":      {...}, "swing_plane_deg": {...}, "swing_direction_deg": {...},
    "low_point_distance_mm": {...}, "low_point_height_mm": {...}, "low_point_side_mm": {...}
  },
  "selection_rule": "...",
  "cross_check": {"arc": {...}, "chained": {...}, "arc_minus_chained_deg": {...}},
  "arc": {... the full fit: residual, radius, head speed, prior, conditioning ...}
}
```

- `status` is one of `measured`, `calculated`, `model_geometry`, `estimated`,
  `experimental` or `not_measured`.
- `source` is one of:
  - `club_arc:shot_fit` (calculated);
  - `club_arc:published_prior` (model geometry);
  - `camera_iwr_chained` or `camera_ops_chained` (the measured chained value).
- `range` is the value ± 2 standard deviations. For a calculated value the
  deviation comes from the data alone; for model geometry it includes the
  prior.

**Selection rule for path and attack.** The arc's value is used when its label
ranks at least as high as the chained camera/IWR value's. The ranking is measured
> calculated > model geometry > estimated > experimental > not measured. Otherwise the chained value is
used. A chained value counts as measured when it exists and is not withheld,
rejected or an error. The arc is at best "calculated", so today the chained
value wins whenever it exists, and the arc fills path or attack only when the
chained value is missing. Both values are always recorded under `cross_check`.
For the collision model, `club_arc.selected_path_and_attack(payload)` returns
the two selected entries.

**Simulators (D31).** The resolver reads the selected path
from the published metric record. Experimental values never reach simulators. Whether a model-geometry club path should go to
simulators is an open decision for the owner (see below). It is the one switch
`sim.resolver.SIMULATORS_ACCEPT_MODEL_GEOMETRY` (default `False`); set to
`True`, the arc's model-geometry path goes out labelled `"model_geometry"`.

## Synthetic recovery

These figures come from 50 noisy draws per row. The truth is a right-handed 7-iron
arc: 57° plane (unless stated), 1.0 m radius, 38 m/s, 2 ms frames, with the low
point 2 ms after impact. Each cell is the median / 95th-percentile absolute
error.

| Sample | Label | Path ° | Attack ° | Plane ° | Low-point distance mm |
|---|---|---|---|---|---|
| 3 points, 1 mm noise | model geometry 50/50 | 0.48 / 0.98 | 0.36 / 0.98 | 0.02 / 0.02 | 7.5 / 20 |
| 4 points, 2 mm | model geometry 50/50 | 0.47 / 1.24 | 0.42 / 1.29 | 0.02 / 0.05 | 8.7 / 27 |
| 5 points, 5 mm (today's rig) | model geometry 50/50 | 0.75 / 2.65 | 0.71 / 2.44 | 0.08 / 0.19 | 15 / 51 |
| 4 points, 2 mm, golfer at 52°, 1.10 m (off the prior) | model geometry 50/50 | 0.46 / 1.38 | 1.17 / 2.25 | 5.0 / 5.1 | 36 / 58 |
| 6 points, 0.2 mm, golfer at 52° | calculated 50/50 | 0.10 / 0.34 | 0.09 / 0.26 | 0.53 / 1.42 | 2.7 / 7.9 |
| 3 points 12 ms apart, 0.1 mm | calculated 50/50 | 0.03 / 0.06 | 0.02 / 0.06 | 0.05 / 0.11 | 0.6 / 1.7 |

In the first three rows the truth matches the prior, so the plane errors there
are small by construction. The off-prior row shows the honest cost of model
geometry: the plane and low point carry the prior's bias. Path and attack stay
near 1°, because their direction comes from the shot's own points.

## Limits

- The head point is the median of tracked features, not TrackMan's geometric
  centre. Features are re-seeded for each frame pair, so the head point
  shifts slightly between frames, and the fit residual absorbs it.
- The model assumes a constant angular speed over the last few frames before
  contact, and uses pre-impact frames only.
- The prior spreads come from skilled golfers (Kwon: ±1.5–2.3°). Kraśna's three
  golfers spanned 37–60° with the driver, so a model-geometry range can be too
  narrow for an amateur.
- With the stock lens and 2 ms frames (a 5 mm floor, 5 pre-impact frames), every
  shot is model geometry. Calculated values need more frames or less noise:
  the new lenses and crop modes.
- The priors are not yet refitted to TrackMan 4 exports. Low point and swing
  plane have no accuracy goal until that data exists.
