# Agent handoff — OpenFlight impact location

> ## ⚠ SUPERSEDED IN PART — read `2026-08-26-agent-handoff.md` §0 first
>
> Several claims below are now known to be wrong, most importantly the
> **"dynamic loft compressed to 55 %"** result, which was refuted: the 4.4° gap follows
> algebraically from the measured 3.5° launch gap, and **static loft is not delivered
> loft**. Also corrected there: the withdrawn 1.51 mm toe–heel figure, the radar elevation
> aperture, the roll-grid reversal, and two geometry questions stated as conclusions.


**Read this first, then `2026-08-25-impact-location-status-checklist.md` for the full
status matrix.** Branch: `feat/silhouette-poc`, 144 tests passing.

**Revised 2026-08-25 (evening)** against session `20260825_181734` — 22 shots, camera +
IWR6843 + TrackMan truth, the first properly exposed capture and the first with radar.
Everything the previous version of this file listed as an open bug has been tested against
it. Read §1 before re-deriving anything.

> ## READ FIRST — a handedness bug invalidated part of the earlier analysis
>
> The capture applies `image[:, ::-1]` before saving (`triggered_buffer.py:159`) because
> `mirror_horizontal: true`. **The golfer is right-handed — confirmed with the person who
> took the shots — so the stored frames show a LEFT-handed club.** We were fitting a
> right-handed mesh to left-handed pixels, which no rotation can do.
>
> Un-mirroring the frames (`F[:, :, ::-1]`) raises IoU **0.548 → 0.633 on all 21 shots**,
> every shot improving, and collapses the two-basin roll degeneracy to one.
>
> **Do not mirror the mesh instead.** Un-mirror the FRAMES: it matches the
> `horizontal_pixel_sign` convention `server.py:2733` already uses, restores physical
> orientation so toe/heel and draw/fade carry correct signs, and keeps one canonical
> right-handed mesh per club.
>
> **Correction to the previous revision:** it stated that widening the roll grid is not a
> fix. That was true on mirrored data, where widening only moved the answer between two
> equally-bad basins. With handedness fixed there is ONE correct basin, it sits near
> −150°, and the shipped grid (−60…+90) cannot reach it. **Widening it now matters.**

---

## 1. What the new session settled

