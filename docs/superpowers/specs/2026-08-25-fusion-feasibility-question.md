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

## 3. The real gap: nothing measures the CLUBHEAD in 3D

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
