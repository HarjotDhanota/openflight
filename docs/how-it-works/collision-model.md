---
icon: lucide/git-merge
---

# Collision model (face, loft, spin)

*Part E, item E2. Added 12 October 2026. Code: `src/openflight/collision_model.py`;
wiring: `server._attach_collision_model`.*

OpenFlight's camera can't see the club face. These metrics are therefore
**calculated** from each shot's own measured inputs, using published physics
and two clean-room fits to a TrackMan 4 normalized export, 6 May 2026:

- face angle and dynamic loft;
- spin loft, face to path and D-plane tilt;
- a calculated spin rate;
- the spin axis;
- the spin index and smash index.

A forward collision model supplies the spin, the wood and hybrid spin axis, and
the face and loft when the spin is not measured. Its forms are published
physics; its ball, friction and restitution are published values, its head
properties are typical values (assumed), and its gains were fitted by the
maintainer.

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
  holds every input it used with its label and source (the spin and full ball
  speed only when the rigid-body relation used them).

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

## The forward model

*Added 20 October 2026; spin, off-centre loss, gear effect and the indices
rebuilt on rigid-ball impulse physics the same day. Code:
`src/openflight/collision_forward.py`.*

One forward model takes a club delivery (club speed V, attack angle, club path,
face angle, dynamic loft) and an impact location to the ball's speed, launch
angle, launch direction, spin rate and spin axis. Its structure is published
physics, per club category (driver and woods, irons and hybrids, wedges).

**Which constants are what.**

