# Empirical clubhead outline template

**CONSISTENCY ONLY. No ground truth exists.** Nothing here is scored against a
launch monitor. The only reference is `openflight/contact_marks.jsonl` — the
maintainer's hand marks, pass 1, one human's reading of the same pixels. Every
error below is a consistency figure between a data-built outline and an
annotator, never an accuracy figure. The marks additionally define the
template's crop, its origin and its heel/toe/topline convention, so the two are
not independent even before scoring; leave-one-shot-out limits, but does not
remove, that coupling.

## What this is

The clubhead outline is built **from the session's own masks**, not from the
GrabCAD mesh. It is then placed on every frame of f66–f72 by translation and
roll, with its size pinned by the radar's range ramp, and carried to the
acoustic contact instant.

## Pipeline

`build_template.py`

1. Frames un-mirrored (`frames[:, :, ::-1]`); teed ball from
   `openflight.camera.club_motion.detect_reference_ball`; per-shot plate scale
   `42.67 mm / diameter_px` (millimetres per pixel at the ball's 1581 mm range).
   The detector reproduces the ball recorded in every mark record exactly.
2. Head mask by the `research/session_metrics/swing_plane.py` recipe:
   background median of f8:56, `|diff| > max(4 sigma, 18)`, 3×3 close plus hole
   fill, largest moving component within 200 px of the ball, split by
   `clubpose.head_split.split_head`. **No tee veto**: f66–f72 are at or before
   contact, where the teed ball is stationary and therefore absent from the
   moving mask, while the head sits on it.
3. For each marked `(shot, frame ∈ {71, 72})`, the mask is cropped to the box
   the marks define — x between heel and toe ±2 px, y from the topline row down
   48 mm — translated so the heel–toe midpoint on the topline row is the
   origin, and resampled onto a common grid at `UPSAMPLE = 4` times the 9-iron
   median plate scale, with the frame's own radar range folded in so every
   sample is an outline **as seen at 1581 mm**.
4. Mean of the samples is the **occupancy map**; thresholded at 0.5 it is the
   **outline**. The map's blur is the label/mask noise.

`align_and_carry.py`

5. Per shot S the template is rebuilt with S's own frames **left out**.
6. Per frame, the outline is scaled by `1581 / range_mm(f)` times the
   plate-scale ratio (`clubpose.fusion.ranges_from_radar`, anchored at 1581 mm
   at `f_c = pre_trigger_frames − 1.575/343 × fps ≈ 71.85`), then translated
   and rolled for maximum IoU against the head mask.
7. The midpoint's image-plane motion over f66–f71 is fitted and evaluated at
   `f_c`; ball-minus-midpoint and ball-minus-topline are reported in px and mm.
8. Predicted heel x, toe x, topline row and midpoint at f71/f72 are scored
   against the held-out marks.

## Deviations from the brief, all forced and all measured

* **Local IoU.** The mask is taken only inside the placed outline's own
  bounding box — exactly the box the template was cropped to. A global IoU is
  dominated by the shaft and hosel, which the crop removed from the template
  but which are still in the mask.
* **Global translation search.** The brief's ±12 px window needs a prior, and
  there is none at f66–f70: the head is 20–30 px from the ball there, and the
  marks that would supply one are the test set. The coarse stage is a global
  FFT cross-correlation over every placement, restricted to midpoints within
  55 px of the ball. The ±12 px is re-imposed as a **track gate**: a frame
  whose midpoint sits more than 12 px off the shot's own fitted f66–f71 line
  fails closed.
* **Quadratic carry reported alongside the linear one.** The brief's linear
  carry is the headline and the one the pre-registered gate uses, but its
  residual is 3–6 px because the projected motion decelerates hard as the club
  swings onto the optical axis. The quadratic and the two-point (f71, f72)
  carries are reported for comparison.

## Known limits of the construction

* **Three of the four template edges are the crop, not the club.** The 48 mm
  depth cuts the outline flat at the bottom and the ±2 px heel/toe margin cuts
  it flat at the sides. Only the topline and the heel/toe shoulders carry
  shape, and the measured template height is exactly 48.2 mm for both clubs —
  the crop, not the head, sets it.
* **The head mask is not a head silhouette at every range.** At f66 the head is
  against the dark mat edge and its whole silhouette registers, sole shadow
  included; by f71–f72 it is on the bright mat and only a rim survives the
  threshold before the close-and-fill rebuilds it.
* The roll search reads deviation from the session's mean roll, which the
  brief's translate-and-scale normalisation bakes into the outline.
  `--roll-normalise` de-rotates each build sample by its heel→toe line instead;
  it changes the mean sample IoU by 0.001 (see `RESULTS.md`).

## Running

```bash
export OPENFLIGHT_SESSION=~/Downloads/openflight_session_20260825_181734_filtered/openflight_session_20260825_181734_filtered
uv run python research/empirical_template/build_template.py
uv run python research/empirical_template/align_and_carry.py
```

Flags: `--session`, `--marks`, `--out`, `--exclude-shot` (defaults to 1),
`--shot` (align only), `--roll-normalise`, `--no-sheets`.

## Outputs (`results/`)

| File | Contents |
| --- | --- |
| `template_{brief,roll_normalised}_{7,9}-iron.png` | occupancy map with the outline contour, and the thresholded outline |
| `template_*.npz` | outline, occupancy map, landmarks, reference scale |
| `template_summary.json` | per-club extent, sample IoU, per-shot ball/range/scale |
| `sheets/<shot>.png` | f66–f72: mask green, aligned outline orange (red = failed closed), landmarks as dots, marks as ticks. Native sensor resolution — zoom the file to read it |
| `align_per_frame.csv` | per frame: range, scale, IoU, roll, gate, every landmark and its mark |
| `align_per_shot.csv` | per shot: acceptance, carry fits, ball-minus-midpoint and ball-minus-topline in px and mm |
| `align_summary.json` | all aggregate scores and the pre-registered pass/fail |

**Look at the sheets before quoting any number.**
