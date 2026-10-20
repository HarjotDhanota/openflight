---
icon: lucide/git-merge
---

# Collision model (face, loft, spin)

*Part E, item E2. Added 12 October 2026. Code: `src/openflight/collision_model.py`;
wiring: `server._attach_collision_model`.*

OpenFlight's camera can't see the club face. These metrics are therefore
**calculated** from each shot's own measured inputs, using published physics
and two clean-room fits to the owner's own TrackMan 4 export:

- face angle and dynamic loft;
- spin loft, face to path and D-plane tilt;
- a calculated spin rate;
- the spin axis.

Gyro is **not measured**. Every number below was read from the cited paper
itself.

Owner decisions: D33 (published PING weighting now, refit to TM4 after the
pilot) and D38 (fitted physics, published sources first, labelled
"calculated (model: source)").

## Labels and rules

| Input label | Output status |
| --- | --- |
| all inputs `measured` or `calculated` | `calculated` |
| any input `model_geometry` (D39, e.g. E1's arc path) | `model_geometry` |
| any input missing, non-finite, or a table estimate | `not_measured`, with the reason |

- **Each shot uses its own inputs.** The model's constants are fixed; its
  inputs never are.
- **No filled-in values.** If a metric's inputs are missing, the metric is
  `not_measured` and names the missing input. An unknown club has no weight
  category, so its face and loft are not measured unless a measured spin lets
  the rigid-body relation build them.
- **Frames.** Every value built from a horizontal angle carries the frame of
  its inputs (`horizontal_frame`). When the inputs are in two frames, it is
  `mixed`, and the value is still computed and labelled.
- **Where it shows up.** `Shot` and the payload carry the flat values:
  - `face_angle_deg`
  - `dynamic_loft_deg`
  - `spin_loft_deg`
  - `face_to_path_deg`
  - `d_plane_tilt_deg`
  - `spin_rpm_collision`

  `Shot.collision_model["metrics"][name]` holds each value with its `status`,
  `source`, `unit`, `frame`, `reason` and `note`. `collision_model["inputs"]`
  holds every input with its label and source.

## Inputs

| Input | Taken from (first available) | Unit |
| --- | --- | --- |
| Club path | `displayed_club_path`: canonical `club_path_deg`, then the camera-fused path (unless rejected or withheld), then an **accepted** IWR path | deg, + right |
| Attack angle | `displayed_attack_angle`: canonical `club_angle_deg`, then the camera-fused attack (unless rejected or withheld). The IWR attack angle is only ever a candidate, so it is never used | deg, + up |
| Launch direction | `launch_angle_horizontal`, unless its source is `estimated` | deg, + right |
| Vertical launch | `launch_angle_vertical`, unless its source is `estimated` | deg, + up |
| Club speed | `club_speed_mph` (today the OPS243 radial reading, which runs 2–6 mph low until W6 and W9) | mph |
| Ball speed (full) | `ball_speed_full_mph`, `calculated`. The radial speed never stands in for it | mph |
| Spin rate | `spin_rpm` under its own label: an accepted measured spin (confidence ≥ `SPIN_CONFIDENCE_HIGH`), or a model's spin labelled as one | rpm |

When the kiosk has no accepted measured path or attack, E1's selected arc value
(`club_arc.selected_path_and_attack`) enters through `_collision_inputs` under
its own label: `calculated` for the shot's own fit, `model_geometry` for the
arc prior (D39, every shot on today's lens). Every output built on it inherits
the label, and the kiosk's face status then ends in `_model_geometry`. A
not-measured arc value is a missing input.

## 1. Face angle and dynamic loft

### With a measured spin: the rigid-body relation

When a ball strikes a rigid face, the friction that slides the ball along the
face also spins it. For a ball of mass m, radius r and moment of inertia I, the
tangential impulse gives m · v_t · r = I · ω (Penner 2001, Eq. (11); Cross &
Nathan 2007, section 4). So the ball's speed along the face is fixed by its
spin:

    ball speed · sin θ = c · spin,     c = I / (m r)

θ is the angle between the ball's launch direction b and the face normal n.
The normal lies in the plane of b and the club's direction v (the D plane), on
the far side of b from v. That fixes it: `n = cos θ · b + sin θ · u`, with u
the unit vector in that plane, normal to b, pointing away from v. Face angle
and dynamic loft are the azimuth and elevation of n (`rigid_body_face`).

**The constant.** c = 7.5005 mm, which is I = 0.352 · m r² for r = 21.335 mm
(a uniform sphere would be 0.4). It comes from a clean-room study of the
owner's TM4 export: the relation held for driver, 7-iron and wedge with a
relative spread of 2.5 × 10⁻⁴. With it, the code rebuilds TM4's face angle to
0.024° RMS and dynamic loft to 0.004° RMS on the 21 measured-spin shots
(`tests/test_rigid_body_face.py` checks this against
`session_logs/OpenFlight-Test.Normalized.csv`). One consequence: TM4's face
angle and loft are calculated from the ball, so they are not independent truth
for a camera face measurement.

**Inputs and label.** The full ball speed, launch direction and vertical
launch, the spin, and the club path and attack angle. The result is
`calculated`, or the weakest input's label (`model_geometry` on an arc path,
`estimated` on a weak launch), with source "rigid-body relation: …".

**Why only a measured spin.** The collision model also calculates a spin
(Penner, section 3) from the face and loft. Building face and loft from that
spin would be circular, so:

- a measured spin uses the rigid-body relation;
- any other spin, including the model's own, keeps the D-plane inversion
  below. The face and loft then carry the note "rigid-body relation not used:
  …" with the reason (no measured spin, a calculated spin, the circular case,
  or a missing full ball speed);