`C:\Users\harjo\Downloads\openflight_session_20260825_181734_filtered\openflight_session_20260825_181734_filtered\`

**The exposure fix did exactly what it should have, and stopped exactly where it should
have.** Detection and tracking were exposure problems and are now largely solved. Pose was
never an exposure problem, and did not move.

| | Old capture | New session |
|---|---|---|
| Impact zone clipped | 83–94 % | **~1 %** |
| Ball detection | inverted polarity, unreliable | **21 / 22, zero mis-detections** |
| Club frames tracked per shot | ~8 on one shot | **17** (median, 21 of 22); session total 75 → **349**, 4.7× |
| Mesh fit | IoU 0.547 on a handful of frames | IoU **0.548**, **349 of 349** tracked frames |
| Pose coherence | yaw/pitch/roll jump >100° | **unchanged — still >100°** |

### 1.1 The session is three exposures, not one

`shot_001` ran the **old** settings (495 µs, gain 15.0, **99.8 % clipped**). Exclude it from
everything. Shots 2–11 are **247 µs / gain 4.00**, shots 14–29 **298 µs / gain 5.00**.
Ambient drained over the 16-minute session: p99 brightness 192→158 and 206→171 in the two
blocks, so the last shots carry ~19 % less signal.

### 1.2 Resolved — do not re-open

- **`detect_reference_ball`'s 64 px false lock was an exposure artefact.** On the new data
  it fires 22/22 and agrees with `find_teed_ball` to a **median 1.17 px**. The failure
  reproduces on exactly one shot — `shot_001`, the clipped one — where it returns a 21 px
  "ball" 32 px off. Same rig, same code, only exposure differs. **Downgraded.**
- **"Clubhead contrast collapses by F73, nothing left to track" is void.** The head tracks
  continuously to F80–F81 on every well-exposed shot.
- **"Ball contrast inverts sign within one shot" is void.** The ball is +87 to +124 DN at
  address on all 21 good shots.
- **The mesh frame is NOT arbitrary.** The previous handoff said `poc_7iron.npz` has "an
  arbitrary orientation baked in by the normalisation step". It does not: the asset is
  face-anchored — **+x is the face normal to machine precision, +y is the heel, +z is the
  sole** (verified by rendering it; manifest `normalization: geometric-face-anchor-v2`).
  Physical bounds *can* be made physical, once the address zero-pose is pinned.

### 1.3 Corrected numbers — every prior accuracy figure used wrong inputs

| Quantity | Measured (21 shots) | Previously used |
|---|---|---|
| Plate scale at the ball | **0.296 px/mm** (0.263–0.320) | 0.656 simulated; 0.327 old capture |
| Camera-to-ball range | **1576 mm** (1456–1777) | 1425 mm |
| Exposure | **247 / 298 µs** | 500 µs simulated; 997 µs old |
| Ball diameter | **12.63 px** (11.20–13.67) | 13.97 px |
| Clubhead motion blur | **2.97 px** (2.54–3.39) | never computed |
| Inter-frame clubhead step | **22.8 px** (19.4–25.7) | never computed |

**Range is not a rig constant** — it varies ±10 % *between shots* because the golfer
re-tees. Measure it per shot from the ball, which the detector already does.

**One pixel of error is 3.38 mm at the ball.** That is the ceiling number for a metric
quoted in millimetres from the face centre.

**Gate 0 now has independent support:** the camera's ball-diameter range (1576 mm, from the
datasheet 2.8 mm lens and nothing else) matches the radar's configured
`tee_slant_range_m = 1.524` to 5 cm. Caveat — 1.524 m is a round 5 ft and may be nominal, so
a tape measurement still has value.

### 1.4 Trigger and timing, measured properly for the first time

- Acoustic trigger lags impact by **6.0 ± 0.68 frames (12.8 ± 1.5 ms)**, range 5–7, over 21
  shots — not inferred from one. It still scales with microphone placement so it is not a
  universal constant, but within one fixed installation it is tight enough to calibrate out.
- **Camera and radar locate impact independently and agree to sd 1.41 ms = 0.66 camera
  frames** (0.62 ms of which is the camera's own whole-frame quantisation). Camera method:
  ball-disc contrast collapse. Radar method: `shot.py::impact_time_s`, back-extrapolating
  the ball's range walk to the tee. Nothing is shared but the swing.

---

## 2. What the radar contributes — first look ever

Config is the shipped default profile `iwr6843_l3dump_wide_24f3ms_53bin_iq16.cfg`:
**3 TX, 12 loops, 24 frames × 3 ms = a 72 ms window**, 4.69 cm range bins, range window
stepping outward (bins 20→32→47) to follow the ball. Roughly 4–5 frames are pre-impact.

**Is the clubhead separable from the golfer's body and arms?**

**In range and speed, yes, decisively.** `find_club` selects a clubhead track on **22 of 22**
shots at **0.83–0.98×** the measured club speed (median 0.91×), back-extrapolating to the
tee within **1.9–8.8 cm** (median 5.4 cm). In the range–time map the golfer is a bright band
at 1.85–1.95 m that **never walks in range** for the whole 72 ms, while the clubhead is a
clean diagonal from 1.05→1.35 m in the 12 ms before impact. The separation is structural:
the golfer stands beyond the tee and a pre-impact clubhead is always short of it, which is
exactly the gate `find_club` already uses.

**In angle, no.** Club path is rejected on **all 22** shots with `rejected_phase_span`.
Candidate attack angle comes out **−25° to −37°** where a real iron is ≈ −4°; candidate club
path spreads across **45°**. Four pre-impact frames is not enough phase span.

**So the division of labour is settled by measurement: radar supplies clubhead range and
timing; the camera supplies transverse geometry.** This is the same split Trackman 4 uses —
impact location falls out of the fusion, not out of frame rate.

### 2.1 Upstream defect worth reporting

`tracking.mti_filter` de-interleaves with a hardcoded `cpf // 2, 2`, and
`shot.PreparedShotDump` documents itself as a "Decoded two-TX capture". **The shipped
default production profile is 3 TX** — the one this entire session ran on. Analysis code
here de-interleaves by the header's own `n_tx` instead.

