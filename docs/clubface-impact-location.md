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
