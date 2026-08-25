# Can our radars + camera actually run an OERT-style fusion?

**Status:** scoped 2026-08-25, not answered. First-pass repo audit below.
**Why it matters:** §2.4 of the camera verdict establishes that Trackman 4 does markerless
impact location on a **60 fps** camera because *"impact location falls out of the fusion,
not out of frame rate."* If that is the mechanism, then our whole feasibility case rests on
whether **our** sensors can supply what that fusion consumes. Nobody has checked.

Comparator set: **Trackman 4, Full Swing KIT, Mevo Gen 2.** Not iO (overhead — see
`camera-feasibility-verdict-2026-08.md` §2.4).

---

## 1. What the fusion needs, and what we actually have

| Fusion input | Trackman 4 | OpenFlight | Status |
|---|---|---|---|
| Impact **timing** | dual 24 GHz radar @ 40 kHz → **25 µs** | OPS243 I/Q @ 30 ksps → **33 µs** (configurable to 100 ksps → **10 µs**) | ✅ adequate; see §2 |
| Clubhead **speed** | radar | OPS243 `find_club_speed` (FFT magnitude) | ✅ scalar only |
| Clubhead **3D position / kinematics** | **dual radar** | **❌ NOTHING** — see §3 | **THE GAP** |
| Ball 3D position | dual radar | IWR6843 `find_ball` / `BallTrack` (RANSAC range-vs-time) | ✅ |
| Camera angular constraint | 60 fps silhouette | 467 fps (or 144 fps at 1:1) silhouette | ✅ better than TM4 |
| Fusion model | theirs, years of work | `research/club_pose/` + `silhouette_poc/fusion/` | the hard part |

## 2. Impact timing — we are NOT better than Trackman, and a doc claim is wrong

`camera-feasibility-verdict-2026-08.md` §0.6.3 (written 2026-08-25) claims our 33 µs is
*"better than Trackman's 40 kHz radar."* **That is backwards.** 40 kHz = 25 µs; 30 ksps =
33 µs. Ours is **coarser by 33 %**. Corrected in the same commit as this document.

It does not matter much — 33 µs at 45 m/s is 1.5 mm of clubhead travel, well inside a
single-digit-mm target, so §2.4's original wording ("better than needed") was right and my
edit introduced the error. But two things follow:

- **Sample rate is a configurable knob, not a hardware limit.** `ops243.py::set_sample_rate`
  documents `10000, 20000, 30000 (recommended), 50000, 100000`. At 100 ksps we get **10 µs**,
  better than Trackman.
- **The trade is buffer duration.** The rolling buffer is 4096 samples: 136 ms at 30 ksps,
  only **41 ms at 100 ksps**. Whether 41 ms still brackets impact reliably is untested and
  is a cheap experiment.

## 3. WITHDRAWN — this section's central claim was false