### 2.2 One thing I got wrong, corrected

`Geometry.loop_time()` indexes `frame_time_offsets_s` **directly**, so those values are
absolute offsets from window start, not deltas — despite `dump.py`'s docstring calling them
"elapsed microseconds since the previous retained frame". The array is a uniform
`arange(0, 72000, 3000)`. Reading them as deltas gives a bogus 828 ms window and the
conclusion that the dump has no pre-impact frames. It has 4–5.

---

## 3. The pose problem — same blocker, now with a measured cause

The 6-DOF fit succeeds on **349 of 349** tracked frames at median IoU **0.548**, and the
recovered orientation is still not trustworthy. Over 326 consecutive frame pairs:

| Fitted parameter | Median jump | p90 | Max | Pairs jumping >45° |
|---|---|---|---|---|
| yaw | 13.1° | 65.0° | 120.0° | 17 % |
| pitch | 20.0° | 100.0° | 140.0° | **33 %** |
| roll | 22.5° | 75.0° | 157.5° | 19 % |
| range | 120 mm | 368 mm | 730 mm | — |

**Fitted range wanders 1119–2113 mm** while the measured ball range is 1456–1777 mm — which
is precisely why item 1 in §6 is worth doing first: the radar pins that to a 4.69 cm bin.

### 3.0 How much of the degeneracy was the handedness bug

Un-mirrored, over 21 shots and 349 frames:

| | mirrored | un-mirrored |
|---|---|---|
| IoU median-of-medians | 0.548 | **0.633** |
| roll basins within 0.03 of best | **2**, 150° apart, 0.009 apart | **1** |
| peak IoU over the roll circle | 0.377 | **0.437** |
| yaw pairs jumping >45° | 17 % | 18 % |
| pitch pairs jumping >45° | **33 %** | **19 %** |
| roll pairs jumping >45° | 19 % | 22 % |
| range median jump | 120 mm | 122 mm |

**The bug was real and fixing it genuinely improves the fit. It did NOT fix pose
coherence** — about a fifth of adjacent frames still disagree by more than 45°. The
surviving basin is ~60–70° wide, so noise still moves the answer around inside it.

The evidence below changed; the conclusion did not.

### 3.1 The degeneracy, quantified on real pixels

Sweeping roll over the full circle, taking the best IoU over yaw and pitch at each roll:

- the score varies only between **0.247 and 0.408 across all 360°**
- the two best basins sit at **−180° and +30° — 150° apart**
- and they differ by **0.009 IoU, about four pixels** on a 400 px mask

**Superseded in part.** The two peaks 150° apart were largely the handedness bug — see
§3.0. Un-mirrored they collapse to one basin. What survives is the FLATNESS: even with a
single basin it is ~60–70° wide, so IoU still cannot pin orientation to better than tens of
degrees. That remains consistent with the 0B-1 simulation, which predicted a flat landscape
on clean synthetic data with no mirror anywhere.

The mesh frame detail is still worth knowing: mesh +z is the SOLE, and the solver's zero
pose maps +z to world *up*, so roll 0 renders the club upside down and the address pose
needs roll near ±180°.

### 3.2 Landmarks — first real-pixel test, and one works

Measured as displacement per frame **relative to the head's own centroid**, so real club
travel is removed and only sliding over the surface remains. 48 consecutive frame pairs,
four shots:

| Candidate | Median step | p90 | Max |
|---|---|---|---|
| **shaft axis angle** | **0.87°** | 1.85° | 3.30° |
| toe extreme | 5.76 px | 13.73 px | 15.59 px |
| heel extreme | 5.67 px | 17.72 px | 25.31 px |
| hosel end of the head | 8.74 px | 20.86 px | 23.70 px |

At 0.296 px/mm the point landmarks slide **19–30 mm per frame across a head 80 mm wide** —
extremal-contour artefacts, exactly as predicted.

**The shaft axis is the exception.** 0.87°/frame and monotonic through impact (108.4 →
111.7 → 113.8 → 115.3 → 116.6 → 117.7 → 118.5° on one shot). It is fitted to several hundred
pixels rather than one, and it is *labelled*. **It did not exist in the old capture** — the
shaft had no contrast — which is why nobody proposed it.

Be honest about what it is: roughly one rotational degree of freedom, and it says nothing
directly about face angle or dynamic loft. A strong temporal anchor and a lie/toe-droop cue,
not a solution.

**The hosel junction is not directly observable.** The neck is the lowest-contrast part of
the club, so background subtraction breaks there: in all but the frames closest to impact
the head and shaft are separate components with a real 17–70 px gap.

---

## 4. Code landed this session

`research/silhouette_poc/replay/head_split.py` + `tests/test_head_split.py` (9 tests).

**Why it exists.** The shipped tracker treats one moving connected component as the
clubhead, and got *worse* on better data — 4 tracked frames per shot, with 1169 px masks
where a clubhead is ~400 px. On a properly exposed capture **the shaft is a strong moving
object** and merges with the head for the frames closest to impact, putting the component
centroid halfway up the shaft. "One moving blob is the clubhead" was inherited from a
capture in which the shaft had no contrast.

Head and shaft separate on **measured** quantities with no overlap and nothing tuned:

| Component | Max inscribed radius | Pixels ≥ 4 px | Reach from head core |
|---|---|---|---|
| shaft, isolated | 2.0–3.0 px | **0.0 %** | — |
| head, isolated | 5.0–11.6 px | 12–49 % | 4.2–40.8 px |
| head + shaft merged | 5.0–9.8 px | 3–20 % | 145.4–187.2 px |

The split is a watershed on the distance transform seeded by the head core, so **the head
keeps its own observed boundary** — never eroded, never padded; only the cut across the neck
is synthetic. It preserves 119 of 119 already-isolated heads intact and never returns a
piece of shaft as a head.

Result: **4 → 17 tracked frames per shot**, F63–F80, on 21 of 22 shots.

---

## 5. Working conventions to preserve

- **Overlays draw the model's own output, never hand-rolled visualisation geometry.** The
  club outline is the projected 3D mesh at its fitted pose; the observed silhouette is drawn
  faint behind it. Drawing the segmentation contour alone looks perfect *by construction*.
- **Never pad an outline.** If an outline looks wrong, the fit is wrong.
- **Fail closed.** Stop tracking rather than latch onto an artefact.
- **Never conclude "X does not exist" from a truncated search.** `grep … | head -N` once
  produced a false "there is no clubhead tracking" claim that was committed and withdrawn.
- **Comparators are Trackman 4, Full Swing KIT, Mevo Gen 2.** Never Trackman iO.
- **No strobe.** Ambient-light design.

---

## 6. Next moves, in order

0. **Un-mirror the frames.** Everything below assumes it. One line, biggest single win
   measured so far (+0.085 IoU on every shot).
1. **Radar range as a hard constraint on the mesh fit.** Measured head-to-head over 21
   shots, pre-impact frames only: the camera's fitted clubhead range scatters at
   **sd 99 mm**, the radar's at **sd 30 mm**, and they do not correlate shot to shot
   (r = +0.20). The camera contributes nothing to range worth keeping. Pin it with the
   radar and drop a dimension from the search.
