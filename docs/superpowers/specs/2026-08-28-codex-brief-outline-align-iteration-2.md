# Codex brief — outline alignment, iteration 2 (last iteration on the mesh route)

**Branch:** `feat/clubpose-fit` @ `5b18e98`. The grounded template is now the right shape and
sits on the head in most panels; 10 frames accept at residual 1.16–1.50 px, 31 fail at
1.5–1.8 px, and the sheets show one dominant failure: **the template's sole boundary is pulled
onto the outer edge of the sole shadow**, which has the same polarity (dark inside, lighter
outside) as a real head/mat boundary. A second, smaller one: on f71 the topline locks onto the
ball's bright cap where the two overlap.

Two principled changes, no threshold changes. This is the last iteration on the mesh-template
route regardless of result; if it does not pass, the outline source becomes the learned
segmenter / address photo per the roadmap.

## Changes

1. **Drop the sole from the template.** The sole boundary is (a) occluded by its own shadow in
   every frame and (b) not needed — heel–toe uses the heel and toe extremes, high–low uses the
   topline. Keep template boundary pixels whose outward normal points up (topline), left
   (heel/hosel side) or right (toe); discard those pointing down (|normal_y| dominant and
   downward). State the fraction kept.
2. **Align on the frames before the ball overlaps the head, then carry to contact.** Use f69,
   f70, f71 (rendered at the radar range for each, template boundary within 1 ball-radius + 1 px
   of the ball centre still excluded). Carry each frame's outline to the contact instant with
   the transverse motion of the outline itself (linear fit of (dx, dy) + template centre across
   the three frames, in pixels) and report the ball-to-outline offset at f_contact from each
   frame. This is the OERT method literally: fix-points tracked across frames bracketing the
   radar-timed impact.
3. **Two sanity outputs per frame:** the template's topline row vs the image's brightest ridge
   row in the toe-side columns (a cheap check that vertical placement is not off by the
   shadow), and the template height vs the observed dark-head height in the toe-side column
   band (checks the mesh's face height against the real club; report in px and mm).

## Checks (pre-registered; unchanged thresholds)

| check | pass |
|---|---|
| Availability: f69, f70, f71 all accepted (residual ≤ 1.5 px, support ≥ 0.5, not on the search boundary) | ≥ 17/21 shots |
| Three-frame consistency: the three carried offsets at f_contact agree | spread ≤ 1 px on ≥ 80 % |
| Shot 014 vs hand marks (ball 18–24 mm heel-side of the outline midpoint; 16–19 mm below the topline) | within 1.5 px |
| Sensitivity: loft ±5°, face ±10°, lie ±3° | offset moves ≤ 1 px |
| Sheets for f69/f70/f71 and one carried-to-contact panel per shot, rendered and looked at first | report what they show |

## Deliverables

Same module/script/tests as before, extended; results appended to
`docs/clubface-impact-location.md` ("Outline alignment — iteration 2") with a one-line verdict
and, if it fails, the sentence that the mesh-template route is closed and why.
