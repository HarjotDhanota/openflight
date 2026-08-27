# Impact location — master status and plan

> **SUPERSEDED TWICE.** Apply `2026-08-26-agent-handoff.md` §0 first, then
> `2026-08-26-falsification-results.md`, which closes falsification tests 1, 2, 5 and 9 and
> both maintainer geometry questions. In particular: the small 7i/9i launch gap is **real**
> (independently reconstructed), the OPS speed contract is **not** a club-dependent bias,
> the camera is **level** (the 2.73°-below-boresight figure used the wrong focal length),
> and the mesh's anomaly is its **face loft**, not its shaft stub.


> ## ⚠ SUPERSEDED IN PART — read `2026-08-26-agent-handoff.md` §0 first
>
> Several claims below are now known to be wrong, most importantly the
> **"dynamic loft compressed to 55 %"** result, which was refuted: the 4.4° gap follows
> algebraically from the measured 3.5° launch gap, and **static loft is not delivered
> loft**. Also corrected there: the withdrawn 1.51 mm toe–heel figure, the radar elevation
> aperture, the roll-grid reversal, and two geometry questions stated as conclusions.


**The authoritative record.** Status as of 2026-08-25, **re-measured against session
`20260825_181734`** — 22 shots, 7-iron and 9-iron, camera + IWR6843 + TrackMan truth, and
the first capture with the exposure fixed. A ✅ means demonstrated on **real data**, not
that code exists.

> **Read this first.** The exposure fix resolved detection and tracking and did **nothing**
> for pose, because pose was never an exposure problem. Items moved out of §C below are
> genuinely done; §C5 is unchanged and now has a measured explanation.
>
> **A handedness bug was then found and fixed.** The capture saves mirrored frames
> (`mirror_horizontal: true` → `triggered_buffer.py:159`) and the golfer is right-handed,
> so we were fitting a right-handed mesh to left-handed pixels. Un-mirroring the frames
> raises IoU **0.548 → 0.633 on every one of 21 shots**. **It did not fix pose coherence.**
>
> **Validation is no longer blocked.** Foot spray on the clubface gives ~1–2 mm ground truth
> for impact location per shot. The no-marker rule constrains the shipped product, not the
> validation rig.

**The session is three exposures, not one.** `shot_001` ran the old settings (495 µs, gain
15.0, **99.8 % clipped**) and must be excluded from everything. Shots 2–11 ran **247 µs /
gain 4.00**, shots 14–29 **298 µs / gain 5.00**; whole-frame clipping 0.12–0.26 %. Ambient
drained across the 16-minute session — p99 brightness 192→158 and 206→171 in the two
blocks — so the last shots carry ~19 % less signal than the first.

Standing constraints: **no strobe**; comparators are **Trackman 4, Full Swing KIT,
Mevo Gen 2** (never iO — ceiling-mounted, not comparable).

---

## A. DONE — demonstrated on real data

