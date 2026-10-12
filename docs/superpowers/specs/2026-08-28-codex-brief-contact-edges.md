# Codex brief — clubhead contact-edge finder (spike), 2026-08-28

**Branch:** `feat/clubpose-fit` (upstream main + the `clubpose/` package). 113 clubpose +
club_delivery tests pass on Windows. Work here; do not touch `feat/silhouette-poc`.

**Question this spike answers:** in the two camera frames that bracket contact, can the
clubhead's **topline**, **heel** and **toe** be located automatically to about ±1 px, on
all 21 usable shots of session `20260825_181734`? Output is an answer with evidence, not
a feature. Nothing gets wired into the shot pipeline.

**Why it matters:** coarse impact location (heel/centre/toe × high/centre/low) needs only
the ball position (known to 0.2 px), the contact instant (known), the plate scale (known
per shot from the ball diameter) and these three edges. It does **not** need the 6-DoF
silhouette pose fit — face angle enters as cos θ (0.5 mm at 10°), loft as 1/cos L
(0.7 mm for ±5°). The edges are the only unbuilt, unproven part.

---

## 0. Facts you must not re-derive (all measured; see `docs/clubface-impact-location-report.md`)

- Frames in `frames.npz` are **stored mirrored**. Un-mirror with `frames[:, :, ::-1]`
  before anything. After that the golfer (right-handed) is on the LEFT, the shaft enters
  from the upper left, **left = heel/hosel side, right = toe**.
- Frames are 320×200 mono, 467.6 fps (`camera_metadata.json: delivered_fps`), 99 frames,
  trigger at frame `pre_trigger_frames` (74 on every shot).
- **Contact instant** = `trigger − 1.575 m / 343 m/s × fps` = frame **71.85** on this rig.
  (The 1.575 m is the tape ball-to-unit distance; `acoustic.py` is not on this branch —
  inline the formula.) The bracketing frames are `floor` and `ceil` of that: **f71, f72**.
  Contact lasts ~0.45 ms, so at f72 (+0.3 ms) the ball has moved ≤ 1 px; by f73 it is
  airborne. Do not "verify" contact by watching the head reach the ball — the head sits
  beside the ball for 2–3 frames before it strikes.
- **Ball**: `openflight.camera.club_motion.detect_reference_ball(frames)` → `x, y,
  diameter_px` (≈12 px). Plate scale per shot = `42.67 mm / diameter_px` ≈ 3.4–3.6 mm/px.
  Never use a range constant for scale.
- Exposure 247–298 µs; the head moves ~3 px along its motion during exposure. The mat is
  mid-grey (~150 DN), the head body dark (~60–90 DN), the topline/shaft highlights
  210–255 DN. `shot_001` is 99.8 % clipped — **exclude it**.
- **A whole-blob background-difference mask of the head, near contact, swallows the sole
  shadow and the motion blur** and its centroid drifts ~20 px downward. That approach was
  tried and produced an impossible −85 mm "impact height". Do not extrapolate a blob
  centroid across the approach. This spike finds three specific edges in two frames.
- Camera sees the **back** of the head at contact (the head is between camera and ball);
  the ball's bright cap overlaps the topline in columns within ~1 ball radius of the ball.

Session export: `~/Downloads/openflight_session_20260825_181734_filtered/
openflight_session_20260825_181734_filtered/` (`shots.csv` + `shots/<shot>/frames.npz`
+ `camera_metadata.json`). Take `--session` and `OPENFLIGHT_SESSION`; fail closed with a
message if absent.

## 1. Hand-marked reference, shot_014_9-iron (un-mirrored pixel coords)

Read from a gridded 12× zoom. Use as a sanity anchor, not as truth.

| frame | heel x (shaft/topline junction) | toe x | topline row at the ball |
|---|---|---|---|
| f71 | 165 | 191 | 141 |
| f72 | 163 | 190 | 140 |

Ball on that shot: (171.4, 145.5), 11.94 px. Implied outline width 26–27 px ≈ 95 mm
hosel-to-toe; ball centre 18–24 mm heel-side of the outline midpoint, 16–19 mm below
the topline.

## 2. The three detectors (starting point — improve if the sheets say so)

Work in a window around the ball: x ∈ [x_b − 32, x_b + 32], y ∈ [y_b − 14, y_b + 3].
Mat level = median of the pre-swing background (`np.median(frames[8:56], axis=0)`) in
that window.