2. **Shaft-axis constraint + temporal smoothness.** Smoothing failed before because the
   parameterisation had nothing physical to smooth toward. The shaft supplies 0.87°/frame of
   real, labelled orientation signal to anchor it.
3. **Wire `impact_time_s` into the camera path.** Both estimators now agree to 0.66 frames;
   the camera still treats the lagging acoustic trigger as impact.
4. **A-v3 accuracy re-run at real settings** — 247/298 µs, **0.296 px/mm**, 467.6 fps,
   2.97 px blur, frame rate swept. Still never run, and it decides feasibility. Every
   accuracy number this project has produced used 500 µs and 0.656 px/mm, both wrong.
5. **Impact location** — still downstream of a trustworthy pose.

**Known not to work, do not retry:** widening the roll grid; physical bounds alone; temporal
smoothness alone.

---

## 7. Artifacts and key files

- Public page: <https://claude.ai/code/artifact/42a6f3f4-0b9b-4faf-bf9c-1ff45b4e94dd>
  (sections 08–08d cover this session)
- `research/silhouette_poc/fusion/ball_detect.py` — production ball detection.
  **Call it with `expected_radius_px=6.5, radius_tolerance=0.30`**: 14/22 → 21/22.
  Counter-intuitively, passing the *true* impact frame instead of the lagging trigger makes
  it worse (9/22) — its departure test needs the club to have swept clear of the tee, and
  the trigger's lag accidentally guarantees that.
- `research/silhouette_poc/replay/head_split.py` — head/shaft separation, 9 tests
- `research/silhouette_poc/replay/fit_real.py` — 6-DOF mesh fitting on real pixels
- `research/silhouette_poc/replay/make_overlay.py` — overlay generator (hardcoded to the
  **old** capture path; needs parameterising)
- `src/openflight/iwr6843/club.py` — `find_club`, `estimate_club_path`
- `docs/superpowers/specs/2026-08-25-impact-location-status-checklist.md` — full status

---

## 8. External research survey, 2026-08-25 (Codex)

`openflight_club_pose_research_survey.md` in the repo root. Sourced and largely sound.
**One caveat before building on it:** its §3 uses our 150° / 0.009-IoU result as primary
evidence that silhouettes cannot encode orientation. That number was largely the handedness
bug (§3.0). The conclusion survives the correction — post-fix coherence barely moved — but
the supporting evidence should be corrected in that document so nobody later discards a
conclusion that is actually right.

**What it establishes that changes our plan:**

- **§4.2 — a monocular shaft line is not a 3D hosel tangent.** One image line is a family
  of 3D directions; head range gives only one depth anchor. This is the maintainer's own
  shaft-flex objection, sharpened, and it is correct.
- **§F — averaging club specs must be split.** Across six OEMs per category:
  players 7i loft **33.0 ± 0.6°**, game-improvement **28.6 ± 0.5°** — a **4.4° gap**, so a
  cross-category loft mean is badly biased. Lie is stable: **62.25 vs 62.42°**, 0.2° apart.
  **Average the lie; take loft as an input or a category choice.**
- **§4.4 — D-plane face recovery is not independent of impact location.** Off-centre strike
  changes launch (0.022–0.040°/mm), so inferring face from launch and impact from face is
  coupled. Usable for degree-scale pose validation; not for millimetres.
- **§G — iron D-plane coefficients** (Wood/Henrikson/Broadie, 157 golfers): horizontal
  **69 ± 3 %** face, vertical **81 ± 5 %** dynamic loft. A robot 7-iron gave 63 ± 4 %, so
  the coefficient is not universal.
- **§E — shaft mechanics.** Torsional twist is order **1°** (0.4° open/closed measured on
  three golfers, driver), so the twist objection is real but small. Droop is the bigger
  unknown: driver bend-lie is **8–10°** and there is **no iron cohort data at all**.

**Two corroborations of the hosel-anchored approach:**

