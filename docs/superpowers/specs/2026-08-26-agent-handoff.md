# Agent handoff — OpenFlight impact location, 2026-08-26

**Branch:** `feat/silhouette-poc`. 144 tests passing.
**Supersedes** `2026-08-25-agent-handoff.md`. Read §0 before anything else — several claims
in that file, in the status checklist, and on the public page are now known to be wrong.

> **UPDATE 2026-08-26 — see `2026-08-26-falsification-results.md`.**
> Falsification tests **1, 2, 5 and 9** have been run on the full 21-shot set, and **both**
> maintainer questions in §5 are now closed. Apply these before citing §3 or §5:
>
> - **Test 1: the small 7i/9i launch gap is REAL.** An independent camera-ray + IWR-range
>   reconstruction gives **+2.91° mean / +4.22° median** against LCMF's +2.59° / +3.60°.
>   The gap is invariant to a 13° swing in camera pitch (≤0.03°).
> - **Test 2: REFUTED as a club-dependent bias.** The contract defect is real, but the
>   *ballistic* contract — the one flagged — is worth **−0.0095°**, and fixing it correctly
>   moves the club gap by **−0.003°**. The +1.24° lives in the TDM phase de-rotation, where
>   radial speed is the physically CORRECT input. **Do not "fix" that one.**
> - **§5 Q1 CLOSED.** The ball is **163 mm BELOW** the lens and the camera is **level to
>   −0.185° ± 0.111°** (21/21 shots). The "2.73° below boresight" figure in §5 was computed
>   with the **A0 preset's fx = 1033 px** instead of the measured 466.67 px; at the wrong
>   focal length it reproduces exactly. There is no contradiction to resolve.
> - **§5 Q2 CLOSED without CAD.** The mesh transform is rigid (`source_units_mm: true`), so
>   its angles are the CAD's. Measured **loft 17.5°, lie 61.5°**, self-consistent to 0.25°.
>   **The shaft stub is NOT mis-oriented — the lie is right. The FACE LOFT is the anomaly**
>   (~17.5° where a 7-iron is ~34°). This inverts the hypothesis stated in §5.2.
> - **NEW, unlisted:** the camera and the radar disagree by **+5.3° in vertical launch**
>   (20/20 shots), matching the **+5.10°** horizontal disagreement the shipped pipeline has
>   been logging in `experimental_camera_iwr_delta_deg` all along. Neither sensor can
>   arbitrate the other from existing data.
> - **NEW, unlisted:** **∂(LCMF launch)/∂v = +0.913 ± 0.113 °/(m/s)** — a 1 % OPS
>   ball-speed error is 0.41° of launch angle. This belongs in the uncertainty budget.

Three external research reports now exist in the repo root and are load-bearing:

| File | Covers |
|---|---|
| `openflight_club_pose_research_survey.md` | monocular pose, small objects, priors, shaft mechanics, iron specs, D-plane, comparators |
| `openflight_shaft_axis_followup_research.md` | partial-arc sphere fitting, shaft axis as a primitive, iron face curvature, vertical vs horizontal strike |
| `openflight_dplane_hardware_followup_research.md` | whether **this** hardware can support a D-plane inversion, and 12 falsification tests |

---

## 0. Corrections — claims that were published and are now wrong

These appear in the previous handoff, the status checklist, and the public page. Fix them
before citing any of it.

