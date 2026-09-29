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

## v1 tool (experimental, unqualified): what exists

Status: implemented on `agent/impact-location`, 2026-09-28. It has been
exercised on synthetic swings only and has read no real shot. No reading has
been scored against a strike reference. Every result carries
`label: experimental_unqualified`.

The code:

- `src/openflight/camera/impact_location.py`: pure functions.
- `scripts/analysis/impact_location.py`: the session CLI.
- `tests/test_camera_impact_location.py`: the tests.

### What v1 implements

- **Ball-defined contact frame.** This is the first frame in which the resting
  ball has moved. It is not the trigger minus a fixed flight time.
  - The primary witness is the ball's top rim on its central columns, located
    to a quarter pixel. It sees the first millimetres of flight.
  - The rim is accepted only when a second witness confirms it within a frame
    or two: either the top cap changes, or a *new* ball-bright disk appears
    clear of the tee.
  - Contact lies between `last_rest_frame` and `contact_frame`.
  - `contact_minus_trigger_ms` is logged per shot, which measures the
    trigger's jitter.
- **The head outline is ported, not reinvented.** The source is
  `research/session-metrics` 37dd859 and its promotion in
  `clubpose/head_outline.py`:
  - Masks: background median with `|diff| > max(4 sigma, 18)`, then close and
    fill, then the thick-head / thin-shaft split. The background ends 32 ms
    before contact.
  - The flying-ball veto runs after contact only.
  - A **self-built** occupancy template per (club, frame size), from every clip
    of the session. Landmarks are seeded from mask extremes and re-read over up
    to 3 passes. The sole and heel come from the occupancy fall-off. The toe is
    calibrated on slow frames.
  - Placement is translation plus roll (FFT local IoU), behind the IoU,
    search-boundary and track gates.
  - All pixel constants are re-expressed in millimetres at the research plate
    scale. That lets 320x200, 640x400 and 1280x800 share one set.
  - Clips are cropped to a box around the ball, which keeps the golfer out.
- **Contact point.** The head is interpolated between the accepted frames that
  bracket contact, which must be at most 9 ms apart.
  - The interpolation runs in metric lateral coordinates. Each frame's depth
    comes from a club-speed model, using OPS club speed from the session JSONL
    when the clip is linked.
  - The heel-toe offset is the ball centre projected on the heel-to-toe line,
    measured from the face centre. On that axis `P = B - r n_hat` lands at the
    ball centre's own heel-toe coordinate.
- **Uncertainty (1 sigma).** These named terms add in quadrature:
  - alignment (1 px landmark noise);
  - contact timing (contact fraction uniform inside the bracket);
  - club speed (15 % when assumed, 3 % when measured);
  - ball scale (1 px of diameter);
  - arc chord (the chord cuts inside the curved path; uses the August arc
    radius and swing plane);
  - a one-frame ambiguity term, when the contact witnesses flag it.

  The **face-centre convention is not in the budget**, because it is
  systematic.

### What it assumes

- **Handedness and mirroring.** Right-handed is the default. For a
  left-handed golfer, pass `--handedness left`. That, and a mirrored save, are
  each mapped by one explicit flip (`analysis_flip`). + is toward the toe for
  either hand.
- **Face-centre convention.** The face centre is the aligned outline's
  heel-toe midpoint + 8 mm toe-ward, the research convention. The self-built
  heel landmark runs up the hosel neck, so this is a **convention until spray
  truth calibrates it**. The CLI prints the `--face-centre-offset-mm` that
  removes the measured bias.
- **Nominal pinhole camera.**
  - Focal length is 933.3 px for 1280-wide saves and 466.7 px otherwise. It
    enters only through the depth ratio.
  - The principal point is taken at the image centre.
  - The ball's range comes from its apparent size. A size implying less than
    0.5 m or more than 4 m is refused by name.
- **Face angle.** Taken as 0 for the projection; 3 deg is about 1 mm
  heel-toe.
- **Club speed.** The source order is:
  1. the linked OPS value;
  2. `--club-speed-mps`;
  3. 35 m/s, ASSUMED and flagged.

  The head slows by a factor of 0.83 through impact.

### Known limits