- `collision_model["face_method"]` says which one ran: `rigid_body` or
  `d_plane_inversion`.

The full ball speed is computed after the first run, so the model runs again
once it is known, whenever an accepted measured spin can use it. Today no
sensor measures spin, so every shot still takes the inversion.

### Without a measured spin: D-plane inversion

**Source:** P. Wood, E. Henrikson, C. Broadie (PING), "The Influence of Face
Angle and Club Path on the Resultant Launch Angle of a Golf Ball",
*Proceedings* **2**(6):249 (2018), doi:10.3390/proceedings2060249.

**Definitions.** The paper defines a launch ratio separately for each plane:

    w_h = (launch direction − club path) / (face angle − club path)
    w_v = (vertical launch − attack angle) / (dynamic loft − attack angle)

**Weights.** From the paper's Table 1, with the 95 % confidence interval and
the number of filtered shots:

| Category | Horizontal w_h | Vertical w_v |
| --- | --- | --- |
| Driver | 76 % ± 8 % (89) | 83 % ± 8 % (137) |
| 7-iron | 69 % ± 3 % (172) | 81 % ± 5 % (252) |
| 58° wedge | 61 % ± 7 % (28) | 72 % ± 6 % (36) |

The paper also gives a robot test: horizontal 63 % ± 4 % (15 shots) on a
7-iron. It is not used here.

**Method.** 157 right-handed golfers, Vicon at 720 fps for the club, Foresight
GC2 for the ball. Only impacts within 0.25 in of face centre were kept; for the
horizontal ratio, only shots with |face − path| ≥ 1.5°.

**Inversion.** Units are degrees:

    face angle   = path   + (launch direction − path)   / w_h
    dynamic loft = attack + (vertical launch − attack)  / w_v

The forward form, `launch = path + w (face − path)`, is `forward_launch`. The
tests forward-simulate a delivery and recover the face and loft exactly.

**Interpolating between categories.** Weights are linear in the club's
*nominal* loft between three anchors, and clamped outside them:

| Anchor | Nominal loft | Where the loft comes from |
| --- | --- | --- |
| Driver | 10.5° | Fork's `clubs.physics` table. The paper says only "a club loft … appropriate for their swing speed" |
| 7-iron | 34.0° | Fork's `clubs.physics` table. Not stated in the paper |
| 58° wedge | 58.0° | Stated in the paper |

Why interpolate by loft:

- Wood et al. 2018 (Fig. 3c, d and Conclusions) show the ratio falling as the
  obliqueness of impact rises: "higher numbers when the dynamic loft is lower".
- Henrikson, Wood, Broadie & Nuttall, "The Role of Friction and Tangential
  Compliance on the Resultant Launch Angle of a Golf Ball", *Proceedings*
  **49**(1):27 (2020), doi:10.3390/proceedings2020049027, use a Hertzian
  contact model with Coulomb friction. Their Fig. 6 shows "the launch ratio
  monotonically decreases for increasing loft" for dry contact (μ > 0.3).

The straight lines between anchors are **our** choice. Neither paper tabulates
the curve's shape. The x-axis is nominal loft rather than the shot's own spin
loft because the anchors' spin lofts are not published. E8 refits both the
weights and the curve to the owner's TM4 exports.

**Status and source.** Labelled `calculated`, with source "D-plane inversion,
Wood et al. 2018 weights" and the note above. It is `model_geometry` if the path
or attack is.

## 2. Spin loft, face to path, D-plane tilt

These come from the D37 definitions in `metric_definitions`:

- `spin_loft_deg`: the 3D angle between the club direction and the face normal;
- `face_to_path_deg`: face minus path;
- `d_plane_tilt_deg`: the tilt of the plane spanned by the club direction and
  the face normal.

All three use the face and loft from section 1, whichever way they were
built, and the shot's own path and attack. When face equals path (the 2D limit), the tilt is 0 and the spin loft
is loft − attack.

## 3. Calculated spin rate: Penner 2001

**Source:** A. R. Penner, "The physics of golf: The optimum loft of a driver",
*Am. J. Phys.* **69**, 563–568 (2001), doi:10.1119/1.1344164.

**Model.** The clubhead is a free flat plate of mass M. The ball is a uniform
solid sphere, `I = (2/5) m r²`, and it is rolling when it leaves the face. The
equations, in SI units with spin in rad/s:

    (5)   e     = 0.86 − 0.0029 · v cos θ                 (v in m/s)
    (7)   v_n   = (1 + e) v cos θ / (1 + m/M)
    (8)   v_p   = v sin θ / (1 + m/M + m r²/I)            (= v sin θ / (3.5 + m/M))
    (10)  ψ     = θ − atan(v_p / v_n)                     (launch angle from the club direction)
    (11)  ω     = m v_p r / I                             (= v_p / (0.4 r))

**θ.** Penner's club moves horizontally, so his θ is the angle between the
club's velocity and the face normal. In 3D that angle is the **spin loft**: the
collision takes place in the D plane.

**Constants, as Penner states them:**

| Constant | Value |
| --- | --- |
| Ball mass m | 45.9 g |
| Ball diameter | 4.27 cm |
| Head mass M | 200 g |

Penner gives M only for a driver, and no per-club masses are published there,
so 200 g is used for every club. Spin goes as `1/(3.5 + m/M)`, so moving M from
200 g to 300 g raises it by about 2 %.

The rolling spin (11) does not depend on e. Club speed is converted at
1 mph = 0.44704 m/s.

**Friction limit.** The ball can roll only if Coulomb friction can supply the
tangential impulse: μ ≥ v_p / v_n. Henrikson et al. 2020 (Sec. 2.2) measured
dry ball-on-face friction at 0.40 for a urethane cover and 0.35 for a surlyn
cover. If a spin loft needs μ above 0.35, which happens beyond about 62°, the
shot is outside Penner's model and its spin is `not_measured`.

**Checked against the paper.** Penner's Fig. 2 (v = 45 m/s, M = 200 g) prints
no table. It was digitised at 400 dpi and compared with the code:

| Loft | Launch speed, fig / code | Launch angle, fig / code | Spin, fig / code |
| --- | --- | --- | --- |
| 6° | 62.95 / 62.99 m/s | 4.90 / 4.85° | 1415 / 1410 rpm |
| 10° | 62.41 / 62.45 | 8.16 / 8.08 | 2335 / 2343 |
| 15° | 61.37 / 61.38 | 12.11 / 12.08 | 3474 / 3492 |
| 25° | 58.05 / 58.00 | 20.02 / 19.96 | 5628 / 5702 |
| 29° | 56.24 / 56.19 | 23.07 / 23.02 | 6441 / 6541 |