| Kind | Constants |
| --- | --- |
| Fixed, published | Ball m = 45.9 g, r = 21.35 mm (Penner 2001); κ = 0.4, I = κ m r² (uniform solid sphere); face friction μ = 0.40 (Henrikson et al. 2020, Sec. 2.2: urethane on a dry grooved face); restitution e = 0.86 − 0.0029 v_n for irons, hybrids and wedges (Penner 2001, Eq. (5)) and e = 0.830 − 0.0029 (v_n − 48.77 m/s) for drivers and woods (the USGA/R&A limit of 0.830 at its 48.77 m/s test speed, with Penner's slope), v_n the normal approach speed, held within 0.5–0.95 |
| Assumed (typical heads) | Head mass, centre-of-mass depth d and moments of inertia, table below |
| Fitted by the maintainer | The launch (K, s₁, s₂), centre speed S and centre axis (a, b), and the spin gain g and loss scales c_x, c_y: to a dense grid of deliveries (below). The gear gains g_x, g_y: to 38 clean export shots, each capped at 1.5 × its rigid-body value. The per-club index constants: to launch-monitor data (section 6) |

The grid: club speed 50–130 mph, attack −8…+6°, path and face ±8°, dynamic
loft 8–55°, impact offset ±25 mm (toe +) and height ±15 mm (up +); driver,
3-wood, 3-hybrid, irons 4–9, pitching and sand wedge; 264,000 deliveries, 20 %
held out for the launch residuals below.

**Head properties (assumptions, not from a paper).** Only the scaling within a
category depends on them; each category's gains are fitted.

| Category | Head mass M | CG depth d | I_z (vertical axis: toe-heel) | I_x (horizontal axis: high-low) |
| --- | --- | --- | --- | --- |
| Driver, woods | 200 g (Penner 2001's driver); 3-, 5-, 7-wood 212, 218, 224 g | 35 mm | 4500 g·cm² | 2800 g·cm² |
| Irons, hybrids | 2-iron 233 g to 9-iron 282 g, 7 g per club; hybrids 236–257 g | 6 mm | 2500 g·cm² | 700 g·cm² |
| Wedges | PW 289, GW 296, SW 303, LW 310 g | 5 mm | 2600 g·cm² | 700 g·cm² |

**Structure.**

1. **D plane** (Jorgensen, *The Physics of Golf*, 2nd ed., 1999). The ball
   leaves in the plane of the club's direction and the face normal, between
   them, at an angle δ back from the face normal. SL is the spin loft.
2. **Launch: rolling, then sliding** (Penner 2001, Eqs. (8)–(10) and Sec. II).
   While the ball rolls up the face, tan δ grows in proportion to tan SL; once
   rolling needs more friction than the face has, it slides and tan δ grows more
   slowly. A p-norm minimum (p = 24) joins the two lines:

        tan δ = K(L) · min_p(tan SL, s₁ + (s₂ − s₁)(tan SL − 1)/1.25)

3. **Ball speed: the normal impulse** (Penner 2001, Eq. (7)):
   `ball speed = V · S(L) · cos SL / cos δ`. An off-centre strike meets a smaller
   effective head mass (Cross & Nathan 2007; Penner 2001), with the centre of
   mass at the face centre, x the toe offset and y the height:

        f = (1 + m/M) / (1 + m/M + c_x m x² / I_z + c_y m y² / I_x)

   The loss grows with the square of the offset, the same on toe and heel.
4. **Spin: a rigid ball leaves gripping or still sliding** (Penner 2001,
   Eqs. (7)–(11); R. Cross, "Grip-slip behavior of a bouncing ball",
   *Am. J. Phys.* **70**, 1093–1102 (2002), doi:10.1119/1.1507792). The
   tangential impulse either brings the ball to rolling (grip) or runs out of
   friction first (slip); the spin is the smaller:

        v_n  = (1 + e) V cos SL / (1 + m/M)
        grip = g V sin SL / (κ r K),   K = 1 + 1/κ + m/M + m d² / I_x
        slip = μ v_n / (κ r)
        spin = min(grip, slip)

   `m d²/I_x` is the head's rotation about its centre of mass (Penner 2001); g = 1
   is Penner's rolling ball, and g − 1 plays the part of Cross's tangential
   restitution. The spin depends on V sin SL and V cos SL only: it goes to zero
   with the spin loft (no floor), rises with it, and turns from grip to slip at
   61–68° of spin loft for typical club speeds, above every shot in the exports;
   past the corner it falls with cos SL.
5. **Spin axis.** The D-plane tilt T scaled toward zero:
   `tan(axis) = tan(T) · (a(L) + b · launch angle)`.
6. **Gear effect** (Cross & Nathan 2007, Eq. (6), linear in the offset). From the
   same impulse algebra as the spin, with v_n the ball's normal speed (the centre
   ball speed times cos δ):

        side = −hand · g_x · m d x v_n / (I_z κ r K_z),   K_z = K with I_z
        back = −g_y · m d y v_n / (I_x κ r K)

   A toe strike draws a right-hander and fades a left-hander; a high strike
   takes off backspin. Without an impact location there is no gear effect, and
   the strike is labelled as at the face centre.

L is the club's nominal loft (`clubs.physics`); a hybrid launches like an iron
11.5° weaker-lofted. A constant that varies with L is stored as its values at
two anchor lofts, linear between and beyond them.

**Fitted gains.**

| Category | Spin g | Loss c_x | Loss c_y | Gear g_x | Gear g_y |
| --- | --- | --- | --- | --- | --- |
| Driver, woods | 0.994 | 1.113 | 2.187 | 0.586 | 0.645 |
| Irons, hybrids | 1.115 | 0.220 | 0.174 | 0.631 | 0.828 |
| Wedges | 1.241 | 0.227 | 0.172 | 0.773 | 1.478 |

The gear gains come from the clean shots of two export sessions (6 May 2026,
and a second, public TM4 session): 6 drives, 15 six- and seven-irons and 17 pitching
wedges. Left out: two drives whose spin the export marks as estimated, two thin
mishits (launch under 15°), and 17 shots with an attack angle of −2° or
shallower or a strike above +8 mm (possible fliers or heavy strikes, section 3).
Each category's two gains were fitted to its spin and spin axis together, within
0–1.5.

**Launch residuals** (hold-out RMS; "typical" is spin loft 8–28° driver/wood,
12–42° iron/hybrid, 22–58° wedge), against the models they replace (Penner 2001
speed, the published launch ratios, the D-plane tilt or iron fit for the axis):

| Category | Model | Centre ball speed | Launch | Direction | Centre spin axis |
| --- | --- | --- | --- | --- | --- |
| Driver/wood | fitted, all | 0.09 % | 0.05° | 0.01° | 1.1° |
| | fitted, typical | 0.09 % | 0.01° | 0.004° | 1.2° |
| | replaced | 2.9 % | 0.94° | 0.47° | 6.6° |
| Iron/hybrid | fitted, all | 0.07 % | 0.09° | 0.02° | 0.6° |
| | fitted, typical | 0.07 % | 0.03° | 0.007° | 0.5° |
| | replaced | 4.2 % | 0.74° | 0.35° | 2.5° |
| Wedge | fitted, all | 0.04 % | 0.07° | 0.01° | 0.5° |
| | fitted, typical | 0.04 % | 0.07° | 0.01° | 0.14° |
| | replaced | 5.6 % | 0.93° | 0.70° | 1.1° |

**Spin and loss on the grid** (driver, 7-iron, pitching wedge; relative RMS):

| Club | Spin, all | Spin, typical | Spin, SL ≤ 45° | Off-centre loss |
| --- | --- | --- | --- | --- |
| Driver | 5.5 % | 2.1 % | 3.6 % | 1.17 % |
| 7-iron | 15.0 % | 6.7 % | 7.4 % | 0.37 % |
| Pitching wedge | 13.1 % | 10.3 % | 6.0 % | 0.36 % |

The grid's spin turns over at 47–53° of spin loft. A rigid ball with the
published 0.40 friction turns at 61–68°; a friction low enough to match would
be below any measured ball-on-face value. Most of the iron and wedge residual
lies beyond 45° of spin loft, where no export shot is.

**On the 6 May 2026 export session** (23 shots with a delivery;
`tests/test_collision_owner_export.py`), with each shot's recorded impact
location. Its clean shots are among the 38 the gear gains saw; nothing else was
fitted to it:

| Model | Ball speed | Launch | Direction | Spin | Spin axis |
| --- | --- | --- | --- | --- | --- |
| this model | 3.1 % | 0.31° | 0.06° | 8.2 % | 1.2° |
| the earlier fitted model | 3.4 % | 0.31° | 0.06° | 10.2 % | 2.6° |
| Penner 2001 and the D-plane tilt | 7.1 % | 0.44° | 0.41° | 26.9 % | 13.6° |

The iron axis stays the clean-room iron fit (section 4): on the export's irons
it is 0.13–0.18° RMS; the forward model's own axis gives 0.75° on the 7-irons.
The forward model's ball speed and launch are recorded on the shot
(`collision_model["forward"]`) beside the measured ones; the kiosk uses its
spin and axis.

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
6 May 2026 TM4 export: the relation held for driver, 7-iron and wedge with a
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
- any other spin, including the model's own, keeps the launch-model inversion
  below. The face and loft then carry the note "rigid-body relation not used:
  …" with the reason (no measured spin, a calculated spin, the circular case,
  or a missing full ball speed);
