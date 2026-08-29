# Is the Titleist 690CB 7-iron mesh an adequate club model?

Scope: `src/openflight/camera/clubpose/mesh.py`, `meshes/SOURCES.md`, `angles.py`,
`docs/clubface-impact-location-report.md`, `docs/superpowers/specs/2026-08-28-codex-brief-template-alignment.md`,
prior research (`2026-08-26-falsification-results.md`, `openflight_club_pose_research_survey.md`,
`markerless-club-data-guide-v2-research-corrected.md`). Tags: CONFIRMED (measured in this
repo or a cited primary source), INFERRED (reasoned from confirmed facts / general domain
knowledge, not independently verified here), UNKNOWN (no data found).

---

## 0. What the mesh actually is, today (repo facts)

- **CONFIRMED** — `MESH_SOURCES["poc_7iron"]` is a GrabCAD community STL, "local research use
  only," never committed, hash-pinned (`mesh.py:38-50`, `meshes/SOURCES.md`). Not redistributed.
- **CONFIRMED** — `CATEGORY_DIMENSIONS_MM["poc_7iron"] = {width: 80.0, height: 50.0, depth: 38.0}`
  (`mesh.py:57`). Comparing against the driver's corrected reference in the same file
  (118×60×112 mm — a plausible 460 cc driver bounding box), this triple reads as an **overall
  clubhead bounding box** (heel-toe × crown/topline-to-sole × face-to-back), not a face-only
  measurement.
- **CONFIRMED** — `admit_mesh` checks that triple against `face.face_span_mm` (in-plane extent
  of whatever surface `detect_face_plane` selects) plus a whole-mesh depth (`ptp` along the
  normal, `mesh.py:437-448`). The normalized 690CB's detected patch is **79.739 × 42.497 mm**
  (`SOURCES.md:49`). Width is 0.3% off nominal (80 mm); **height is 15.0% off nominal (50 mm)**,
  landing exactly on `admit_mesh`'s `tolerance_fraction=0.15` boundary (`mesh.py:420,447`) — a
  pass, but the narrowest possible one. No other iron has ever been run through this gate to know
  if 15% is typical scatter or this mesh's outlier.
- **CONFIRMED** — the surface `detect_face_plane` selects is the **cavity rim on the back**, not
  the striking face (16.36° off anti-parallel to the true face; `SOURCES.md:42-51`,
  `angles.py:1-19`). The rim's *width* is a reasonable proxy for heel-toe head length (a cavity
  rim traces close to the head's outer contour), so the absolute-size checks above are less
  compromised by this bug than the loft/lie numbers are — but it is a proxy, not a direct
  heel-toe measurement, and nothing in the repo has ever measured the mesh's true head length
  directly.
- **CONFIRMED** — the mesh has no shaft. Its 63.8 mm × 13–17 mm protrusion is hosel + ferrule
  (`clubface-impact-location-report.md` §09). **INFERRED**: 63.8 mm is long for hosel+ferrule
  alone — published hosel bore depths run roughly 20–40 mm (`markerless-club-data-guide-v2...md`
  line 304, citing club-construction patents), and a ferrule adds a few mm more. A 63.8 mm
  protrusion is 1.5–2× that range, which is either a real feature of this particular casting or
  a sign the mesh carries extra length nobody has isolated. **UNKNOWN** which.