Launch speed and angle agree to within 0.1. Spin agrees to within 1.6 %, which
is within the reading tolerance of the printed curve.

**Known bias.** A rolling, rigid-sphere ball under-predicts spin:

- R. Cross & P. Dewhurst, "Launch speed, angle and spin in golf",
  *Eur. J. Phys.* **39**, 065003 (2018), doi:10.1088/1361-6404/aadda8. Their
  abstract states that the ball grips rather than rolls, "with the result that
  the outgoing ball spin is generally larger than previously estimated". Only
  the abstract was available; the full text was not read.
- Lieberman's barrier measurements, as quoted in Penner's 2003 review
  (*Rep. Prog. Phys.* **66**, 131), are about 10–40 % above the rolling value at a
  rigid barrier.

The refit in E8 sets the size of this bias.

**Status and source.** Labelled `calculated`, source "Penner 2001 collision
model (Am. J. Phys. 69, 563)". The value is always recorded in
`spin_rpm_collision`. When there is no accepted measured spin (confidence ≥
`SPIN_CONFIDENCE_HIGH`), it becomes the shot's `spin_rpm` under its own status:

- `calculated`: `spin_source = "calculated"`, `spin_method = "Penner 2001;
  club inputs measured"` (or `calculated` when an input is);
- `model_geometry` (an input is model geometry, D39): `spin_source =
  "model_geometry"`, `spin_method = "Penner 2001; club inputs model geometry
  (D39)"`. It feeds the flight model (D38) under that label; simulators do not
  get it (D31).

The spin it displaces is kept in `spin_rpm_measured` (its method in
`spin_method_measured`), and is given back when a later run of the model has no
spin. A model's value is never labelled measured.

**The `--calculated-spin` flag.** Its formula, `170 · v · sin(LA)^1.2` in
`spin_estimate.py`, is empirical, not a published collision model. When the
flag is on, its value is recorded in `spin_rpm_calculated_empirical`. If
Penner's spin is calculated for the shot, Penner's is the one used; over a
model-geometry Penner spin, the formula's calculated value is used
(`spin_method` names it).

## 4. Spin axis

**A measured spin axis always wins.** The model never replaces an axis whose
source starts with "measured".

**Irons and wedges** (2-iron to lob wedge) take a clean-room fit to the
owner's TM4 irons, which reproduces them to 0.014° in one session:

    tan(spin axis) = tan(D-plane tilt) · (0.81385 + 0.00342 · dynamic loft)

