# Constrained silhouette pose refit in the corrected frame — results

Branch `research/pose-refit-corrected`. Session
`openflight_session_20260825_181734_filtered`, 21 shots (shot_001 excluded),
8 × 7-iron and 13 × 9-iron. Research only; nothing here is upstream.

## Verdict

**Loft/lie: NOT plausible. Face angle: weak, as expected — but so is everything
else, and for a reason that makes the whole read uninformative.**

0 of 139 fitted frames land inside the physical envelope, on 0 of 21 shots.
That is the same headline as the voided 2026-08-27 run, but it is now a
*diagnosed* failure rather than a broken frame: every frame saturates against
the edge of the search box, and perturbing the camera by ±1° of pitch or
±25 mm of lens height moves the reported angles by **exactly 0.000°**. The
numbers are set by the constraint, not by the silhouettes. This experiment
measures the box, not the club.

The frame corrections themselves are sound and are not what fails — see
"What the corrections did fix".

## Method

Masks and geometry as pre-registered: frames un-mirrored `[:, :, ::-1]`,
background `median(frames[8:56])`, moving `|frame − bg| > max(4σ, 18)`, ball
veto disk `0.75 × diameter`, largest component within 200 px of the ball,
`head_split.split_head` to drop the shaft, frames f62…`floor(f_c)` with
`f_c = pre_trigger − (1.575/343)·fps`, minimum 60 px. Per-frame range from
`fusion.ranges_from_radar`. Fit against `angles.delivered_angles` with
`club_axes(mesh)`. Mesh cache v4, digest `9588c7dee779c4f5`, axes source
`mesh`; `square_pose()` = (179.648, 13.345, 4.522) → loft 33.10, face 0.00,
lie 61.19, sole 0.00.

Reproduce:

```bash
uv run python research/pose_refit/refit_corrected.py --out results/run --stage calib
uv run python research/pose_refit/refit_corrected.py --out results/run --shots 2 3 4 5 6
# ... remaining shots; per-shot JSON is cached so the sweep resumes ...
uv run python research/pose_refit/refit_corrected.py --out results/run --stage aggregate --sensitivity
```

## Camera solved from the ball, and verified before fitting

`projection._rotation_world_to_camera` aims the camera at the world origin, so
the model *must* project the ball to `(cx, cy) = (160, 100)`. It is really at
`(176.6, 146.8)`. Rather than assume "level", pitch is solved per shot in
closed form from the ball's pixel row (`tan p = (kD − h)/(D + kh)`,
`k = (v − cy)/fy`); the ball's lateral position drops out of the row under a
pure pitch, so the row is a clean measurement of pitch alone. The lateral
offset is then solved from the column.

| quantity | solved | independent reference |
| --- | --- | --- |
| camera pitch | **−0.222°** (sd 0.121, range −0.500…−0.069) | level camera predicts ≈ −0.2° |
| lateral offset | **−55.7 mm** (sd 11.1) | taped **−60.325 mm** — 4.6 mm apart |
| camera→ball range | 1577.3 mm | tape chain 1581 mm |

**Ball-projection residual, session-median camera (the pre-fit gate):**

| axis | rms | within 2 px |
| --- | --- | --- |
| **v (row — tests pitch)** | **1.01 px** | **19 / 21** |
| u (column) | 3.40 px | 10 / 21 |

The v gate passes at exactly 19/21. The u axis does not, and it should not be
expected to: the ball's column spans 169.6–180.4 px (37 mm) across the session
because it is re-teed between shots, while its row spans only 3.5 px. A single
session camera cannot track a moving tee in u. That the v axis — which is
untouched by lateral tee position — passes tightly with the same camera, and
that the solved lateral offset lands 4.6 mm from the taped one, is what makes
this a real verification rather than a fit to itself.

**Camera roll is NOT zero.** The net/gravel boundary measures **3.18°**
(range 2.69–3.58 across 21 shots, median-of-slopes and least-squares agree to
0.1°). That is well past 1°, so it is flagged rather than absorbed. Roll is
left at 0 in the fits and priced in the sensitivity table below. Caveat: a
boundary tilt is only a camera roll if that boundary is truly horizontal in
the world; it should be settled with a level or a plumb target, not with this.

World frame used here: **origin on the floor under the ball, +z up (so ground
is z = 0), +x downrange, +y = `WORLD_RIGHT`.** Ball centre `(0, 0, 40)`, lens
at z = 203.2. `projection.py` instead puts the origin at the ball *centre* and
then uses the floor-referenced 203.2 mm as a height above it — a ~40 mm
inconsistency this script avoids by naming the datum.