| Area | What | Evidence |
|---|---|---|
| Geometry | **Gate 0 settled.** Lens is 2.8 mm (not the inferred 6 mm), `focal_px` = 466.7 in the shipped 320×200 2× subsampled mode. **Plate scale and range are per-rig, not constants** — see the row below; the 0.327 px/mm / 1425 mm / 14 px figures belong to the superseded capture | Datasheet lens + measured ball, now corroborated by the radar's tape range |
| Capture truth | New session: **247 µs / gain 4.00** (7-iron) and **298 µs / gain 5.00** (9-iron), 467.6 fps, duty cycle 13.9 %. The old 997 µs / gain 15.94 applies only to the superseded capture and to `shot_001` | `frames.npz` metadata, 22 shots |
| Plate scale | **0.296 px/mm** median (0.263–0.320), camera-to-ball **1576 mm** median (1456–1777). Varies ±10 % *between shots* because the golfer re-tees, so it is **not a rig constant** — measure it per shot from the ball. **One pixel = 3.38 mm at the ball** | 21 shots |
| Gate 0 corroborated | The camera's ball-diameter range (1576 mm, datasheet 2.8 mm lens) matches the radar's configured `tee_slant_range_m = 1.524` to **5 cm** — independent support for `focal_px = 466.7` | camera vs radar config |
| Ball detection | **21 of 22 shots**, zero mis-detections, once the expected radius Gate 0 already supplies is passed in (`expected_radius_px=6.5, radius_tolerance=0.30`). Without it, 14/22 with 3 silent mis-detections. Polarity is no longer inverted at address — that was a clipping artefact | 22 shots |
| Club tracking (new) | **17 frames/shot median** (15–18) on 21 of 22 shots, spanning **F63–F80** — up from **4**. Session totals **75 → 349 usable frames, 4.7×**. Requires separating the shaft from the head | 22 shots |
| Impact timing | Camera (ball-disc contrast collapse) and radar (`impact_time_s`, back-extrapolated range walk) agree to **sd 1.41 ms = 0.66 camera frames** over 21 shots, of which 0.62 ms is the camera's own whole-frame quantisation | 21 shots |
| Trigger lag | Acoustic trigger lags impact by **6.0 ± 0.68 frames (12.8 ± 1.5 ms)**, range 5–7, measured on 21 shots rather than inferred from one | 21 shots |
| Radar clubhead | `find_club` selects a clubhead track on **22 of 22** at **0.83–0.98×** the measured club speed, back-extrapolating to the tee within **1.9–8.8 cm** | 22 shots |
| Circle fit | Geometric (Kasa + Gauss-Newton). Exact on exact points at any arc span; the previous algebraic fit was biased **−44 % at 120°** | test suite |
| Ball selection | By **departure**, not shape. Solves multiple balls on the mat by construction | 3 tests |
| Club tracking (old capture) | Continuity tracker, seeded where the head is clearest, walking forward and backward | F62–71 verified correct |
| Trigger | Ball-departure detection validated: **43 px steady for 23 frames, then gone, zero false absences** | phone video |
| Pose | Mesh fitting runs on **real pixels** and succeeds on **349 of 349 tracked frames**, median IoU **0.548** (0.494–0.596 per shot). The overlap is fine; the orientation is not — see §C5 | 21 shots |
| Fusion | Camera+radar+speed fusion **already exists upstream** and is merged | `camera/club_delivery.py`, `iwr6843/club.py` |

## B. MEASURED LIMITS — not bugs, physical facts about this capture

These were properties of the one overexposed capture. Struck through = **void**, measured
against 21 well-exposed shots.

| Finding | Number | Status |
|---|---|---|
| ~~Impact zone clipped at 255 — 83–94 %~~ | now **~1 %** | **void** |
| ~~Clubhead contrast F73–79: 19–80 px, nothing left to track~~ | head tracked continuously to **F80–F81** on every shot | **void** |
| ~~Ball contrast inverts sign within one shot~~ | ball is **+87 to +124 DN** at address on all 21 | **void** |
| Acoustic trigger lag behind impact | **6.0 ± 0.68 frames (12.8 ± 1.5 ms)** | refined, and tight enough to calibrate per installation |
| Unmodelled lens distortion → club path / AoA | ~2.7° at 640×400; **0.06°** at impact-zone radii in the shipped 320×200 mode | superseded |
| Two-point velocity differencing | ~0.65° avoidable | unchanged |
| **Silhouette orientation signal** | best-over-yaw/pitch IoU spans only **0.247–0.408 across all 360° of roll**; the two best basins are **150° apart and differ by 0.009 IoU ≈ 4 px** | **new, and it is the pose blocker** |
| Motion blur, clubhead | **2.97 px** (2.54–3.39) at real exposure | new |
| Inter-frame clubhead step | **22.8 px** (19.4–25.7) | new |

## C. NOT DONE — required for a functional system

