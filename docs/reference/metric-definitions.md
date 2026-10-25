---
icon: lucide/ruler
---

# Metric definitions

OpenFlight uses TrackMan's **public** definitions for smash factor, face to
path, spin loft, D-plane tilt and curve. Each formula below was
derived from the quoted public wording and plain geometry. The code is
`src/openflight/metric_definitions.py`; its tests, `tests/test_metric_definitions.py`,
check hand-worked cases and properties.

The kiosk shows all five. Spin loft, face to path and D-plane tilt use the
[collision model](../how-it-works/collision-model.md)'s calculated face and loft;
curve uses the flight model's landing point.

## Frame, units and signs

- **Frame:** x downrange along the target line, y target-right, z up.
- **Angles:** degrees.
    - A horizontal angle (club path, face angle, launch direction) is the
      azimuth from +x toward +y. **Positive is right of the target line.**
    - A vertical angle (attack angle, dynamic loft) is the elevation above the
      horizontal. **Positive is up.**
- **TrackMan's public signs agree:**
    - club path: "A positive value means the club is moving to the right of
      the target line regardless of dexterity"
      ([club path](https://www.trackman.com/blog/club-path));
    - launch direction: positive "indicates a ball that starts right of the
      target" ([guide](https://www.trackman.com/blog/the-ultimate-guide-to-understanding-trackman));
    - face to path: positive "means the face is pointed to the right of the
      club path regardless of dexterity" ([face to path](https://www.trackman.com/blog/face-to-path));
    - attack angle: positive is hitting up on the ball
      ([attack angle](https://www.trackman.com/blog/attack-angle)).
- **Missing and bad inputs:**
    - a missing input (None) gives None, with nothing filled in;
    - a non-finite input (NaN, inf) gives a non-finite result, so the payload's
      "not measured" reason still names it;
    - an input outside the definition's domain gives None.

## Smash factor

- **Public definition:** "Smash Factor is ball speed divided by club speed"
  ([smash factor](https://www.trackman.com/blog/smash-factor)); "The ratio
  between the measured Ball Speed and Club Speed"
  ([support](https://support.trackmangolf.com/hc/en-us/articles/39726314021915-Parameters-Smash-Factor-Tee-to-Green)).
- **Formula:** `smash = ball speed / club speed`, with both speeds in the same unit.
- **Domain:** None without a positive, finite club speed, or with a negative ball speed.
- **What the kiosk shows:**
    - TrackMan divides the ball's full speed by the head's full speed.
    - A fusion unit divides the OPS243 radial ball speed by the OPS243 radial
      club speed, which is a different quantity.
    - So a fusion unit's shot carries `smash_factor_status: "radial_ratio"`,
      and the kiosk shows "radial ratio" under the value. The value is still
      shown, not hidden.
    - An OPS-only unit's smash is unlabelled, as before.

## Face to path

- **Public definition:** "The angle difference between FACE ANGLE and CLUB
  PATH as defined (FACE ANGLE minus CLUB PATH)"
  ([face to path](https://www.trackman.com/blog/face-to-path)).
- **Formula:** `face to path = face angle − club path`.
- **Sign:** positive when the face points right of the path.

## Spin loft

- **Public definition:** "Spin Loft is the three-dimensional angle between the
  direction the club head is moving (both club path and attack angle) and the
  direction the club face is pointing (both face angle and dynamic loft)." "This
  angle is calculated between two 3D vectors"
  ([spin loft](https://www.trackman.com/blog/spin-loft)).
- **Club direction:** the unit vector at elevation A (attack angle) and azimuth
  P (club path):

    `d = (cos A cos P, cos A sin P, sin A)`

- **Face normal:** our stated spherical construction. Its elevation is the
  dynamic loft L, and the azimuth of its horizontal projection is the face
  angle F:

    `n = (cos L cos F, cos L sin F, sin L)`

    TrackMan calls dynamic loft "the vertical angle of the club face" and face
    angle "the direction the club face is pointing"
    ([parameters](https://www.trackman.com/blog/40-trackman-parameters)).
- **Formula:** `spin loft = arccos(d · n)`, computed in the stable half-angle
  form. Expanded:

    `cos(spin loft) = cos A cos L cos(F − P) + sin A sin L`

- **Not loft minus attack.** Spin loft equals dynamic loft − attack angle only
  when face to path is zero. Otherwise it is larger, as the same page says:
  "if the face to path value is not equal to zero, then the simple subtraction
  method will vary from the actual Spin Loft".
- **Worked example:** a level club on the target line (A = P = 0) with a
  45° loft and a 45° face gives cos = ½, so the spin loft is 60°. Loft − attack
  would say 45°.

## D-plane tilt

- **Public definition:** "D Plane Tilt is the tilt of the D Plane relative to
  the plumb line. D Plane Tilt is the expected spin axis disregarding any gear
  effect [...] Thus, if face-to-path is zero, D Plane Tilt will be zero"
  ([support](https://support.trackmangolf.com/hc/en-us/articles/39726207070875-Parameters-D-Plane-Tilt-Tee-to-Green)).
- **The D plane:** TrackMan's public pages use the term but do not define the
  plane. We take the physics definition: the plane containing the club head's
  direction of travel and the face normal at impact (T. P. Jorgensen, *The
  Physics of Golf*, 2nd ed., 1999).
- **Formula:**
    1. The plane's normal is `m = d × n`. It is oriented so its horizontal part
       points left of the club's direction of travel, the backspin side for a
       strike with loft above attack.
    2. `tilt = asin(m_z / |m|)`.
- **Sign:** zero for a vertical plane. Positive when the top of the plane
  leans right. On a backspin strike, the sign is the sign of face to path,
  matching TrackMan's spin axis sign: "a positive Spin Axis represents a ball
  curving to the right"
  ([guide](https://www.trackman.com/blog/the-ultimate-guide-to-understanding-trackman)).
- **Undefined (None):** when d and n are parallel (zero spin loft), since there
  is no plane.
- **Check:** with face to path zero, d and n lie in one vertical plane, so the
  tilt is zero, as TrackMan states.

## Curve

- **Public definitions**
  ([parameters](https://www.trackman.com/blog/40-trackman-parameters),
  [support](https://support.trackmangolf.com/hc/en-us/articles/5089892383515-Practice-Trackman-Data-Parameter-Definitions)):
    - Curve: "The horizontal side movement perpendicular from the launch
      direction to carry side."
    - Carry: "The straight-line distance between where the golf ball was
      launched from and where it crosses a point that has the same elevation."
    - Carry Side: "The perpendicular distance between the target line and where
      the trajectory crosses a point that has the same elevation as where the
      golf ball was launched."
- **Frame:** the landing point is at launch elevation, so carry C is its
  horizontal radial distance from the launch point. With carry side S
  (positive right), the landing point is `(√(C² − S²), S)`.
- **Formula:** the launch line has direction `(cos t, sin t)` for launch
  direction t, and right-hand perpendicular `(−sin t, cos t)`. So:

    `curve = S cos t − √(C² − S²) sin t`

- **Sign and unit:** positive when the ball landed right of its launch line.
  The unit is carry's unit.
- **Checks:**
    - A landing point on the launch line gives 0.
    - With t = 0, curve equals carry side.
    - A ball launched 5° right that lands on the target line at 200 yd curved
      200 sin 5° = 17.4 yd left.
    - A TM4 normalized export, 6 May 2026: the formula on its flat carry, carry side and
      launch direction gives its Curve column on all 24 shots, to 1e-9 ft.
- **Domain:** None for a non-positive carry, |S| > C, or a launch direction
  that is not downrange (|t| ≥ 90°).

## The D plane

The D plane itself has no TrackMan public definition (only its tilt has), so the
plane comes from the physics literature cited above.