## The four corrections

1. **Camera** — as above. Solved and verified, not assumed.
2. **Bounds** — coarse grid *and* accept bounds centred on `square_pose()`,
   ±30° yaw/pitch/roll in 10° steps, refinement as shipped. `fit.fit_sequence`
   could not be used directly: its bounds are absolute around the triad origin,
   which is a *backwards* club, so the corrected square pose is out of bounds
   before the search starts.
3. **Hosel clip** — cut at **3.78 mm** along `axes.shaft_local`, 26238 → 21367
   faces, chosen where the perpendicular extent falls from ~58 mm across the
   head to the flat ~12.5 mm hosel-bore plateau. `template_check.png` confirms
   it: the full mesh renders an L (head + hosel limb, 338 px at square pose),
   the clipped template renders the head body alone (263 px) — the same object
   `split_head` leaves on the observed side.
4. **Range** — **the instruction cannot be satisfied, and that is a finding.**
   `club.estimate_club_path` *is* reachable offline from the `.l3dump` and does
   return `range_evidence`, but `BallTrack.quad_bins is None` for club tracks:
   there is no quadratic refit, so `track.range_at` is exactly the linear ramp
   `ranges_from_radar` already applies. The track also spans only
   impact−13.5 ms to impact−0.8 ms, so earlier frames are extrapolation either
   way. Both the full frame set and the ±3-frame near-contact subset are
   reported below.

## What the corrections did fix

| | 2026-08-27 pilot (aimed camera, hosel intact) | after all four |
| --- | --- | --- |
| min clubhead centre height | **−148 mm (underground)** | **+6.4 mm**, 0/21 shots underground |
| fitted dynamic loft | −25.0° | +0.8…+1.1° |
| IoU fitted vs at square | 0.49 vs 0.22 | 0.32 vs 0.19 |
| search saturation | 100 % at ±52° | 100 % at ±30° |

The camera fix is real and it holds: the reconstructed clubhead track now
descends 327 → 30 mm and stays above ground on every frame of every shot. The
hosel clip narrowed the IoU gap between the fitted and square poses without
closing it. Loft moved 26° toward physical. None of it was enough.

## Pre-registered reads

Primary = all frames, sequence fit with shipped smoothness. The **independent**
column drops the frame-to-frame smoothness link, and it is the one to read for
coherence: the shipped `smooth_deg = 70` charges a full unit of IoU for 210° of
change, which over a 5–10 frame run freezes every frame at the first frame's
pose. A spread measured under that penalty reports the penalty, not the club.

| read | all frames | near-contact (±3 fr) | independent | pass? |
| --- | --- | --- | --- | --- |
| frames in envelope | **0 / 139** | 0 / 38 | 0 / 139 | **FAIL** |
| shots with any frame in envelope | 0 / 21 | 0 / 20 | 0 / 21 | **FAIL** |
| within-shot spread, loft | 0.16° | 0.00° | **1.83°** | — |
| within-shot spread, lie | 0.88° | 0.00° | **23.14°** | **FAIL** |
| within-shot spread, sole tilt | 0.95° | 0.00° | **26.72°** | **FAIL** |
| within-shot spread, face angle | 0.30° | 0.00° | **41.14°** | expected weak — confirmed |
| ω = v/r ratio (r = 1.6 m) | **0.00** | 0.00 | 0.00 | **FAIL** (old: 0.39) |
| 7i − 9i loft gap | **−0.30°** | 0.00° | +0.08° | **FAIL** (static gap 7.9°) |
| frames rail-pinned | **100 %** | 100 % | 100 % | **FAIL** |
| mean IoU fitted / at square | 0.319 / 0.194 | 0.299 / 0.175 | 0.328 / 0.194 | square never wins (0/21) |

On the brief's expectation that loft and lie would be tighter than face angle:
**only loft is** (1.83° vs 41.14°), and it is tight for the wrong reason — it
is pinned flat against the box wall at ~1°, not measured. Lie (23.14°) and sole
tilt (26.72°) are *not* meaningfully tighter than face angle. The "±11° flat
direction" for face angle is if anything optimistic here; the independent fit
swings face angle over 41°.

## Why it fails

**The answer is the constraint, not the data.** Sensitivity, refitting shots 2
and 6 with the camera perturbed (median change in the delivered angles):