| Claim | Status | Why |
|---|---|---|
| "Dynamic loft is **compressed to 55 %** — 4.4° measured against ~8° expected" | **REFUTED** | The 4.4° gap follows *algebraically* from the measured 3.5° launch gap: `ΔL = ΔA + (ΔV−ΔA)/0.81 = 4.41°`. TrackMan's PGA averages give a 7i/9i **vertical launch** gap of **4.1°**, close to our 3.5°. **Static loft is not delivered loft** — comparing them assumed the conclusion. (D-plane report §2.1–2.2) |
| "The face plane is tangent to the ball, so the ball pins its position and we don't need clubhead range" | **INCOMPLETE** | Tangency fixes the plane's *normal and offset* and gives the contact point `T = B − R·n`. **Two translational degrees of freedom remain.** Strike *coordinates* need the face origin **O** and axes, which require the full 6-DoF head pose. Face angle + loft + ball position + tangency is **not** sufficient. (D-plane report §5.1) |
| "Toe–heel offset measurable to 1.51 mm from the shaft perpendicular" | **WITHDRAWN** | Smash-factor test found no coherent relationship (R² 0.01–0.23, signs inconsistent between clubs). The image perpendicular contains vertical and face-normal cross-talk. Precision was mistaken for validity. |
| "Radar angular beamwidth ~19°" | **UNDERSTATED** | The IWR6843**LEVM** has 3 TX × 4 RX = 12 virtual channels but only **two effective ELEVATION positions**. TI quotes ~15° azimuth and **58° elevation** two-target resolution. Do not plug 12 into an N-element array formula. (D-plane report §7.1) |
| "Widening the roll grid is not a fix" | **REVERSED** | True on mirrored data. With handedness fixed there is one correct basin near −150°, unreachable from the shipped (−60…+90) grid. |
| "The camera's fitted clubhead range agrees with the radar's" | **RETRACTED** | Extrapolated from one shot. Across 21: camera sd 99 mm, radar sd 30 mm, correlation **r = +0.20**. |
| "The camera is essentially level; the 10° tilt is the radar's" | ~~UNSETTLED~~ → **CONFIRMED 2026-08-26** | Measured on 21/21 shots: boresight pitch **−0.185° ± 0.111°**. The camera is level; the 10.405° tilt is the radar's alone. |
| "Mesh face-to-shaft angle is inconsistent with 36°/60° by 18.5°" | ~~UNSETTLED~~ → **SETTLED 2026-08-26** | The inconsistency is real — `arccos(sin L·sin λ)` is the *definition* of loft and lie, not an extra assumption. But it is in the **face loft** (measured 17.5°), not the shaft: the lie measures 61.5°, matching catalogue. |
| "The ball sits 2.73° below the camera boresight (sd 0.33°)" | **WRONG FOCAL LENGTH** | Reproduces exactly at the **A0 preset's fx = 1033 px**. At the measured 466.67 px the ball sits **5.742° ± 0.111°** below boresight, which matches the tape's 5.926° geometric depression. |
| D-plane report: "a 17°→25° launch changes the projection factor 0.956→0.906" | **OVERSTATED ~4×** | Those are plain `cos(launch)`, i.e. a horizontal line of sight. With the radar 4″ below the ball and 5.2 ft behind, the true factors are **0.976 / 0.969**. |

**The pattern worth naming:** every one of these came from drawing a conclusion from one
shot, one measurement, or an unverified geometric assumption. The 22-shot dataset makes
that unnecessary. **Run the cross-set check before reporting, not after being challenged.**

---

## 1. What genuinely advanced

### 1.1 The handedness bug — the single biggest fix

The capture applies `image[:, ::-1]` before saving (`triggered_buffer.py:159`) because
`mirror_horizontal: true`. **The golfer is right-handed (confirmed with the person who
took the shots), so the stored frames show a LEFT-handed club**, and a right-handed mesh
was being fitted to them. No rotation can do that.

**Un-mirror the FRAMES, not the mesh** — it matches the `horizontal_pixel_sign` convention
`server.py:2733` already applies, restores physical orientation so toe/heel and draw/fade
signs come out right, and keeps one canonical mesh per club.

Effect: IoU **0.548 → 0.633 on every one of 21 shots**; the two roll basins 150° apart
collapse to one; peak IoU over the roll circle 0.377 → 0.437.

**It did not fix pose coherence.** ~20 % of adjacent frames still jump >45°.

### 1.2 Head/shaft separation — club tracking 4 → 17 frames per shot

`research/silhouette_poc/replay/head_split.py` (+ 9 tests). On a properly exposed capture
the shaft is a strong moving object that merges with the head near impact; the shipped
tracker's "one moving blob is the clubhead" was inherited from a capture where the shaft
had no contrast. Head and shaft separate on measured inscribed radius with no overlap:

