# Fusion method plan: from the test bench to the live unit

Written 4 October 2026. Owner: the fork owner. Status: stage 1 done (1e closed by D20). Stages 2–7 superseded on 5 October by the
readiness roadmap (`openflight-test-bench`, `docs/superpowers/plans/2026-10-05-readiness-roadmap.md`),
itself paused on 8 October (MASTER-PLAN W12). Entry point: the bench's `docs/MASTER-PLAN.md`.
Since 7 October (the bench spec's Revisions 2-5), the "rules the live unit will follow" below
are partly superseded: club path, face, D-plane and carry are not measured; the arc and D-plane
models are banned or on hold. Calibration is the bench's Part B (after the new lens) and the
TrackMan session is its Part C pilot, replacing the D19 field day.

## Where we are

- **Software plumbing is largely built.** The server emits OPS speeds first and fills
  in IWR and camera results in the background. Live and replay share one camera
  core. Replay, provenance and Trackman comparison tools exist.
- **The physical gates are open.** Camera intrinsics and optical centre, target-line
  alignment and timing qualification (M1) are unproven. Three wiring items are open:
  F3 (radar range spaces and the −2 ms club-impact constant), F8 (alignment-stick
  horizontal reference) and C8 (phone-level check before applying LIS3DH roll).
  M3–M5 have not passed.
- **The live unit does not use the bench's measurement method.** The bench
  (the private test-bench repository, method in its `docs/SYSTEM.md`) measures
  each shot from its own captures only. The live pipeline breaks those rules in
  several places and keeps methods the bench showed don't work at this geometry.
  The full comparison is listed under stage 1 and the port stages below.
- **Every recording so far used foam balls.** Nothing is checked against Trackman yet.

## Decisions (4 October 2026)

| # | Decision | Consequence |
|---|---|---|
| D16 | The bench stays in its own repository. Only the fusion code itself (the measurement method) moves into openflight. | Bench tools, recorded data, hand marks, grading and pages stay in the bench. The bench then grades openflight's fusion code instead of its own copy (stage 7). |
| D17 | Fix the live unit's rule violations first. | Stage 1 below, before any port. |
| D18 | Setup becomes a readiness check only. | No shot takes a value from setup. The exposure ladder can start without the camera seeing the resting ball. The tee range stays as a diagnostic. |
| D19 | One field day: checkerboard at home first, then the phone-level and alignment-stick checks, then the paired Trackman 4 session on the same rig. | The Trackman data is recorded on a checked rig. |
| D20 (5 Oct) | The system is for normal golf balls only; foam balls are not catered for. | No ball-type question in setup and no foam handling in the kiosk: carry is computed as for a golf ball (superseded 7 Oct: carry, side and apex are not measured; golf balls only stands). Stage 1e is closed. The bench keeps marking its own foam recordings, which physically were foam, so its pages show no carry for them. |

## The rules the live unit will follow

From the bench's `docs/SYSTEM.md`:

1. **Each shot stands on its own data.** No medians, history, anchors or values pooled
   across shots. No setup value stands in for a shot's own measurement. A shot
   without a measurement reports it as not measured, with the reason.
2. **System outputs only.** Hand marks and Trackman data grade the system; they never
   feed a shot. A model constant may be calibrated once and fixed, with its source.
3. **Each sensor measures what it measures best.**
   - The camera locates: the resting ball, the ball's flight and the club head.
   - The IWR gives the ball's distance, from where its ball track leaves its club
     track.
   - The OPS243 gives speeds.
   - The camera never estimates distance from ball size or image row.
4. **Physics first.**
   - Impact is no later than the sound trigger less the sound's travel from the ball
     to the microphone.
   - Ball speed is the full speed.
   - The club moves on an arc.
   - The face comes from the D-plane.
5. **Every number carries an honest status and range:** measured, calculated,
   experimental, or not measured with the reason.

## Stage 1 — remove the rule violations (no port needed)

Each item is a bug against rule 1, 3 or 5. Write a failing test first, then fix.

| Item | Today | After |
|---|---|---|
| 1a. Resting-ball session anchor | `ReferenceBallTracker` (`camera/club_delivery.py:245`) keeps up to 15 past balls. From the 4th accepted shot, `resolve_stable` (`camera/ball_flight.py:541`) returns their median instead of this shot's detection. The camera model and contact time are built on it, and it persists for the server's lifetime (`server.py:4334`). | Each shot uses only its own detectors. If the scene and departure detectors disagree, the reference ball is withheld with the reason. No tracker state is carried between shots or stored in the frozen context. |
| 1b. IWR recovery prior | `iwr6843/runtime.py:274, 330, 386` fits up to 50 past shots to pick a track when a shot lacks an impact time. | Removed. No impact time means not measured. |
| 1c. Camera-size distance | Setup falls back to the camera's ball-size range when the radar finds nothing (`camera/tester_server.py:740`, `patch_pairing.py:52`). Ball flight falls back to `camera_size` depth (`camera/ball_flight.py:276, 861`). | Removed. The distance is the IWR's, or none. |
| 1d. Filled-in values | Missing vertical launch filled from club tables, horizontal set to 0.0 (`server.py:946–971`). Formula spin labelled high confidence (`server.py:3316`). Optimal-spin carry (`server.py:4800`). | Each is not measured, with the reason. |
| 1e. Carry for any ball | Carry, apex and side are computed for every ball, foam included (`server.py:4781`). | Closed by D20: the system is for golf balls only, so no change. |
| 1f. Previous clock sync | `rolling_buffer/trigger.py:541` reuses the previous OPS clock sync when a fresh one is rejected. | To review: a fixed clock property is allowed, a previous shot's value is not. Decide after reading it. |

**Progress (5 October).**
- **1a–1d are done**, test first, full suite green: `ee3ea360` (1b) and `cf0c96ea`
  (1a, 1c, 1d).
- **What changed in practice:**
  - A fusion context recorded at version 1 still replays, its tracker state ignored
    and noted.
  - A setup saved before this with a camera-size range hands swings nothing.
  - The `camera_only_experimental` label stays readable for old recordings.
- **1d as decided:** with the IWR or camera running, nothing is filled in, and carry
  needs a measured launch, spin and spin axis. An OPS-only install keeps its labelled
  estimates. `--calculated-spin` stays opt-in, labelled calculated (no confidence),
  and still feeds carry and simulators under that label.
- **1f reviewed: allowed, no change.** The reused value is the offset between the
  OPS243's clock and the Pi's, measured at most
  `SoundTrigger.CLOCK_SYNC_MAX_FALLBACK_AGE_S` (60 s) earlier and labelled `previous`
  with its age. It is a property of the two clocks, like the rig file, not data
  from another swing.

**What stage 1 leaves for stage 2.** The setup tee range is still used for every shot
(distance, IWR impact back-extrapolation, the club gate, the full-speed geometry), and
impact is still the trigger time. Both need the stage 2 port; they are removed there.

## Stages 2–6 — move the fusion method into openflight

> **Superseded (5 October, the owner's call).** The readiness roadmap ports the bench once,
> as opt-in `openflight/fusion/` modules on branches cut from upstream main, after its
> Phase 1 makes the bench deterministic. Stage 2's work is the roadmap's branches #11
> (IWR tracks) and #14 (timing). The stages below are kept as the method they describe,
> not as the plan.

Port each stage from the bench's committed modules with their tests. Each stage is
done when:
- openflight's pipeline, replayed on the recorded sessions (Outdoors-test-5, -7), gives
  the bench's numbers on the same shots to stated tolerances;
- the bench's grading tests pass against it;
- the old path is deleted, not kept beside it.

**Before stage 2, check the IWR frame times.**
- **The finding.** In an outside contributor's real-ball TrackMan session
  (4 Oct; IWR firmware 1.0.2 hybrid cadence, v10 dumps), the dump's frame
  times hide a 17–33 ms gap at the first range-window switch (frame 9). Every delta
  reads 2000 µs, but the ball's line only meets the club's line if shifted 17–33 ms.
  That breaks the crossing the IWR distance comes from.
- **Our recordings.** They switch windows at the same frame. Their headers show steady
  3 ms steps, and their ball tracks mostly match the OPS243's speed, which suggests no
  large gap. That isn't verified.
- **The check.** Before porting the crossing:
  - fit the ball's line separately before and after the switch on every recorded
    swing, and confirm they join;
  - ask whether any firmware the unit will run records the real switch time.
- **Result (5 October): no hidden gap in our recordings.**
  - Across the 27 ms switch, the ball's track joins within ±0.4 ms on all 10 swings
    that have one (test 1, 5 and 7).
  - One straight line fits 20–55 points on each side, which a gap of more than about
    1 ms would break.
  - Every header reads 3000 µs per frame. The 48 ms switch rarely has ball points.
  - Re-run this check on any new IWR firmware before trusting its crossing
    (now bench test `69981dc`).

**Stage 2 — timing and the IWR's ball distance.** Ports `timing.py`,
`radar/streaks.py`, `replay.shot_ball_distance`.
- Frame times at mid-exposure from sensor timestamps.
- Sound-delay impact window, from the rig's microphone position at 343 m/s.
- The Doppler impact bracket check, from the already-logged OPS bracket centres.
- The IWR club-then-ball track search at this shot's OPS speeds, with the address
  spread.

Removed:
- the tee range as a shot input (`runtime.py:479`, `lcmf.py:852`, `shot.py:111`,
  `club.py:365`, `server.py:3815, 3936, 1311`);
- the 2.25 m ball gate;
- the −2 ms club-impact constant (F3's constant; F3's range-space check remains).

**Stage 3 — the resting ball, the flight and full ball speed.** Ports `address.py`,
`camera/rest_ball.py`, `camera/flight.py`, `camera/geometry.py`, `radar/ops.py`, and
step 5a (re-measuring at both ends of the distance's spread, launch doubt past 5°).
- The ball sits at the IWR's distance with the IWR bearing gate, centred on its
  outline arc.
- The flight is three unknowns at the OPS speed.
- Focal length comes from the rig file, not from ball size.
- Full ball speed is shown and drives smash.
- The camera's launch angle replaces the IWR LCMF vertical as the shown value. Keep or
  drop LCMF as a diagnostic: decide in this stage.

Removed: the blob sweep, the setup-ball gate (`camera/ball_flight.py:44`) and the
focal-from-size model (`camera/geometry.py:71`).

**Stage 4 — setup becomes a readiness check (D18).**
- Setup checks sensors, aim, tilt and exposure, and shows the 2 ft patch.
- It feeds no per-shot value.
- The ladder no longer refuses to start without seeing the resting ball.
- The static IWR tee range and the camera cross-check stay as diagnostics.
- Update `docs/camera/tester-pilot.md`.

**Stage 5 — club, face and strike.** Ports `camera/club.py` and `dplane.py`.
- The head fit is a circular arc at the OPS club speed reaching the ball at impact,
  with a free strike offset.
- Radius and plane are swept, plus the leave-one-out and distance-end ranges.
- The guards and doubts from the bench apply.
- D-plane face with k = 0.9183 − 0.003896 × spin loft.
- The strike offset is measured along the face.
- The spin axis is the D-plane tilt.

Removed:
- the IWR club path and IWR horizontal as shot metrics;
- the IWR club ranges in the camera club fit;
- the frame after impact in the club fit;
- the fixed 0.8 face weight;
- `spin_axis = launch − path`.

**Stage 6 — statuses and ranges in the shot contract.**
- Every metric carries status, low/high and a reason: `launch_monitor.Shot`, the
  session logger, review metrics and the UI (`ui/src`).
- The provisional OPS emit stays.

## Stage 7 — the bench grades openflight's code

The bench's measurement modules are replaced by imports from openflight, so there is
one implementation. The bench keeps:
- recorded data, hand marks, spray marks and setups;
- grading tests;
- `live.run_live`, swing videos, pages and Trackman pairing.

## Field day (D19)

**Before:** fit the camera's intrinsics from a checkerboard at the shot capture mode
(M1).

**On the day, in order:**
1. **Phone-level check against the LIS3DH roll (C8).** Then apply roll in both camera
   paths.
2. **Alignment-stick session (F8).** It gives the IWR's horizontal phase reference,
   used for the bearing gate. It may explain test 7 shot 1's bearing, 10° off.
3. **Paired Trackman 4 session**, about 60 real-ball shots
   (`openflight-test-bench`'s pairing checklist).
   - Raw clips, dumps and OPS buffers are the record, so the session replays through
     whichever code is current.
   - Record the ball type as golf.

**From the session:**
- the microphone board's delay;
- the first accuracy figures for every motion metric;
- k checked on new shots;
- every constant tuned on the foam recordings re-checked;
- the club-independent ball distance retried (the resting ball's echo vanishing at
  impact).

**Before scoring it:** freeze the numerical acceptance limits for the six motion
metrics (bias, MAE, 90th percentile, gross-error rate, read rate). Hold out one
session, as the master plan's acceptance policy requires.

## After that, not yet scheduled

- **Production ballistics:**
  - apex about 21% low on irons and wedges;
  - the off-line spin axis (A20) and its source (A05).
- **3D club-head model** for a consistent head point: now bench MASTER-PLAN W9; the 690CB mesh is a generic stand-in, the owner's club is a Maltby TS4.
- **More club frames:** the 468 fps mode gives about ten head positions instead of two
  or three (a binned mode, ruled out; the full-density row window is the bench's camera plan).
- **The IWR firmware work:**
  - an IWR-detected impact trigger on a clock the Pi can map;
  - a buffer centred on impact holding ≥ 30 ms of the club's approach.

  These unblock soundless fusion, which is OPS-only today.
- **Kiosk defaults.** `start-kiosk.sh` runs OPS-only unless `--camera-capture
  --iwr6843` are passed. Decide when fusion becomes the default.
- **Possible missed shot.** On test 7, an OPS buffer at 10:20:03 (91 mph) was never
  reported as a shot.
- **Per-shot processing time on the Pi**, and its full workload behaviour.