> **CORRECTION 2026-08-25 (later the same day).** §3 below claimed there is "no clubhead
> detector, tracker, or state anywhere in the module." **That is wrong.**
> `src/openflight/iwr6843/club.py` is **38 KB** and has been in the tree the whole time.
>
> **How I got it wrong:** my audit ran
> `grep -rniE "club" src/openflight/iwr6843/*.py | head -12`. Results are alphabetical,
> `calibration_session.py` sorts before `club.py` and had more than twelve matches, so
> `head -12` truncated `club.py` out of the output entirely. I then reported the absence of
> evidence as evidence of absence, and committed it. **Never conclude "X does not exist"
> from a truncated search.**
>
> ### What actually exists
>
> | Capability | Where | What it does |
> |---|---|---|
> | Clubhead detection + tracking | `iwr6843/club.py` | `find_club`, `estimate_club_path` — converts each snapshot to Cartesian (x along boresight, y cross-range) and fits **both against time**, so `path_deg = atan2(v_y, v_x)` is exact for straight-line motion |
> | Attack angle | `iwr6843/club.py` | `estimate_attack_angle_candidate`, impact-centred windows |
> | **Camera+radar+OPS fusion** | `camera/club_delivery.py` (1394 lines) | *"combines camera transverse motion, IWR6843 depth, and OPS club speed"*, with a camera-only perspective-flow fallback when IWR range is unavailable |
> | Ball flight reconstruction | `camera/ball_flight.py` | camera centroids give bearing, **IWR range is the preferred depth source**, apparent ball size is the fallback |
> | Reference-ball anchoring | `club_delivery.ReferenceBallTracker` | rolling session anchor, rejects false blobs |
> | Measured rig geometry | `club_delivery.CameraDeliveryGeometry` | camera/radar heights, tee range, lateral offset, roll correction |
>
> **So the pixel-ray ∩ range-sphere architecture is not a proposal — it is implemented and
> merged.** The fusion premise this document set out to test is already answered in the
> affirmative by working code.
>
> ### Two independent corroborations of our own measurements
>
> - `ReferenceBallTracker` gates on `9.0 <= diameter_px <= 30.0`. **Our measured ~14 px sits
>   comfortably inside it**; the previously-assumed 28 px sat at the very top of the range.
> - `iwr6843/calibration.py` carries `tee_range_m  # tape-measured launch point (slant)` —
>   the tape measurement §4 asks for already has a home in the calibration schema.
>
> ### What genuinely does NOT exist (verified with untruncated searches)
>
> - **Impact location on the face: nothing.** No match for `impact_location`,
>   `strike_location`, `face_impact`, `impact_point`, `heel_toe` or `gear_effect` anywhere
>   in `src/openflight/`.
> - **Clubface orientation measured optically:** `face_angle` appears only in `server.py`,
>   not in any camera or radar module.
>
> **Therefore the silhouette work is complementary, not redundant, and its position is much
> stronger than this document originally concluded.** Clubhead *kinematics* are solved
> upstream; what silhouette fitting adds is clubface *orientation* and the ball's position
> relative to the face — the two things impact location actually needs — and it can now sit
> on top of merged fusion infrastructure instead of inventing it.

## 3 (superseded). The claimed gap: nothing measures the CLUBHEAD in 3D

This is the finding that matters, and it is the question to answer before any more
optimisation work.

- **IWR6843 tracks the ball, not the club.** `iwr6843/tracking.py` is entirely ball-oriented:
  `find_ball`, `BallTrack`, and a RANSAC line fit of *range vs time* for "the unambiguous
  ball speed". There is no clubhead detector, tracker, or state anywhere in the module.
- **OPS243 gives clubhead SPEED only** — a scalar from FFT magnitude
  (`find_club_speed` / `_find_club_speed_by_magnitude`), gated between 0.67× and 0.85× of
  ball speed. No position, no direction, no 3D.

**So where Trackman fuses radar-derived clubhead *position* with camera constraints, we
have a clubhead *speed* scalar and a camera.** That is a materially weaker input set, and
the fusion has to make up the difference from the silhouette alone.

**The open question, stated precisely:** can the club's 6-DOF pose be recovered from
`(silhouette, club speed scalar, ball 3D position, impact time)` — without radar-derived
clubhead position? Three candidate answers, none yet tested:

1. **Ball as depth anchor.** The ball's 3D position is known; solve the club's pose with the
   ball anchoring depth. Weakness: at the frames we observe it, the club is at a *different*
   depth than the ball, and that offset is what we are trying to measure.
2. **Silhouette scale as depth.** The clubhead's apparent size gives its depth, given a known
   template. Weakness: needs the template to match the actual club, and apparent size is a
   weak depth cue at these pixel counts (a 14 px ball implies a modestly-sized head).
3. **Speed + timing as a kinematic constraint.** Club speed plus impact time plus the swing
   arc constrains where the head must be at each frame. This is closest to what Trackman
   describes ("clubhead position in every frame — and in-between frames") and is probably
   the right line, but it needs a swing-arc model we do not have.

**Can the IWR6843 be made to see the clubhead at all?** Unknown and worth an hour: it is an
FMCW radar with angle capability, the clubhead is large and metallic, and it is already
streaming. If it can produce even coarse clubhead range+angle, that closes the gap directly
and moves us onto Trackman's actual architecture rather than a weaker substitute.