| perturbation | Δloft | Δface | Δlie | Δsole |
| --- | --- | --- | --- | --- |
| pitch +1° | 0.000° | 0.000° | 0.000° | 0.000° |
| pitch −1° | 0.000° | 0.000° | 0.000° | 0.000° |
| lens +25 mm | 0.000° | 0.000° | 0.000° | 0.000° |
| lens −25 mm | 0.000° | 0.000° | 0.000° | 0.000° |
| roll +3.18° (measured) | +0.15° | +0.30° | +0.89° | −0.95° |

Exact zeros are not a bug. Every frame is saturated at the ±30° corner
(yaw +30, pitch −30, roll −30 from square — the *same* corner for every frame
of every shot), so the IoU optimum lies outside the box and moving the camera
does not move the reported pose. A reproducible gradient like that is a
systematic modelling error, not search noise. Widening the box does not help:
at ±52° in the pilot it ran to ±52°.

**Underneath it is segmentation, as the earlier work concluded.** 139 of 210
candidate frames survive (66 %); all 71 rejections are
`no_head_body_in_component` — the head merges with the shaft and its inscribed
thickness collapses below the `split_head` core threshold as it crosses the
bright mat. Of the frames that do survive, the head mask spans **80–933 px**
(median 393, IQR 238–656) while the true head size barely changes. Against the
square-pose template the render/observed area ratio runs **0.63 at p10 to 3.84
at p90** (median 1.12). Even in shot 17, the cleanest shot with all 10 frames
kept, the mask swings 219–905 px and grows a tail over the shadow past the ball
by f68–f71 — visible in `sheets/shot_017_9-iron_masks.png` and
`sheets/shot_016_9-iron_fit_independent.png`, where the late-frame fits sit on
the shadow rather than the head.

A template fit cannot recover 1° of face angle from masks with 4× area error.
This is the wall named in the earlier findings, and it is upstream of every
geometric correction in this report.

## Production fixes this run justifies

None applied on this branch; all research-local.

1. `src/openflight/camera/clubpose/projection.py:66` —
   `_rotation_world_to_camera` derives orientation by aiming at the world
   origin. That is an assumption, wrong here by 46.8 px in v. The camera needs
   an explicit orientation (pitch, and a roll that is measured, not assumed).
2. `src/openflight/camera/clubpose/projection.py:62` — `camera_center_world`
   hard-codes `y = 0`, dropping the tape chain's −60.325 mm lateral offset that
   `fit.py`'s own docstring records. Solving it from the image gives −55.7 mm.
3. `src/openflight/camera/clubpose/projection.py:46-53` — `CAMERA_HEIGHT_MM` is
   floor-referenced (kiosk `mount_height_m`) but is used as a height above the
   world origin, which is the ball *centre*. ~40 mm inconsistency. Name the
   datum.
4. `src/openflight/camera/clubpose/fit.py:316-318, 416-418` —
   `YAW_BOUND_DEG = 60`, `PITCH_RANGE_DEG`, `ROLL_BOUND_DEG` are absolute
   bounds around the triad origin, which is a backwards club; the corrected
   `square_pose()` at yaw 179.6° is rejected before the search starts. Bounds
   should be relative to a seed pose.
5. `src/openflight/camera/clubpose/fit.py:261-263, 375-377` — the default
   `yaw_grid` / `pitch_grid` / `roll_grid` centre on that same backwards club.
6. `src/openflight/camera/clubpose/fit.py:375` — `smooth_deg = 70` costs one
   IoU unit per 210° of change, which freezes a 5–10 frame sequence at frame
   one's pose. Any within-sequence spread computed under it is an artefact.
7. Template/mask domain mismatch — the cached mesh carries a hosel that
   `head_split.split_head` (`head_split.py:38`) removes from every observation.
   Whatever renders the template must apply the same cut, or IoU pays for
   hiding a limb the observation never had.
8. `src/openflight/iwr6843/tracking.py:118` — `quad_bins` is populated for ball
   tracks (`tracking.py:378`) but is None for club tracks, so club range has no
   curvature model and camera fusion silently gets a straight line.

## Files

- `refit_corrected.py` — the script.
- `results/run/summary.json`, `summary.csv`, `results.json`, `shots/*.json`.
- `results/run/camera_calibration.json`, `camera_calibration.png`.
- `results/run/template_check.png` — hosel clip, before/after.
- `results/run/angles_by_shot.png`, `rotation_rate_vs_omega.png`.
- `results/run/sheets/` — per-shot mask sheets and fit overlays (smoothed and
  independent), 63 sheets.