- TrackMan's own technical description establishes face centre by **an adjustable offset
  from the hosel**.
- Rapsodo patent **US20230364468A1** treats the shaft segment between hosel and ferrule as
  non-bending and uses it for pose recovery (stereo, and with no deformation measurement
  validating the assumption).

**Where the survey is wrong or incomplete:**

- **§4.5** says radar angular recovery failed so 3D club path may be unavailable. The radar
  *angle* did fail 22/22, but the camera+radar *fused* path produced values on 21/22
  (`experimental_fused_club_path_deg`, median 4.8°). Unvalidated, not absent.
- **§5 says millimetric impact location cannot be demonstrated scientifically** without an
  independent reference. **This is wrong, and it is the most consequential error in the
  document.** The no-marker rule constrains the shipped product, not the validation rig.
  **Foot spray or impact tape on the face** gives ~1–2 mm ground truth per shot,
  photographed after each swing — standard club-fitting practice. The maintainer already
  owns foot spray and uses it. That turns "unvalidatable" into measurable in an afternoon,
  and it is the missing piece in the survey's recommended validation gate.

---

## 9. Swing plane as the missing depth constraint — TESTED, DOES NOT WORK

Codex §4.2 says a 2D shaft line cannot give a 3D direction without depth at two points.
The head's own 3D track was the proposed second constraint: camera bearing per frame plus
radar range gives a trajectory, and if it is planar the shaft lies close to that plane.

**It does not survive contact with the data, and the first version of this test was wrong.**

An initial gate asked "does a plane FIT the head's 3D track?" and it passed at 3.8 mm rms
over a 343 mm track. **That test is vacuous:** over the ~5 pre-impact frames the head travels
in a nearly straight line, and *any* plane containing a line fits it. The right question is
whether the track curves enough to DEFINE a plane.

Measured by the singular values of the centred track (`step17_cond.py`), where the plane
normal's uncertainty is `atan(sv2 / sv1)`:

| Window | Frames | Span | sv1/sv2 | Normal uncertainty |
|---|---|---|---|---|
| pre-impact only | 4–6 | ~343 mm | 1.8 – 15.2 | **13° median, up to 29°** |
| whole tracked pass | 15–18 | ~1150 mm | 3.9 – 16.9 | **7° median, 3.4–14.5** |

A 7–13° uncertainty in the plane normal propagates straight into the shaft direction, and
1° of face error is 0.7 mm at a 40 mm lever. **Codex §4.2 stands; this workaround does not
rescue it as posed.**

What might, and is untested:

- **Per-frame radar range.** The dump reports one club range plus a rate, so depth had to be
  modelled as linear, which also partly smooths the axis being tested for flatness.
- **An arc model rather than a free plane.** The head moves on a circle, which is a stronger
  constraint than "some plane" and is far better conditioned on a short track.
- More frames with genuine curvature.

### 9.1 Camera geometry — an unresolved inconsistency, and the measurement that settles it

**Maintainer states the whole enclosure is tilted UP 10°**, camera height 8.1 in (205.7 mm).
Take that as given. The code's assumed height of 209.55 mm is fine — 3.8 mm out.

The imagery gives one number very tightly. The ball sits **2.73° BELOW the camera's optical
axis** (observed y ≈ 147; principal point y ≈ 124.75 from the crop geometry — `sensor_crop`
(336,150,816,516), 2× subsampled, sensor optical centre (640,400)). Solved independently per
shot, **sd 0.33° across 21 shots**. That part is solid.

**The inconsistency.** With a +10° boresight, a ball 2.73° below it sits **+7.3° above
horizontal**, which at 1576 mm puts the ball roughly **200 mm ABOVE the camera lens**. The
model instead assumes `ball_height_m: 0.04` against a 205.7 mm lens — the ball 166 mm BELOW.
That sign flip is the whole disagreement.