## 3a. First-pass answer from the radar's own config (2026-08-25)

No IWR6843 shot data exists locally — `~/openflight_sessions/` holds only mock sessions with
zero-byte raw logs — so this could not be tested against real captures. But the configured
chirp answers most of it. From `config/iwr6843_l3dump_wide_24f3ms_53bin_iq16.cfg`
(`profileCfg 0 60.0 7 3 38 0 0 100 1 128 4000 0 0 30`, `frameCfg 0 2 12 0 3 1 0`):

| Quantity | Value |
|---|---|
| Wavelength | 5.00 mm |
| Swept bandwidth during ADC | 3.20 GHz |
| **Range resolution** | **47 mm** |
| Range window (53 bins) | 2.5 m |
| Chirp period, TDM ×3 | 135 µs per Doppler sample |
| **Max unambiguous velocity** | **±9.3 m/s** |
| Frame rate | 333 fps (wide) / 500 fps (dense) |

**Doppler is useless to us — everything aliases.** Clubhead 36–50 m/s and ball 55–73 m/s all
fold back inside ±9.3 m/s (a 50 m/s driver head reads as −5.6 m/s). This is exactly why
`tracking.py` opens with *"RANSAC line fit of range vs time = the unambiguous ball speed.
Doppler is [ambiguous]"* — the existing design already works around it.

**But range-vs-time is strong, and that is what matters.** At 47 mm resolution and 333–500 fps
the clubhead moves 90–135 mm per frame, i.e. **2–3 range bins per frame** — a clean track.

**And before impact the clubhead is the only fast-moving object in the scene.** The ball is
stationary and falls to the existing `mti_filter`; static clutter likewise. So pre-impact the
club should be the *dominant* moving return, using the same machinery `find_ball` already runs.

⇒ **The gap identified in §3 looks like a software gap, not a sensor gap.**

### The angle caveat, and why it does not matter

`channelCfg 15 7` = 4 RX × 3 TX → 8 azimuth virtual channels, giving azimuth resolution of
roughly 2/8 rad ≈ **14°**, or ~**357 mm** cross-range at 1.43 m. Far too coarse to locate a
clubhead laterally.

**This is fine, because we do not need radar angle.** The design is already *pixel ray ∩ radar
range sphere*: the **camera** supplies angle precisely and the **radar** supplies range. Splitting
it that way plays to each sensor's strength — and it is the same division Trackman describes.

So the candidate architecture is: **camera angle (sub-pixel) + IWR clubhead range (47 mm bins,
better with sub-bin interpolation) = clubhead 3D position**, which is the input §3 said we were
missing.

### What still needs a real capture

1. Does a clubhead return actually appear, at what SNR, and is it separable from the golfer's
   body and arms — which are also moving, closer, and larger?
2. Sub-bin range accuracy achievable in practice (47 mm bins; interpolation typically buys
   5–15 mm at good SNR, which is what decides whether this is useful).
3. Whether the return survives the moment of impact, when club and ball briefly coincide.

**Required capture:** one real shot with IWR6843 L3 dump enabled and the camera capturing
simultaneously, so radar and silhouette can be checked against each other on the same swing.

## 4. A bug found while auditing

`silhouette_poc/fusion/solver.py` hardcodes `NOMINAL_RANGE_MM = 1_575.0`. The real capture
implies **~1425 mm** (§0.5 of the camera verdict: 2.8 mm lens, `focal_px` 466.7, measured
13.97 px ball). That is a **10 % depth error**, which propagates straight into plate scale
and therefore into every millimetre the solver reports.

Also flagged: `RADAR_STATIC_BIAS_MM = 66.0069821`. Seven significant figures on a physical
bias is a fitted constant wearing a measurement's clothes. Its provenance should be checked
before it is trusted against real data.

## 5. Next actions

1. **Determine whether IWR6843 can detect the clubhead.** Highest value — it decides whether
   we are on Trackman's architecture or a weaker one. Offline, against existing I/Q captures.
