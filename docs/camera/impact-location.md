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

## The face centre

Both axes are measured from the same face centre the kiosk's face view draws
(`faceCentre` in `ui/src/components/panel/faceImpact.ts`). It follows the R&A/USGA
centre template (TPX3004):

- **Driver, fairway wood, hybrid:** the centre of the face perimeter. The point sits at
  the middles of the heel-toe and sole-crown chords through it, found by iterating.
- **Iron and wedge:** heel to toe, the middle of the longest score line. Up the face,
  midway between the face's bottom edge and the top line at that point.

The backend cannot see grooves or the face from behind, so it carries the centre as two
numbers per category, measured on the same drawings:

- **C**, how far the centre sits heel-ward of the head's toe extreme;
- **H**, the centre's height along the face above the **leading edge**: the head's
  lowest point under the centre, seen along the face normal.

Both live in one file, `src/openflight/camera/face_centre_reference.json`, with the
population each comes from. `ui/src/components/panel/faceCentreReference.test.ts`
re-measures them on the drawings and fails if a drawing moves by more than 0.1 mm
without the file following.

Each drawing is the **population-average club** of its category (see "The face view"),
so C and H are the population means and the sigmas the population spread combined with
the measurement error:

| Category | Population | C (mm) | H (mm) | H above the face's bottom edge (mm) |
|---|---|---|---|---|
| Driver | 12 face-on photos of 2024–26 drivers, 6 brands; face heights of 121 heads, 15 brands, 2021–26 | 66.4 ± 5.2 | 30.0 ± 2.0 | 25.2 ± 1.8 |
| Fairway | 8 photos of 2024–26 3-woods, 6 brands; 7 published face heights | 56.1 ± 6.2 | 17.9 ± 1.6 | 15.0 ± 1.3 |
| Hybrid | 8 photos of 2024–26 3-hybrids (18–19°), 6 brands; 5 published face heights | 52.3 ± 7.5 | 17.4 ± 1.8 | 14.1 ± 1.6 |
| Iron | 45 7-iron models, 10 brands, 2021+ | 44.2 ± 2.0 | 24.6 ± 2.0 | 22.2 ± 1.6 |
| Wedge | 19 sand and lob wedges (54–60°), 8 brands, 2021+ | 45.0 ± 2.1 | 28.3 ± 1.7 | 25.9 ± 1.8 |

They are labelled `reference_geometry_face_centre_rule`. An unknown club takes the
iron's numbers, 1.5× wider. Until 23 Oct 2026 each category was one photographed model
with bracket sigmas; the old and new values are compared under "The face view".

**Why the toe.** The toe is the landmark calibrated against slow frames. The heel
landmark depends on how much of the hosel the camera's mask keeps, so a centre built
from the heel-toe midpoint moves with the hosel cut. The toe extreme's heel-toe
coordinate is the same in the face-normal drawing and in the behind-ball camera: both
views are perpendicular to the heel-toe axis.