It matters because the 320×200 mode's vertical half-FOV is only `atan(100/466.7)` = **12.1°**.
There is very little room: taking the shipped config literally (radar 152.4 mm, ball 40 mm,
tilt 10.4°) puts the ball **14.6° below boresight** — fine for a radar with a ~19° beam that
is aimed up to catch the climbing ball, but **outside the camera's FOV entirely**.

**Two candidate resolutions, distinguishable by inspection:**

1. **The hitting surface is raised relative to where the unit sits** (mat, tee box, unit on
   grass beside a platform). Then the ball IS above the lens, +10° is right for both sensors,
   and `ball_height_m: 0.04` is simply the wrong figure to feed the camera model.
2. **The camera sits at a different angle inside the enclosure than the radar** — a sensible
   design, since a wide-beam radar can afford to point up and a 24°-tall camera FOV cannot.

**The one measurement that settles it: is the ball HIGHER or LOWER than the camera lens, and
by how much?** Everything angular downstream depends on the answer, so it is worth a tape
measure before any further pose work.

**Do not repeat this mistake:** an earlier revision of this file asserted from the imagery
alone that "the camera is essentially level and the 10° is the radar's". That was one of two
possibilities stated as a conclusion. The imagery constrains the ball's angle *relative to
the camera boresight*, not the boresight's angle relative to the world.

**Note:** camera pitch is NOT the cause of the swing-plane lie error. Changing it moves the
recovered lie 1:1 (46° → 53° for a +7.3° pitch change) and away from the expected ~28°. The
ill-conditioned plane normal in §9 is the cause.

## 10. Hardware: where we actually stand against TrackMan 4

Worth recording because the intuition runs the wrong way on one of these.

**Radar — we win on range, lose on velocity and angle.**

| | OpenFlight | TrackMan 4 |
|---|---|---|
| Bands | 60 GHz IWR6843 + 24 GHz OPS243 | ~24 GHz + ~10 GHz |
| Range resolution | **4.69 cm** (~3.2 GHz sweep) | ~60 cm at 24 GHz ISM (~250 MHz) |
| Unambiguous velocity | ~13.9 m/s at 90 µs PRI — **a 45 m/s ball aliases** | ~83 m/s at 10 GHz — unambiguous |
| Downrange coverage | short; path loss goes as f² | full flight at X-band |
| Angular recovery | **failed 22/22** — ~30 mm aperture, ~19° beamwidth | large aperture, 40 kHz sampling over the whole flight |

The 13× range-resolution advantage is real and is why we can separate the clubhead from the
golfer's body at all. But **angle is what club path and face orientation need**, and that is
where we are weakest.

**Camera — the intuition is backwards, and the fix is free.**

| | OpenFlight | TrackMan 4 OERT |
|---|---|---|
| Resolution | **320×200** | 1280×720 |
| Frame rate | **467.6 fps** | 60 fps |

We have 8× the frame rate and **1/14th the pixels**. Impact location is limited by *spatial*
sampling — our clubhead is 20–40 px, and Codex §B found no literature demonstrating usable
pose below ~50 px. TrackMan does markerless impact location at 60 fps because frame rate was
never the constraint.

**The shipped 320×200 mode uses ~20 % of the sensor's pixel throughput** — it is a 2×
subsampled readout, trading resolution for frame rate we do not need.

| Mode | Plate scale | Ball | Clubhead | Frame rate |
|---|---|---|---|---|
| 320×200, 2× subsample (shipped) | 0.296 px/mm | 12.6 px | ~24 px | 468 fps |
| **1280×800, 1:1** | **0.592 px/mm** | **25 px** | **~47 px** | ~144 fps |
| 1:1 plus a longer M12 lens (~$15) | higher again | — | **100+ px** | ~144 fps |

A 1:1 readout is a capture-mode setting with no new hardware, and it attacks the exact limit
the whole survey identifies as fatal. The trade to check is ball-in-frame time, since a
narrower field gives fewer flight frames — but at 144 fps we would still have 2.4× TrackMan's
camera rate.
