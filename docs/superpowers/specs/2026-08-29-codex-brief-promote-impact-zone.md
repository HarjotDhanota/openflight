# Codex brief — promote the impact-zone extractor into `clubpose`, on fixed geometry

**Branch:** start from `feat/clubpose-drift-fixes` @ 52343fe (rebased onto `feat/clubpose-fit`
@ 8d244e2; suite green). Create `feat/clubpose-impact-zone` from it. TDD per `CLAUDE.md`; one
commit per step; conventional messages; no AI attribution lines; do not push.

**What this delivers:** the heel–toe impact zone as a shipped-experimental metric, computed by
the data-built outline extractor that passed its pre-registered gates on 2026-08-29
(`research/session-metrics` @ 37dd859, `research/empirical_template/`), running on corrected
camera geometry, with the 42 hand marks (`openflight/contact_marks.jsonl`, pass 1 = annotator
`harjot`) as the regression set. Vertical (high–low) stays research (r = 0.84) — carry it as a
field marked `experimental_unvalidated`, never as a zone.

## Part A — fix the eight geometry defects first (all verified 2026-08-29, file:line on 52343fe)

| # | file:line | defect | fix + test |
|---|---|---|---|
| 1 | `projection.py:66` `_rotation_world_to_camera` | aims the camera at the world ORIGIN (7.4° down); real camera is level — ball projects 46.8 px off | camera orientation = explicit pitch/roll fields on the camera model; `measured_camera()` gets pitch **solved per session from the teed ball's row** (closed form, as in `research/pose_refit/refit_corrected.py`) with a documented default of −0.22°, roll default 0 with the 3.18° net-line measurement recorded as UNRESOLVED. Test: the ball (40 mm up, 1581 mm) projects within 2 px of its observed pixel on ≥19/21 shots (rows). |
| 2 | `projection.py:62` `camera_center_world` | y hard-coded 0; the lens is −60.3 mm lateral (tape), −55.7 solved | make lateral offset a field, default −60.325 mm; test the ball column residual improves. |
| 3 | `projection.py:46-53` | `CAMERA_HEIGHT_MM` is floor-referenced but used as height above the BALL CENTRE (~40 mm) | define both explicitly (lens above floor; ball centre 40 mm); test the geometry chain reproduces 1581 mm slant range. |
| 4–5 | `fit.py:316-318, 416-418, 261-263, 375-377` | accept bounds and default grids centred on the backwards triad origin; `square_pose()` (yaw ≈ 180°) is rejected before any search | centre bounds/grids on `square_pose()`; test that the grounded pose is inside the box. |
| 6 | `fit.py:375` | `smooth_deg=70` freezes short sequences at frame one's pose | default `smooth_deg=None` (no penalty) and document; test that a 5-frame sequence with a real 5° ramp is not frozen. |
| 7 | `head_split.py:38` / renderer | template keeps a hosel every observed mask has cut | `render_mask_6dof(..., clip_hosel=True)` cutting at the neck the same way `split_head` does; test the rendered area matches a hosel-free mask within 5 %. |
| 8 | `iwr6843/tracking.py:118` | `quad_bins` populated for ball tracks, `None` for club tracks → fusion silently gets a straight-line range | populate the quadratic refit for club tracks (or expose the linear model explicitly and make `ranges_from_radar` state which it got); test on a synthetic decelerating track. |

Then re-run `research/pose_refit_corrected` once on the fixed code and record that the mesh
route still fails (expected) — the fixes are for the extractor, not to reopen the mesh route.

## Part B — port the extractor (`src/openflight/camera/clubpose/impact_zone.py` + tests)

Port from `research/empirical_template/{build_template_v3.py, align_and_carry_v3.py}` (read
their RESULTS.md first; three iterations of lessons are in it):

1. **Head masks**: background median (pre-swing frames), |diff| > max(4σ, 18), 3×3 close + hole
   fill, no tee veto BEFORE contact, **ball veto AFTER contact** (departing ball = brightest
   compact blob above the tee row), largest component near the ball, `split_head`. Fail closed
   with a reason when the mask is < 60 px or merges with turf (shot 016 f71 is the known case).
2. **Template per club** (`ClubOutlineTemplate`): occupancy map from masks cropped by
   landmarks; sole edge AND heel edge from the occupancy fall-off (it3); toe crop at the mark;
   0.5 contour; store as a small PNG/npz + the two landmark offsets (hosel-neck→midpoint,
   topline→ball column). Build modes: (a) from labelled frames (the 42 marks — regression only),
   (b) **self-built from a user's own swings** with no marks: bootstrap landmarks from the mask's
   own extremes on the first shot, refine across shots, report convergence; (c) from one address
   photo (frame before the swing) — reserve the API, implement if the session provides one.
3. **Alignment**: translation ±12 px (0.25 px refine) + roll ±20°, size pinned by the radar
   range per frame (`fusion.ranges_from_radar`, or the quadratic track from A8), local IoU,
   fail-closed IoU < 0.6 or on the search boundary. Frames f_c−6 … f_c+1.
4. **Carry to contact**: quadratic over the aligned frames (NOT linear — 6–10 px wrong), with the
   f71/f72 interpolation as a cross-check; report the disagreement.
5. **Rule-based sanity gates** (USGA Equipment Rules, Part 2): heel must lie within **15.88 mm
   (4.7 px)** of the plane through the observed shaft line and the line of play (§1d); nothing
   may rise above the topline by more than **2.54 mm (0.75 px)** (§4a(i)) — a mask that does is
   segmentation error → reject the frame.
6. **Output** (`ImactZoneResult`): `heel_toe_mm` relative to the face centre using the stated
   convention (face centre = outline midpoint + 8 mm toe-ward, to be replaced by the address
   photo / foot-spray calibration), `zone` ∈ heel / heel-centre / centre / centre-toe / toe
   (±5 / ±15 mm), `high_low_mm` marked `experimental_unvalidated`, per-frame residuals,
   `status` with a reason on withhold. Record `template_source` (labels | self-built | photo).
7. **Category prior for the first swing**: blade length by category from official data
   (Mizuno comparison JSON, Srixon shape PDF — see the reference memo): players ~74–77 mm,
   players-cavity ~77–81, GI ~85–87 at 7-iron. Used only to size the initial outline; never the
   answer.

Regression tests (real data, skip cleanly if the session export is absent): on the 21 shots the
extractor reproduces it3's numbers — heel 0.89/1.71, toe 0.52/1.09, topline 0.85/1.78 px
(median/p90) vs pass-1 marks, ≥ 17/21 available, heel–toe zone r ≥ 0.95 vs `analyse_marks`'s
hand zones. Synthetic tests for every gate.

## Part C — wiring (behind a flag, off by default)

`server.py`/`club_delivery` integration only as a `experimental_impact_zone` field populated
when the camera archive + IWR range evidence exist and a template for the selected club exists;
withheld with a reason otherwise. No UI. Document in `docs/clubface-impact-location.md`
("Impact zone — promoted") with the same no-truth caveat and the Stage-1 foot-spray plan.

## Do not
Reopen the mesh route; change the world frame; touch LCMF; tune thresholds against the marks;
use any community 3-D model (the GrabCAD mesh is a research fixture only).