### C1. Capture
- ✅ **Exposure calibrated.** Done — 247 µs / gain 4.00 and 298 µs / gain 5.00. Clipping 83–94 % → ~1 % in the impact zone.
- ✅ **Simultaneous camera + IWR L3 capture** — taken, 22 of them.
- ❌ Shot detection without the microphone — validated in principle, not implemented. **The trigger's 6.0 ± 0.68 frame lag is now measured**, so it can be calibrated per installation in the meantime.
- ❌ Ball placement zone + READY state — specified, not built.

### C2. Geometry
- ❌ **Lens distortion correction.** No model anywhere in `src/openflight`. Lower priority than believed: **0.06° at impact-zone radii** in the shipped 320×200 mode.
- ⚠️ Camera-to-ball distance. The radar's configured `tee_slant_range_m = 1.524` and the camera's ball-diameter estimate (1576 mm median) agree to 5 cm, but 1.524 m is a round 5 ft and may be nominal rather than freshly tape-measured. **One tape measurement still settles it.**
- ⚠️ **Camera height confirmed (8.1 in = 205.7 mm; assumed 209.55 is fine). Tilt is UNRESOLVED and blocking.** Maintainer states the whole enclosure is tilted up 10°. The imagery says the ball sits **2.73° below the camera boresight** (sd 0.33° over 21 shots) — solid. But at +10° boresight that places the ball ~**200 mm ABOVE the lens**, while the config's `ball_height_m: 0.04` puts it 166 mm below. Both cannot hold. The vertical half-FOV is only 12.1°, so there is no slack. **Settle it with one tape measure: is the ball higher or lower than the camera lens, and by how much?** Every angular result downstream scales with the answer.
- ❌ **Readout mode is the biggest available accuracy lever and is set wrong.** The shipped 320×200 mode is a 2× subsampled readout using ~20 % of the sensor's pixel throughput. A 1:1 1280×800 readout doubles plate scale (0.296 → 0.592 px/mm; ball 12.6 → 25 px; clubhead ~24 → ~47 px) at ~144 fps — still 2.4× TrackMan's 60 fps camera, which does markerless impact location. No literature demonstrates usable pose below ~50 px, so this directly attacks the binding constraint.

### C3. Detection
- ✅ Ball detection across 22 shots, two clubs, a fading ambient light and ±10 % range variation: **21/22, zero mis-detections**.
- ❌ Ball sizing across varied distances — this session spans 11.2–13.7 px only.
- ❌ Robustness across venues — still **one** venue; everything is tuned to an outdoor bay.

### C4. Timing
- ✅ **Camera and radar impact times cross-validated** to sd 1.41 ms (0.66 camera frames) over 21 shots. Two independent methods, nothing shared but the swing.
- ❌ Retrospective impact time **wired into the camera path**. `iwr6843/shot.py::impact_time_s` demonstrably works on this session; the camera still does not consume it.

### C5. Pose — **the blocker, unchanged, and now explained**
- ❌ **Pose is not temporally coherent.** Reproduces on well-exposed pixels, measured over 326 consecutive frame pairs from 21 shots:

  | Fitted parameter | Median jump | p90 | Max | Pairs jumping >45° |
  |---|---|---|---|---|
  | yaw | 13.1° | 65.0° | 120.0° | 17 % |
  | pitch | 20.0° | 100.0° | 140.0° | **33 %** |
  | roll | 22.5° | 75.0° | 157.5° | 19 % |
  | range | 120 mm | 368 mm | 730 mm | — |

  Fitted range wanders **1119–2113 mm** while the measured ball range is 1456–1777 mm.
  **Better exposure changed nothing here.**
