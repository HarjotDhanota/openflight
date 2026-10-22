# How impact location works (experimental)

Status: **experimental_unqualified**. Nothing here is validated. No reading has been
scored against a strike reference, because none of the recorded shots with marked face
photos could be read (see "Results so far"). It runs after a session on its recorded
clips, and live on the kiosk after each shot (see "Live on the kiosk"), so testers can
judge it against their own spray marks. On the kiosk it is labelled `experimental`
(badge **EXP**, "not yet validated") and it feeds no other metric.

Code: `src/openflight/camera/impact_location.py` (one shot),
`src/openflight/camera/impact_session.py` (a session), the CLI
`scripts/analysis/impact_location.py`, and the kiosk's live reading
`src/openflight/camera/impact_live.py`. All four are research code
(`config/module_buckets.json`: RESEARCH).

## What it reports

| Field | Meaning |
|---|---|
| `impact_offset_mm` | Heel to toe along the face, + toward the toe, for either hand |
| `impact_height_mm` | Up the face from the face centre, + up, along the face |
| `impact_offset_sigma_mm`, `impact_height_sigma_mm` | 1 sigma: named terms in quadrature |
| `label` | Always `experimental_unqualified` |
| `status` / `reason` | `ok`, or `rejected` with the reason. A rejected offset rejects the shot |
| `height_status` / `height_reason` | Height can be refused on its own |

Both axes are the face coordinates of the contact point **P = B − r·n̂**:

- B is the resting ball's centre;
- r = 21.34 mm;
- n̂ is the face normal from the face angle and the dynamic loft.

## The shared steps

1. **The ball's range.** The shot's own IWR slant range comes first, then the run's
   selected tee range, then the session's tee-range handoff, then `--range-m`. A camera
   range prior is never used. The rig's IWR offset turns the slant range into the ball's
   depth along the resting ball's ray. The range is **never** read from the ball's size.
   A resting ball whose size contradicts the range by more than 35 % is refused.