- `collision_model["face_method"]` says which one ran: `rigid_body` or
  `d_plane_inversion`.

The full ball speed is computed after the first run, so the model runs again
once it is known, whenever an accepted measured spin can use it. Today no
sensor measures spin, so every shot still takes the inversion.

### Without a measured spin: inverting the fitted launch model

The forward model's launch relation is solved for δ given the measured launch
and the club's direction (ψ is known, so δ = δ(ψ + δ) by fixed-point
iteration). The face normal then lies in the D plane beyond the launch from
the club, at δ from it, the same construction as the rigid-body face with δ in
place of θ (`collision_forward.face_and_loft_from_launch`). It needs all four
angles: face and loft each take the weakest label of the path, attack and both
launch angles. Source "D-plane inversion of the fitted launch model".

On the fitting hold-out (|face − path| > 1°, typical spin lofts) it recovers
face to 0.005°, 0.011° and 0.021° RMS (driver/wood, iron/hybrid, wedge) and
loft to 0.012°, 0.040° and 0.085°; the published ratios below give 0.80°,
0.61° and 1.01° for face and 0.60°, 0.59° and 1.20° for loft. Refitting the
ratios per category (constant or linear in launch minus attack) reached only
0.11–0.33° for face and 0.25–1.27° for loft, so the inversion replaced them.
On the 6 May 2026 export it gives face 0.07° and loft 0.41° RMS (the ratios: 0.57°
and 0.54°).

**Partial inputs.** Without the launch direction or path, the loft alone comes
from the vertical plane (the inversion with face = path), noted. Without the
vertical launch or attack, the face alone comes from the published horizontal
ratio below, source "D-plane horizontal launch ratio, Wood et al. 2018
weights", noted.

### The published launch ratios (the face fallback)

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
weights and the curve to TM4 exports.