| | max inscribed radius | pixels ≥ 4 px | reach from head core |
|---|---|---|---|
| shaft alone | 2.0–3.0 px | **0.0 %** | — |
| head alone | 5.0–11.6 px | 12–49 % | 4.2–40.8 px |
| merged | 5.0–9.8 px | 3–20 % | 145.4–187.2 px |

### 1.3 The physical loft gate

**37 % of fitted frames (128/349) imply a physically impossible dynamic loft** — face-normal
elevation from −70° to +60°. Bounding it to 5–45° keeps 221 frames and takes pitch
disagreement >45° from **19 % to 0 %**.

**Why this works where earlier physical bounds failed:** the earlier attempt bounded the
fit's own yaw/pitch/roll, which are offsets in the mesh's frame and are not face angle,
loft and lie. Bounding the **world-frame face normal** bounds a real physical quantity.
*Constrain what the club does, not what the parameterisation does.*

### 1.4 Other established results

| Result | Evidence |
|---|---|
| Ball detection **21/22**, zero mis-detections | pass `expected_radius_px=6.5, radius_tolerance=0.30` |
| Camera and radar locate impact independently, agreeing to **0.66 camera frames** | 21 shots |
| Acoustic trigger lags impact by **6.0 ± 0.68 frames (12.8 ± 1.5 ms)** | 21 shots |
| Radar selects a clubhead track on **22/22** at 0.83–0.98× club speed, tee within 1.9–8.8 cm | `find_club` |
| Radar clubhead range 3× more precise than the camera fit (sd 30 vs 99 mm) | 21 shots |
| Plate scale **0.296 px/mm**, range **1576 mm**, ball 12.63 px, blur 2.97 px | 21 shots |
| Irons are **planar** — no bulge/roll coupling, unlike drivers | Corke et al. |

---

## 2. The open blockers, in priority order

### 2.1 The ball's centre is biased, and it sets every millimetre figure

The detected outline is **not a circle**. Signed radial residual by sector, 20 shots:

- **top of ball: −1.85 px = −6.1 mm** (inside the true silhouette)
- bottom of ball: −0.11 px (accurate)

The lower edge sits against its own contact shadow and thresholds cleanly; the upper edge
sits against the lit mat receding behind it, where contrast is weak. **A gradient-based
estimator was built and does recover the top edge** (26–36 votes/shot) — but left/right/
bottom sector centres still disagree by **2.47 px = 8.3 mm**, so the cause is physical
(shading across a 12.6 px ball), not segmentation.

Range is `focal_px × 42.67 / diameter_px`, so this moves 0.296 px/mm, the 1576 mm range,
and the 3.38 mm-per-pixel ceiling together.

**Unlike clubhead centroid migration, this one should roughly halve at a 1:1 readout** —
it is PSF-limited edge localisation, and the pixel is currently the limiting element.

The shaft-axis report §1 gives the estimator theory: fixing the radius turns a short-arc
fit from a three-parameter problem into a two-parameter one, reducing centre SD in the weak
direction by **4.0–5.6×** at 120–140° of arc. A 0.7 % radius error moves the centre only
~0.05 px, so fixing the radius from radar range is safe.

### 2.2 Impact location needs the face ORIGIN, which nothing currently measures

```
contact point   T = B − R·n            (tangency — solved)
strike coords   x = (T−O)·e_t ,  y = (T−O)·e_u    (needs O, e_t, e_u — NOT solved)
```

**O cannot be recovered from the face normal.** It needs the clubhead's full 3D translation
*and* orientation plus a per-club face coordinate system. A 1° normal error moves the
tangent point only 0.37 mm; the unmeasured in-plane origin enters strike coordinates
**one-for-one** and is currently unbounded.

Corke et al. did achieve sub-millimetre strike repeatability — with multiple calibrated
high-speed cameras and **rigid-body markers defining the face coordinate system**. The
critical ingredient was the measured face frame, not tangency.