2. **Contact, from the ball.** Contact is the first frame in which the resting ball has
   moved. The witnesses are its top rim, its top cap and the departing ball, and a
   second witness must confirm. Some balls cannot be seen to leave: a saturated view, or
   a ball with no contrast against what surrounds it. Then the shot is refused by name.
   A ball clock (the ball's own flight fit, on the bench) can stand in; it is labelled
   `ball_clock` and adds a one-frame ambiguity term.
3. **The head outline.** Each frame's head is a background-difference mask, split from
   the shaft. A self-built occupancy template is made per (club, frame size) from the
   session's clips. Each frame is placed by translation and roll at the scale the depth
   model sets (OPS club speed). This step is ported unchanged from the September v1.
4. **Interpolate to contact.** The last frame before contact and the first after it
   bracket contact, at most 9 ms apart. Heel, toe and the sole line are interpolated
   between them, in millimetres, to the contact moment.

## Heel to toe

The ball centre is projected on the interpolated heel-to-toe line, and measured from
the face centre. The face centre is a **convention**: the outline's heel-toe midpoint
plus 8 mm toe-ward, uncalibrated.

The image sees `a·cos F + r·sin F` for a true offset a at face angle F. So F, + open in
the analysis orientation, is taken out: a = (imaged − r·sin F) / cos F.

| Term | Source |
|---|---|
| alignment | 1 px of landmark noise, interpolated |
| contact timing | Contact fraction: uniform in the bracket, or ±0.1 with a ball clock |
| club speed | 3 % when measured (OPS), 15 % when assumed |
| range scale | The range's own sigma (IWR spread, else one 46.9 mm bin) |
| arc chord | The straight chord against the curved path |
| face angle | The face angle's sigma |
| contact frame ambiguity | Only when the contact witnesses or the ball clock flag it |

Not in the budget: the face-centre convention (systematic), toe blur beyond the toe
calibration, and lens distortion. The pinhole model is used; the stock lens has about
16 % barrel distortion at the edge.

## Top to bottom (new)

The camera sees the back of the head. The bottom of its outline at contact is the sole
line, read at the ball's heel-toe position:

1. The ball centre's height above the sole line at contact, in the ball's plane.
2. Less the **sole datum**: the silhouette's bottom is the trailing edge, which sits
   below the leading edge. Parallax adds lens height × sole width / range, and bounce
   adds sole width × sin(effective bounce).
3. Less r·sin(loft): the contact point sits below the ball centre on a lofted face.
4. Divided by cos(loft): this gives the distance along the face.
5. Less the **face-centre height**: the face centre's height above the leading edge,
   along the face.

| Term | Source |
|---|---|
| edge noise | 1 px on the sole line (interpolated) and 0.5 px on the ball centre |
| contact timing | The larger of the re-read across the contact fraction and V·sin(\|attack\| + sigma) × timing sigma |
| club speed, range scale | As for heel-toe |
| dynamic loft | The loft's sigma, re-read (about 0.35 mm per degree) |
| face-centre height | The anchor's or the prior's sigma |
| sole datum | From the sole width, bounce and lens-height sigmas |
| contact frame ambiguity | As for heel-toe |

### The face-centre height

No manufacturer publishes it. Two sources, in order:

1. **An anchor per club from marked face photos.** The photo must mark the leading edge
   and the top line; the face-centre height is half the distance between them at the
   groove midpoint. The test bench builds the file (`face_centre_anchors.json`, schema
   `openflight.face_centre_anchors.v1`); pass it with `--anchors`.
2. **A category prior**, labelled `prior_unpublished_category_bracket`. It is irons
   23 ± 5 mm, wedges 24 ± 5, hybrids 20 ± 5, fairway woods 17 ± 5, driver 30 ± 6. An
   unknown club gets the iron prior, 1.5× wider.

The other inputs are labelled too:

- **Dynamic loft, face angle and attack.** These come from the shot record's labelled
  metrics. The sigma is 2° when measured, 3° calculated, 5° model geometry and
  6° estimated. Without them, priors are used: nominal loft less a typical shaft lean
  (±6°), square face (±4°), and a category attack (±4°).
- **Sole width.** Irons take Mizuno's official 7-iron range of 15.9–23.9 mm. Other
  categories take labelled priors.

### Do the top and bottom edges blur less than the toe?

Each reading reports, per bracketing frame:

- the measured 10–90 % edge width of the toe, the sole and the topline;
- the blur their motion predicts: image speed × exposure.

On the one real head frame that segments, test 7 shot 3's frame 17 (sunlit, 300 µs,
1.05 mm/px, 8.7 ms before contact), the sole edge is 1.3 px, the toe 3.0 px and the
topline 3.8 px. The sole is sharper than the toe, as the small vertical motion predicts.
The topline is not, because the hosel and shaft join it there. That is one frame: a
hint, not a finding.

## Running it

```
uv run python scripts/analysis/impact_location.py SESSION --out OUT \
    [--anchors face_centre_anchors.json] [--handedness right|left] [--truth marks.csv]
```

- `SESSION` is a tester session folder, an unzipped bundle or a bundle `.zip`.
- The golfer's hand comes from the session's `--handed` record; right-handed is the
  default.
- The focal length comes from the session's lens record; for a session without one,
  `--lens-id` names the lens in `config/lenses.json`. Otherwise the rig file's stock lens
  is used, and every row says which.
- The output is `impact_location.csv` and `.json`, one row per clip.
- `overlays/` holds the analysed frames. Look at them before any number. Green is the
  head mask, orange the placed outline (red when a frame failed a gate), blue heel and
  toe, yellow the resting ball, and magenta heel, toe, face centre and sole at contact.

## Live on the kiosk

`impact_live.py` is a kiosk extension (entry point `impact_live`; `--no-impact-live`
turns it off). It does the same reading, one shot at a time:

1. **When.** Once a shot with a saved camera clip has been shown, its clip, its shot
   record and the session's start record go to a worker process. The worker is
   `python -m openflight.camera.impact_live --worker`, started when the camera is set
   up. It runs at low priority (nice 10, or below-normal on Windows) with one thread
   for its maths. It is a separate process, so it never holds the kiosk's Python
   threads, the next shot, the ready light or the camera.
2. **The head outline.** The worker keeps a running outline per (club, frame size)
   from this session's swings so far. Only swings with a head in their clip count,
   and the latest 8 are kept. The swings pool their shape only; every reading is the
   shot's own. Until a club has 5 such swings, a shot is not measured with the
   reason "building the head outline: 3 of 5 swings with this club". The fifth swing
   builds the outline before it is read. Later shots read the current outline. While
   the worker is idle, it rebuilds the outline with the swings that arrived since.
   A new shot interrupts that rebuild, and the old outline stays in use. A new
   session starts the outlines again.
3. **Analysis width.** A clip wider than 640 pixels is averaged down to 640x400 by
   2x2 blocks and read with that mode's focal length. This keeps a reading to about
   a second on a desktop. One pixel of alignment noise is then about 1.7 mm at
   1.5 m, and the reading's 1-sigma says so.
4. **The reading.** The result goes on `Shot.impact_location` and out as a shot
   update (`server.publish_shot_update`: the UI replaces the shot in place, and the
   session log gets a `shot_updated` entry). Simulators and phones are not sent it
   again. `impact_offset` and `impact_height` become `experimental` records with
   their 1-sigma range, or `not_measured` with the reason in plain words: no radar
   distance to the ball, glare, the camera never saw the ball leave, the club head
   did not show in the clip, and so on. The code stays in `reason_code`.
5. **Dropped.** A reading still open when the next shot arrives is dropped, and the
   worker stops on it. Its reason is "dropped: the next shot arrived before this one
   was read". If the worker had already read its clip, the swing still joins the outline.
6. **Logged.** Every reading, refusal and drop is an `impact_location` entry in the
   session log, with its stage times (`stages_ms`, `analysis_ms`) and the time from
   hand-off to result (`latency_ms`).

### It feeds nothing else

The collision model never receives it. `shot_models` passes no impact location, and
the model's gear effect accepts only a `measured` one. Spin, spin axis, face, loft,
flight and every other metric are the same with or without a reading
(`tests/test_impact_isolation.py`).

### The face view

The Club tab opens with a face-impact view:

- **The face.** Our own drawing of the shot's club category (driver, fairway
  wood, hybrid, iron, wedge), seen from the target, with the toe on the left for a
  right-hander. A left-hander's drawing is mirrored. The proportions are
  illustrative and labelled so. The iron's length sits inside Mizuno's published
  7-iron head lengths (74-87 mm).
- **The grid.** 10 mm, every number in mm whatever the unit preference, centred on
  the drawn face's centre: the middle of the groove block on irons and wedges, and
  on woods midway between the score lines' heel and toe ends and midway up the face.