**Topline** — per column (skipping columns within `r_b + 1.5` of the ball), the topmost
row with `I ≥ max(205, mat + 50)` and `I[y + 2] ≤ mat − 35` (bright ridge with dark body
beneath); sub-pixel row = intensity-weighted centroid of the bright run. Robust line
through the points (Theil–Sen, then refit on |resid| < 1.5 px). Report slope (that IS the
projected lie) and row at `x_b`.

**Toe** — in rows 2–6 px below the topline line, starting from `x_b + r_b + 3`, walk right
while pixels are dark (`≤ mat − 35`, tolerate a 1-px speck); sub-pixel crossing by linear
interpolation at the threshold; median over rows. Report the per-row spread.

**Heel** — bright shaft pixels (`≥ max(210, mat + 55)`) in the region above-left of the
ball (x ∈ [x_b − 70, x_b − 2], y ∈ [y_b − 60, y_b − 4]); PCA line with 3 rounds of inlier
trimming (|dist| < 2 px, ≥ 20 px remaining); heel = intersection of that line with the
topline line. Report the shaft angle. (`club_motion.track_bright_shaft_endpoint` exists;
reuse the line, never the endpoint.)

Each detector returns `None` with a named reason when its gate fails. **Fail closed.**

## 3. Pre-registered checks (truth-free — decide pass/fail before running)

1. **Width constancy.** `toe − heel` is a physical constant per club. Report per-club
   mean ± sd over shots and frames. PASS if sd ≤ 1.5 px for both 7-iron and 9-iron. Any
   shot > 3 px from its club's median is flagged, and its overlay must explain why.
2. **Two-frame consistency.** Between f71 and f72 the heel, toe and topline must shift
   together. Report `Δtoe − Δheel` and `Δtopline` per shot. PASS if |Δtoe − Δheel| ≤ 1 px
   on ≥ 80 % of shots.
3. **Hand-mark agreement** on shot 014: every automatic edge within 1.5 px of §1.
4. **Availability.** ≥ 17/21 shots return all three edges in both frames. Every failure
   carries its reason.
5. **Look.** A contact sheet (all shots, f71 and f72, 8× crops, the three marks drawn) is
   rendered *before* any number in the report is quoted, and the report says what the
   sheet shows — including the cases where a detector locked onto the wrong thing.

Do not tune thresholds against the checks and then report the checks as passed; if you
change a threshold, say what you saw that motivated it and re-run everything.

## 4. Deliverables

- `src/openflight/camera/clubpose/contact_edges.py` — the three detectors + a
  `find_contact_edges(frame, ball, mat) -> ContactEdges` that composes them; dataclass with
  `status`, `topline_slope`, `topline_row_at_ball`, `heel_x`, `toe_x`, `width_px`,
  `toe_row_spread_px`, `shaft_angle_deg`. Pure numpy/cv2, no rendering, no mesh.
- `tests/test_clubpose_contact_edges.py` — TDD per `CLAUDE.md`: synthetic frames (grey mat,
  dark head rectangle, 2-px bright topline, bright shaft line, ball cap overlapping the
  topline) with known edges; each detector recovers them to ≤ 0.5 px; each gate's failure
  reason is exercised; the ball-column exclusion is tested.
- `scripts/analysis/clubpose_contact_edges.py` — runs the session, writes
  `contact_edges.json` (per shot, per frame, every field + reason) and `sheet_f71.png` /
  `sheet_f72.png` to an `--out` dir (not committed), prints the §3 table.
- A short results note appended as a new section to
  `docs/clubface-impact-location.md` ("Contact edges — spike result"): the §3 numbers, what
  the sheets showed, and the verdict in one line: **findable to ±1 px / not / findable
  with caveats**. No accuracy claim against truth — none exists.
- Lint clean (`ruff`, `pylint` per pre-commit). Conventional-commit messages. No AI
  attribution lines in commits.

## 5. Out of scope — do not do

Silhouette IoU, chamfer, mesh rendering, 6-DoF or any pose fit; extrapolating anything
across the approach; converting edges to a "strike location in mm on the face" (that
needs the mesh outline→face-centre offset and is the next step, not this one); any change
under `src/openflight/camera/club_delivery.py` or the server.