### 2.3 The D-plane runs but is not validated

Inverting with 69/31 horizontal and 81/19 vertical gives face angle median **−1.89°**
(−9.96 … +6.00) and dynamic loft median **26.9°** (16.9 … 34.9). Both central values are
credible. There is **no external truth in the dataset** — ball speed is the OPS243, launch
angles are our own fusion, and `spin_rpm` reproduces the project's own kinematic formula
`170 × ball_speed × sin(launch)^1.2` to **0.00 rpm on all 22 shots**.

The deeper problem (D-plane report, executive conclusion): we measure the velocity of an
observed **centroid or radar scattering centre**, not the velocity of the **point on the
face that contacts the ball**. Those differ by the head's angular-velocity term, which
neither an incoherent silhouette pitch nor four range samples supplies.

---

## 3. The twelve falsification tests — the highest-value next work

From the D-plane report. All run on existing data except #12. Ordered by value.

1. **Independent vertical trajectory.** Reconstruct ball elevation from calibrated camera
   rays + IWR range, *excluding* LCMF elevation, gravity-aware. If the 7i/9i gap is also
   ~3.5–4°, "compression" is wrong. If materially larger, LCMF is compressing.
2. **OPS speed contract audit.** `lcmf.py` may be receiving **line-of-sight** speed while
   the ballistic model uses it as **total launch speed**. A 17°→25° launch changes the
   projection factor 0.956→0.906 — enough to bias the grid search club-dependently.
3. **Four/five-frame jackknife.** Delete each pre-impact frame in turn; report the full
   range. The −31° and +25° attack angles should fail visibly.
4. **Impact-time perturbation.** Shift ±1 camera frame and ±1.4 ms. A result that changes
   qualitatively is not a measurement.
5. **Attack-angle robustness.** Robust per-club median. `∂L/∂A = −0.235`, so the club gap
   should barely move; if the headline changes, bad AoA was driving it.
6. **Coefficient envelope.** Sweep vertical 0.68–0.86, horizontal across the published
   range, bootstrap within club. No plausible coefficient turns 3.5° into 8°.
7. **Component-model disagreement.** Save raw elevation phase slope and the `two8` /
   `four4_path_tdm` outputs *before* winner selection; compare club gaps.
8. **Geometry perturbation.** Re-run LCMF across radar tilt, heights, tee distance, floor
   plane, channel phase. Reported uncertainty must include the induced between-club bias.
9. **Failure clustering.** Cross-tab per shot: CAD loft rejection, AoA instability, radar
   phase-span rejection, camera/radar fallback, LCMF disagreement, COR extremes. Clustering
   implies a common cause (timing/association), not benign noise.
10. **Same-point consistency.** Compare the camera centroid's predicted radial motion with
    IWR range/range-rate over the pre-impact frames. A drifting residual means camera and
    radar track *different points* and fusing them as one trajectory is invalid.
11. **Impulse feasibility grid.** Rejection test only — do not read the fitted strike as a
    measurement.
12. **Blind holdout.** Freeze everything first, then a reference session spanning both
    clubs with deliberate toe/heel and high/low strikes. *(needs new data)*

---

## 4. Validation routes that need the maintainer

- **Foot spray on the clubface.** Standard club-fitting practice, gives ~1–2 mm strike truth
  per shot. **The no-marker rule constrains the shipped product, not the measuring rig.**
  Maintainer already owns it; blocked only on daylight.
- **1:1 readout capture.** 320×200 is a 2× subsampled readout using ~20 % of the sensor's
  pixel throughput. 1280×800 at 1:1 doubles plate scale (ball 12.6 → 25 px, clubhead ~24 →
  ~47 px) at ~144 fps — still 2.4× TrackMan's 60 fps camera, which does markerless impact
  location. **Halves the ball-centre error; does NOT fix clubhead centroid migration.**
