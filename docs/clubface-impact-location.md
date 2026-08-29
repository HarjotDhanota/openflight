# Clubface impact location: status and how to help

An investigation into measuring clubface impact location and face angle from
the existing hardware — the single behind-ball OV9281 camera plus the OPS243
and IWR6843 radars, ambient light, no markers on the ball or club.

## Read this first

**[Technical report](clubface-impact-location-report.md)** — the full assessment:
what is measured, what is not, and what would resolve the rest. States every
retracted claim alongside what replaced it. The
[web version](https://claude.ai/code/artifact/c8817c34-c3ea-4455-9700-cf5a4e238b75)
carries eleven figures: real frames with the model’s own projections overlaid.

**[Fusion status, frame by frame](https://claude.ai/code/artifact/ab9f69dd-de06-4335-83b1-29e3e29ee6b9)**
— two real shots with the model's own projections overlaid, nothing padded.
Thirty seconds of stepping through it conveys the state faster than any prose.

**[Full working log](https://claude.ai/code/artifact/42a6f3f4-0b9b-4faf-bf9c-1ff45b4e94dd)**
— the chronological record, corrections applied in place, for tracing how any
conclusion was reached.

## Where it stands, in three lines

- **Validated:** ball detection (21/22), impact timing (camera and radar agree
  to 0.66 frames), camera attitude (measured, not assumed), and the fused
  radar+camera clubhead velocity, which matches the OPS243's independent club
  speed with a mean ratio of 0.970 (sd 0.029, spread 0.941–1.015).
- **Not yet working:** clubhead orientation. Face angle, dynamic loft and
  impact location remain model-dependent inferences with no accuracy figure
  against truth.
- **Why:** the first 5° of face angle change the projected silhouette by zero
  pixels; one pixel of segmentation error is worth about 10° of face angle;
  and the club is segmentable for only ~10 pre-impact frames, of which the
  current extractor keeps 3–5, against a four-parameter fit.

## Where help is most valuable

**Contributing a capture is the most useful thing you can do**, and you no
longer need anyone else's data to do it — see *Running it on your own device*
below. The current session is 21 shots of 7-iron and 9-iron from a single rig,
thin enough that several tests cannot discriminate.

1. **A session recorded alongside a Trackman.** Nothing here has been scored
   against a reference instrument, so no accuracy figure exists for any club
   metric. This is the single measurement that would change that.
2. **Clubhead segmentation.** Extracting more of the ~10 frames the club
   appears in roughly doubles the observations per shot. The masks currently
   come from a hard background-difference threshold.
3. **A capture at 1280×800 1:1** (doubles plate scale at the same frame rate)
   **and across a wide club-speed range** (a driver and a wedge; the existing
   session is 7-iron/9-iron with no speed overlap, which starves several
   discriminating tests of power).

## Running the code

The library lives in `src/openflight/camera/clubpose/`, with its tests in `tests/`,
whose `README.md` maps every script to the question it answers. Per-shot
result JSONs are committed so conclusions can be re-analysed without repeating
fits that cost ~25 minutes per arm.

### Running it on your own device

Two inputs are not in git, and both fail closed with instructions when absent.

**One-time setup — the club mesh.** Every analysis run projects the 7-iron
model, so this is needed whichever capture you use. It is a GrabCAD community
model used as local research truth and is **not redistributed**;
`src/openflight/camera/clubpose/meshes/SOURCES.md` records the source link, expected
SHA-256, and licence position, and you fetch your own copy under GrabCAD's
terms:

```bash
uv run python \
    scripts/analysis/download_club_mesh.py --local-iron <path-to-STL>
```

**Then your own captures.** The library takes frames and a mesh; it has no
opinion about where your data lives. A session recorded by `start-kiosk.sh`
already contains everything needed — the camera `frames.npz` and the IWR6843
`.l3dump` per shot — and `openflight.iwr6843.replay.inputs_from_session`
resolves those paths straight out of the session JSONL.

Both the camera and the IWR6843 must be enabled while capturing; a shot
missing either one cannot be fitted.

The reference **capture session** used throughout the report is
available from the maintainer if you want to reproduce its exact numbers. Your
own export works for everything else. The **7-iron mesh** is fetched from
GrabCAD (local research use only, no redistribution);
`src/openflight/camera/clubpose/meshes/SOURCES.md` has the provenance, hashes, and
download script.

Deliberately excluded from this branch: the superseded synthetic-phase
evaluation, the old web studio, and the June–July simulation studies. They
remain on the fork's `feat/silhouette-poc` branch for archaeology.

## Contact edges — spike result

Session `20260825_181734` was replayed after un-mirroring every capture, excluding
clipped `shot_001`, and using f71/f72 around the measured contact time. Ball location
and diameter came from `club_motion.detect_reference_ball`; each JSON record carries
the per-shot plate scale `42.67 / diameter_px`. The output sheets were rendered at 8×
and inspected before the results below were recorded.

| Pre-registered check | Result | Threshold | Outcome |
|---|---:|---:|---|
| Width constancy, 7-iron | 28.827 ± 3.892 px over 8 returned frames | sd ≤ 1.5 px | **FAIL** |
| Width constancy, 9-iron | 25.770 ± 1.094 px over 3 returned frames | sd ≤ 1.5 px | PASS numerically, but sparse |
| Width constancy, both clubs | 7-iron failed; 9-iron passed on 3 frames | both clubs pass | **FAIL** |
| Two-frame consistency | 1/21 shots (4.8%) | `|Δtoe − Δheel| ≤ 1 px` on ≥ 80% | **FAIL** |
| Hand-mark agreement, shot 014 | largest returned-edge difference 6.333 px; f72 heel unavailable | every edge within 1.5 px | **FAIL** |
| Availability | 3/21 shots returned all three edges in both frames | ≥ 17/21 | **FAIL** |
| Sheet inspection | many closed failures and several visibly wrong locks | inspect all f71/f72 overlays | **FAIL** |

The width outliers more than 3 px from their club median were shot 005 f71
(33.142 px), shot 006 f71 (25.108 px), and shot 009 f71 (21.315 px). The
shot-014 comparisons were: f71 heel 160.644 versus 165, toe 187.649 versus 191,
and topline row 142.093 versus 141; f72 heel unavailable, toe 183.667 versus 190,
and topline row 142.209 versus 140. These hand marks are only a sanity anchor,
not truth.

| Shot | f71 status | f72 status | Δtoe − Δheel (px) | Δtopline (px) |
|---|---|---|---:|---:|
| 002 | ok | ok | -2.818 | 0.371 |
| 003 | ok | ok | -0.118 | -0.223 |
| 004 | `topline_insufficient_points` | `topline_insufficient_points` | — | — |
| 005 | ok | `topline_insufficient_inliers` | — | — |
| 006 | ok | `shaft_insufficient_pixels` | — | — |
| 008 | `topline_insufficient_inliers` | `topline_insufficient_inliers` | — | — |
| 009 | ok | ok | 7.236 | -0.513 |
| 011 | `topline_insufficient_points` | `topline_insufficient_points` | — | — |
| 014 | ok | `shaft_insufficient_inliers` | — | — |
| 015 | `topline_insufficient_points` | `topline_insufficient_points` | — | — |
| 016 | `topline_insufficient_points` | `topline_insufficient_points` | — | — |
| 017 | `topline_insufficient_inliers` | `topline_insufficient_inliers` | — | — |
| 018 | `topline_insufficient_inliers` | ok | — | — |
| 020 | `topline_insufficient_points` | `topline_insufficient_inliers` | — | — |
| 021 | `topline_insufficient_inliers` | `topline_insufficient_inliers` | — | — |
| 023 | `topline_insufficient_inliers` | `shaft_insufficient_inliers` | — | — |
| 024 | `topline_insufficient_points` | `topline_insufficient_points` | — | — |
| 025 | `topline_insufficient_inliers` | `topline_insufficient_inliers` | — | — |
| 026 | `topline_insufficient_inliers` | `topline_insufficient_points` | — | — |
| 028 | ok | `shaft_insufficient_pixels;toe_insufficient_crossings` | — | — |
| 029 | `topline_insufficient_points` | `topline_insufficient_points` | — | — |

The first sheets showed the topline fit locking onto the diagonal shaft highlight.
Restricting the robust fit to the unobscured toe-side ridge removed that specific
swap. The final sheets still show the cyan line following the bright ball cap or
an internal head highlight in accepted shots, heel intersections landing on the
wrong junction, and toe crossings stopping on internal face detail. Shot 014 f71
is representative: its heel is left of the marked shaft/topline junction and its
toe crossings have a 9.952 px row spread. Many other frames fail closed even though
a blurred ridge is visible.

Because those visible ridges and shafts were being rejected, one complete diagnostic
rerun lowered the topline gate from `max(205, mat + 50)` to `max(190, mat + 35)` and
the shaft gate from `max(210, mat + 55)` to `max(195, mat + 40)`. Availability rose
only to 5/21, two-frame consistency remained 1/21, and the sheets admitted more ball-cap
and internal-highlight locks. The change was rejected; the table above is from a fresh
full replay with the specified thresholds restored.

**Verdict: not findable to ±1 px on this session with these three automatic edge
detectors.** This is a consistency and availability result only; no accuracy claim
against truth exists.

## Outline alignment — spike result

Session `20260825_181734` was replayed after un-mirroring every capture and excluding
`shot_001`. The fixed 690CB 7-iron outline used the measured camera, the ball ray,
radar range ramp, shaft-derived lie, and fused-attack loft prior; only its image-plane
translation was searched. The same 7-iron mesh was used for the 9-iron shots, so their
few-millimetre head-shape difference is a known bias. No threshold or search-range
setting was changed after seeing the data.

| Pre-registered check | Result | Threshold | Outcome |
|---|---:|---:|---|
| Two-frame consistency | 0/21 shots evaluable and within 1 px | ≥ 80% within 1 px | **FAIL** |
| Shot 014 vs hand marks | neither frame passed the alignment gates | both offsets within 1.5 px | **FAIL** |
| Availability | 0/21 shots passed in both frames | ≥ 17/21 | **FAIL** |
| Loft sensitivity ±5° | unavailable: no nominal alignment was accepted | offset moves ≤ 1 px | **FAIL** |
| Face-angle sensitivity ±10° | unavailable: no nominal alignment was accepted | offset moves ≤ 1 px | **FAIL** |
| Lie sensitivity ±3° | unavailable: no nominal alignment was accepted | offset moves ≤ 1 px | **FAIL** |
| Accepted residual distribution | unavailable: 0 accepted frames | median ≤ 1.0 px | **FAIL** |
| Sheet inspection | systematic wrong locks and closed failures | inspect every f71/f72 overlay at 8× | **FAIL** |

The f71 sheet contained 16 `support_below_half`, two `search_boundary`, one
`residual_above_1_5`, and two `nominal_pose_failed` results. The f72 sheet contained
13 `support_below_half`, six `search_boundary`, and two `nominal_pose_failed` results.
In both sheets, the candidate outline commonly sits below or beside the visible head:
its lower arc follows the sole shadow, its hosel boundary crosses the shaft or ball,
and several translations stop on the ±12 px boundary. Shot 014 follows the same
sole/shadow structure rather than the hand-marked head outline. Shots 020 and 025 fail
closed before rendering because their accepted fused attack values produce dynamic
lofts of 2.12° and 58.33°, outside the physical pose envelope. Lowering an edge gate
would admit more of the visible shadow and internal highlights, so the pre-registered
settings were retained and no check-directed tuning run was made.

**Verdict: the translation-only outline is not alignable to ±1 px on this session.**
This is a repeatability and fail-closed result, not an accuracy claim against truth;
no truth reference exists.

**Correction, 2026-08-28:** the result immediately above is void. Its normalized
mesh was mirrored relative to the world frame and its `square_pose()` solution
left the sole rolled about
73°, producing a 22 × 39 px upright template beside a roughly horizontal head.
The 0/21 result therefore measured a bad coordinate frame, not image-edge
localisability. The earlier fused-pose envelope verdict used the same frame and
is void for the same reason. The recorded run remains here for provenance only.

## Grounded outline alignment — corrected rerun

The pinned STL is now explicitly reflected into the world frame at load, by
flipping local z and reversing triangle winding.
The striking-face heel–toe axis supplies a horizontal sole constraint, with the
heel toward world −y; a virtual catalogue-lie shaft driven by the observed image
shaft replaces the mesh's suspect 76° hosel axis as the pose reference. On shot
014 f71, the unchanged ball-ray/radar-range render moved from a 22 × 39 px upright
shape beside the club to a wide, grounded head whose sole and hosel sit on the
real head and shaft with no image search.

**Amended, 2026-08-28:** the reflection is right; its original explanation was
not. That explanation recorded the 690CB source as left-handed. It is not: the
source is a RIGHT-handed club, exactly as its file label says. The frame is the
left-handed thing. `projection._project` maps world +y to the image RIGHT, where
a physical camera behind the ball looking downrange with +z up would put world
−y — the projector's (right, down, forward) basis satisfies right × down =
−forward. That convention is deliberate: with +y on the image right, a positive
face angle is an open face and a positive club path is in-to-out for a
right-handed golfer. The cost is that a right-handed club loaded unchanged
renders as its own mirror image. So the correct statement is **source
right-handed; mirrored at load into the left-handed world frame (y = image
right)**, and the `handedness` field on the source registration and in the asset
manifest is the load-time reflection flag rather than a claim about the CAD.
Nothing about the mirroring, the frame, or any measured result above changes;
only the label does. The frame itself is now pinned by
`tests/test_clubpose_camera_center.py::TestWorldFrameHandedness`.

**Amended, 2026-08-29:** the two facts the old `handedness` field confused are
now separate fields, and right-handed is the explicit default. `club_handedness`
records which club the CAD depicts; `reflect_into_world_frame` records whether
the geometry still has to be reflected, which is true for every physical model
of either handedness. The mesh cache carries both behind a version guard, and a
v3 cache is migrated rather than reinterpreted. Behaviour is unchanged: the
right-handed asset is still reflected at load and the no-search shot 014 f71
render above is pixel-identical, which a test now holds by mask hash. A
left-handed 690CB is registered for left-handed golfers so they are fitted
against a left-handed model instead of a mirrored right-handed one; it has not
been imported, and `square_pose(handedness="left")` refuses rather than guessing
until it is.

The full 21-shot replay then used exactly the previous alignment thresholds,
support/residual gates, ±12 px search, camera, range, and pre-registered checks.
The 690CB 7-iron mesh was again used for the 9-iron shots, retaining the known
few-millimetre head-shape bias.

| Pre-registered check | Corrected result | Threshold | Outcome |
|---|---:|---:|---|
| Two-frame consistency | 0/21 shots; none passed both frames and was evaluable | ≥ 80% within 1 px | **FAIL** |
| Shot 014 vs hand marks | f71 rejected; f72 midpoint error 10.0 px and topline error 3.5 px | both offsets within 1.5 px | **FAIL** |
| Availability | 0/21 shots passed in both frames | ≥ 17/21 | **FAIL** |
| Loft sensitivity ±5° | maximum 2.062 px over 16 returned variants | offset moves ≤ 1 px | **FAIL** |
| Face-angle sensitivity ±10° | maximum 1.601 px; one variant unavailable | offset moves ≤ 1 px | **FAIL** |
| Lie sensitivity ±3° | maximum 8.860 px; four variants unavailable | offset moves ≤ 1 px | **FAIL** |
| Accepted residual distribution | median 1.369 px over 8 accepted frames | median ≤ 1.0 px | **FAIL** |
| Sheet inspection | corrected shape, but sole-shadow locks and closed failures remain | inspect every f71/f72 overlay at 8× | **FAIL** |

The corrected f71 sheet has two accepted frames, 13 `residual_above_1_5`, three
`support_below_half`, one `search_boundary`, and two out-of-envelope
`nominal_pose_failed` results. The f72 sheet has six accepted frames, six
`residual_above_1_5`, five `search_boundary`, two `support_below_half`, and the
same two nominal-pose failures. No shot has both frames accepted. The gross
shaft/shadow swap from the mirrored, rolled template is gone: the hosel now runs
along the real up-left shaft and the head is horizontal. The remaining wrong
locks pull the lower outline onto the sole shadow, while several toe/ball-side
boundaries stop at the search limit. Shots 020 and 025 still fail before render
because their fused attack priors imply physical-envelope violations. These
visual failures did not motivate a threshold change; no tuning run was made.

**Verdict: correcting handedness and grounding fixes the template geometry, but
the unchanged translation-only alignment still does not localise the outline to
±1 px on this session.** This is a repeatability and fail-closed result only;
there is still no truth reference and no accuracy claim.

## Outline alignment — iteration 2

The final mesh-template replay removed every boundary sample whose outward image
normal was downward-dominant (`normal_y > 0` and
`abs(normal_y) > abs(normal_x)`). It retained a median 81.0% of candidate
boundary samples (79.8–82.2% across the 57 templates that rendered). The ball
overlap exclusion, polarity bins, 80 edge threshold, ±12 px search, 1.5 px
support distance, 0.5 minimum support, and 1.5 px maximum residual were all
unchanged.

Each shot was aligned independently at its measured radar range on f69, f70,
and f71. The intended contact carry fits linear image motion to the three
aligned template centres and carries each observed outline to f71.85. Because
not one individual frame passed the fixed gates, every shot failed closed before
the three-frame motion fit and no contact offset was produced.

| Pre-registered check | Iteration-2 result | Threshold | Outcome |
|---|---:|---:|---|
| Availability | 0/21 shots had f69, f70, and f71 accepted | ≥ 17/21 | **FAIL** |
| Three-frame carried consistency | 0/21 evaluable (0.0%); all spreads unavailable | spread ≤ 1 px on ≥ 80% | **FAIL** |
| Shot 014 vs hand intervals | unavailable because all three frames were rejected | within 1.5 px of 18–24 mm heel-side and 16–19 mm below topline | **FAIL** |
| Loft sensitivity ±5° | unavailable: no nominal three-frame fit | offset moves ≤ 1 px | **FAIL** |
| Face-angle sensitivity ±10° | unavailable: no nominal three-frame fit | offset moves ≤ 1 px | **FAIL** |
| Lie sensitivity ±3° | unavailable: no nominal three-frame fit | offset moves ≤ 1 px | **FAIL** |
| Sheet inspection | wrong shaft/hosel locks, displaced head outlines, and closed failures; no carried outlines | inspect f69/f70/f71 and contact sheets first | **FAIL** |

The failure reasons were f69: nine `support_below_half`, eight
`search_boundary`, two `residual_above_1_5`, and two `nominal_pose_failed`;
f70: nine `residual_above_1_5`, six `support_below_half`, four
`search_boundary`, and two `nominal_pose_failed`; f71: 13
`residual_above_1_5`, five `support_below_half`, one `search_boundary`, and two
`nominal_pose_failed`. Thus zero of 63 attempted frame alignments was accepted.

The four rendered sheets were inspected before recording these numbers. Removing
the sole deletes the dense lower boundary, but the remaining cyan side/diagonal
samples still commonly lock onto the shaft or hosel rather than the head. At f69
many outlines are visibly displaced and eight translations reach the fixed
search boundary. At f70 and f71 the outline is often near the head but is still
rejected by the unchanged residual or support gate; in other panels its topline
lands on a ball highlight or head detail. The contact sheet contains only the
real frames and ball circles because every three-frame input is incomplete.

The toe-band sanity output supports the visual result: the signed template
topline-minus-brightest-ridge median is 2.0 px (7.0 mm at each shot's plate
scale), while median template height is 14.0 px / 49.4 mm versus 7.0 px /
26.5 mm for the observed contiguous dark-head run. Per-frame pixel and
plate-scale millimetre values remain in `outline_align.json`; they are diagnostics,
not truth measurements. The 690CB 7-iron template was also used on the 9-iron
shots, preserving the already-declared head-shape bias.

**Verdict: the outline is not localisable to ±1 px by this final
mesh-template iteration. The mesh-template route is closed because no f69/f70/f71
triple passes the fixed alignment gates; the next outline source must be the
learned segmenter or an address photo.** No accuracy claim against truth is made,
because no truth reference exists.