**Why the head's lowest point for H.** The camera's sole line is the trailing edge, and
the sole datum (parallax plus bounce, below) lifts it to the front of the sole, where it
meets the face. Seen along the face normal, that is the head's lowest point: the sole
behind it projects higher, by about its width × sin(loft). The face's own bottom edge sits
2.4–4.8 mm above it on the drawings (the leading edge's radius seen face-on). The table
gives both; the backend uses the first.

**Marked face photos** (the test bench's strike truth) see the face's bottom edge, its top
line and the lowest score line, not the leading edge or the face perimeter.
`face_photo_reference(club)` gives what carries them onto this rule, measured on the same
drawings and checked by the same vitest file, each with the population's spread:

| Category | Band: H − H above the face's bottom edge (mm) | Centre toe-ward of the lowest line's middle (mm) | Lowest line: length / above the face's bottom edge (mm) |
|---|---|---|---|
| Driver | 4.8 ± 2.5 | +1.2 ± 3.2 | 63.5 ± 10.2 / 9.9 ± 4.0 |
| Fairway | 2.9 ± 1.2 | −1.0 ± 4.9 | 40.0 ± 4.8 / 5.3 ± 1.6 |
| Hybrid | 3.3 ± 1.6 | −1.0 ± 1.5 | 35.8 ± 4.5 / 4.8 ± 1.3 |
| Iron | 2.4 ± 1.2 | 0 (the lowest line is the longest) | 53.6 ± 2.5 / 4.3 ± 1.0 |
| Wedge | 2.4 ± 1.2 | 0 (the lowest line is the longest) | 52.2 ± 2.5 / 3.6 ± 0.6 |

A photo's centre height above the leading edge is its own half-distance from the face's
bottom edge to the top line plus the band. The lowest line is its whole row, end to end
across any gap (the driver's two blocks). The driver keeps the kiosk's own line design
(7 + 7 block lines and 3 centre bars), so its lowest-line numbers are that design's, not
a population mean: 2021+ drivers share no common line pattern (from 3 short lines to
about 47 milled ones), and the population's lowest line sits 3.5 mm (median, ± 4.0)
above the face bottom. The file also carries each category's longest line (iron 53.6 ±
2.5, wedge 52.2 ± 2.5, 3-wood 74.0 ± 8.8, hybrid 65.0 ± 8.1, the driver's middle bar 30.6)
and the irons' toe margin (17.4 ± 1.1, wedge 18.9 ± 1.0).

## Heel to toe

The ball centre is projected on the interpolated heel-to-toe line and measured from the
toe landmark: `ball_from_toe_mm`, negative on the face. Then

impact offset = (ball_from_toe − r·sin F) / cos F + C

For a true distance d along the face from the toe, the image sees `d·cos F + r·sin F`.
So the face angle F, + open in the analysis orientation, is taken out before C is added.

`--face-centre-from-toe-mm` (and `face_centre_from_toe_mm` in `locate_impact` and
`analyse_shot`) is a research override of C. It is exact (no sigma), labelled
`research_override`, and named in `face_centre_convention`.

| Term | Source |
|---|---|
| alignment | 1 px of landmark noise, interpolated |
| contact timing | Contact fraction: uniform in the bracket, or ±0.1 with a ball clock |
| club speed | 3 % when measured (OPS), 15 % when assumed |
| range scale | The range's own sigma (IWR spread, else one 46.9 mm bin) |
| arc chord | The straight chord against the curved path |
| face angle | The face angle's sigma |
| face centre | C's sigma (0 under the research override) |
| contact frame ambiguity | Only when the contact witnesses or the ball clock flag it |

Not in the budget: toe blur beyond the toe calibration, and lens distortion. The pinhole
model is used; the stock lens has about 16 % barrel distortion at the edge.

## Top to bottom (new)

The camera sees the back of the head. The bottom of its outline at contact is the sole
line, read at the ball's heel-toe position:

1. The ball centre's height above the sole line at contact, in the ball's plane.
2. Less the **sole datum**: the silhouette's bottom is the trailing edge, which sits
   below the leading edge. Parallax adds lens height × sole width / range, and bounce
   adds sole width × sin(effective bounce).
3. Less r·sin(loft): the contact point sits below the ball centre on a lofted face.
4. Divided by cos(loft): this gives the distance along the face.
5. Less the **face-centre height** H: the face centre's height above the leading edge,
   along the face.

The sole line is read under the ball, and H under the face centre. Where the leading
edge rises between the two, heel to toe, the difference is not in the budget.

| Term | Source |
|---|---|
| edge noise | 1 px on the sole line (interpolated) and 0.5 px on the ball centre |
| contact timing | The larger of the re-read across the contact fraction and V·sin(\|attack\| + sigma) × timing sigma |
| club speed, range scale | As for heel-toe |
| dynamic loft | The loft's sigma, re-read (about 0.35 mm per degree) |
| face-centre height | The anchor's sigma, or H's |
| sole datum | From the sole width, bounce and lens-height sigmas |
| contact frame ambiguity | As for heel-toe |

### The face-centre height

No manufacturer publishes it. Two sources, in order:

1. **An anchor per club from marked face photos.** The test bench builds the file
   (`face_centre_anchors.json`, schema `openflight.face_centre_anchors.v1`); pass it
   with `--anchors`. Its value must be the same H: the rule's centre, measured above the
   head's lowest point. The bench's anchors do not follow that yet (see below).
2. **The category's H** from `face_centre_reference.json` (table above).

Until 22 Oct 2026 the category values were unpublished brackets, labelled
`prior_unpublished_category_bracket`: irons 23 ± 5 mm, wedges 24 ± 5, hybrids 20 ± 5,
fairway woods 17 ± 5, driver 30 ± 6.

**The bench's anchors.** `openflight_bench.truth.spray_marks` takes half the
leading-edge-to-top-line distance at the midpoint of the face's *lowest* groove, from the
marked `leading_edge_px` line (described as the face's bottom edge). Two changes are
needed there for its anchors to mean H:

- **Where.** The rule's heel-toe point is the longest score line's middle. That is the
  lowest groove's middle only when the lowest groove is full length, as on the average
  iron and wedge. Woods and hybrids follow the perimeter rule instead.
- **Above what.** Midway between the face's bottom edge and the top line, then measured
  from the head's lowest point. If the marked line is the face's bottom edge, the anchor
  is short by the leading edge's band (2.4–4.8 mm on the drawings). If it is the head's
  lowest point, it is short by half that band. The photo needs both lines, or the
  category's band (H less the face-bottom height in the reference file) added.

Its heel-toe truth for grading (+ toe from the lowest groove's middle) has the same
"where" difference.

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
    [--anchors face_centre_anchors.json] [--handedness right|left] [--truth marks.csv] \
    [--face-centre-from-toe-mm C]
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
  wood, hybrid, iron, wedge), seen along the face normal from the target, with the
  toe on the left for a right-hander; a left-hander's drawing is mirrored. That is
  the view impact location is measured in (mm along the face). Each drawing is the
  **population-average club** of its category, so it fits most clubs fairly well. No
  lettering or logos; the per-model data, photos and fit scripts stay out of the
  repository.
  - **Method.** Official dimensions (OEM blade lengths, lofts and lies, GolfWorks and
    Mizuno/Srixon head lengths) plus OEM face-on photos, each rectified to the
    face-normal view: levelled on its score lines; a homography where the pitch
    drifted or the line ends sheared; the aspect from the hosel, which a face-normal
    view shows at atan(tan lie · cos loft). Irons and wedges are scaled by blade length
    (official where published, else the sub-type's prior). Woods are scaled by face
    height: driver face heights are lab measurements by Golf Digest Japan (121 heads,
    15 brands, 2021–26, 54.9 ± 1.6 mm); 3-wood and hybrid face heights are component
    makers' published figures plus one lab measurement (n = 7, 32.8 ± 2.2; n = 5, 31.5 ± 2.4 mm). All models are 2021 or later.
  - **Aggregates the drawings take** (mm; x heel-ward of the toe extreme, heights
    above the leading edge, the head's lowest point under the centre):

    | | 7-iron | Wedge |
    | --- | --- | --- |
    | loft / lie / hosel seen face-on | 31.4° / 62.3° / 58.4° | 56.3° / 64.0° / 48.7° |
    | blade length, toe extreme to hosel transition | 79.4 ± 3.7 | 80 ± 2.5 |
    | toe height, at x | 56.5 at 16 | 61.2 at 20 |
    | crotch height, at x | 28.3 at 83 | 28.9 at 85 |
    | top-line slope | 24.9 ± 1.9° | 31.0 ± 2.2° |
    | face height at C | 44.4 ± 2.7 | 51.8 ± 2.6 |
    | band | 2.4 ± 1.2 | 2.4 ± 1.2 |
    | lines / pitch / stepped under the top line | 13 / 3.46 / 6 | 18 / 2.98 / 8 |
    | lowest line above the face bottom | 4.3 ± 1.0 | 3.6 ± 0.6 |
    | line length / toe margin / heel margin | 53.6 / 17.4 / 8.4 | 52.2 / 18.9 / 9.0 |
    | face centre (C, H) | (44.2, 24.6) | (45.0, 28.3) |

    | | Driver | 3-wood | Hybrid |
    | --- | --- | --- | --- |
    | loft / lie / hosel seen face-on | 10.4° / 57.4° / 57.0° | 14.9° / 56.6° / 55.7° | 18.8° / 57.7° / 56.3° |
    | head length (R&A, heel at 22.23 mm) | 116.8 ± 7.9 | 105.3 ± 11.9 | 101.0 ± 11.6 |
    | head height | 63.1 ± 4.8 | 38.8 ± 3.9 | 35.0 ± 3.7 |
    | face width × visible height at the centre | 110 × 50.3 | 90 × 30.0 | 82 × 28.2 |
    | band | 4.8 ± 2.5 | 2.9 ± 1.2 | 3.3 ± 1.6 |
    | lines | the kiosk's design | 5 at 4.7, longest 73.9 ± 8.8 | 6 at 3.5, longest 65.3 ± 8.1 |
    | lowest line: length / above the face bottom | — | 0.54 × longest / 5.3 ± 1.6 | 0.55 × longest / 4.8 ± 1.3 |
    | face centre (C, H) | (66.4, 30.0) | (56.1, 17.9) | (52.3, 17.4) |

    The woods' face is the mean of the face perimeter's radius from the template centre
    every 15° (its width stretched to the mean face width, which the 15° sampling cuts
    at the high toe and heel corners). The irons' head is the mean top and bottom contour
    every 4 mm, its top lifted (see "Even rim").
  - **7-iron sub-types** (for reference; the drawing is the whole population):

    | Sub-type | n | Blade length | C | H | centre above the face bottom | line length | toe margin |
    | --- | --- | --- | --- | --- | --- | --- | --- |
    | Players blade | 7 | 74.3 | 41.8 ± 1.4 | 23.9 ± 2.7 | 21.1 ± 2.3 | 51.2 ± 1.8 | 16.2 ± 0.7 |
    | Players cavity/distance | 23 | 78.0 | 44.0 ± 1.4 | 24.9 ± 1.9 | 22.4 ± 1.4 | 53.2 ± 1.6 | 17.4 ± 0.9 |
    | Game improvement | 15 | 84.0 | 45.8 ± 1.8 | 24.4 ± 1.9 | 22.4 ± 1.4 | 55.3 ± 2.8 | 18.1 ± 1.1 |

  - **Even rim.** As on a real club, the head rims the face with a band of one width all
    the way round, the category's leading-edge band (driver 4.8, 3-wood 2.9, hybrid 3.3,
    iron and wedge 2.4 mm), until it runs into the neck at the heel. A wood's head is its
    face offset outward; the hosel's heel-side edge runs straight down until it is
    tangent to the band, and at the top heel the band rises through the crotch into the
    hosel. A blade's face is its head inset by the band, cut by a straight heel edge; the
    head's top is lifted 2 mm (fading out toward the toe) so the face keeps its mean
    height at C. Outside the neck the band varies by at most 1.135 : 1 (the iron's top-heel
    corner) and 1.02–1.06 : 1 elsewhere; `faceFairness.test.ts` fails above 1.25 : 1.
  - **Drawn and reference C.** The even rim brings a wood's toe in, so its drawn C is
    shorter than the population's: the reference file keeps the research value, because
    the backend measures from the real club's toe and the drawing is illustrative.

    | | Driver | 3-wood | Hybrid | 7-iron | Wedge |
    | --- | --- | --- | --- | --- | --- |
    | reference C (research) | 66.4 | 56.1 | 52.3 | 44.2 | 45.0 |
    | drawn C | 61.6 | 48.3 | 45.6 | 44.2 | 45.0 |

    H, the band, the lowest line and the line lengths match the reference on every
    drawing (`faceCentreReference.test.ts`, within 0.1 mm).
  - **Clean outlines.** Each outline is a few fair curves: C2 cubic splines (chord-length
    parameter, so curvature is continuous) between designed breaks only: a straight iron
    top line, the joins into the tube along its edges, a wood's straight heel-side neck,
    and the blades' heel corners. `faceFairness.test.ts` samples curvature along every
    drawn head and face and fails on unintended inflections (only the crotch's two are
    designed), kinks, a radius under 3 mm, or a curvature jump over 0.01/mm at a smooth
    join. The even rim moves the heads off the population's mean contours: 0.9 mm on
    average for the blades (2–3 mm at most, the lifted top), 1.8–2.2 mm for the woods
    (5–8 mm at most at the toe and crown, which the population carries farther from the
    face than an even band). The wood faces lie within 0.2–0.4 mm of the radius table
    stretched to the mean face width (2–2.5 mm at most, at the high toe and heel corners
    the 15° sampling cuts).
  - **Look.** Chrome iron, satin wedge, black woods and hybrid; the sleeves, ferrules,
    joints, the wedge's two hosel joints and the fairway's grey band are kept. The
    driver keeps its 7 + 7 block lines with slanted inner ends and 3 centre bars, scaled
    to the average face, and its centre cross now sits on the computed face centre.
  - **Old against new reference values** (the single photographed models of 22 Oct 2026):

    | Category | C | H | above the face bottom | band | lowest line: length / above the face bottom |
    | --- | --- | --- | --- | --- | --- |
    | Driver | 60.4 → 66.4 | 29.1 → 30.0 | 24.7 → 25.2 | 4.3 → 4.8 | 53.4 / 10.9 → 63.5 / 9.9 |
    | Fairway | 58.5 → 56.1 | 19.7 → 17.9 | 17.5 → 15.0 | 2.1 → 2.9 | 44.6 / 7.1 → 40.0 / 5.3 |
    | Hybrid | 49.6 → 52.3 | 17.8 → 17.4 | 14.3 → 14.1 | 3.5 → 3.3 | 35.0 / 2.3 → 35.8 / 4.8 |
    | Iron | 44.0 → 44.2 | 22.8 → 24.6 | 18.3 → 22.2 | 4.5 → 2.4 | 52.6 / 2.6 → 53.6 / 4.3 |
    | Wedge | 45.4 → 45.0 | 26.9 → 28.3 | 25.2 → 25.9 | 1.7 → 2.4 | 52.5 / 3.9 → 52.2 / 3.6 |

- **The centre lines.** One heel-to-toe and one low-to-high line, clipped to the
  face, cross at the face centre. `faceCentre(category, shape)` computes it from the
  drawn geometry, following the R&A/USGA centre-template idea (TPX3004): the
  distances from the centre to the face edges are equal heel-toe and equal
  sole-crown.
  - Woods and hybrids: start at the middle of the face outline's box. Then
    alternately move to the middle of the heel-toe chord (parallel to the score
    lines) and of the sole-crown chord through the point, until the point moves less
    than 0.01 mm.
  - Irons and wedges: heel-toe at the middle of the longest score line. The milled
    area's toe and heel edges sit within 0.6 mm of its ends, so they agree to 0.1 mm.
    Up the face, the centre is midway between the leading edge and the top line, on
    the line through that point.

  The impact centre sits at the unrounded mm from there; every number is in mm whatever the
  unit preference. The driver's centre cross sits on the computed centre: on an
  average club the design centre is the face centre.
- **The backend measures from this centre** (since 22 Oct 2026; see "The face
  centre"). Before, its origin was the camera outline's heel-toe midpoint + 8 mm
  toe-ward, and a category height above the head outline's lowest point; that origin
  also moved with how much hosel the camera's mask kept.
- **USGA limits.** The drawings keep to the parts of the USGA/R&A Equipment Rules,
  Part 2, that a face-on drawing can show: score lines straight, parallel and evenly
  pitched, drawn 0.6 mm wide against the 0.9 mm (0.035 in) limit, with edge-to-edge
  separation at least 3 widths and 1.905 mm (5c); driver and fairway heel and toe
  blocks outside the central 42.67 mm impact strip (interpretation 5.b(1)), and the
  short centre bars inside it drawn as score lines that meet 5c; the small centre
  cross inside a 9.53 mm square (5d); wood heads at most 127 mm heel to toe (to the heel
  at 0.875 in above the ground, Appendix II) and 71.12 mm sole to crown (4b); the shaft fixed at the heel with the neck top at most 127 mm
  from the sole (2c). Groove depth, cross-section, edge radius and face-to-back
  depth are not visible face-on. Sources:
  [Part 2 Rule 2 (shaft)](https://www.usga.org/equipment-standards/equipment-rules-2019/equipment-rules/part-2-rule-3.html),
  [Rule 4 (clubhead)](https://www.usga.org/equipment-standards/equipment-rules-2019/equipment-rules/part-2-rule-5.html),
  [Rule 5 (club face)](https://www.usga.org/equipment-standards/equipment-rules-2019/equipment-rules/part-2-rule-6.html)
  and [its interpretations](https://www.usga.org/equipment-standards/equipment-rules-2019/equipment-rules/part-2-rule-6-interpretations.html).
- **The contact marker.** When both coordinates are available, a flat, mottled
  contact imprint with incomplete dimple impressions is centred at the
  unrounded reading. It is grey on light faces and pale on the dark hybrid face;
  the grooves remain visible over it. Its fixed, roughly 20 by 22 mm shape is
  illustrative, not a
  measured contact patch or a speed/compression estimate. It is clipped to the
  face and mirrors with handedness. The subdued dashed 1-sigma ellipse retains
  the reading's uncertainty; its size does not control the imprint. An offset-only
  reading keeps the hollow marker and horizontal uncertainty bar, without an
  invented contact patch at zero height. Earlier shots remain faint dots. The
  full-reading centre circle remains an unpainted coordinate anchor.
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
  leading edge or top line. Both axes therefore rest on one reference club per category
  (±3–4 mm), and the photos' scale rests on a groove-length prior (±13 %) until a club's
  groove length is measured.
- **Lens.** The stock lens is uncalibrated and about 16 % barrel at the edge.
- **Live width.** The kiosk reads at 640x400 to keep up. The 1280x800 detail is lost
  to the averaging until the reading is fast enough at full size.