- ✅ **Handedness bug found and fixed.** The two basins 150° apart were largely an artefact of fitting a right-handed mesh to mirrored pixels. Un-mirrored they collapse to **one** basin and peak IoU rises 0.377 → 0.437.
- ❌ **What survives is the FLATNESS.** Even with a single basin it is **~60–70° wide**, so IoU still cannot pin orientation to better than tens of degrees. Consistent with the 0B-1 simulation, which predicted a flat landscape on clean synthetic data with no mirror anywhere.
- ⚠️ **Correction to the previous revision of this file:** it said widening the roll grid is not a fix. True on mirrored data; **wrong now.** With handedness fixed the single correct basin sits near −150°, unreachable from the shipped (−60…+90) grid.
- ❌ Temporal smoothness constraint.
- ❌ **Radar range as a hard constraint** — measured head-to-head over 21 shots, pre-impact frames only: camera fitted clubhead range scatters at **sd 99 mm**, radar at **sd 30 mm**, and the two **do not correlate shot to shot (r = +0.20)**. The camera contributes nothing to range worth keeping. Pin it and drop a dimension.
- ❌ **Swing plane as the missing depth constraint — TESTED, does not work.** The plane *fits* the head's 3D track to 3.8 mm, but that test is vacuous: over ~5 pre-impact frames the head is nearly a straight line and any plane containing a line fits. Conditioning (`sv1/sv2`) gives a plane-normal uncertainty of **13° median over the pre-impact window, 7° over the whole pass**. That propagates straight into shaft direction. Untested alternatives: per-frame radar range, and an **arc** model rather than a free plane.
- ❌ Physical bounds on loft and lie. Note the mesh frame is **not** arbitrary as previously recorded: `poc_7iron.npz` is face-anchored (+x face normal, +y heel, +z sole), so bounds *can* be made physical once the address zero-pose is pinned.
- ❌ Any ground-truth pose to validate against.

### C6. Impact location
- ❌ **Does not exist anywhere.** No match for `impact_location`, `strike_location`, `face_impact` or `gear_effect` in `src/openflight`.

### C7. Validation
- ❌ **A-v3 accuracy re-run at real settings.** Every accuracy number this project has produced was simulated at 500 µs and 0.656 px/mm — **both wrong**. There is currently **no validated accuracy figure for real hardware**.
- ✅ **A ground-truth route exists.** Foot spray or impact tape on the clubface marks the strike point to ~1–2 mm; photograph the face after each shot. The system under test stays markerless — only the measurement of its accuracy uses the spray. This closes the gap the external survey called scientifically unbridgeable.
- ❌ Not yet collected. Needs one session with spray applied per shot and the face photographed.

---

## D. The pose approach — landmark correspondence, not blob overlap

**Maintainer's proposal, and it is better than what is implemented.** Rather than scoring
outline-against-outline as an undifferentiated blob, identify *which part of the club faces
the camera* and match that to the corresponding part of the mesh — if the toe points at the
camera during the downswing, the silhouette has a toe-forward signature that the mesh can be
matched to at that vantage.

**Why this beats what we do now.** The current fit maximises IoU, and at 20–40 px many
orientations project to nearly the same outline — which is exactly why the recovered angles
wander unphysically (§C5). Landmarks break that degeneracy because they are *labelled*: a
toe is not a heel even when the outlines match.

**A rejected alternative, and why.** An earlier suggestion was to match interior shading via
the z-buffer. **The maintainer is right to reject it**: interior appearance is club-specific
and lighting-dependent, so it would not generalise across clubs and would make the canonical-
template goal impossible. Landmarks are generic — *every* iron has a toe, heel, hosel
junction, leading edge, topline and sole.

**What it unlocks.** Three or more labelled 2D↔3D correspondences is exactly the input
`cv2.solvePnP` wants. Pose stops being a brute-force grid search over IoU and becomes a
proper least-squares solve with a residual — which also gives an uncertainty estimate, which
IoU never did.

**Known difficulty, stated honestly.** A silhouette's boundary is the *extremal contour* — a
view-dependent set of surface points, not fixed mesh vertices — so not every landmark is
stable under rotation. The hosel junction, the sole line and the leading edge are relatively
stable; the toe and heel extremes migrate across the surface as the club rotates. Any
implementation must handle that rather than assume fixed vertices.