**Status and source.** Used only for the face when the vertical launch or
attack is missing; labelled `calculated` (or the weakest input's label).

## 2. Spin loft, face to path, D-plane tilt

These come from the D37 definitions in `metric_definitions`:

- `spin_loft_deg`: the 3D angle between the club direction and the face normal;
- `face_to_path_deg`: face minus path;
- `d_plane_tilt_deg`: the tilt of the plane spanned by the club direction and
  the face normal.

All three use the face and loft from section 1, whichever way they were
built, and the shot's own path and attack. When face equals path (the 2D limit), the tilt is 0 and the spin loft
is loft − attack.

## 3. Calculated spin rate

The calculated spin is the forward model's impulse spin (above), at the face
centre unless a measured impact location is given, with the note "no impact
location: centre-face strike assumed, no gear effect". Source "impulse
collision model (Penner 2001 normal impulse; rolling or sliding, Cross 2002;
gains fitted by the maintainer)"; `spin_method = "impulse collision model; club
inputs measured"` (or `calculated`, `model geometry (D39)`, `an input
estimated`). An older record's "fitted collision model" or "Penner 2001" spin is
still recognised as the model's own, so it never feeds face and loft.

### Contact quality: a possible flier or heavy strike

The impulse spin assumes a clean, dry strike. Grass or water between face and
ball, or a heavy strike, can leave a shot with far less spin than its delivery
predicts; a shot that looks like one gets a low-confidence note on the
calculated spin, and **its value is not changed**:

- an iron or wedge (2-iron to lob wedge) with an attack angle of −2° or
  shallower;
- a measured strike more than 8 mm above the face centre;
- an iron or wedge shot whose camera club delivery gave no low point (E1's
  arc). Without camera club data there is no low point to miss, and no note.

The note reads "low confidence: possible flier or heavy contact (…)" with the
reasons, on `collision_model["metrics"]["spin_rate"]` and on the catalogue's
spin record when the model's spin is the shot's. On the second export session
these shots were 40–60 % below the clean-contact spin at the same smash.

### Penner 2001, the published baseline

Kept as functions (`penner_impact`, `calculated_spin`) and checked against the
paper; the fitted model replaced it for the shot's spin.

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

**Status and source (the shot's spin).** Labelled `calculated`, source the
impulse model. The value is always recorded in `spin_rpm_collision`. When there is no accepted measured spin (confidence ≥
`SPIN_CONFIDENCE_HIGH`), it becomes the shot's `spin_rpm` under its own status:

- `calculated`: `spin_source = "calculated"`, `spin_method = "impulse collision
  model; club inputs measured"` (or `calculated` when an input is);
- `model_geometry` (an input is model geometry, D39): `spin_source =
  "model_geometry"`, `spin_method = "impulse collision model; club inputs model
  geometry (D39)"`. It feeds the flight model (D38) under that label; simulators do not
  get it (D31).

The spin it displaces is kept in `spin_rpm_measured` (its method in
`spin_method_measured`), and is given back when a later run of the model has no
spin. A model's value is never labelled measured.

**The `--calculated-spin` flag.** Its formula, `170 · v · sin(LA)^1.2` in
`spin_estimate.py`, is empirical, not a published collision model. When the
flag is on, its value is recorded in `spin_rpm_calculated_empirical`. If
the collision model's spin is calculated for the shot, that is the one used;
over a model-geometry model spin, the formula's calculated value is used
(`spin_method` names it).

## 4. Spin axis

**A measured spin axis always wins.** The model never replaces an axis whose
source starts with "measured".

**Irons and wedges** (2-iron to lob wedge) take a clean-room fit to the
irons of a TM4 normalized export, 6 May 2026, which reproduces them to 0.014° in one session:

    tan(spin axis) = tan(D-plane tilt) · (0.81385 + 0.00342 · dynamic loft)

It is labelled `calculated` (or its inputs' weakest label), with
`spin_axis_source = "calculated (model: D-plane tilt x TM4 iron factor, …)"`.

**Drivers, woods and hybrids:** the fitted forward model's centre-strike axis,
source "fitted collision model: D-plane tilt, centre-face strike".

**Impact location.** With a **measured** impact offset or height
(`impact_offset_mm`, + toe; `impact_height_mm`, + up; E5 builds them), the
forward model's gear back- and sidespin are added to the centre spin at the
centre axis, for every club (an iron's is smaller, its centre of mass shallower): the source then ends "plus
Cross & Nathan 2007 gear effect". `evaluate` takes the golfer's handedness
(right-handed unless "LH"): a toe strike draws a right-hander and fades a
left-hander. Without one, the note reads "no impact location: centre-face
strike assumed, no gear effect". An impact location that is not measured is
not applied.

**Sign.** Positive means the ball curves right. On a normal strike it has the
sign of face to path.

**A weak horizontal launch.** When the horizontal launch the model uses is a
low-coherence IWR value (source `radar_low_coherence`), or its confidence is below
`SPIN_AXIS_MIN_CONFIDENCE` (0.6) or not given, it enters as an `estimated` input
with that reason. Every output built on it is then `estimated`, with the reason:
face angle, dynamic loft (the inversion uses all four angles), face to path,
spin loft, D-plane tilt, the model's spin and the spin axis. The axis used to be
withheld below 0.6; it is now shown, labelled `estimated`
(`spin_axis_source = "estimated (D-plane tilt, ...; <reason>)"`), and the spin
becomes the shot's under `spin_source = "estimated"` when no measured spin was
accepted. Through the weakest-label rule the flight is then `estimated` too.
Simulators get neither (D31). The label order is measured > calculated >
model_geometry > estimated > not_measured.

**Club speed.** The model's club speed is the OPS243's club speed, which Tier 1 of
the Part E plan treats as measured. It is a line-of-sight (radial) reading, not
the head's full speed (E3 cannot project it yet), and the spin scales with it.

**On the shot.** When calculated, it becomes `spin_axis_deg` with its frame and
`spin_axis_source = "calculated (fitted collision model, centre-face strike, fitted launch-model inversion)"` (an
iron: `"calculated (model: D-plane tilt x TM4 iron factor, …)"`; a rigid-body
face says "rigid-body face" in place of the inversion), or `"calculated (fitted collision model + gear effect, Cross &
Nathan 2007)"` with a measured impact location. The flight (E4) and the simulators treat any source starting
with "calculated" as a calculated value.

When an input is model geometry (D39), the axis still becomes `spin_axis_deg`,
with a source that starts with "model geometry" and names those inputs, e.g.
`"model geometry (fitted collision model, centre-face strike, fitted
launch-model inversion; club path from the arc prior)"`. The flight takes it under that label; simulators do not get it.

**Cross & Nathan's own gear formula (reference).** Source: R. Cross & A. M. Nathan, "Experimental study
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

The forward model's gear effect is this formula's small-offset, linear form
with the typical heads above and the impulse algebra of the spin (κ r K in
place of 1.4 r [...]), times the fitted gains g_x and g_y. The paper's Appendix
covers oblique impacts.

## 5. Gyro

Penner's collision is planar: the spin vector is normal to the D plane, so gyro
is zero by construction. That is not a result, so gyro is `not_measured`, with
that reason.

## 6. Spin index and smash index

The shot against what its delivery should give, in percent, calculated metrics
in the Club panel after spin loft:

- **Spin index** = measured spin / expected spin × 100. Expected spin =
  `V · k · sin(spin loft)` (Penner 2001, Eq. (11): the rolling spin goes as
  V sin θ). k is fitted by the maintainer to launch-monitor data per club where
  an export has the club: driver 109.50, 7-iron 150.46, pitching wedge 165.13
  rpm per mph; other
  clubs take their category's k(L). It needs a **measured** spin: a calculated
  spin is not measured against itself, so on today's kiosk the spin index is
  `not_measured`, with that reason.
- **Smash index** = ball speed (full) / expected ball speed × 100. Expected
  ball speed = `V · (a · cos(spin loft) + c)`, fitted by the maintainer to
  launch-monitor data per club: driver a = 1.5059, c = 0.0072; 7-iron 1.4956,
  0.0321; pitching wedge 1.4682, 0.0716. Other clubs take the published normal
  speed and rolling (or Coulomb-capped) tangential speed, Penner 2001 Eqs. (5),
  (7), (8) with the heads above, times a category gain fitted by the maintainer
  (driver and woods 0.997, irons and hybrids 1.022, wedges 1.022). It needs the
  full ball speed, the club speed and the spin loft; the model runs again once
  the full ball speed is known.

Each takes its inputs' weakest label. A club without its own fitted constants
carries the note "no fitted index for this club: expected value from its
category". On the 6 May 2026 export, to which the per-club constants were fitted,
the indices follow the exported ones to 0.45 (spin) and 0.05 (smash) index
points RMS. Each club's shots span only 3–6° of spin loft there, so a and c are
fitted values for that club's usual deliveries, not a law over all spin lofts.

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
  weight for every club. It was replaced by the PING weights per category, then
  (20 October 2026) by the fitted launch model's inversion, and by the
  rigid-body relation when the spin is measured.
- **The spin.** Penner 2001's rolling spin, replaced by the fitted forward
  model, then (also 20 October 2026) by the impulse model: the earlier form had
  a floor at small spin loft and a roll-off from about 47°, which a rigid ball
  with the published face friction does not have; its smooth heel-biased loss
  and saturating gear effect gave way to the effective mass and the linear gear
  effect. The
  `experimental_face_angle_*` fields mirror the new value so the kiosk tile
  keeps working until the UI pass.
- **The spin-axis proxy.** Was `spin axis = launch direction − path`. It is
  replaced by the D-plane tilt, and for irons by the iron fit on it; drivers,
  woods and hybrids then by the fitted model's centre axis.
