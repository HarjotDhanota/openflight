# Codex brief — 2-D template alignment of the head outline at contact, 2026-08-28

**Branch:** `feat/clubpose-fit` (HEAD `75d86bf`, contact-edge spike committed). Work here.

**Question:** with the head's *shape* supplied by the mesh and its *scale* and *tilt*
supplied by the radar and the shaft line, can the outline be aligned to the two frames
bracketing contact by a **translation only** (x, y in pixels), and does that alignment
give a ball-to-outline offset that is consistent to ±1 px on ≥ 80 % of the 21 shots?

This is the cross-check to the learned segmenter
(`2026-08-28-clubhead-segmentation-labelling-spec.md`), which needs labels. This needs
none. Whichever passes first becomes the edge source. It is still a spike: an answer with
evidence, nothing wired into the shot pipeline.

**Why translation-only is legitimate here** (and why the report's IoU warnings do not
apply): the IoU/pose failures came from letting depth and three angles float on a 30 px
outline. Here range is pinned by the radar, lie by the shaft, loft by a static prior, so
the only free parameters are the two the camera measures directly. Face angle changes the
outline width by 0 px over the first 5° (report §04) and does not need to be solved.

---

## 0. Facts you must not re-derive

Everything in `2026-08-28-codex-brief-contact-edges.md` §0 still holds (un-mirror,
contact = trigger − 1.575/343 × fps = f71.85, ball from `detect_reference_ball`, plate
scale from the ball diameter, `shot_001` excluded, the camera sees the **back** of the
head). In addition:

- **Mesh:** `src/openflight/camera/clubpose/mesh.py` → `load_normalized_mesh`,
  `default_mesh_asset_root`. The asset is **not in git**; fetch it with
  `scripts/analysis/download_club_mesh.py --local-iron <STL>` per `meshes/SOURCES.md`
  (ask Harjot for the STL path). It is a 690CB **7-iron**; use it for the 9-iron shots
  too and say so — the two heads differ by a few mm, which is a known, reported bias.
- **Renderer:** `clubpose/fit.py::render_mask_6dof(mesh, center_world, yaw, pitch, roll,
  camera)` with `camera = fit.measured_camera(320, 200)` (fx = 466.7 — never the A0
  preset). `triad(0,0,0)` is a **backwards** club; every orientation must be built from
  `clubpose/angles.py::square_pose(dynamic_loft_deg, face_angle_deg, lie_deg)`.
- **Range per frame:** the radar range rate `iwr_club_path_range_rate_ms` (shots.csv,
  22/22 populated, ~33 m/s) anchored at the ball at contact:
  `range_mm(f) = 1581 − range_rate_ms × 1000 × (f_contact − f) / fps`. At f71 that is
  ~1516 mm, at f72 ~1591 mm. The world centre for the render is
  `CAMERA_CENTER_WORLD + ray(ball pixel) × range_mm` — i.e. render the head **on the ray
  through the ball**, then translate in the image.
- **Lie (roll):** measure the shaft image angle with `contact_edges.detect_heel`'s
  principal line (reuse `_principal_line`, or `club_motion.track_bright_shaft_endpoint`
  — the *direction*, never the endpoint). Solve the `lie_deg` argument of `square_pose`
  so the projected `angles.SHAFT_LOCAL` direction matches the observed shaft angle
  (1-D root find; the mesh has no shaft, but the axis is known). If no shaft line is
  found, fall back to `STATIC_LIE_DEG` and flag it.
- **Loft (pitch):** `dynamic_loft = STATIC_LOFT_DEG + experimental_fused_attack_angle_deg`
  from shots.csv when its status is `approach_high`/`approach_mixed`, else static loft.
  Report sensitivity to ±5°.
- **Face angle (yaw):** 0. Report sensitivity to ±10°.
- **Sole shadow and shaft are not in the mesh.** Mask the shaft out of the image edge
  map (±3 px around the shaft line, above the topline). Handle the shadow by polarity
  (§2), not by a threshold.

## 1. Method

Per shot, per frame f ∈ {71, 72}:

1. **Render once** at the nominal pose (ball ray, radar range, lie from shaft, loft
   prior, yaw 0). Extract the rendered outline as boundary pixels; keep for each boundary
   pixel its outward normal (from the mask gradient). Drop boundary pixels within 1
   ball-radius + 1 px of the ball centre (the ball's cap sits in front of the head there).
2. **Image edges:** Sobel gradient of the raw frame in a 64×48 window around the ball.
   An image edge pixel *supports* a template boundary pixel only when the image gradient
   points the same way as the template's outward normal (dark head inside, lighter mat
   outside → gradient points outward). This is what rejects the sole shadow (its outer
   edge has the opposite polarity relative to the head outline) and the internal face
   highlights.
3. **Score a translation (dx, dy)** as the mean over template boundary pixels of the
   distance to the nearest polarity-consistent image edge — a distance transform of the
   polarity-filtered edge map sampled at the shifted template points, bilinear for
   sub-pixel. Do **not** re-render per candidate; a translation of a few pixels at fixed
   range is a translation of the outline to first order (state the error: < 0.1 px for
   ±10 px at 1.5 m).
4. **Search:** coarse grid ±12 px at 1 px, then refine at 0.25 px around the best; report
   the residual (mean edge distance, px) and the fraction of template pixels with an
   edge within 1.5 px (support). **Fail closed** when support < 0.5 or residual > 1.5 px
   or the best sits on the grid boundary.
5. **Outputs per frame:** `(dx, dy)`, residual, support, lie used, loft used, and the
   **ball-to-outline offset**: ball centre minus the shifted outline's heel–toe midpoint
   (in px and mm via the per-shot plate scale), and ball centre row minus the shifted
   topline row at the ball column. These are the same quantities the hand marks in the
   contact-edge brief §1 express (shot 014: 18–24 mm heel-side of midpoint, 16–19 mm
   below topline).

## 2. Pre-registered checks (decide pass/fail before running)

| check | pass |
|---|---|
| Two-frame consistency: carry the f71 offset to f72 with the image-plane shift of the ball ray (radar range ramp + the measured transverse motion of the outline between f70 and f71), compare to the f72 offset | ≤ 1 px on ≥ 80 % of shots |
| Shot 014 vs hand marks (outline midpoint offset and topline row) | within 1.5 px, both frames |
| Availability (both frames pass the fail-closed gates) | ≥ 17/21 |
| Loft sensitivity ±5°, face-angle sensitivity ±10°, lie ±3° | offset moves ≤ 1 px |
| Residual distribution on accepted frames | median ≤ 1.0 px |
| **Sheets**: f71 and f72 for every shot at 8×, outline drawn at its aligned position, ball circle, heel/toe/midpoint marks, residual and status in the label — rendered and **looked at** before any number is quoted | report what they show, including wrong locks |

If a threshold or the search range is changed, say what you saw on the sheet that
motivated it and re-run everything; never tune against the checks and then report them
as passed.

## 3. Deliverables

- `src/openflight/camera/clubpose/outline_align.py` — nominal pose construction (lie from
  shaft, loft prior), polarity-aware edge map, distance-transform scoring,
  `align_outline(frame, ball, mesh, camera, range_mm, shaft_angle_deg, loft_deg) ->
  OutlineAlignment` (status + fields above). No IoU, no orientation search.
- `tests/test_clubpose_outline_align.py` — TDD per `CLAUDE.md`: synthetic frame from a
  rendered mask at a known translation with a drawn shadow and shaft, recovered to
  ≤ 0.25 px; polarity filter rejects the shadow edge; every fail-closed gate exercised;
  `triad(0,0,0)` never used as a seed (assert the nominal pose is in the physical
  envelope via `angles.in_envelope`).
- `scripts/analysis/clubpose_outline_align.py` — `--session`/`OPENFLIGHT_SESSION`,
  `--mesh`, `--out`; writes `outline_align.json` and `sheet_f71.png`/`sheet_f72.png`;
  prints the §2 table.
- Results section appended to `docs/clubface-impact-location.md` ("Outline alignment —
  spike result"), one-line verdict, and the 9-iron-with-7-iron-mesh caveat stated. No
  accuracy claim against truth.
- Lint clean, conventional commits, no AI attribution lines.

## 4. Out of scope — do not do

Solving orientation or depth; IoU or chamfer over a pose grid; whole-blob masks or
centroid extrapolation across the approach; converting to a strike position on the face
(next step, needs the outline→face-centre offset from the mesh); edits under
`club_delivery.py` or the server.
