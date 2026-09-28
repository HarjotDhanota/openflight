# Impact Location and Face Angle Plan

Status: planned; nothing here is implemented or validated

Date: 2026-09-28

Related milestones: fusion master plan M3 (camera mode and exposure) and M4
(pose, strike and spin feasibility)

This records the approach agreed on 2026-09-28 so the work can start once the
setup-range and static-exposure work has passed its Pi tests. It depends on a
trustworthy ball position, so it comes after that qualification, not before.

## Face angle: D-plane, not clubhead pose

Trackman 4 and Mevo Gen 2 do not measure face orientation from pixels. They
invert the impact collision model (the D-plane): the horizontal launch
direction is roughly 75-85% face angle and 15-25% club path, with the share
depending on club and strike (61-87% in a robot study). Given launch direction
and club path, face angle follows; spin axis is a cross-check.

- Face error is about 1.33 x launch-direction error + 0.33 x path error, so
  horizontal launch direction is the input that matters. Earlier simulation
  reached about 0.6 deg face at launch-direction error <= 1 deg.
- Off-centre strikes (gear effect) are the main corruption: about 10 mm toward
  the toe is about 2 deg of apparent face angle. Impact location corrects it.
- The behind-ball silhouette measured flat to about +-11 deg in face angle, so
  orientation from the outline is not expected to work (see the CAD retest
  below for the one exception worth testing).

Prerequisites, in order:

1. Horizontal launch direction within 1 deg of the target line. Apply and
   validate the IWR azimuth registration (about 5 deg was measured, never
   applied) and the auto-alignment target-line datum.
2. A per-club face-to-launch coefficient, validated per club.
3. Impact location for gear-effect correction (mostly driver).

## Impact location: a small model, not CAD

Impact location needs a model of where the face is relative to what the
camera sees: a 2D head outline plus a face-centre anchor, heel-toe direction
and sole line. It does not need a 3D mesh. Three sources, combined:

- The empirical template built from the club's own frames (already passes
  +-1-2 px gates on 18 of 21 shots; automatic heel-toe zone r = 0.97 against
  hand marks).
- Official per-club dimensions where published (Mizuno per club; Srixon 4i,
  7i and PW). Face height is not published anywhere.
- A few foot-spray or impact-tape shots per club to anchor the face centre
  relative to the toe landmark and to provide truth.

Heel-toe is feasible behind the ball; high-low is hard with one behind-ball
camera because the face points away from it.

## Contact point on the ball

At contact the face plane is tangent to the ball, so the contact point is one
radius from the ball centre along the face normal:

    P = B - r * n_hat        (r = 21.34 mm, n_hat = face normal)

In face coordinates, projecting the ball centre onto the face along the face
normal lands exactly on the contact point. Errors come only from projecting
along the wrong direction:

| Error | Heel-toe | High-low |
|---|---|---|
| Face angle 3 deg wrong | about 1 mm | - |
| Dynamic loft wrong | - | about 0.4 mm per degree |
| Target line used instead of face normal (7-iron, about 22 deg loft) | - | about 8-9 mm |

Inputs:

1. Ball centre in 3D: the resting ball, locked rig geometry and the qualified
   setup range.
2. Face plane at contact: heel-toe position from the head model; face normal
   from face angle plus dynamic loft (D-plane or club loft).
3. Head position at the instant of contact. This is the accuracy wall. At
   120 fps the head moves about 30 cm between frames, so interpolate between
   the last frame before and the first frame after contact (the head slows
   only 10-25%), placed with the ball-defined contact time. Do not extrapolate
   from the last pre-impact frame: a 3 deg path error over half a frame gap is
   about 8 mm. Toe blur (about 7-10 mm) is the other limit, so light matters.

## CAD model retest

The first mesh attempt ran with a wrong focal length (1033 px instead of
466.67), a mirrored right-handed mesh, an unmeasured rig and range as a free
variable. It was not a fair test. Rerun it with everything locked.

- Model source: official geometry only. Most OEMs do not publish CAD, so the
  candidate is a parametric head built from official specs, or a scan of the
  owner's own club if the owner approves it as measurement.
- Contenders: (a) empirical template, (b) parametric or scanned head. Both use
  the same contact-point geometry and before/after interpolation.
- Truth: foot spray on every shot, 30+ shots per club, 2 clubs, locked rig,
  camera settings and range.
- Score: heel-toe and high-low error in mm against spray; separately, face
  angle against a reference to test whether the outline stays flat for
  orientation. Do not score by silhouette IoU, which previously favoured the
  wrong poses.
- Decision: keep whichever head model wins on spray error, per club. If CAD
  also recovers face angle within about +-2 deg, record it as a real finding.

## Truth sources without a face-angle jig

- Net grid plus phone slow motion: vertical tape lines every 5 cm on the net,
  measured ball-to-net distance, 240 fps video. Horizontal launch =
  atan(offset / distance). Validates the dominant D-plane input.
- Foot spray or impact tape: impact location truth on every shot.
- Outdoor draw/fade sign test: the predicted face-to-path sign must match the
  ball's curve.
- One hour of Trackman 4 at a fitter, recording both systems on the same
  shots: the only direct face-angle reference.

## Order of work

1. Pass the Pi setup-range and static-exposure tests.
2. Spray calibration and net-grid capture in the same Pi session.
3. Apply azimuth registration and the target-line datum; build the D-plane
   face-angle estimator on validated launch direction.
4. Contact-point geometry with before/after interpolation and the ball-defined
   contact time.
5. CAD versus template retest against spray truth.
6. Trackman session to score face angle.