**Measured on real pixels, 2026-08-25.** Candidate landmarks were tested for the first
time, by measuring how far each moves per frame **relative to the head's own centroid**, so
real club travel is removed and only sliding over the surface remains. 48 consecutive frame
pairs, four shots:

| Candidate | Median step | p90 | Max |
|---|---|---|---|
| **shaft axis angle** | **0.87°** | 1.85° | 3.30° |
| toe extreme | 5.76 px | 13.73 px | 15.59 px |
| heel extreme | 5.67 px | 17.72 px | 25.31 px |
| hosel end of the head | 8.74 px | 20.86 px | 23.70 px |

At 0.296 px/mm the point landmarks slide **19–30 mm per 2.14 ms frame across a head only
80 mm wide**. The prediction above was right: they are extremal-contour artefacts, not
landmarks.

**The shaft axis is the exception, and it is a good one.** 0.87° per frame, monotonic
through impact (108.4 → 111.7 → 113.8 → 115.3 → 116.6 → 117.7 → 118.5° on one shot). It is
fitted to several hundred pixels rather than one, and it is *labelled* — a shaft is not a
sole. **It did not exist in the old capture**, where the shaft had no contrast, which is why
nobody proposed it.

Caveats, stated plainly: the shaft axis constrains roughly one rotational degree of freedom
and says nothing directly about face angle or dynamic loft, which behind-ball optics are not
expected to supply. It is a strong temporal anchor and a lie/toe-droop cue, not a solution.

**Also measured:** the hosel *junction* is not directly observable. The neck is the
lowest-contrast part of the club, so background subtraction breaks there — in all but the
few frames closest to impact the head and shaft are separate components with a real 17–70 px
gap between them.

**Still not attempted.** No landmark detector on the *mesh* side, and no PnP solve.

---

## E. Critical path, in order

1. ~~**Calibrated-exposure capture.**~~ **Done.** It unblocked detection and tracking. It did
   **not** unblock pose, and the claim above that it would was wrong — pose is an information
   problem, not a light problem.
2. **Radar range as a hard constraint on the mesh fit.** Cheapest remaining win and the data
   to do it now exists: `find_club` pins the clubhead to a 4.69 cm bin on 22/22 shots, while
   the fit searches ±125 mm freely. Removes the scale ambiguity a silhouette cannot resolve.
3. **Shaft-axis constraint + temporal smoothness.** The shaft gives 0.87°/frame of genuine,
   labelled orientation signal. Use it to anchor the sequence rather than smoothing an
   unanchored parameterisation, which is what failed before.
4. **Wire `impact_time_s` into the camera path.** Both estimators now demonstrably agree to
   0.66 frames; the camera still uses the lagging acoustic trigger as if it were impact.
5. **A-v3 re-run** at real settings — 247/298 µs, **0.296 px/mm**, 467.6 fps, 2.97 px blur —
   for the first honest accuracy figure.
6. **Impact location** — still comparatively small work, and still downstream of a
   trustworthy pose.

### What is now known not to work

- **Widening the roll grid.** It reaches a better basin worth 0.009 IoU. Not a fix.
- **Physical bounds alone.** The mesh frame is face-anchored, so bounds can be made
  physical — but bounds cannot manufacture signal that the silhouette does not contain.
- **Temporal smoothness alone.** Already tried; it froze the pose. It needs something
  anchored to the club's physical frame to smooth *toward*, which item 3 supplies.

### Why impact location is not step 2

Impact location is `f(clubface pose, ball position)`. Ball position is solid; **pose is the
weak link and we now know exactly how it is weak.** Building `f()` on an incoherent pose
yields a millimetre figure nobody should believe — precisely the trap this project already
fell into with the synthetic 1.050 mm result.

---

## F. Presentation rule

**Overlays must show the exact fitted geometry — never padded.** A circle drawn at 1.6× the
measured radius, or a radius floored to a minimum, misrepresents the system as less accurate
than it is. Both instances have been removed. If an outline looks wrong, that must mean the
fit *is* wrong.