- **Lighter background behind the pre-impact clubhead.** Post-impact frames fit coherently
  (roll pinned −180°, IoU 0.63–0.70); pre-impact frames do not. Coming into the ball the
  head is dark metal against a dark hedge; after impact it is against the lit mat. **This is
  a contrast problem, fixable by aim or backdrop, and it lands on exactly the frames impact
  location needs.**
- **Tape measure, lens to ball.** Anchors the whole scale chain.

---

## 5. Two geometry questions only the maintainer can close

1. **Camera tilt.** The enclosure is tilted up 10°. The imagery says the ball sits **2.73°
   below the camera boresight** (sd 0.33° over 21 shots — solid). At +10° boresight that
   places the ball ~200 mm *above* the lens, while `ball_height_m: 0.04` puts it 166 mm
   *below*. Both cannot hold, and the 320×200 vertical half-FOV is only **12.1°**, so there
   is no slack. **Measure: is the ball higher or lower than the camera lens, and by how
   much?**
2. **The mesh's face-to-shaft angle.** Measured **77.89°** (well determined: six region
   choices agree to 0.15°, eigenvalue ratio 23.5, from the cylinder's surface normals).
   Maintainer states the CAD is ~36° loft, ~60° lie. Whether 77.89° is consistent with that
   depends on assumptions about the club's construction that were asserted, not verified.
   **Measure the face-plane-to-shaft-axis angle directly in CAD.** If the model's shaft stub
   is mis-oriented relative to its head, every fitted pose inherits the error.

Useful either way: both reference directions are now measured in the mesh frame —
face normal `[1, 0.003, 0.007]`, shaft axis `[−0.204, 0.288, −0.936]`.

---

## 6. Architecture: the fit is 2D

`fit_frame_6dof` maximises `iou(rendered_binary_mask, observed_binary_mask)`. The 3D mesh is
projected, **rasterised to a binary mask**, and compared region-to-region. Interior shading,
edge sharpness, gradients, depth and the raw pixels are all discarded at the comparison
step. The 3D only generates hypotheses.

Note also that **none of this is in the shipped product.** `grep -r "mesh\|silhouette\|iou"
src/openflight` returns nothing. The shipped path (`camera/club_delivery.py`) tracks the
shaft, takes `_head_end`, and fuses with IWR depth and OPS speed. The mesh work is
research only.

Four ways to make it genuinely 3D, in cost order:
1. **Radar range as a hard constraint** — available now, 3× more precise, unused
2. Score against raw pixels rather than a binary mask (region-based trackers)
3. A real second viewpoint (stereo) — what Rapsodo's patent requires
4. Don't use the camera for orientation at all — the D-plane path, which is what all three
   comparators actually do

---

## 7. Working conventions

- **Overlays draw the model's own output.** Never hand-rolled visualisation geometry.
- **Never pad an outline.** If it looks wrong, the fit is wrong.
- **Fail closed.**
- **Never conclude "X does not exist" from a truncated search.**
- **Comparators are Trackman 4, Full Swing KIT, Mevo Gen 2.** Never Trackman iO.
- **No strobe.** Ambient only.
- **Verify geometric assumptions before building on them** — see §0.

---

## 8. Files

- Public page: <https://claude.ai/code/artifact/42a6f3f4-0b9b-4faf-bf9c-1ff45b4e94dd>
  — **sections 09b, 10c and 11b contain the §0 errors and need correcting**
- `research/silhouette_poc/replay/head_split.py` + `tests/test_head_split.py`
- `research/silhouette_poc/replay/fit_real.py` — `fit_frame_6dof`, the 2D IoU objective
- `src/openflight/camera/club_delivery.py` — the actual shipped camera path
- `src/openflight/iwr6843/lcmf.py`, `multipath.py` — the vertical-launch estimator (test #2)
- `docs/superpowers/specs/2026-08-25-impact-location-status-checklist.md` — needs §0 applied
- Data: `C:\Users\harjo\Downloads\openflight_session_20260825_181734_filtered\...`
  **Exclude `shot_001` — it ran the old 495 µs / gain 15 settings and is 99.8 % clipped.**