- **At 120 fps there is one frame either side of contact.**
  - There is no witness for where contact fell inside the ~8.7 ms gap, so v1
    takes the midpoint.
  - On synthetic swings with 12 mm/frame of lateral head motion, a true
    fraction of 0.1 or 0.9 costs up to about 6 mm.
  - The uncertainty budget reports about 5 mm at 120 fps. At 450 fps it
    reports about 2 mm.
  - The ball clock (ray fit), or the head's own apparent size, would pin the
    fraction; neither is used yet.
- **A high toe strike can hide the rim before contact.** A soled topline sits
  only 3-5 mm below the ball's top, and the camera is low.
  - The bracket can then land one frame early.
  - The pattern is flagged `rim_lost_under_a_partial_cap_change...` and widens
    the uncertainty.
  - On synthetic swings, 14 of 15 brackets were right, and the miss was
    flagged.
- **Camera height is not used.** Camera height is now solved per session, but
  heights enter only the high-low channel, which v1 does not report.
- **Blur at 1280x800.** At 300 us a 7-iron smears about 10 mm, mostly along
  the line of sight.
  - The documented wall is the **7-10 mm heel-ward toe-blur overhang**.
  - The toe calibration needs 4 slow frames, so it usually stands down at
    120 fps. When it does, the result says `toe_calibrated: false`.
  - Spray calibration of the centre offset absorbs the mean overhang, not its
    shot-to-shot spread.
- **Research parts not ported:** the USGA gates, the specular-topline ridge,
  and the high-low channel. v1 is heel-toe only.
- **The flying-ball veto** is the research's: the brightest ball-sized disk
  above the tee row. A specular head brighter than 0.8x the ball can be vetoed
  instead, so check the overlays.
- **No accuracy carries over yet.** The research's r = 0.97 was consistency
  against hand marks, at 467 fps, on another rig.
- **The one real bundle tried was fully rejected.** On
  `harjot-pilot-test-1` (2026-09-24, arm 5, dim outdoor scene), every clip is
  rejected by name. The detector never found a real ball: the ladder "balls"
  are 5-6 px specks or sit on the fence line, and none of them ever departs.

### Running it on tomorrow's bundle

1. Keep the spray photos keyed by clip folder name
   (`camera_YYYYMMDD_HHMMSS_mmm_NNN`), the key the ladder uses for impact
   photos. On each photo, measure the mark centre's offset along the
   scorelines, in mm, + toward the toe, from the face centre you want to
   report against (for example the scoreline midpoint). Write `spray.csv`:

       shot_id,heel_toe_mm
       camera_20260929_091502_123_004,-6.5
       camera_20260929_091540_882_005,3.0

2. Run the CLI on the unzipped bundle, or on the `.zip` directly (it is
   extracted under `--out`):

       uv run python scripts/analysis/impact_location.py \
           ~/Downloads/<tester>-session-bundle-<stamp>.zip \
           --out impact_2026-09-29 --truth spray.csv

   Optional flags:
   - `--handedness left` for a left-handed golfer.
   - `--club 7-iron` when the session JSONL carries no club.
   - `--club-speed-mps N` when OPS club speed is not linked.
   - `--ball X Y DIAMETER` (saved-frame px) or `--redetect-ball` when the
     overlays show the resting ball in the wrong place.

3. **Look at `overlays/*.png` and `templates/*.png` before reading any
   number.** In the overlays:
   - green is the head mask;
   - orange is the placed outline (red means the frame failed a gate);
   - blue marks heel and toe;
   - yellow is the resting ball;
   - magenta is heel, toe and face centre interpolated to contact.

   The contact tiles are labelled `last rest` and `ball moved`.

4. Read `impact_location.csv`. It has one row per clip: `status` and
   `reason`, `contact_frame`, `heel_toe_mm`, `uncertainty_mm` and
   `contact_minus_trigger_ms`.

   The console and `impact_location.json` add agreement with the spray truth,
   overall and per club:
   - bias;
   - RMS;
   - SD after bias;
   - Pearson r;
   - the truth's own spread;
   - the face-centre offset that removes the bias.

   **Only the SD after bias and r are evidence about the method.** The bias
   is the convention being calibrated. Re-run with the printed
   `--face-centre-offset-mm` only on a held-out half.