2. **Test 100 ksps on the OPS243** and confirm a 41 ms buffer still brackets impact. Cheap.
3. **Fix `NOMINAL_RANGE_MM`** and re-check anything that consumed it.
4. **Establish the provenance of `RADAR_STATIC_BIAS_MM`.**
5. Only then: the A-v3 re-run, sweeping exposure, plate scale **and frame rate** as free
   parameters.

---

## 6. Removing the sound trigger entirely (2026-08-25) — **[MEASURED]**

Raised by the maintainer: acoustic delay scales with how far the user places the unit, so
it cannot be calibrated out in general. Can the camera and radars do it alone?

**Yes, and the camera alone already beats the microphone.** Watching a **22x22 px window**
on the teed ball — 484 pixels, trivial at 467 fps — the shot crosses a 6-sigma threshold
at **frame 67** on the reference capture. The acoustic trigger fired at **frame 74**.

| | Fires at | vs microphone |
|---|---|---|
| Camera ROI watch on the tee | **F67** | **15.0 ms earlier** |
| SEN-14262 acoustic | F74 | baseline |

Quiet-period residual in that window is 1.51 DN mean; the shot drives it to 35–66 DN. The
margin is enormous, and it has **no distance dependence**.

This is what the photometric class does. Foresight's GCQuad detects a shot by *"monitoring
when the ball disappears from the hitting zone"* its cameras already track — continuous
monitoring, no discrete trigger, plus a ball-found indicator. **[MAN]** Acoustic triggering
appears in patent claim language as one option among many, not in shipping products of this
class.

**Recommended architecture:**

1. **Detection** — camera ROI watch on the tee zone. Freeze the ring. No microphone.
2. **Timing** — retrospective, never from whatever fired the capture. Three independent
   routes already exist: `iwr6843/shot.py::impact_time_s` (ball range walk back-extrapolated
   to the tee, 14/14 on the 2026-07-25 captures), `rolling_buffer/processor.py::estimate_impact`
   (OPS club-to-ball speed transition), and camera back-extrapolation of the ball's flight.

Note the ROI fired at F67 on **club arrival**, not ball departure — which is preferable for
triggering (it captures *before* impact) but means the two events must be separated
retrospectively. A hand or club entering the ROI during setup could also false-fire, so this
should be armed only from the READY state (see
`2026-08-25-ball-placement-and-exposure-readiness.md`).

## 7. Mesh identity dominates the fit — and hosel removal does NOT fix it

First fit of the real mesh to real pixels (`silhouette_poc/replay/fit_real.py`). The club
swung was a **9-iron**; available meshes are a Titleist 690CB *blade* and a driver.

| Frame | Observed | 690CB blade | Blade, hosel removed | Driver |
|---|---|---|---|---|
| F66 | 759 px | 0.388 | 0.371 | **0.623** |
| F68 | 481 px | 0.460 | 0.379 | **0.652** |
| F70 | 219 px | 0.236 | 0.274 | **0.606** |

**The driver mesh fits a 9-iron better than the blade does.** The blade includes a hosel
(96.9 mm span vs a 54.8 mm head), and removing it looked like the obvious fix since the
observed head is ~58 mm against the driver's 60 mm. **It did not help** — 0.460 to 0.379 at
F68. The hosel is not where the error is; a muscleback blade is simply not shaped like a
modern 9-iron.

**Consequence for the design.** The plan is *one canonical template per club type, zero user
setup*. This says head shape varies enough WITHIN a type that one template may not cover it,
and that IoU against a partial silhouette will prefer the wrong club. **Settle this before
relying on the canonical-template assumption.**

**What did land:** at F68 the fit independently recovers range **1425 mm** at roll -2 deg,
matching the camera-to-ball distance derived separately from the measured 13.97 px ball and
the 2.8 mm datasheet lens. Two unrelated routes, same number.

**Scale of the gap:** synthetic evaluation of the same machinery reached IoU **0.9928**. Real
pixels give 0.65 with a mismatched model. **Synthetic performance is not predictive here.**
