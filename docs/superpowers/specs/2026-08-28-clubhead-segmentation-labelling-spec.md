# Clubhead segmentation — labelling spec (ML leg), 2026-08-28

**Purpose.** The contact-edge spike (`75d86bf`, `docs/clubface-impact-location.md`
"Contact edges — spike result") showed that hand-written threshold rules cannot separate
*head* from *shaft*, *ball cap* and *sole shadow* at contact, even though the head is
plainly visible on most frames. That is a semantic segmentation problem. This spec fixes
the dataset so that (a) a segmenter can be trained on it, (b) the same truth-free checks
used by the spike can score it, and (c) the heel / toe / topline edges fall out of the
mask boundary with no further rules.

The shipped product stays markerless; labels are a training/measurement artefact.

---

## 1. Source frames

- Session `20260825_181734_filtered`, all shots except `shot_001` (clipped). 21 shots.
- **Un-mirror first**: `frames[:, :, ::-1]`. Every label is on the un-mirrored frame and
  the manifest records `"mirror_corrected": true`. Left = heel/hosel, right = toe.
- Frames per shot: **f60–f78** (club enters the frame ~f62 at this framing, contact at
  f71.85, head still fully visible to ~f78). 19 frames × 21 shots = **399 frames**. Label
  all of them; the post-impact frames are as useful for training as the pre-impact ones,
  and the ball's departure gives a free "ball" class under motion blur.
- Label the raw 320×200 pixels. Never label the 2× tone-mapped `.mp4`.
- Later sessions (driver + wedge, 1280×800 1:1) follow the same contract with a new
  `session` id; do not mix resolutions inside one manifest entry.

## 2. Classes

| id | class | definition (label to the visible edge, not the "true" object) |
|---|---|---|
| 0 | background | mat, netting, tee, everything else |
| 1 | **head** | clubhead from the hosel/ferrule junction to the toe, topline to sole. **Includes** the hosel and ferrule (the mesh has them, `SOURCES.md`). **Excludes** the shaft, the shadow, and the ball. Where the ball's cap overlaps the head, the overlapping pixels are **ball**, not head — label what is in front. |
| 2 | shaft | the shaft from the ferrule up to the frame edge; its motion-blur streak counts as shaft |
| 3 | ball | the ball on the tee and in flight, including blur; when occluded by the head, only the visible part |
| 4 | shadow | the head's cast shadow on the mat, and the darkened mat under the sole. If you cannot tell shadow from sole, the boundary is where the mat texture stops being visible — that is the sole. |

Two rules that decide the edges we care about:
- **Topline** = the head/background boundary along the top. The bright specular ridge is
  *inside* the head; the label edge is where the ridge meets the mat, not the ridge's
  centre.
- **Sole** = head/shadow boundary. The shadow is darker than the head on this mat, so
  the boundary is a dark→darker step; label it even when it is faint. The toe is the
  rightmost head pixel at each row.

Motion blur: the head smears ~3 px along its motion at 300 µs. Label the blurred band as
head to its 50 % intensity point (half-way between head and mat grey). Record the
uncertainty per frame (§4) rather than agonising per pixel.

## 3. Workflow

Tool is your choice; the export contract (§4) is not. Two workable routes:

1. **CVAT + SAM2 interactive** (from the tools survey): point prompts per class per frame,
   propagate along the shot with SAM2 video tracking, correct by hand, export as
   semantic masks. Propagation works well f60→f70; break the track at f71 (head over ball)
   and re-prompt f71–f78.
2. **Scripted SAM2** on the ROCm box: one script that takes point prompts from a small
   JSON, runs SAM2 per class, writes masks; corrections in any paint tool at 8×.

Either way: **label at ≥ 8× nearest-neighbour zoom** (the head is ~30 px), one class at a
time, and view f71/f72 side by side with f70 so the head/ball boundary is labelled
consistently across the contact.

Two annotators (or one annotator, two passes a day apart) on **4 shots** (002, 009, 014,
026 — two per club, spanning the clean and the messy end of the sheet). The inter-pass
disagreement on those frames is the label noise figure; it caps every claim made later.

## 4. Export contract

```
clubhead_labels/
  manifest.json
  <session>/<shot>/f<NN>.png          # uint8, HxW = 200x320, pixel value = class id 0..4
  <session>/<shot>/f<NN>.frame.png    # the un-mirrored raw frame, for audit; byte-identical to frames.npz[NN][:, ::-1]
```

`manifest.json`:

```json
{
  "session": "20260825_181734_filtered",
  "mirror_corrected": true,
  "resolution": [320, 200],
  "classes": {"0": "background", "1": "head", "2": "shaft", "3": "ball", "4": "shadow"},
  "frames": [
    {"shot": "shot_014_9-iron", "frame": 71, "club": "9-iron",
     "annotator": "harjot", "pass": 1, "tool": "cvat-sam2",
     "quality": "clean|blurred|occluded", "notes": ""}
  ]
}
```

Rules: never overwrite a pass — a second pass is a new entry with `pass: 2`; the
comparison between passes is the noise measurement. Masks are lossless PNG. No
resampling. The dataset lives outside git (≈400 × 2 small PNGs; fine in a zip next to
the session export) with the manifest committed to the fork if useful.

## 5. What the labels are for, and how they are scored

**Training.** Hold out **whole shots**, never frames: 4 held-out shots (one clean and one
messy per club), 17 for training. Any lightweight segmenter is fine (a small U-Net at
native resolution is enough for a 30 px object; heavier models buy nothing at 320×200).
Augment with the mirror (labels mirror with it — the class ids are handedness-free) and
brightness/gain jitter only; no geometric warps that change the head's aspect.

**Scoring on the held-out shots — decide thresholds before training:**

| check | metric | threshold |
|---|---|---|
| Boundary accuracy, head class | boundary F-score at 1 px tolerance vs. label | ≥ 0.85; and ≥ the inter-pass agreement, which is the ceiling |
| Topline / toe / heel from the mask | edge positions from the predicted mask vs. from the label mask | median abs error ≤ 1 px, p90 ≤ 2 px |
| Width constancy (truth-free) | hosel-to-toe width per club over all frames f66–f72 | sd ≤ 1.5 px |
| Two-frame consistency (truth-free) | heel/toe/topline shift f71→f72 equal within 1 px | ≥ 80 % of shots |
| Radar area check (truth-free) | head area last/first pre-impact frame vs. the radar 1/r² prediction | within the report's 0.81 ± 0.05 band |
| Availability | head mask ≥ 60 px and connected on f71 and f72 | ≥ 19/21 shots |

Render a contact sheet of predicted masks on f66–f74 for every shot and look at it before
any number is quoted. The failure modes to look for are the same three the spike found:
ball cap absorbed into head, shadow absorbed into head, shaft/hosel boundary wandering.

**Not in scope of this spec:** converting edges to millimetres on the face (needs the mesh
outline→face-centre offset — see the template-alignment brief), and any accuracy claim
against a reference instrument.