- **CONFIRMED** (web, 2026-08-28) — Titleist's own current spec pages
  ([titleist.com](https://www.titleist.com/golf-clubs/irons/2005-forged-690cb),
  [equip2golf.com](https://www.equip2golf.com/specifications/titleist/titleist_forged_690CB.html))
  give the 690CB 7-iron as **loft 36.00°, lie 63.00°, offset 0.110 in (2.79 mm)**. The repo's own
  commentary elsewhere cites "690CB catalogue ~34/62" as the comparison point for the mesh's
  measured 33.10°/61.19°. **These two catalogue figures disagree with each other by 2–3° in loft
  and lie** (36/63 vs the repo's "34/62"), which is larger than the loft-anchor bug's own residual
  against either one. **UNKNOWN** which catalogue figure the repo's "34/62" came from, or why it
  differs from Titleist's own page — worth resolving before trusting the mesh's absolute
  loft/lie as ground truth for *this* casting, independent of the face-anchor bug.

---

## 1. Real-iron dimensions: category and model variation, in mm and px

Plate scale this session: **0.2952 px/mm → 1 px = 3.39 mm** (measured, `report` §02; the brief's
"~3.5 mm/px" is the same figure rounded).

**CONFIRMED**, from `openflight_club_pose_research_survey.md` §F (six-OEM 2026 snapshot: Titleist,
PING, TaylorMade, Callaway, Mizuno, Srixon; sources linked there):

| Category | 7i loft | 7i lie | 9i loft | 9i lie |
|---|---:|---:|---:|---:|
| Players, n=6 | 33.00° ± 0.63° | 62.25° ± 0.42° | 41.17° ± 0.41° | 63.33° ± 0.41° |
| Game-improvement, n=6 | 28.58° ± 0.49° | 62.42° ± 0.49° | 37.33° ± 0.52° | 63.54° ± 0.46° |

- 7-iron loft spread across categories: **~4.4°**. 9-iron: **~3.8°**. Lie: **~0.2°** (stable).
- 7i→9i loft gap *within* a category: **~8°** (players) — this is the number that matters for
  "same mesh used for both clubs," since it is baked into `STATIC_LOFT_DEG` being 7-iron-only.

**CONFIRMED**, same source §F, two-OEM subset (Mizuno + Srixon comparison pages):

| Category | 7i head length (heel-toe) | 7i sole width |
|---|---:|---:|
| Players | 76.2 mm mean [74.5–77.2] | ~17 mm [15.6–19.5] |
| Game-improvement | 84.7 mm mean [82.0–86.6] | 23.4 mm [22.9–23.9] |

Converted at 3.39 mm/px: **players-vs-GI head length differs by ~8.5 mm ≈ 2.5 px**; sole width by
~6.4 mm ≈ 1.9 px. The source explicitly flags that two live Srixon pages disagree on the *same*
model's topline/sole numbers (15.6/4.0 mm vs 18.9/6.0 mm), so treat single-mm figures as ±1–2 mm
noisy even within one OEM's own publication.

**CONFIRMED** (web, 2026-08-28) — 690CB catalogue offset **0.110 in = 2.79 mm**. **INFERRED**
(general clubfitting knowledge, not independently sourced here): offset commonly runs ~2–5 mm for
players/blade irons up to ~5–8 mm for game-improvement — a 3–5 mm spread, ≈1–1.5 px.

**UNKNOWN / not found published anywhere** (confirmed absent by the prior survey, §F): face
height, sole-to-topline face height in isolation, face-centre coordinates relative to the hosel
bore axis, hosel-bore-axis convention. These are exactly the numbers use (c) below needs, and
**no manufacturer publishes them** in a comparable cross-brand format.

**7i-vs-9i head-size difference (as opposed to category difference):** **INFERRED, no citation
found.** Iron sets are known to size heads progressively smaller as loft increases through a set,
but no source in this research or the prior surveys quantifies the 7i→9i step in mm. Given the
players/GI category gap is ~8.5 mm across a much larger design gulf, a same-set two-club step is
plausibly a few mm — **call it 2–5 mm ≈ 0.6–1.5 px**, genuinely unknown until measured.

**Bottom line for §1:** category-level geometry variation (blade length, sole width) is
**2–3 px** at this plate scale; loft/lie variation is **several degrees**, not a pixel-scale
quantity but a direct bias on any angle read off the mesh; face height and face-centre offset —
the numbers use (c) needs most — are **not published at all**, so "how wrong is the mesh" for
that use cannot even be bounded from manufacturer data.

---

## 2. Cost of mesh error, by use

### (a) 6-DoF silhouette pose fit — **mesh is adequate; it is not the bottleneck**

**CONFIRMED** (report §04): the first 5° of face angle changes the projected width by **0 px**;
10° changes it by 2 px. Segmentation noise of 1 px is worth **~10° of face angle** — 4–8×
larger than any mesh dimension error from §1 (2–3 px). A mesh that was 2–3 px wrongly sized would
be *invisible* against that noise floor. **This use is second-order with respect to mesh
geometry and first-order with respect to segmentation** — exactly the report's own conclusion,
independent of which iron mesh is used. **Keep the mesh here; fixing it would not move this
number.**

### (b) 2-D template alignment (translation-only) at contact — **first-order, and currently
unguarded**

Per the spec (`2026-08-28-codex-brief-template-alignment.md`): "shape supplied by the mesh,
scale... supplied by the radar." But rendered pixel size = physical size (mm) × fx / range —
**the mesh's absolute physical dimensions set the pixel scale of the template at a given radar
range just as much as the range does.** If the true club is 6–8% larger or smaller than the mesh
(the players-vs-GI head-length spread from §1, or an unknown make/model entirely), the rendered
template is uniformly the wrong size by that fraction — **~2–3 px at 30 px head width**, per §1.

A translation-only search cannot correct a scale error; it can only find the (dx, dy) that
minimizes edge residual against a wrong-sized template, which **systematically biases** the
recovered outline position (and hence the reported ball-to-outline offset) rather than adding
noise. Two properties of the spec's own pre-registered checks make this bias hard to catch:

- The two-frame consistency check (f71 vs f72, §2 of the spec) compares the *same* shot to
  *itself* — a constant scale bias reproduces consistently across both frames and would **pass**
  that check.
- The only truth comparison is one shot (014) against hand marks, and the hand-marked club's true
  make/model is itself unknown (per this task's framing) — so even that check cannot rule out a
  shared scale bias between the mesh and the real club used for the marks.

**This is first-order for (b): a wrong mesh size does not add scatter, it adds an
undetected constant offset**, of the same order as the accuracy target (spec checks to 1–1.5 px
= 3.5–5 mm).

### (c) outline/centroid → face-centre offset — **first-order, and the highest-consequence use**

This is a fixed geometric constant, read once off the mesh, then applied to every shot. Two
independent problems compound here:

1. **The constant itself is currently ill-defined.** `detect_face_plane` anchors to the back
   cavity rim, not the true face (§0). `angles.py` patches the *direction* (`FACE_NORMAL_LOCAL`,
   `SHAFT_LOCAL`) for angle computation, but nothing in the repo has yet defined or patched an
   analogous **face-centre landmark** for impact-location purposes. Building (c) directly on
   `detect_face_plane`'s output today would silently locate the wrong point on the club.
2. **Even with that fixed, the constant depends on the specific casting.** Face-centre location
   relative to the outline centroid depends on hosel offset, face height, and camber/bulge — the
   exact quantities §1 shows are **not published by any manufacturer** and vary by a few mm
   category-to-category, and by an unknown amount for the actual, unidentified clubs in this
   session.

Because this use converts directly to "impact location in mm" — the headline product number —
**any mesh-vs-real-club mismatch here is not attenuated by anything downstream; it is the
output.** A 3–5 mm bias (the scale of §1's known unknowns) is comparable to or larger than the
whole system's target precision (commercial comparators report impact-location accuracy in the
low single-digit mm; see `openflight_club_pose_research_survey.md`/`markerless-club-data-guide...`
D-plane budget work, where a 3 mm generic-template bias alone costs ~1.7° loft / 4.0 mm, isolated
from all other error sources). **Do not ship a headline mm number built on this constant without
first pinning down what club produced it.**

### Verdict by use

| Use | Mesh-error order | Verdict |
|---|---|---|
| (a) 6-DoF silhouette pose fit | second-order (dwarfed by 1 px segmentation noise) | **adequate as-is** |
| (b) 2-D template alignment at contact | first-order (systematic, not noise; undetected by the spec's own checks) | **not adequate without a scale/shape source independent of the mesh** |
| (c) outline→face-centre offset (impact location) | first-order, largest consequence, currently built on an undefined landmark | **not adequate; the highest-priority fix** |

---

## 3. Alternatives — cost/benefit for a DIY, arbitrary-club product

| Option | What it fixes | Cost | Fit for "users bring arbitrary clubs" |
|---|---|---|---|
| **(i) Small parametric head model** (face length/height, topline curvature, hosel offset, sole radius) fit per club from one calibration photo | Per-club shape *and* size, generalizable to any viewing angle via the parameters | Moderate — needs a parametric shape model plus a fitting routine, and the same segmentation-quality ceiling as the in-swing fit (though a static, well-lit address photo is a far easier segmentation target than a 468 fps motion-blurred frame) | Good, but more machinery than (ii) buys for this rig |
| **(ii) Per-club user calibration photo → 2-D outline template directly, no CAD** | Everything: exact outline, exact size, exact face-centre landmark (user marks or software detects the grooves) for *the actual club*, at *the actual camera position* | Low — this camera is fixed behind the ball, and address ball position ≈ impact ball position for irons, so **a photo of the club soled at address, from this same camera, is already close to the template (b) and (c) need at contact** — no 3D reconstruction, no CAD, no license question | **Best fit.** Solves the unknown-make/model problem and the 7-iron-mesh-for-9-iron problem in one step, using hardware already in the rig |
| **(iii) Mesh library (GrabCAD/other) with admission checks** | Coarser per-category shape than one fixed mesh | High — sourcing, licensing (as `SOURCES.md`'s rejected-candidates section already shows is non-trivial), and vetting each addition through `admit_mesh` | Poor alone — a library still can't cover arbitrary user clubs, only a larger fixed set. Reasonable as a **fallback prior** for users who skip calibration |
| **(iv) Learn the outline distribution from labelled frames** | Segmentation robustness / a prior over plausible outlines | High — needs a labelled corpus across clubs and sessions; doesn't resolve per-club specificity unless conditioned on the very club-id information (i) or (ii) already supply | Weakest fit for the immediate problem; more useful later, as a segmentation-quality lever (report's own Tier-2/3 items), than as a club-geometry source |

**For an open-source, DIY product where users bring their own, arbitrary clubs: (ii) is the
right primary path.** It needs no new sensor (same camera, same fixed geometry that already
makes contact ≈ address), no CAD sourcing/licensing, and it eliminates both concrete defects in
§2 (undefined face-centre landmark, cross-club mesh reuse) at the source, rather than bounding
their error. (i) is the natural extension if a single fixed-camera address photo turns out
insufficient (e.g., swing plane at contact meaningfully rotates the head relative to address) —
build it second, informed by what (ii) shows. (iii) is worth keeping only as a rough default for
users who never calibrate. (iv) is a segmentation project, not a club-geometry one; keep it off
this critical path.

---

## 4. Concrete checks to run now, on existing data

1. **The one asked for directly:** for all 21 shots, at each pre-impact frame with a valid radar
   range, measure the observed clubhead mask's heel-toe extent in px and compare to the mesh's
   projected width at that exact range (same camera model, same `fx=466.7`). This is the direct
   test of absolute mesh size that nothing in the report or the spec currently runs — the
   existing "43–55% pixel coverage" figure (report §04, Figure 9) conflates scale-mismatch with
   the missing-shaft-tail and sole-shadow pixels; this check isolates size alone by comparing
   heel-toe span, which neither the shaft nor the shadow should extend past.
2. **Aspect ratio, independent of absolute scale:** width/height of the observed mask vs. the
   mesh's silhouette at the matched fitted pose, per frame. This separates "the mesh is the wrong
   overall size" (check 1) from "the mesh is the wrong shape" (this check) — currently
   `admit_mesh`'s 15%-tolerance pass on height (§0) is the only existing signal on shape, and it
   is a single number from a single normalization run, not a per-shot empirical comparison.
3. **Re-run `admit_mesh`'s dimension check against the true head bounding box, not the
   mis-anchored face patch.** Compute heel-toe/topline-sole extent directly from the mesh's own
   vertices along world-frame-appropriate axes (not `detect_face_plane`'s selected surface), and
   compare that to the 80/50/38 mm nominal. This tells you whether the mesh passed admission on
   the right quantity or a proxy that happened to be close.
4. **Resolve the 34/62-vs-36/63 catalogue discrepancy** (§0) against a primary Titleist source or
   the physical clubs if available, before trusting either the mesh's measured 33.10°/61.19° or
   any catalogue figure as ground truth for this specific casting.
5. **Sanity-check the 63.8 mm hosel+ferrule protrusion** (§0) against a tape measurement of a real
   iron's hosel, if one is on hand — cheap, and would settle whether the mesh carries excess
   length that the "43–55% coverage" figure is partly attributing to the missing shaft instead.

None of these require new capture; all run against the existing 21-shot session and the mesh
already in hand.

---

## 5. Verdict

**Keep** the mesh for (a) — proven immaterial there, since segmentation noise (1 px ≈ 10°) swamps
any mesh-size error (2–3 px) by 4–8×. **Do not extend it to (b)/(c) as-is** — those uses are
first-order in mesh error and one of them (c) is currently built on an undefined landmark
(`detect_face_plane`'s wrong surface) even before asking whether this is the right club. The
first step: run check #1 above (heel-toe px vs. projected width across the 21 shots) to put a
real number on today's absolute-size error before wiring (b) or (c) to anything, then move (b)/(c)
onto a per-club address-calibration photo (option ii) rather than the GrabCAD mesh, since that
also resolves the unknown-make/model and 7-iron-mesh-for-9-iron problems this session already has.

## Sources

- Repo: `src/openflight/camera/clubpose/mesh.py`, `meshes/SOURCES.md`, `angles.py`,
  `docs/clubface-impact-location-report.md`, `docs/clubface-impact-location.md`,
  `docs/superpowers/specs/2026-08-28-codex-brief-template-alignment.md`
- Prior research (this session's scratchpad `research/prior/`):
  `2026-08-26-falsification-results.md`, `openflight_club_pose_research_survey.md` §E/F/G,
  `markerless-club-data-guide-v2-research-corrected.md` §1G/4
- [Titleist Forged 690.CB Irons](https://www.titleist.com/golf-clubs/irons/2005-forged-690cb)
- [Titleist Forged 690CB Specifications — equip2golf.com](https://www.equip2golf.com/specifications/titleist/titleist_forged_690CB.html)
- Manufacturer spec pages cited by the prior survey for head geometry: Mizuno 7-iron comparison,
  Srixon iron-shape comparison (URLs in `openflight_club_pose_research_survey.md` §F; both sites
  blocked automated fetch on 2026-08-28, so figures here are as extracted by that prior research
  pass, not re-verified live)
