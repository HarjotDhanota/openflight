# Agent handoff — OpenFlight impact location

**Read this first, then `2026-08-25-impact-location-status-checklist.md` for the full
status matrix.** Branch: `feat/silhouette-poc`, clean, 135 tests passing.

---

## 1. The single most important thing

**New data arrived that unblocks the project's biggest problem.**

`C:\Users\harjo\Downloads\openflight_session_20260825_181734_filtered\openflight_session_20260825_181734_filtered\`

| | Old capture (all prior work) | **New session** |
|---|---|---|
| Exposure | 997 µs (untouched default) | **298 µs** |
| Analogue gain | 15.94 (100 % of max) | **5.00** |
| Frame clipped at 255 | 26–31 % | **0.2 %** |
| **Impact zone clipped** | **83–94 %** | **~1 %** |
| Shots | 1 | **22** (7-iron and 9-iron) |
| **IWR6843 radar** | **none, ever** | **`.l3dump` for every shot** |

Per shot: `frames.npz` (99 × 320×200, same 467.6 fps, 2.1385 ms), `camera_metadata.json`,
an `.l3dump`, and a 30 fps review MP4. Session-level `shots.csv` has live shot + capture
metadata; `source_logs/` has the session JSONL.

**Already validated against it:** the production ball detector
(`fusion/ball_detect.find_teed_ball`) finds the ball in **7 of the first 8 shots**, at a
consistent **12–13 px diameter**, polarity `light_on_dark`. That independently confirms the
Gate 0 prediction of ~14 px across seven shots, and the polarity flip is gone because the
mat is no longer clipped.

⚠️ `shot_001` still shows 100 % impact-zone clipping — check it before including it.
`shot_004` returned radius 3.22 px against ~6.3 for its neighbours; likely a mis-detection.

---

## 2. What is settled — do not re-derive

- **Gate 0.** Lens is **2.8 mm** (an earlier "6 mm" was *inferred* and wrong), effective
  pitch 6.0 µm in the 2× subsampled 320×200 mode, **`focal_px` = 466.7**, plate scale
  **0.327 px/mm**, ball ≈14 px, camera ≈1425 mm.
- **The capture archive is camera-only** in the old data; the new session adds radar.
- **The trigger is the SEN-14262 ACOUSTIC detector on BCM17, not radar**, and it **lags
  impact by ≥4.7 frames (~10 ms)**. The lag scales with placement distance, so it cannot be
  calibrated out generally. Never treat the trigger frame as impact.
- **Upstream fusion already exists and is merged.** `iwr6843/club.py` (clubhead detection
  and Cartesian path fitting), `camera/club_delivery.py` (camera + IWR depth + OPS speed),
  `camera/ball_flight.py`, `ReferenceBallTracker`. Do not rebuild these.
- **Impact location does not exist anywhere** — no match for `impact_location`,
  `strike_location`, `face_impact`, `gear_effect` in `src/openflight`.
- **Comparators are Trackman 4, Full Swing KIT, Mevo Gen 2.** Never Trackman iO — it is
  ceiling-mounted and its 4,600 fps is not a behind-ball number.
- **No strobe.** Reaffirmed repeatedly. Ambient-light design.

---

## 3. Open bugs, highest value first

### 3.1 `detect_reference_ball` locks onto a false object — **verified**

On the old capture it returns **(182.8, 128.8) diameter 17.0 px** against a hand-measured
ball at **(125.8, 157.4)** — 63.8 px away, on the dark backdrop. **It passes every gate.**
Since `focal_px = diameter_px × range / ball_diameter`, the error multiplies every world
coordinate, and camera pitch is derived from the same false object's `y`.

**Re-test this on the new well-exposed data first** — it may simply have been an
exposure artefact, in which case the finding downgrades.

### 3.2 Optical axis assumed at image centre — ~1.0° of pure yaw

`_pixels_to_world` uses `center_x = width/2`. For the 320×200 crop the axis is at
**(151.75, 124.75)**, from `OV9282_PIXEL_ARRAY_LEFT/TOP = 8` and the ISP offset being in
the decimated domain. The −8.25 px X error is **1.01° of yaw** landing on club path; the Y
error cancels because pitch is derived from `ball_z`.

**Fix, ~5 lines, no calibration:** port the yaw term at `club_delivery.py:650-652` into
`_pixels_to_world`, which currently **discards the ball's azimuth as `_ball_x`** (line 248).

### 3.3 Which capture mode actually runs is unresolved

Code defaults to **640×400** (`server.py:4231`, `capture_runtime.py:49`,
`club_delivery.py:113`), docs say the production experiment should use **320×200**
(`docs/camera/README.md:170`). The new session ran 320×200. **Pin this** — distortion
magnitude differs by 10–40× between the two.

### 3.4 Lens distortion — smaller than first reported

An earlier doc claimed ~2.7° systematic. **That is mode-dependent and wrong for 320×200**,
where the mode reads only the central 640×400 of the array so in-frame radii stay small:
**0.06° at impact-zone radii**, 0.25° at r=70 px, 1.0–2.6° only at the extreme corner.

**Do not ship a datasheet `k1` at 320×200** — if the lens is k₂-dominated it makes things
worse. At 640×400 the ~2.7° figure is roughly right.

### 3.5 Keep the 2.8 mm lens

Ball-in-frame is the binding constraint: **16.6 trackable frames at 2.8 mm → 3.0 at 12 mm**,
while frame rate falls 468 → ~142 fps (rows cost frame time 1:1) and depth of field
collapses to 375 mm total. The 320×200 mode already crops to 37.8° × 24.2° in silicon, so a
narrower lens adds no cropping we do not have. At fixed f-number, focal length does not
change per-pixel exposure.

---

## 4. The pose problem — the actual blocker

The 6-DOF mesh fit (`replay/fit_real.py`) reaches **median IoU 0.547** on real pixels, but
**the recovered orientation is not trustworthy** — Harjot's words: *"it is on the club and
follows it, but the orientation is way off."*

**Two fixes were tried and both failed, informatively:**

1. **Physical bounds** (yaw ±25, pitch 5–55, roll ±35) — the fit pinned **all three** to
   their bounds. Those angles are offsets from the **mesh's normalised frame**, which has an
   arbitrary orientation baked in; they are *not* face angle, dynamic loft and lie.
2. **Temporal smoothness** — froze the pose completely (0.0° change across 11 frames) and
   collapsed IoU 0.547 → 0.181. Softened 6× it gave 0.36 with angles still pinned.

**Root cause of both:** the parameterisation is not anchored to the club's physical frame.

**The recommended fix (Harjot's proposal, and it is right):** match **labelled landmarks** —
toe, heel, hosel junction, leading edge, sole — between mesh and image, rather than scoring
outline-against-outline as an undifferentiated blob. At 20–40 px many orientations project
to nearly the same outline; landmarks break that degeneracy because a toe is not a heel even
when the outlines match. Three or more labelled 2D↔3D correspondences is also exactly what
`cv2.solvePnP` wants, giving a residual and therefore an uncertainty estimate.

**Rejected, correctly, by Harjot:** matching interior shading via the z-buffer. Interior
appearance is club-specific and lighting-dependent, so it would not generalise and would
break the canonical-template goal.

**Known difficulty:** a silhouette boundary is the **extremal contour** — view-dependent
surface points, not fixed mesh vertices. Hosel junction, sole line and leading edge are
comparatively stable; toe and heel extremes migrate as the club rotates.

---

## 5. Working conventions to preserve

- **Overlays draw the model's own output, never hand-rolled visualisation geometry.** The
  club outline is the projected 3D mesh at its fitted pose; the observed silhouette is drawn
  faint behind it for comparison. Drawing the segmentation contour alone looked perfect *by
  construction* and hid every fitting error.
- **Never pad an outline.** A circle at 1.6× the measured radius, or a radius floored to a
  minimum, misrepresents accuracy. If an outline looks wrong, the fit must be wrong.
- **Fail closed.** The club track stops when contrast collapses rather than latching onto
  artefacts (measured: 267–550 px of >30 DN contrast at F67–71, **19–80 px by F73**).
- **Never conclude "X does not exist" from a truncated search.** `grep ... | head -N` once
  produced a false "there is no clubhead tracking" claim that was committed and later
  withdrawn — `iwr6843/club.py` is 38 KB and had been there all along.

---

## 6. Suggested first moves in the new session

1. **Re-run everything against the new well-exposed data.** Ball detection, club tracking,
   the mesh fit. Most prior limits were exposure artefacts and may simply dissolve.
2. **Re-test `detect_reference_ball`** (§3.1) on the new data before treating it as a bug.
3. **Open an `.l3dump`.** Nobody has ever looked at IWR data alongside a camera capture.
   The open question is whether the clubhead is separable from the golfer's body and arms —
   `iwr6843/club.py::find_club` and `estimate_club_path` already exist to try.
4. **Then the landmark pose work** (§4), which is the real blocker for impact location.
5. Still never run: the **A-v3 accuracy re-run** at real settings. Every accuracy number
   this project has produced was simulated at 500 µs and 0.656 px/mm, both wrong. **There is
   no validated accuracy figure for real hardware.**

## 7. Artifacts and key files

- Public page: <https://claude.ai/code/artifact/42a6f3f4-0b9b-4faf-bf9c-1ff45b4e94dd>
  (three earlier pages were retired and now redirect to it)
- `research/silhouette_poc/fusion/ball_detect.py` — production ball detection, 13 tests
- `research/silhouette_poc/replay/make_overlay.py` — frame-by-frame overlay generator
- `research/silhouette_poc/replay/fit_real.py` — 6-DOF mesh fitting on real pixels
- `docs/Personal Research/camera-feasibility-verdict-2026-08.md` — §0.5, §0.6, §1 corrections
- `docs/Personal Research/camera-club-path-aoa-accuracy-2026-08.md` — club path / AoA analysis
- `docs/superpowers/specs/2026-08-25-impact-location-status-checklist.md` — full status
- `docs/superpowers/specs/2026-08-25-ball-placement-and-exposure-readiness.md` — READY-state design
- `docs/superpowers/specs/2026-08-25-fusion-feasibility-question.md` — fusion audit