- **USGA limits.** The drawings keep to the parts of the USGA/R&A Equipment Rules,
  Part 2, that a face-on drawing can show: score lines straight, parallel and evenly
  pitched, drawn 0.6 mm wide against the 0.9 mm (0.035 in) limit, with edge-to-edge
  separation at least 3 widths and 1.905 mm (5c); driver and fairway score lines
  outside the central 42.67 mm impact strip (interpretation 5.b(1)); the centre mark
  inside a 9.53 mm square (5d); wood heads at most 127 mm heel to toe and 71.12 mm
  sole to crown (4b); the shaft fixed at the heel with the neck top at most 127 mm
  from the sole (2c). Groove depth, cross-section, edge radius and face-to-back
  depth are not visible face-on. Sources:
  [Part 2 Rule 2 (shaft)](https://www.usga.org/equipment-standards/equipment-rules-2019/equipment-rules/part-2-rule-3.html),
  [Rule 4 (clubhead)](https://www.usga.org/equipment-standards/equipment-rules-2019/equipment-rules/part-2-rule-5.html),
  [Rule 5 (club face)](https://www.usga.org/equipment-standards/equipment-rules-2019/equipment-rules/part-2-rule-6.html)
  and [its interpretations](https://www.usga.org/equipment-standards/equipment-rules-2019/equipment-rules/part-2-rule-6-interpretations.html).
- **The dots.** The shot's dot with its 1-sigma ellipse, and this session's earlier
  shots with the same club as faint dots.
- **The numbers.** Toe/heel and high/low with the EXP badge, or "Not measured:
  reason".

Code: `ui/src/components/panel/FaceImpactView.tsx` and `faceImpact.ts`.

### Time per shot

`scripts/analysis/impact_live_benchmark.py` runs the worker's analyser shot by
shot. Use `--synthetic N` for synthetic swings, or give a recorded session. Run it on
a Pi to replace the estimates below. Measured on an AMD Ryzen 7 5800X (Windows,
22 Oct 2026), with 1280x800 clips read at 640x400:

| Step | Desktop | Pi 5, estimated (x3-4) |
|---|---|---|
| A refused real outdoor clip (Outdoors-test-7) | 0.6-0.9 s | 2-4 s |
| A synthetic swing while the outline builds | 0.3-0.45 s | 1-2 s |
| A reading with the outline ready | 0.8-1.0 s | 2.5-4 s |
| The fifth swing (builds the outline first) | 7.0 s | 20-30 s |
| An idle outline rebuild, 6-8 swings | 6-8 s | 20-30 s, interruptible |
| Worker start (imports) | about 1.5 s | 5-8 s, at kiosk start |

The Pi factor is single-core Geekbench 6 (a Pi 5 scores about 0.4 of a 5800X), widened
for numpy and scipy FFTs on 128-bit NEON against AVX2. At 1280x800 without averaging
down, a reading takes about 1.1 s refused and 3 s read on the desktop, and the
outline takes 26-40 s.

## Results so far

The test bench graded it on the 8 confirmed spray marks of Outdoors-test-1, -5 and -7.
None could be read:

| Cause | Shots |
|---|---|
| The ball contrasts 0–2 DN with its surroundings at dusk, and the dark head does not segment either | test 5 shots 3, 5, 6 |
| The IWR has no ball distance of its own | test 5 shots 2, 4 |
| No resting ball found | test 7 shot 1 |
| The frames are saturated | test 1 shot 2 |
| The head after contact is washed out against a white mat | test 7 shot 3 |

The September kiosk logs carry no per-shot range, so the CLI alone refuses every clip
of those sessions with `no_ball_range_for_this_shot`. The August hand marks (heel-toe
r = 0.97) are not in the bench, so that regression was not run.

## What blocks better accuracy

- **Light.** Every marked shot failed on exposure. Dusk foam balls and dark heads
  vanish at 300 µs; sun at gain 1–12 saturates the mat, the ball and the head.
- **Frame rate.** At 115–120 fps the frame before contact is about 300 mm from the
  ball, at about 0.8 of its depth. Contact timing then dominates both budgets, and the
  height budget especially.
- **Range.** It needs the shot's own IWR distance, and two of the eight shots had none.
- **Face-centre truth.** No official face height exists, and the existing marks have no
  leading edge or top line. Height therefore rests on priors (±5 mm), and the photos'
  scale rests on a groove-length prior (±13 %) until a club's groove length is
  measured.
- **Lens.** The stock lens is uncalibrated and about 16 % barrel at the edge.
- **Live width.** The kiosk reads at 640x400 to keep up. The 1280x800 detail is lost
  to the averaging until the reading is fast enough at full size.
