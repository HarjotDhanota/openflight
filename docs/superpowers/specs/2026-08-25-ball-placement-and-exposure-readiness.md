# Ball placement zone + per-shot exposure readiness

**Status:** design agreed 2026-08-25, not implemented.
**Origin:** the first real OpenFlight capture with metadata (`frames.npz`, 2026-08-24).
**Constraint carried forward:** **no strobe.** Reaffirmed twice by the maintainer. Everything
here is an ambient-light design. The strobe question stays deferred, not cancelled.

---

## 1. Why this exists

The first real capture was taken at the shipped default exposure — `capture_runtime.py`
line 54, `exposure_us: int = 1000`; the archive recorded **997 µs**. It was never
calibrated. Consequences measured from that file:

| Symptom | Measured |
|---|---|
| Hitting zone clipped at 255 DN | **~49 %** (the repo's own `exposure_quality()` trips at 8 %) |
| Ball vs. its background at address | **−62 DN — the ball is DARKER than the mat** |
| Ball vs. background in flight | **+141 DN** — contrast *inverts sign* within one shot |
| Ball radius, address vs. flight | 6.86 px vs 13.97 px — **2× apart**, because the ball's lit top merges into the clipped mat |

The repo already ships everything needed to prevent this — `exposure_quality()`,
`scripts/hardware-test/calibrate_camera_exposure.py`, a live preview with an alignment
guide, live exposure/gain controls that apply without stopping the ring buffer, and a
docs line reading *"Clipped ball and club pixels cannot provide reliable centroids"* —
and none of it was in the loop. **This design closes that loop automatically.**

## 2. The workflow

```
PLACE_BALL  ->  MEASURING  ->  READY  ->  ARMED  ->  (shot)
     ^                            |
     +----------------------------+   continuously re-evaluated, never latched
```

1. **PLACE_BALL** — the live preview draws a hitting box. The golfer puts a ball in it.
2. **MEASURING** — with the golfer *at address* (their body and shadow change the light in
   the hitting zone, so this cannot be done on an empty mat), the system sets exposure and
   measures the ball.
3. **READY** — all named checks pass; the unit says so on screen.
4. **ARMED** — exposure drops to the short capture value; ring buffer live.

## 3. Design decisions

### 3.1 A box, not a point

Sized from **the radar's requirements, not the camera's**. The camera does not need a
pinpoint, because the system *measures* rather than assumes: ball diameter gives per-shot
plate scale, ball position gives geometry, and the pose fit solves full 6-DOF regardless.

For a generous 300 × 200 mm box at the measured 0.327 px/mm:

| | Effect |
|---|---|
| ±150 mm lateral | ±49 px — ball stays within the central third of the frame |
| ±100 mm depth | plate scale varies ±7 %, **and it is measured each shot**, so it self-corrects |

An earlier draft of this design proposed constraining the ball to the optical axis to
dodge the lens's −17 % barrel distortion. **That is withdrawn.** It solved a missing
calibration by constraining the user. Shoot the checkerboard, correct `k1`/`k2`, and the
box can be generous. Forcing an unnatural ball position also changes the golfer's stance
and swing — measuring a compromised swing precisely is worse than measuring a natural one
slightly less precisely.

### 3.2 Depth is communicated by apparent SIZE, not by position

The camera sits ~200–300 mm up looking near-horizontally, so **depth barely maps to
vertical image position**. That 300 × 200 mm box projects to roughly **98 px wide by only
10–14 px tall**. Drawing the literal projected rectangle produces a thin horizontal strip
that reads as a far tighter constraint than it is.

Instead: a wide lateral box for left–right, and a *too far / good / too near* state driven
by measured ball diameter — 7 % size change per 100 mm of depth, against a handful of
pixels of vertical movement. Much stronger signal, and it is computed anyway.

### 3.3 Two exposures, because the ball is stationary and the club is not

- **At READY**, take a *longer* exposure. The ball is not moving, so long exposure costs
  nothing and buys a clean edge and an accurate radius — the per-shot plate scale.
- **At ARMED**, use the *shortest* exposure that keeps club-vs-background contrast above a
  floor. Blur minimised where it actually matters.

`capture_runtime.py::update_image_controls(exposure_us, gain)` already applies both
without stopping the ring buffer.

The armed rule is **minimise exposure subject to a contrast floor** — not "make a
well-exposed image". Those coincide only by accident, and exposure converts directly into
motion blur, which is what costs accuracy.

### 3.4 Exposure targets the ball's EDGE, not its brightness

The naive rule — "expose so the white ball sits just below clipping" — fails here, because
at address the ball reads **192 DN against a 255 DN mat**: darker than its own background.
Exposing "for the white ball" would push the mat further into clipping.

What is measured is the ball's *edge*, and an edge needs **both sides in range**. The
criterion is contrast across the ball boundary, not the ball's level.

### 3.5 Apparent diameter is also a free integrity check

If the ball is in the zone but reads 11 px where nominal is 14, something changed — the
unit was bumped, or set up further back than last session. That is exactly the silent
error that would otherwise corrupt an entire session unnoticed. Costs one comparison.

### 3.6 Named, fail-closed readiness

Matching the project's existing convention. READY requires all of:

- ball found inside the zone
- ball edge unclipped on **all** sides (not merely "ball detected")
- contrast across the boundary above floor
- armed exposure within the blur budget
- measured diameter within tolerance of nominal

Any failure shows its reason on screen. **"Not ready" beats silently capturing an
unusable shot.**

### 3.7 Re-verify at trigger; never trust the latch

Golfers waggle, re-address and nudge the ball. Take the ball position from the **last
pre-swing frame**, not from the moment READY lit up.

### 3.8 The club needs its own calibration

The ball says nothing about whether the *club* will be well exposed — measured clubhead
brightness swings 138 → 253 DN during a single downswing as the face turns toward the
light. Either carry the setting from the previous shot, or calibrate on a practice swing.
TrackMan does the latter (it asks for a few ball-less swings to establish the hitting
plane), so the interaction is already familiar to golfers.

## 4. Comparator evidence

| System | Ball placement | Source |
|---|---|---|
| **TrackMan 4** | Ball must be placed **inside a blue square** during indoor calibration. Ball-to-screen distance is a measured input — *"rounding to the nearest metre introduces consistent carry errors."* Prompts for ball-less swings to establish the hitting plane. | **[MAN]** [TM4 Indoor Manual Target Calibration](https://support.trackmangolf.com/hc/en-us/articles/36510759333275-TM4-Indoor-Manual-Target-Calibration) |
| **Mevo+** | Placed 8 ft behind the ball; the FS Golf app *"provides a visual guide using the device's built-in camera, displaying a graphic of the golf ball, target line, and hitting area."* | **[MAN]** [Setting up your Mevo+](https://flightscope.com/blogs/news/setting-up-your-mevo) |
| **SkyTrak+** | Projects a **red laser dot** for ball placement, plus status indicators for readiness. | **[PRESS]** [review](https://breakingeighty.com/skytrak-plus-review) |

All three constrain ball placement and signal readiness. This is standard practice at the
top of the market, not an OpenFlight compromise.

**Placement distance — OpenFlight sits closer than both comparators:**

| | Distance behind ball |
|---|---|
| Mevo+ | 8 ft |
| TrackMan 4 | 7 ft recommended, 6 ft minimum |
| **OpenFlight** | **3–5 ft** (README), measurement implies **4.7 ft** |

Closer is an advantage for the camera — at 8 ft the same 2.8 mm lens would give an 8 px
ball instead of 14 — but it makes plate scale proportionally more sensitive to placement
depth, which §3.1 absorbs by measuring per shot.

## 5. What this does and does not buy

**Does:** removes the uncalibrated-exposure failure entirely; turns the detector's
`tee_region` from a hardcoded guess into a product guarantee; raises per-frame ball
detection well above the 20–45 % measured on the uncalibrated capture; solves multi-ball
by construction; makes errors systematic (calibratable) rather than random; fails loudly.

**Does not:** reduce motion blur. Whether the residual blur at a properly calibrated
exposure is survivable is a separate open question, answered by re-running the A-v3 arm at
the real exposure and the real plate scale.

Making the armed rule *"shortest exposure meeting the contrast floor"* has one further
benefit: when the unit **cannot** meet its blur budget at adequate contrast, that is a
measured, quantitative statement that ambient light is insufficient in that bay. It turns
the deferred strobe question from a judgement call into a number the unit reports.

## 6. Open before implementation

1. **Radar beamwidth** sets the box size. OPS243 and IWR6843 patterns need checking; this
   is not a camera question and it is the binding constraint.
2. **Checkerboard calibration** for `k1`/`k2` — the 2.8 mm lens is < −17 % TV distortion
   and no calibration exists anywhere in the repo.
3. **Illumination falloff** across the box — the capture showed inverse-square falloff
   from a near-field source, so edge-of-box lighting differs from centre. Per-shot exposure
   should absorb it; worth confirming.
4. **Nominal ball diameter** for §3.5 must come from a calibrated capture, not from the
   over-exposed one.