It is labelled `calculated` (or its inputs' weakest label), with
`spin_axis_source = "calculated (model: D-plane tilt x TM4 iron factor, …)"`.
Irons take no gear term.

**Drivers, woods and hybrids, without an impact location** (the only case
today): the spin axis is the D-plane tilt. TrackMan's public definition: "D
Plane Tilt is the expected spin axis disregarding any gear effect". The note
reads "no impact location: gear effect not applied". In the owner's export the
driver axes are not locked to the tilt; the residual follows the toe-heel
impact offset, which is the gear effect.

**Sign.** Positive means the ball curves right. On a normal strike it has the
sign of face to path.

**A weak horizontal launch.** When the horizontal launch the model uses is a
low-coherence IWR value (source `radar_low_coherence`), or its confidence is below
`SPIN_AXIS_MIN_CONFIDENCE` (0.6) or not given, it enters as an `estimated` input
with that reason. Every output built on it is then `estimated`, with the reason:
face angle, face to path, spin loft, D-plane tilt, the Penner spin and the spin
axis (dynamic loft does not use it and keeps its label). The axis used to be
withheld below 0.6; it is now shown, labelled `estimated`
(`spin_axis_source = "estimated (D-plane tilt, ...; <reason>)"`), and the spin
becomes the shot's under `spin_source = "estimated"` when no measured spin was
accepted. Through the weakest-label rule the flight is then `estimated` too.
Simulators get neither (D31). The label order is measured > calculated >
model_geometry > estimated > not_measured.

**Club speed.** Penner's club speed is the OPS243's club speed, which Tier 1 of
the Part E plan treats as measured. It is a line-of-sight (radial) reading, not
the head's full speed (E3 cannot project it yet), and the spin scales with it.

**On the shot.** When calculated, it becomes `spin_axis_deg` with its frame and
`spin_axis_source = "calculated (D-plane tilt, Wood et al. 2018 weights)"` (an
iron: `"calculated (model: D-plane tilt x TM4 iron factor, …)"`; a rigid-body
face says "rigid-body face" in place of the weights), or
`"calculated (D-plane + gear effect, Cross & Nathan 2007)"` once a measured
impact location supplies a gear sidespin. The flight (E4) and the simulators treat any source starting
with "calculated" as a calculated value.

When an input is model geometry (D39), the axis still becomes `spin_axis_deg`,
with a source that starts with "model geometry" and names those inputs, e.g.
`"model geometry (D-plane tilt, Wood et al. 2018 weights; club path from the arc
prior)"`. The flight takes it under that label; simulators do not get it.

**Gear effect (for E5).** Source: R. Cross & A. M. Nathan, "Experimental study
of the gear effect in ball collisions", *Am. J. Phys.* **75**, 658–664 (2007),
doi:10.1119/1.2713788. Eq. (6), for normal incidence:

    ω = −(1 + e) d b v_y1 / (1.4 r [b² + I_B (1/m + 1/M)])     (rad/s)

| Symbol | Meaning |
| --- | --- |
| b | impact offset along the face |
| d | depth of the head's centre of mass behind the face |
| I_B | head's moment of inertia about its centre of mass |
| M | head mass |

`cross_nathan_gear_spin_rad_s` reproduces the paper's worked value of
−7.6 rad/s (210 g block, b = 50 mm, e = 0.83). The gear sidespin g (rpm, + =
curves right) tilts the axis:

    axis = atan2(S sin t + g, S cos t)

where S is the calculated spin and t is the D-plane tilt (`spin_axis(...,
gear_sidespin_rpm=g)`).

The hook is wired but inactive: `evaluate` takes `impact_offset_mm` and
`gear_sidespin_rpm`, and applies the gear term only to a driver, wood or hybrid
with a **measured** impact offset. A measured offset without a gear sidespin
gives the note "measured impact location but no head model: gear effect not
applied". Impact location and the head's d, I_B and M (from an official
specification) are a later feature. The paper's Appendix covers oblique impacts.

## 5. Gyro

Penner's collision is planar: the spin vector is normal to the D plane, so gyro
is zero by construction. That is not a result, so gyro is `not_measured`, with
that reason.

## Simulators (D31)

Simulators get the calculated spin and spin axis under the provenance
`"calculated"`, never `"measured"`. Back spin and side spin take the weaker of
the two labels. A fusion shot with no measured or calculated spin or axis is
still not sent.

A spin, spin axis or carry built on model geometry (D39), and the arc's
model-geometry club path, are not sent. A fusion shot that has its spin or axis
only as model geometry is refused with the reason "… not measured (model
geometry only, not sent to simulators (D31): spin, spin axis)". Whether
simulators get model-geometry values is the owner's open decision: it is the
one switch `sim.resolver.SIMULATORS_ACCEPT_MODEL_GEOMETRY` (default `False`).
Set to `True`, each of those values goes out labelled `"model_geometry"`, and
back spin and side spin take the weaker label.

## What this replaced

- **The face estimate.** Was `face = (launch − 0.2 · path) / 0.8`, one fixed
  weight for every club. It is replaced by the PING weights per category, and
  by the rigid-body relation when the spin is measured. The
  `experimental_face_angle_*` fields mirror the new value so the kiosk tile
  keeps working until the UI pass.
- **The spin-axis proxy.** Was `spin axis = launch direction − path`. It is
  replaced by the D-plane tilt, and for irons by the iron fit on it.
